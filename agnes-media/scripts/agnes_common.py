#!/usr/bin/env python3
"""agnes-media 技能包共享库。

纯 Python 标准库实现（无第三方依赖），供 gen_image.py / gen_video.py 复用：
- API Key 解析（环境变量 AGNES_API_KEY 或 CLI 参数，绝不落盘）
- HTTP JSON 请求（429/503 退避重试 2s/4s；GET 网络错误重试 5s/10s；
  POST 网络错误不自动重试，避免重复计费）
- 文件下载（分块写入 + Content-Length 比对 + 魔数校验 + 失败清理）
- 本地图片转 Data URI（按文件头魔数判定 MIME，兼容扩展名误标）
- 输出文件路径去重（冲突时循环追加后缀，绝不覆盖已有文件）
- SAVED_PATH / ERROR 输出契约（编码安全，任何终端环境下可履行）

输出契约（agent 依赖此格式解析结果，勿改动）：
- 成功：最后一行输出 `SAVED_PATH=<文件绝对路径>`，退出码 0
- 失败：最后一行输出 `ERROR=<原因摘要，截断至 500 字符>`，退出码 1
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

API_BASE = "https://api.agnes-ai.cn"
MAX_RETRIES = 3


class AgnesError(Exception):
    """skill 内统一错误类型，message 会进入 ERROR= 输出。"""


def _stdout_write(text):
    """编码安全地写 stdout：绕过 Windows cp936 等终端编码限制，保证契约行可输出。"""
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except UnicodeEncodeError:
        sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()


def resolve_api_key(cli_key=None):
    """解析 API Key：CLI 参数优先，其次环境变量 AGNES_API_KEY。两端均做 strip。"""
    key = (cli_key or os.environ.get("AGNES_API_KEY", "")).strip()
    if not key:
        raise AgnesError(
            "未提供 API Key：请设置环境变量 AGNES_API_KEY 或传参 --api-key"
        )
    return key


def http_json(method, url, api_key, payload=None, timeout=120):
    """发起 JSON HTTP 请求并返回解析后的 dict。

    重试策略：
    - 429/503：指数退避 2s/4s（最多重试 2 次），GET/POST 均重试（服务端已拒绝请求，未计费）
    - 网络错误（URLError/OSError）：仅 GET 自动重试（5s/10s）。
      POST 为非幂等操作——首次请求可能已达服务端并计费，自动重试会导致重复扣费，
      因此 POST 网络错误直接抛 AgnesError 并提示人工确认。
    - 其余 HTTP 错误：不重试，抛 AgnesError 携带状态码与响应体摘要。
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "agnes-media-skill/1.0",
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                if not body.strip():
                    raise AgnesError(f"HTTP {method} 响应体为空: {url}")
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError as e:
                    raise AgnesError(f"响应非合法 JSON（{e}），前 200 字符: {body[:200]}")
                if not isinstance(parsed, dict):
                    raise AgnesError(f"响应不是 JSON 对象: {str(parsed)[:200]}")
                return parsed
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            if e.code in (429, 503) and attempt < MAX_RETRIES:
                wait = 2 ** attempt
                print(f"  HTTP {e.code}，{wait}s 后重试（第 {attempt} 次重试，共 {MAX_RETRIES - 1} 次）...")
                time.sleep(wait)
                continue
            raise AgnesError(f"HTTP {e.code} {e.reason}: {body}")
        except AgnesError:
            raise
        except (urllib.error.URLError, OSError) as e:
            last_err = e
            if method.upper() == "GET" and attempt < MAX_RETRIES:
                wait = 5 * attempt
                print(f"  网络错误（{e}），{wait}s 后重试（第 {attempt} 次重试）...")
                time.sleep(wait)
                continue
            if method.upper() == "POST":
                raise AgnesError(
                    f"网络错误，POST 请求不自动重试（首次请求可能已提交并计费，"
                    f"请先到控制台确认后再手动重试）: {e}"
                )
            raise AgnesError(f"网络错误（已重试 {MAX_RETRIES - 1} 次）: {e}")
    raise AgnesError(f"请求失败: {last_err}")  # 防御性兜底，正常不可达


def detect_image_format(raw):
    """按文件头魔数检测图片格式。返回 (ext, mime)；无法识别返回 (None, None)。

    支持 PNG / JPEG / WEBP / GIF / BMP。
    """
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return ".gif", "image/gif"
    if raw.startswith(b"BM"):
        return ".bmp", "image/bmp"
    return None, None


