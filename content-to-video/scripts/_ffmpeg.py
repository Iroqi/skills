#!/usr/bin/env python3
"""Shared ffmpeg binary resolver.

Single source of truth for locating the ffmpeg executable.
All scripts that need ffmpeg should import from here:

    from _ffmpeg import get_ffmpeg

解析优先级（先检测系统是否自带，避免重复安装）：
  1. 系统 PATH 上能正常运行的 ffmpeg（检测到就直接用，无需 imageio-ffmpeg）
  2. imageio-ffmpeg 自带的完整版二进制（仅当系统没有可用 ffmpeg 时才需要）
  3. 兜底字符串 "ffmpeg"（交给 subprocess 自己去 PATH 里找）
"""
import os
import re
import shutil
import subprocess


def _system_ffmpeg():
    """检测系统 PATH 上是否有能正常运行的 ffmpeg，返回路径或 None。"""
    path = shutil.which("ffmpeg")
    if not path:
        return None
    try:
        r = subprocess.run([path, "-version"], capture_output=True, timeout=10)
        if r.returncode == 0 and r.stdout:
            return path
    except Exception:
        pass
    return None


def get_ffmpeg():
    """Get ffmpeg executable path.

    优先用系统自带且能运行的 ffmpeg（多数 Windows 机器已通过 winget/官网安装），
    没有才回退到 imageio-ffmpeg 打包的完整版二进制。
    """
    sys_ff = _system_ffmpeg()
    if sys_ff:
        return sys_ff
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        return get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def parse_duration(stderr_text):
    """从 `ffmpeg -i` 的 stderr 解析 `Duration: HH:MM:SS.xx`，返回秒数。

    三处调用方（_audio.measure_duration / verify_render.parse_ffmpeg_info /
    gen_hyperframes 的视频探测）原先各自维护同一份正则，收口到这里做
    单一来源。解析失败返回 None。
    """
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr_text or "")
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
