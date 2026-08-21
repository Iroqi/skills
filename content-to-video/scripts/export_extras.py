#!/usr/bin/env python3
"""从 timing_manifest.json 导出两个"顺手"的附加产出物（渲染视频不需要它们，
纯粹是数据都已经在 manifest 里了，白捡）：

  chapters.txt   B 站/YouTube 简介常用的章节时间戳列表（00:00 开场 / 00:32 xxx）
  captions.srt   独立字幕文件，可以传到别的平台自动配字幕、或做二次剪辑参考

Usage:
  python export_extras.py -m hf-project/timing_manifest.json -o hf-project
  python export_extras.py -m audio_output/timing_manifest.json -o hf-project --only chapters
  python export_extras.py -m audio_output/timing_manifest.json -o hf-project --only srt
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _contracts import load_timing_manifest  # noqa: E402
from _script_utils import split_subtitle_cues  # noqa: E402


def _fmt_chapter_ts(seconds):
    """章节时间戳格式：< 1 小时用 mm:ss，否则 hh:mm:ss（B 站/YouTube 通用写法）。"""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_srt_ts(seconds):
    """SRT 时间戳格式：HH:MM:SS,mmm。"""
    seconds = max(0, seconds)
    total_ms = round(seconds * 1000)
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# id 前缀 -> 章节列表里的默认展示名（没有 title 或 title 为空时兜底用）
_ID_LABEL_FALLBACK = {"opening": "开场", "closing": "结尾"}


def build_chapters(manifest):
    """从 manifest['segments'] 生成 [(start_seconds, label), ...]；
    没有 segments 字段（手写 manifest 才会发生）时返回空列表——章节这个
    概念本来就依赖"段落"，没有分段信息就无法生成有意义的章节。
    """
    segments = manifest.get("segments")
    if not segments:
        return []
    sentences_by_index = {s["index"]: s for s in manifest.get("sentences", [])}
    chapters = []
    for seg in segments:
        seg_sentences = seg.get("sentences") or []
        if not seg_sentences:
            continue
        first_idx = seg_sentences[0]["index"]
        start_time = sentences_by_index.get(first_idx, seg_sentences[0])["start_time"]
        label = seg.get("title") or _ID_LABEL_FALLBACK.get(seg.get("id", ""), seg.get("id", ""))
        chapters.append((start_time, label))
    return chapters


def write_chapters(manifest, out_path):
    chapters = build_chapters(manifest)
    if not chapters:
        print("[skip] manifest 没有 'segments' 字段（没有分段信息），"
              "章节列表无法生成", file=sys.stderr)
        return False
    lines = [f"{_fmt_chapter_ts(t)} {label}" for t, label in chapters]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[ok] {len(chapters)} 章 -> {out_path}", file=sys.stderr)
    return True


def write_srt(manifest, out_path):
    sentences = sorted(manifest.get("sentences", []), key=lambda s: s["index"])
    if not sentences:
        print("[skip] manifest 没有 'sentences'，无法生成字幕", file=sys.stderr)
        return False
    lines = []
    n = 0
    for s in sentences:
        # 与成片字幕同规则：每屏最多两行，超出的行拆成独立 SRT
        # 条目，时长按字符占比切分、相邻条目首尾相接——SRT 与视频内字幕
        # 逐条对齐；说话人前缀只挂首条。
        groups = split_subtitle_cues(s["text"])
        total_chars = sum(len("".join(g)) for g in groups)
        t = s["start_time"]
        for gi, g in enumerate(groups):
            frac = (sum(len(l) for l in g) / total_chars) if total_chars else 1.0
            d = s["duration"] * frac
            speaker_prefix = f"[{s['speaker']}] " if (s.get("speaker") and gi == 0) else ""
            n += 1
            lines.append(str(n))
            lines.append(f"{_fmt_srt_ts(t)} --> {_fmt_srt_ts(t + d)}")
            lines.append(speaker_prefix + "\n".join(g))
            lines.append("")
            t += d
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[ok] {n} 条字幕（长句已按每屏两行拆条） -> {out_path}", file=sys.stderr)
    return True


def main():
    parser = argparse.ArgumentParser(description="从 timing_manifest.json 导出章节时间戳 + SRT 字幕")
    parser.add_argument("-m", "--manifest", required=True, help="timing_manifest.json 路径")
    parser.add_argument("-o", "--output", required=True, help="输出目录（写 chapters.txt / captions.srt）")
    parser.add_argument("--only", choices=["chapters", "srt"], default=None,
                        help="只导出其中一个（缺省两个都导出）")
    args = parser.parse_args()

    try:
        manifest = load_timing_manifest(args.manifest)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output, exist_ok=True)
    did_something = False
    if args.only in (None, "chapters"):
        did_something |= write_chapters(manifest, os.path.join(args.output, "chapters.txt"))
    if args.only in (None, "srt"):
        did_something |= write_srt(manifest, os.path.join(args.output, "captions.srt"))

    if not did_something:
        sys.exit(1)


if __name__ == "__main__":
    main()
