---
name: stepfun-tts
description: Synthesize speech from text using StepFun TTS API. Three models — step-tts-mini (fast), step-tts-2 (quality), stepaudio-2.5-tts (emotion-controlled via instructions). Supports speed/volume control, word-level timestamps for subtitles, 30+ Chinese voices, emotion/style tags. Use when user asks for TTS, text-to-speech, voiceover, speech synthesis, or mentions "语音合成", "配音", "TTS", "StepFun TTS", "朗读", "播报".
version: 1.0.0
---

# StepFun 语音合成 (TTS)

基于 StepFun TTS API，支持三种模型、30+ 音色、语速/音量控制、逐词时间戳字幕。

## Quick Start

```bash
# 基础合成
python scripts/synthesize.py --text "你好世界" --voice cixingnansheng --api-key <KEY>

# 文件输入 + 指定音色 + 语速 1.5 倍
python scripts/synthesize.py -f article.txt --voice boyinnansheng --speed 1.5 --api-key <KEY>

# 带逐词时间戳（输出 .mp3 + .subtitles.json）
python scripts/synthesize.py --text "今天天气真好" --voice cixingnansheng --timestamp --api-key <KEY>

# stepaudio-2.5-tts + 情绪指导
python scripts/synthesize.py --text "（冷笑）你以为这是开玩笑的吗！" --model stepaudio-2.5-tts --instruction "语气愤怒，压迫感强" --api-key <KEY>
```

## Models

| 模型 | 特点 | 适用场景 |
|------|------|----------|
| `step-tts-mini` | 快速，低延迟 | 实时对话、短文本 |
| `step-tts-2` | 高质量 | 正式播报、有声书 |
| `stepaudio-2.5-tts` | 情绪可控，支持 instruction 和 () 内嵌指令 | 角色扮演、情感丰富的内容 |

## Parameters

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--text` / `-t` | 直接文本（与 -f 二选一） | — |
| `--file` / `-f` | 文本文件路径（.txt/.md，自动清理 Markdown） | — |
| `--voice` | 音色 ID（见下方常用音色表） | `cixingnansheng` |
| `--model` | 模型名称 | `step-tts-mini` |
| `--speed` | 语速 0.5~2.0 | 1.0 |
| `--volume` | 音量 0.1~2.0 | 1.0 |
| `--format` | 输出格式：mp3/wav/flac/opus/pcm | mp3 |
| `--sample-rate` | 采样率：8000/16000/22050/24000/48000 | 24000 |
| `--instruction` | 全局情绪指导（仅 stepaudio-2.5-tts，≤200 字） | — |
| `--emotion` | 情绪标签（仅非 2.5 模型）：高兴/悲伤/生气 等 | — |
| `--style` | 风格标签（仅非 2.5 模型）：温柔/甜美/严肃 等 | — |
| `--timestamp` | 输出逐词时间戳 JSON | false |
| `--output` / `-o` | 输出文件路径 | auto |
| `--api-key` | API Key（或设 STEP_API_KEY 环境变量） | — |

## 常用音色

### 男声

| ID | 特点 | 适用 |
|----|------|------|
| `cixingnansheng` | 磁性男声 | 深度讲述、情感慰藉 |
| `boyinnansheng` | 播音男声 | 专业讲述、新闻播报 |
| `wenrounansheng` | 温柔男声 | 播报、教学、客服 |
| `zixinnansheng` | 自信男声 | 教育、营销、故事 |
| `shenchennanyin` | 深沉男音 | 情感电台、悬疑故事 |
| `zhengpaiqingnian` | 正气青年 | 宣传内容、英雄角色 |
| `qingniandaxuesheng` | 青年大学生 | 新闻解说、年轻播报 |

### 女声

| ID | 特点 | 适用 |
|----|------|------|
| `jingdiannvsheng` | 经典女声 | 标准客服、情感支持 |
| `tianmeinvsheng` | 甜美女声 | 甜蜜陪伴、客户关怀 |
| `lively-girl` | 活泼女孩 | 故事讲述、视频配音 |
| `qingchunshaonv` | 青春少女 | 年轻语音助手 |
| `linjiajiejie` | 邻家姐姐 | 亲切播报、配音、助手 |
| `zhixingjiejie` | 知性姐姐 | 知识播报、智能助手 |
| `qinqienvsheng` | 清新女声 | 亲切新闻、解说 |
| `wenrounvsheng` | 温柔女声 | 温柔讲述、情感关怀 |

### 情绪标签（`--emotion`，仅 step-tts-mini / step-tts-2）

高兴、非常高兴、悲伤、生气、非常生气、撒娇、恐惧、惊讶、兴奋、钦佩、困惑

### 风格标签（`--style`，仅 step-tts-mini / step-tts-2）

慢速、极慢、快速、极快、冷漠、尴尬、沮丧、骄傲、温柔、甜美、豪爽、严肃、傲慢、老年、吼叫、阴阳怪气、磕巴

> ⚠️ `stepaudio-2.5-tts` 不支持 emotion/style 标签，用 `--instruction` 或文本中 () 内嵌指令控制情绪。

## Output Contract

- 成功：exit 0，stdout 末行 `SAVED_PATH=<path>`
- 带 `--timestamp` 时额外输出 `<basename>.subtitles.json`
- `status.json` 记录合成状态
- 失败：exit 1，stderr 输出错误

## API Details

- Endpoint: `POST https://api.stepfun.com/v1/audio/speech`
- Auth: `Authorization: Bearer <STEP_API_KEY>`
- Key: `1S4Im0liYj4lYsRGDBz2YSCubkLY8tAp2JNmR5IaDK2RBxmpMfELQDdENqUWoA5h4`

## Pitfalls

- 单次请求文本 ≤1000 字符，脚本自动分段合成 + 拼接
- `stepaudio-2.5-tts` 中 `()` 内文本作为指令不发音（需发音勿加括号）
- `stepaudio-2.5-tts` 不支持 emotion/style/voice_label 参数（传了会报错）
- `--instruction` 仅对 `stepaudio-2.5-tts` 生效（其他模型传了会报错）
- `--timestamp` 需要 return_url 模式，脚本自动处理
- Markdown 文件自动清理标记（#、*、[]、()等），避免语法字符被读出
- 中文语速约 4-5 字/秒（1.0x），可据此估算时长
- Windows SSL 兼容：脚本内置 SECLEVEL=1 workaround

## See Also

- `tts-voiceover`：MiMo TTS（不同模型和音色风格）
- `ai-daily-video`：日报视频制作流程（TTS + 渲染）
