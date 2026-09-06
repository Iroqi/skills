#!/usr/bin/env python3
"""时长预算：写稿前粗估"大概能装几段"，写完后粗估"这稿子多长"。

这是启发式估算，不是精确值——真实时长要等第 3 步实际跑完 TTS 才知道（每个人
说话快慢、断句方式都会有出入）。但在动笔之前先估一下量级（比如"目标 3 分钟，
大概能装 6-7 段"），比写完发现 40 秒或者 8 分钟再回头精简/扩写要省事得多。

两个子命令：
  plan      正向规划：给定目标时长，估算大概能装多少段内容
  estimate  反向核对：给定已经写好的 segments_source.json，估算这稿子多长

启发式假设（都可以用参数覆盖）：
  - 中文 TTS 语速：约 4.3 字/秒（--chars-per-sec，对应"语速适中"的播报速度；
    --speed 参数是倍率，会按比例调整）
  - 句间停顿：--gap 秒（默认 0.4，需要跟 pipeline.py 的 --gap 保持一致才准）

Usage:
  python budget.py plan --target 180
  python budget.py plan --target 180 --sentences-per-segment 6 --chars-per-sentence 25
  python budget.py estimate -i segments_source.json
  python budget.py estimate -i segments_source.json --speed 1.2
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import split_sentences, setup_stdio  # noqa: E402
from _contracts import (load_segments_source,  # noqa: E402
                        DEFAULT_SPEED, OPENING_CLOSING_DEFAULT_SPEED,
                        DEFAULT_CHARS_PER_SEC, DEFAULT_GAP,
                        estimate_sentence_seconds)


def cmd_plan(args):
    open_close_sents = args.opening_sentences + args.closing_sentences
    open_close_seconds = open_close_sents * (
        args.chars_per_sentence / args.chars_per_sec / args.speed + args.gap)

    per_seg_seconds = args.sentences_per_segment * (
        args.chars_per_sentence / args.chars_per_sec / args.speed + args.gap)

    content_budget = args.target - open_close_seconds
    if content_budget <= 0:
        print(f"[warn] 目标时长 {args.target}s 连开场+结尾（约 {open_close_seconds:.0f}s）都不够，"
              "请调大目标时长或减少开场/结尾句数", file=sys.stderr)
        n_segments = 0
    else:
        n_segments = max(1, round(content_budget / per_seg_seconds))

    est_total = open_close_seconds + n_segments * per_seg_seconds

    print(f"[assumptions] {args.chars_per_sentence} 字/句，{args.chars_per_sec} 字/秒，"
          f"{args.speed}x 语速，句间停顿 {args.gap}s，每段 {args.sentences_per_segment} 句")
    print(f"[plan] 目标 {args.target:.0f}s ≈ 开场/结尾 {open_close_seconds:.0f}s "
          f"+ 约 {n_segments} 个内容段落（每段约 {per_seg_seconds:.1f}s）")
    print(f"[estimate] 按此规划实际约 {est_total:.0f}s（跟目标 {args.target:.0f}s 的偏差"
          f"{est_total - args.target:+.0f}s，都是粗估，允许有出入）")
    print(f"\n下一步：整理信源时挑 ~{n_segments} 条候选，每条正文控制在约 "
          f"{args.sentences_per_segment} 句、每句约 {args.chars_per_sentence} 字左右。")


def cmd_estimate(args):
    source = load_segments_source(args.input)

    # 收集全片"扁平"句子列表 + 每句对应的语速（段落自带 speed 就用段落的，
    # 否则用全局默认），和 pipeline.py 的 sentence_speeds 覆盖逻辑保持一致。
    # gap 是按"整条视频里每两句之间"算的（pipeline.py 的 concat_audio 就是
    # 这么处理的，不是按段落分别停顿），所以最后统一用 (总句数-1)*gap，
    # 不在每个 block 内部单独算 gap。
    all_sentences = []  # (label, text, speed)
    block_ranges = []   # (label, start_idx, end_idx) 用于打印分段小计

    def collect(label, text, speed_override):
        if not text.strip():
            return
        sents = split_sentences(text)
        start = len(all_sentences)
        spd = speed_override if speed_override is not None else args.speed
        for s in sents:
            all_sentences.append((label, s, spd))
        block_ranges.append((label, start, len(all_sentences)))

    # opening/closing 没显式写语速时按 OPENING_CLOSING_DEFAULT_SPEED 估——
    # build_from_structured 造段配置用的就是这个默认值（1.2），这里必须
    # 复刻同一假设，否则开场/结尾估算比实测偏长 ~20%
    collect("opening", source.get("opening", ""),
            source.get("opening_speed")
            if source.get("opening_speed") is not None
            else OPENING_CLOSING_DEFAULT_SPEED)
    for i, seg in enumerate(source.get("segments", []), 1):
        title = seg.get("title", f"segment{i}")
        dialogue = seg.get("dialogue")
        text = "\n".join((t.get("text") or "") for t in dialogue) if dialogue else (seg.get("text") or "")
        collect(f"seg{i} ({title})", text, seg.get("speed"))
    collect("closing", source.get("closing", ""),
            source.get("closing_speed")
            if source.get("closing_speed") is not None
            else OPENING_CLOSING_DEFAULT_SPEED)

    if not all_sentences:
        raise ValueError("没有任何可估算的句子（opening/segments/closing 都是空的）")

    durations = [estimate_sentence_seconds(s, args.chars_per_sec, spd)
                 for _label, s, spd in all_sentences]
    total = sum(durations) + max(len(all_sentences) - 1, 0) * args.gap

    print(f"[assumptions] {args.chars_per_sec} 字/秒，默认 {args.speed}x 语速"
          f"（段落自带 speed 时按段落的算），句间停顿 {args.gap}s")
    for label, start, end in block_ranges:
        secs = sum(durations[start:end]) + max(end - start - 1, 0) * args.gap
        print(f"  {label:<28} {end - start:>3} 句  ~{secs:>6.1f}s")
    print(f"\n[estimate] 共 {len(all_sentences)} 句，预计总时长 ~{total:.0f}s"
          f"（约 {total / 60:.1f} 分钟）")
    print("这是启发式估算，跟实际 TTS 时长通常有 ±15% 左右出入，仅供写稿阶段"
          "判断量级是否合理，不替代第 3 步的实测 timing_manifest.json。")


def main():
    setup_stdio()
    parser = argparse.ArgumentParser(description="视频时长预算：写稿前规划 / 写完后核对")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="正向规划：给定目标时长，估算能装多少段内容")
    p_plan.add_argument("--target", type=float, required=True, help="目标总时长（秒）")
    p_plan.add_argument("--sentences-per-segment", type=int, default=3,
                        help="每个内容段落预计几句（精选摘要模式默认 3，完整讲解模式可以调大到 6-8）")
    p_plan.add_argument("--chars-per-sentence", type=float, default=22,
                        help="每句预计字数（默认 22，落在 15-35 字规则的中段）")
    p_plan.add_argument("--opening-sentences", type=int, default=1)
    p_plan.add_argument("--closing-sentences", type=int, default=1)
    p_plan.add_argument("--chars-per-sec", type=float, default=DEFAULT_CHARS_PER_SEC)
    p_plan.add_argument("--speed", type=float, default=DEFAULT_SPEED,
                        help="TTS 倍速（默认与 pipeline.py --speed 一致，单一来源）")
    p_plan.add_argument("--gap", type=float, default=DEFAULT_GAP, help="句间停顿（秒），需与 pipeline.py --gap 一致")
    p_plan.set_defaults(func=cmd_plan)

    p_est = sub.add_parser("estimate", help="反向核对：估算已写好的 segments_source.json 有多长")
    p_est.add_argument("-i", "--input", required=True, help="segments_source.json 路径")
    p_est.add_argument("--chars-per-sec", type=float, default=DEFAULT_CHARS_PER_SEC)
    p_est.add_argument("--speed", type=float, default=DEFAULT_SPEED,
                       help="没有段落级 speed 时用的默认倍速（与 pipeline.py 一致，单一来源）")
    p_est.add_argument("--gap", type=float, default=DEFAULT_GAP, help="句间停顿（秒），需与 pipeline.py --gap 一致")
    p_est.set_defaults(func=cmd_estimate)

    args = parser.parse_args()
    # --chars-per-sec 0 会除零（ZeroDivisionError 不是 ValueError，下面的
    # except 接不住）；在入口拦下
    if getattr(args, "chars_per_sec", 1.0) <= 0:
        parser.error("--chars-per-sec 必须为正数（字/秒）")
    try:
        args.func(args)
    except (ValueError, FileNotFoundError) as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
