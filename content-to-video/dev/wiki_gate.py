#!/usr/bin/env python3
"""wiki_gate.py — WikiSkill 四阶段里的「④ 门控」。

流程：**基线门控 → 快照 → 应用 → 门控 → 绿则留痕 / 红则回滚**。

两条关键设定：

1. **先跑基线再应用。** 如果应用前门控就已经是红的，那么应用后变红不能怪这份
   提案——直接拒绝应用并说清"基线已经是红的"。这是对 patterns/p03（环境限制 vs
   代码回归必须分开）的制度化。
2. **回滚用文件级快照，不用 `git checkout --`。** 技能目录里可能有维护者未提交
   的在制品，checkout 会把它们一起抹掉。这里只复制/还原本次真正改到的那一个
   文件，影响面严格等于改动面。git 仅记录 HEAD 供事后对照，不参与回滚动作。

门控分档：
  · fast（默认）：selftest + run_eval + check，确定性、零 API，秒级~分钟级
  · full：fast 通过后再跑 dev/evals.json 的 10 条 forward-test（需另一个 agent
    实例 + 真实 API）。本脚本跑完 fast 后会**停下来**生成检查单（退出码 3），
    由人工/agent 评分后用 --forward-result 续跑。

用法：
  python dev/wiki_gate.py --apply dev/wiki/proposals/xxx.json
  python dev/wiki_gate.py --apply dev/wiki/proposals/xxx.json --gate full
  python dev/wiki_gate.py --forward-result /tmp/ft.json --proposal-id <id>
  python dev/wiki_gate.py --rollback <proposal-id>
  python dev/wiki_gate.py --status
"""
import argparse
import datetime
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _wiki_common import (  # noqa: E402
    IMPACT_MD, INCUBATING_DIR, PROPOSALS_DIR, SKILL_DIR, SNAPSHOTS_DIR,
    apply_proposal, append_log, gate_summary, in_git_repo, read_json,
    restore, run_gate, snapshot, validate_proposal, write_json,
)

EXIT_OK = 0
EXIT_REFUSED_OR_ROLLED_BACK = 1
EXIT_WAITING_FORWARD = 3

AUTO_MARKER = "<!-- AUTO-ROWS-BELOW -->"


# ─────────────────────────────────────────────────────────────
# 留痕
# ─────────────────────────────────────────────────────────────

def _impact_row(date, version, summary, refs, gate_text, result):
    return "| %s | %s | %s | %s | %s | %s |" % (
        date, version, summary, refs, gate_text, result)


def _prepend_impact_row(row):
    """把新行插到 AUTO_MARKER 正下方（新行在前）。文件/标记缺失则跳过，
    不因为留痕失败而推翻一次已经通过门控的改动。"""
    if not os.path.isfile(IMPACT_MD):
        print("[warn] 找不到 %s，跳过留痕" % IMPACT_MD, file=sys.stderr)
        return False
    with open(IMPACT_MD, "r", encoding="utf-8") as f:
        text = f.read()
    if AUTO_MARKER not in text:
        print("[warn] %s 缺 %s 标记，跳过留痕"
              % (IMPACT_MD, AUTO_MARKER), file=sys.stderr)
        return False
    with open(IMPACT_MD, "w", encoding="utf-8") as f:
        f.write(text.replace(AUTO_MARKER,
                             AUTO_MARKER + "\n" + row, 1))
    return True


def _skill_version():
    try:
        sys.path.insert(0, os.path.join(SKILL_DIR, "scripts"))
        import _trace
        return _trace.skill_version(SKILL_DIR) or "?"
    except Exception:
        return "?"


def _load_proposal(path):
    if not os.path.isfile(path):
        # 也允许只给 id，去 proposals/ 与 incubating/ 里找
        for d in (PROPOSALS_DIR, INCUBATING_DIR):
            cand = os.path.join(d, path if path.endswith(".json")
                                else path + ".json")
            if os.path.isfile(cand):
                return cand, read_json(cand)
        return None, None
    return path, read_json(path)


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

