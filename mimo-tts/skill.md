---
name: tts-voiceover
description: 将文章或文本转换为 MP3 语音配音。使用 MiMo-V2.5-TTS API
  合成自然语音，支持纯文本和文件路径输入（.txt、.md），自动对长文本智能分段合成。当用户要求为文章配音、生成语音朗读、文字转语音、TTS
  合成、音频旁白时使用此技能。不支持实时流式合成、视频配音或多语言混合 TTS。
description_zh: 将文章或文本转换为 MP3 语音配音。使用 MiMo-V2.5-TTS API 合成自然语音，支持纯文本和文件路径输入，自动对长文本智能分段合成。
version: 1.3.0
disable-model-invocation: true
---

# TTS 文章配音

基于 MiMo-V2.5-TTS API 的文章配音工具。Markdown 文件自动清理标记，避免语法字符被读出。

## API 密钥

```
sk-xxx
```

运行脚本时通过 `--api-key` 参数传入上述密钥。也可设置环境变量 `MIMO_API_KEY` 后省略该参数。

## 依赖

```bash
pip install openai imageio-ffmpeg -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 工作流程

### 第 1 步：确认输入

确认用户的文本来源（直接文本 或 .txt/.md 文件路径）。如用户未指定语音风格描述，使用默认：`"清晰自然的朗读声，语速适中，语气流畅"`。

### 第 2 步：运行配音脚本

脚本位于本技能目录下的 `scripts/voiceover.py`，使用绝对路径执行。

```bash
# 直接文本
python scripts/voiceover.py --api-key <KEY> -t "文章内容..." -o output.mp3

# 文件输入（.md 文件会自动清理 Markdown 标记）
python scripts/voiceover.py --api-key <KEY> -f article.md -o output.mp3

# 指定语音风格描述
python scripts/voiceover.py --api-key <KEY> -f article.md -v "温柔女声，娓娓道来" -o output.mp3

# 指定预设音色（Milo=男声, Chloe=女声）
python scripts/voiceover.py --api-key <KEY> -f article.md --voice-id Milo -o output.mp3

# 预览分段结果（不调用 API，无需 --api-key）
python scripts/voiceover.py -f article.md --dry-run

# 调整分段粒度（每段最大字符数，默认 300）
python scripts/voiceover.py --api-key <KEY> -f article.md --max-chars 200 -o output.mp3

# 查看当前配音状态（无需 --api-key）
python scripts/voiceover.py --status
```

`<KEY>` 替换为上方 API 密钥章节中的密钥值。

### 第 3 步：交付结果

1. **提取输出路径** — 脚本 stdout 最后一行为 `SAVED_PATH=<绝对路径>`。
2. **交付文件**：使用 `present_files` 工具或提供 `file://` 链接。IM 渠道可用时，通过 IM 媒体发送能力推送 MP3 文件。
3. **告知用户**：输出路径、文件大小、语音时长、分段数量。

## 输出契约

| 信号 | 含义 |
|------|------|
| `SAVED_PATH=<路径>` | 成功 — stdout 最后一行包含文件绝对路径 |
| `exit 0` | 脚本成功完成 |
| `exit 1` | 脚本失败 — 查看 stderr 获取错误详情 |
| `exit 1` + stderr `[错误] 所有段落合成失败` | 全部段落 API 调用失败 |
| `status.json`（临时目录） | 机器可读状态：`segmenting` → `synthesizing` → `concatenating` → `completed` / `failed` |

**容错行为**：单段失败时跳过并继续，跳过的段落列在 `[警告]` 行中。全部失败时 exit 1。

## 分段策略

| 文本长度 | 策略 | 说明 |
|----------|------|------|
| ≤ `--max-chars` | 整段合成 | 一次 API 调用，音质最连贯 |
| 超过阈值 ~ 2000 字 | 自动分段 | 按段落拆分，每段不超过阈值 |
| > 2000 字 | 自动分段 | 段落内按句号/逗号二次拆分 |
| 无标点超长文本 | 硬切分 | 按字符数强制切分（最终 fallback） |

