---
name: stepfun-image
description: Generate and transform images using StepFun APIs. Three modes — txt2img (text-to-image, pure prompt), img2img (image-to-image, style transfer with source image + prompt), edit (image editing, modify uploaded image with prompt). Use when user asks to generate images from text, transform/restyle images, edit images, or mentions "文生图", "图生图", "图片编辑", "style transfer", "image generation", "StepFun".
version: 1.0.0
---

# StepFun 图像生成 (Text-to-Image / Image-to-Image / Edit)

统一封装 StepFun 三套图像 API，纯 Python 标准库，无额外依赖。

## Quick Start

```bash
# 文生图
python scripts/generate.py txt2img --prompt "一只在星空下奔跑的狐狸" --api-key <KEY>

# 图生图（风格迁移）
python scripts/generate.py img2img --prompt "换成宫崎骏风格" --source photo.jpg --weight 0.5 --api-key <KEY>

# 图片编辑（上传修改）
python scripts/generate.py edit --prompt "让图中角色戴上帽子" --image photo.png --api-key <KEY>
```

## Three Modes

| 模式 | 端点 | 模型 | 输入 |
|------|------|------|------|
| txt2img | `/v1/images/generations` | `step-image-edit-2`(推荐) / `step-2x-large` | prompt |
| img2img | `/v1/images/image2image` | `step-2x-large` | prompt + source(URL/base64) |
| edit | `/v1/images/edits` | `step-image-edit-2` | prompt + image(文件上传) |

## Parameters

### 通用参数（三种模式共享）

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--prompt` | 文本描述（必填） | — |
| `--api-key` | API Key（或设 STEP_API_KEY 环境变量） | — |
| `--output` | 输出目录 | `.` |
| `--filename` | 自定义文件名（不含扩展名） | auto |
| `--seed` | 随机种子（0=随机） | 0 |
| `--steps` | 生成步数 | 各模型不同 |
| `--cfg-scale` | CFG 引导强度 | 各模型不同 |

### txt2img 特有

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--model` | `step-image-edit-2`(推荐) 或 `step-2x-large` | `step-image-edit-2` |
| `--size` | 输出尺寸 | 1024x1024 |
| `--negative-prompt` | 反向提示词（仅 edit-2 模型） | — |
| `--text-mode` | 增强文字渲染（仅 edit-2 模型） | false |

### img2img 特有

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--source` | 源图片 URL 或本地路径（必填） | — |
| `--weight` | 源图权重 (0,1]，越小越接近原图 | 0.5 |
| `--size` | 输出尺寸 | 1024x1024 |

### edit 特有

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--image` | 待编辑图片本地路径（必填） | — |
| `--negative-prompt` | 反向提示词 | — |
| `--text-mode` | 增强文字渲染 | false |

### 支持的输出尺寸

正方形：256x256, 512x512, 768x768, 1024x1024
长方形：1280x800, 800x1280

> ⚠️ edit 模式输出尺寸跟随输入图片，size 参数无效。

## Output Contract

- 成功：exit 0，stdout 末行 `SAVED_PATH=<path>`，同时生成 `status.json`
- 失败：exit 1，stderr 输出错误信息

## API Details

- Base URL: `https://api.stepfun.com`
- Auth: `Authorization: Bearer <STEP_API_KEY>`
- Key: `1S4Im0liYj4lYsRGDBz2YSCubkLY8tAp2JNmR5IaDK2RBxmpMfELQDdENqUWoA5h4`

## Pitfalls

- 支持中文 prompt，无需翻译英文
- txt2img 推荐 `step-image-edit-2`（效果更好，支持 negative_prompt 和 text_mode）
- img2img 的 source_weight 越小越接近原图，越大越接近 prompt
- edit 模式用 multipart/form-data 上传文件，图片 ≤4096x4096
- img2img 的 source URL 须公网可访问；本地文件自动转 base64
- 图片 ≤10MB（img2img）/ 像素 ≤4096x4096（edit），格式 PNG/JPEG
- Windows SSL 兼容：脚本内置 SECLEVEL=1 workaround
- 429 限流自动重试（解析 retryDelay）

## See Also

- `agnes-image-gen`：Agnes 文生图/图生图（不同模型和风格）
- `ai-daily-video` 的 `search_images.py`：StepFun 文搜图（检索，非生成）
