# Agnes Video V2.0 视频生成规格

面向生产场景的异步视频生成 API，支持文生视频、图生视频和关键帧动画。采用异步任务模式：先创建任务，再通过 `video_id` 或 `task_id` 轮询获取结果。

> 本文件为 `agnes-media` 技能包的视频模型规格。认证、通用错误码、输出下载问题见主入口 `SKILL.md`。

**官方文档**：`https://wiki.agnes-ai.cn/en/docs/agnes-video-v20.md`

## 触发场景

- 文生视频：通过文本提示词直接生成视频
- 图生视频：将静态图片（肖像、产品、角色、场景）转化为动态视频
- 关键帧动画：在多个关键帧之间生成流畅过渡
- 典型用途：短片叙事、营销视频、产品演示、社交媒体短视频（Reels/Shorts/TikTok）、应用动态素材

## API 规范（官方）

- 创建任务：`POST https://api.agnes-ai.cn/v1/videos`
- 获取结果（推荐）：`GET https://api.agnes-ai.cn/agnesapi?video_id=<VIDEO_ID>`
- 获取结果（兼容旧版）：`GET https://api.agnes-ai.cn/v1/videos/<TASK_ID>`
- 价格：`¥0.035 / 秒`（5 秒视频约 ¥0.175，创建任务前评估时长成本）
- **异步流程**：创建任务 → 轮询状态（`queued` → `in_progress` → `completed`/`failed`）→ 从 `metadata.url` 取结果

## 创建任务参数（官方）

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `model` | string | 是 | 模型名称，使用 `agnes-video-v2.0` |
| `prompt` | string | 是 | 视频内容的文本描述 |
| `image` | string | 否 | **图生视频**使用的图片 URL（顶层单值字符串） |
| `mode` | string | 否 | 生成模式，如 `ti2vid` 或 `keyframes` |
| `height` | integer | 否 | 视频高度，默认 `768` |
| `width` | integer | 否 | 视频宽度，默认 `1152` |
| `num_frames` | integer | 否 | 帧数，必须 `≤ 441` 且遵循 `8n + 1` 规则（如 81/121/241/441） |
| `frame_rate` | number | 否 | 帧率，支持 `1–60` |
| `num_inference_steps` | integer | 否 | 推理步数 |
| `seed` | integer | 否 | 随机种子，用于可复现结果 |
| `negative_prompt` | string | 否 | 反向提示词，描述需要避免的内容 |
| `extra_body.image` | array | 否 | **关键帧模式**下的输入图片 URL 数组 |
| `extra_body.mode` | string | 否 | 附加模式设置，如 `keyframes` |

## 重要规则（官方）

1. **视频生成是异步任务**：先 `POST /v1/videos` 创建任务，再轮询获取结果，不能一次请求拿到视频。
2. 创建响应同时返回 `task_id` 和 `video_id`，**新接入优先使用 `video_id`** 查询。
3. `num_frames` 必须 `≤ 441` 且满足 `8n + 1`（即 9, 17, 25, ..., 441）；`seconds = num_frames / frame_rate`。
4. **图生视频用顶层 `image`（单值 URL），关键帧动画用 `extra_body.image`（URL 数组）+ `extra_body.mode: "keyframes"`**，两者不要混用。
5. 尺寸会被标准化：支持 `480p`/`720p`/`1080p` 三档，宽高比支持 `16:9`、`9:16`、`1:1`、`4:3`、`3:4`。请求的 `width`/`height` 不完全匹配时会自动映射到最近的标准档位（如 `1024x576` → `832x448` 即 480p/16:9）。
6. 展示信息、计算时长或排查问题时，**以响应中的 `size`、`seconds`、`metadata.size_mapping` 为准**，不要用请求时的原始值。
7. 图生视频/关键帧的图片 URL 必须公开可访问。
8. 视频生成尺寸须为 64 的倍数（官方错误码文档），否则可能 500。**注：脚本默认按 ratio 发送的请求尺寸（如 16:9 的 1280x720）可能非 64 倍数，实测由服务端标准化消化（1280x720 → 1280x704），不会 500；以响应 `size_mapping` 为准。**
9. 最终视频 URL 仅在 `status: "completed"` 时位于 `metadata.url`。

## 宽高比与推荐场景（官方）

| 宽高比 | 推荐场景 |
| --- | --- |
| `16:9` | 横版视频、产品演示、网站展示、YouTube 风格 |
| `9:16` | 竖版短视频、移动端、TikTok / Reels / Shorts |
| `1:1` | 方形视频、社交媒体信息流、角色或产品展示 |
| `4:3` | 传统横版格式、通用演示内容 |
| `3:4` | 竖版演示、肖像或产品为主的内容 |

