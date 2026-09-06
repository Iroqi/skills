#!/usr/bin/env python3
"""wiki_trace.py — Raw Layer 的查看与维护 CLI。

轨迹由 scripts/run.py 在每次实跑结束时旁路写到
~/.config/ai-video/traces/（可由 CTV_TRACE_DIR 改）。本脚本负责看、统计、
剪枝、导入导出，**不产生轨迹、也不修改轨迹内容**（Raw Layer 只追加）。

  python dev/wiki_trace.py stats                 # 失败率/兜底率/高频失败环节
  python dev/wiki_trace.py list --outcome step_failed --limit 10
  python dev/wiki_trace.py show <trace-id>
  python dev/wiki_trace.py prune --max 200 --older-than-days 180 --dry-run
  python dev/wiki_trace.py export -o /tmp/traces.jsonl
  python dev/wiki_trace.py import --from /tmp/traces.jsonl
  python dev/wiki_trace.py mark-maintained       # 打"已蒸馏到此"的进度标记
"""
import argparse
import datetime
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))

from _wiki_common import (  # noqa: E402
    marker_cutoff, marker_path, parse_trace_ts, write_json,
)
import _trace  # noqa: E402

# 分界时间的解析与比较一律走 _wiki_common：两边各写一遍的后果，是同一个时区
# bug 在一个脚本里崩溃、在另一个里被 try/except 吞成静默失效（见 p12）。
MARKER = marker_path()


def _fmt(v, n=24):
    s = str(v)
    return s if len(s) <= n else s[:n - 1] + "…"


def cmd_list(args):
    rows = []
    for _p, rec in _trace.iter_traces():
        if args.outcome and rec.get("outcome") != args.outcome:
            continue
        if args.since_last:
            # 不再自己 fromtimestamp：那边给的是朴素时间，跟 ts 的带时区时间
            # 一比就 TypeError。统一用 marker_cutoff / parse_trace_ts。
            cutoff = marker_cutoff(MARKER)
            ts = parse_trace_ts(rec.get("ts"))
            if cutoff and ts and ts < cutoff:
                continue
        rows.append(rec)
    rows = rows[:args.limit] if args.limit else rows
    if not rows:
        print("（没有匹配的轨迹）")
        return 0
    print("%-22s %-13s %-11s %-8s %s" % ("ID", "OUTCOME", "SKILL", "秒", "摘要"))
    for r in rows:
        fb = ",".join(r.get("fallbacks") or []) or "-"
        print("%-22s %-13s %-11s %-8s %s" % (
            _fmt(r.get("id"), 22), _fmt(r.get("outcome"), 13),
            _fmt(r.get("skill_version") or "-", 11),
            _fmt(r.get("total_seconds"), 8), _fmt(fb, 40)))
    print("\n共 %d 条" % len(rows))
    return 0


def cmd_show(args):
    for p, rec in _trace.iter_traces():
        if rec.get("id") == args.trace_id or \
                os.path.splitext(os.path.basename(p))[0] == args.trace_id:
            print(json.dumps(rec, ensure_ascii=False, indent=2))
            return 0
    print("找不到轨迹 %s" % args.trace_id, file=sys.stderr)
    return 1


