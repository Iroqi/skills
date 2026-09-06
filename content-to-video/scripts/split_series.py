#!/usr/bin/env python3
"""系列视频编排器：长文档 + 目标每条时长 → 自动建议切分点，产出多份
segments_source_N.json 骨架，供后续逐条精修。

背景（见 SKILL.md"信源"一节）：材料太长时要跟用户确认拆成系列。本脚本
省的是"数清楚切几刀、复制出多份骨架"这个机械劳动；"切在哪、每条讲什么"
这种内容判断仍然留给人/agent。

切分原则：**按标题/小节边界切，不做硬字数切**——半句话被腰斩到下一集开头，
比"这一集稍微长/短一点"糟糕得多。算法只在小节边界之间决策"这条该收尾了"，
不会切开任何一个小节内部。

识别小节边界：
  - Markdown 标题行（# ~ ######）
  - 或者没有 Markdown 标题时，退化为按连续两个以上空行分隔的段落块，
    取每块第一行（截断到 30 字）当标题候选

用法：
  python scripts/split_series.py -i notes.md -o out_dir --target-seconds 180
  python scripts/split_series.py -i notes.md -o out_dir --episodes 3
  python scripts/split_series.py -i notes.md -o out_dir --target-seconds 180 --dry-run

产出：out_dir/segments_source_1.json, segments_source_2.json, ...
每份骨架的 segments[].text 是该小节原文（不做摘要——build_from_structured.py
在 pipeline.py --source 时会自动摘要生成画面 body，见 pitfalls.md 第 15 条），
title 取小节标题；opening/closing 留空字符串，交给人工或 agent 按整个系列的
系列名/收尾语再填。切分出多于一集时，每份骨架会带一个 `_series_meta` 字段
（episode_index/total_episodes/prev_episode_title/next_episode_title），
纯粹是给人/agent 精修 opening（比如写"上期我们讲到……"）时参考的事实性信息，
不生成任何自动衔接文案（那需要语义判断），下游脚本也不读这个 key。

本脚本只负责"数清楚切几刀、切在哪"，不判断内容质量、不做任何摘要/改写，
产出的骨架仍需要人工/agent 逐条过一遍再送进 pipeline.py。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import split_sentences, setup_stdio  # noqa: E402
from _contracts import (DEFAULT_SPEED, DEFAULT_CHARS_PER_SEC, DEFAULT_GAP,  # noqa: E402
                        estimate_sentence_seconds)  # 默认倍速/时长估算单一来源

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _split_sections_by_headings(lines):
    """按 Markdown 标题行切分；``` 围栏代码块内的 '#' 行不算标题。

    技术文档里代码块内以 # 开头的行几乎都是注释（shell/python 配置示例），
    不做围栏识别会把注释行当成小节标题、切出垃圾小节。
    """
    sections = []
    cur_title, cur_body = None, []
    in_fence = False

    def _flush():
        nonlocal cur_title, cur_body
        if cur_title is not None or cur_body:
            sections.append((cur_title or "（前言）", "\n".join(cur_body).strip()))
        cur_title, cur_body = None, []

    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            cur_body.append(line)
            continue
        m = None if in_fence else _HEADING_RE.match(line)
        if m:
            _flush()
            cur_title = m.group(2)
        else:
            cur_body.append(line)
    _flush()
    return [(t, b) for t, b in sections if b.strip()]


def parse_sections(text):
    """把文档切成 [(title, body), ...] 小节列表。

    优先按 Markdown 标题（围栏代码块内的 '#' 行不算，见
    _split_sections_by_headings）；文档里一个标题都没有时，
    退化为按空行分块。
    """
    lines = text.splitlines()

    has_headings = False
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and _HEADING_RE.match(line):
            has_headings = True
            break

    if has_headings:
        sections = _split_sections_by_headings(lines)
    else:
        sections = []
        blocks = re.split(r"\n\s*\n+", text.strip())
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            first_line = block.splitlines()[0].strip()
            title = first_line[:30] + ("…" if len(first_line) > 30 else "")
            sections.append((title, block))

    if not sections:
        raise ValueError("文档解析不出任何小节（内容为空，或格式无法识别）")
    return sections


def estimate_section_seconds(body, chars_per_sec, speed, gap):
    sents = split_sentences(body)
    if not sents:
        return 0.0
    durations = [estimate_sentence_seconds(s, chars_per_sec, speed) for s in sents]
    return sum(durations) + max(len(sents) - 1, 0) * gap


def plan_episodes(sections, target_seconds=None, n_episodes=None,
                  chars_per_sec=DEFAULT_CHARS_PER_SEC, speed=DEFAULT_SPEED, gap=DEFAULT_GAP):
    """把小节列表分组成若干集，只在小节边界之间切。

    - target_seconds 模式：贪心地往当前集里加小节，直到加下一节会超过目标时长
      就收尾开新集（跟"这节课比较长，宁可单独一集"这种直觉一致：不会为了凑
      时长硬切小节）。
    - n_episodes 模式：把小节按估算时长尽量均分成 n 集（同样只在边界切）。
    """
    sec_durations = [estimate_section_seconds(body, chars_per_sec, speed, gap)
                     for _title, body in sections]

    if n_episodes:
        total = sum(sec_durations) or 1.0
        target_seconds = total / n_episodes

    episodes = []
    cur, cur_dur = [], 0.0
    for (title, body), dur in zip(sections, sec_durations):
        if cur and (cur_dur + dur) > target_seconds and not (
                n_episodes and len(episodes) == n_episodes - 1):
            # 收尾开新集的守卫：已经切出 n-1 集时，剩余小节全部留在第 n 集
            #（只在边界切的代价是最后一集可能超预算）。因此 len(episodes)
            # 恒 <= n_episodes，不需要"多切出来再合并回去"的兜底分支。
            episodes.append(cur)
            cur, cur_dur = [], 0.0
        cur.append((title, body, dur))
        cur_dur += dur
    if cur:
        episodes.append(cur)

    return episodes


def write_skeleton(episode, out_path, episode_index=None, total_episodes=None,
                   prev_title=None, next_title=None):
    segments = [{"title": title, "text": body} for title, body, _dur in episode]
    skeleton = {"opening": "", "segments": segments, "closing": ""}
    if episode_index is not None:
        # 不自动生成"上期回顾"文案（那需要对内容的语义判断，机械脚本硬写只会
        # 是套话），只把"这是第几集/上一集讲了什么"这种事实性信息透传给
        # 后续精修 opening/closing 的人/agent 做参考——agent 拿到
        # prev_episode_title 之后完全可以自己写"上期我们讲到……"这种衔接句。
        # pipeline.py/build_from_structured.py 不读这个 key，纯粹是给人看的
        # 元数据，不影响任何下游脚本的行为。
        skeleton["_series_meta"] = {
            "episode_index": episode_index,
            "total_episodes": total_episodes,
            "prev_episode_title": prev_title,
            "next_episode_title": next_title,
        }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(skeleton, f, ensure_ascii=False, indent=2)


def main():
    setup_stdio()
    parser = argparse.ArgumentParser(description="长文档 → 系列 segments_source_N.json 骨架")
    parser.add_argument("-i", "--input", required=True, help="长文档路径（纯文本/Markdown）")
    parser.add_argument("-o", "--output", required=True, help="输出目录")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--target-seconds", type=float,
                       help="每集目标时长（秒），贪心切分")
    group.add_argument("--episodes", type=int, help="固定切成几集，按估算时长尽量均分")
    parser.add_argument("--chars-per-sec", type=float, default=DEFAULT_CHARS_PER_SEC)
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED)
    parser.add_argument("--gap", type=float, default=DEFAULT_GAP)
    parser.add_argument("--dry-run", action="store_true", help="只打印切分方案，不写文件")
    args = parser.parse_args()

    # --episodes 0 会在 plan_episodes 里除零（ZeroDivisionError 不是
    # ValueError，main 的 except 接不住）；--target-seconds 非正会退化成
    # 每节一集；--chars-per-sec 0 会在时长估算里除零。都在入口拦下。
    if args.episodes is not None and args.episodes < 1:
        parser.error("--episodes 至少为 1")
    if args.target_seconds is not None and args.target_seconds <= 0:
        parser.error("--target-seconds 必须为正数（秒）")
    if args.chars_per_sec <= 0:
        parser.error("--chars-per-sec 必须为正数（字/秒）")

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"[error] 读取 {args.input} 失败：{e}", file=sys.stderr)
        sys.exit(1)

    try:
        sections = parse_sections(text)
        episodes = plan_episodes(
            sections, target_seconds=args.target_seconds, n_episodes=args.episodes,
            chars_per_sec=args.chars_per_sec, speed=args.speed, gap=args.gap)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[split-series] 识别到 {len(sections)} 个小节，切分为 {len(episodes)} 集：")
    for i, ep in enumerate(episodes, 1):
        ep_dur = sum(d for _t, _b, d in ep)
        titles = "、".join(t for t, _b, _d in ep)
        print(f"  第{i}集  ~{ep_dur:.0f}s  {len(ep)} 节：{titles[:80]}"
              f"{'…' if len(titles) > 80 else ''}")

    if args.dry_run:
        print("\n[dry-run] 未写入任何文件。")
        return

    os.makedirs(args.output, exist_ok=True)
    written = []
    episode_titles = [ep[0][0] for ep in episodes]  # 每集第一节标题，当集名占位
    for i, ep in enumerate(episodes, 1):
        out_path = os.path.join(args.output, f"segments_source_{i}.json")
        write_skeleton(
            ep, out_path,
            episode_index=i if len(episodes) > 1 else None,
            total_episodes=len(episodes),
            prev_title=episode_titles[i - 2] if i > 1 else None,
            next_title=episode_titles[i] if i < len(episodes) else None,
        )
        written.append(out_path)

    print(f"\n[split-series] 已写出 {len(written)} 份骨架 -> {args.output}/")
    print("这些骨架的 text 是小节原文，尚未精修——建议逐份过一遍：调整措辞、"
          "补开场/结尾语、必要时手写更贴合画面的 body 摘要，再送进 pipeline.py。")


if __name__ == "__main__":
    main()
