# 第 4 步深度参考：配图方式 A/B/C/D 详细配置

本文件是 SKILL.md 第 4 步（配图）的深度参考，"按内容类型选路径"的判断与候选审阅流程见 SKILL.md 第 4 步。

## images.json 统一格式（四种方式共用）

四种方式（A 搜图 / B 生图 / C 图表 / D 视频）的配图映射都写同一份 `images.json`（默认在 `hf-project/` 下，路径相对 HTML 输出目录），`gen_hyperframes.py` 不区分图片来源、天然支持混用。统一对象格式——静态图只需 `src`，视频按需加播放控制字段：

```json
{
  "seg1": {"src": "images/seg1.png"},
  "seg2": {
    "src": "images/seg2.mp4",
    "type": "video",
    "loop": true,
    "muted": true,
    "autoplay": true,
    "playsinline": true,
    "poster": "images/seg2-poster.png"
  }
}
```

字段说明：
- `src`（必填）：媒体文件路径（相对 HTML 输出目录）
- `type`（可选）：`auto`（默认，按扩展名判断：`.mp4`/`.webm`/`.mov` → 视频，`.gif` → 动图，其余 → 静态图）/ `image` / `video` / `gif`
- `loop` / `muted` / `autoplay` / `playsinline`（可选，默认 `true`）：`<video>` 元素的属性，仅视频有效
- `poster`（可选）：视频封面图路径，视频加载前显示的静态帧

> 字符串简写 `"images/seg1.png"`（等价于 `{"src": ...}`）仍可读取——仅为旧文件/快速手写兼容；新写法与工具落盘一律用对象格式，不要两种值格式混写。

## 方式 A：StepFun 文搜图（真实实体段落的路径，快速 + 零额度消耗）

使用 StepFun 文搜图 API 根据内容标题检索相关图片（数据来自百度搜图），速度快、无需 AI 生成额度。

**使用脚本**：

```bash
# 从 manifest 自动提取内容段落标题并搜图
python scripts/search_images.py \
  -m audio_output/timing_manifest.json \
  -o hf-project/images

# 直接指定标题列表
python scripts/search_images.py \
  --titles "反向传播算法" "梯度消失问题" "Adam 优化器" \
  -o hf-project/images
```

**参数说明**：
- `-m` / `--manifest`：timing_manifest.json 路径（从中提取内容段落标题）
- `--source`：segments_source.json 路径（直接从稿件取标题，不必等 TTS 产出 manifest；与 `-m` 二选一，段落 id 按位置分配，两条输入路径效果等价）
- `--titles`：直接指定搜索标题列表（空格分隔）
- `-o` / `--output`：图片输出目录（如 hf-project/images）
- `--api-key`：StepFun API Key（两级优先级查找）
- `--topk 3`：每条内容最多下载几张候选图供当前模型审阅挑选（默认 3）
- `--min-width` / `--min-height`：过滤条件，图片宽/高需 >= 此值（滤掉过小的缩略图，默认 `0`=不过滤，示例：`--min-width 800 --min-height 600`；若全部候选都低于阈值，会回退使用未过滤的结果并打印警告）
- `--no-optimize`：关闭下载后压缩（默认自动缩放到长边 ≤1040px 并在无透明通道时转 JPEG，减小渲染开销）
- `--workers 4`：并行搜图/下载线程数（默认 4）
- `--resume`：跳过已有成品图的段落（不重新搜索/下载），`images.json` 已定稿条目与已下载候选都保留、多轮补图互不覆盖；目录里已存在成品命名（`<sid>.<ext>`）的图会被自动采纳写回 `images.json`
- `--pick "seg1:2,seg2:0"`：审阅后指定采用（数字为候选编号 1-based；`0`=不采用，改 ImageGen 生图）
- `--sids "seg1,seg3"`：只搜这些段落（逗号分隔 sid）——按 SKILL.md 第 4 步路由只有部分段落走方式 A 时用，避免给 B/C/D 段落白搜一轮；列表里的未知 sid 会打警告（防手滑写错）

