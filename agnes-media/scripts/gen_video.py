#!/usr/bin/env python3
"""agnes-video-v2.0 视频生成 CLI（创建 → 轮询 → 下载一条龙）。

用法示例：
  python gen_video.py --prompt "海滩上漫步的猫，电影感" --duration 3
  python gen_video.py --prompt "让人物缓缓转头看向镜头" --image portrait.png
  python gen_video.py --prompt "两帧之间平滑过渡" --image k1.png --image k2.png

说明：
- --image 传 1 张 = 图生视频（顶层 image）；传 ≥2 张 = 关键帧动画（extra_body.image + mode=keyframes）
- --duration 自动映射合规帧数（8n+1 规则已内置）；--num-frames 可精确覆盖
- 价格：¥0.035/秒（3 秒 ≈ ¥0.10）
- 成功末行输出 SAVED_PATH=<绝对路径>；失败末行输出 ERROR=<原因>

未覆盖的官方参数（需要时按规格文件中的官方模板直连 API）：
- num_inference_steps（推理步数）、mode: ti2vid（附加生成模式）
"""

import argparse
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agnes_common import (  # noqa: E402
    API_BASE,
    AgnesError,
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

MODEL = "agnes-video-v2.0"

# 目标时长（秒）→ (num_frames, frame_rate)，均满足 8n+1 且 ≤441
DURATION_MAP = {3: (81, 24), 5: (121, 24), 10: (241, 24), 18: (441, 24)}

# 宽高比 → 请求尺寸（服务端会标准化到 480p/720p/1080p 最近档位，
# 并将宽高调整为 64 的倍数，如 1280x720 → 1280x704；以响应 size_mapping 为准）
RATIO_SIZE_MAP = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1": (960, 960),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
}


def extract_video_url(status):
    """从 completed 响应提取视频 URL。

    实测（2026-08-16）URL 在顶层 `url` 字段；官方文档记载为 `metadata.url`。
    兼容链：顶层常见字段 → metadata.url。
    """
    for field in ("url", "video_url", "output_url", "result_url", "download_url"):
        v = status.get(field)
        if isinstance(v, str) and v.startswith("http"):
            return v
    metadata = status.get("metadata") or {}
    v = metadata.get("url")
    if isinstance(v, str) and v.startswith("http"):
        return v
    return None


