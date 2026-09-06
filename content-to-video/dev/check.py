#!/usr/bin/env python3
"""一键维护 gate：串起本技能所有可离线跑的检查，产出单一 JSON 摘要 + 单一退出码。

agent 改完代码后跑一条命令即可验证：
  python dev/check.py

做的事：
  1. scripts/selftest.py（确定性冒烟测试）
  2. dev/run_eval.py（离线集成测试）
  3. dev/evals.json / dev/changelog.json 是否合法 JSON
  4. SKILL.md frontmatter 是否有 name + description
  5. SKILL.md"不适用场景"范围声明关键词是否还在（non_chinese_input_rejected
     这条 forward-test case 的半自动化机械前提，见下方 _check_scope_declaration）
  6. 声明-实际交叉校验（防文档漂移，见下方 _check_doc_drift）：SKILL.md/注释里
     声明的具体值（--gap 默认、tagline 兜底文案、模板图片尺寸、踩坑/评估条数）
     必须与代码/配置的实际值一致
  7. 打包完整性（见下方 _check_version_consistency / _check_toc_anchors）：
     frontmatter version == changelog current_version、SKILL.md 目录锚点必须能对上
     正文标题

任何一步失败都以非零码退出，并在输出里给出是哪一步、以及失败详情。末尾打一行
`__SUMMARY_JSON__ {...}` 供 agent 程序化解析（和 selftest/run_eval 同款约定）。
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

# 本脚本要打印子进程捕获的输出（含 U+FFFD 替换符/emoji 等任意字符），
# Windows 控制台按 GBK 写 stdout 时会 UnicodeEncodeError 裸栈——与
# scripts/_script_utils.setup_stdio 同款防线（gate 自身不能先倒下）
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from _script_utils import setup_stdio  # noqa: E402

# 子脚本（selftest/run_eval）自己打 __SUMMARY_JSON__ 机器可读摘要，里面是
# 真实的 passed/total——上层门禁若只记"这个子进程过了"，摘要会退化成
# "selftest 1/1"，看着像全技能只跑了一项检查，是伪装成信息的误导。
SUMMARY_PREFIX = "__SUMMARY_JSON__"


def parse_summary_totals(text):
    """从一段脚本输出里取它自报的 (passed, total)；取不到返回 (None, None)。"""
    for line in reversed(text.splitlines()):
        s = line.strip()
        if s.startswith(SUMMARY_PREFIX):
            try:
                data = json.loads(s[len(SUMMARY_PREFIX):].strip())
            except ValueError:
                return None, None
            p, t = data.get("passed"), data.get("total")
            if isinstance(p, int) and isinstance(t, int):
                return p, t
            return None, None
    return None, None


def _run_py(rel):
    return subprocess.run([PY, rel], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _check_json(rel):
    try:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            json.load(f)
        return True, ""
    except Exception as e:
        return False, str(e)


def _check_frontmatter():
    try:
        with open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return False, str(e)
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return False, "SKILL.md 缺少 YAML frontmatter"
    fm = m.group(1)
    for key in ("name:", "description:"):
        if key not in fm:
            return False, f"SKILL.md frontmatter 缺少 {key}"
    return True, ""


# `non_chinese_input_rejected` 这条 case（见 dev/evals.json）本质是"agent 读到
# 越界请求要正确拒绝"的语义判断，没有脚本能真的替 agent 做这个判断（见
# dev/run_eval.py 头部说明）。但 agent 之所以能做对这个判断，机制上依赖的是
# SKILL.md"不适用场景"里明确写了纯英文稿件不适用——这段声明本身是可以离线
# 机械检查的：它没有被误删/改弱，是这条 case 大概率能通过的必要（非充分）条件。
# 这不是给这条 case"接了断言逻辑"（那仍然需要真的跑一个 agent 实例去 forward-test），
# 只是把"这条 case 依赖的声明文本还在不在"这一半机械
# 部分先行接入离线 gate，减少"改 SKILL.md 时手滑删掉这段导致 agent 从此不再正确
# 拒绝，但没人发现"这种退化——发现得越早，成本越低。
_SCOPE_DECLARATION_KEYWORDS = ("纯英文", "此技能不适用")


def _check_scope_declaration():
    skill_path = os.path.join(ROOT, "SKILL.md")
    try:
        with open(skill_path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return False, str(e)
    missing = [kw for kw in _SCOPE_DECLARATION_KEYWORDS if kw not in text]
    if missing:
        return False, (
            f"SKILL.md '不适用场景' 一节似乎不再包含纯英文稿件的范围声明"
            f"（缺关键词：{missing}）——这是 non_chinese_input_rejected 这条 "
            f"forward-test case 能通过的前提之一，改动前请确认是有意为之，"
            f"不是手滑删掉了"
        )
    return True, ""


# ── 声明-实际交叉校验（防文档漂移）──────────────────────────────────
# 历史上反复出现"文档/注释里写死的具体值与代码/配置实际值漂移"：
# --gap 默认值（budget 0.3 vs pipeline 0.4）、tagline 兜底文案
# （"补充阅读" vs "AI 资讯"）、模板图片尺寸（460 vs 680）、踩坑/评估
# 条数。这类漂移没有任何机制兜底，等真实用户按文档操作时才暴露。
# 这里把可以机械比对的部分接进离线 gate，原则是：
#   - 文档声明了具体值 → 必须与实际一致，否则 FAIL；
#   - 文档改成"引用权威来源、不写死数值"（推荐的防漂移写法，见
#     run_eval.py 头部同款教训）→ 放行。
# 这样既抓住漂移，又不强迫文档必须保留某个数字。

def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _check_gap_default():
    """_contracts.DEFAULT_GAP 必须等于 pipeline.py --gap 的默认值
    （不一致会让 estimate 相对实测系统性偏移 (n-1)×Δ）。
    两处都是稳定字面量，解析不出来按失败处理，提醒同步维护本检查。"""
    m1 = re.search(r'add_argument\("--gap",\s*type=float,\s*default=([0-9.]+)',
                   _read("scripts/pipeline.py"))
    m2 = re.search(r"DEFAULT_GAP\s*=\s*([0-9.]+)", _read("scripts/_contracts.py"))
    if not m1 or not m2:
        return False, ("无法从 pipeline.py / _contracts.py 解析出 --gap 默认值"
                       "（代码结构变了？请同步更新本检查）")
    if abs(float(m1.group(1)) - float(m2.group(1))) > 1e-9:
        return False, (f"_contracts.DEFAULT_GAP={m2.group(1)} 与 pipeline --gap "
                       f"默认值={m1.group(1)} 不一致（两处必须一起改，"
                       "否则 estimate 系统性偏差）")
    return True, ""


def _declared_count_check(text, pattern, actual, what):
    """text 里声明了"N 条"（pattern 第 1 组捕获数字）时，必须等于 actual；
    没声明（文档改成不含数字的写法）则放行。"""
    m = re.search(pattern, text)
    if not m:
        return True, ""
    declared = int(m.group(1))
    if declared != actual:
        return False, f"{what}：文档声明 {declared} 条，实际 {actual} 条"
    return True, ""


def _measure_python_floor():
    """测出 scripts/ 下全部脚本真正需要的最低 Python 版本。

    用 ast.parse(feature_version=N) 从 3.8 起逐级探测：feature_version 只
    拒绝"比该版本新"的语法，所以能全部通过的最小版本就是实际下限。
    拿不到（例如代码用了探测范围之外的语法）返回 None。
    """
    import ast
    import glob as _glob
    versions = [(3, 8), (3, 9), (3, 10), (3, 11), (3, 12), (3, 13)]
    sources = []
    for path in sorted(_glob.glob(os.path.join(ROOT, "scripts", "*.py"))):
        try:
            with open(path, encoding="utf-8") as f:
                sources.append((path, f.read()))
        except OSError:
            return None
    for mv in versions:
        try:
            for _, src in sources:
                ast.parse(src, feature_version=mv)
        except SyntaxError:
            continue
        return mv
    return None


# 声明的 Python 下限允许比实测下限高 1 个小版本（保守声明是安全的：留一点
# 余量防 stdlib API 意外）；高得更多就成了虚构的硬门槛——本次 review 就撞到
# 声明 3.12、实测 3.8 语法即可跑（Python 3.11 全绿），把 3.11 的用户整条
# 挡在门外。这类"文档凭空抬高环境要求"没有任何别的机制能发现。
_PY_CLAIM_TOLERANCE_MINORS = 1


def _check_python_version_claim():
    """SKILL.md 声明的 Python 最低版本不得远高于代码实际所需版本。"""
    import re as _re
    skill = _read("SKILL.md")
    m = _re.search(r"Python\s+(\d+)\.(\d+)\+", skill)
    if not m:
        return True, ""  # 文档不写死版本（推荐写法）→ 放行
    claimed = (int(m.group(1)), int(m.group(2)))
    actual = _measure_python_floor()
    if actual is None:
        return False, ("测不出 scripts/ 的最低 Python 版本（用了探测范围之外的"
                       "语法？）——无法校验 SKILL.md 的版本声明，请同步更新本检查")
    allowed = (actual[0], actual[1] + _PY_CLAIM_TOLERANCE_MINORS)
    if claimed > allowed:
        return False, (
            f"SKILL.md 声明 Python {claimed[0]}.{claimed[1]}+，但 scripts/ 实际只需 "
            f"{actual[0]}.{actual[1]}（最高允许声明 {allowed[0]}.{allowed[1]}）。"
            f"凭空抬高版本门槛会把低版本用户整条挡在门外——请改回实测值，"
            f"或说明确有必要的新语法依赖")
    return True, ""


def _check_workers_default():
    """渲染抓帧 workers 的文档声明必须与 run.py 的默认值一致。

    注意作用域：本技能里有三个同名的 --workers，默认值各不相同——
      · run.py / hyperframes render：渲染抓帧 worker（默认 6）
      · search_images.py：并行搜图/下载线程（默认 4）
      · pipeline.py：并行 TTS 调用数（默认 4）
    所以这条检查只认"渲染"语境下的声明，另外两个工具的 --workers 4 是
    合法默认值，不能拿 run.py 的 6 去比对它们。
    """
    import re as _re
    m = _re.search(r'add_argument\("--workers",\s*type=int,\s*default=(\d+)',
                   _read("scripts/run.py"))
    if not m:
        return False, "无法从 run.py 解析出 --workers 默认值（代码结构变了？请同步更新本检查）"
    actual = int(m.group(1))
    # 出现"这些词"说明这条讲的是别的工具/或是在给调优建议，不是默认值声明
    _OTHER_TOOL = ("搜图", "下载线程", "TTS", "配音", "并行 TTS")
    _TUNING_ADVICE = ("降回", "降到", "建议降", "低配", "崩溃", "堆上限")
    for rel in ("SKILL.md", "references/rendering.md"):
        for lineno, line in enumerate(_read(rel).splitlines(), 1):
            if "--workers" not in line:
                continue
            if any(k in line for k in _OTHER_TOOL + _TUNING_ADVICE):
                continue
            # 两种写法都算声明："--workers（默认 6）" / 命令里的 "--workers 6"
            for dm in _re.finditer(r"--workers[）)]?\s*[（(]?\s*(?:默认\s*)?(\d+)",
                                   line):
                if int(dm.group(1)) != actual:
                    return False, (
                        f"{rel}:{lineno} 声明 --workers {dm.group(1)}，run.py 渲染"
                        f"抓帧 worker 实际默认 {actual}。调默认值时必须连同文档示例"
                        f"命令一起改，否则 agent 照抄示例就退回旧值")
    return True, ""


def _check_doc_drift():
    """返回 [(name, passed, err)] 列表，由 main 逐项计入结果。"""
    checks = []
    skill = _read("SKILL.md")

    r, err = _check_gap_default()
    checks.append(("doc_drift:gap_default", r, err))

    r, err = _check_python_version_claim()
    checks.append(("doc_drift:python_version_claim", r, err))

    r, err = _check_workers_default()
    checks.append(("doc_drift:workers_default", r, err))

    # tagline 兜底文案：pipeline.py 里写死的兜底字面量必须同步出现在 SKILL.md
    m = re.search(r'_tagline\s*=\s*"([^"]+)"', _read("scripts/pipeline.py"))
    if m:
        lit = m.group(1)
        passed = lit in skill
        checks.append(("doc_drift:tagline_fallback", passed,
                       "" if passed else
                       f"pipeline 兜底 tagline 为“{lit}”，但 SKILL.md 未提及该文案（两处必须同步）"))
    else:
        checks.append(("doc_drift:tagline_fallback", True,
                       "跳过：pipeline.py 未匹配到兜底字面量（变量名变了？）"))

    # 模板横屏图片尺寸：SKILL.md 与 references/rendering.md 各自声明的 W×H
    # 都必须与 template.json 一致（逐文件判，避免一份正确就放行另一份漂移）
    try:
        tpl = json.loads(_read("config/template.json"))
        img = tpl["layout"]["landscape"]["image"]
        declared = f'{img["width"]}×{img["height"]}'
        _docs = (("SKILL.md", skill),
                 ("references/rendering.md", _read("references/rendering.md")))
        _missing = [name for name, doc in _docs if declared not in doc]
        passed = not _missing
        checks.append(("doc_drift:image_size", passed,
                       "" if passed else
                       f"template.json 横屏图片为 {declared}，但 {', '.join(_missing)} "
                       f"未按此声明（改模板或改文档，两处必须同步）"))
    except Exception as e:
        checks.append(("doc_drift:image_size", False, f"无法解析 template.json：{e}"))

    # 踩坑条数：SKILL.md / pitfalls.md 标题里声明的条数 == 实际 ### N. 小节数
    pitfalls = _read("references/pitfalls.md")
    actual = len(re.findall(r"^### \d+\.", pitfalls, re.M))
    for src_text, pat, what in (
            (skill, r"(\d+) 条踩坑记录", "SKILL.md 踩坑条数"),
            (pitfalls, r"（(\d+) 条踩坑记录）", "pitfalls.md 标题条数")):
        r, err = _declared_count_check(src_text, pat, actual, what)
        checks.append((f"doc_drift:pitfalls_count:{what}", r, err))

    # CLI 选项漂移：SKILL.md 里出现的 `--aspect <val>` 必须都是 run.py argparse
    # 实际接受的 choices。这是之前 `--aspect vertical` 存活一整个版本的根因——
    # 没任何门禁交叉校验"文档声明的 CLI 选项 <-> 代码 argparse choices"。
    try:
        run_src = _read("scripts/run.py")
        m = re.search(r'add_argument\("--aspect"[^;]*?choices=\[([^\]]+)\]',
                       run_src, re.S)
        if m:
            choices = [c.strip().strip('"\'') for c in m.group(1).split(",")
                       if c.strip()]
            doc_aspects = set(re.findall(r"--aspect\s+([A-Za-z]+)", skill))
            bad = sorted(doc_aspects - set(choices))
            passed = not bad
            checks.append(("doc_drift:cli_aspect_choices", passed,
                           "" if passed else
                           f"SKILL.md 声明了 --aspect {bad}，但 argparse choices 只有 {choices}"
                           "（文档与 CLI 不同步，agent 照抄会 invalid choice）"))
        else:
            checks.append(("doc_drift:cli_aspect_choices", True,
                           "跳过：scripts/run.py 未匹配到 --aspect choices 定义"))
    except Exception as e:
        checks.append(("doc_drift:cli_aspect_choices", False, f"无法校验 CLI 选项：{e}"))

    # 评估用例条数：SKILL.md 项目结构里声明的条数 == evals.json 实际条数
    try:
        n_cases = len(json.loads(_read("dev/evals.json")).get("test_cases", []))
        r, err = _declared_count_check(skill, r"(\d+) 条 agent 行为评估",
                                       n_cases, "SKILL.md 评估用例条数")
        checks.append(("doc_drift:evals_count", r, err))
    except Exception as e:
        checks.append(("doc_drift:evals_count", False, f"无法解析 dev/evals.json：{e}"))

    return checks


def _check_version_consistency():
    """SKILL.md frontmatter version 必须等于 dev/changelog.json 的
    current_version（两处不同步没有任何机制兜底，只能靠这条门禁拦）。"""
    skill = _read("SKILL.md")
    m = re.match(r"^---\n(.*?)\n---\n", skill, re.S)
    if not m:
        return False, "SKILL.md 缺少 YAML frontmatter"
    vm = re.search(r'^version:\s*"?([0-9][0-9A-Za-z.\-]*)"?\s*$', m.group(1), re.M)
    if not vm:
        return False, "frontmatter 里解析不出 version 字段"
    try:
        cur = json.loads(_read("dev/changelog.json")).get("current_version")
    except Exception as e:
        return False, f"无法读取 dev/changelog.json：{e}"
    if vm.group(1) != cur:
        return False, (f"frontmatter version={vm.group(1)} 与 changelog "
                       f"current_version={cur} 不一致（发版时两处必须一起改）")
    return True, ""


def _heading_anchor(heading):
    """GitHub 风格 slug：小写、去标点（含中文标点）、空白转 '-'，保留字母
    数字与 CJK。与 SKILL.md 现有目录锚点写法（如 #核心工作流5-步）一致。"""
    out = []
    for ch in heading.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch.isspace():
            out.append("-")
        # 其余（（）／、：等标点）丢弃
    return "".join(out)


