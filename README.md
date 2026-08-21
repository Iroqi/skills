# Skills

一套面向 AI Agent 的媒体生成与教学技能集合，覆盖 **学习教学**、**信源转视频**、**图像生成**、**视频生成** 和 **语音合成** 五大方向。

---

## 目录

- [项目概览](#项目概览)
- [技能总览](#技能总览)
- [快速开始](#快速开始)
- [依赖与安装](#依赖与安装)
- [API 密钥管理](#api-密钥管理)
- [目录结构](#目录结构)
- [不适用场景](#不适用场景)

---

## 项目概览

| 技能 | 一句话描述 | 核心入口 |
|------|-----------|---------|
| `learning-system` | 把任何主题分解为知识图谱，通过诊断→教学→练习→评估循环追踪掌握度 | `learning-system/SKILL.md` |
| `content-to-video` | 把任意信源（文本/文档/网页/API）自动制成带字幕、配图、动效的解说视频 | `content-to-video/SKILL.md` |
| `agnes-media` | 统一封装 Agnes AI 平台的图像生成与视频生成接口 | `agnes-media/SKILL.md` |
| `stepfun-image` | StepFun 三套图像 API：文生图、图生图、图片编辑 | `stepfun-image/SKILL.md` |
| `stepfun-tts` | StepFun 语音合成，三种模型，30+ 音色，支持情绪控制与逐词时间戳 | `stepfun-tts/SKILL.md` |
| `mimo-tts` | 基于 MiMo-V2.5-TTS 的文章配音，支持文本/文件输入、风格描述、预设音色（Milo/Chloe）、内联表演控制 | `mimo-tts/skill.md` |

---

## 技能总览

### 1. Learning System（学习系统）

将任意主题编译为 **Learning Graph**（知识点 + 依赖关系 + 常见误解），再通过 **Runtime 主循环**（诊断 → 教学 → 练习 → 评估 → 更新掌握度 → 间隔复习）进行系统性教学。

- **Compiler 模式**：只分解知识，产出交互式学习制品（离线可打开的 HTML/看板）
- **Runtime 模式**：对话式教学循环，追踪掌握度，支持项目实战模式
- 两条路径共享同一份 Learning Graph，Graph 是唯一知识源
- 支持 QUICK（快速解释）、brief/standard/deep 三档深度

**关键参考文件：**
- `references/compiler.md` — 分解流水线与制品构建
- `references/runtime.md` — 运行时主循环与掌握度追踪
- `references/mastery-model.md` — 定性掌握度状态机定义
- `references/pedagogy.md` — 学科适配策略矩阵

---

### 2. Content-to-Video（信源转视频）

从任意信源到成品视频，五步自动流水线：

```
整理信源 → 写结构化解说稿 → 逐句 TTS 配音 → 配图 → 渲染成片
```

- **信源不绑定任何格式**：粘贴文本、上传文档（PDF/DOCX/MD）、网页链接、结构化资讯 API 均可
- **两种产出模式**：完整讲解（讲义/教材/笔记）与精选摘要（日报/资讯/播报）
- **配图自动路由**：方式 A（StepFun 文搜图）/ B（ImageGen 生图）/ C（本地图表公式）/ D（动画视频）
- **渲染唯一路径**：Hyperframes（HTML/CSS/GSAP → headless Chrome + ffmpeg），支持横竖屏
- **免费配音**：MiMo TTS `mimo-v2.5-tts`，无需额外付费

**关键参考文件：**
- `references/pitfalls.md` — 18 条踩坑记录，首次执行前建议通读
- `references/tts_pipeline.md` — TTS 管线参数与预置音色表
- `references/image_options.md` — 配图方式 A/B/C/D 详细配置
- `references/rendering.md` — 渲染调参与动画配置

---

### 3. Agnes Media（Agnes AI 媒体生成）

统一入口封装 Agnes AI 平台的两个模型：

| 模型 | 用途 | 价格 |
|------|------|------|
| `agnes-image-2.1-flash` | 文生图 / 图生图 / 图片编辑 | ¥0.02/张 |
| `agnes-video-v2.0` | 文生视频 / 图生视频 / 关键帧动画 | ¥0.035/秒 |

- 纯 Python 标准库实现，无额外依赖
- 自动重试、b64 输出优先、本地保存

---

### 4. StepFun Image（StepFun 图像生成）

封装 StepFun 三套图像 API，支持三种模式：

| 模式 | 端点 | 说明 |
|------|------|------|
| `txt2img` | `/v1/images/generations` | 文生图，推荐 `step-image-edit-2` |
| `img2img` | `/v1/images/image2image` | 图生图 / 风格迁移 |
| `edit` | `/v1/images/edits` | 图片编辑 / 局部修改 |

- 纯 Python 标准库，支持 8 种比例、4 档分辨率（1K–4K）
- 支持中文 prompt，无需翻译

---

### 5. StepFun TTS（StepFun 语音合成）

三种模型，覆盖速度与质量的不同需求：

| 模型 | 特点 | 适用场景 |
|------|------|---------|
| `step-tts-mini` | 快速、低延迟 | 实时对话、短文本 |
| `step-tts-2` | 高质量 | 正式播报、有声书 |
| `stepaudio-2.5-tts` | 情绪可控，支持 instruction | 角色扮演、情感内容 |

- 30+ 预置音色（男声 7 个、女声 8 个）
- 支持 `--emotion` / `--style` 标签（仅部分模型）
- 支持 `--timestamp` 输出逐词时间戳 JSON
- 单次请求 ≤1000 字符，脚本自动分段拼接

---

### 6. MiMo TTS（MiMo 文章配音）

基于 **MiMo-V2.5-TTS API** 的文章配音工具，将文本或 Markdown 文件转换为 MP3 语音。

- **输入灵活**：直接文本（`-t`）或 `.txt`/`.md` 文件路径（`-f`），Markdown 自动清理标记
- **智能分段**：长文本按标点/段落自动分段合成，每段 ≤300 字符（`--max-chars` 可调），单段失败跳过继续
- **风格控制**：`-v` 自由风格描述（如"温柔女声，娓娓道来"）或 `--voice-id` 预设音色（`Milo` 男声 / `Chloe` 女声）
- **内联表演**：支持 `(慵懒)` `(严肃)` 段首风格标签、`[叹气]` `[笑声]` 内联音频标签、`(唱歌)` 前缀
- **零额外依赖**：纯 Python 标准库 + `openai` + `imageio-ffmpeg`

**关键参数：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-t` / `--text` | 直接文本 | — |
| `-f` / `--file` | 文件路径（.txt/.md） | — |
| `-o` / `--output` | 输出 MP3 路径 | 自动 |
| `-v` / `--voice` | 语音风格描述 | 清晰自然的朗读声 |
| `--voice-id` | 预设音色（Milo/Chloe） | 不指定 |
| `--max-chars` | 每段最大字符数 | 300 |
| `--dry-run` | 预览分段（不调 API） | false |
| `--status` | 查看配音状态 | — |

**API 细节：**
- Endpoint: `https://api.xiaomimimo.com/v1/chat/completions`
- Model: `mimo-v2.5-ttos`
- 合成文本放 `assistant` 角色，风格描述放 `user` 角色
- 音频格式：`wav`，中文语速约 4-5 字/秒

---

## 快速开始

### 信源转视频（完整示例）

```bash
# 1. 进入技能目录
cd content-to-video

# 2. 环境自检
python scripts/pipeline.py --check-env

# 3. 写好结构化解说稿 segments_source.json
# （按 SKILL.md 第 2 步格式编写，含 opening/closing/segments）

# 4. 一键编排：TTS → 配图 → HTML → 渲染 → 校验
python scripts/run.py --source segments_source.json -o audio_output

# 竖屏短视频
python scripts/run.py --source segments_source.json -o audio_output --aspect vertical

# 横竖屏双版本一次性生成
python scripts/run.py --source segments_source.json -o audio_output --aspect both
```

### StepFun 图像生成

```bash
cd stepfun-image

# 文生图
python scripts/generate.py txt2img --prompt "星空下的狐狸" --api-key $STEP_API_KEY

# 图生图（宫崎骏风格）
python scripts/generate.py img2img --prompt "宫崎骏风格" --source photo.jpg --weight 0.5 --api-key $STEP_API_KEY
```

### StepFun 语音合成

```bash
cd stepfun-tts

# 基础合成
python scripts/synthesize.py --text "你好世界" --voice cixingnansheng --api-key $STEP_API_KEY

# 带时间戳（用于字幕）
python scripts/synthesize.py --text "今天天气真好" --voice cixingnansheng --timestamp --api-key $STEP_API_KEY
```

### Agnes 图像生成

```bash
cd agnes-media

# 文生图
python scripts/gen_image.py --prompt "赛博朋克城市夜景" --size 2K --ratio 16:9 --api-key $AGNES_API_KEY

# 图生视频（3 秒）
python scripts/gen_video.py --prompt "镜头缓缓推进" --duration 3 --api-key $AGNES_API_KEY
```

### MiMo 文章配音

```bash
cd mimo-tts

# 直接文本合成
python scripts/voiceover.py --api-key $MIMO_API_KEY -t "文章内容..." -o output.mp3

# Markdown 文件输入（自动清理标记）
python scripts/voiceover.py --api-key $MIMO_API_KEY -f article.md -o output.mp3

# 指定风格 + 预设音色
python scripts/voiceover.py --api-key $MIMO_API_KEY -f article.md -v "温柔女声，娓娓道来" --voice-id Chloe -o output.mp3

# 预览分段结果（不调 API）
python scripts/voiceover.py -f article.md --dry-run
```

---

## 依赖与安装

### 通用要求

- **Python 3.8+**（含 pip）
- **Node.js 22+**（仅 content-to-video 的 Hyperframes CLI 需要，通过 `npx`/`npm` 使用）

### content-to-video 额外依赖

```bash
# 核心依赖
pip install "openai>=1.0.0,<2.0"
pip install "Pillow>=10.0.0"

# 可选：系统无 ffmpeg 时
pip install imageio-ffmpeg

# 可选：图表/公式配图方式 C 需要
pip install matplotlib

# Hyperframes CLI（首次使用）
npm install hyperframes --registry=https://registry.npmmirror.com
```

国内网络加 pip 源加速：`-i https://pypi.tuna.tsinghua.edu.cn/simple`

---

## API 密钥管理

所有技能统一通过环境变量或全局配置文件读取密钥，**不硬编码在任何文件中**。

### 推荐方式：全局配置文件

```bash
# ~/.config/ai-video/.env（Linux/Mac）
# 或 %APPDATA%\ai-video\.env（Windows）
```

### 内容速查

| 技能 | 所需密钥 | 环境变量名 |
|------|---------|-----------|
| content-to-video | MiMo TTS（配音）+ StepFun（搜图） | `MIMO_API_KEY` / `STEPFUN_API_KEY` |
| mimo-tts | MiMo TTS API | `MIMO_API_KEY` |
| agnes-media | Agnes AI 平台 | `AGNES_API_KEY` |
| stepfun-image | StepFun 图像 API | `STEP_API_KEY` |
| stepfun-tts | StepFun TTS API | `STEP_API_KEY` |
| learning-system | 无外部 API 依赖 | — |

> **安全提示**：仓库内只保留 `.env.example`（占位模板，无真实密钥）。真实密钥只应放在上述全局配置路径或系统环境变量中，分享/打包时不要携带含真实密钥的 `.env` 文件。

---

## 目录结构

```
skills/
├── learning-system/             学习系统
│   ├── SKILL.md                 核心入口（执行流程 + 设计原则）
│   └── references/              按需参考文档
│       ├── compiler.md          分解流水线 + 制品构建
│       ├── runtime.md           主循环 + 掌握度追踪
│       ├── mastery-model.md     掌握度状态机定义
│       ├── graph-schema.md      Learning Graph 数据规范
│       ├── pedagogy.md          学科适配策略
│       ├── ui-patterns.md       交互式 UI 组件库
│       └── persistence.md       持久化层规范
│
├── content-to-video/            信源转视频
│   ├── SKILL.md                 核心工作流入口
│   ├── .env.example             环境变量模板
│   ├── scripts/                 运行时脚本
│   │   ├── run.py               一键编排（TTS→配图→HTML→渲染→校验）
│   │   ├── pipeline.py          TTS 管线（逐句合成 + timing_manifest）
│   │   ├── build_from_structured.py 结构化解说稿分句分组
│   │   ├── gen_hyperframes.py   timing_manifest → Hyperframes HTML
│   │   ├── search_images.py     StepFun 文搜图
│   │   ├── gen_charts.py        本地图表/公式/曲线（matplotlib）
│   │   ├── verify_render.py     渲染后时长/编码校验
│   │   ├── render_watch.py      渲染命令包装辅助
│   │   ├── budget.py            时长预算（plan/estimate）
│   │   ├── export_extras.py     导出章节时间戳 + SRT 字幕
│   │   ├── visual_regression.py 关键帧像素级视觉回归
│   │   ├── split_series.py      长文档 → 多集 segments
│   │   ├── gen_cover.py         竖版封面 + 候选标题
│   │   ├── check_series.py      系列跨集参数一致性检查
│   │   ├── preview.js           浏览器预览（人工看，不参与渲染）
│   │   ├── selftest.py          确定性冒烟测试（不联网不调 API）
│   │   └── _*.py                内部共享模块
│   ├── config/                  渲染配置数据
│   │   ├── theme_registry.json  配色主题（cream/dark/tech/alert）
│   │   └── template.json        布局参数（横竖屏两套）
│   └── references/              深度参考文档（按需查阅）
│       ├── pitfalls.md          18 条踩坑记录（首次执行前建议通读）
│       ├── internals.md         内部机制速览（数据流/路径/缓存）
│       ├── rendering.md         渲染调参与动画配置
│       ├── image_options.md     配图方式 A/B/C/D 配置
│       ├── tts_pipeline.md      TTS 管线参数表/音色表
│       ├── sources.md           信源接入方式
│       └── content_templates.md 内容类型举例
│   └── dev/                     维护/迭代用（不影响视频制作）
│       ├── check.py             一键 gate（selftest + eval + 校验）
│       ├── run_eval.py          离线集成测试
│       ├── evals.json           10 条 agent 行为评估用例
│       └── changelog.json       版本变更记录
│
├── agnes-media/                  Agnes AI 媒体生成
│   ├── SKILL.md                 统一入口 + 模型路由表
│   ├── agnes-image-2.1-flash.md 图像模型完整规格
│   ├── agnes-video-v2.0.md      视频模型完整规格
│   └── scripts/
│       ├── gen_image.py         文生图/图生图/图片编辑
│       ├── gen_video.py         文生视频/图生视频/关键帧动画
│       └── agnes_common.py      共享请求/重试/编码工具
│
├── stepfun-image/                StepFun 图像生成
│   ├── SKILL.md                 三种模式（txt2img/img2img/edit）
│   └── scripts/
│       └── generate.py          统一 CLI 入口
│
└── stepfun-tts/                  StepFun 语音合成
    ├── SKILL.md                 三种模型 + 30+ 音色
    └── scripts/
        └── synthesize.py        统一 CLI 入口

└── mimo-tts/                     MiMo 文章配音
    ├── skill.md                  MiMo-V2.5-TTS 配音工具
    └── scripts/
        └── voiceover.py         文本/文件 → MP3，支持风格描述 + 预设音色
```

---

## 不适用场景

- 需要真人出镜的视频
- 需要高度定制化视觉特效（粒子、3D 场景等）的视频
- 纯英文视频（content-to-video 断句规则为中文设计）
- 安全关键 procedures 的纯 AI 教学（learning-system 适用于通用知识教学，不适用于安全关键操作指导）
- 实时/流式视频生成

---

> 每个技能目录下的 `SKILL.md` 是该技能的权威入口文档，包含完整工作流、参数说明与踩坑记录。使用前请先阅读对应技能的 `SKILL.md`。
