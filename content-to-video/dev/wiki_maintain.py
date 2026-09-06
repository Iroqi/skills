#!/usr/bin/env python3
"""wiki_maintain.py — WikiSkill 四阶段里的「② Wiki 维护者」。

把 Raw Layer 的轨迹蒸馏成**有界的**摘要，供维护 agent 据此写/改
dev/wiki/patterns/*.md；并校验已写好的 pattern 是否符合契约。

关键设计：**蒸馏本身是语义判断，脚本不做假。** 本脚本只负责确定性的部分——
聚类、排序、截断、去重、取证——把"这条经验到底该怎么表述"留给 agent。
硬让脚本去"总结"只会产出正确的废话。

  python dev/wiki_maintain.py --brief                     # 打印蒸馏摘要
  python dev/wiki_maintain.py --brief --out /tmp/brief.md # 落盘
  python dev/wiki_maintain.py --brief --since-last        # 只看上次标记之后
  python dev/wiki_maintain.py --commit                    # 校验 patterns 契约
  python dev/wiki_maintain.py --merge-check               # 查重/合并建议（防 wiki 膨胀）
"""
import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))

from _wiki_common import (  # noqa: E402
    BRIEF_ITEM_MAX_CHARS, BRIEF_MAX_FAILURES, BRIEF_MAX_SUCCESSES,
    LOGS_MD, PATTERNS_DIR, WIKI_DIR, append_log, iter_patterns,
    marker_cutoff, marker_path, parse_trace_ts, validate_pattern,
)
import _trace  # noqa: E402

MARKER = marker_path()


# ─────────────────────────────────────────────────────────────
# ① 蒸馏摘要
# ─────────────────────────────────────────────────────────────

def _truncate(s, limit=BRIEF_ITEM_MAX_CHARS):
    s = s or ""
    return s if len(s) <= limit else s[:limit] + "\n…[已达单条 %d 字符上限]…" % limit


def _kill(s):
    """聚类用的 key 归一化：None/空统一成一个占位，避免 None 与 "" 分成两簇。"""
    return str(s) if s not in (None, "") else "-"


def _env_tag(rec):
    e = rec.get("env") or {}
    return "py%s/node%s/ff%s/h264=%s" % (
        _kill(e.get("py")), _kill(e.get("node")), _kill(e.get("ffmpeg")),
        _kill(e.get("h264_encoder")))


def _collect(since_last):
    # 分界时间一律走 _wiki_common。这里原来自己 fromtimestamp 并 try/except
    # 掉 (ValueError, TypeError)：时区不匹配时它**不崩**，`--since-last` 直接
    # 静默退化成"不过滤"，比崩溃更难发现（见 patterns/p12）。
    cutoff = marker_cutoff(MARKER) if since_last else None
    out = []
    for _p, rec in _trace.iter_traces():
        if cutoff:
            ts = parse_trace_ts(rec.get("ts"))
            if ts and ts < cutoff:
                continue
        out.append(rec)
    return out


def _cluster_key(rec):
    f = rec.get("failure") or {}
    return (_kill(rec.get("outcome")), _kill(f.get("stage")),
            _kill(f.get("rc")))