def _check_toc_anchors():
    """SKILL.md 目录/正文里的 `](#锚点)` 内链必须对得上某个标题。
    只查"链接有目标"，不强制"每个标题都被目录收录"（后者不是错误）。"""
    skill = _read("SKILL.md")
    anchors = {_heading_anchor(h) for h in re.findall(r"^#{1,6}\s+(.+?)\s*$",
                                                      skill, re.M)}
    broken = []
    for text, anchor in re.findall(r"\[([^\]]+)\]\(#([^)]+)\)", skill):
        if anchor not in anchors:
            broken.append(f"[{text}](#{anchor})")
    if broken:
        return False, ("SKILL.md 内链锚点对不上任何标题："
                       + "；".join(broken)
                       + "（改标题时目录同步改）")
    return True, ""


def main():
    setup_stdio()
    results = []
    ok = True

    for name, rel in (("selftest", "scripts/selftest.py"),
                      ("run_eval", "dev/run_eval.py")):
        r = _run_py(rel)
        passed = r.returncode == 0
        ok = ok and passed
        entry = {"name": name, "ok": passed, "exit_code": r.returncode}
        # 把子脚本自报的明细数（如 443/443）带上。不加这一层，上游只能说
        # "selftest 通过了"，门控摘要会显示成 "selftest 1/1"——看着像全局
        # 只跑了 1 项检查，是伪装成信息的误导。
        p2, t2 = parse_summary_totals((r.stdout or "") + (r.stderr or ""))
        if p2 is not None and t2 is not None:
            entry["passed"], entry["total"] = p2, t2
        results.append(entry)
        detail = " · %s/%s" % (p2, t2) if p2 is not None else ""
        print("[%s] %s (exit %s%s)"
              % ("OK" if passed else "FAIL", name, r.returncode, detail))
        if not passed:
            tail = (r.stderr or r.stdout).strip().splitlines()[-15:]
            for line in tail:
                print("      " + line)

    for rel in ("dev/evals.json", "dev/changelog.json"):
        passed, err = _check_json(rel)
        ok = ok and passed
        results.append({"name": f"json:{rel}", "ok": passed})
        print(f"[{'OK' if passed else 'FAIL'}] {rel} 合法 JSON"
              + ("" if passed else f"：{err}"))

    passed, err = _check_frontmatter()
    ok = ok and passed
    results.append({"name": "frontmatter", "ok": passed})
    print(f"[{'OK' if passed else 'FAIL'}] SKILL.md frontmatter"
          + ("" if passed else f"：{err}"))

    passed, err = _check_scope_declaration()
    ok = ok and passed
    results.append({"name": "scope_declaration:non_chinese_input_rejected", "ok": passed})
    print(f"[{'OK' if passed else 'FAIL'}] SKILL.md 范围声明（non_chinese_input_rejected 半自动化）"
          + ("" if passed else f"：{err}"))

    for name, func in (
            ("packaging:version_consistency", _check_version_consistency),
            ("packaging:toc_anchors", _check_toc_anchors)):
        passed, err = func()
        ok = ok and passed
        results.append({"name": name, "ok": passed})
        print(f"[{'OK' if passed else 'FAIL'}] {name}"
              + ("" if passed else f"：{err}"))

    for name, passed, err in _check_doc_drift():
        ok = ok and passed
        results.append({"name": name, "ok": passed})
        print(f"[{'OK' if passed else 'FAIL'}] {name}"
              + ("" if passed else f"：{err}"))

    summary = {
        "tool": "check",
        "ok": ok,
        "total": len(results),
        "passed": sum(1 for r in results if r["ok"]),
        "results": results,
    }
    print("\n__SUMMARY_JSON__ " + json.dumps(summary, ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