## 视频时长控制（官方）

`seconds = num_frames / frame_rate`

| 目标时长 | 推荐参数 |
| --- | --- |
| 约 3 秒 | `num_frames: 81`, `frame_rate: 24` |
| 约 5 秒 | `num_frames: 121`, `frame_rate: 24` |
| 约 10 秒 | `num_frames: 241`, `frame_rate: 24` |
| 约 18 秒 | `num_frames: 441`, `frame_rate: 24` |

## 推荐参数（官方）

| 场景 | 推荐设置 |
| --- | --- |
| 标准视频生成 | `width: 1152`, `height: 768`, `num_frames: 121`, `frame_rate: 24` |
| 社交短视频 | `num_frames: 81` 或 `121`, `frame_rate: 24` |
| 较长视频 | 增大 `num_frames` 或降低 `frame_rate` |
| 更流畅的运动 | `frame_rate: 24` 或 `30` |
| 可复现结果 | 设置固定 `seed` |
| 关键帧过渡 | `extra_body.mode: "keyframes"` |
| 避免不需要的内容 | 使用 `negative_prompt` |

## 创建任务示例（官方模板）

### 文生视频

```bash
curl -X POST https://api.agnes-ai.cn/v1/videos \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agnes-video-v2.0",
    "prompt": "A cinematic shot of a cat walking on the beach at sunset, soft ocean waves, warm golden lighting, realistic motion",
    "height": 768,
    "width": 1152,
    "num_frames": 121,
    "frame_rate": 24
  }'
```

### 图生视频

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "The woman slowly turns around and looks back at the camera, natural facial expression, cinematic camera movement",
  "image": "https://example.com/image.png",
  "num_frames": 121,
  "frame_rate": 24
}
```

### 关键帧动画

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "Generate a smooth cinematic transition between the keyframes, maintaining visual consistency and natural camera movement",
  "extra_body": {
    "image": [
      "https://example.com/keyframe1.png",
      "https://example.com/keyframe2.png"
    ],
    "mode": "keyframes"
  },
  "num_frames": 121,
  "frame_rate": 24
}
```

## 响应格式（官方）

### 创建任务响应

