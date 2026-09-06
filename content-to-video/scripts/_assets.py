#!/usr/bin/env python3
"""共享的第三方前端资源本地缓存（目前只有 GSAP）。

Hyperframes 渲染默认从 jsdelivr CDN 加载 GSAP（`<script src="https://cdn...">`）。
这意味着**每一次**渲染，headless Chrome 在开始截帧前都要先完成一次网络请求——
CDN 抖动/超时会直接拖慢甚至搞崩渲染（`references/pitfalls.md` #3：CDN 加载失败
会导致 timeline 注册崩溃、视频黑屏）。反复调试正式渲染时这个网络往返会被
放大很多次。

`ensure_local_gsap()` 把 GSAP 下载到一个跨 run 共享的本地缓存目录
（`~/.cache/content-to-video/vendor/`），首次调用联网下载一次，之后所有渲染
都直接从本地缓存拷贝，
不再依赖网络，也不再受 CDN 抖动影响。下载失败（沙箱/内网无外网）时返回
`None`，调用方应回退到
`GSAP_CDN_URL`——本地化是尽力而为，不是硬性要求。
"""
import os
import shutil
import sys
import tempfile
import threading
import urllib.request

GSAP_VERSION = "3.14.2"
GSAP_CDN_URL = f"https://cdn.jsdelivr.net/npm/gsap@{GSAP_VERSION}/dist/gsap.min.js"

_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "content-to-video", "vendor")
_CACHE_PATH = os.path.join(_CACHE_DIR, f"gsap-{GSAP_VERSION}.min.js")
# 兼容旧缓存路径：skill 改名前的 ~/.cache/ai-daily-video/ 下若已有同版本
# GSAP 缓存，新路径 miss 时先迁移过来，老用户不用重新联网下载一次 GSAP。
_LEGACY_CACHE_PATH = os.path.join(os.path.expanduser("~"), ".cache",
                                  "ai-daily-video", "vendor",
                                  f"gsap-{GSAP_VERSION}.min.js")

# 保护"检查缓存是否存在 -> 不存在则下载"这段逻辑，避免多个线程同时判断
# 缓存缺失、同时发起下载并同时写同一个缓存文件。
_download_lock = threading.Lock()


def _download_to_cache(cache_dir, cache_path, timeout=15):
    """下载 GSAP 到共享缓存。原子写入（临时文件 + rename）避免半下载的文件
    被其他线程/进程当成有效缓存用。缓存路径由调用方传入（默认为模块常量，
    测试注入临时路径用参数、不改写模块全局）。
    """
    os.makedirs(cache_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
    fd_opened = True  # mkstemp 返回的 fd 需要显式关闭/接管，跟踪它的归属
    try:
        req = urllib.request.Request(GSAP_CDN_URL, headers={"User-Agent": "content-to-video/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 1000:
            raise ValueError(f"downloaded file too small ({len(data)} bytes), likely not real GSAP")
        with os.fdopen(fd, "wb") as f:
            fd_opened = False  # 所有权已移交给 fdopen 的 with 块
            f.write(data)
        os.replace(tmp_path, cache_path)
        return True
    except Exception as e:
        print(f"[warn] GSAP 本地缓存下载失败，本次渲染回退到 CDN 直连：{e}",
              file=sys.stderr)
        if fd_opened:
            # 下载在 os.fdopen 之前就失败：fd 还开着。Windows 不允许删除
            # 被占用句柄的文件，不关掉的话 os.remove 必然失败、.tmp 永久残留。
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return False


def ensure_local_gsap(html_output_dir, timeout=15,
                      cache_dir=None, cache_path=None, legacy_cache_path=None):
    """确保 `<html_output_dir>/vendor/gsap.min.js` 存在（HTML 用相对路径
    `vendor/gsap.min.js` 加载它，Hyperframes CLI 渲染时是本地文件读取，
    不再走网络）。

    缓存路径参数（默认取模块常量）：测试需要隔离缓存时用参数注入，
    不改写模块全局——全局变量从此只读。

    返回值：
      - 成功：返回相对路径字符串 `"vendor/gsap.min.js"`，直接用作
        `<script src=...>` 的值。
      - 失败（无法下载且本地也没有缓存）：返回 `None`，调用方应回退到
        `GSAP_CDN_URL`。
    """
    cache_dir = cache_dir or _CACHE_DIR
    cache_path = cache_path or _CACHE_PATH
    legacy_cache_path = legacy_cache_path or _LEGACY_CACHE_PATH
    dest_dir = os.path.join(html_output_dir, "vendor")
    dest_path = os.path.join(dest_dir, "gsap.min.js")

    # 这个输出目录之前已经准备过，直接复用，不用重新拷贝。
    if os.path.isfile(dest_path) and os.path.getsize(dest_path) > 1000:
        return "vendor/gsap.min.js"

    with _download_lock:
        if not (os.path.isfile(cache_path) and os.path.getsize(cache_path) > 1000):
            # 新缓存 miss：先把旧目录（ai-daily-video 时代）的缓存搬过来，
            # 搬不动再走联网下载。
            try:
                if (os.path.isfile(legacy_cache_path)
                        and os.path.getsize(legacy_cache_path) > 1000):
                    os.makedirs(cache_dir, exist_ok=True)
                    shutil.copyfile(legacy_cache_path, cache_path)
            except OSError:
                pass  # 迁移失败无所谓，下面还有正常的下载路径
        if not (os.path.isfile(cache_path) and os.path.getsize(cache_path) > 1000):
            if not _download_to_cache(cache_dir, cache_path, timeout=timeout):
                return None

    os.makedirs(dest_dir, exist_ok=True)
    try:
        shutil.copyfile(cache_path, dest_path)
    except OSError as e:
        print(f"[warn] 无法拷贝 GSAP 到 {dest_path}: {e}", file=sys.stderr)
        return None
    return "vendor/gsap.min.js"