**输出**：
- `images/seg1.*` 等（下载到 `-o` 指定目录；扩展名取决于来源 URL，可能是 `.png`/`.jpg`/`.webp`/`.gif`，压缩后无透明通道会转成 `.jpg`）
- `images.json`：默认写到 `-o` 目录的上一级（例如 `-o hf-project/images` → `hf-project/images.json`），映射 segment_id -> 相对 HTML 输出目录的图片路径，供第 5 步 `--images` 使用

**规则**：
1. 脚本自动从 manifest 的 segments 中提取内容段落（seg 前缀）标题
2. 标题尾部标点会被自动去除（API 对尾部标点敏感）
3. 下载的图片自动校验大小（< 1KB 视为无效，跳过）
4. 如果某条内容搜图失败，不影响其他内容

## 方式 B：ImageGen 工具（AI 生成，风格统一）

**使用 ImageGen 工具**，为每个段落生成一张横版 4:3 高画质配图（`image_size=landscape_4_3`，1152×864）。

> 比例依据：横屏图片槽是 900×700（≈1.29:1）横版卡片，`object-fit:contain` 填充下 4:3 图填充率约 96%（上下各 ~12px 模糊底），方图（1:1）只有约 78%（两侧各 ~100px）。竖屏家族只有 verse 模式、图片槽随画幅取形：标准竖屏（1080×1920）是正方形 980×980（方图填充率 100%，4:3 约 75%），紧凑竖屏 portrait（1080×1440）是 4:3 横版 980×735（4:3 图 100% 填充——1:1 方图加 300px 句子流在 1440 高度里放不下）——默认横屏优先，`--aspect both` 双画幅时 4:3 是两边兼顾的折中。

> 调用入口：ImageGen 指 agent 环境自带的图像生成能力（如 GenerateImage 工具、agnes-media skill），由 agent 在第 4 步直接调用；本仓库脚本不封装生图调用。

**Prompt 写法**：用英文描述该段核心意象，加入视觉风格关键词。有真实事件/实体的内容描述事件/实体意象；完整讲解类内容描述**概念本身的示意图/图解**（流程图、结构图、类比意象），不要为抽象概念硬凑写实场景。

| 段落 | Prompt 示例 |
|------|------------|
| IMO 满分（事件/实体类） | `A glowing golden medal engraved with mathematical formulas, IMO theme, dark navy background, digital art` |
| AI 模型发布（事件/实体类） | `Three interconnected glowing AI model nodes, blue cyan energy connections, dark background, futuristic digital art` |
| 安全事件（事件/实体类） | `A dramatic cybersecurity breach visualization, cracked digital shield, red danger theme, dark background` |
| 反向传播算法（概念讲解类） | `Clean minimal diagram of a neural network with arrows flowing backward through layers, labeled gradient flow, dark background, flat vector illustration style` |
| 供需曲线（概念讲解类） | `Simple economics diagram, two intersecting curves on an axis, equilibrium point highlighted, dark background, flat vector illustration style` |

**规则**：
1. 每张图风格统一：dark background + 数字艺术风格（讲解类建议加 `flat vector illustration style` / `diagram style`，比写实风格更适合表达抽象结构，也更不容易出现幻觉细节）
2. 图中包含与段落 accent 色相近的色调，让 glow 效果协调
3. 避免文字出现在图中（渲染后不可读）
4. 生成后重命名为 `seg1.png`、`seg2.png` 等，放在 `hf-project/images/` 下

**写映射**：生成的图重命名放进 `hf-project/images/` 后，把各段落写进 `images.json`——统一对象格式见上文「images.json 统一格式」节。

## 方式 C：图表 / 公式（真实数据，零额度消耗）