def main():
    parser = argparse.ArgumentParser(description="agnes-video-v2.0 视频生成")
    parser.add_argument("--prompt", required=True, help="视频内容的文本描述")
    parser.add_argument("--duration", type=int, default=5, choices=sorted(DURATION_MAP),
                        help="目标时长秒数（默认 5）")
    parser.add_argument("--num-frames", type=int, default=None,
                        help="精确帧数覆盖 --duration（须满足 8n+1 且 ≤441）")
    parser.add_argument("--fps", type=float, default=None, help="帧率 1-60（默认随时长档位 24）")
    parser.add_argument("--ratio", default="16:9", choices=sorted(RATIO_SIZE_MAP),
                        help="宽高比（默认 16:9，支持 16:9/9:16/1:1/4:3/3:4；实际输出以服务端标准化为准）")
    parser.add_argument("--width", type=int, default=None, help="精确宽度（覆盖 --ratio）")
    parser.add_argument("--height", type=int, default=None, help="精确高度（覆盖 --ratio）")
    parser.add_argument("--image", action="append", default=[],
                        help="输入图片（可重复）：1 张=图生视频，≥2 张=关键帧动画；"
                             "支持本地路径/公共 URL/data URI")
    parser.add_argument("--seed", type=int, default=None, help="随机种子（可复现结果）")
    parser.add_argument("--negative-prompt", default=None, help="反向提示词")
    parser.add_argument("--poll-interval", type=int, default=10, help="轮询间隔秒（默认 10，最小 1）")
    parser.add_argument("--timeout", type=int, default=900, help="轮询总超时秒（默认 900）")
    parser.add_argument("--filename", default=None, help="输出文件名（默认自动生成 .mp4）")
    parser.add_argument("--output-dir", default="agnes-output", help="输出目录（默认 ./agnes-output）")
    parser.add_argument("--api-key", default=None, help="API Key（优先于环境变量 AGNES_API_KEY）")
    args = parser.parse_args()

    try:
        api_key = resolve_api_key(args.api_key)

        if args.poll_interval < 1:
            emit_error(f"--poll-interval 须 ≥1 秒（当前 {args.poll_interval}），过小会触发 API 限流")

        # 帧数与帧率
        if args.num_frames is not None:
            if args.num_frames > 441 or (args.num_frames - 1) % 8 != 0:
                emit_error(f"num_frames 须满足 8n+1 且 ≤441（如 81/121/241/441），当前: {args.num_frames}")
            num_frames = args.num_frames
        else:
            num_frames = DURATION_MAP[args.duration][0]
        fps = args.fps if args.fps is not None else 24
        if not 1 <= fps <= 60:
            emit_error(f"帧率须在 1-60 范围内，当前: {fps}")

        # 尺寸：默认按 ratio 映射，显式 width/height 优先
        width, height = RATIO_SIZE_MAP[args.ratio]
        if args.width is not None:
            width = args.width
        if args.height is not None:
            height = args.height

        # 构造请求体。多图 = 关键帧模式（extra_body.image + mode），单图 = 图生视频（顶层 image）
        payload = {
            "model": MODEL,
            "prompt": args.prompt,
            "num_frames": num_frames,
            "frame_rate": fps,
            "width": width,
            "height": height,
        }
        images = [resolve_image_input(p) for p in args.image]
        if len(images) == 1:
            payload["image"] = images[0]
            mode = "图生视频"
        elif len(images) >= 2:
            payload["extra_body"] = {"image": images, "mode": "keyframes"}
            mode = f"关键帧动画（{len(images)} 帧）"
        else:
            mode = "文生视频"
        if args.seed is not None:
            payload["seed"] = args.seed
        if args.negative_prompt:
            payload["negative_prompt"] = args.negative_prompt

        est = num_frames / fps
        print(f"[1/3] 创建{mode}任务：请求 {width}x{height}，{num_frames} 帧 @ {fps}fps ≈ {est:.1f}s（≈¥{est * 0.035:.2f}）")
        task = http_json("POST", f"{API_BASE}/v1/videos", api_key, payload, timeout=120)

        video_id = task.get("video_id") or task.get("task_id") or task.get("id")
        if not video_id:
            emit_error(f"创建响应缺少 video_id/task_id: {str(task)[:300]}")
        print(f"  任务已创建：video_id={str(video_id)[:40]}... status={task.get('status')}")

        # 轮询直到 completed/failed 或超时（video_id 可能是长 base64 串，必须 URL 编码）
        print(f"[2/3] 轮询结果（每 {args.poll_interval}s 一次，超时 {args.timeout}s；3 秒视频实测约 60s 完成）")
        deadline = time.time() + args.timeout
        result = None
        while time.time() < deadline:
            # sleep 受 deadline 约束，避免最后一次等待超出 --timeout
            time.sleep(min(args.poll_interval, max(0.5, deadline - time.time())))
            query = f"{API_BASE}/agnesapi?" + urllib.parse.urlencode({"video_id": video_id})
            result = http_json("GET", query, api_key, timeout=60)
            status = result.get("status", "unknown")
            print(f"  [{status}] progress: {result.get('progress', 0)}%")
            if status in ("completed", "failed"):
                break
        if not isinstance(result, dict) or result.get("status") not in ("completed", "failed"):
            last = result.get("status") if isinstance(result, dict) else "无响应"
            emit_error(f"轮询超时（{args.timeout}s），最后状态: {last}；"
                       f"可稍后用 video_id={video_id} 重查")

        if result.get("status") == "failed":
            emit_error(f"任务失败: {str(result.get('error'))[:300]}")

        url = extract_video_url(result)
        if not url:
            emit_error(f"completed 但未找到视频 URL，响应字段: {list(result.keys())}")

        # 下载并校验
        os.makedirs(args.output_dir, exist_ok=True)
        name = args.filename or f"{timestamp_prefix()}.mp4"
        path = unique_path(args.output_dir, name)
        print(f"[3/3] 下载视频：{url[:100]}...")
        if not download_file(url, path):
            emit_error("视频下载失败（网络问题），可稍后手动下载上述 URL")
        ok, reason = validate_magic(path, "video")
        if not ok:
            try:
                os.remove(path)
            except OSError:
                pass
            emit_error(f"输出文件校验失败（{reason}）")

        mapping = result.get("size_mapping") or (result.get("metadata") or {}).get("size_mapping") or {}
        actual = result.get("size") or f"{mapping.get('width', '?')}x{mapping.get('height', '?')}"
        size_kb = os.path.getsize(path) / 1024
        print(f"  实际输出：{actual}，时长 {result.get('seconds', '?')}s，{size_kb:.0f}KB")
        emit_saved(path)

    except AgnesError as e:
        emit_error(e)
    except SystemExit:
        raise
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
