#!/usr/bin/env python3
"""wiki_propose.py — WikiSkill 四阶段里的「③ Skill 提议者」（ReAct，单原子改动）。

**提议制**：本脚本默认只把提案写进 dev/wiki/proposals/，**不碰 SKILL.md**。
真正落地由 `wiki_gate.py --apply` 负责（先跑基线门控 → 快照 → 应用 → 跑门控 →
红则回滚）。把"提议"和"应用"拆开，是为了让每次改动在生效前都有一份可审阅、
可撤销的实体。

硬约束（WikiSkill：每轮恰好一处原子改动）：
  · 一次提案 = 一个文件 + 一处 old→new 替换
  · old 在目标文件中必须**恰好出现一次**
  · 必须能追到至少一条 patterns/ 下的经验
  · 不得改 dev/wiki/（不许改写自己的真值来源）

  python dev/wiki_propose.py --scaffold --from p03-ffmpeg-encoder-absent
  python dev/wiki_propose.py --scaffold --from p03-... -o dev/wiki/proposals/x.json
  python dev/wiki_propose.py --validate dev/wiki/proposals/x.json
  python dev/wiki_propose.py --list
  python dev/wiki_propose.py --incubate dev/wiki/proposals/x.json
"""
import argparse
import datetime
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _wiki_common import (  # noqa: E402
    INCUBATING_DIR, PROPOSALS_DIR, SKILL_DIR, iter_patterns, new_proposal_id,
    read_json, validate_proposal, write_json,
)

REACT_STEPS = [
    "OBSERVE：读 patterns/<id>.md 的「机制」与「证据」，确认根因不是环境问题",
    "THINK：这次要改的是文档、默认值、代码，还是提示语？只能选一个文件的一处",
    "ACT：填 target / old / new；old 必须是从目标文件里**原样复制**的片段，"
    "且全文件唯一",
    "REFLECT：改完后哪条现在会红的检查会变绿？写进 expected_impact；"
    "想不出可验证的影响，就别提这个案",
]


def _find_pattern(pid):
    for p, meta, body in iter_patterns():
        if meta.get("id") == pid:
            return p, meta, body
    return None, None, None


def cmd_scaffold(args):
    refs = args.from_pattern or []
    unknown = []
    context = []
    for pid in refs:
        path, meta, body = _find_pattern(pid)
        if path is None:
            unknown.append(pid)
            continue
        # 只带 frontmatter + 正文开头，避免把整篇经验塞进提案（提案要小）
        context.append({
            "id": pid,
            "type": meta.get("type"),
            "title": meta.get("title"),
            "confidence": meta.get("confidence"),
            "excerpt": (body or "").strip()[:600],
        })
    if unknown:
        print("[错误] patterns/ 下找不到：%s" % ", ".join(unknown),
              file=sys.stderr)
        print("现有条目：%s" % ", ".join(
            m.get("id") or "?" for _p, m, _b in iter_patterns()),
            file=sys.stderr)
        return 1

    prop = {
        "id": new_proposal_id(refs[0] if refs else None),
        "created": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "pattern_refs": refs,
        "rationale": "TODO：一句话说明这条经验暴露了什么、为什么非改不可",
        "target": args.target or "TODO：相对技能目录的路径，如 SKILL.md",
        "locator": args.locator or "TODO：定位描述（行号/锚点/小节标题）",
        "old": "TODO：从目标文件原样复制的待替换片段（必须全文件唯一）",
        "new": "TODO：替换后的文本",
        "expected_impact": "TODO：哪条检查会从红变绿？给出可验证的判据",
        "risk": args.risk,
        "gate": args.gate,
        "status": "proposed",
        "_react": REACT_STEPS,
        "_pattern_context": context,
    }
    text = json.dumps(prop, ensure_ascii=False, indent=2)
    if args.out:
        write_json(args.out, prop)
        print("提案骨架已写入 %s" % args.out)
        print("填完后再跑：python dev/wiki_propose.py --validate %s" % args.out)
        return 0
    print(text)
    return 0


def cmd_validate(args):
    for f in args.files:
        if not os.path.isfile(f):
            print("❌ 文件不存在：%s" % f, file=sys.stderr)
            continue
        try:
            prop = read_json(f)
        except ValueError as e:
            print("❌ %s 不是合法 JSON：%s" % (f, e), file=sys.stderr)
            continue
        ok, errors = validate_proposal(prop, SKILL_DIR)
        if ok:
            print("✅ %s —— 合法（%s：%s）"
                  % (f, prop.get("target"), prop.get("locator")))
            print("   下一步：python dev/wiki_gate.py --apply %s "
                  "--gate %s" % (f, prop.get("gate") or "fast"))
        else:
            print("❌ %s" % f)
            for e in errors:
                print("   · %s" % e)
        print()
    return 0


