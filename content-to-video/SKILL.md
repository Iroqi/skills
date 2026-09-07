---
name: content-to-video
description: 把任意信息源（粘贴的文本/笔记、上传的文档、网页链接、结构化资讯 API）自动转成带字幕、配图和动效的解说视频——当前对话模型提炼要点写稿，逐句 TTS 配音，Hyperframes 渲染成片。适用于"把这份 PDF/文章/会议纪要/读书笔记做成讲解视频"、"做一期资讯播报"、"日报视频"、"把讲义做成系列短视频"等需求。触发词包括"把……做成视频"、"视频讲解/播报"、"资讯视频"、"帮我出个视频版"等；只要意图是输入一批文字/资料、输出一条配音字幕视频，就应使用本技能。
version: "1.5.77"
---

# 信源转视频（Content-to-Video）

从任意信息源到成品视频：整理信源要点 → 写结构化解说稿 → 逐句 TTS 配音 → 配图 → 渲染带字幕动效的成片。

> **设计原则**：本技能的核心任务有五步（**整理信源** → 写结构化稿件 → 逐句 TTS 配音 → 配图 → 按时长渲染输出），下面"核心工作流"只讲这五步。**第 1 步"信源"是可插拔的**——不管资料来自哪里（粘贴文本、上传文档、网页、某个资讯 API……），只要能被提炼成"标题+正文"的条目列表，就能进入第 2 步，后面四步完全不关心信源来自哪。其中**配图默认执行**（除非用户明确说不要配图），纯文字版渲染仅在配图来源全部不可用时作为兜底。
>
> 渲染只维护 **Hyperframes 一条路径**（HTML/CSS/GSAP，通过 headless Chrome + ffmpeg 渲染，速度快、支持竖屏）。

## 目录

