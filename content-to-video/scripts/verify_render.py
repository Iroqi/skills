#!/usr/bin/env python3
"""Content-to-Video — 渲染产物校验（用 ffmpeg 解析，不需要 ffprobe）。

render_watch.py 只证明"文件写完了"，本脚本补上"写对了"的一环：
- 文件存在且非空
- 时长与 manifest 的 total_duration 匹配（默认容差 1.0s 或 3%，取较大者）
- 视频流为 H.264（h264/avc1）
- 音频流为 AAC

与 pipeline 共用 _ffmpeg.get_ffmpeg（优先系统 ffmpeg，没有则 imageio-ffmpeg）。
imageio-ffmpeg 不含 ffprobe，因此这里解析 `ffmpeg -i` 的 stderr 而不是 ffprobe。

Usage:
  python scripts/verify_render.py -f out/video.mp4 -m audio_output/timing_manifest.json

退出码：全部通过为 0，任一检查失败为 1。
"""
import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ffmpeg import get_ffmpeg, parse_duration  # noqa: E402
from _contracts import load_timing_manifest  # noqa: E402


STREAM_RE = re.compile(r"Stream\s+#\d+:\d+.*?(?:Video|Audio):\s*([a-z0-9_]+)", re.IGNORECASE)
# 分辨率/帧率：只匹配 Video 行；SIZE_RE 限 2-5 位数字，跳过
# "(avc1 / 0x31637661)" 这类 codec tag 里的 0x 十六进制
SIZE_RE = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})")
FPS_RE = re.compile(r"Video:.*?(\d+(?:\.\d+)?)\s+fps")


def parse_ffmpeg_info(stderr_text):
    """从 `ffmpeg -i` 的 stderr 提取 (duration_sec, video_codec, audio_codec)。

    Returns:
        tuple: (duration or None, video codec or None, audio codec or None)
    """
    duration = parse_duration(stderr_text)

    video = None
    audio = None
    for line in stderr_text.splitlines():
        m = STREAM_RE.search(line)
        if not m:
            continue
        if "Video:" in line and video is None:
            video = m.group(1).lower()
        elif "Audio:" in line and audio is None:
            audio = m.group(1).lower()
    return duration, video, audio


def parse_video_size_fps(stderr_text):
    """从 `ffmpeg -i` 的 stderr 提取视频流的 (width, height, fps)。

    只扫含 "Video:" 的行（避免音频流行误匹配）。任一项未解析到时
    对应元素为 None。

    Returns:
        tuple: (width or None, height or None, fps or None)
    """
    width = height = fps = None
    for line in stderr_text.splitlines():
        if "Video:" not in line:
            continue
        if width is None:
            m = SIZE_RE.search(line)
            if m:
                width, height = int(m.group(1)), int(m.group(2))
        if fps is None:
            m = FPS_RE.search(line)
            if m:
                fps = float(m.group(1))
        if width is not None and fps is not None:
            break
    return width, height, fps


def main():
    parser = argparse.ArgumentParser(
        description="校验渲染产物：时长与 manifest 匹配、H.264 视频 + AAC 音频")
    parser.add_argument("-f", "--file", required=True, help="渲染输出的 MP4 路径")
    parser.add_argument("-m", "--manifest", default=None,
                        help="timing_manifest.json 路径（校验 total_duration 用）")
    parser.add_argument("--tolerance", type=float, default=1.0,
                        help="时长容差秒数（默认 1.0；另取总时长的百分之三，取较大者）")
    parser.add_argument("--expect-size", default=None, metavar="WxH",
                        help="期望分辨率（形如 1920x1080）；给出且与实际不符则 FAIL")
    parser.add_argument("--expect-fps", type=int, default=None,
                        help="期望帧率（如 24/30/60）；给出且与实际不符则 FAIL")
    args = parser.parse_args()

    if args.expect_size is not None:
        m = re.fullmatch(r"(\d+)[xX](\d+)", args.expect_size.strip())
        if not m:
            parser.error("--expect-size 格式应为 宽x高，如 1920x1080")
        args.expect_size = (int(m.group(1)), int(m.group(2)))

    failed = False

    def status(ok, msg):
        nonlocal failed
        failed = failed or not ok
        print(f"  {'[OK]' if ok else '[FAIL]'} {msg}", flush=True)

    print("=== Render verification ===", flush=True)

    # 1. 文件存在且非空
    if not os.path.isfile(args.file):
        print(f"  [FAIL] 输出文件不存在: {args.file}", file=sys.stderr)
        sys.exit(1)
    size = os.path.getsize(args.file)
    status(size > 0, f"输出文件非空（{size} bytes）")

    # 2. ffmpeg 解析流信息
    ff = get_ffmpeg()
    try:
        r = subprocess.run([ff, "-i", args.file],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
    except Exception as e:
        print(f"  [FAIL] ffmpeg 无法读取文件: {e}", file=sys.stderr)
        sys.exit(1)
    stderr_text = r.stderr or ""
    duration, video_codec, audio_codec = parse_ffmpeg_info(stderr_text)

    status(duration is not None,
           f"解析到时长: {duration:.2f}s" if duration is not None else "未解析到时长")

    # 3. 时长与 manifest 匹配
    if args.manifest and os.path.isfile(args.manifest):
        try:
            manifest = load_timing_manifest(args.manifest)
        except ValueError as e:
            print(f"  [FAIL] manifest 校验失败: {e}", file=sys.stderr)
            sys.exit(1)
        expected = manifest.get("total_duration")
        if expected is None:
            status(True, "manifest 无 total_duration，跳过时长比对")
        elif duration is None:
            status(False, "无法比对时长（ffmpeg 未解析到）")
        else:
            tol = max(args.tolerance, expected * 0.03)
            diff = abs(duration - expected)
            status(diff <= tol,
                   f"时长 {duration:.2f}s ≈ manifest {expected:.2f}s（容差 {tol:.2f}s，"
                   f"偏差 {diff:.2f}s）")
    else:
        status(True, "未传 --manifest，跳过时长比对")

    # 4. 编码检查
    status(video_codec is not None and video_codec.startswith(("h264", "avc1")),
           f"视频流 H.264（实际: {video_codec or '未识别'}）")
    status(audio_codec == "aac",
           f"音频流 AAC（实际: {audio_codec or '未识别'}）")

    # 5. 画幅/帧率检查（可选）：--expect-size / --expect-fps 给出才检查。
    #    （画幅渲错如竖屏渲成横屏时，其余检查仍会全绿，画幅必须显式断言。）
    v_width, v_height, v_fps = parse_video_size_fps(stderr_text)
    if args.expect_size is not None:
        ew, eh = args.expect_size
        actual_size = (f"{v_width}x{v_height}"
                       if v_width is not None else "未解析")
        status(v_width == ew and v_height == eh,
               f"分辨率 {actual_size}（期望 {ew}x{eh}）")
    if args.expect_fps is not None:
        status(v_fps is not None and abs(v_fps - args.expect_fps) <= 0.01,
               f"帧率 {v_fps if v_fps is not None else '未解析'}"
               f"（期望 {args.expect_fps}）")

    if failed:
        print("\n[FAIL] 渲染产物未通过校验，详见上方 [FAIL] 项。", file=sys.stderr)
        sys.exit(1)
    print("\n[OK] 渲染产物通过校验：H.264 + AAC，时长与 manifest 一致。")
    sys.exit(0)


if __name__ == "__main__":
    main()
