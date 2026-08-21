# 第 3 步深度参考：TTS 管线参数、预置音色与 manifest 字段

本文件是 SKILL.md 第 3 步（运行 TTS 管线）的深度参考，主流程与示例命令见 SKILL.md。

## pipeline.py 参数说明

- `--api-key`：可选，默认从环境变量或 `~/.config/ai-video/.env` 读取 `MIMO_API_KEY`
- `--source segments_source.json`：结构化输入（必填）。逐段独立分句，直接产出带 segments 分组的 manifest
- `--voice-id`：预置音色 ID，见下表（默认 `冰糖`）
- `--voice-style`：自然语言风格描述，控制语气情绪（如"清晰沉稳的讲解风格，语速适中"）
- `--gap 0.4`：相邻两句之间的静音间隔（秒，默认 `0.4`）。拼接音频与 manifest 时间轴都会在句间插入这段静音
- `--speed 1.5`：**语速倍率**，默认 `1.5`（比正常快一半）。通过 ffmpeg `atempo` 对每句合成音频做精确变速，**与 TTS 模型自然语速无关、完全确定**。设 `1.0` 即原始语速。字幕时间轴会按变速后实测时长对齐，依然精准同步
- `--resume`：跳过已存在且时长有效的 WAV 文件（断点续传，省 API 额度），日常反复调试同一份稿件时**始终建议加上**
- `--bgm bgm.mp3` / `--bgm-volume 0.15`：背景音乐（自动循环、混音；音量 0.0-1.0，默认 0.15）
- `--model` / `--base-url`：TTS 模型名 / API base URL，一般不需要改，默认读 env
- `--dry-run`：仅断句 + 段落预览，不调 TTS、不写音频（验证稿件格式用，零额度消耗）。注意仍必须传 `-o`（只会被校验，不会创建文件）
- `--workers 4`：并行 TTS 调用数（默认 4）。句子数多时能明显缩短总耗时；调大会更快触及 API 限速，遇到大量 `[retry]` 日志时调小
- `--api-timeout`：单次 TTS API 调用超时（默认 30s）
- `--on-fail {skip,silence}`：单句 TTS 失败时的兜底（默认 `skip`，直接丢弃该句——成片少这句的音频与字幕）。`silence` 改为静音占位继续：时长按字数/语速启发式估算折算、落 `.failed` marker、manifest 对应句子带 `synth_failed: true`，不阻断整条视频，事后可定位补录
- `--check-env`：首次运行环境自检（API key / ffmpeg / lavfi / WAV demuxer / Node 等），不合成任何音频；新环境建议先跑一遍

> **默认语速**：`--speed` 默认 `1.5`（日常调用可省略）。如需原始语速，显式传 `--speed 1.0`。
>
> 完整参数列表见 `python scripts/pipeline.py --help`——本文只列核心参数。

## 预置音色列表

| voice-id | 语言 | 性别 | 适用场景 |
|----------|------|------|----------|
| `冰糖` | 中文 | 女 | 默认推荐，清晰自然，适合讲解播报 |
| `茉莉` | 中文 | 女 | 温柔娓娓道来，适合故事/科普 |
| `苏打` | 中文 | 男 | 中文男声，沉稳有力 |
| `白桦` | 中文 | 男 | 中文男声，低沉浑厚 |
| `Mia` | 英文 | 女 | 中文稿件里偶尔出现的英文术语/品牌名 |
| `Chloe` | 英文 | 女 | 中文稿件里偶尔出现的英文术语/品牌名 |
| `Milo` | 英文 | 男 | 中文稿件里偶尔出现的英文术语/品牌名，正式播报 |
| `Dean` | 英文 | 男 | 中文稿件里偶尔出现的英文术语/品牌名 |

> **中文内容推荐使用 `冰糖` 或 `苏打`**，中英混合发音更自然。`Milo`/`Chloe` 等英文音色在朗读中文时效果欠佳；这四个英文音色只用于中文稿件里的英文术语/品牌名朗读，**整篇纯英文或以非中文为主的稿件不适用本技能**（见 SKILL.md"不适用场景"）。
>
> **双人对话推荐搭配**：`茉莉`（女，温柔）+ `苏打`（男，沉稳）或 `冰糖`（女）+ `白桦`（男）——一男一女音色反差明显，字幕的说话人变色 + 音色切换双重提示，听感上不容易混淆是谁在说话。避免选两个音域接近的同性别音色做对话。

## manifest 的 `segments` 字段格式

manifest 由 pipeline 从 `segments_source.json` 自动产出——**手写 manifest 少见，以 pipeline 产出为准**；确需手写/裁剪时按下述格式提供（`_contracts.validate_timing_manifest` 会校验，缺字段直接报错）：

```json
{
  "sentences": [
    {"index": 0, "text": "大家好，今天我们来看反向传播算法。", "start_time": 0.0, "duration": 3.2},
    {"index": 1, "text": "它是神经网络学习的核心机制。", "start_time": 3.6, "duration": 2.8}
  ],
  "total_duration": 6.4,
  "gap": 0.4,
  "voice_id": "冰糖",
  "combined_audio": "<项目目录>/audio_output/combined.wav",
  "segments": [
    {
      "id": "seg1",
      "title": "反向传播是什么",
      "tagline": "深度学习基础",
      "body": "反向传播是神经网络学习的核心算法，把预测误差从输出层往回传。",
      "accent": "#64b5f6",
      "sentences": [
        {"index": 0, "text": "大家好，今天我们来看反向传播算法。", "start_time": 0.0, "duration": 3.2},
        {"index": 1, "text": "它是神经网络学习的核心机制。", "start_time": 3.6, "duration": 2.8}
      ]
    }
  ]
}
```

- 顶层 `sentences`（非空列表）与数值 `total_duration`（ffmpeg 实测总时长）必填；每个句子对象含 `index`/`text`/`start_time`/`duration` 四个字段（对话段落的句子另有 `speaker`，TTS 失败降级为静音的句子带 `synth_failed: true`）
- `segments` 每段必须自带**非空** `sentences` 列表（分组渲染的数据源，缺失或为空会被契约校验直接拒绝）；段落版面字段为 `id`/`title`/`tagline`/`body`/`accent`，不再用 `start`/`end` 句子索引——那是 pipeline 内部中间格式，最终 manifest 不含这两个字段

**可选字段**：
- `speed`（float）：段落级语速倍率，覆盖全局 `--speed`。例如开场/结尾用 `1.2`、正文段用 `1.5`。仅影响 TTS atempo 变速，不影响字幕时间轴精度
- `voice_id` / `voice_style`（string）：段落级音色覆盖，覆盖全局 `--voice-id`/`--voice-style`。常见用法是开场/结尾换一个音色制造"主播+播报"的双人感
