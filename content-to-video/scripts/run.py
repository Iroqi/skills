#!/usr/bin/env python3
"""Content-to-Video — 一键编排（可选，薄组合层）。

把第 3→5 步（TTS → 配图 → HTML → check → 渲染 → 校验）串成一条命令，
内部按依赖顺序调用各子脚本；不改变任何子脚本的独立性，只做组合与默认值。
日常一条命令：

  python scripts/run.py --source segments_source.json -o audio_output

参数：
  --source            segments_source.json 路径（必填）
  -o/--output         音频/中间产物输出目录（默认 audio_output）
  --project           HTML/渲染项目目录（默认 <output 同级>/hf-project）
  --no-images         跳过配图步骤（纯文字版）
  --refresh-images    配图不复用，全部重新搜索/下载（默认 --resume 跳过已有成品图）
  --search-sids       只对列出的段落跑文搜图（逗号分隔 sid，如 'seg1,seg3'）；
                      按配图路由只有部分段落走方式 A 时用，其余段落走 B/C/D
  --until {tts,images,html,render}   跑到该步骤为止（用于调试）
  --no-verify         渲染后不跑 verify_render
  --no-resume         TTS 不用 --resume（默认启用）
  --aspect            画幅 landscape/portrait/both（默认 landscape；portrait=1080x1440 竖屏 3:4）。
                      字幕/内容呈现模式固定按画幅绑定：横屏 bar、竖屏 verse（无模式选项）
  --theme             主题（默认 cream；可选值见 config/theme_registry.json，目前含 cream/dark/tech/alert）
  --fps               输出帧率（默认 24，官方支持 24/30/60）
  --quality           渲染质量 draft/standard/high（默认 standard）
  --workers           渲染抓帧 worker 数（默认 6；2026-08 实测 6 比 4 快约 10%）
  --gpu               启用 GPU 硬件编码（NVENC/VideoToolbox/VAAPI/QSV，需本机有编码器）
  --no-check          跳过 hyperframes check --snapshots（仅重渲染且 HTML 未变时使用）
  --force-check       HTML 未变时也强制重跑 check（默认自动跳过）
  --reuse-render      HTML/音频/参数都没变时复用已校验的成片，跳过渲染
  --speed             语速倍率（透传给 pipeline）
  --voice-id          音色 ID（透传给 pipeline）
  --loudness          响度归一化 LUFS（透传给 pipeline；默认不做归一化，
                      开启后成片音频为 combined_loud.wav）
  --dry-run           只跑 pipeline --dry-run（不写任何文件）

超时相关环境变量（默认够用，长稿件/慢机器才需要调）：
  CTV_CHECK_TIMEOUT       check --snapshots 的墙钟上限秒数（默认 300）
  CTV_PARALLEL_TIMEOUT    TTS+配图并行阶段的整体上限秒数（默认 3600）
"""
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import datetime
import signal

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
# 技能目录（scripts/ 的上一级）：制作产物一律不得落在这里——SKILL.md 的路径
# 约定要求产物写在用户项目目录，混进技能目录会污染仓库、多次制作之间串台。
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)
from render_watch import resolve_command, _try_kill  # noqa: E402  复用 npx/.cmd 的 Windows 解析与进程树收尾
from _theme import list_theme_names  # noqa: E402  --theme choices 与 registry.json 单一数据源同步
from _voices import list_voice_ids  # noqa: E402  --voice-id choices 与音色注册表单一数据源同步
from _audio import validate_speed  # noqa: E402  坏语速 fail-fast
from _script_utils import setup_stdio  # noqa: E402  重定向场景 stdout 强制 UTF-8

# ── 制作报告：一次 run.py 跑完后，各步骤耗时/配图情况/跳过了什么散落在各
# 步骤自己的 stdout 里，人工翻起来很累。这里不改变任何步骤本身的行为，只是
# 在旁路记一份轻量流水账，跑完打印小结 + 写一份 production_report.json，
# 方便"这次跑得正不正常"一眼判断（例如：TTS 花了很久是不是网络问题、配图
# 是不是大面积没搜到而不是稿件写少了）。任何一步失败退出前也会尽量把已记录
# 的部分写盘，不指望"必须跑到最后才有报告"。
_REPORT = {"steps": [], "images": None, "skipped": []}
# 制作报告的 out 目录：main() 一解析出 -o/--output 就赋值
_REPORT_DIR = None
# --dry-run 承诺"不写任何文件"（见参数 help），旁路制作报告也不能例外
# ——失败路径同样只打印不落盘。main() 解析完参数就置 True。
_DRY_RUN = False
# 整个 run 的起始时刻：total_seconds 用真实墙钟，而不是把各步骤耗时直接
# 相加（TTS 与配图并行时两步各记各的全程，相加会大于真实墙钟）。
_RUN_T0 = None


def _total_seconds():
    """本次 run 的真实墙钟时长；_RUN_T0 未赋值时退回各步骤耗时之和。"""
    if _RUN_T0:
        return round(time.time() - _RUN_T0, 1)
    return round(sum(s["seconds"] for s in _REPORT["steps"]), 1)