def build_brief(recs, max_fail=BRIEF_MAX_FAILURES,
                max_succ=BRIEF_MAX_SUCCESSES):
    """把轨迹聚成有界摘要。上限是 WikiSkill 的设定：≤5 失败 + ≤3 成功。"""
    bad = [r for r in recs if r.get("outcome") != "ok"]
    good = [r for r in recs if r.get("outcome") == "ok"]

    fail_groups = defaultdict(list)
    for r in bad:
        fail_groups[_cluster_key(r)].append(r)
    succ_groups = defaultdict(list)
    for r in good:
        succ_groups[r.get("params_hash")].append(r)

    # 排序：先按出现次数，再按最近一次——高频且新鲜的优先蒸馏
    def recency(g):
        return max((x.get("ts") or "") for x in g)

    fail_items = sorted(fail_groups.items(),
                        key=lambda kv: (len(kv[1]), recency(kv[1])),
                        reverse=True)[:max_fail]
    succ_items = sorted(succ_groups.items(),
                        key=lambda kv: (len(kv[1]), recency(kv[1])),
                        reverse=True)[:max_succ]

    lines = []
    lines.append("# 蒸馏摘要（Wiki Maintainer brief）")
    lines.append("")
    lines.append("- 生成时间：%s" % datetime.datetime.now().astimezone()
                 .strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("- 本次纳入轨迹：**%d** 条（其中非正常结局 %d 条）"
                 % (len(recs), len(bad)))
    lines.append("- 上限：失败簇 ≤%d，成功簇 ≤%d，单条 ≤%d 字符"
                 % (max_fail, max_succ, BRIEF_ITEM_MAX_CHARS))
    lines.append("")
    lines.append("> 用法：照下面每一簇写/改 `dev/wiki/patterns/<id>.md`，"
                 "frontmatter 七字段必填（见 `dev/wiki/README.md`），"
                 "然后跑 `python dev/wiki_maintain.py --commit` 校验，"
                 "再跑 `python dev/wiki_trace.py mark-maintained` 打进度标记。")
    lines.append("")

    envs = Counter(_env_tag(r) for r in recs)
    if len(envs) > 1:
        lines.append("## ⚠️ 环境指纹发生变化")
        lines.append("")
        lines.append("本次轨迹出现在 %d 种环境上。跨环境对比时先确认"
                     "**失败是不是换环境导致的**，别急着归因到代码："
                     % len(envs))
        for k, v in envs.most_common():
            lines.append("- %s 次：%s" % (v, k))
        lines.append("")

    lines.append("## 失败簇（按「出现次数 → 最近发生」排序，取前 %d）" % max_fail)
    lines.append("")
    if not fail_items:
        lines.append("（无失败轨迹）")
        lines.append("")
    for (outcome, stage, rc), group in fail_items:
        tails = []
        for r in group:
            t = (r.get("failure") or {}).get("stderr_tail")
            if t and t not in tails:
                tails.append(t)
        fbs = Counter(",".join(r.get("fallbacks") or []) or "-" for r in group)
        item = [
            "### [%d 次] outcome=%s stage=%s rc=%s"
            % (len(group), outcome, stage, rc),
            "",
            "- 轨迹样例：%s" % ", ".join(
                _kill(r.get("id")) for r in group[:3]),
            "- 环境：%s" % ", ".join(
                "%s×%d" % (k, v) for k, v in
                Counter(_env_tag(r) for r in group).most_common(3)),
            # params 没有长度上限（`--title` 这类自由文本一旦进 params 就能
            # 单条撑爆），所以先在每个长字段上各截一刀，保住条目结构。
            "- 参数：%s" % " | ".join(
                _truncate(json.dumps(r.get("params"), ensure_ascii=False,
                                     sort_keys=True), 2000)
                for r in group[:2]),
            "- 兜底信号：%s" % ", ".join(
                "%s×%d" % (k, v) for k, v in fbs.most_common(3)),
            "- stderr 尾巴（已去敏）：",
        ]
        for t in tails[:3]:
            item.append("  ```")
            item.append("  %s" % t.replace("\n", "\n  "))
            item.append("  ```")
        item.append("- **待回答**：这是环境问题、代码问题，还是文档/默认值问题？"
                    "（见 patterns/p03：三者必须分开报告）")
        # 上限的粒度是「每条 ≤ BRIEF_ITEM_MAX_CHARS」，不是「全文 ≤ N×上限」。
        # 之前只在返回前对**整篇**截一刀，单条能悄悄超限——而头部还写着
        # 「单条 ≤15000 字符」，读者会信。粒度错了，上限等于没写。
        lines.append(_truncate("\n".join(item)))
        lines.append("")

    lines.append("## 成功簇（同一参数组合跑通的，取前 %d）" % max_succ)
    lines.append("")
    if not succ_items:
        lines.append("（无成功轨迹）")
        lines.append("")
    for ph, group in succ_items:
        secs = [r.get("total_seconds") for r in group if r.get("total_seconds")]
        clean = sum(1 for r in group if not r.get("fallbacks"))
        item = [
            "### [%d 次] params_hash=%s" % (len(group), _kill(ph)),
            "",
            "- 参数：%s" % _truncate(
                json.dumps((group[0].get("params") or {}), ensure_ascii=False,
                           sort_keys=True), 2000),
            "- 全程耗时：中位数 %ss（样本 %d）"
            % (_median(secs) if secs else "-", len(secs)),
            "- 其中未触发任何兜底：%d/%d" % (clean, len(group)),
            "- 轨迹样例：%s" % ", ".join(
                _kill(r.get("id")) for r in group[:3]),
        ]
        lines.append(_truncate("\n".join(item)))
        lines.append("")

    text = "\n".join(lines)
    # 整篇再兜一刀，防的是头部/环境警告那几段自己失控，不是用来代替每条上限
    return _truncate(text, BRIEF_ITEM_MAX_CHARS * 12), len(fail_items), \
        len(succ_items)


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return round(xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2.0, 1)


# ─────────────────────────────────────────────────────────────
# ② 契约校验
# ─────────────────────────────────────────────────────────────

def cmd_commit(args):
    if not os.path.isdir(PATTERNS_DIR):
        print("patterns/ 不存在：%s" % PATTERNS_DIR, file=sys.stderr)
        return 1
    total = ok_n = 0
    problems = []
    for name in sorted(os.listdir(PATTERNS_DIR)):
        if not name.endswith(".md") or name.startswith("."):
            continue
        total += 1
        ok, errors, meta = validate_pattern(os.path.join(PATTERNS_DIR, name))
        if ok:
            ok_n += 1
            print("  ✅ %-40s [%s/%s]" % (name, meta.get("type"),
                                          meta.get("confidence")))
        else:
            print("  ❌ %-40s" % name)
            for e in errors:
                print("       · %s" % e)
            problems.append((name, errors))
    print("\npatterns 契约：%d/%d 通过" % (ok_n, total))
    if problems:
        print("未通过 %d 个：%s" % (len(problems),
                                    ", ".join(n for n, _ in problems)))
        return 1
    if args.log:
        append_log("## %s · commit · patterns 契约 %d/%d 通过"
                   % (datetime.date.today().isoformat(), ok_n, total))
        print("已追加到 %s" % LOGS_MD)
    return 0


# ─────────────────────────────────────────────────────────────
# ③ 查重 / 合并建议（对抗"wiki 无自动剪枝"这条已知局限）
# ─────────────────────────────────────────────────────────────

def _tokens(s):
    return set(t for t in re.split(r"\W+", (s or "").lower()) if len(t) > 1)


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def cmd_merge_check(args):
    items = []
    for p, meta, _body in iter_patterns():
        tags = meta.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        items.append({"path": p, "id": meta.get("id"),
                      "title": meta.get("title") or "",
                      "tags": set(tags), "status": meta.get("status")})
    by_tags = defaultdict(list)
    for it in items:
        by_tags[frozenset(it["tags"])].append(it)

    hits = 0
    print("── 同标签集合（可能重复覆盖同一主题）")
    for tags, group in by_tags.items():
        if len(group) > 1 and tags:
            hits += 1
            print("  [%s] → %s" % (",".join(sorted(tags)),
                                   ", ".join(g["id"] for g in group)))
    if not hits:
        print("  （无）")

    print("\n── 标题高度相似（Jaccard ≥ %.2f）" % args.threshold)
    n = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            s = _jaccard(_tokens(items[i]["title"]), _tokens(items[j]["title"]))
            if s >= args.threshold:
                n += 1
                print("  %.2f  %s ↔ %s" % (s, items[i]["id"], items[j]["id"]))
    if not n:
        print("  （无）")

    stale = [it for it in items if it["status"] == "deprecated"]
    print("\n── 已标记 deprecated 的条目（可归档）：%s"
          % (", ".join(i["id"] for i in stale) or "无"))
    print("\n建议：合并/归档后把旧条目标 status: merged 或 deprecated，"
          "**不要删文件**——wiki 不留空洞，被回滚的改动要能追到历史经验。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Wiki 维护者：蒸馏 + 契约校验")
    ap.add_argument("--brief", action="store_true", help="生成蒸馏摘要")
    ap.add_argument("--out", default=None, help="摘要落盘路径（默认 stdout）")
    ap.add_argument("--since-last", action="store_true",
                    help="只纳入上次 mark-maintained 之后的轨迹")
    ap.add_argument("--max-fail", type=int, default=BRIEF_MAX_FAILURES)
    ap.add_argument("--max-succ", type=int, default=BRIEF_MAX_SUCCESSES)
    ap.add_argument("--commit", action="store_true",
                    help="校验 patterns/ 下所有文件的契约")
    ap.add_argument("--log", action="store_true",
                    help="--commit 通过时追加一条 logs.md")
    ap.add_argument("--merge-check", action="store_true",
                    help="查重与合并建议")
    ap.add_argument("--threshold", type=float, default=0.6)
    args = ap.parse_args()

    if args.commit:
        return cmd_commit(args)
    if args.merge_check:
        return cmd_merge_check(args)
    if args.brief or not (args.commit or args.merge_check):
        recs = _collect(args.since_last)
        text, nf, ns = build_brief(recs, args.max_fail, args.max_succ)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
            print("摘要已写入 %s（失败簇 %d / 成功簇 %d）" % (args.out, nf, ns))
        else:
            print(text)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
