---
name: "agnes-media"
description: "Unified Agnes AI media generation: images via agnes-image-2.1-flash, videos via agnes-video-v2.0. Invoke when user asks to generate/edit/composite images or create/animate videos."
---

# Agnes AI 媒体生成技能包

统一入口，聚合 Agnes AI 平台的图像与视频生成模型。每个模型保留完整官方规格，作为本文件同级的独立规格文件存放，按任务类型路由加载。

## 模型路由表

| 任务类型 | 模型 | 规格文件（同级目录） | Endpoint | 价格 |
| --- | --- | --- | --- | --- |
| 生成 / 编辑 / 合成**图像** | `agnes-image-2.1-flash` | `agnes-image-2.1-flash.md` | `POST /v1/images/generations` | `¥0.02 / 张` |
| 生成 / 动画化**视频** | `agnes-video-v2.0` | `agnes-video-v2.0.md` | `POST /v1/videos`（异步） | `¥0.035 / 秒` |

## 执行流程

1. 根据用户请求判断任务类型（图像 or 视频）。
2. **优先用 `scripts/` 下的 CLI 脚本执行**（见下节用法）。脚本已内置官方规格约束与实测修正（重试、b64 输出、URL 兼容取值、关键帧参数构造、data URI 转换），不要每次现写调用代码。
3. 需要理解参数语义、组合高级请求或排查异常响应时，**用 Read 工具读取对应规格文件**获取完整官方规格。
4. 混合任务（如先生成图像再动画化）按顺序分别执行，前一步的 `SAVED_PATH` 输出可作为后一步 `--image` 输入。

## 脚本执行入口（首选执行方式）

依赖：Python 3.8+（纯标准库，无需安装第三方包）。API Key 从环境变量 `AGNES_API_KEY` 读取，或用 `--api-key` 传入；**不要写进任何文件**。

### 图像：`scripts/gen_image.py`（同步，¥0.02/张）

```powershell
# 文生图（默认 2K + 1:1 + b64 输出）
python .trae/skills/agnes-media/scripts/gen_image.py --prompt "..." --size 2K --ratio 16:9

# 图生图 / 多图合成（本地文件自动转 data URI）
python .trae/skills/agnes-media/scripts/gen_image.py --prompt "..." --image a.png --image b.png
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--prompt` | 必填 | 文本指令 |
| `--size` | `2K` | `1K`/`2K`/`3K`/`4K` |
| `--ratio` | `1:1` | 8 种：`1:1`/`3:4`/`4:3`/`16:9`/`9:16`/`2:3`/`3:2`/`21:9` |
| `--image` | 无 | 可重复；传入即切换为图生图/多图合成 |
| `--format` | `b64` | `url` 输出域名在部分网络不可达，慎用 |
| `--filename` / `--output-dir` | 自动 / `agnes-output` | 输出位置（默认文件名 `agnes-时间戳.扩展名`，冲突自动加后缀，绝不覆盖） |
| `--api-key` | 无 | API Key（优先于环境变量 `AGNES_API_KEY`） |

### 视频：`scripts/gen_video.py`（异步，创建→轮询→下载，¥0.035/秒）

```powershell
# 文生视频（--duration 自动映射 8n+1 合规帧数）
python .trae/skills/agnes-media/scripts/gen_video.py --prompt "..." --duration 3

# 图生视频（1 张图） / 关键帧动画（≥2 张图，自动构造 extra_body + keyframes 模式）
python .trae/skills/agnes-media/scripts/gen_video.py --prompt "..." --image p.png
python .trae/skills/agnes-media/scripts/gen_video.py --prompt "..." --image k1.png --image k2.png
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--prompt` | 必填 | 视频内容的文本描述 |
| `--duration` | `5` | `3`/`5`/`10`/`18` 秒档位 |
| `--num-frames` / `--fps` | 随时长档位 / `24` | 精确覆盖（自动校验 8n+1、≤441、1-60） |
| `--ratio` | `16:9` | 5 种：`16:9`/`9:16`/`1:1`/`4:3`/`3:4`（与图像的 8 种不同）；实际输出以响应 `size_mapping` 为准 |
| `--width` / `--height` | 随 ratio | 精确覆盖 |
| `--image` | 无 | 可重复；1 张=图生视频，≥2 张=关键帧动画 |
| `--seed` / `--negative-prompt` | 无 | 可复现 / 反向提示词 |
| `--poll-interval` / `--timeout` | `10` / `900` | 轮询节奏（最小 1s；3 秒视频实测约 60s 完成） |
| `--filename` / `--output-dir` | 自动 / `agnes-output` | 输出位置（默认 `agnes-时间戳.mp4`，冲突自动加后缀） |
| `--api-key` | 无 | API Key（优先于环境变量 `AGNES_API_KEY`） |