def cmd_stats(args):
    recs = [r for _p, r in _trace.iter_traces()]
    if not recs:
        print("（暂无轨迹。跑一次 python scripts/run.py ... 就会留下一条）")
        return 0
    total = len(recs)
    print("轨迹总数：%d" % total)
    if os.path.isfile(MARKER):
        print("上次蒸馏标记：%s" % datetime.datetime.fromtimestamp(
            os.path.getmtime(MARKER)).strftime("%Y-%m-%d %H:%M:%S"))

    print("\n── 结局分布")
    for k, v in Counter(r.get("outcome") for r in recs).most_common():
        print("  %-16s %4d  %5.1f%%" % (k, v, 100.0 * v / total))

    fb = sum(1 for r in recs if r.get("fallbacks"))
    print("\n── 兜底率：%d/%d (%.1f%%)" % (fb, total, 100.0 * fb / total))
    for k, v in Counter(",".join(r.get("fallbacks") or []) or "-"
                        for r in recs).most_common(5):
        print("  %-30s %4d" % (_fmt(k, 30), v))

    print("\n── 失败环节 TOP 5")
    stages = Counter()
    for r in recs:
        f = r.get("failure") or {}
        if r.get("outcome") not in ("ok",) or f:
            if f.get("stage"):
                stages[f["stage"]] += 1
    if stages:
        for k, v in stages.most_common(5):
            print("  %-24s %4d" % (_fmt(k, 24), v))
    else:
        print("  （无失败记录）")

    print("\n── 参数组合（params_hash）出现次数 TOP 5")
    ph = Counter(r.get("params_hash") for r in recs)
    for k, v in ph.most_common(5):
        sample = next((r.get("params") for r in recs
                       if r.get("params_hash") == k), {})
        print("  %-16s %4d  %s" % (k, v, _fmt(json.dumps(
            sample, ensure_ascii=False, sort_keys=True), 90)))

    print("\n── 失败率按参数组合（样本 ≥2 的才列出，避免单次噪声）")
    by_ph = defaultdict(lambda: {"n": 0, "bad": 0})
    for r in recs:
        d = by_ph[r.get("params_hash")]
        d["n"] += 1
        if r.get("outcome") != "ok":
            d["bad"] += 1
    rows = [(v["bad"] / v["n"], v, k) for k, v in by_ph.items() if v["n"] >= 2]
    for rate, v, k in sorted(rows, reverse=True)[:5]:
        print("  %-16s %3d/%-3d  失败率 %5.1f%%" % (k, v["bad"], v["n"],
                                                    100.0 * rate))

    print("\n── 环境指纹")
    envs = Counter(json.dumps(r.get("env"), sort_keys=True, ensure_ascii=False)
                   for r in recs)
    for k, v in envs.most_common(3):
        print("  %4d 次  %s" % (v, _fmt(k, 140)))
    return 0


def cmd_prune(args):
    n = _trace.prune(max_traces=args.max, max_age_days=args.older_than_days,
                     dry_run=args.dry_run)
    print(("[dry-run] 将删除 %d 条轨迹" if args.dry_run else "已删除 %d 条轨迹")
          % n)
    return 0


def cmd_export(args):
    n = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for _p, rec in _trace.iter_traces(newest_first=False):
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    print("已导出 %d 条到 %s" % (n, args.out))
    return 0


def cmd_import(args):
    n = 0
    with open(args.from_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError as e:
                print("[warn] 跳过无法解析的行：%s" % e, file=sys.stderr)
                continue
            if isinstance(rec, dict) and rec.get("id"):
                _trace.write_record(rec)
                n += 1
    print("已导入 %d 条" % n)
    return 0


def cmd_mark(args):
    write_json(MARKER, {"marked_at": datetime.datetime.now()
                        .astimezone().isoformat(timespec="seconds")})
    print("已打标记：%s（下次 --since-last 只看之后的轨迹）" % MARKER)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Raw Layer 轨迹查看与维护")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列出轨迹")
    p.add_argument("--outcome", default=None)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--since-last", action="store_true",
                   help="只显示上次 mark-maintained 之后的")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="显示单条轨迹")
    p.add_argument("trace_id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("stats", help="聚合统计（蒸馏前先看这个）")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("prune", help="按条数/天数滚动清理")
    p.add_argument("--max", type=int, default=_trace.DEFAULT_MAX_TRACES)
    p.add_argument("--older-than-days", type=int,
                   default=_trace.DEFAULT_MAX_AGE_DAYS)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser("export", help="导出为 JSONL")
    p.add_argument("-o", "--out", required=True)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="从 JSONL 导入")
    p.add_argument("--from", dest="from_file", required=True)
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("mark-maintained", help="打已蒸馏进度标记")
    p.set_defaults(func=cmd_mark)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