def cmd_apply(args):
    path, prop = _load_proposal(args.file)
    if prop is None:
        print("❌ 找不到提案：%s" % args.file, file=sys.stderr)
        return EXIT_REFUSED_OR_ROLLED_BACK
    pid = prop.get("id") or os.path.splitext(os.path.basename(path))[0]

    if prop.get("status") not in ("proposed", "incubating"):
        print("❌ 提案状态为 %r，只有 proposed/incubating 可被应用"
              % prop.get("status"), file=sys.stderr)
        return EXIT_REFUSED_OR_ROLLED_BACK

    ok, errors = validate_proposal(prop, SKILL_DIR)
    if not ok:
        print("❌ 提案校验未通过：", file=sys.stderr)
        for e in errors:
            print("   · %s" % e, file=sys.stderr)
        return EXIT_REFUSED_OR_ROLLED_BACK

    level = args.gate or prop.get("gate") or "fast"

    print("=" * 60)
    print("提案 %s" % pid)
    print("  目标：%s（%s）" % (prop.get("target"), prop.get("locator")))
    print("  依据：%s" % ", ".join(prop.get("pattern_refs") or []))
    print("  门控档位：%s" % level)
    print("=" * 60)

    # ── 1) 基线门控：基线红就别赖提案 ──────────────────────────
    print("\n[1/5] 跑基线门控（应用前）…")
    base = run_gate("fast", SKILL_DIR)
    print("      基线：%s → %s" % (gate_summary(base),
                                   "绿" if base["passed"] else "红"))
    if not base["passed"]:
        msg = ("基线门控未通过，拒绝应用。\n"
               "  现在红的是既有问题，不是这份提案造成的——先把基线修绿再来自旋，\n"
               "  否则'应用后变红'这个信号将失去判别力。")
        if not args.force_baseline:
            print("❌ " + msg, file=sys.stderr)
            for s in base["steps"]:
                if not s["ok"]:
                    print("   · %s：%s" % (s["name"], s), file=sys.stderr)
            return EXIT_REFUSED_OR_ROLLED_BACK
        print("⚠️  基线为红，但 --force-baseline 已指定，继续（后果自负）")

    # ── 2) 快照 ───────────────────────────────────────────────
    # 快照失败必须中止，绝不能"没快照就往下改"——那等于把回滚能力提前交出去了。
    # snapshot() 对"源文件不存在"是抛错而非静默跳过，这里接住它，别裸崩。
    print("\n[2/5] 快照将被修改的文件…")
    try:
        snap_dir = snapshot([prop["target"]], pid, SKILL_DIR)
    except Exception as e:
        print("❌ 快照失败，已中止（未改动任何文件）：%s" % e, file=sys.stderr)
        return EXIT_REFUSED_OR_ROLLED_BACK
    print("      → %s" % snap_dir)

    # ── 3) 应用 ───────────────────────────────────────────────
    print("\n[3/5] 应用改动…")
    try:
        apply_proposal(prop, SKILL_DIR)
    except Exception as e:
        n = restore(pid, SKILL_DIR)
        if not n:
            print("❌ 应用失败，且回滚未生效（还原 %s 个文件）：%s"
                  % (n, e), file=sys.stderr)
            print("   请手工检查 .snapshots/%s/" % pid, file=sys.stderr)
        else:
            print("❌ 应用失败，已还原 %d 个文件：%s" % (n, e), file=sys.stderr)
        return EXIT_REFUSED_OR_ROLLED_BACK
    print("      已改 %s" % prop["target"])

    # ── 4) 门控 ───────────────────────────────────────────────
    print("\n[4/5] 跑门控（应用后）…")
    gate = run_gate("fast", SKILL_DIR)
    print("      %s → %s" % (gate_summary(gate),
                             "绿" if gate["passed"] else "红"))

    if not gate["passed"]:
        return _rollback(pid, path, prop, gate, args,
                         "门控未通过：" + gate_summary(gate))

    # fast 绿但档位是 full → 停下来等 forward-test
    if level == "full" and not args.forward_result:
        return _wait_forward_test(pid, path, prop, gate)

    # ── 5) 收尾 ───────────────────────────────────────────────
    ft_note = ""
    if args.forward_result:
        ft = read_json(args.forward_result)
        ft_note = "；forward-test %s" % ("通过" if ft.get("passed")
                                         else "未通过（%s）" % ft.get("notes", ""))
        if not ft.get("passed"):
            return _rollback(pid, path, prop, gate, args,
                             "forward-test 未通过：" + str(ft.get("notes", "")))

    return _finalize(pid, path, prop, gate, ft_note, level)