```json
{
  "id": "task_YOUR_TASK_ID",
  "task_id": "task_YOUR_TASK_ID",
  "video_id": "video_YOUR_VIDEO_ID",
  "object": "video",
  "model": "agnes-video-v2.0",
  "status": "queued",
  "progress": 0,
  "created_at": 1780457477,
  "seconds": "10.0",
  "size": "1280x768"
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 任务 ID，可与旧版查询接口配合使用 |
| `task_id` | string | 任务 ID，作用与 `id` 相同 |
| `video_id` | string | 视频 ID，**推荐用于获取视频结果** |
| `object` | string | 对象类型，通常为 `video` |
| `model` | string | 当前任务使用的模型 |
| `status` | string | 当前任务状态 |
| `progress` | integer | 当前任务进度百分比 |
| `created_at` | integer | 任务创建时间戳 |
| `seconds` | string | 视频时长（秒） |
| `size` | string | 视频分辨率 |

### 获取结果响应

官方文档记载最终视频 URL 位于 `metadata.url`；**实测（2026-08-16）URL 位于顶层 `url` 字段，响应中无 `metadata` 字段**。取值时做兼容处理（优先顶层 `url`，回退 `metadata.url`）：

```json
{
  "id": "video_682d4b9499ce4973922a916f8582be87",
  "task_id": "task_KAW...",
  "object": "video",
  "model": "agnes-video-v2.0",
  "status": "completed",
  "progress": 100,
  "created_at": 1786868479,
  "completed_at": 1786868538,
  "seconds": "3.4",
  "size": "1088x832",
  "size_mapping": {
    "adjusted": true,
    "height": 832,
    "message": "Input size 1152x768 was mapped to nearest preset 720p/4:3 (1088x832)",
    "ratio": "4:3",
    "requested_height": 768,
    "requested_width": 1152,
    "resolution": "720p",
    "width": 1088
  },
  "error": null,
  "url": "https://cos-platform-outputs.agnes-ai.cn/videos/agnes-video-v2.0/video_xxx.mp4"
}
```

结果响应额外字段：`completed_at`（完成时间戳）、`url`（最终视频 URL，仅 `completed` 时可用；官方文档记载为 `metadata.url`，实测为顶层 `url`，脚本做兼容取值：顶层优先、回退 `metadata.url`）、`size_mapping`（尺寸标准化详情：请求尺寸、实际输出、宽高比、分辨率档位；官方文档记载在 `metadata.size_mapping`，实测为顶层字段，脚本同样双向兼容）、`error`（失败时的错误信息，成功时为 `null`）。实测响应还含 `perf_*`、`internal_*`、`request_params` 等扩展字段，可忽略。

### 任务状态

| 状态 | 说明 |
| --- | --- |
| `queued` | 任务正在队列中等待 |
| `in_progress` | 视频正在生成 |
| `completed` | 视频生成成功 |
| `failed` | 视频生成失败 |

## 提示词最佳实践（官方）

- **文生视频**：`[主体] + [动作] + [场景] + [镜头运动] + [光线] + [风格]`
  - 示例：A young astronaut walking across a red desert planet, dust blowing in the wind, slow cinematic tracking shot, dramatic sunset lighting, realistic sci-fi style
- **图生视频**：描述哪些内容应该运动、哪些关键主体元素保持稳定
  - 示例：Animate the character with subtle breathing motion, hair moving gently in the wind, background lights flickering softly, while keeping the face and outfit consistent
- **关键帧动画**：清晰描述关键帧之间的过渡关系
  - 示例：Create a smooth transition from the first keyframe to the second keyframe, maintaining character identity, consistent camera angle, and natural motion between scenes

## 运行时执行入口

本模型的执行代码为 `scripts/gen_video.py`（用法见主入口 `SKILL.md` 的"脚本执行入口"）。脚本已内置上述全部官方规则与实测修正：

- 异步全流程封装：创建 → 轮询（`video_id` 自动 URL 编码，sleep 受 `--timeout` 约束）→ 下载 → MP4 魔数校验
- `--duration` 档位自动映射 `8n+1` 合规帧数；`--num-frames` 精确覆盖时自动校验
- **关键帧修复**：`--image` 传 1 张构造顶层 `image`（图生视频），传 ≥2 张构造 `extra_body.image` 数组 + `extra_body.mode: "keyframes"`
- 视频 URL 兼容取值链：顶层 `url` → `metadata.url` → 其他常见字段
- 实际输出尺寸/时长以响应 `size`/`seconds`/`size_mapping` 为准并打印
- 重试：429/503 退避 2s/4s；轮询/下载（GET）网络错误自动重试 5s/10s，创建任务（POST）不自动重试（防重复计费），详见主入口"重试策略"
- 成功输出 `SAVED_PATH=<绝对路径>`，失败输出 `ERROR=<原因>`

脚本未覆盖的官方参数：`num_inference_steps`（推理步数）、顶层 `mode: ti2vid`（附加生成模式）——需要时参考上文官方模板直接调用 API。

## 实测备注（非官方文档内容）

2026-08-16 全链路实测（文生视频，`num_frames: 81`, `frame_rate: 24`, 请求 1152x768）：

- **创建 → 完成**：任务创建成功（`queued`），约 60 秒后 `completed`（3.4 秒视频）。
- **最终 URL 在顶层 `url` 字段**，响应无 `metadata` 字段；文档记载的 `metadata.url` 与实测不符，`size_mapping` 实测为顶层字段。取 URL 时做兼容：优先 `url`，回退 `metadata.url`。
- **输出域名为 `cos-platform-outputs.agnes-ai.cn`**，实测可正常下载（约 960KB MP4，ftyp 校验有效）。
- **尺寸标准化**：请求 1152x768（3:2 比例）被映射为 720p/4:3（1088x832）。注意官方"标准视频生成"推荐的 1152x768 并非 16:9 而是 3:2，会被标准化；如需 16:9 输出，明确请求 16:9 的尺寸组合并核对响应 `size_mapping`。请求 1280x720 实测输出 1280x704（16:9 保持，宽高均调整为 64 的倍数）。
- 2026-08-16 脚本化验证：`gen_video.py` 文生视频 3 秒档全链路通过（创建 → 轮询约 60s → 下载 794KB MP4 → ftyp 校验 → SAVED_PATH 输出）。
- **`video_id` 格式**：创建响应返回的 `video_id` 可能是长 base64 上游 ID，用于轮询查询时需 URL 编码；轮询响应中的 `id` 为 `video_<hex>` 格式。
- **系统默认注入 negative_prompt**（video game / cartoon / blurry / jittery 等），未传时也会生效；`request_params` 中可见。
- 响应包含 `perf_*`（推理耗时、输出大小、实际参数）、`internal_*` 等扩展字段，可用于排查，正常流程忽略。
