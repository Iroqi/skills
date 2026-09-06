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
from _script_utils import (split_subtitle_cues, setup_stdio,  # noqa: E402
                           subtitle_params_for)


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
        # 库调用路径可能拿到未经 load_timing_manifest 校验的 manifest：
        # 缺 start_time 时回退 0.0 而不是 KeyError 裸栈（CLI 路径有契约
        # 校验兜底，这里只为直接 import 本模块的用法兜底）
        first_sent = sentences_by_index.get(first_idx, seg_sentences[0])
        start_time = first_sent.get("start_time", 0.0) if isinstance(
            first_sent, dict) else 0.0
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


def write_srt(manifest, out_path, aspect="landscape", sub_mode="bar"):
    sentences = sorted(manifest.get("sentences", []), key=lambda s: s["index"])
    if not sentences:
        print("[skip] manifest 没有 'sentences'，无法生成字幕", file=sys.stderr)
        return False
    # 与成片字幕共用同一份切分参数（_script_utils.subtitle_params_for）：
    # 以前这里用 split_subtitle_cues 的默认横屏参数，而 gen_hyperframes
    # 竖屏用 22/1.0/22、verse 竖屏 cue_max_lines=99——竖屏/verse 项目导出
    # 的 SRT 与片内字幕切行不一致，本文件"逐条对齐"的承诺会悄悄失效。
    _sub_p = subtitle_params_for(aspect, sub_mode)
    lines = []
    n = 0
    for s in sentences:
        # 与成片字幕同规则：每屏最多两行，超出的行拆成独立 SRT
        # 条目，时长按字符占比切分、相邻条目首尾相接——SRT 与视频内字幕
        # 逐条对齐；说话人前缀只挂首条。
        groups = split_subtitle_cues(
            s["text"], max_chars=_sub_p["max_chars"],
            cue_max_lines=_sub_p["cue_max_lines"], slack=_sub_p["slack"],
            hard_cap=_sub_p["hard_cap"])
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
    setup_stdio()
    parser = argparse.ArgumentParser(description="从 timing_manifest.json 导出章节时间戳 + SRT 字幕")
    parser.add_argument("-m", "--manifest", required=True, help="timing_manifest.json 路径")
    parser.add_argument("-o", "--output", required=True, help="输出目录（写 chapters.txt / captions.srt）")
    parser.add_argument("--only", choices=["chapters", "srt"], default=None,
                        help="只导出其中一个（缺省两个都导出）")
    parser.add_argument("--aspect", default="landscape",
                        choices=["landscape", "vertical", "portrait"],
                        help="成片画幅（默认 landscape）。必须与渲染时用的"
                             " --aspect 一致，否则 SRT 切行与片内字幕对不上"
                             "（竖屏每行 22 字、横屏 28 字）")
    parser.add_argument("--sub-mode", default=None, choices=["verse", "bar"],
                        help="字幕模式（默认按画幅取，与 gen_hyperframes.py "
                             "同规则：横屏 bar、竖屏家族 verse）")
    args = parser.parse_args()

    # 与 gen_hyperframes.py 同规则解析默认 sub_mode（横屏 bar、竖屏 verse）
    _sub_mode = args.sub_mode or ("bar" if args.aspect == "landscape" else "verse")

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
        did_something |= write_srt(
            manifest, os.path.join(args.output, "captions.srt"),
            aspect=args.aspect, sub_mode=_sub_mode)

    if not did_something:
        sys.exit(1)


if __name__ == "__main__":
    main()
