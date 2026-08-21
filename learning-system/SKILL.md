---
name: learning-system
description: >
  Tutor and learning-workflow skill for turning a topic or source into a
  dependency-aware Learning Graph, then teaching, practicing, reviewing,
  quizzing, building a project while learning, or producing an interactive
  learning workspace/offline artifact with evidence-based mastery tracking. Use for requests
  such as "teach me X", "explain X" when it has multiple testable concepts,
  "quiz/practice/review X", "check my mastery", "continue my learning", or
  "make an interactive lesson/lab". Prefer a quick explanation for simple
  questions. Do not use for generic calculators/trackers, slide decks, physical
  or audio-feedback skills, pure art, hardware-dependent work, rapidly changing
  API reference, or safety-critical procedures.
---

# Learning System

核心:概念分解/连接/排序方法论(知识架构) + 围绕掌握度的学习循环。代码/交互是手段,不是目的。

**默认走 Runtime,Compiler 是它按需调用的教学脚手架:**
- **Runtime**——默认路径。消费 Graph,运行"诊断→教学→练习→评估→更新→调度下一个"循环,追踪掌握度、做间隔复习。循环内需要制品时按 `references/runtime.md` §3.11"循环内要制品"调用 Compiler 子例程(契约见 `references/compiler.md` 头部)生成,生成完返回主循环继续。见 `references/runtime.md`。
- **Compiler**——把主题编译为 Learning Graph,并可产出交互式制品。只编译知识、不教学不评分。**用户明确要求"只要能离线打开的成品、不用陪练"时**,可跳过 Runtime 直接单独进入。见 `references/compiler.md`。

两条路径共用同一份 Learning Graph(只分解一次)。Graph 字段定义见 `references/graph-schema.md`。

---

## 设计原则(简)

- **Graph 是唯一知识源。** 新知识只能由 Compiler 生成或经确认的 PATCH 合并进去;Runtime 只读 Graph,不直接改它,只能提议 PATCH。
- **Compiler 不做教学判断,Runtime 不做知识分解。** 两者各管一段,谁都不越界。
- **Mastery 更新要有真实证据。** 用户一句"我懂了"不算数,要有探针/练习/复习的实际作答支撑(有代码可跑时以实际运行结果为准)。
- **用户体验优先于流程完整性。** 用户明显只想要个简单答案时(QUICK)、或要求降档(brief)时,不强行走完整流程。

这几条冲突时按这个顺序判断;不需要额外的"不变量登记表"。

**语言:** 本 skill 全部文档、GATE 提示文案与示例均用中文书写(字段名/状态/事件等技术标识符保留英文),但这只是内部设计语言。对用户的全部输出——快速解释、GATE 确认文案、Learner View 进度展示、讲解与出题——始终使用用户当前对话使用的语言,不因本文档书写语言而改变。

---

## 平台原生能力(全局规则,后文各节均引用此处)

两类平台能力,统一在这里定义一次:

1. **用户选择/确认场景**(GATE-1/2/3、CLARIFY 方向选择、QUICK 升档、ROUTE 路径选择、PATCH 确认、Runtime 主循环中适合离散选项的提问):优先用交互式点选组件,而不是"打印编号列表 + 等打字回复"。**例外:** 需要用户组织语言解释推理过程的开放式问题(EVALUATE 依赖这类回答),不强制套成选择题。
2. **讲解/对比场景**(TEACH 解释概念、依赖展示、PREDICT 出题、REINFORCE 对比讲解等):优先内联渲染示意图,而不是只用纯文字,也不是直接落盘成文件。

**判断能不能用:** 交互式选项和内联可视化不是必需依赖。使用前探测当前会话是否提供相应能力；可用就使用，不可用就立即降级为清晰的纯文字或静态 SVG/HTML，不要因能力缺失阻塞主流程。

**与“离线可带走的正式制品”是两码事:** 内联可视化是“对话里临时给用户看一眼,不落盘”;正式制品是“用户明确要求能离线打开、能带走分享的成品”。用户没明确要制品时,教学过程中的临时可视化优先用内联渲染,不要每次都走 compiler.md 文件管线。口诀:**临时演示用内联工具,带走的成品走 compiler.md。**

### 交互 UI 升级规则

采用“对话编排 + 局部 UI”混合模式,不要把每条消息都改造成 UI。UI 只负责选择、操作、反馈和状态展示；对话继续负责理解目标、解释概念、追问和开放式推理。需要交互式选项、题型卡片、学习看板或动态 HTML 时读取 `references/ui-patterns.md`。