def _write_json_atomic(path, data, indent=2):
    """原子写 JSON：写 <path>.tmp → fsync → os.replace 覆盖。

    制作产物（production_report.json / .render_cache.json）被 Ctrl-C 或断电
    打断在写到一半时，落盘的是截断的 JSON——下次 --resume 读它要么抛
    JSONDecodeError traceback，要么更糟：读到半份数据还当有效缓存用。
    原子替换保证"要么完整要么根本不存在"，中断后重跑自然重来一遍。
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_report(path):
    if _DRY_RUN:
        return
    try:
        _REPORT["finished_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        _REPORT["total_seconds"] = _total_seconds()
        _write_json_atomic(path, _REPORT)
    except Exception as e:
        print(f"[run] 写制作报告失败（不影响主流程产出）：{e}", file=sys.stderr)


def _print_report_summary():
    print("\n" + "=" * 44)
    print("[run] 本次制作报告")
    print("=" * 44)
    for s in _REPORT["steps"]:
        tag = "OK  " if s["ok"] else "FAIL"
        print(f"  [{tag}] {s['name']:<28} {s['seconds']:>7.1f}s")
    total = _total_seconds()
    print(f"  {'合计':<34} {total:>7.1f}s")
    if _REPORT["images"] is not None:
        img = _REPORT["images"]
        if img.get("skipped_reason"):
            print(f"  配图：跳过（{img['skipped_reason']}）")
        else:
            print(f"  配图：{img['matched']}/{img['total']} 条内容已定稿"
                  + (f"，{img['missing']} 条待人工审阅/兜底" if img.get("missing") else ""))
    if _REPORT["skipped"]:
        print("  跳过的步骤：" + "；".join(_REPORT["skipped"]))
    print("=" * 44)


def _script(name):
    return os.path.join(SCRIPTS_DIR, name)


def _split_missing_by_candidates(missing, cand_json_path, candidates_dir):
    """把缺图段落按"有没有候选可审阅"分成两类。

    缺图拦截必须分开指路，否则会给出互相矛盾的下一步：
      · 有候选、待审阅定稿 → --pick
      · 本轮压根没有候选（按 --search-sids 路由交给方式 B/C/D，或候选已被
        --pick 0 全部判否）→ --pick 对它无效，只能走方式 B/C/D 补图
    混成一条"然后执行 --pick"的提示，第二类段落会原地打转。

    Returns:
        (pending_pick, no_candidate, review_lines)
    """
    pending_pick, no_candidate, review_lines = [], [], []
    try:
        with open(cand_json_path, encoding="utf-8") as _f:
            cj = json.load(_f) if os.path.isfile(cand_json_path) else {}
    except Exception:
        cj = {}
    if not isinstance(cj, dict):
        cj = {}
    for sid in missing:
        entry = cj.get(sid) or {}
        files = [c.get("file") for c in entry.get("candidates", [])
                 if c.get("file")]
        if files:
            pending_pick.append(sid)
            paths = "  ".join(os.path.join(candidates_dir, f) for f in files)
            review_lines.append(
                f"  · {sid}（{entry.get('title', '')}）候选原图：{paths}")
        else:
            no_candidate.append(sid)
    return pending_pick, no_candidate, review_lines


def _is_inside(child, parent):
    """child 是否位于 parent 目录内（realpath 归一化，软链接也能判对）。"""
    try:
        child_r = os.path.realpath(child)
        parent_r = os.path.realpath(parent)
    except OSError:
        return False
    return child_r == parent_r or child_r.startswith(parent_r + os.sep)


def _guard_not_in_skill_dir(*labeled_paths):
    """制作产物落在技能目录内时 fail-fast。

    文档要求"从其他目录调用时先 cd 到技能目录"，而 -o/--project 又是相对
    CWD 解析的——照抄文档里的示例命令（`-o audio_output`）恰好会把
    audio_output/ 与 hf-project/ 建在技能目录里，正好踩中同一份文档里
    "不要在技能目录内生成任何文件"的禁令。这道防线把约定变成机械拦截。
    """
    offenders = [(label, p) for label, p in labeled_paths if _is_inside(p, SKILL_DIR)]
    if not offenders:
        return
    lines = "\n".join(f"  · {label} -> {p}" for label, p in offenders)
    raise SystemExit(
        f"[run] 制作产物不能写在技能目录内（{SKILL_DIR}）：\n{lines}\n"
        "产物混进技能目录会污染技能仓库，也容易在多次制作之间串台。\n"
        "请 cd 到你的项目目录后重跑（用脚本绝对路径调用即可，不必 cd 到技能目录）：\n"
        "  cd <你的项目目录>\n"
        f"  python {_script('run.py')} --source <稿件路径> -o audio_output\n"
        "或用 -o/--project 显式指定技能目录之外的绝对路径。")


# `hyperframes check --snapshots` 的墙钟上限（秒）。它不经 render_watch 的
# --max-wait 兜底，Chrome 卡住时会永久阻塞。实测抓帧 ~30s，300s 很宽裕。
# 大项目/慢机器可用环境变量 CTV_CHECK_TIMEOUT 调大。
CHECK_TIMEOUT = int(os.environ.get("CTV_CHECK_TIMEOUT", "300"))

# TTS 与配图并行阶段的整体墙钟上限（秒）。两个子进程各自可能卡在没设超时
# 的路径上，没有这个上限 run.py 会无限轮询而不报错。默认 1 小时，长稿件
# 用 CTV_PARALLEL_TIMEOUT 调大。
PARALLEL_TIMEOUT = int(os.environ.get("CTV_PARALLEL_TIMEOUT", "3600"))


def _norm_rc(rc):
    """把子进程退出码规范成 shell 能读懂的正数。

    被信号终止时 subprocess 的 returncode 是负数（-15 = SIGTERM），直接
    sys.exit(-15) 在 shell 里显示成 241，把"被信号杀掉"伪装成一个莫名
    其妙的失败码。统一收敛：有效正数原样传播，其余按失败退出 1。
    """
    return rc if isinstance(rc, int) and rc > 0 else 1


def _run(cmd, cwd=None, step_name=None, timeout=None):
    """跑一条子命令，失败即记报告并退出。

    timeout（秒）：headless Chrome 挂死的兜底。渲染步骤走 render_watch.py
    （自带 --max-wait），check 步骤是裸 npx hyperframes check --snapshots——
    浏览器卡在 "Timed out closing browser" 时它会永久阻塞且无日志，必须
    在这里设上限；超时后收掉进程树再退出，不留常驻 Chrome。
    """
    resolved = resolve_command(cmd)
    print(f"\n>>> {' '.join(resolved)}", flush=True)
    t0 = time.time()
    try:
        subprocess.run(resolved, cwd=cwd, check=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        # 收树：Chrome 常驻会拖慢后续渲染并堆积临时 profile 目录。
        # 只收本命令自己的进程树——绝不按进程名无差别 kill，那样会误杀
        # 用户自己开的浏览器。
        _p = getattr(e, "process", None)
        if _p is not None:
            try:
                _try_kill(_p)
            except Exception:
                pass
        if step_name:
            _REPORT["steps"].append(
                {"name": step_name, "seconds": round(time.time() - t0, 1),
                 "ok": False, "timeout": timeout})
            _write_report(_report_path())
        print(f"[run] 步骤超时（>{timeout}s）：{' '.join(resolved)}\n"
              "      已尝试收掉残留进程。若反复超时，可加 --no-check 跳过"
              "快照 QA（HTML 未变时下次会自动跳过），或单独复现该命令看"
              "Chrome 是否卡在关闭阶段。", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        if step_name:
            _REPORT["steps"].append(
                {"name": step_name, "seconds": round(time.time() - t0, 1), "ok": False})
            _write_report(_report_path())
        print(f"[run] 步骤失败（退出码 {e.returncode}）：{' '.join(resolved)}",
              file=sys.stderr)
        sys.exit(e.returncode or 1)
    except (FileNotFoundError, OSError) as e:
        # 命令本身起不来（可执行文件不存在/无权限等）：按步骤失败处理并
        # 报对人，不再裸抛 traceback
        if step_name:
            _REPORT["steps"].append(
                {"name": step_name, "seconds": round(time.time() - t0, 1), "ok": False})
            _write_report(_report_path())
        print(f"[run] 步骤失败（无法启动命令 {resolved[0]}）：{e}",
              file=sys.stderr)
        sys.exit(1)
    if step_name:
        _REPORT["steps"].append(
            {"name": step_name, "seconds": round(time.time() - t0, 1), "ok": True})


def _report_path():
    """制作报告写到哪：优先 out 目录（跟 timing_manifest.json 放一起），
    -o/--output 还没解析出来（极早期失败）时退回当前目录。"""
    return os.path.join(_REPORT_DIR, "production_report.json") if _REPORT_DIR \
        else "production_report.json"


def _sha256_file(path):
    """文件内容 sha256（check 缓存 / 成片复用键）。读不到返回 None。"""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


_CHECK_CACHE_NAME = ".check_cache.json"
_RENDER_CACHE_NAME = ".render_cache.json"


def _cache_load(project, name):
    p = os.path.join(project, name)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _cache_save(project, name, data):
    try:
        # 原子写：渲染缓存被打断成半截 JSON 时，_cache_load 读到坏数据会
        # 让"HTML 没变"的判断失真（要么每次都重跑 check 白等 30s，要么
        # 更糟——把过期的命中当成有效命中）。
        _write_json_atomic(os.path.join(project, name), data, indent=1)
    except OSError:
        pass  # 缓存写失败不影响主流程（下次多跑一轮 check/render 而已）


def _check_cache_hit(project, html_path, snaps_dir, force):
    """HTML 未变且上次快照还在 → 跳过 check（省一轮 ~30s 的 Chrome 抓帧）。

    键是 index.html 的内容 hash——gen 阶段任何影响画面的输入（稿件/配图/
    主题/模式/尺寸）最终都落在 HTML 字节里，改了必然 miss；快照目录被
    手动删掉也 miss。--force-check 无条件 miss。
    """
    if force:
        return False
    html_sha = _sha256_file(html_path)
    if not html_sha or not os.path.isdir(snaps_dir):
        return False
    cached = _cache_load(project, _CHECK_CACHE_NAME).get(
        os.path.basename(snaps_dir))
    return bool(cached) and cached.get("html_sha") == html_sha


def _check_cache_put(project, html_path, snaps_dir):
    html_sha = _sha256_file(html_path)
    if not html_sha:
        return
    data = _cache_load(project, _CHECK_CACHE_NAME)
    data[os.path.basename(snaps_dir)] = {
        "html_sha": html_sha, "snaps": os.path.basename(snaps_dir)}
    _cache_save(project, _CHECK_CACHE_NAME, data)


def _render_cache_key(html_path, manifest_path, args):
    """成片复用键：HTML + manifest（含音频时长/路径变化）+ 渲染参数。

    任一影响成片内容的输入变了键就变；渲染参数（fps/quality/workers/gpu）
    变了也变——不同参数的产物不可互换。
    """
    parts = [
        _sha256_file(html_path) or "",
        _sha256_file(manifest_path) if manifest_path else "",
        f"fps={args.fps}", f"quality={args.quality}",
        f"workers={args.workers}", f"gpu={bool(args.gpu)}",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _image_coverage(manifest_path, images_json):
    """返回 (all_sids, missing_sids)：manifest 中 news/seg 段落的全部 sid，
    以及其中 images.json 里还没配图的那些。

    之前 run.py 里同时存在两份"怎么从 manifest 里挑出 news/seg 段落 id"的
    逻辑——一份在这个函数内部，一份在 main() 里为了统计总数又重新拼了一遍
    （还多读了一次 manifest 文件）。把"news/seg 前缀是配图相关段落"这个
    命名约定收敛到一个地方，main() 只管要总数/缺图列表，不需要知道这个
    约定长什么样——manifest 的 id 命名规则改了，也只用改这一处。
    """
    # 走 _contracts 的加载器而不是裸 json.load：上一轮若被中断在写 manifest
    # 的半途，文件是截断的 JSON，裸 load 会抛 JSONDecodeError traceback，
    # 用户看到的堆栈跟"TTS 产物坏了、该重跑"这个真实原因毫无关系。
    # load_timing_manifest 会把缺字段/坏结构报成一句人话。
    sys.path.insert(0, SCRIPTS_DIR)
    from _contracts import load_timing_manifest, is_content_sid
    manifest = load_timing_manifest(manifest_path)
    sids = [seg.get("id", "") for seg in manifest.get("segments", [])
            if is_content_sid(seg.get("id"))]
    if not sids:
        return [], []
    if not os.path.isfile(images_json):
        return sids, sids
    with open(images_json, "r", encoding="utf-8") as f:
        mapping = json.load(f)
    missing = [s for s in sids if s not in mapping]
    return sids, missing


def main():
    setup_stdio()
    global _REPORT_DIR, _REPORT, _DRY_RUN, _RUN_T0
    # 同一进程里重复调用 main()（import run 后直接调用的测试/嵌入式用法）
    # 时，模块级 _REPORT 不能累积上一次的 steps/skipped——每次都从空白
    # 报告开始。
    _REPORT = {"steps": [], "images": None, "skipped": []}
    _DRY_RUN = False
    _RUN_T0 = time.time()
    parser = argparse.ArgumentParser(description="信源转视频一键编排（薄组合层）")
    parser.add_argument("--source", required=True, help="segments_source.json 路径")
    parser.add_argument("-o", "--output", default="audio_output",
                        help="音频/中间产物输出目录（默认 audio_output）")
    parser.add_argument("--project", default=None,
                        help="HTML/渲染项目目录（默认 <output 同级>/hf-project）")
    parser.add_argument("--no-images", action="store_true", help="跳过配图")
    parser.add_argument("--refresh-images", action="store_true",
                        help="配图不启用 --resume（重新搜索/下载全部图片）")
    parser.add_argument("--search-sids", default=None,
                        help="只对这些段落跑搜图（逗号分隔 sid，如 'seg1,seg3'，"
                             "透传给 search_images.py --sids）——按第 4 步路由"
                             "只有部分段落走方式 A 时用，其余段落走方式 B/C/D，"
                             "由缺图拦截兜住")
    parser.add_argument("--until", choices=["tts", "images", "html", "render"],
                        default="render", help="跑到该步骤为止（默认 render）")
    parser.add_argument("--no-verify", action="store_true", help="渲染后跳过校验")
    parser.add_argument("--no-resume", action="store_true", help="TTS 不用 --resume")
    parser.add_argument("--aspect", default="landscape",
                        choices=["landscape", "portrait", "both"],
                        help="画幅：landscape=1920x1080 横屏 16:9（默认），"
                             "portrait=1080x1440 竖屏 3:4（抖音等平台全屏"
                             "播放零裁切），both=横竖两版一次产出")
    parser.add_argument("--theme", default="cream", choices=list_theme_names())
    parser.add_argument("--fps", type=int, default=24, choices=[24, 30, 60],
                        help="输出帧率（默认 24；Hyperframes 官方支持 24/30/60，"
                             "非法值直通到渲染端只会以更隐晦的报错失败）")
    parser.add_argument("--quality", default="standard",
                        choices=["draft", "standard", "high"],
                        help="渲染质量（默认 standard）")
    parser.add_argument("--workers", type=int, default=6,
                        help="渲染抓帧 worker 数（默认 6。2026-08 实测：110s 成片"
                             " 4/6/8 workers = 194/174/162s，6 起收益递减——抓帧"
                             " 存在浏览器主线程串行瓶颈；低配机器若遇 V8 堆崩溃"
                             " 再降回 4）")
    parser.add_argument("--gpu", action="store_true",
                        help="启用 GPU 硬件编码（NVENC/VideoToolbox/VAAPI/QSV）")
    parser.add_argument("--no-check", action="store_true",
                        help="跳过 hyperframes check --snapshots（重渲染且 HTML 未变时用）")
    parser.add_argument("--force-check", action="store_true",
                        help="HTML 未变时也强制重跑 check（默认：HTML hash 未变且"
                             "上次快照还在时自动跳过，省一轮 ~30s 的 Chrome 抓帧）")
    parser.add_argument("--reuse-render", action="store_true",
                        help="成片复用：HTML/音频/渲染参数都没变且已有通过校验的"
                             " 成片时跳过渲染直接复用（幂等重跑场景，省 1-3 分钟；"
                             " 复用前仍会跑 verify_render 校验，失败照常重渲）")
    parser.add_argument("--speed", type=float, default=None)
    parser.add_argument("--voice-id", default=None, choices=list_voice_ids())
    parser.add_argument("--dry-run", action="store_true",
                        help="只跑 pipeline --dry-run（不写文件）")
    parser.add_argument("--loudness", type=float, default=None,
                        help="对最终音频做响度归一化（LUFS，透传给 pipeline.py "
                             "--loudness）。平台有响度要求时用（如 -16）；"
                             "默认不做归一化。开启后成片音频为 combined_loud.wav")
    args = parser.parse_args()
    _DRY_RUN = args.dry_run

    if args.speed is not None:
        try:
            validate_speed(args.speed)
        except ValueError as e:
            parser.error(str(e))

    out = os.path.abspath(args.output)
    project = os.path.abspath(args.project) if args.project else \
        os.path.join(os.path.dirname(out) or ".", "hf-project")
    # --dry-run 承诺不写任何文件，不需要拦产物路径
    if not args.dry_run:
        _guard_not_in_skill_dir(("-o/--output", out), ("--project", project))
    _REPORT_DIR = out
    _REPORT["source"] = os.path.abspath(args.source)
    _REPORT["output"] = out
    _REPORT["project"] = project
    _REPORT["started_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    # 制作参数进报告——check_series.py 靠它对比各集实际用的
    # theme/voice/speed 等，跨集漂移不用翻命令行历史就能发现。
    # speed/voice_id 为 None 表示用默认值，检查侧跳过不比。
    _REPORT["params"] = {
        "theme": args.theme,
        "aspect": args.aspect,
        "fps": args.fps,
        "quality": args.quality,
        "speed": args.speed,
        "voice_id": args.voice_id,
        "loudness": args.loudness,
    }

    # ── 第 3 步：TTS ──────────────────────────────────────────────
    tts_cmd = [sys.executable, _script("pipeline.py"),
               "--source", args.source, "-o", out]
    if not args.no_resume:
        tts_cmd.append("--resume")
    if args.speed is not None:
        tts_cmd += ["--speed", str(args.speed)]
    if args.voice_id:
        tts_cmd += ["--voice-id", args.voice_id]
    if args.loudness is not None:
        # 响度归一化在 pipeline 末端对拼接后的人声轨执行（可选混入 BGM 之后），
        # 归一化后的音频由 manifest 的 combined_audio 指路，下游 HTML/校验
        # 都从 manifest 取，不在这里硬编码文件名。
        if not math.isfinite(args.loudness):
            parser.error(f"--loudness 必须是有限数值（LUFS，收到 {args.loudness}）")
        tts_cmd += ["--loudness", str(args.loudness)]
    if args.dry_run:
        tts_cmd.append("--dry-run")
    # ── 第 3/4 步：TTS + 配图 ─────────────────────────────────────
    # 两步都只依赖 segments_source.json，互不等待对方的实际产物——配图标题走
    # search_images.py --source 直接从稿件取（不必等 timing_manifest.json），
    # id 分配规则跟 pipeline.py --source 最终写进 manifest 的完全一致（见
    # _titles.py 的 extract_titles_from_segments_source 说明），因此能真的
    # 并行跑，省下配图搜索那部分的墙钟时间。只有明确不需要配图这一步时
    # （--no-images / --until tts / --dry-run / 没配 STEPFUN_API_KEY）才退回
    # 纯顺序执行。
    manifest = os.path.join(out, "timing_manifest.json")
    images_json = os.path.join(project, "images.json")

    want_images = not args.dry_run and not args.no_images and args.until != "tts"
    has_key = False
    if want_images:
        try:
            sys.path.insert(0, SCRIPTS_DIR)
            from _env import get_key
            has_key = bool(get_key("STEPFUN_API_KEY"))
        except Exception:
            has_key = False

    if want_images and has_key:
        images_cmd = [sys.executable, _script("search_images.py"),
                      "--source", args.source, "-o", os.path.join(project, "images")]
        if not args.refresh_images:
            images_cmd.append("--resume")
        if args.search_sids:
            images_cmd += ["--sids", args.search_sids]
        resolved_tts = resolve_command(tts_cmd)
        resolved_images = resolve_command(images_cmd)
        print(f"\n>>> [并行启动] {' '.join(resolved_tts)}", flush=True)
        print(f">>> [并行启动] {' '.join(resolved_images)}", flush=True)
        _t0 = time.time()
        p_tts = subprocess.Popen(resolved_tts, start_new_session=True)
        p_images = subprocess.Popen(resolved_images, start_new_session=True)
        # POSIX 下 start_new_session 让子进程脱离本进程组：Ctrl+C 只中断
        # run.py，TTS/配图子进程会继续跑（继续烧 TTS 时间）。装 SIGINT
        # handler，中断时先收掉两棵子进程树再退出。
        #
        # Windows 同样注册：CPython 在 Windows 上会把控制台 Ctrl-C 转成
        # SIGINT 派给 handler（只有 os.kill 发 CTRL_C_EVENT 才是另一条路），
        # 之前用 `if os.name != "nt"` 跳过是白放掉一层保护——TTS 是按句
        # 计费的，中断后继续跑就是在烧额度。_try_kill 内部已按平台分流
        # （Windows 走 taskkill /T /F，POSIX 走 killpg）。
        def _on_sigint(signum, frame):
            for _p in (p_tts, p_images):
                if _p.poll() is None:
                    try:
                        _try_kill(_p)
                    except Exception:
                        pass
            # 130 是 "被 SIGINT 终止" 的惯例退出码；Windows 上 sys.exit
            # 传负数会被 shell 显示成 241，130 才是可读的。
            sys.exit(130)
        try:
            signal.signal(signal.SIGINT, _on_sigint)
        except (ValueError, OSError):
            pass  # 非主线程等场景装不上，那就退回默认行为（不致命）
        # 轮询两个子进程而不是先后阻塞 wait()：一来各步耗时在"自身退出"
        # 的时点测量（先后 wait 会把先完成那步的耗时记成"等到另一个也结
        # 束"，两步相加还大于真实墙钟）；二来任一子进程非零退出就立刻收
        # 掉另一个（进程树）并传播失败退出，不再陪跑完剩下的那个。
        tts_ret = images_ret = None
        tts_elapsed = images_elapsed = 0.0
        fail_cmd = fail_rc = None
        # 整体 deadline：两个子进程都可能卡在自身没有超时的路径上（TTS 网络
        # 挂起、搜图下载卡死）。没有 deadline 时这个 5Hz 轮询会永久转下去——
        # run.py 既不退出也不报错，终端看起来"还在跑"，实际上已经死了。
        deadline = time.time() + PARALLEL_TIMEOUT
        while tts_ret is None or images_ret is None:
            if time.time() > deadline:
                for p in (p_tts, p_images):
                    if p.poll() is None:
                        _try_kill(p)
                        try:
                            p.wait(timeout=15)
                        except Exception:
                            pass
                _stuck = [n for n, r in (("TTS", tts_ret), ("配图搜索", images_ret))
                          if r is None]
                _REPORT["steps"].append(
                    {"name": "TTS（与配图并行）", "seconds": round(time.time() - _t0, 1),
                     "ok": tts_ret == 0})
                _REPORT["steps"].append(
                    {"name": "配图搜索（与 TTS 并行）",
                     "seconds": round(images_elapsed, 1), "ok": images_ret == 0})
                _write_report(_report_path())
                print(f"[run] 并行阶段超时（>{PARALLEL_TIMEOUT}s），"
                      f"仍未结束：{'、'.join(_stuck)}。已收掉子进程。\n"
                      f"      稿件很长时可用环境变量 CTV_PARALLEL_TIMEOUT "
                      f"调大；反复卡住建议拆开逐步跑定位是哪一步。",
                      file=sys.stderr)
                sys.exit(1)
            if tts_ret is None and p_tts.poll() is not None:
                tts_ret = p_tts.returncode
                tts_elapsed = time.time() - _t0
                if tts_ret != 0 and fail_rc is None:
                    fail_cmd, fail_rc = resolved_tts, tts_ret
            if images_ret is None and p_images.poll() is not None:
                images_ret = p_images.returncode
                images_elapsed = time.time() - _t0
                if images_ret != 0 and fail_rc is None:
                    fail_cmd, fail_rc = resolved_images, images_ret
            if fail_rc is not None:
                for p in (p_tts, p_images):
                    if p.poll() is None:
                        _try_kill(p)
                        try:
                            p.wait(timeout=15)
                        except Exception:
                            pass
                if tts_ret is None:
                    tts_ret = p_tts.returncode
                    tts_elapsed = time.time() - _t0
                if images_ret is None:
                    images_ret = p_images.returncode
                    images_elapsed = time.time() - _t0
                break
            if tts_ret is None or images_ret is None:
                time.sleep(0.2)
        _REPORT["steps"].append({"name": "TTS（与配图并行）", "seconds": round(tts_elapsed, 1),
                                 "ok": tts_ret == 0})
        _REPORT["steps"].append({"name": "配图搜索（与 TTS 并行）",
                                 "seconds": round(images_elapsed, 1), "ok": images_ret == 0})
        if fail_rc is not None:
            _write_report(_report_path())
            _stage = ("TTS" if tts_ret not in (0, None) else "配图搜索")
            print(f"[run] 步骤失败（退出码 {fail_rc}）：{' '.join(fail_cmd)}",
                  file=sys.stderr)
            sys.exit(_norm_rc(fail_rc))
    else:
        _run(tts_cmd, step_name="TTS")
        if want_images and not has_key:
            if os.path.isfile(images_json):
                # 无 Key 但已有 images.json（手动走方式 B/C/D 补的图）：
                # 不是纯文字版兜底——稍后照常带 --images 渲染已定稿配图。
                # 报告里也要如实区分：记成"纯文字版兜底"会让 production_report
                # 与实际产物矛盾（明明带图渲染，报告却说跳过配图）。
                print("[run] 未找到 STEFUN_API_KEY，跳过搜图；"
                      "检测到已有 images.json，将使用其中已定稿的配图渲染",
                      flush=True)
                _REPORT["images"] = {"skipped_reason":
                                     "未配置 STEFUN_API_KEY，跳过搜图"
                                     "（使用已有 images.json 的手动配图）"}
            else:
                print("[run] 未找到 STEFUN_API_KEY，跳过配图（纯文字版兜底）",
                      flush=True)
                _REPORT["images"] = {"skipped_reason": "未配置 STEFUN_API_KEY，纯文字版兜底"}
            _REPORT["skipped"].append("配图搜索（无 STEFUN_API_KEY）")
        elif args.no_images:
            _REPORT["images"] = {"skipped_reason": "--no-images（纯文字版）"}
            _REPORT["skipped"].append("配图（--no-images）")

    # images.json 是否可用：有 Key 时由搜图落盘；无 Key 时手动补图（方式
    # B/C/D，不依赖 STEFUN_API_KEY）同样落这里。只要本次想要配图且映射已
    # 存在，第 5 步就带 --images 渲染。has_images 必须在此统一赋值、不能
    # 放进有 Key 的并行分支——否则无 Key 手动补图后重跑会被静默无视。
    has_images = want_images and os.path.isfile(images_json)

    if args.dry_run or args.until == "tts":
        if args.until == "tts":
            _REPORT["skipped"].append("html/render（--until tts）")
        # dry-run 承诺"不写任何文件"：_write_report 内部短路不落盘，只打
        # 印摘要（非 dry-run 的 --until tts 仍是写报告 + 打印）。
        _write_report(_report_path())
        _print_report_summary()
        return

    # 覆盖率统计：有 Key（搜图跑过）或 images.json 已存在（无 Key 时手动走
    # 方式 B/C/D 补图）都算。不能只看 has_images——search_images.py 的 dump
    # 模式（不带 --pick）只写 candidates.json、不写 images.json，首跑时
    # images.json 必然不存在，只看它会静默跳过缺图拦截、直接渲染无图成片；
    # _image_coverage 对"images.json 不存在"已按"全部缺图"处理。
    missing = []
    if want_images and (has_key or has_images):
        # "不要瞎配图"：缺图的段落需要当前对话模型审阅候选并 --pick，
        # 或改用 ImageGen 生图。
        all_sids, missing = _image_coverage(manifest, images_json)
        _REPORT["images"] = {
            "total": len(all_sids), "matched": len(all_sids) - len(missing),
            "missing": len(missing)}

    # --until images：配图搜索跑完即正常结束（exit 0）。必须放在缺图拦截
    # 之前——调试意图是"看看这轮搜到了哪些候选/覆盖率如何"，被 exit 2
    # 劫持就和"要渲染到底但缺图"混成了同一种失败。
    if args.until == "images":
        _REPORT["skipped"].append("html/render（--until images）")
        _write_report(_report_path())
        _print_report_summary()
        return

    if want_images and missing:
        if has_key:
            # 纯独立审阅：run.py 走到缺图分支时，不再生成拼图，而是把每个缺图
            # 段落的候选原图全路径直接列在提示里，让 agent 逐张、全分辨率 Read
            # 判断图↔题贴合度（缩略图/拼图会漏掉水印、角标、图内烧字等细节）。
            candidates_dir = os.path.join(project, "images")
            cand_json = os.path.join(candidates_dir, "candidates.json")
            pending_pick, no_candidate, review_lines = _split_missing_by_candidates(
                missing, cand_json, candidates_dir)
            if not review_lines:
                # 兜底：candidates.json 缺失时直接枚举 images 目录里的候选文件
                import glob as _glob
                cands = sorted(_glob.glob(os.path.join(candidates_dir, "*_cand*")))
                if cands:
                    review_lines.append("  · 候选原图文件："
                                        + "  ".join(cands))

            msg = ("[run] 以下段落还没有定稿配图：" + ", ".join(missing) + "。\n")

            if pending_pick:
                msg += ("\n【有候选 · 待你审阅定稿】" + "、".join(pending_pick) + "\n"
                        "请逐张 Read 下列候选原图（全分辨率，不要看缩略图）判断"
                        "图↔题贴合度：标题党 / 带平台角标(如 B站、什么值得买) / "
                        "图内烧字 / 水印图一律不采用，改走 ImageGen 生图。\n")
                msg += "\n".join(review_lines) + "\n"
                _example = ",".join(f"{s}:1" for s in pending_pick[:3])
                msg += ("然后执行（数字为候选编号 1-based，0=不采用改生图）：\n"
                        f"  python {_script('search_images.py')} -o {candidates_dir} "
                        f"--resume --pick \"{_example}\"\n"
                        "（--resume 保留 images.json 里已定稿的其它条目，"
                        "多轮补图不会互相覆盖）\n")
            elif review_lines:
                msg += (f"\n请用当前对话窗口的模型逐张 Read 下列候选原图"
                        f"（全分辨率，不要看缩略图）审阅，或参考 {cand_json}：\n")
                msg += "\n".join(review_lines) + "\n"

            if no_candidate:
                msg += ("\n【没有候选 · 需按方式 B/C/D 补图】" + "、".join(no_candidate) + "\n"
                        "  这些段落本轮没有候选图（按 --search-sids 路由交给了方式 B/C/D，"
                        "或候选已全部判为不合适）——--pick 对它们无效，不要对它们跑搜图。\n"
                        "  请按第 4 步用 ImageGen 生图（方式 B）或 gen_charts.py 图表（方式 C）补图："
                        f"图片放进 {candidates_dir}，并在 {images_json} 写好该段映射后重跑本命令。\n")

            msg += "完成后重跑本命令。"
            print(msg, file=sys.stderr)
        else:
            # 无 Key 的手动补图流程（方式 B/C/D）没有搜图候选可审阅，拦截
            # 提示直接指路生图/图表，不引导去跑需要 Key 的 search_images。
            print("[run] 以下段落还没有定稿配图：" + ", ".join(missing) + "。\n"
                  "请按第 4 步用 ImageGen 生图（方式 B）或图表（方式 C）补图："
                  f"图片放进 {os.path.join(project, 'images')}，并在 {images_json} "
                  "写好该段映射后重跑本命令（方式 B/C/D 均不依赖 STEFUN_API_KEY）。",
                  file=sys.stderr)
        _write_report(_report_path())
        sys.exit(2)

    # （--until images 的提前返回已上移到缺图拦截之前：配图搜索完成即
    #  正常 return，不再被缺图 exit 2 劫持。）

    # ── 第 5 步 a：生成 composition HTML ──────────────────────────
    html_cmd = [sys.executable, _script("gen_hyperframes.py"),
                "-m", manifest, "-o", os.path.join(project, "index.html"),
                "--aspect", args.aspect, "--theme", args.theme]
    if has_images:
        html_cmd += ["--images", images_json]
    _run(html_cmd, step_name="生成 HTML")

    # 不设独立的版式几何校验步骤（puppeteer 量坐标那类）：正常制作中
    # 模板/布局不变，几何溢出类问题罕见，check --snapshots 关键帧 +
    # agent 逐帧视觉审阅（强制步骤）是同一道防线；为此维护一条 Node
    # 依赖链不值（打包遗漏、环境漂移都从它来）。

    if args.until == "html":
        _REPORT["skipped"].append("check/render（--until html）")
        _write_report(_report_path())
        _print_report_summary()
        return

    # ── 第 5 步 b：检查 HTML + 抓取标注关键帧（快速 QA，不渲染整片）──
    if not args.no_check:
        def _check(step_label):
            # check 是裸 `npx hyperframes check --snapshots`，不经
            # render_watch 的 --max-wait 兜底——Chrome 卡在
            # "Timed out closing browser" 时会永久阻塞、无日志。实测抓帧
            # ~30s，300s 已很宽裕；超时即收树退出，不留常驻 Chrome。
            _run(["npx", "hyperframes", "check", "--snapshots"], cwd=project,
                 step_name=step_label, timeout=CHECK_TIMEOUT)

        if args.aspect == "both":
            # hyperframes check 要求项目只有一个根 composition（多个根级
            # HTML 带 data-composition-id 会报 multiple_root_compositions
            # 错——运行时把两个入口都当播放起点，音频双放）。而 --aspect
            # both 恰好生成 index.html + index.vertical.html 两个根；check
            # CLI 又不像 render 那样支持
            # -c 指定入口，只能分两轮跑：每轮把另一画幅的 HTML 改名成
            # .disabled（不再匹配 *.html glob）临时隔离，try/finally 保证
            # 无论成败都恢复。快照按画幅分目录保留（snapshots.landscape/
            # snapshots.vertical），供第 6 步逐帧审阅两版画面。
            # 竖屏轮还有第二层约束：check 只认 index.html 这个文件名作
            # 入口（index.vertical.html 即使是项目里唯一的根也报 No
            # composition found），所以竖屏轮是把 vertical HTML 临时改名
            # 成 index.html 顶替上场，跑完再换回来。
            _idx = os.path.join(project, "index.html")
            _vhtml = os.path.join(project, "index.vertical.html")
            _snaps = os.path.join(project, "snapshots")

            # 自愈：上次运行若在隔离期间被强杀（SIGKILL / 断电 / 超时收树），
            # .disabled 会留在盘上——下次 --aspect both 走到下面的存在性检查
            # 就 exit 1「找不到 index.vertical.html」，而报错完全不提真正
            # 原因（只是上次中断的残留），只能手工改名。进隔离前先扫一遍
            # 把残留恢复回来，让中断可自愈。
            import glob as _glob_disabled
            for _stale in sorted(_glob_disabled.glob(
                    os.path.join(project, "*.html.disabled"))):
                _back = _stale[: -len(".disabled")]
                try:
                    if os.path.exists(_back):
                        # 同名正常文件也在：这份 .disabled 是过期副本，删掉
                        os.remove(_stale)
                    else:
                        os.replace(_stale, _back)
                        print(f"[run] 已恢复上次中断残留的隔离文件："
                              f"{os.path.basename(_back)}", file=sys.stderr)
                except OSError as _e:
                    print(f"[warn] 无法处理残留文件 {_stale}：{_e}",
                          file=sys.stderr)

            # 竖屏 HTML 缺失（上一步 gen 半途失败/被手工清理）时裸 os.replace
            # 会 FileNotFoundError 且报告里没有任何线索——提前拦下指明原因
            for _need in (_idx, _vhtml):
                if not os.path.isfile(_need):
                    print(f"[run] --aspect both 需要 {_idx} 与 {_vhtml} 同时存在，"
                          f"但找不到 {os.path.basename(_need)}——"
                          f"重新跑生成 HTML 步骤后再试", file=sys.stderr)
                    sys.exit(1)

            def _isolate(html_path):
                os.replace(html_path, html_path + ".disabled")

            def _restore(html_path):
                os.replace(html_path + ".disabled", html_path)

            def _keep_snapshots(tag):
                if os.path.isdir(_snaps):
                    _dest = f"{_snaps}.{tag}"
                    if os.path.isdir(_dest):
                        import shutil
                        shutil.rmtree(_dest)
                    os.replace(_snaps, _dest)

            _isolate(_vhtml)
            try:
                if _check_cache_hit(project, _idx, f"{_snaps}.landscape",
                                    args.force_check):
                    print("[run] 横屏 HTML 未变且快照还在，跳过 check"
                          "（--force-check 可强制重跑）", flush=True)
                    _REPORT["skipped"].append("check（横屏，HTML 未变）")
                else:
                    _check("check --snapshots（横屏）")
                    _keep_snapshots("landscape")
                    _check_cache_put(project, _idx, f"{_snaps}.landscape")
            finally:
                _restore(_vhtml)
            # 竖屏轮的 hash 必须在改名前取——rename 之后 _vhtml 已不存在，
            # _sha256_file 返回 None 会让缓存永远 miss
            _v_sha = _sha256_file(_vhtml)
            _isolate(_idx)
            os.replace(_vhtml, _idx)  # vertical 顶替 index.html 名义
            try:
                _v_snaps = f"{_snaps}.vertical"
                _v_cached = (not args.force_check and _v_sha
                             and os.path.isdir(_v_snaps)
                             and _cache_load(project, _CHECK_CACHE_NAME).get(
                                 "snapshots.vertical", {}).get("html_sha") == _v_sha)
                if _v_cached:
                    print("[run] 竖屏 HTML 未变且快照还在，跳过 check"
                          "（--force-check 可强制重跑）", flush=True)
                    _REPORT["skipped"].append("check（竖屏，HTML 未变）")
                else:
                    _check("check --snapshots（竖屏）")
                    _keep_snapshots("vertical")
                    if _v_sha:
                        _vdata = _cache_load(project, _CHECK_CACHE_NAME)
                        _vdata["snapshots.vertical"] = {
                            "html_sha": _v_sha, "snaps": "snapshots.vertical"}
                        _cache_save(project, _CHECK_CACHE_NAME, _vdata)
            finally:
                os.replace(_idx, _vhtml)
                _restore(_idx)
        else:
            _snaps_main = os.path.join(project, "snapshots")
            if _check_cache_hit(project, os.path.join(project, "index.html"),
                                _snaps_main, args.force_check):
                print("[run] HTML 未变且快照还在，跳过 check"
                      "（--force-check 可强制重跑）", flush=True)
                _REPORT["skipped"].append("check（HTML 未变）")
            else:
                _check("check --snapshots")
                _check_cache_put(project, os.path.join(project, "index.html"),
                                 _snaps_main)
    else:
        _REPORT["skipped"].append("check --snapshots（--no-check）")

    # ── 第 5 步 c：渲染 ───────────────────────────────────────────
    # --aspect both 时 gen_hyperframes 已产出 index.html + index.vertical.html
    # 两个画幅，这里对应渲染两个成片（out.mp4 / out.vertical.mp4）并各自
    # 校验，报告步骤名区分横竖屏；--no-check/--no-verify 对两个成片一视
    # 同仁。渲染目标是 (HTML 路径, 成片路径, 步骤名后缀, 期望分辨率)。
    if args.aspect == "both":
        render_targets = [
            (os.path.join(project, "index.html"),
             os.path.join(project, "out.mp4"), "横屏", "1920x1080"),
            (os.path.join(project, "index.vertical.html"),
             os.path.join(project, "out.vertical.mp4"), "竖屏", "1080x1440"),
        ]
    elif args.aspect == "portrait":
        # 竖屏单画幅：固定 1080x1440（3:4）
        render_targets = [(os.path.join(project, "index.html"),
                           os.path.join(project, "out.mp4"), "", "1080x1440")]
    else:
        render_targets = [(os.path.join(project, "index.html"),
                           os.path.join(project, "out.mp4"), "", "1920x1080")]

    final_mp4s = []
    _rc_key_by_out = {}
    for html_path, out_mp4, label, expect_size in render_targets:
        _label_sfx = f"（{label}）" if label else ""
        # ── 成片复用（--reuse-render）：键命中且成片通过校验 → 跳过渲染。
        # 复用前先删缓存条目再校验：校验失败会 sys.exit，若不先删，坏成片
        # 会被缓存无限续命（每次重跑都命中同一个坏文件）；校验通过再写回。
        if args.reuse_render:
            _ck = _render_cache_key(html_path, manifest, args)
            _rc_key_by_out[out_mp4] = _ck
            _rc_data = _cache_load(project, _RENDER_CACHE_NAME)
            if (os.path.isfile(out_mp4)
                    and _rc_data.get(os.path.basename(out_mp4)) == _ck):
                _cache_del = _cache_load(project, _RENDER_CACHE_NAME)
                _cache_del.pop(os.path.basename(out_mp4), None)
                _cache_save(project, _RENDER_CACHE_NAME, _cache_del)
                print(f"[run] 渲染键命中，先校验已有成片 {out_mp4}……", flush=True)
                _run([sys.executable, _script("verify_render.py"),
                      "-f", out_mp4, "-m", manifest,
                      "--expect-size", expect_size,
                      "--expect-fps", str(args.fps)],
                     step_name=f"复用校验{_label_sfx}")
                _rc_data = _cache_load(project, _RENDER_CACHE_NAME)
                _rc_data[os.path.basename(out_mp4)] = _ck
                _cache_save(project, _RENDER_CACHE_NAME, _rc_data)
                print(f"[run] 成片与渲染参数均未变，复用 {out_mp4}"
                      f"（省一次渲染；强制重渲改任何参数或删掉该 mp4）",
                      flush=True)
                _REPORT["skipped"].append(f"渲染{_label_sfx}（复用缓存）")
                final_mp4s.append(out_mp4)
                continue
        render_cmd = [sys.executable, _script("render_watch.py"), "-o", out_mp4, "--",
                      "npx", "hyperframes", "render", "-o", out_mp4,
                      "--quality", args.quality, "--fps", str(args.fps),
                      "--workers", str(args.workers)]
        if os.path.basename(html_path) != "index.html":
            # 竖屏版是独立的 composition 文件；不指定的话 hyperframes 默认
            # 渲染项目根的 index.html（横屏版）
            render_cmd += ["-c", os.path.basename(html_path)]
        if args.gpu:
            render_cmd.append("--gpu")
        _run(render_cmd, cwd=project,
             step_name=f"渲染（{label}）" if label else "渲染")
        final_mp4s.append(out_mp4)

    # ── 第 5 步 d：校验成片 ───────────────────────────────────────
    if not args.no_verify:
        # 画幅/帧率按渲染目标传给 verify_render：竖屏 1080x1440、横屏
        # 1920x1080，both 时两个成片各自对应。校验通过后写复用缓存
        # （--reuse-render 复用路径的缓存已在上面提前写回，这里重复写同值无害）
        for _, out_mp4, label, expect_size in render_targets:
            _run([sys.executable, _script("verify_render.py"),
                  "-f", out_mp4, "-m", manifest,
                  "--expect-size", expect_size,
                  "--expect-fps", str(args.fps)],
                 step_name=f"校验成片（{label}）" if label else "校验成片")
            if out_mp4 in _rc_key_by_out:
                _rc_data = _cache_load(project, _RENDER_CACHE_NAME)
                _rc_data[os.path.basename(out_mp4)] = _rc_key_by_out[out_mp4]
                _cache_save(project, _RENDER_CACHE_NAME, _rc_data)
    else:
        _REPORT["skipped"].append("校验成片（--no-verify）")
        if args.reuse_render:
            print("[warn] --reuse-render 依赖校验把关，与 --no-verify 同用会"
                  "失去坏成片检测——本次渲染结果未写复用缓存", file=sys.stderr)
    print("\n[run] 完成。成片：", "、".join(final_mp4s), flush=True)
    _write_report(_report_path())
    _print_report_summary()


if __name__ == "__main__":
    main()