def _iter_proposal_files(include_incubating=True):
    out = []
    for d in (PROPOSALS_DIR, INCUBATING_DIR if include_incubating else None):
        if not d or not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            if n.endswith(".json") and n != "manifest.json":
                out.append(os.path.join(d, n))
    return out


def cmd_list(args):
    rows = _iter_proposal_files()
    if not rows:
        print("（暂无提案。用 --scaffold --from <pattern-id> 生成一份）")
        return 0
    print("%-42s %-12s %-24s %s" % ("ID", "STATUS", "TARGET", "依据"))
    for f in rows:
        try:
            prop = read_json(f)
        except ValueError:
            continue
        if args.status and prop.get("status") != args.status:
            continue
        print("%-42s %-12s %-24s %s" % (
            prop.get("id", os.path.basename(f))[:42],
            prop.get("status", "?")[:12],
            str(prop.get("target", "?"))[:24],
            ",".join(prop.get("pattern_refs") or [])))
    return 0


def cmd_incubate(args):
    """把提案移入 incubating/：门控拒掉的案子**不删**，将来可复活。

    对应论文局限"严格门控会丢弃使能型改动"——本次失败但为后续铺路的改动，
    留着才能让后来的 pattern 引用它。
    """
    src = args.file
    if not os.path.isfile(src):
        print("❌ 不存在：%s" % src, file=sys.stderr)
        return 1
    prop = read_json(src)
    prop["status"] = "incubating"
    prop["incubated_at"] = datetime.datetime.now().astimezone().isoformat(
        timespec="seconds")
    prop["incubate_reason"] = args.reason or "（未填）"
    os.makedirs(INCUBATING_DIR, exist_ok=True)
    dst = os.path.join(INCUBATING_DIR, os.path.basename(src))
    write_json(dst, prop)
    if os.path.abspath(src) != os.path.abspath(dst):
        os.remove(src)
    print("已转入孵化：%s" % dst)
    return 0


def cmd_revive(args):
    src = args.file
    if not os.path.isfile(src):
        print("❌ 不存在：%s" % src, file=sys.stderr)
        return 1
    prop = read_json(src)
    prop["status"] = "proposed"
    prop["revived_at"] = datetime.datetime.now().astimezone().isoformat(
        timespec="seconds")
    dst = os.path.join(PROPOSALS_DIR, os.path.basename(src))
    write_json(dst, prop)
    if os.path.abspath(src) != os.path.abspath(dst):
        os.remove(src)
    ok, errors = validate_proposal(prop, SKILL_DIR)
    print("已复活到 %s" % dst)
    if not ok:
        print("⚠️  复活后校验未通过（目标文件可能已变）：")
        for e in errors:
            print("   · %s" % e)
        return 1
    print("✅ 校验通过，可重新走门控")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Skill 提议者：生成/校验单原子改动提案（默认不改 SKILL.md）")
    ap.add_argument("--scaffold", action="store_true", help="生成提案骨架")
    ap.add_argument("--from", dest="from_pattern", action="append", default=[],
                    help="依据的 pattern id（可多次）")
    ap.add_argument("--target", default=None, help="目标文件（相对技能目录）")
    ap.add_argument("--locator", default=None, help="定位描述")
    ap.add_argument("--risk", default="medium",
                    choices=("low", "medium", "high"))
    ap.add_argument("--gate", default="fast", choices=("fast", "full"))
    ap.add_argument("-o", "--out", default=None, help="骨架落盘路径")
    ap.add_argument("--validate", dest="validate_files", nargs="*", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--status", default=None, help="--list 时按状态过滤")
    ap.add_argument("--incubate", default=None, help="把提案转入孵化区")
    ap.add_argument("--revive", default=None, help="把孵化区的提案复活")
    ap.add_argument("--reason", default=None, help="--incubate 的原因")
    args = ap.parse_args()

    if args.scaffold:
        return cmd_scaffold(args)
    if args.validate_files is not None:
        args.files = args.validate_files
        return cmd_validate(args)
    if args.list:
        return cmd_list(args)
    if args.incubate:
        args.file = args.incubate
        return cmd_incubate(args)
    if args.revive:
        args.file = args.revive
        return cmd_revive(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
