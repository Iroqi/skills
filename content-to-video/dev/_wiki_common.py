#!/usr/bin/env python3
"""_wiki_common.py — 自进化闭环的共用契约与工具（dev/ 内部模块，不直接调用）。

被 wiki_maintain.py / wiki_propose.py / wiki_gate.py 共用。放这里是为了让
"什么算一个合法 pattern""什么算一个合法 proposal""回滚到底怎么回"
只有一份定义——三处各写一遍必然漂移（见 patterns/p04-doc-value-drift）。
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

DEV_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(DEV_DIR)
SCRIPTS_DIR = os.path.join(SKILL_DIR, "scripts")
WIKI_DIR = os.path.join(DEV_DIR, "wiki")
PATTERNS_DIR = os.path.join(WIKI_DIR, "patterns")
PROPOSALS_DIR = os.path.join(WIKI_DIR, "proposals")
INCUBATING_DIR = os.path.join(PROPOSALS_DIR, "incubating")
SNAPSHOTS_DIR = os.path.join(WIKI_DIR, ".snapshots")
LOGS_MD = os.path.join(WIKI_DIR, "logs.md")
IMPACT_MD = os.path.join(WIKI_DIR, "skill-impact.md")
PURPOSE_MD = os.path.join(WIKI_DIR, "PURPOSE.md")

sys.path.insert(0, SCRIPTS_DIR)

# pattern frontmatter 契约：缺字段或枚举越界即判非法（由 check.py 的
# wiki 契约检查把关，也由 wiki_maintain.py --commit 校验）
PATTERN_REQUIRED = ("id", "type", "title", "first_seen", "source",
                    "confidence", "status")
PATTERN_TYPE = ("failure", "success", "anti")
PATTERN_CONFIDENCE = ("high", "medium", "low")
PATTERN_STATUS = ("active", "merged", "deprecated")

# 必填正文小节按类型分开设：成功经验没有"现象"可言，硬要求它写"现象"只会
# 逼出一段凑字数的废话（这条契约本身就是这样被自己的门控抓出来的）。
PATTERN_SECTIONS = {
    "failure": ("## 现象", "## 机制"),
    "success": ("## 做法", "## 为什么值得固化"),
    "anti": ("## 现象",),
}

# 每轮蒸馏的增量上限（WikiSkill 论文设定）
BRIEF_MAX_FAILURES = 5
BRIEF_MAX_SUCCESSES = 3
BRIEF_ITEM_MAX_CHARS = 15000

# ── 提议者不得碰的路径：wiki 是它自己的真值来源，改了就等于篡改证据。
# changelog 由版本流程单独维护，也不该被自动改。
_PROTECTED = ("dev/wiki/", "dev/changelog.json")
PROPOSAL_REQUIRED = ("id", "created", "pattern_refs", "rationale", "target",
                     "old", "new", "expected_impact")
PROPOSAL_RISK = ("low", "medium", "high")
PROPOSAL_GATE = ("fast", "full")
PROPOSAL_STATUS = ("proposed", "applied", "rejected", "rolled_back",
                   "incubating")


# ─────────────────────────────────────────────────────────────
# frontmatter
# ─────────────────────────────────────────────────────────────

def parse_frontmatter(text):
    """解析 `---` 包裹的 YAML 子集（只支持 `key: value` 与 `[a, b]` 列表）。

    不引入 yaml 依赖——frontmatter 就七个标量字段，为它多装一个包不划算，
    而且 skill 的依赖清单是刻意保持极简的（见 SKILL.md 环境准备）。
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    head = text[3:end]
    body = text[end + 4:]
    meta = {}
    for line in head.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            v = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
        else:
            v = v.strip("\"'")
        meta[k] = v
    return meta, body