- 一个 UI 表面只服务一个当前决定，优先使用最小组件。
- 每次点击、提交或操作都必须转成事件，再交给 Runtime 判断；UI 不直接修改 Graph 或 mastery。
- 选择题、排序题和操作题可以丰富证据形式，但单次 UI 答对不能直接升级为 Mastered。
- “实时”默认指事件级更新；不要假设 HTML 能直接访问网络、后端或固定宿主 API。
- UI 不可用时必须能回退为文字或静态图，不能因可视化能力缺失阻塞学习。

本节与"平台原生能力"节是摘要;能力分级(L0-L4)、题型证据限制、交互事件协议等细则以 `references/ui-patterns.md` 为准,两边不一致时以后者为准。

---

## 加载路线图

本 skill 按需加载。SKILL.md 始终在上下文中,references 按以下路线按需读取:

```
用户请求
  ├─ 简单解释("讲讲 X") → QUICK,不加载任何 reference
  ├─ 系统学习/练习/出题 → PARSE→CLARIFY→DECOMPOSE→ROUTE(仅 SKILL.md)
  │    ├─ CLARIFY / DECOMPOSE 细则 → compiler.md §CLARIFY / §DECOMPOSE
  │    └─ Graph 字段 → references/graph-schema.md
  │    └─ 需要 UI 题型/事件 → references/ui-patterns.md
  ├─ 要交互式制品/学习看板 → compiler.md + references/ui-patterns.md(按需读取相关小节)
  ├─ 要对话式练习 → references/runtime.md + references/mastery-model.md
  │    └─ 需要 UI 题型/事件/看板 → references/ui-patterns.md
  │    ├─ 生成学习计划(PLAN)→ compiler.md §C0.5
  │    ├─ 学科适配 → references/pedagogy.md
  │    └─ 跨 session → references/persistence.md
  └─ 边做项目边学 → references/runtime.md §7
```

`compiler.md` 较长(标题按"第一部分…第八部分"分区),只读当前需要的那一段即可,不必整篇加载。每个 reference 文件顶部声明"何时读取"。

**阶段顺序:**

| 阶段 | 一句话职责 | 详见 |
|------|-----------|------|
| QUICK(可选前置) | 简单问题直接给出简短解释,不生成 Graph | 本文 §QUICK |
| PARSE | 识别输入形式与来源,读取内容,记录学习者背景 | 本文 §PARSE |
| CLARIFY | 判断目标是否够具体,不够则给选项收窄 | `references/compiler.md` §CLARIFY |
| DECOMPOSE | 把已清晰的目标编译为 Learning Graph | `references/compiler.md` §DECOMPOSE |
| ROUTE | 默认进 Runtime;仅用户明确要离线制品时单独进 Compiler | 本文 §ROUTE |
| PLAN(仅 Runtime) | 把 Graph 排成 Session 学习计划 | `references/compiler.md` §C0.5 |
| 运行中(Runtime 主循环) | 逐概念诊断→教学→练习→评估→更新 | `references/runtime.md` §3 |

---

## 执行检查(每次响应前自检)

- [ ] 手上还没有 Learning Graph 之前,不开始**系统性**讲解——先走完 PARSE→CLARIFY→DECOMPOSE→ROUTE。**例外:** QUICK 允许不生成 Graph 回答简单概念问题,但回答后必须给升档选项。
- [ ] 主题是宽泛词且未澄清目标时,不要自行猜测切片直接分解——先问(见 `references/compiler.md` §CLARIFY 触发条件)。
- [ ] 判断模式(ROUTE)之前,不要提前写 HTML 或提前进入 Runtime 主循环——先确定用户是否明确只要离线制品,不明确就默认走 Runtime。
- [ ] **Runtime 主循环每次出题/讲解前选择合适的呈现形式。** 离散选项优先使用可用的选择组件；需要开放推理就保留开放式回答；空间结构、状态变化或多方案对比优先配图。组件不可用时立即使用编号选项、文字解释或静态 SVG/HTML，不因工具缺失阻塞教学。
- [ ] 使用 UI 时先读取 `references/ui-patterns.md`，为当前题目生成唯一 `question_id`，并确保用户操作能回到 Runtime 事件处理链。

不满足以上任一条 → 退回对应阶段补做。

---

## 进度展示(默认给学习者看的版本)