`--max-chars` 默认 300。分段优先级：空行 > 段落 > 句末标点（。！？；）> 句中标点（，、：）> 字符数硬切

## 语音风格描述示例

| 场景 | 风格描述 |
|------|----------|
| 通用朗读 | 清晰自然的朗读声，语速适中，语气流畅 |
| 新闻播报 | 专业播音腔，语速偏快，字正腔圆 |
| 故事讲述 | 温柔女声，娓娓道来，富有感情 |
| 技术讲解 | 清晰专业的讲解男声，语速适中 |
| 儿童内容 | 活泼欢快的童声，语速稍慢 |

## 预设音色（`--voice-id`）

`-v/--voice` 控制语气和情感，`--voice-id` 控制音色（嗓音本身）。两者可组合使用。

| voice-id | 音色 | 适用场景 |
|----------|------|----------|
| `Milo` | 男声 | 新闻播报、技术讲解、故事讲述 |
| `Chloe` | 女声 | 通用朗读、故事讲述、儿童内容 |
| `mimo_default` | 默认 | 不指定时的模型自动选择 |
| 不指定 | 模型自选 | 仅靠 `-v` 风格描述控制 |

## MiMo TTS API 细节

### 消息角色约定

```python
messages = [
    {"role": "user", "content": "语音风格描述"},      # 可选：风格描述
    {"role": "assistant", "content": "要朗读的文本"},  # 必须：合成文本放 assistant
]
```

- 要合成的文本 → `assistant` 角色（⚠️ 严禁放在 user 中）
- 语音风格描述 → `user` 角色（可选）

### Inline 表演控制

放在 assistant 文本中：

| 标签 | 用途 | 示例 |
|------|------|------|
| `(慵懒)` `(严肃)` 等 | 段首风格标签，控制整段语气 | `(温柔)今天天气真好` |
| `[叹气]` `[笑声]` 等 | 内联音频标签，插入表演细节 | `我真的[叹气]没办法` |
| `(唱歌)` 前缀 | 让模型唱歌而非朗读 | `(唱歌)月亮代表我的心` |

### 扩展模型（当前脚本未使用）

- `mimo-v2.5-tts-voicedesign`：从文本描述生成定制音色
- `mimo-v2.5-tts-voiceclone`：用音频样本克隆音色

### API 端点

- Endpoint: `https://api.xiaomimimo.com/v1/chat/completions`
- Model: `mimo-v2.5-tts`
- Audio format: `wav`（官方支持 wav / pcm16）
- 中文语速约 4-5 字/秒

## 已知陷阱

1. **`strip_markdown` 下划线正则限制** — 已用 word-boundary 约束修复（v1.3.0），但 `__bold__` 双下划线仍会被清除。技术文本中的 `variable_name` 不受影响。
2. **非中文文本** — 默认语音风格描述和分段标点均为中文优化。英文文本建议调整 `--voice` 风格描述，分段行为可能不如中文精准。
3. **TTS 表演标签与 Markdown 链接冲突** — 若源文本含 `[叹气](url)` 格式，`strip_markdown` 会将 `叹气` 当作链接文本保留，但语义可能丢失。建议配音前先手动处理此类冲突。
4. **MiMo API 服务不可用时无内置降级** — 如 API 持续失败，可建议用户使用 `edge-tts`（`pip install edge-tts`）作为免费替代方案。

## 不适用场景

- 实时/流式语音合成（本工具仅生成 MP3 文件）
- 视频配音（需视频编辑工具配合）
- PDF/DOCX 等非纯文本输入（需先提取文本）
- URL 输入（需先抓取网页内容再传入）

## 故障排查

| 问题 | 解决方案 |
|------|----------|
| `ModuleNotFoundError: openai` | `pip install openai` |
| ffmpeg 找不到 | `pip install imageio-ffmpeg` |
| API 超时 | 脚本内置 3 次重试 + 120s 超时，通常可自动恢复 |
| 单段合成音质差 | 尝试减小 `--max-chars` 或更换语音风格描述 |