def validate_pattern(path):
    """校验单个 pattern 文件。返回 (ok, errors, meta)。"""
    errors = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return False, ["读取失败：%s" % e], {}
    meta, body = parse_frontmatter(text)
    if not meta:
        return False, ["缺少 frontmatter（文件须以 --- 开头）"], {}
    for k in PATTERN_REQUIRED:
        if not meta.get(k):
            errors.append("缺字段 %s" % k)
    if "type" in meta and meta["type"] not in PATTERN_TYPE:
        errors.append("type=%r 非法（应为 %s）"
                      % (meta["type"], "/".join(PATTERN_TYPE)))
    if "confidence" in meta and meta["confidence"] not in PATTERN_CONFIDENCE:
        errors.append("confidence=%r 非法" % meta["confidence"])
    if "status" in meta and meta["status"] not in PATTERN_STATUS:
        errors.append("status=%r 非法" % meta["status"])
    # id 必须与文件名主干一致：靠人记住对应关系迟早错
    stem = os.path.splitext(os.path.basename(path))[0]
    if meta.get("id") and meta["id"] != stem:
        errors.append("id=%r 与文件名 %r 不一致" % (meta["id"], stem))
    if len(body.strip()) < 80:
        errors.append("正文过短（<80 字符），不足以支撑一条经验")
    for sec in PATTERN_SECTIONS.get(meta.get("type"), ()):
        if sec not in body:
            errors.append("正文缺 %s 小节（type=%s 的必填小节）"
                          % (sec, meta.get("type")))
    return (not errors), errors, meta


def iter_patterns(status=None, ptype=None):
    """遍历 patterns/，yield (path, meta, body)。文件级错误不抛出。"""
    if not os.path.isdir(PATTERNS_DIR):
        return
    for name in sorted(os.listdir(PATTERNS_DIR)):
        if not name.endswith(".md") or name.startswith("."):
            continue
        p = os.path.join(PATTERNS_DIR, name)
        meta, body = _read_meta_body(p)
        if status and meta.get("status") != status:
            continue
        if ptype and meta.get("type") != ptype:
            continue
        yield p, meta, body