- [项目结构](#项目结构)
- [环境准备](#环境准备)（依赖安装 / API 密钥）
- [核心工作流（5 步）](#核心工作流5-步)（整理信源 → 写解说稿 → TTS → 配图 → 渲染）
- [一键编排（可选）](#一键编排可选)
- [输出契约](#输出契约)
- [不适用场景](#不适用场景)
- [附录：参考文档索引](#附录参考文档索引)

## 项目结构

按"谁会用到这个文件"分层：`scripts/` 是行为（代码），`config/` 是渲染用的配置
数据，`references/` 是**执行本技能时**可能要查的深度参考（agent 面向）；
`test.py` + `CHANGELOG.md` 是**维护/迭代本技能时**才用得到的（人类维护者
面向，不影响任何一次视频制作，平时不用读）。

```text
content-to-video/
├── SKILL.md                            核心工作流入口（从这里开始读）
├── .env.example                        环境变量模板（复制为 .env 后填密钥，不进包）
├── test.py                             一键测试（确定性断言 + 离线集成 + 文档门禁，改完必跑；仅维护用，不参与制作）
├── CHANGELOG.md                        版本变更记录（发版时与 frontmatter version 同步，仅维护用）
├── scripts/                            运行时脚本（下划线 _* 为内部共享模块，不直接调用）
│   ├── run.py                           一键编排：串联 TTS→配图→HTML→校验→渲染，输出制作报告
│   ├── pipeline.py                      第 3 步 TTS 管线（合成/变速/断点续跑/timing_manifest）
│   ├── build_from_structured.py         第 2 步 分句分组库（pipeline --source 内部调用）
│   ├── check_facts.py                   第 2 步 数字漂移检查（抽全稿数字清单，供对照信源核对出处）
│   ├── gen_hyperframes.py               第 5 步 timing_manifest → Hyperframes HTML（字幕/配色）
│   ├── search_images.py                 第 4 步 A：StepFun 文搜图
│   ├── gen_charts.py                    第 4 步 C：本地图表/公式/曲线（matplotlib）
│   ├── verify_render.py                 第 5 步 渲染后 时长/编码 校验
│   ├── render_watch.py                  渲染命令解析辅助（run.py 内部用）
│   ├── budget.py                        可选：时长预算（plan/estimate）
│   ├── series.py                        可选：split=长文档→多集 segments 骨架 / check=系列跨集参数一致性
│   ├── preview.js                       可选：浏览器预览（仅人工看，渲染/校验不受影响）
│   └── _*.py                            内部共享模块：_env/_contracts/_audio/_tts/_voices/_theme/_template/_titles/_assets/_ffmpeg/_script_utils
├── config/                             渲染配置数据（与 scripts/ 分离：这是数据不是代码）
│   ├── theme_registry.json              配色主题（cream/dark，单文件装全部主题）
│   └── template.json  布局参数（横竖屏两套）+ 叙事模式预设（modes 块，chapter/flow 的全部版式差异定义面）
├── references/                         深度参考，按需查阅，非必读
│   ├── pitfalls.md                      历次踩坑记录 + 内部机制速览（首次执行前建议通读；排查路径/缓存问题查文末机制节）
│   ├── rendering.md                     第 5 步 主题语义/动画/渲染调参（fps/workers/--gpu/--quality）
│   ├── image_options.md                 第 4 步 配图方式 A/B/C/D 配置
│   ├── tts_pipeline.md                 第 3 步 TTS 管线参数表/音色表/manifest 格式
│   ├── script_format.md                第 2 步 segments_source.json 字段规范（完整语义/兜底行为）
│   ├── sources.md                       信源（aihot/文档/网页/笔记）接入方式
│   └── content_templates.md             更多内容类型举例（评测/周报/播客/书评/财报）
```

## 环境准备

### 依赖安装

- Python 3.9+（含 pip）——全部脚本只用 3.8 兼容语法，实测 Python 3.11 下 `test.py` 全绿；不要凭印象抬高版本门槛。环境自检跑 `python scripts/pipeline.py --check-env`，以它的输出为准
- Node.js 22+（含 npm）—— Hyperframes CLI 通过 `npx`/`npm` 使用，渲染步骤必需（当前版 Hyperframes CLI 明确要求 Node.js 22+，18.x 已不支持；`--check-env` 会校验版本号）

缺少 Python 依赖时**先检测再安装**，不要盲目整包安装：

1. 先跑 `python scripts/pipeline.py --check-env` 看缺什么
2. 按缺失项补装（装到运行脚本的那个 Python runtime 里）：
   - `openai`（必装）：`pip install "openai>=1.0.0,<2.0"`
   - `Pillow`（配图需要）：`pip install "Pillow>=10.0.0"`
   - `imageio-ffmpeg`（**仅当系统没有可用 ffmpeg 时才需要**）：`pip install imageio-ffmpeg`
   - `matplotlib`（**仅第 4 步配图方式 C / 图表时才需要**）：`pip install matplotlib --break-system-packages`
3. 系统自带可用 ffmpeg（`ffmpeg -version` 能跑）时，脚本自动优先用系统 ffmpeg，无需安装 imageio-ffmpeg（见 `scripts/_ffmpeg.py`）

> 上面 4 条安装命令即是全部依赖清单（核心 openai + Pillow，可选 imageio-ffmpeg / matplotlib）。
> 不额外用 requirements.txt——核心依赖直接内联安装、可选依赖按需单独装，避免 `pip install -r`
> 把用不到的包也一起装上。

国内网络可加 pip 源加速：`-i https://pypi.tuna.tsinghua.edu.cn/simple`

### API 密钥

密钥通过 `_env.py` 统一按**两级优先级**加载（所有脚本共享）：

1. **环境变量**（最高优先级）：`MIMO_API_KEY`、`STEPFUN_API_KEY` 等
2. **全局配置文件**：`~/.config/ai-video/.env`（推荐）

```
# ~/.config/ai-video/.env 示例
MIMO_API_KEY=sk-xxxxx
STEPFUN_API_KEY=xxxxx
```

> `.env` 必须是 **UTF-8 编码**。PowerShell 5.1 的 `>` 重定向产出的是 UTF-16（脚本已能容错识别，但会打警告），记事本另存为时请右下角显式选 UTF-8——编码不对轻则 key 查不到、重则整个工具报错，且报错不一定指向编码问题

- `MIMO_API_KEY`：MiMo TTS 配音使用（模型 `mimo-v2.5-tts`，免费）
- `STEPFUN_API_KEY`：StepFun 文搜图（配图方式 A）使用；`run.py` 一键编排未配置此 Key 时跳过搜图——无任何已定稿配图时退回纯文字版（即第 4 步末条的兜底例外），`images.json` 里手动补的图（方式 B/C/D）则照常带图渲染，只有逐段搜图候选不合适（`--pick 0`）时才回退 ImageGen 生图（方式 B/C/D 不依赖此 Key）

所有脚本（pipeline.py、search_images.py）都通过 `get_key("KEY_NAME", cli_value)` 统一查找，也可通过 `--api-key` 参数覆盖。本技能不依赖任何外部对话模型：TTS 用免费配音模型 `mimo-v2.5-tts`；**选题与配图评估由当前对话窗口的模型完成**（即执行本技能的 agent 本身，无论是 Claude、Codex 还是其他模型，都用自身的判断力完成，不额外调用外部对话模型）。

> **分发安全**：本技能仓库内只保留 `.env.example`（占位模板，无真实密钥）。真实密钥只应放在 `~/.config/ai-video/.env`（或系统环境变量），**分享/打包本技能时不要携带任何含真实密钥的 `.env` 文件**。

> **首次执行前必读**：`references/pitfalls.md` 收录了历次踩坑记录，文末附内部机制速览（数据流/路径/缓存），**建议在跑下方工作流前先通读踩坑部分**，遇到报错/异常现象时再按需回查。

## 核心工作流（5 步）

> **路径约定（产物一律写项目目录，不写技能目录）**：`scripts/...` 是相对技能目录的路径——从其他目录调用时先 `cd` 到技能目录，或替换为技能目录的绝对路径。但**所有生成产物**（`segments_source.json`、`audio_output/`、`hf-project/` 及其配图/HTML/成片、导出的字幕与章节文件）必须写入**用户的项目目录**（技能目录之外），**不要在技能目录内生成任何文件**——技能目录只存放技能自身的源码/配置/文档，混入制作产物会污染技能仓库、也容易在多次制作间串台。本文后续示例中的产物路径（`segments_source.json`、`audio_output/`、`hf-project/` 等）均为简写，实际执行时替换为项目目录下的显式路径（如 `<项目目录>/ch01-xxx/segments_source.json`）；多集系列在项目目录内按 `chXX-<名称>/` 每集一个子目录隔离，TTS 缓存、配图、渲染工程互不污染。

> **首次运行前**：执行 `python scripts/pipeline.py --check-env` 做环境自检（Python 依赖、ffmpeg、API key、Node.js 版本），避免跑到一半才发现环境问题。

### 产出目标先判断：完整讲解，还是精选摘要？

同样是"信源 → segments_source.json"，但**目标不同会导致第 1、2、4 步的具体做法不同**（第 3/5 步的
TTS/渲染机制完全一样，不受影响）。开始整理信源前先判断落在哪一类，或者是不是需要两者混合：

| | **完整讲解模式**（默认，文档/讲义/笔记/教材场景） | **精选摘要模式**（高密度信息场景：日报、周报、播客摘要、资讯播报） |
|---|---|---|
| 目标 | 材料本身就是要讲清楚的对象，**按结构走完整篇**，不是挑亮点 | 信源候选很多，**挑出**5-8 条最值得讲的，其余舍弃 |
| 条数 | 由材料结构决定——可能 6 条，也可能 15 条以上（一个概念一段/一个小节一段） | 通常 5-8 条，多了会显得杂 |
| 切分依据 | 材料自带的结构：目录、章节标题、逻辑步骤、概念先后顺序 | 语义价值排序（见下面"选材判断标准"） |
| 每段 `text` | 讲清"是什么 + 为什么 + 举例/怎么用"，多写几句也没问题（每句仍需按第 2 步规则断句） | 短平快，2-3 句讲清"是什么+为什么重要" |
| `title`/`tagline` | title=概念名/小节标题，tagline=章节名、"第 N 步"、来源书名等 | title=条目标题，tagline=来源/分类标签（公司、栏目、嘉宾等） |
| opening/closing 措辞 | "今天我们来讲解……"、"这一讲我们拆解……"，closing 可以加一句"下一讲我们看……"式预告 | "大家好，欢迎收看本期内容"、"感谢收看" |
| `--voice-style` | `耐心讲解，语速适中偏慢，逻辑清晰，像老师带你梳理知识点`；也可以整段改用"双人对话"（见第 2 步"双人对话段落"），一人问一人答，比单人念稿更有讲解感 | `专业播报，语速适中，语气沉稳自信` |
| 第 4 步配图方式 | 优先方式 B（ImageGen 生成示意图/图解）——抽象概念、公式、流程用真实照片往往驴唇不对马嘴；有具体实体（产品、建筑、人物）时也可走方式 A | 有真实对应实体的内容优先方式 A（文搜图找真实图片）；数据类优先方式 C（真图表） |

> **材料很长怎么办**：一份完整讲义/长论文如果拆完超过约 15-20 段，单条视频会很长（每段几秒到十几秒配音，
> 十几段下来可能是 5-10 分钟以上）。这种情况下，跟用户确认是要一条长视频，还是拆成"系列"——按章节
> 拆成几条 `segments_source.json`，分别跑完整的 5 步流程，产出多条短视频（比如"第一讲""第二讲"），
> 不要在不确认的情况下默默把长文档压缩成 5-8 条草草带过，那样会丢失讲解类内容最需要的完整性。
>
> **两种模式也可以混用**：比如"本周论文精选讲解"——先按精选模式挑 5-8 篇最值得讲的论文（每篇一段），
> 再在每段 `text` 里用讲解模式的写法把这篇论文讲清楚（不只是标题+一句话），中间地带按实际需求判断即可，
> 不必严格二选一。
>
### 第 1 步：整理信源

本技能不绑定任何特定信源——**只要能被整理成"一批带标题/正文的条目"，就可以进入第 2 步**。信源可以是下面任意一种，也可以混合使用：

| 信源类型 | 怎么拿到内容 | 适用场景举例 |
|---------|-------------|-------------|
| 用户直接粘贴/口述的文本或大纲 | 直接读用户消息里的内容 | "把下面这段笔记做成视频" |
| 用户上传的文档（pdf/docx/md/txt/pptx 等） | 用 `file-reading`（及 pdf/docx/pptx 对应技能）读取全文 | "把这份 PDF 报告做成讲解视频" |
| 网页 / 文章链接 | `web_fetch` 抓取正文 | "把这几篇文章做成视频" |
| 结构化资讯 API（如 `aihot` AI 日报/热点接口） | 按 API 文档请求，得到"标题+摘要"条目列表 | "AI 日报视频"、"AI 资讯视频" |
| 会议记录 / 转写稿、读书笔记等长文本 | 通读后自行切分要点 | "把这次会议纪要做成播报视频" |

> `aihot` 只是"结构化资讯 API"这一类信源里的一个具体示例（面向 AI 圈日报/热点场景），完整的调用方式、日期回退规则等实现细节已挪到 `references/sources.md`——需要做 AI 日报视频时按需查阅，不需要时可以完全忽略这份参考，本步骤不因为没有 aihot 而无法进行。

无论信源是什么，本步骤的产出都是同一件事：由**当前对话窗口的模型**（即执行本技能的 agent 本身）通读信源，挑出 5-8 个最值得讲的要点，写进 `segments_source.json`（下一步的输入）。**不依赖任何单独的打分脚本**——判断"哪条信息值得讲"本质是语义/价值判断，不是可以用固定权重公式模拟的确定性计算。

**选材判断标准（供当前模型参考，非强制打分公式；内容类型不同时侧重点不同）**：
1. **优先实质内容而非套话/铺垫**：具体的概念解释、结论、数据、案例 > 空泛介绍、重复表述
2. **同一主题的多处提及要合并**：信源里反复出现的同一个点只算一条，选表达最完整/最清晰的版本
3. **内容要有层次**：条目之间避免过多重叠（除非材料本身就是聚焦一件事），让观众有"逐步展开"的感觉
4. **重要性/理解门槛优先**：核心概念、关键转折、容易混淆的点优先；边缘细节、例行的背景介绍可以简化或省略
5. **可讲性**：能在 2-3 句话内讲清楚"是什么 + 为什么重要/怎么用"的条目优先；需要大量前置知识铺垫才能讲清楚的内容慎选，或拆成多条

这五条是判断维度，不是需要逐条打分相加的公式——**由模型综合判断，不要在 Python 里实现一套弱于 LLM 判断力的规则引擎**。

> **可选：先估一下时长量级**。用户如果对视频长度有要求（"做一条 3 分钟左右的"），选材之前可以先跑一下 `scripts/budget.py plan --target <目标秒数>`，粗估大概能装几条内容、每条写多深，比写完发现太短或太长再回头调整省事：
> ```bash
> python scripts/budget.py plan --target 180
> ```
> 这是启发式估算（假定的语速/字数，跟实际 TTS 时长有 ±15% 左右出入），只用来判断量级，不追求精确。写完 `segments_source.json` 之后也可以反过来核对：
> ```bash
> python scripts/budget.py estimate -i segments_source.json
> ```
> 没有时长要求时可以跳过这一步，按"选材判断标准"直接写。

### 第 2 步：写解说稿（结构化）

直接写结构化稿件（`segments_source.json`），而不是先写自由文本再让脚本反推段落结构——结构化输入从根上不存在"段落识别失败"的问题。

**从零开始写**格式如下：

```json
{
  "opening": "大家好，今天我们来讲解一个很有意思的概念——反向传播算法。",
  "closing": "希望讲清楚了，下期我们看另一个核心概念。",
  "segments": [
    {
      "title": "反向传播是什么",
      "tagline": "深度学习基础",
      "text": "反向传播是神经网络学习的核心算法。简单说，就是把预测误差从输出层往回传，一层层告诉每个参数该怎么调整。这样模型才能越训越准。"
    },
    {
      "title": "链式法则的作用",
      "tagline": "数学原理",
      "text": "反向传播的数学基础是链式法则。它让我们能把输出层的一个小误差，逐层分解到每一个权重上——知道哪个参数该往哪个方向调。"
    }
  ]
}
```

**写作规则**：
1. 专有名词使用标准写法（英文术语如 OpenAI、ChatGPT 保留原名，中文术语用通用写法），MiMo TTS 原生支持中英混合，无需音译
2. 每段 `text` 内部以句号、感叹号或问号结尾——脚本按标点断句，每句独立生成 TTS（除 `。！？` 外，分句也会在中文分号 `；`、ASCII 分号 `;` 和换行处断开；中英混合稿里的 ASCII `.!?` 也会终止句子——小数点/缩写（U.S.、Dr.、e.g.）/省略号有边界守卫不会被误切，纯英文长段不再粘成一坨），每条完整句子仍必须以 `。！？` 之一收尾
3. 断句后每句控制在 15-35 个字符（约 5-8 秒 TTS），避免过长句子导致节奏拖沓。超长句（>45 字）跑 pipeline 时会打 `[warn]` 提醒——字幕切行/拆 cue 只是显示层兜底，念稿喘不过气的问题要靠拆句根治
4. 数字用阿拉伯数字（如"3.0"、"15亿"）
5. 每段 `title` 是该条目的标题（概念名、章节标题、新闻标题、笔记要点均可），会显示在画面上；段落顺序就是 JSON 数组顺序，不需要在 `text` 里写"第N条"
6. **事实可回溯**：正文里的每个数字、百分比、金额、结论都必须能在信源里找到出处，宁可不写数字也不能编造——对数据类内容来说，编一个数字比配错一张图更严重。写完稿件后跑 `python scripts/check_facts.py -s segments_source.json` 可机械抽出全稿数字清单（含上下文），逐条对照信源确认后再进第 3 步。数据类段落优先走第 4 步方式 C（真图表），图表数值必须来自信源本身
7. **每段自包含**（信源是摘录/转写时尤其重要）：第一句必须交代清楚"主体+背景"（是谁/什么事/什么时间），最后一句给结论或落点——观众随时从中间开始看也能听懂，不能出现"它还表示…"这种无头无尾直接粘贴信源片段的句子。**引用故事/案例/比喻时尤其注意**：首次引入必须先铺“场景+前提”（人物是谁、在哪、之前发生了什么），不能从故事中段讲起——信源里的精彩片段往往依赖前文语境，摘用时要把前置场景用一两句话补出来，否则观众会觉得没头没脑（正反例见 `references/content_templates.md`"首次引入故事/案例"节）。信源缺上下文时由当前对话模型补足衔接成分（补“据 XX 消息”“在今天的发布会上”这类必要框架），补充只限衔接性信息，不得引入信源里没有的事实
8. **段间承接**：每个内容段的第一句承担从上一段过渡过来的职责，必须钩住上一段的落点——句子里要复现上一段结尾的关键词/结论/意象，让观众听出“接着刚才的讲”，而不是每段另起炉灶换话题。禁止每段都用同款句式开头，可用句式（回顾式/递进式/设问式/场景式）与例句见 `references/content_templates.md`"承接句怎么写"节。开场白说清"本期主题+为什么值得看"，结尾段做"回顾+一句收束"，这两条是承接链的头尾

> 上面的示例是"知识讲解"场景；换成其它信源时结构完全一样，只是 `title`/`tagline`/`text`
> 填的内容不同——比如资讯播报视频里 `title` 填新闻标题、`tagline` 填来源/公司名，读书笔记视频里 `tagline` 可以填书名。

**字段说明**（每个字段的完整语义与缺省兜底行为见 `references/script_format.md`）：
- `title` + `tagline` 必填：画面标题与标题下方小字（来源/章节/发言人等分类标签）。内容段落 tagline 极个别情况下留空时，pipeline 对内容段兜底为"补充阅读"、画面不会空；`opening`/`closing` 的 tagline 不兜底
- `text` 与 `body` 是两套内容：口播稿精炼口语化（念着顺），`body` 画面正文推荐手写（看着值，可从信源补口播没讲的细节，两者不必对齐）；不写 body 时兜底展示 text 分句（约 7 行视觉行预算）
- 可选段落级覆盖：`accent`（强调色，缺省按调色板顺序取）、`speed`（语速，覆盖全局 `--speed`）
- 开场/结尾专用顶层字段：`opening_title`/`closing_title`（应按主题显式设置）、`opening_tagline`/`closing_tagline`（显式给非空才显示）、`opening_body`/`closing_body`（朗读稿的提炼而非重复；给了就不再自动生成开场预告/回顾标签条）
- `opening_agenda` / `closing_recap`（默认 `true`）：自动开场内容预告/结尾回顾标签条的开关，设 `false` 得到干净的开场/结尾
- `flow: true` 自然叙事模式：无编号 badge、开场预告变轻量 chips、收尾 recap 默认关、切段用柔和 cross-fade——适合讲解/叙事类；新闻速览等板块化内容保持默认章节模式（两种模式写法差异见 `references/content_templates.md`）

> 语速默认值：`opening`/`closing` 段落默认写 `"speed": 1.2`（比正文慢一点），可用
> `opening_speed`/`closing_speed` 覆盖；正文未单独指定时跟随全局 `--speed`（默认 1.5）。

**进入第 3 步**：把 `segments_source.json` 直接交给 pipeline，逐段独立分句，不需要中间产物：
```bash
python scripts/pipeline.py --source segments_source.json -o audio_output
```

`--source` 路径对每个段落**独立**分句——结构化输入下每段是独立字符串，跨段短句合并结构上不可能发生，所以没有对应的失败模式。分句/分组逻辑在 `scripts/build_from_structured.py`（pipeline 内部调用，不需要单独跑）。

#### 可选：双人对话段落（讲解模式常用，让内容更像"两个人在帮你梳理"）

某个段落也可以不写单人 `text`，改写成 `dialogue`（多轮对话），由两个不同音色轮流念——一个抛问题/日常疑惑，另一个负责讲解，比单人念稿更有"人在帮你梳理知识"的感觉，尤其适合完整讲解模式里比较抽象的概念。

```json
{
  "speakers": {
    "host": {"voice_id": "茉莉", "label": "主播"},
    "guide": {"voice_id": "苏打", "label": "讲解", "voice_style": "耐心讲解，逻辑清晰"}
  },
  "segments": [
    {
      "title": "反向传播算法",
      "tagline": "深度学习基础",
      "dialogue": [
        {"speaker": "host", "text": "反向传播听起来很复杂，能简单说说吗？"},
        {"speaker": "guide", "text": "简单说，就是把预测误差从输出层往回传，一层层告诉每个参数该怎么调整。这样模型才能越训越准。"}
      ]
    }
  ]
}
```

**规则**：
1. 顶层 `speakers` 是 `dialogue` 段落的前提——每个在 `dialogue` 里出现的 `speaker` key 都必须在这里声明 `voice_id`（必填，从"预置音色列表"（见 `references/tts_pipeline.md`）选两个反差明显的音色，比如一男一女）；`voice_style`/`label` 可选，`label` 是画面上显示的说话人名（不传则直接显示 speaker key）。
2. 同一段里可以有任意轮次，`text` 和 `dialogue` 二选一，不要同时给。每轮独立分句（跟普通段落的分句规则一样：以 `。！？` 收尾、每句 15-35 字），轮次之间不要求句数对等。
3. 一个段落里的对话会自动拼进同一条字幕时间轴，两位说话人交替时字幕上方会叠一行说话人标签、按说话人变色，不需要额外配置。**字幕配色按说话人首次出现顺序从说话人色板取色、取完循环**（浅色/深色字幕栏各一套，色值见 `config/template.json` 的 `speaker` 块），设计上面向多人对话；同一段里 speaker 数超过色板大小时颜色才会复用，此时靠说话人标签文字本身区分。
4. 段落级的 `voice_id`/`voice_style`/`accent`/`tagline`/`title`/`body`/图片 都还是按整段生效，只有"这一句该用谁的声音念"细化到了轮次级别——所以对话段落照样可以配一张图（第 4 步），照样有标题和 tagline。
5. 不是所有段落都要用对话——精选摘要模式的播报类段落通常还是单人 `text` 更合适，对话适合需要"来回追问-讲清楚"的知识点；同一期视频里对话段落和单人段落可以混用。

### 第 3 步：运行 TTS 管线

脚本位于本技能目录下的 `scripts/pipeline.py`，直接传 `--source segments_source.json`：

> **记住一件事：反复调试时始终加 `--resume`**，已生成且时长有效的音频不会重新调用 API，省时间也省额度。缓存/断点续跑的内部机制见 `references/pitfalls.md` 文末「内部机制速览」节，日常用不到。

```bash
python scripts/pipeline.py \
  --source segments_source.json \
  -o audio_output \
  --voice-id 冰糖 \
  --voice-style "清晰沉稳的讲解风格，语速适中，语气自然，中英文表达流畅自然" \
  --gap 0.4 \
  --resume
```

**完整参数表**（核心参数逐条说明）、**预置音色列表**（8 个音色及双人对话搭配建议）与 **manifest `segments` 字段格式**（含 `speed`/`voice_id`/`voice_style` 可选字段）见 `references/tts_pipeline.md`。

**管线做了什么**：
1. 按标点符号逐句拆分脚本（英文术语保持原文，不做音译转换）
2. 逐句调用 MiMo TTS API 生成音频（WAV）
3. 用 ffmpeg -i 精确测量每句时长（imageio-ffmpeg 不含 ffprobe）
4. 拼接所有句子（中间插入静音间隔，强制绝对路径）
5. 可选：混入背景音乐
6. 输出 `timing_manifest.json`（每句的精确起止时间 + 实测总时长 + 可选段落分组）

**输出**：
- `audio_output/combined.wav` — 完整配音音频（或 `combined_bgm.wav` 如启用 BGM）
- `audio_output/timing_manifest.json` — 逐句时间清单（含 segments 分组）

> 配图默认执行，见下面**第 4 步**。

### 第 4 步：配图（默认执行）

除非用户明确说了不要配图，第 3 步完成配音后默认接着跑配图。**路由判断先于工具调用**：先按下表逐段判断该内容适合哪种方式，再分别准备——只有方式 A 的段落交给搜图（`--sids` 指定只搜它们），B/C/D 段落直接走各自路径，不要默认全部先搜一遍图。

| 内容类型 | 方式 | 一句话依据 |
|---------|------|-----------|
| 故事性、过程性（叙事、动态过程、因果链演示） | **D 动画/视频** | 观感体感最好，观众直接看到过程和因果 |
| 专业概念、数学/物理推演（公式、特性曲线、结构框图） | **C 图表/公式** | 准确、信息密度高；动画和 AI 生图都容易把严谨概念画失真 |
| 抽象概念、流程、原理（完整讲解模式典型：PDF 论文、教材知识点） | **B ImageGen 生图** | 本来就没有真实照片可搜，示意图比照片更能传达抽象结构 |
| 有真实对应实体（公司、产品、人物、事件；精选摘要模式典型） | **A StepFun 文搜图** | 检索真实网络图片，快、零生成额度 |
| 具体数据/数字（营收、增长率、占比、排行；财报解读典型） | **C 图表** | 真实数据画的图表比意象图/资料照片更有说服力 |

拿不准或内容混合两种类型时按段落分别判断，同一期里不同段落可以走不同方式（`images.json` 是按 segment id 映射的，天然支持混用）。每类的判断细节与反例（公式为什么不能交给动画/ImageGen、文搜图对抽象概念为什么文不对题等）见 `references/image_options.md`「配图路由」节；动画时长选择、多段关联动画一致性、额度降级细则见同文件方式 D 节；更多具体内容类型举例见 `references/content_templates.md`。

> **配图比例原则（四种方式统一）**：图框横竖屏统一是 √2:1（读模板 `layout.*.image.aspect`），主流素材比例里 **4:3 最接近**（contain 填充率约 94%；16:9 约 80%、1:1 约 71%、竖图更低）——四种方式统一向 **4:3 横版**靠拢：方式 A 审图时优先选 4:3/横版候选（`candidates.json` 带每张宽高，竖图 contain 后大量留白、慎选）；方式 B 固定 `landscape_4_3`；方式 C 画布 4:3；方式 D 生成视频选横版比例。填充率数据与依据见 `references/image_options.md`「配图比例原则」节。

1. **方式 A：StepFun 文搜图**（`scripts/search_images.py`，按标题搜真实图片，零生成额度，只需要 `STEPFUN_API_KEY`）。脚本先下载每条的多张候选（默认 3 张）并输出 `candidates.json`（含图片摘要和本地重合度 score 提示）——**由当前对话窗口的模型审阅判断哪张贴合主题**，再用 `--pick` 指定采用（`0`=不采用，改 ImageGen 生图）。不依赖任何外部对话模型，没有 402/额度问题。按路由只有部分段落走方式 A 时，用 `--sids "seg1,seg3"` 只搜这些段落，其余段落留给方式 B/C/D（`run.py` 对应透传参数 `--search-sids`）。
2. **方式 B：ImageGen 生图**（每张图约消耗 5-10 credits，用之前需要提醒用户）。抽象概念类内容（知识讲解、原理拆解、论文/PDF 内容）默认走这条；有真实对应实体的内容（新闻播报、产品评测）在文搜图没有合适候选时回退到这条。`run.py` 遇到缺图会停下并提示先审阅候选、`--pick` 或生图。
3. **方式 C：图表/公式/曲线**（`scripts/gen_charts.py`，本地用 matplotlib 从真实数据点画图、渲染数学公式与函数曲线，不消耗任何 API 额度）。数据类段落优先用这条；公式本体用 `formula` 类型输出 SVG；函数曲线用 `curve` 类型（表达式求值+多曲线对比）。详细说明见 `references/image_options.md`。
4. **方式 D 动画/视频**（视频生成工具：Seedance 优先，agnes-media 备选）——故事性/过程性段落的优先选项。额度与时长细则（单次生成时长内选择、长段拼接属例外、单配图单次生成优先、多段关联动画保持一致性）见 `references/image_options.md`
5. 以上路径都没有可用 Key/环境（或数据）时，才允许跳过配图直接进入第 5 步纯文字版渲染——跳过属于兜底例外，不是默认行为

> **审阅流程（两段式，仅方式 A 需要）**：`search_images.py` 第一遍只下载候选并输出清单（`[candidates]` 与 `candidates.json`，候选图落盘在 `hf-project/images/`、命名 `seg*_cand*.jpg`），不落定稿；`run.py` 检测到仍有内容段落没定稿配图时，会在提示里**逐张列出这些候选原图的全路径**——请用当前对话模型的 Read 工具**逐张、全分辨率**打开判断图↔题贴合度（不要看缩略图/拼图：360px 缩略会漏掉水印、角标、图内烧字等细节），这比拼图一审更准，代价是读图次数多一些。判断后执行：
> ```bash
> python scripts/search_images.py -o hf-project/images --pick "seg1:2,seg2:1,seg3:0"
> ```
> （数字为候选编号 1-based，`0`=不采用改生图）。之后重跑 `run.py` 即可（配图步骤会自动跳过已定稿的 `images.json`）。生成图（ImageGen）默认信任、不再逐张回看，除非观感可疑。

> **警惕标题党/SEO 缩略图**：StepFun 搜图来自公开网络，偶尔会命中带"震惊""No.1""最新版"等字眼、或带平台角标（如"B站""什么值得买"）的引流缩略图——这类图拉低可信度、也属于 AI slop 的一种。审阅候选时**一律不用**此类图，宁可 `--pick 0` 转 ImageGen 生图。

**方式 A/B/C/D 的详细配置**（`search_images.py` 参数与输出、ImageGen prompt 写法与 `images.json` 格式、`charts.json` 图表配置、视频/动图配图要求与 AI 生成视频入口）**见 `references/image_options.md`**。

### 第 5 步：渲染视频

Hyperframes 是唯一渲染路径，使用 HTML/CSS/GSAP 组合，通过 headless Chrome + ffmpeg 渲染，速度快、支持竖屏。

**安装 Hyperframes CLI**（首次使用，在项目根目录）：

```bash
# npm 安装（国内用 npmmirror 加速）
npm install hyperframes --registry=https://registry.npmmirror.com
# 或用 npx 临时调用（无需预装，但每次会下载）
npx hyperframes --version
```

> Hyperframes CLI 通过 `npx hyperframes <command>` 调用。若未预装，`npx` 会自动下载临时副本；预装后调用更快。

**生成 composition HTML**：使用本技能目录下的 `scripts/gen_hyperframes.py`，读取 `timing_manifest.json`（含 segments 分组），自动生成完整的 Hyperframes HTML。

不带配图（兜底，仅当配图来源全部不可用时）：
```bash
python scripts/gen_hyperframes.py \
  -m audio_output/timing_manifest.json \
  -o hf-project/index.html
```

带配图（默认，需先完成第 4 步）：
```bash
python scripts/gen_hyperframes.py \
  -m audio_output/timing_manifest.json \
  -o hf-project/index.html \
  --images hf-project/images.json
```

> **音频引用**：上面两条命令都不传 `--audio`——`gen_hyperframes.py` 会自动从 manifest 里的 `combined_audio` 绝对路径解析引用；**音频位于项目根之外时会被自动拷贝进 `<HTML 输出目录>/audio/` 并以根相对路径引用**（Hyperframes 不允许 `../` 越出项目根的资源路径，否则 check 报错且渲染无声）。只有你手动移动过音频文件、自动推导不到时才用 `--audio` 显式指定（路径必须落在项目根内，根相对解析）。
>
> **浏览器预览（内置，默认停在首帧）**：直接在浏览器打开 `index.html` 即可预览——预览布局为"上方画面 + 底部控制条"：画面按窗口比例缩放居中、控制条在画面下方绝不遮挡；默认不自动播放，点击"播放"（或空格键）后才开始，属用户手势、音频不会被浏览器拦截，画面与声音同步；支持播放/暂停/进度拖动/播完重播。预览脚本 `preview.js` 由生成 HTML 时自动复制到项目目录；渲染/check 走 headless（自动检测 `navigator.webdriver`）时完全 inert，不影响出片。

**GSAP 加载：默认自动本地缓存**：`gen_hyperframes.py` 默认不再直连 CDN——首次运行会把 GSAP 下载一次到 `~/.cache/content-to-video/vendor/`，之后每次生成 HTML 都从这份共享缓存拷贝一份到 `<HTML 输出目录>/vendor/gsap.min.js` 并引用它，不再依赖网络，也不再受 CDN 抖动/超时拖慢渲染（`references/pitfalls.md` #3）。只有本地缓存也没有、且这次下载失败时（比如从来没联网跑过一次、或确实在无外网的 CI/内网环境）才会回退到 jsdelivr CDN 直连——回退发生时会在 stderr 打印 `[warn] GSAP 本地缓存下载失败...`。

完全无外网的环境可以用 `--gsap-src vendor/gsap.min.js` 显式指定本地路径（需要自己提前把 `gsap.min.js` 放到 HTML 输出目录的 `vendor/` 下）。

**横屏布局策略（`--aspect landscape`，默认）**：横屏是左右分区排布——标题在上、配图靠右（默认 910×644 横版框，√2:1，整屏垂直居中于 top 540、左右各距屏 25px）、左列是正文要点卡片 + 底部字幕条（bar，横屏唯一模式）。横屏配图是**按需选择**而非全覆盖——按第 4 步路由表判断每个段落适合什么配图方式，不强制每段都配图。但“按需”的前提是**内容性质不需要图**（过渡段、纯承接段可以留空）：搜图无结果/候选不合适**不算**按需留空，应按第 4 步转 ImageGen 生图或方式 C 图表补图——分步执行时 `gen_hyperframes.py` 会对没有配图映射的内容段落打 `[warn]` 提醒（`run.py` 一键编排则是缺图直接拦截停下），不要把这行 warn 当噪音忽略。章节模式（默认）段间用 accent 色 wipe 扫场 + 左侧编号圆 badge；flow 叙事模式关 badge、用更长 cross-fade。

**竖屏（`--aspect portrait`，3:4）**：默认输出 1920×1080 横屏（`landscape`）。传 `--aspect portrait` 切到 1080×1440 竖屏——**这是唯一的竖屏画幅**（内部 `data-aspect` 写 `vertical`，全部竖屏 CSS 规则命中），紧凑留白：顶部 100px、底部 80px、图片槽统一 4:3（1:1 方图加 300px 句子流在 1440 高度里放不下），标题位置、图片尺寸、句子流钉底方式等布局整体按竖屏适配，不需要额外传 `--width`/`--height`。适用场景是抖音/快手等沉浸式竖屏 feed 的**裁切规避**：这些平台对标准 9:16 视频会按高度铺满裁掉两侧（20:9 屏每侧约 118-135px），50px 侧边距的内容必被裁；3:4 不是标准 9:16，平台会按宽度适配保留完整画面（上下留边）。选 portrait 就意味着接受"两侧完整、上下留边"的观感，适合信息密度高、两侧内容不可裁的竖屏内容。

**竖屏配图策略：内容段落优先全覆盖配图**。竖屏是三段式排布（标题在上、配图居中、字幕在下），有图段画面饱满；无图段只剩标题和一张要点卡片，先天单薄。因此选竖屏画幅时，第 4 步配图应按「每个内容段落都有图」来规划——即使内容性质偏概念/数据（横屏可只用图表或干脆不配图的段落），竖屏也应至少配一张示意图/图表。真无法配图的过渡性段落（如收尾悬念段）才允许无图，渲染器会用居中要点卡片兜底。额度紧张时按第 4 步路由优先级保内容主干段，删减次要段配图。开场/收尾页配图：`images.json` 写 `"opening"`（或 `"closing"`）键即生效，值格式与内容段一致——竖屏开场页即从「标题+预告+句子流」变为「标题+图+句子流」（预告/正文自动让位于图，垂直预算装不下并存），横屏走既有配图段版式、开场预告保留。**竖屏 flow 模式（portrait/both + 稿件 `"flow": true`）开场封面图默认必配、无需手动开启**（1.5.67 起）：flow 开场只有轻量 chips 预告，第 4 步配图规划里包含一张 ImageGen 点题封面图（风格与内容段配图一致），`run.py` 对缺 `opening` 映射的制作直接拦截提示、`gen_hyperframes.py` 分步执行时打 warn；仅配图来源全部不可用（纯文字版兜底）时才允许无图开场。**竖屏章节（正常）模式开场保持目录（agenda 本身就是画面主体），无配图义务**；`closing` 与横屏 `opening` 仍可选。

**一次性生成横竖屏两个版本（`--aspect both`）**：需要同时发布横屏（B站）和竖屏（抖音/小红书）平台时，不用把命令跑两遍——`--aspect both` 会一次性输出两套文件：HTML 层面 `-o` 指定的路径本身是横屏版，竖屏版文件名在此基础上自动插入 `.vertical` 后缀（如 `index.html` + `index.vertical.html`）；走 `run.py` 一键编排时会分别渲染两个成片——`out.mp4`（横屏）与 `out.vertical.mp4`（竖屏），并逐个用 `verify_render.py` 校验。

**字幕/内容呈现模式（按画幅绑定，没有 `--sub-mode` 选项）**：同一份口播稿/配图/cue 数据，两种画面呈现。**横屏固定 `bar`、竖屏（portrait）固定 `verse`**，不可选：
- `verse` = **歌词式句子流**（竖屏唯一形态）：段落全部句子静态渲染，当前句高亮加粗、已播句淡出，窗口随播报平滑滚动；有图段的正文要点卡片隐藏（正文信息由句子流逐句完整呈现），无图段保留要点卡片兜底；说话人不显示（cue 数据层保留 speaker/spk 字段，导出 SRT 仍带 `[说话人]` 前缀）。窗口钉在内容区底部（高与内边距读模板 `verse` 块）。
- `bar` = **经典形式**（横屏唯一形态）：底部字幕条（当前句随播报切换、多行错峰淡入）+ 正文要点卡片始终显示（正文字号小于字幕字号，口播字幕是画面主导文字——具体值见模板 `layout` 块的 body/subtitle 字号）；双人对话在字幕条上方显示说话人标签（颜色按字幕条背景亮度自适应深/浅配色）。

怎么选：模式与画幅绑定、不需要选——想要 bar 的"完整要点清单常驻 + 说话人身份可见"（教学步骤、操作指南、双人对话）就用横屏出片；竖屏沉浸式 feed 内容选 portrait。

**主题配色（`--theme`）**：默认 `cream`（米白色科技风：暖米白背景 + 冷蓝灰网格线 + 石墨黑文字）。当前可选 `cream` / `dark` 两个（1.5.68 奥卡姆剃刀：原 tech 与 dark 观感难分、并入 dark，原 alert 语义过窄、删除——警示感用红色 accent 表达；`config/theme_registry.json` 是唯一权威来源，加新主题只改这一处，CLI 的 `--theme` choices 会自动同步，不会出现"改了注册表、命令行还报不认识这个选项"的漂移）。只影响背景渐变/网格线/正文文字/body 背景色/句子流文字色，不影响每段的 accent 强调色。

> **按内容基调选主题**：两个主题各适配哪类内容、由当前对话模型在写稿阶段凭语义判断，以及"逐条内容的版式变体尚不支持单独指定"的说明，见 `references/rendering.md`。

> 若 manifest 不含 `segments` 字段（只有手写 manifest 才会发生——pipeline 产出的 manifest 一定带），`gen_hyperframes.py` 会自动按每 5 句一组生成段落（id 为 `seg1`/`seg2`/...，accent 统一取调色板默认色——以 `config/theme_registry.json` 的 `_accent_palette` 首色为准，不要在这里写死数值）。这是兜底行为，没有正确的标题和 accent 配色，正式制作不要依赖。

**内置动画（始终生效，不依赖配图）**、开场"内容目录"/结尾"回顾"标签条的自动生成规则、`--images` 配图额外效果与生成的 HTML 结构，见 `references/rendering.md`。

> 关键 CSS 约定速记：子元素（tagline、body-text）不要设 `opacity:0`（父级 clip 的 `opacity:0` 已控制可见性）——规则细节见 `references/pitfalls.md` #3。

**渲染命令（快照检查 → 人工审帧 → 正式渲染）**：`npx hyperframes check --snapshots` 抓取标注关键帧后，**必须用 Read 工具逐张打开关键帧 PNG 人工审帧**（每个内容段落至少 1 帧、开场/结尾各 1 帧——查配图是否贴合对应段落、标题/正文是否溢出画布、是否与配图重叠被裁切；视觉审帧是版式问题的唯一防线，不是"跑完命令就算过"），确认无问题再进 `render_watch.py` 包装渲染 → `verify_render.py` 校验。完整命令序列，以及帧率（默认 24fps）、抓帧 worker（默认 6，实测数据见 references/rendering.md）、`--gpu` 实测结论、`--quality` 档位等逐条说明，见 `references/rendering.md`。`run.py` 另有两个迭代提效开关：HTML 未变时自动跳过 check（`--force-check` 强制重跑）、`--reuse-render` 在 HTML/音频/渲染参数都没变时复用已校验成片直接跳过渲染。

## 一键编排（可选）

日常不想逐步敲命令时，可以用薄编排脚本 `run.py` 把第 3→5 步串成一条命令
（TTS → 配图 → HTML → check → 渲染 → 校验）：

```bash
python scripts/run.py --source segments_source.json -o audio_output

# 需要更高流畅度时换 30fps
python scripts/run.py --source segments_source.json -o audio_output --fps 30

# 纯文字版 / 只跑到 HTML 为止（调试用）
python scripts/run.py --source segments_source.json -o audio_output --no-images --until html
```

常用参数：`--no-images`（跳过配图）、`--fps`（默认 24）、`--quality`（默认 standard）、`--workers`（默认 6）、`--gpu`（有硬件编码器时开启）、`--no-check`（跳过快照 QA）、
`--force-check`（HTML 未变也强制重跑 check）、`--reuse-render`（成片复用：输入与参数都没变时跳过渲染）、`--until {tts,images,html,render}`（调试）、`--aspect`/`--theme`/`--speed`/`--voice-id`
（透传给下游脚本）、`--no-verify`。

其余几个按场景用得上、容易被忽略的参数：

| 参数 | 作用 | 什么时候需要 |
|------|------|-------------|
| `--project <目录>` | HTML/渲染工程目录（默认 `<-o 同级>/hf-project`） | 想把渲染工程和音频输出分开放、或一稿渲多个版本时 |
| `--search-sids "seg1,seg3"` | 只对列出的段落跑文搜图 | 按第 4 步路由只有部分段落走方式 A 时（其余段落走 B/C/D） |
| `--refresh-images` | 配图不复用，全部重新搜索/下载 | 换了一批候选想重搜；默认 `--resume` 会跳过已有成品图的段落 |
| `--no-resume` | TTS 不复用已生成的音频 | 改了音色/语速要整稿重录（默认复用会让改音色不生效） |
| `--loudness -16` | 对最终音频做响度归一化（LUFS，透传 `pipeline.py`） | 投递平台有响度要求时；默认不做归一化。开启后成片音频为 `combined_loud.wav` |

> **注意 `--workers` 有三个同名参数，默认值不同，别串了**：`run.py` 的是**渲染抓帧** worker（默认 6）；`search_images.py` 的是**并行搜图/下载**线程（默认 4）；`pipeline.py` 的是**并行 TTS 调用**数（默认 4）。三个同名不同义，写命令时看清传的是哪个脚本。

> **卡住时有兜底，不用干等**：`check --snapshots` 这一步有 300s 墙钟上限（Chrome 卡在关闭阶段时不再无限阻塞），并行阶段（TTS + 配图）有 1 小时整体上限。超时会收掉子进程并报出卡在哪一步。稿件特别长时可用 `CTV_CHECK_TIMEOUT` / `CTV_PARALLEL_TIMEOUT`（秒）调大。

> **产物路径守卫**：`run.py` 会检查 `-o`/`--project` 是否落在技能目录内，是则直接报错退出——产物混进技能目录会污染仓库、也容易在多次制作之间串台。用脚本绝对路径从你的项目目录调用即可（不需要 `cd` 到技能目录）。

`run.py` 生成 HTML 后会自动跑 `check --snapshots`（关键帧 QA）再渲染——注意一键编排**不会在审帧处停下等人**，要在渲染前人工把关版式，就用 `--until html` 停在 HTML 阶段，自己跑快照并按第 5 步要求逐张审帧后再重跑 `run.py`（TTS/配图有断点缓存，重跑不重复消耗额度）。`run.py` 只是**组合层**，不改变任何子脚本的
独立性——需要逐步执行时完全可以绕开它。`run.py` 的自动配图走方式 A 文搜图（按第 4 步路由只有部分段落走方式 A 时，加 `--search-sids "seg1,seg3"` 只搜它们，其余段落按路由走 B/C/D、由缺图拦截兜住），需要 `STEPFUN_API_KEY`，没有时跳过搜图——无已定稿配图则退回纯文字版；方式 B/C/D 不依赖此 Key，手动补图写进 `images.json` 后重跑即带图渲染（缺映射的内容段落仍会被缺图拦下，提示按 ImageGen/图表补图，而不是去跑需要 Key 的搜图）。
如果某条内容搜图结果不合适/无结果，`run.py` 会停下并列出需要 ImageGen 补图的内容段落；
生成图片放进 HTML 输出目录下的 `images/<sid>.png`（即 `hf-project/images/<sid>.png`）后，还需让 `images.json` 里有该 sid 的映射再重跑——只放图不写映射仍会被缺图拦截：重跑 `search_images.py --resume` 会自动采纳目录里已存在的成品图写回 `images.json`，或按 `references/image_options.md` 的「images.json 统一格式」节手写映射。

> **第 3/4 步并行**：配了 `STEPFUN_API_KEY` 时，`run.py` 会把 TTS（第 3 步）和配图搜索（第 4 步）同时启动，而不是等 TTS 完全跑完才开始配图——两步都只依赖 `segments_source.json`，互不等待对方产物（配图这时走 `search_images.py --source` 直接从稿件取标题，不是等 `timing_manifest.json`）。省下的是配图搜索那部分的墙钟时间，不影响任何产出内容。如果自己逐步敲命令而不用 `run.py`，也可以手动这样并行：
> ```bash
> python scripts/pipeline.py --source segments_source.json -o audio_output --resume &
> python scripts/search_images.py --source segments_source.json -o hf-project/images --resume &
> wait
> ```
> （上例为 bash 语法；Windows PowerShell 没有 `&` 后台与 `wait`——开两个终端分别跑这两条命令，或改用 Git Bash。）
> `search_images.py` 的 `-m/--manifest`（从 `timing_manifest.json` 取标题）和 `--source`（直接从 `segments_source.json` 取标题）两种输入二选一，效果等价（`id` 按位置分配为 `seg1`/`seg2`/...，两条路径的取值规则完全一致）。日常制作只有 `segments_source.json` 时直接用 `--source`；只有手里只剩一份 manifest（比如复用历史产物）时才需要 `-m`。

## 输出契约

| 信号 | 含义 |
|------|------|
| `audio_output/combined.wav` | 完整配音音频（纯人声） |
| `audio_output/combined_bgm.wav` | 混入 BGM 的音频（仅 --bgm 时生成） |
| `audio_output/combined_loud.wav` | 响度归一化后的音频（仅 `--loudness` 时生成） |
| `audio_output/timing_manifest.json` | 逐句时间清单（含实测总时长 + 可选 segments 分组） |
| `hf-project/images.json` | 配图映射（第 4 步产出，供第 5 步 `--images` 使用） |
| `python scripts/verify_render.py -f <mp4> -m <manifest>` | 渲染校验通过（时长匹配 + H.264 + AAC），exit 0 |
| `exit 0` | 成功 |
| `exit 1` + stderr | 失败，查看错误信息 |
| 渲染成功 | 输出 MP4 文件，含 H.264 视频 + AAC 音频 |

## 不适用场景

- 需要真人出镜的视频
- 需要高度定制化视觉特效（粒子、3D 场景等）的视频
- **纯英文视频**：预置的 `Mia`/`Chloe`/`Milo`/`Dean` 等英文音色仅用于中文稿件中偶尔出现的英文术语（如产品名）朗读，断句规则（中文标点）也是针对中文稿件设计的。若整篇稿件是纯英文或以非中文语言为主，断句会失效，此技能不适用
- 实时/流式视频生成

## 附录：参考文档索引

均为按需查阅、非必读（首次执行前建议先通读 `references/pitfalls.md`）：

- `references/pitfalls.md` — 历次踩坑记录 + 文末「内部机制速览」节（数据流全景/路径解析/缓存续跑）；日常制作不必逐条看，遇到报错或异常现象时按需回查
- `references/tts_pipeline.md` — 第 3 步 TTS 管线参数、预置音色、manifest 格式
- `references/script_format.md` — 第 2 步 segments_source.json 全部字段的语义与缺省兜底行为
- `references/image_options.md` — 第 4 步 配图方式 A/B/C/D 配置与配图路由展开
- `references/rendering.md` — 第 5 步 主题语义/动画/渲染调参（fps/workers/--gpu/--quality）
- `references/sources.md` — 信源（aihot/文档/网页/笔记）接入方式
- `references/content_templates.md` — 更多内容类型举例（评测/周报/播客/书评/财报）