视频脚本未覆盖的官方参数：`num_inference_steps`（推理步数）、`mode: ti2vid`——需要时按视频规格文件中的官方模板直连 API。

### 输出契约（解析执行结果）

- 成功：进程末行输出 `SAVED_PATH=<文件绝对路径>`，退出码 0
- 失败：进程末行输出 `ERROR=<原因摘要，截断至 500 字符>`，退出码 1
- 输出经编码安全处理（Windows cp936 终端下契约行仍可输出）
- 批量执行时用 `Select-String 'SAVED_PATH|ERROR'` 汇总结果

### 重试策略（脚本内置，无需人工干预）

- `429`/`503`：退避 2s/4s 自动重试（服务端已拒绝，未计费，GET/POST 均安全）
- 网络错误：仅 GET（轮询/下载）自动重试 5s/10s；**POST 不自动重试**（首次请求可能已提交并计费，脚本会报错并提示先到控制台确认）

### 批量并行模式（多张图/多个视频）

用 PowerShell Job 并行，**并发上限 3、单批 ≤10 个任务**（免费档 RPM 20）：

```powershell
$jobs = 1..3 | ForEach-Object {
  Start-Job -ScriptBlock { param($i)
    python .trae/skills/agnes-media/scripts/gen_image.py --prompt "concept art variation $i" --size 1K
  } -ArgumentList $_
}
$jobs | Wait-Job | Receive-Job | Select-String 'SAVED_PATH|ERROR'
$jobs | Remove-Job
```

注意：**不要用 subagent 并行执行生成任务**（会绕过并发控制），一律用上述 Job 模式。

## 共享规范（两个模型通用）

- **API 网关**：`https://api.agnes-ai.cn`
- **认证**：`Authorization: Bearer YOUR_API_KEY`
  - API Key 运行时从环境变量 `AGNES_API_KEY` 读取；若未设置，向用户询问，不要硬编码
  - Key 在 Agnes AI 开发者控制台生成
- **Content-Type**：`application/json`
- **计费意识**：创建请求前按价格表估算成本；验证链路时用最低成本参数（图像 1K 单张 ¥0.02，视频 81 帧 ≈ 3 秒 ≈ ¥0.1）
- **同步/异步差异**：图像为同步接口（超时建议 60s–360s）；视频为异步任务（创建 → 轮询 → 取结果，流程见视频规格文件）

## 共享错误码速查（官方 Common Error Codes 摘录）

| 状态码 | 含义 | 处理 |
| --- | --- | --- |
| `400` | 请求参数无效 | 检查必填参数与 JSON 格式 |
| `401` | 认证失败 | API Key 错误/过期/未放入 Authorization 头 |
| `402` | 余额不足 | 充值或升级 Token Plan 后重试 |
| `404` | 路径不存在 | Base URL / 模型名错误，检查 `/v1` 是否被重复拼接 |
| `408` | 请求超时 | 加大客户端超时并重试 |
| `413` | 请求体过大 | Base64 输入过大，改用公共 URL |
| `422` | 参数值不合规 | 输入 URL 不可访问、Base64 无效、尺寸/参数超约束 |
| `429` | 频率超限 | 免费用户 RPM 20，等待 1 分钟 |
| `500` | 服务器内部错误 | **尺寸约束：图像须为 16 的倍数，视频须为 64 的倍数** |
| `503` | 服务繁忙 | 稍后重试 |
| `504` / `524` | 网关/上游超时 | 稍后重试或降低生成规格 |

完整错误码：`https://wiki.agnes-ai.cn/en/docs/code.md`

## 平台实测备注（非官方文档内容）

2026-08-16 实测（两个模型均全链路验证通过）：

- **图像**：`b64_json` 输出可靠；`return_base64: true` 实测未生效（仍返回 URL）。图像 URL 域名 `platform-outputs.agnes-ai.space` 在部分网络下无法直接下载（连接被重置），本地保存优先用 `b64_json`。
- **视频**：全链路正常（创建 → 约 60 秒生成 → 下载）。输出域名为 `cos-platform-outputs.agnes-ai.cn`，实测可直接下载。最终 URL 在顶层 `url` 字段（文档记载为 `metadata.url`，与实测不符）。
- API 网关 `api.agnes-ai.cn` 实测可正常访问。
- 两个模型各自的实测差异详情见对应规格文件的"实测备注"章节。

## 官方文档索引

- 文档总索引：`https://wiki.agnes-ai.cn/llms.txt`
- 图像模型：`https://wiki.agnes-ai.cn/en/docs/agnes-image-21-flash.md`
- 视频模型：`https://wiki.agnes-ai.cn/en/docs/agnes-video-v20.md`
- Quickstart：`https://wiki.agnes-ai.cn/en/docs/quickstart.md`
- 错误码：`https://wiki.agnes-ai.cn/en/docs/code.md`