def _read_meta_body(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return parse_frontmatter(f.read())
    except OSError:
        return {}, ""


# ─────────────────────────────────────────────────────────────
# proposal
# ─────────────────────────────────────────────────────────────

def new_proposal_id(pattern_ref=None):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = re.sub(r"[^a-z0-9]+", "-", (pattern_ref or "adhoc").lower()).strip("-")
    return "%s-%s" % (ts, suffix[:40])


def _norm_rel(rel):
    """归一化成相对技能目录的 POSIX 相对路径（只剥前导 ./）。

    **别写成 lstrip("./")**：lstrip 吃的是字符集合，会把 `.gitignore` 削成
    `gitignore`。后果不是报错而是静默失效——快照时 src 找不到就 continue，
    manifest 记 0 个文件，回滚时"还原 0 个文件"还报成功，安全网是空的。
    """
    s = str(rel).replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s


def _is_unsafe_rel(rel):
    """挡住绝对路径与向上穿越（../）。

    提议制下 target 来自提案文件，不校验的话一份提案（哪怕是抄错的）就能把
    技能目录**之外**的文件改掉。宁可拒绝，不可猜。
    """
    s = _norm_rel(rel)
    if s.startswith("/"):
        return True
    if len(s) > 1 and s[1] == ":":   # Windows 盘符，如 C:/...
        return True
    return any(part == ".." for part in s.split("/"))


def validate_proposal(prop, skill_dir=None):
    """校验一份提案。返回 (ok, errors)。

    核心约束（WikiSkill 的"每轮恰好一处原子改动"）：
      1. 只改一个文件
      2. old 在目标文件中**恰好出现一次**（0 次无从下手，2 次说明不够原子）
      3. 不得改 dev/wiki/ 与 changelog
      4. 目标必须在技能目录内（不含绝对路径与 ../ 穿越）
    """
    skill_dir = skill_dir or SKILL_DIR
    errors = []
    if not isinstance(prop, dict):
        return False, ["提案必须是 object"]
    for k in PROPOSAL_REQUIRED:
        if not prop.get(k):
            errors.append("缺字段 %s" % k)
    if prop.get("risk") and prop["risk"] not in PROPOSAL_RISK:
        errors.append("risk=%r 非法" % prop["risk"])
    if prop.get("gate") and prop["gate"] not in PROPOSAL_GATE:
        errors.append("gate=%r 非法" % prop["gate"])
    if prop.get("status") and prop["status"] not in PROPOSAL_STATUS:
        errors.append("status=%r 非法" % prop["status"])

    refs = prop.get("pattern_refs") or []
    if not isinstance(refs, list) or not refs:
        errors.append("pattern_refs 不得为空：改动必须能追到某条经验")
    else:
        known = set()
        for _p, meta, _b in iter_patterns():
            if meta.get("id"):
                known.add(meta["id"])
        for r in refs:
            if r not in known:
                errors.append("pattern_refs 中的 %r 在 patterns/ 下不存在" % r)

    if prop.get("old") and prop["old"] == prop.get("new"):
        errors.append("old 与 new 相同：这不是一次改动")

    target = prop.get("target")
    if target:
        rel = _norm_rel(target)
        if any(rel.startswith(x) for x in _PROTECTED):
            errors.append("目标 %r 位于保护路径（%s）——提议者不得改写自己的"
                          "真值来源" % (target, "/".join(_PROTECTED)))
        if os.path.isabs(target) or _is_unsafe_rel(target):
            errors.append("target 必须是技能目录内的相对路径，不得是绝对路径"
                          "或含 ../ 穿越：%r" % target)
        else:
            full = os.path.join(skill_dir, rel)
            if not os.path.isfile(full):
                errors.append("目标文件不存在：%s" % full)
            elif prop.get("old"):
                try:
                    with open(full, "r", encoding="utf-8") as f:
                        cur = f.read()
                except OSError as e:
                    errors.append("目标文件读取失败：%s" % e)
                    cur = None
                if cur is not None:
                    n = cur.count(prop["old"])
                    if n == 0:
                        errors.append("old 片段在目标文件中不存在（文件已变？"
                                      "重新生成提案）")
                    elif n > 1:
                        errors.append("old 片段出现 %d 次——必须唯一才能算原子"
                                      "改动，请扩大上下文或先拆分" % n)
    return (not errors), errors


def apply_proposal(prop, skill_dir=None):
    """落地一处原子替换。成功返回 True。替换前由调用方负责快照。"""
    ok, errors = validate_proposal(prop, skill_dir)
    if not ok:
        raise ValueError("；".join(errors))
    skill_dir = skill_dir or SKILL_DIR
    rel = _norm_rel(prop["target"])
    full = os.path.join(skill_dir, rel)
    with open(full, "r", encoding="utf-8") as f:
        cur = f.read()
    new_text = cur.replace(prop["old"], prop["new"], 1)
    tmp = full + ".wiki-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_text)
    os.replace(tmp, full)
    return True


# ─────────────────────────────────────────────────────────────
# 快照 / 回滚
# ─────────────────────────────────────────────────────────────
#
# 刻意**不用 git checkout 回滚**：技能目录里可能有维护者未提交的在制品，
# `git checkout --` 会把它们一起抹掉，代价远大于收益。这里一律用"先复制
# 被改文件、回滚时复制回去"的文件级快照——无论有没有 git 都能工作，且
# 影响面严格等于这次改动碰到的那些文件。git 只用来记录当时的 HEAD，
# 便于事后对照，不参与回滚动作。

def snapshot(rel_paths, snap_id, skill_dir=None):
    """把将被修改的文件复制到 .snapshots/<snap_id>/，返回快照目录。"""
    skill_dir = skill_dir or SKILL_DIR
    dest = os.path.join(SNAPSHOTS_DIR, snap_id)
    os.makedirs(dest, exist_ok=True)
    manifest = {"id": snap_id,
                "created": datetime.datetime.now().astimezone().isoformat(
                    timespec="seconds"),
                "git_head": git_head(skill_dir),
                "files": []}
    for rel in rel_paths:
        rel = _norm_rel(rel)
        if _is_unsafe_rel(rel):
            continue
        src = os.path.join(skill_dir, rel)
        if not os.path.isfile(src):
            # 静默跳过曾经让 dotfile 的快照变成空 manifest，回滚时报"还原 0 个
            # 文件"还装作成功。空 manifest 必须被当成硬错误抛出来。
            raise FileNotFoundError(
                "快照源不存在：%s（路径归一化后为 %r）——宁可中止也别留一份"
                "还原不了的空快照" % (src, rel))
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        manifest["files"].append(rel)
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return dest


def restore(snap_id, skill_dir=None):
    """按快照还原文件。返回还原的文件数；快照不存在返回 None。"""
    skill_dir = skill_dir or SKILL_DIR
    dest = os.path.join(SNAPSHOTS_DIR, snap_id)
    mpath = os.path.join(dest, "manifest.json")
    if not os.path.isfile(mpath):
        return None
    with open(mpath, "r", encoding="utf-8") as f:
        man = json.load(f)
    n = 0
    for rel in man.get("files", []):
        src = os.path.join(dest, rel)
        dst = os.path.join(skill_dir, rel)
        if not os.path.isfile(src):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    return n


def git_head(skill_dir=None):
    skill_dir = skill_dir or SKILL_DIR
    try:
        out = subprocess.run(["git", "-C", skill_dir, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


def in_git_repo(skill_dir=None):
    return git_head(skill_dir) is not None


# ─────────────────────────────────────────────────────────────
# 门控
# ─────────────────────────────────────────────────────────────

# dev/check.py 是**超集门控**：它内部已经跑了 selftest 与 run_eval，再单独
# 跑一遍等于把他俩各跑两次（实测 selftest 是全门控里最慢的一环）。所以默认
# 只跑 check.py，再从它的 __SUMMARY_JSON__ 里把三组拆出来。
GATE_ENTRY = "dev/check.py"
SUMMARY_PREFIX = "__SUMMARY_JSON__"
# 解析不到机器摘要时（旧版本/被改动过的 check.py）才退回逐个跑
GATE_FALLBACK = (
    ("selftest", "scripts/selftest.py"),
    ("run_eval", "dev/run_eval.py"),
    ("check", "dev/check.py"),
)
GROUP_ORDER = ("selftest", "run_eval", "check")


def _parse_summary(text):
    """取输出里最后一行 __SUMMARY_JSON__（三个脚本都约定了这条机器可读摘要）。"""
    for line in reversed(text.splitlines()):
        s = line.strip()
        if s.startswith(SUMMARY_PREFIX):
            try:
                return json.loads(s[len(SUMMARY_PREFIX):].strip())
            except ValueError:
                return None
    return None


def parse_summary_totals(text):
    """从一段脚本输出里取它自报的 (passed, total)。

    子脚本（selftest/run_eval）自己会打 __SUMMARY_JSON__，里面是真实的
    passed/total（如 443/443）。上层 check.py 若只记录"这个子进程过了"，
    摘要就会退化成 "selftest 1/1"——读者会误以为全技能只跑了一项检查。
    取不到返回 (None, None)：宁可显示得粗略，也不许编数字。
    """
    s = _parse_summary(text)
    if not s:
        return None, None
    p, t = s.get("passed"), s.get("total")
    if not isinstance(p, int) or not isinstance(t, int):
        return None, None
    return p, t


def _count_group(name, items):
    """统计一个门控分组的 (passed, total)。

    selftest/run_eval 是**单个子进程条目**，但条目里带着子脚本自报的
    真实明细（443/443）——这时必须用明细，否则摘要会退化成 "selftest 1/1"，
    看着像全技能只跑了一项检查，是伪装成信息的误导。取不到明细就退回
    "数条目" 的粗口径。
    """
    passed = total = None
    if name in ("selftest", "run_eval") and len(items) == 1:
        passed, total = items[0].get("passed"), items[0].get("total")
    if not isinstance(passed, int) or not isinstance(total, int):
        total = len(items)
        passed = sum(1 for i in items if i.get("ok"))
    return passed, total


def _group_results(results):
    """把 check.py 的 results 归成 selftest / run_eval / check 三组。"""
    groups = {k: [] for k in GROUP_ORDER}
    for r in results or []:
        n = r.get("name")
        if n in ("selftest", "run_eval"):
            groups[n].append(r)
        else:
            groups["check"].append(r)
    return groups


def _exec(script, skill_dir, timeout):
    try:
        p = subprocess.run([sys.executable, script], cwd=skill_dir,
                           capture_output=True, text=True, timeout=timeout,
                           errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return "timeout", ""
    except OSError as e:
        return "oserror: %s" % e, ""


def run_gate(level="fast", skill_dir=None, timeout=1800):
    """跑 fast 门控。返回 {"level","mode","steps","failures","passed"}。

    fast 全档均为**确定性、零 API** 检查，每次提议都能跑。
    """
    skill_dir = skill_dir or SKILL_DIR
    out = {"level": level, "mode": "single", "steps": [], "failures": [],
           "passed": True}
    rc, text = _exec(os.path.join(skill_dir,
                                  GATE_ENTRY.replace("/", os.sep)),
                     skill_dir, timeout)
    summary = _parse_summary(text)
    if summary and summary.get("results"):
        groups = _group_results(summary.get("results"))
        for name in GROUP_ORDER:
            items = groups[name]
            passed, total = _count_group(name, items)
            bad = [i.get("name") for i in items if not i.get("ok")]
            ok = (rc == 0) and (total == 0 or passed == total)
            out["steps"].append({"name": name, "ok": ok, "passed": passed,
                                 "total": total or None, "rc": rc,
                                 "skipped": summary.get("skipped")})
            out["failures"].extend(bad)
            if not ok:
                out["passed"] = False
        if rc != 0 and not out["failures"]:
            # 退出码非零但摘要里没有红项：脚本自身崩了，必须当成红
            out["passed"] = False
            out["failures"].append("%s 退出码 %s（无明细）" % (GATE_ENTRY, rc))
        return out

    # ── 回退：逐个跑，靠文本模式抓数 ────────────────────────────
    out["mode"] = "fallback"
    for name, rel in GATE_FALLBACK:
        src = os.path.join(skill_dir, rel.replace("/", os.sep))
        rc2, text2 = _exec(src, skill_dir, timeout)
        s = _parse_summary(text2)
        passed = total = None
        if s:
            passed, total = s.get("passed"), s.get("total")
        if passed is None or total is None:
            m = re.findall(r"(\d+)\s*/\s*(\d+)", text2)
            if m:
                try:
                    passed, total = int(m[-1][0]), int(m[-1][1])
                except (TypeError, ValueError):
                    passed = total = None
        ok = (rc2 == 0)
        if ok and passed is not None and total is not None:
            ok = (passed == total)
        out["steps"].append({"name": name, "ok": ok, "passed": passed,
                             "total": total, "rc": rc2, "skipped": None})
        if not ok:
            out["passed"] = False
            out["failures"].append(name)
    return out


def gate_summary(gate):
    parts = []
    for s in gate["steps"]:
        if s.get("passed") is not None and s.get("total") is not None:
            parts.append("%s %s/%s" % (s["name"], s["passed"], s["total"]))
        else:
            parts.append("%s %s" % (s["name"],
                                    "OK" if s.get("ok") else "rc=%s" % s["rc"]))
    text = " · ".join(parts)
    if gate.get("failures"):
        text += " ｜ 红项：" + "、".join(str(f) for f in gate["failures"][:6])
    if gate.get("mode") == "fallback":
        text += " ｜(回退模式)"
    return text


# ─────────────────────────────────────────────────────────────
# 蒸馏进度标记（"已蒸馏到此"）
# ─────────────────────────────────────────────────────────────
# 两个 CLI 都要按"上次蒸馏到哪"来过滤轨迹，时间比较的规则必须只有一份。
# 之前两边各写一遍，于是同一个时区 bug 一个崩、一个静默失效。

def marker_path():
    """蒸馏进度标记的位置。"""
    return os.path.join(WIKI_DIR, ".last_maintained")


def marker_cutoff(marker=None):
    """取标记的 mtime 作为分界时间，返回 **aware** datetime；无标记返回 None。

    坑：`datetime.fromtimestamp()` 给的是**朴素**本地时间，而轨迹的 `ts`
    是 `astimezone().isoformat()` 生成的**带时区**时间。两者一比就抛
    `TypeError: can't compare offset-naive and offset-aware datetimes`。
    所以这里显式按 UTC 取出 aware 时间，交给 `parse_trace_ts()` 的另一半保证。
    """
    path = marker or marker_path()
    try:
        dt = datetime.datetime.fromtimestamp(
            os.path.getmtime(path), datetime.timezone.utc)
    except OSError:
        return None
    # 向下取到整秒：轨迹的 ts 是 `isoformat(timespec="seconds")`，精度只到秒。
    # 拿带亚秒的 mtime 去比，会把"跟标记同一秒落盘"的轨迹判成旧的而丢掉。
    # 不确定归属时**保留**而不是丢弃——重复蒸馏一条已经蒸馏过的轨迹是幂等的，
    # 漏掉一条没蒸馏过的则是永久丢失。
    return dt.replace(microsecond=0)


def parse_trace_ts(value):
    """解析轨迹的 `ts` 字段，返回 aware datetime；解析不出来返回 None。

    老轨迹或手工导入的 `ts` 可能是朴素时间（没带时区后缀），这里按本地时区
    补齐，保证跟 `marker_cutoff()` 的结果可比——否则一比就 TypeError。
    """
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


# ─────────────────────────────────────────────────────────────
# 落盘小工具
# ─────────────────────────────────────────────────────────────

def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def append_log(text, skill_dir=None):
    """向 logs.md 追加一段（只增不改）。"""
    skill_dir = skill_dir or SKILL_DIR
    path = LOGS_MD
    if not os.path.isfile(path):
        return None
    stamp = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    block = "\n<!-- %s -->\n%s\n" % (stamp, text.rstrip())
    with open(path, "a", encoding="utf-8") as f:
        f.write(block)
    return path