def _wait_forward_test(pid, path, prop, gate):
    """full 档：fast 绿了，但 forward-test 需要 agent 参与，先停下来出检查单。"""
    pending = os.path.join(SNAPSHOTS_DIR, pid, "pending.json")
    evals = os.path.join(SKILL_DIR, "dev", "evals.json")
    cases = []
    try:
        with open(evals, "r", encoding="utf-8") as f:
            data = json.load(f)
        cases = [{"id": c.get("id"), "prompt": c.get("prompt"),
                  "expect": c.get("expect"), "reject": c.get("reject")}
                 for c in data.get("test_cases", [])]
    except (OSError, ValueError) as e:
        print("[warn] 读 evals.json 失败：%s" % e, file=sys.stderr)
    write_json(pending, {"proposal_id": pid, "proposal_path": path,
                         "fast_gate": gate, "cases": cases,
                         "waiting_since": datetime.datetime.now()
                         .astimezone().isoformat(timespec="seconds")})
    print("\n⏸  fast 全绿，但本提案档位为 full。")
    print("    forward-test 需要另一个 agent 实例 + 真实 API，脚本代劳不了。")
    print("    检查单已写入：%s" % pending)
    print("    逐条跑完 10 个用例后，把结果写成 "
          '{"passed": true/false, "notes": "..."} 然后：')
    print("      python dev/wiki_gate.py --forward-result <结果.json> "
          "--proposal-id %s" % pid)
    print("    （改动**已应用但未最终确认**，快照在 "
          "%s，可随时 --rollback %s）" % (os.path.join(SNAPSHOTS_DIR, pid), pid))
    return EXIT_WAITING_FORWARD


def _rollback(pid, path, prop, gate, args, reason):
    """回滚 skill 层。**wiki 层不动**——这是 WikiSkill 与"试错重来"的分界线。"""
    print("\n[5/5] ❌ 回滚：%s" % reason)
    n = restore(pid, SKILL_DIR)
    if not n:
        # 还原 0 个文件 = 安全网没兜住。绝不能当成"回滚成功"——那比不回滚更糟：
        # 人以为回到原点了，实际改动还留在磁盘上。
        print("      ⚠️ 还原了 0 个文件，回滚**没有生效**，请手工检查 %s"
              % prop.get("target"), file=sys.stderr)
        append_log("## %s · rollback-failed · `%s`\n\n"
                   "- 目标：%s\n- **还原了 0 个文件**：快照为空或路径对不上，"
                   "回滚未生效，改动仍在磁盘上。这是安全网失效，必须人工介入。"
                   % (datetime.date.today().isoformat(), pid,
                      prop.get("target")))
    print("      已从快照还原 %s 个文件（%s）"
          % (n if n is not None else 0, prop.get("target")))
    prop["status"] = "rolled_back"
    prop["rolled_back_at"] = datetime.datetime.now().astimezone().isoformat(
        timespec="seconds")
    prop["rollback_reason"] = reason
    prop["gate_result"] = gate
    write_json(path, prop)

    if not args.no_incubate:
        os.makedirs(INCUBATING_DIR, exist_ok=True)
        dst = os.path.join(INCUBATING_DIR, os.path.basename(path))
        write_json(dst, dict(prop, status="incubating",
                             incubate_reason=reason))
        print("      提案已转入孵化区（不删）：%s" % dst)

    append_log("## %s · rollback · `%s`\n\n"
               "- 目标：%s（%s）\n- 依据：%s\n- 原因：%s\n"
               "- 门控：%s\n- **wiki 层未回滚**——经验保留，改动撤销。"
               % (datetime.date.today().isoformat(), pid, prop.get("target"),
                  prop.get("locator"), ",".join(prop.get("pattern_refs") or []),
                  reason, gate_summary(gate)))
    _prepend_impact_row(_impact_row(
        datetime.date.today().isoformat(), _skill_version(),
        "（已回滚）%s" % (prop.get("rationale") or "")[:40],
        ",".join(prop.get("pattern_refs") or []),
        gate_summary(gate), "❌ 已回滚"))
    return EXIT_REFUSED_OR_ROLLED_BACK


def _finalize(pid, path, prop, gate, ft_note, level):
    print("\n[5/5] ✅ 门控全绿，保留改动")
    prop["status"] = "applied"
    prop["applied_at"] = datetime.datetime.now().astimezone().isoformat(
        timespec="seconds")
    prop["gate_result"] = gate
    write_json(path, prop)

    note = "fast: %s%s" % (gate_summary(gate), ft_note)
    append_log("## %s · applied · `%s`\n\n"
               "- 目标：%s（%s）\n- 依据：%s\n- 门控（%s）：%s\n"
               "- 快照：%s"
               % (datetime.date.today().isoformat(), pid, prop.get("target"),
                  prop.get("locator"), ",".join(prop.get("pattern_refs") or []),
                  level, note, os.path.join(".snapshots", pid)))
    _prepend_impact_row(_impact_row(
        datetime.date.today().isoformat(), _skill_version(),
        (prop.get("rationale") or "")[:48],
        ",".join(prop.get("pattern_refs") or []),
        note, "✅ 保留"))
    print("      已写入 %s 与 logs.md" % os.path.basename(IMPACT_MD))
    return EXIT_OK