数据类段落（营收、增长率、占比、排行……）优先用这条：从信源里提取具体数值，
本地用 matplotlib 画成柱状图/折线图/饼图，风格跟方式 B 的"dark background"
约定保持一致（固定深色底，不随 `--theme` 变化），不消耗任何生成额度、不用等
网络请求。数学公式本体也走这条（`formula` 类型渲染成 SVG）：公式没有真实照片
可搜（方式 A 文不对题），AI 生图容易把符号画错（方式 B 不可靠），本地 mathtext
渲染最准确。函数曲线也走这条（`curve` 类型：表达式在受限 numpy 命名空间求值 +
真·数值 x 轴 + 多曲线同图对比，画 y=x**2 / 1.08**x 这类函数不需要手算采样点）。

**使用脚本**（`scripts/gen_charts.py`，依赖 `matplotlib`，不在核心依赖里、按需安装
——第一次用之前 `pip install matplotlib --break-system-packages`，见 SKILL.md 环境准备）：

先写 `charts.json`：
```json
{
  "charts": [
    {
      "id": "seg1",
      "type": "bar",
      "title": "2026 Q2 营收（亿元）",
      "labels": ["Q1", "Q2"],
      "values": [12.3, 15.8]
    },
    {
      "id": "seg2",
      "type": "pie",
      "title": "用户构成",
      "labels": ["个人", "企业", "教育"],
      "values": [55, 30, 15]
    },
    {
      "id": "seg3",
      "type": "formula",
      "formula": "E = mc^2",
      "title": "质能方程"
    },
    {
      "id": "seg4",
      "type": "curve",
      "title": "单利 vs 复利（10万本金·8%年化）",
      "curves": [
        {"expr": "10*1.08**x", "label": "复利"},
        {"expr": "10+0.8*x", "label": "单利"}
      ],
      "x_min": 0,
      "x_max": 30,
      "x_label": "年"
    }
  ]
}
```
```bash
python scripts/gen_charts.py -i charts.json -o hf-project/images
```

**规则**：
1. `type` 支持 `bar`（默认，适合对比几个数值）、`line`（适合趋势）、`pie`（适合占比构成）、`formula`（数学公式本体，mathtext 渲染成 `<id>.svg` 矢量图——没写 `$` 定界符会自动包一层，上下标用 `^`/`_`、分式用 `\frac{a}{b}`；中文说明放 `title`，别放进 `formula`）、`curve`（函数曲线，表达式自动求值+多曲线同图对比——每项 `{"expr": "1.08**x", "label": "复利"}`，`x_min`/`x_max` 定数值域；与 `line` 的区别是 line 为分类 x 轴+手算值，curve 才是数学函数图像）——按数据/内容的性质选，不要为了"好看"硬套不合适的类型（比如两个时间点的对比用柱状图，不要用饼图）。图表类是**配图**，走方形 `seg-image` 图片槽。
2. `id` 对应段落 id（`seg1`/`seg2`…），输出文件名自动是 `<id>.png`（formula 类型为 `<id>.svg`），跟方式 A/B 生成的图片放在同一个 `images/` 目录、写进同一份 `images.json`——各方式可以在同一期视频里混用，`images.json` 不区分图片来源。
3. 图表数值必须来自信源本身的真实数据，不要编造——这是这条路径存在的意义（跟"意象图只传达感觉"相对，图表传达的是数字本身，编造数据比配一张不太贴切的意象图伤害更大）。
4. 中文标签需要系统装了中文字体（脚本会自动探测 `Noto Sans CJK`/文泉驿等常见字体；如果生成出来中文变方块，按脚本打印的提示装字体后重跑）。
5. 几何示意图（三角形/圆/向量/角度标注）**不做成配置类型**——每种几何图需要的图元和标注（辅助线、直角符号、角度弧）差异太大，JSON 化会变成无限膨胀的绘图 DSL。需要时直接手写 SVG 放进 `images/` 目录并在 `images.json` 里引用（SVG 是文本，三角形+圆+坐标标注十几行就能写出来，`gen_hyperframes.py` 对 `.svg` 按存在性校验后即可用），或简单几何示意走方式 B。

