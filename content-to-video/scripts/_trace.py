#!/usr/bin/env python3
"""_trace.py — Raw Layer：一次制作 = 一条不可变执行轨迹。

WikiSkill 三层架构（arXiv 2608.27454）的第一层是 Raw Layer：只追加、永不
修改的执行证据。本模块负责把一次 run.py 的实跑结果落成一条 JSON 轨迹，
供后续的 Wiki Maintainer 蒸馏。**它不参与任何一次视频制作**，写入失败一律
静默吞掉——trace 是旁路观测，绝不能反过来搞挂主流程。

落盘位置（按优先级）：
  1. 环境变量 CTV_TRACE_DIR
  2. ~/.config/ai-video/traces/         ← 默认

刻意**不落在技能目录内**：SKILL.md 的路径约定要求"产物一律写项目目录，不
写技能目录"，而 trace 是机器高频产生的运行数据，塞进 dev/ 既违反本技能
自己的约定、又会在技能重装/更新时被冲掉。用户级目录与既有的
~/.config/ai-video/.env 同级，符合本技能"全局配置在用户目录"的既有惯例。

隐私约定（写进代码，不靠自觉）：
  · 不记录绝对路径——只记 sha1(salt+realpath) 前 12 位做"同一项目"关联
  · 不记录任何稿件正文、标题、文件名原文
  · 不记录任何 API Key / 环境变量值
  · stderr 只保留末尾若干字符，且过一遍 _scrub

用法（run.py 内部调用，外部一般不需要直接用）：
  from _trace import record_run
  record_run(outcome="ok", params={...}, stages=_REPORT["steps"], ...)
"""
import datetime
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time

TRACE_VERSION = 1

# 默认保留的轨迹条数上限：wiki 会无限膨胀是 WikiSkill 作者自己列的局限之一，
# 这里用"按条数滚动 + 按天数老化"做最便宜的自动剪枝（prune 子命令可调）。
DEFAULT_MAX_TRACES = 200
DEFAULT_MAX_AGE_DAYS = 180

_SEP = re.compile(r"[\/\\]+")

# ── 敏感信息清洗：宁可多洗，不可漏洗 ──────────────────────────────
# 出现这些词样的 key=value / 长串 token 一律替换；覆盖各家 key 的常见前缀。
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),                     # OpenAI 风格
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|access[_-]?key)"
               r"\s*[=:]\s*\S+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}"),
)


def _home():
    try:
        return os.path.expanduser("~") or None
    except Exception:
        return None


def _scrub(text, limit=2000):
    """截断 + 去敏。给 stderr/命令行的尾巴用的，不能指望调用方先洗一遍。

    除了 key/token，还会把家目录与常见绝对路径前缀抹掉——本模块对外承诺
    "不记录绝对路径"，这条必须机械保证，不能靠每个调用方自觉只传 basename。
    """
    if not text:
        return ""
    s = str(text)
    for pat in _SECRET_PATTERNS:
        s = pat.sub("<redacted>", s)
    home = _home()
    if home and home in s:
        s = s.replace(home, "~")
    # 兜底：抹掉残余的绝对 POSIX/Windows 路径，只留最后一段。
    # 字符类里的引号用 \x22/\x27 转义写，避免正则字面量自身把字符串截断
    # （这行曾经就是这样写崩的）。
    s = re.sub(r"(?<![\w~])(?:[A-Za-z]:)?[\\/](?:[^\\/\s\x22\x27]+[\\/])+"
               r"([^\\/\s\x22\x27]+)", r"<path>/\1", s)
    if len(s) > limit:
        s = "…(前略)…" + s[-limit:]
    return s


def trace_dir():
    """trace 落盘目录；不可用（被显式关闭 / 无法创建）时返回 None。"""
    if os.environ.get("CTV_TRACE", "1").strip().lower() in ("0", "off", "false", "no"):
        return None
    d = os.environ.get("CTV_TRACE_DIR", "").strip()
    if not d:
        home = os.path.expanduser("~")
        d = os.path.join(home, ".config", "ai-video", "traces")
    return os.path.abspath(d)


def _salt(d):
    """每机一个随机盐，用于把项目路径哈希成不可反推的 project_key。"""
    p = os.path.join(d, ".salt")
    try:
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                s = f.read().strip()
            if s:
                return s
        os.makedirs(d, exist_ok=True)
        s = hashlib.sha1(os.urandom(32)).hexdigest()
        with open(p, "w", encoding="utf-8") as f:
            f.write(s)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return s
    except OSError:
        # 建不了盐就不关联项目——宁可丢这个字段，也不裸存路径
        return "nosalt"


def project_key(path):
    """把项目绝对路径哈希成 12 位短 key：能跨次关联"同一个项目"，但不泄露路径。"""
    if not path:
        return None
    d = trace_dir()
    if d is None:
        return None
    try:
        rp = os.path.realpath(os.path.abspath(path))
    except OSError:
        return None
    return hashlib.sha1((_salt(d) + rp).encode("utf-8")).hexdigest()[:12]