def cmd_rollback(args):
    pid = args.proposal_id
    n = restore(pid, SKILL_DIR)
    if n is None:
        print("❌ 没有 %s 的快照" % pid, file=sys.stderr)
        return EXIT_REFUSED_OR_ROLLED_BACK
    if n == 0:
        print("❌ 快照存在但还原了 0 个文件——回滚未生效，改动仍在磁盘上，"
              "请手工检查 .snapshots/%s/manifest.json" % pid, file=sys.stderr)
        append_log("## %s · manual-rollback-failed · `%s`\n\n"
                   "- 还原 0 个文件，回滚未生效。安全网失效，必须人工介入。"
                   % (datetime.date.today().isoformat(), pid))
        return EXIT_REFUSED_OR_ROLLED_BACK
    print("已从快照还原 %d 个文件" % n)
    for d in (PROPOSALS_DIR, INCUBATING_DIR):
        cand = os.path.join(d, pid + ".json")
        if os.path.isfile(cand):
            prop = read_json(cand)
            prop["status"] = "rolled_back"
            prop["rolled_back_at"] = datetime.datetime.now().astimezone() \
                .isoformat(timespec="seconds")
            write_json(cand, prop)
    append_log("## %s · manual-rollback · `%s`\n\n- 手工回滚，还原 %d 个文件。"
               % (datetime.date.today().isoformat(), pid, n))
    return EXIT_OK


def cmd_status(args):
    print("技能目录：%s" % SKILL_DIR)
    print("git 仓库：%s（回滚一律用文件快照，不用 git checkout）"
          % ("是" if in_git_repo(SKILL_DIR) else "否"))
    if not os.path.isdir(SNAPSHOTS_DIR):
        print("\n（尚无快照）")
        return 0
    print("\n%-42s %-14s %s" % ("SNAPSHOT", "文件数", "创建时间"))
    for n in sorted(os.listdir(SNAPSHOTS_DIR), reverse=True):
        d = os.path.join(SNAPSHOTS_DIR, n)
        m = os.path.join(d, "manifest.json")
        if not os.path.isfile(m):
            continue
        man = read_json(m)
        pending = " ⏸待 forward-test" if os.path.isfile(
            os.path.join(d, "pending.json")) else ""
        print("%-42s %-14s %s%s" % (n, len(man.get("files", [])),
                                    man.get("created", "?"), pending))
    return 0


def main():
    ap = argparse.ArgumentParser(description="门控：应用提案 / 回滚 / 查看状态")
    ap.add_argument("--apply", dest="file", default=None,
                    help="提案文件路径或提案 id")
    ap.add_argument("--gate", default=None, choices=("fast", "full"),
                    help="覆盖提案自带的门控档位")
    ap.add_argument("--forward-result", default=None,
                    help="forward-test 结果 JSON：{\"passed\":bool,\"notes\":str}")
    ap.add_argument("--proposal-id", default=None,
                    help="与 --forward-result 同用，指定续跑哪个提案")
    ap.add_argument("--force-baseline", action="store_true",
                    help="基线门控为红时仍强行应用（不推荐）")
    ap.add_argument("--no-incubate", action="store_true",
                    help="回滚后不把提案转入孵化区")
    ap.add_argument("--rollback", dest="rollback_id", default=None)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        return cmd_status(args)
    if args.rollback_id:
        args.proposal_id = args.rollback_id
        return cmd_rollback(args)
    if args.forward_result:
        if not args.proposal_id:
            print("❌ --forward-result 需要同时给 --proposal-id",
                  file=sys.stderr)
            return EXIT_REFUSED_OR_ROLLED_BACK
        path, prop = _load_proposal(args.proposal_id)
        if prop is None:
            print("❌ 找不到提案 %s" % args.proposal_id, file=sys.stderr)
            return EXIT_REFUSED_OR_ROLLED_BACK
        args.file = path
        args.gate = "full"
        args.no_incubate = True   # 已应用过一次，回滚也别再复制一份
        # 改动已在上一轮应用，这里只跑 fast + 收尾，不重复应用
        gate = run_gate("fast", SKILL_DIR)
        ft = read_json(args.forward_result)
        note = "；forward-test %s" % ("通过" if ft.get("passed")
                                      else "未通过（%s）" % ft.get("notes", ""))
        if not gate["passed"]:
            return _rollback(prop.get("id"), path, prop, gate, args,
                             "续跑时 fast 门控变红：" + gate_summary(gate))
        if not ft.get("passed"):
            return _rollback(prop.get("id"), path, prop, gate, args,
                             "forward-test 未通过：" + str(ft.get("notes", "")))
        return _finalize(prop.get("id"), path, prop, gate, note, "full")
    if args.file:
        return cmd_apply(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
