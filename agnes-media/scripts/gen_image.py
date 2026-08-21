#!/usr/bin/env python3
"""agnes-image-2.1-flash 图像生成 CLI。

用法示例：
  python gen_image.py --prompt "一只戴宇航员头盔的猫" --size 2K --ratio 1:1
  python gen_image.py --prompt "把物体改成橙色，保留构图" --image input.png
  python gen_image.py --prompt "合成两张参考图" --image a.png --image b.png --size 1K

说明：
- 无 --image 为文生图；有 --image 为图生图/多图合成（本地文件自动转 data URI）
- 默认 extra_body.response_format=b64_json（实测可靠；URL 输出域名在部分网络不可达）
- 价格：¥0.02/张
- 成功末行输出 SAVED_PATH=<绝对路径>；失败末行输出 ERROR=<原因>
"""

import argparse
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agnes_common import (  # noqa: E402
    API_BASE,
    AgnesError,
    detect_image_format,
    download_file,
    emit_error,
    emit_saved,
    http_json,
    resolve_api_key,
    resolve_image_input,
    timestamp_prefix,
    unique_path,
    validate_magic,
)

MODEL = "agnes-image-2.1-flash"
RATIOS = ["1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9"]


def main():
    parser = argparse.ArgumentParser(description="agnes-image-2.1-flash 图像生成")
    parser.add_argument("--prompt", required=True, help="图像生成/编辑的文本指令")
    parser.add_argument("--size", default="2K", choices=["1K", "2K", "3K", "4K"],
                        help="尺寸档位（默认 2K）")
    parser.add_argument("--ratio", default="1:1", choices=RATIOS,
                        help="宽高比（默认 1:1）")
    parser.add_argument("--image", action="append", default=[],
                        help="参考图（可重复传入实现多图合成）；支持本地路径/公共 URL/data URI")
    parser.add_argument("--format", default="b64", choices=["b64", "url"],
                        help="返回格式（默认 b64，实测可靠；url 需网络可访问输出域名）")
    parser.add_argument("--filename", default=None, help="输出文件名（默认自动生成）")
    parser.add_argument("--output-dir", default="agnes-output", help="输出目录（默认 ./agnes-output）")
    parser.add_argument("--api-key", default=None, help="API Key（优先于环境变量 AGNES_API_KEY）")
    args = parser.parse_args()

    try:
        api_key = resolve_api_key(args.api_key)

        # 构造请求体：图生图/多图合成的输入图像放 extra_body.image（官方示例用法）
        payload = {
            "model": MODEL,
            "prompt": args.prompt,
            "size": args.size,
            "ratio": args.ratio,
            "extra_body": {"response_format": "b64_json" if args.format == "b64" else "url"},
        }
        if args.image:
            payload["extra_body"]["image"] = [resolve_image_input(p) for p in args.image]

        mode = "文生图" if not args.image else ("图生图" if len(args.image) == 1 else "多图合成")
        print(f"[1/2] {mode}：size={args.size} ratio={args.ratio} format={args.format}")
        resp = http_json("POST", f"{API_BASE}/v1/images/generations", api_key,
                         payload, timeout=360)

        items = resp.get("data")
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            emit_error(f"响应 data 字段异常: {str(resp)[:300]}")
        item = items[0]

        os.makedirs(args.output_dir, exist_ok=True)

        if item.get("b64_json"):
            # 按实际格式决定扩展名（PNG/JPEG/WEBP/GIF/BMP 全支持，防误删有效结果）
            raw = base64.b64decode(item["b64_json"])
            ext, _ = detect_image_format(raw)
            if ext is None:
                emit_error(f"Base64 输出无法识别图片格式（文件头: {raw[:8]!r}）")
            name = args.filename or f"{timestamp_prefix()}{ext}"
            path = unique_path(args.output_dir, name)
            with open(path, "wb") as f:
                f.write(raw)
        elif item.get("url"):
            if args.format == "b64":
                print("  注意：请求了 b64 但返回了 URL，尝试下载（该域名在部分网络不可达）")
            url_ext = os.path.splitext(item["url"].split("?")[0])[1]
            name = args.filename or f"{timestamp_prefix()}{url_ext if url_ext in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp') else '.png'}"
            path = unique_path(args.output_dir, name)
            if not download_file(item["url"], path):
                emit_error("图像 URL 下载失败：建议改用 --format b64")
        else:
            emit_error(f"响应中无 b64_json/url 字段: {str(item)[:300]}")

        ok, reason = validate_magic(path, "image")
        if not ok:
            try:
                os.remove(path)
            except OSError:
                pass
            emit_error(f"输出文件校验失败（{reason}）")

        size_kb = os.path.getsize(path) / 1024
        print(f"[2/2] 已保存：{os.path.abspath(path)}（{size_kb:.0f}KB）")
        emit_saved(path)

    except AgnesError as e:
        emit_error(e)
    except SystemExit:
        raise
    except Exception as e:  # 未预期异常也走统一输出契约
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