def file_to_data_uri(image_path):
    """本地图片文件 → data URI（Base64）。超过 20MB 拒绝（413 风险）。

    MIME 按文件头魔数判定（扩展名仅作兜底提示），扩展名误标不影响请求正确性。
    """
    if not os.path.isfile(image_path):
        raise AgnesError(f"图片文件不存在: {image_path}")
    size = os.path.getsize(image_path)
    if size > 20 * 1024 * 1024:
        raise AgnesError(f"图片过大（{size / 1048576:.1f}MB，上限 20MB），请压缩后重试")
    with open(image_path, "rb") as f:
        raw = f.read()
    ext, mime = detect_image_format(raw)
    if mime is None:
        ext_name = os.path.splitext(image_path)[1].lower() or "无扩展名"
        raise AgnesError(
            f"无法识别图片格式（扩展名 {ext_name}，文件头非 PNG/JPEG/WEBP/GIF/BMP）: {image_path}"
        )
    b64 = base64.b64encode(raw).decode("ascii")
    print(f"  已编码本地图片 {os.path.basename(image_path)}（{size / 1024:.0f}KB，{mime}）")
    return f"data:{mime};base64,{b64}"


def resolve_image_input(image_arg):
    """解析 --image 参数：http(s) URL 与 data: URI 直通，其余按本地文件转 data URI。"""
    if image_arg.startswith(("http://", "https://", "data:")):
        return image_arg
    return file_to_data_uri(image_arg)


def validate_magic(file_path, kind):
    """魔数校验：kind='image' 校验 PNG/JPEG/WEBP/GIF/BMP 头，kind='video' 校验 MP4 ftyp。"""
    if not os.path.isfile(file_path):
        return False, "输出文件不存在（可能被并发删除）"
    if os.path.getsize(file_path) < 1024:
        return False, "文件小于 1KB，疑似损坏"
    with open(file_path, "rb") as f:
        head = f.read(16)
    if kind == "image":
        ext, _ = detect_image_format(head)
        if ext:
            return True, ""
        return False, f"非 PNG/JPEG/WEBP/GIF/BMP 文件头: {head[:8]!r}"
    if kind == "video":
        if b"ftyp" in head[4:12]:
            return True, ""
        return False, f"非 MP4 文件头: {head[:12]!r}"
    return False, f"未知校验类型: {kind}"


def _remove_quiet(path):
    """尽力删除文件，失败不影响主流程（Windows 下可能被杀毒软件占用）。"""
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def download_file(url, output_path, timeout=300):
    """分块下载文件到 output_path。

    - 失败自动重试（5s/10s 退避，GET 下载可安全重试）
    - 响应头含 Content-Length 时，下载后比对字节数，不符视为截断
    - 所有尝试失败后清理残留的部分写入文件
    成功返回 True。
    """
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "agnes-media-skill/1.0"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                expected = resp.headers.get("Content-Length")
                expected = int(expected) if expected and expected.isdigit() else None
                with open(output_path, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
            actual = os.path.getsize(output_path)
            if expected is not None and actual != expected:
                raise IOError(f"下载不完整：收到 {actual} 字节，Content-Length 为 {expected}")
            return True
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES:
                wait = 5 * attempt
                print(f"  下载失败（{e}），{wait}s 后重试（第 {attempt} 次重试）...")
                time.sleep(wait)
            else:
                print(f"  下载失败: {e}", file=sys.stderr)
    _remove_quiet(output_path)  # 清理残留的部分写入文件
    return False


def unique_path(directory, filename):
    """拼接输出路径；冲突时循环追加后缀，保证绝不覆盖已有文件。"""
    path = os.path.join(directory, filename)
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(filename)
    stamp = datetime.now().strftime("%H%M%S")
    candidate = os.path.join(directory, f"{stem}-{stamp}{ext}")
    seq = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{stem}-{stamp}-{seq}{ext}")
        seq += 1
    return candidate


def timestamp_prefix():
    """默认输出文件名前缀：agnes-YYYYMMDD-HHMMSS。"""
    return datetime.now().strftime("agnes-%Y%m%d-%H%M%S")


def emit_saved(path):
    """输出契约：成功。必须是 stdout 最后一行。"""
    _stdout_write(f"SAVED_PATH={os.path.abspath(path)}")


def emit_error(message):
    """输出契约：失败。必须是 stdout 最后一行，并返回退出码 1。"""
    _stdout_write(f"ERROR={str(message)[:500]}")
    sys.exit(1)