默认把内部字段译成自然语言进度提示,不出现字段名/英文标签/状态机术语。用户主动要求看内部执行细节时,才展示阶段名+当前动作这类调试信息,并告知用户说"不用看这些了"就能切回去。

---

## QUICK:快速解释前置路径(可选)

**触发:** 用户问"什么是 X"/"讲讲 X"等简单解释请求,且未明确要求系统学习/练习/出题/制品。含多个子问题则进 PARSE。

**"教我 X"类措辞:** X 已框定具体概念(如"教我贝叶斯定理")→ 进 PARSE。X 是裸工具/领域名(如"教我 Docker")→ 默认 QUICK。

**例外(即使 X 是宽泛词也跳过 QUICK 直接分解):** 含系统学习信号词("系统学"/"从头到尾"/"全面深入"/"打好基础"/"陪我练习"/"出题测试"/"帮我复习"/"边做项目边学")、本轮已表达系统学习意图、输入是概念簇或已有 Graph/progress 文件。

**执行:**
1. 直接解释(3-5 句,聚焦"是什么+为什么重要+一个关键直觉")
2. **(可选)路线图预览:** 主题有明显子结构时,在升档问题前先给一行预览(如"往下学大概会碰到:模型怎么训练的、怎么做推理、常见的坑在哪几个"),不生成正式 Graph、不存文件
3. 给升档选项——默认三个方向"拆解知识点/出题练习/做交互式页面";主题适合动手搭建(存在微型项目/模拟场景可自然覆盖核心概念,如编程/工具类主题,判断标准与 `compiler.md` C2"项目驱动模式"一致)时追加第四个方向"边做项目学"。按§平台原生能力用组件或文字降级
   - **主题感知版(命中以下任一形状换成针对性理由):** 空间/结构关系→"直接看图比读文字直观";动态过程→"一步步怎么变,自己点着看更合适";多方案对比→"并排摆开更容易看出取舍";反直觉结论→"自己动手试一次比我讲更有说服力"
   - 命中多条选最贴切一条;都不命中用通用版"想深入学这个吗?我可以帮你拆解知识点、出题练习、或做一个交互式页面"(主题适合动手搭建时追加"、或边做一个小项目学")

**升档规则:** "好/继续/深入"→进 PARSE;追问相关概念→保持 QUICK;明确要求练习/制品→跳到对应模式;明确要求边做项目学→进 PARSE 后 ROUTE 时直接命中 `runtime.md` §7 项目实战模式,不再走"孤立知识点"主循环判断。不升档时不生成 Graph、不进入 Runtime、不写 progress 文件。

**与 `brief` 深度档的区别:** `brief` 是下面"学习深度"里的一档,仍会生成精简 Graph(3 个概念);QUICK 是分解前的可选前置,连 Graph 都不生成——两者完全不同。

---

## PARSE:解析输入

**职责:** 识别输入形式与来源(主题词/URL/文件/PDF/概念簇/已有产出物),读取内容,记录学习者背景设置。**不做:** 不判断目标是否需澄清(那是 CLARIFY 的职责)。

| 输入类型 | 处理方式 |
|---------|---------|
| 纯主题词 | 直接进入 CLARIFY |
| URL | 用当前会话的 web/browser 能力抓取正文;失败/空/登录付费页请用户粘贴文本 |
| 本地文件 | 用当前会话的文件能力读取;失败则告知原因,请用户重新提供路径或粘贴文本 |
| PDF | 优先用 pdf 技能解析;报错/乱码/超 50 页则请用户粘贴关键段落 |
| 粘贴文本 | 同文件处理流程 |
| 概念簇(已给出概念列表) | 跳过"识别核心概念",从"映射依赖"开始 |
| 已有 `{topic}-learning-graph.md` | 读取复用,跳过分解进 ROUTE;损坏则告知用户重新分解 |
| "继续上次学习"/上传 progress 文件 | 读取 `references/persistence.md` 恢复;失败则询问重新开始 |

**学习者水平:** 未声明按初学者。用户提及背景/已掌握概念时记入 `meta.learner_profile`——对应概念的预掌握初始化规则见 `mastery-model.md`「初始化」;后续表现不符可随时下调。

**学习深度(brief / standard / deep,默认 standard):**