# ── 环境指纹：跨次/跨机对比"是不是环境变了"的唯一依据 ──────────────
_ENV_CACHE = {}


def _sh(cmd, timeout=5):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, errors="replace")
        return (out.stdout or "") + (out.stderr or "")
    except Exception:
        return ""


def _pick_h264(enc_text):
    """可用的 h264 编码器（按优先级取第一个）。渲染失败排查时这是关键变量。"""
    for name in ("libx264", "libopenh264", "h264_nvenc", "h264_qsv",
                 "h264_vulkan", "h264_vaapi", "h264_amf", "h264_v4l2m2m"):
        if re.search(r"^\s*[VASFXBD\.]{6}\s+" + re.escape(name) + r"\s",
                     enc_text, re.M):
            return name
    return None


def env_fingerprint(force=False):
    """采集环境指纹。进程内缓存一次——run.py 只在收尾写一次，不该反复 spawn。"""
    if _ENV_CACHE and not force:
        return dict(_ENV_CACHE)

    ff = _sh(["ffmpeg", "-version"])
    m = re.search(r"ffmpeg version (\S+)", ff)
    ffmpeg_ver = m.group(1) if m else None
    enc_text = _sh(["ffmpeg", "-hide_banner", "-encoders"]) if ffmpeg_ver else ""
    node = _sh(["node", "--version"])

    fp = {
        "py": platform.python_version(),
        "platform": sys.platform,
        "machine": platform.machine(),
        "node": (node or "").strip() or None,
        "ffmpeg": ffmpeg_ver,
        "h264_encoder": _pick_h264(enc_text),
        "has_libx264": bool(re.search(r"^\s*[VASFXBD\.]{6}\s+libx264\s",
                                      enc_text, re.M)),
    }
    _ENV_CACHE.update(fp)
    return dict(fp)