## 方式 D：视频 / 动图配图（可选）

方式 A/B/C/D 怎么选，以 SKILL.md 第 4 步"按内容类型选路径"为唯一权威，此处不重复路由表。

**动画（方式 D）使用细则**：

- **动画优先但不要盲目**：动画观感体感最好，故事性内容最适合；但概念推演、数学公式类内容不适合动画——公式没有"动"的故事，强行动画化只会得到氛围镜头，传达效率反而不如一张准确图表。
- **多段关联动画的一致性**：同一期里多段动画讲同一个主体（同一装置、同一场景）时，prompt 要锁定主体外观、视角、风格、色调，必要时用首段成片/首帧作为后续段的参考输入，避免两段动画里"同一台装置长得不一样"。
- **时长选择**：在生成工具单次时长上限内选择贴合段落节奏的时长（段落短就别生成长视频，尾部截断浪费）；确需长动画时可拼接多段，但**单个配图优先单次生成**（拼接是例外，成本与风格一致性风险都更高）。
- **额度策略**：视频生成额度是全流程最稀缺资源，按 SKILL.md 第 4 步路由只给"故事性/动态过程"段花额度；额度耗尽时降级路径为视频段→ImageGen 静态图（选循环观感接静态的构图），不要为等额度阻塞整个制作流程。降级决策记录进制作笔记，额度恢复后可单独补做。
- **实战沉淀（PID 系列四集）**：动态过程段——离心调速器小球的张合、虚假水位的形成、水池进出水的自平衡，用 5s 循环视频的传达效率远高于示意图（观众直接看到因果链）；状态/实物/人物——历史装置外观（瓦特调速器、指南车）、人物（维纳）、场景（船舵），重点是"长什么样"，ImageGen 静态图性价比更高；数据关系——理论年表、PID 大事记、阀门特性曲线，永远用图表。

如果某个段落有现成的视频素材（`.mp4`/`.webm`/`.mov`），或想用动图（`.gif`），可以直接放进 `images/` 目录并在 `images.json` 里引用。渲染层会自动识别格式：
- 视频 → `<video loop muted autoplay playsinline>`，循环播放直到段落结束
- 动图 / 静态图 → `<img>`，行为不变

**手动放置**：

```bash
# 把视频文件放到 images 目录
cp segment3.mp4 hf-project/images/seg3.mp4
```

然后在 `images.json` 里加该段落的映射（格式见上文「images.json 统一格式」节；视频不写 `type` 也会按扩展名自动识别）。

**视频要求**：
- 建议 3-8 秒长度（与段落时长匹配，`loop` 自动循环）
- 静音素材即可（渲染强制 muted，配音由 TTS 音频轨道提供）
- 文件大小：单条 5 秒 1080p MP4 约 2-5 MB，多视频段落时总大小会显著增加
- 时长适配：视频循环播放直到段落结束——AI 生成的视频多为 5s 左右，配 20-40s 的段落会循环 4-8 次，选循环观感自然的素材（无明显开头/结尾跳变）。若视频时长超过段落时长，`gen_hyperframes.py` 生成 HTML 时会打 `[warn]`（渲染只显示前段，尾部内容被截断），此时请剪短视频或把它换到更长的段落

**AI 生成视频**：如果想让 AI 为某条内容生成视频片段，优先用 `Seedance` 插件（每日限额），备选 `agnes-media` skill（按 credits 计费）——与 SKILL.md 第 4 步"Seedance 优先，agnes-media 备选"口径一致。生成的 `.mp4` 放到 `images/` 目录后按上述方式引用；生成前建议跟用户确认额度/credits 消耗（视频生成比图片贵）。

**审阅**：候选图为视频时，请直接用 Read 打开该 `.mp4`/`.webm` 文件（或本地播放器）逐条核对内容，与静态图一样走「逐张独立审阅」流程，不再使用拼图。