**触发条件(命中任一即切换,未命中走 standard):**
- **brief**:用户明确说"时间紧/只要概览/快速过一遍/不用太细";或用户主动要求降档(Runtime 主循环中触发词见 `runtime.md` §3.11 LOOP CONTROL);或单次会话时长明显受限(如用户声明只有 10 分钟)
- **deep**:用户明确说"完整/系统学/不要遗漏/深入原理/彻底搞懂";或 `brief`/standard 过程中用户主动要求"再深入/讲细一点/别跳过"
- 仅凭主题本身宽窄不自动切档,需用户措辞或场景信号命中以上条件

**各档行为差异:**
- **brief**:跳过 CLARIFY 追问(判断本身不跳,宽泛词默认选 beginner overview);跳过 DECOMPOSE 逐条确认清单,直接给 3 个核心概念精简 Graph;GATE-1 降为一句话确认(动作不可跳);Applied 后允许停在 Applied 不进复习排期(详见 `runtime.md` §3.11、§4 Review Engine 的 brief 分支)
- **standard**:默认流程
- **deep**:不设裁剪阈值,走完整 compiler.md 制品流程;仅概念 >12 时建议分组或拆分
- 深度选择只影响颗粒度和确认详略,不改变 Graph 数据结构

---

## CLARIFY / DECOMPOSE

**详细规则见 `references/compiler.md` §CLARIFY 和 §DECOMPOSE。** 此处只给边界声明。

**CLARIFY 职责:** 判断当前主题/目标是否清晰到可以直接分解——清晰或虽宽泛但不同目标下核心概念本就收敛则跳过;真正不清晰(宽泛且目标会导致选不同概念)才给 2-4 个选项收窄。**不做:** 不解析输入格式(PARSE 已做完),不产出 Graph 本体(DECOMPOSE 的职责)。

**DECOMPOSE 职责:** 把已清晰的目标编译为 Learning Graph——核心概念、依赖关系、常见误解、关键洞察。**不做:** 不重新判断目标是否清晰,不解析原始输入格式。

**GATE-1(强制门禁,DECOMPOSE 后):** 概念清单+依赖+误解译写成自然语言展示,用"我们"开场。按§平台原生能力:有交互式选项组件就用(能配概念依赖关系图一并展示更好);没有用文字降级方案——给出清单+"这个顺序可以吗?",末尾追加:
```
⛔ 等待你的确认
```
标记之后不允许出现任何内容(包括预判同意而提前写的后续教学内容)。`brief` 深度档降为一句话确认,但确认动作不可跳过。

---

## ROUTE:判断模式

Graph 拿到手后判断走哪条路。**默认进 Runtime**,只有一种情况单独进 Compiler:

- **离线制品逃生舱口:** 用户明确说"只要能离线打开的成品/不用陪练/给别人看"→单独进 **Compiler 模式**,读 `references/compiler.md` 从 C0 走完整流程,产出后即结束。**命令行工具操作类主题**(Git/Docker CLI/Linux 命令)主动告知 HTML 模拟终端的局限,提示项目实战模式更贴近真实,除非用户仍坚持要离线制品
- **其余情况(默认)**——进 **Runtime 模式**,读 `references/runtime.md`,用 Graph 初始化状态,进入逐概念主循环:
  - 孤立知识点(对话式练习/复习)→ Tutor Runtime 主循环；需要交互 UI 时按 `references/ui-patterns.md` 生成事件和状态投影
  - "边写/边搭真实的东西"→ runtime.md §7 项目实战模式(按里程碑逐个解锁,边做边学;有真实代码可跑时用当前会话的 shell/执行能力实际运行验证)
  - 循环内调用 Compiler 子例程生成制品的条件见本文开头一节,此处不重复

**用户中途切换:** Runtime→Compiler 保存 progress 文件;Compiler→Runtime 用已有 Graph 初始化。切换无需重新分解,Graph 复用。

**两类文件不要混:** `{topic}-learning-graph.md`(概念+依赖+误解,主题不变不重新生成,Runtime/Compiler 共用)、`{topic}-progress.md`(Runtime 维护的 mastery 运行时数据)。

---

## 关键门禁(确认)

纯 markdown 阶段无外部 hook 强制流程,关键节点用**人在环确认**代替 LLM 自律。展示给用户的内容遵循自然语言,不出现内部字段名/模式名/状态机英文标签。