def skill_version(skill_dir=None):
    """从 SKILL.md frontmatter 读版本号失败时返回 None（不影响 trace 落盘）。"""
    try:
        base = skill_dir or os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        with open(os.path.join(base, "SKILL.md"), "r", encoding="utf-8") as f:
            head = f.read(4096)
        m = re.search(r'^version:\s*"?([0-9][0-9A-Za-z.\-]*)"?\s*$',
                      head, re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def _now_id():
    ts = datetime.datetime.now(datetime.timezone.utc)
    rand = hashlib.sha1(os.urandom(16)).hexdigest()[:6]
    return ts.strftime("%Y%m%dT%H%M%SZ") + "-" + rand


def params_hash(params):
    """参数组合指纹：把"同一类跑法"聚到一起，用于统计哪种配置最容易翻车。"""
    try:
        blob = json.dumps(params, sort_keys=True, ensure_ascii=False,
                          default=str)
    except (TypeError, ValueError):
        return None
    return "ph:" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def build_record(outcome, params=None, stages=None, skipped=None,
                 images=None, failure=None, outputs=None,
                 total_seconds=None, project_path=None, skill_dir=None):
    """组装一条轨迹记录（不落盘）。所有字段缺失都合法——trace 是尽力而为。"""
    params = dict(params or {})
    stages = list(stages or [])
    rec = {
        "v": TRACE_VERSION,
        "id": _now_id(),
        "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "outcome": outcome,
        "skill_version": skill_version(skill_dir),
        "env": env_fingerprint(),
        "params": params,
        "params_hash": params_hash(params),
        "project_key": project_key(project_path),
        "stages": [
            {
                "name": _scrub((s or {}).get("name"), 80),
                "seconds": (s or {}).get("seconds"),
                "ok": (s or {}).get("ok"),
            }
            for s in stages
        ],
        "skipped": [_scrub(x, 120) for x in (skipped or [])],
        # 兜底信号单独拎出来：这是 WikiSkill 蒸馏时最有价值的一类字段——
        # "跑完了但走了兜底"比"报错退出"更难被发现，却同样说明文档/默认值
        # 与实际环境不匹配。
        "fallbacks": _derive_fallbacks(stages, skipped, images),
        "images": _clean_images(images),
        "failure": _clean_failure(failure),
        "outputs": dict(outputs or {}),
        "total_seconds": total_seconds,
    }
    if total_seconds is None:
        rec["total_seconds"] = round(
            sum(s.get("seconds") or 0 for s in rec["stages"]), 1)
    return rec


def _derive_fallbacks(stages, skipped, images):
    """从跳过项/图片统计里反推"这次是不是走了兜底"。"""
    out = []
    for s in (skipped or []):
        s = str(s)
        if "纯文字" in s or "无图" in s:
            out.append("纯文字版兜底")
        elif "配图" in s and ("跳过" in s or "无 Key" in s):
            out.append("跳过配图")
    img = images or {}
    if img.get("total") and img.get("missing"):
        out.append("存在缺图段落(%s)" % img["missing"])
    if img.get("matched") == 0 and img.get("total"):
        out.append("配图零命中")
    # 去重保序
    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def _clean_images(images):
    if not images:
        return None
    keys = ("total", "matched", "missing", "skipped_reason")
    return {k: images.get(k) for k in keys if images.get(k) is not None} or None


def _clean_failure(failure):
    if not failure:
        return None
    return {
        "stage": _scrub(failure.get("stage"), 80),
        "rc": failure.get("rc"),
        "stderr_tail": _scrub(failure.get("stderr_tail") or
                              failure.get("stderr"), 2000) or None,
    }


def _maybe_prune(directory):
    """落盘后顺手做一次机会性剪枝：只有条数超上限才真动手（一次 listdir）。

    DEFAULT_MAX_TRACES / DEFAULT_MAX_AGE_DAYS 如果"写在那儿、等人手动跑
    prune 才生效"，那它们根本不是默认值——轨迹会无限增长，没人会记得去清。
    所以写盘路径自己兜住这件事。Raw Layer 可丢弃（蒸馏过的经验早已固化进
    patterns/，见 Wiki Layer 的不可回滚约定），删老轨迹不损失知识。

    异常一律吞掉：本模块最高优先级是"旁路不得破坏主流程"。为了剪枝把一次
    已经做成功的视频搞挂，完全不划算。
    """
    try:
        d = directory or trace_dir()
        if not d or not os.path.isdir(d):
            return 0
        names = [n for n in os.listdir(d)
                 if n.endswith(".json") and not n.startswith(".")]
        if len(names) <= DEFAULT_MAX_TRACES:
            return 0
        # 显式传参、不用 prune 的默认参数：默认参数在**函数定义时**就绑定了
        # 常量值，之后改 DEFAULT_MAX_TRACES 不会生效——上限会被焊死在 import
        # 时刻。在函数体里读模块常量才是调用时求值。
        return prune(d, max_traces=DEFAULT_MAX_TRACES,
                     max_age_days=DEFAULT_MAX_AGE_DAYS, dry_run=False)
    except Exception:
        return 0


def write_record(rec, directory=None):
    """落盘一条轨迹。任何异常都吞掉并返回 None——trace 绝不能搞挂主流程。"""
    d = directory or trace_dir()
    if d is None:
        return None
    path = None
    try:
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, rec.get("id", _now_id()) + ".json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)  # 原子落盘：半截 JSON 会污染后续统计
    except Exception:
        return None
    # 放在 try 之外：剪枝失败不该把本次已经落盘的结果也吞掉
    _maybe_prune(d)
    return path


def record_run(**kw):
    """run.py 收尾调用的一站式入口：组装 + 落盘，返回路径或 None。"""
    return write_record(build_record(**kw))


# ── 读取侧（dev/wiki_trace.py 与 dev/wiki_maintain.py 用）────────────

def iter_traces(directory=None, newest_first=True):
    """遍历轨迹文件，逐条 yield (path, record)。坏文件跳过并报 stderr。"""
    d = directory or trace_dir()
    if not d or not os.path.isdir(d):
        return
    names = [n for n in os.listdir(d)
             if n.endswith(".json") and not n.startswith(".")]
    names.sort(reverse=newest_first)
    for n in names:
        p = os.path.join(d, n)
        try:
            with open(p, "r", encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError) as e:
            print("[warn] 跳过无法解析的轨迹 %s：%s" % (n, e), file=sys.stderr)
            continue
        if isinstance(rec, dict):
            yield p, rec


def prune(directory=None, max_traces=DEFAULT_MAX_TRACES,
          max_age_days=DEFAULT_MAX_AGE_DAYS, dry_run=False):
    """按条数 + 天数滚动清理。超出上限的老轨迹直接删除（Raw Layer 可丢弃，
    Wiki Layer 不可——蒸馏过的经验早已固化进 patterns/）。"""
    d = directory or trace_dir()
    if not d or not os.path.isdir(d):
        return 0
    items = list(iter_traces(d))
    removed = 0
    if max_age_days and max_age_days > 0:
        cutoff = time.time() - max_age_days * 86400
        for p, _rec in items:
            try:
                if os.path.getmtime(p) < cutoff:
                    if not dry_run:
                        os.remove(p)
                    removed += 1
            except OSError:
                pass
    keep = []
    for p, _rec in list(iter_traces(d)):
        keep.append(p)
    if max_traces and max_traces > 0 and len(keep) > max_traces:
        for p in keep[max_traces:]:
            try:
                if not dry_run:
                    os.remove(p)
                removed += 1
            except OSError:
                pass
    return removed


if __name__ == "__main__":
    print(json.dumps(build_record(outcome="__selfcheck__",
                                  params={"aspect": "landscape"},
                                  stages=[{"name": "TTS", "seconds": 1.0,
                                           "ok": True}]),
                     ensure_ascii=False, indent=2))