| 门禁 | 位置 | 强度 | 必须确认什么 |
|------|------|------|-------------|
| **GATE-1 Graph 确认** | DECOMPOSE 后 | 🔴 强制 | 见上文 CLARIFY/DECOMPOSE 节 GATE-1 定义(含 ⛔ 硬阻断标记与 brief 降档规则),不在此重复 |
| **GATE-2 模式确认** | ROUTE 后 | 🟡 建议 | 自然语言告知接下来怎么进行。短 session 必确认;长 session 已表达过意图可不重复;**紧接 GATE-1 且默认进 Runtime、无歧义时,与 GATE-1 合并成一次确认**(如"…这个顺序可以吗?确认后我们就开始一起练习"),不再单独停顿一次——避免相邻两个确认点把开场拆成两轮等待 |
| **GATE-3 Mastered 检查** | runtime.md §6 REFLECTION 通过后 | 🟡 建议 | 自然语言告知"已掌握"判断+证据，给用户一次纠偏机会；默认不阻塞状态落盘 |

**执行:** GATE-1 是硬阻断：未获确认不得继续生成教学内容。GATE-2 和 GATE-3 是非阻断检查点：用户已明确表达继续意图或证据已充分时可简短告知后继续，否则只询问一次。用户提出异议时退回对应阶段修正；不要把“继续”当作 mastery 证据。

---

## PLAN:生成学习计划(仅 Runtime)

进入 Runtime 后,先用学习顺序排序生成计划,再初始化 Runtime 状态。按 `references/compiler.md` §C0.5 将 concept 分为 Session 列表,输出作为 Runtime 初始化参数,并写入 progress 文件快照的 `[PLAN]` 段(见 `references/persistence.md` §2,供学习看板与跨 session 恢复读取),不另建独立计划文件。

---

## 能力探测与降级

不要把某个客户端的工具名写进流程假设。先检查当前会话提供的能力，再按下表选择；能力不可用时使用同一行的降级方案，继续主流程。

| 需要的能力 | 使用方式 | 不可用时 |
|---------|-----------|---------|
| 读取 URL/外部资料 | 使用当前会话的 web/browser 能力 | 请用户粘贴正文或关键段落，并标明未抓取到原文 |
| 读取/写入/编辑文件 | 使用当前会话的文件与补丁能力 | 在对话中给出最小可复制内容，不声称已保存 |
| 运行代码或命令 | 使用当前会话的 shell/执行能力，并复述关键结果 | 退回口头推演，明确说明未实际运行验证 |
| 交互式选项确认 | 使用当前会话的选择/表单能力 | 用短编号列表，一次只问当前必要问题 |
| 内联可视化/图片 | 使用当前会话的可视化或图像能力 | 用 ASCII、静态 SVG/HTML 或文字描述 |
| 动画/视频 | 仅在当前会话确有对应能力且对学习有必要时使用 | 降级为可控的 SVG/Canvas 动画或静态分步图 |
| 交付离线制品 | 保存到用户指定路径；未指定时使用工作区的 `outputs/` 并返回可点击路径 | 在对话中提供内容或明确说明无法保存 |

**安全边界:** 不为可视化、动画或交互组件反复重试；不因缺少可选工具阻塞教学；不把工具调用结果当作用户已看到的内容，引用结果前先在正文复述关键结论。

---

## 参考文件

- `references/graph-schema.md` - Learning Graph 完整数据规范:单层 `concepts[]`、字段级可变性标记、PATCH 操作契约。分解阶段读一次。
- `references/compiler.md` - 分解流水线(CLARIFY / DECOMPOSE / C0 / C0.5 / C1 / C2)+ Artifact Builder + aha 交互模板 + SVG 模板库 + CDN 检查 + 创意发散 + iframe 沙盒 + 质量验收。按标题只读需要的部分即可。
- `references/runtime.md` - 状态设计、主循环(§3)、环节边界、Graph Feedback(PATCH)、置信度系统、反思模块、Review Engine、项目实战模式。仅需运行对话式练习循环时读取。
- `references/ui-patterns.md` - 对话与局部 UI 的混合模式、题型、交互事件协议、学习看板、动态更新和能力降级。需要交互式学习界面时读取。
- `references/mastery-model.md` - 定性状态机定义、三维度定性级别、状态转换规则、state block 格式、Learner View。运行时读一次。
- `references/pedagogy.md` - 教学策略变体矩阵(programming/math-science/humanities/language/arts)+ 类型检测 + 掌握度驱动调整。Runtime 中根据 `meta.pedagogy` 读取对应变体。
- `references/persistence.md` - 持久化层规范(progress 快照+日志双段文件格式 / 变化记录格式 / 持久化策略)。跨 session 时读取。
