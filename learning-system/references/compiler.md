# Compiler 模式细节

> **何时读取:** 需要分解主题、生成 Learning Graph 或生成交互式制品时读取。承接 SKILL.md ROUTE 之后,以及"需要先做分解"的 CLARIFY/DECOMPOSE 阶段。需要设计题型卡片、学习看板或动态 HTML 时同时读取 `ui-patterns.md`。
>
> **本文件较长,按标题只读需要的部分:** 分解流水线(CLARIFY/DECOMPOSE/C0/C0.5/C1/C2)、Artifact Builder 层、知识结构设计层、aha 交互设计、可视化工具箱、质量验收、陷阱、输出——共八部分,标题里已写明触发场景,不必整篇加载。例如 Runtime 主循环中需要临时 SVG 可视化,只跳到"可视化工具箱"一节即可。

## 内容导航

- [第一部分：分解流水线](#第一部分分解流水线)：CLARIFY、DECOMPOSE、Graph 自检与格式决策
- [第二部分：Artifact Builder 层](#第二部分artifact-builder-层)：正式离线制品的结构设计
- [第三部分：知识结构设计层](#第三部分知识结构设计层)：创意发散与参与模式
- [第四部分：aha 交互设计](#第四部分aha-交互设计)：预测→揭示交互模板
- [第五部分：可视化工具箱](#第五部分可视化工具箱)：SVG 模板与选用原则
- [第六部分：质量验收](#第六部分质量验收)：自动检查、教学 gate 与 CDN 检查
- [第七部分：陷阱](#第七部分陷阱)
- [第八部分：输出](#第八部分输出)：文件命名、保存与交付

**接口契约:**
- **Input:** PARSE 产出的主题词/输入内容(进入 CLARIFY)+ ROUTE 阶段的格式决策(进入 C1/C2)+ 已生成/已复用的 Learning Graph(字段定义见 `graph-schema.md`)
- **Output:** Learning Graph(若从 CLARIFY 进入)或交互式制品文件(HTML/structured-guide/simulation/project-scaffold 之一);Runtime 复用时同步保存 `{topic}-learning-graph.md`
- **约束:** 不重新做主题分解(那是 Runtime 消费 Graph 之后的事,不是 Compiler 的职责);C0 自检有 ✗ 项不交付,返回重新分解

**职责边界:** 本文件是"分解流水线(CLARIFY/DECOMPOSE/C0/C0.5/C1/C2)/制品构建/aha 交互/SVG 模板/CDN 检查/创意发散/iframe 沙盒"的 canonical home。不重做这些之外的事——Graph 数据结构见 `graph-schema.md`(含 PATCH 操作契约),Runtime 主循环见 `runtime.md`,状态机见 `mastery-model.md`,教学法类型检测见 `pedagogy.md`,文件格式见 `persistence.md`。

**与 Runtime 轻量可视化的边界:** 判断口诀定义见 SKILL.md §平台原生能力"与'离线可带走的正式制品'是两码事"。本文件完整流水线(C0/创意发散/参与模式选择)只用于生成用户明确要带走的独立成品;临时演示一个点走 `runtime.md` §3.4 和 `ui-patterns.md`,不触发本文件完整制品流水线。

**子例程调用契约(呼应"Compiler 降级为教学脚手架"):** 这类调用生成完制品后,**返回 Runtime 主循环继续**,不是流程终点(终点只存在于"用户明确要纯离线制品"那条独立入口路径)。这次调用若恰好有已确认的 Feedback PATCH 待合并,会在这次生成里一并合并进 Graph、`graph_version` +1——这是 Compiler 在此场景下唯一会写 Graph 的时刻(读写细节见 `persistence.md` §2/§3、`graph-schema.md` PATCH 操作契约),Runtime 自己不直接写 Graph。

---

## 第一部分:分解流水线

CLARIFY 和 DECOMPOSE 是 Runtime/Compiler 共用阶段(边界声明见 SKILL.md"CLARIFY / DECOMPOSE"节),Canonical 内容落在本文件;C0/C0.5/C1/C2 是 Compiler 内部环节。

### CLARIFY:目标澄清

**职责(唯一句):** 判断当前主题/目标是否已经清晰到可以直接分解——清晰、或虽宽泛但不同目标下核心概念本就收敛,则跳过;真正不清晰(宽泛且目标会导致选不同概念)才给 2-4 个选项收窄,拿到回覆再往下走。**不做:** 不识别输入格式(PARSE 已做完),不产出 Learning Graph 本体(DECOMPOSE 的职责)。

**接口契约:**
- **Input:** PARSE 产出的主题词/输入内容;若触发追问,还包括用户对选项的回覆
- **Output:** 写入 Graph `meta.goal`(用户目标)与 `meta.pedagogy`(教学策略标签,判定规则见 `pedagogy.md` §类型检测);"收敛度判断"命中与否的结果
- **Invariant:** 不产出 Graph 本体(DECOMPOSE 职责);不确定是否收敛时按"要问"处理,不强行跳过

#### 触发条件

**任一条件满足则执行:** 主题是宽泛学科词(如"Linux学习"、"编程入门"、"数据结构"、"机器学习");只圈定工具/领域而不圈定具体应用场景;用户表述缺少明确的"为了什么而学"。

**任一条件满足则跳过:** 主题已框定具体范围(如"JS闭包"、"Python装饰器"、"Docker volumes");用户已包含明确目标(如"面试用"、"做项目练手"、"理解原理");输入是概念簇;**主题虽是宽泛学科词,但不同学习目标下入门阶段的核心概念集合高度重合**(如"Learn AI"——无论想面试、做项目还是搞研究,入门都得先过一遍"模型/训练/推理"这批基础概念,目标只影响后面往哪深挖,不影响这一步选哪些概念)。

#### 收敛度判断(避免不必要的追问)

命中上面"核心概念高度重合"这条跳过条件时,不代表用户的目标不重要,只是不值得为了这点信息增量打断一轮对话:

- 直接按 `beginner overview` 策略分解(见下方 Goal → Graph 映射),不追问方向
- 分解结果在 GATE-1 展示时,补一句给用户事后纠偏的机会,例如:"如果你是想面试准备或者上生产用,后面会不太一样,现在先按最通用的入门路径来,想聚焦哪个方向可以随时说"——不是默认锁死方向,只是不提前问
- **判断口诀:** 不同目标会不会导致"这一步该选哪些概念"不同(会→要问)vs 只会导致"学到多深/往哪个方向深挖"不同(不会→不用问)。前者走正常选项询问,后者跳过直接给通用路径
- 这是定性判断,不是数值阈值——不确定命中与否时按"要问"处理(保守),不强行跳过

#### 灰色地带的判定(半明确目标)

"工作要用"/"想用得更熟练"这类表述比学科词具体、但不像"面试用"能直接映射裁剪策略,可能两组条件都不完全命中。判据:

- **主题范围已窄到不需要裁剪**(如"Git branch 这块")→ 即使目标模糊也跳过目标分析,直接分解,把用户提到的具体困惑点(如"branch 老搞混")作为选核心概念和裁剪范围的依据
- **主题范围仍宽**(如"Git,工作要用",未圈定到具体点)→ 即使有模糊目标也执行目标分析,但选项要覆盖该模糊目标作为选项之一(如"1.日常协作用——分支管理、代码提交规范 2.独立项目维护——完整版本管理流程 3.其他"),不完全无视已有线索重新开放提问

口诀:**范围窄→跳过;范围仍宽→目标分析,把模糊目标当候选选项之一,不当"已经足够明确"。**

#### 执行方式

以简短选项问用户,不开放式提问。**呈现形式按 SKILL.md §平台原生能力:优先用交互式选项组件展示 2-4 个方向供点选;组件不可用时降级为下面的文字格式。**

**文字格式(降级方案):**
> 你想聚焦哪个方向?
> 1. <方向一>
> 2. <方向二>
> 3. <方向三>

选项数量 2-4 个。**生成规则(无论用组件还是文字格式都适用):** 从主题推断最常见的 2-4 个学习动机;选项间互斥;最后一个可以是"其他(请描述)"兜底。

**示例(主题:"机器学习"):**
> 1. 面试准备——掌握高频考点和概念辨析
> 2. 生产部署——理解模型上线、监控、迭代流程
> 3. 学术理解——深入算法原理和数学基础
> 4. 其他(请描述)

#### Goal → Graph 映射

用户选择后,goal 信息写入 Graph 的 `meta` 段,并影响:

| Goal 类型 | depth 影响 | priority 影响 | strategy 倾向 |
|-----------|-----------|-------------|--------------|
| 面试准备 | 应用层为主,不过深 | 高频考点=core, 冷门知识=optional | 练习导向(多出题、多对比) |
| 生产部署 | 全链路覆盖,含运维 | 上线流程=core, 理论细节=optional | 项目驱动(按部署阶段分组) |
| 学术理解 | 深入原理和数学 | 基础理论=core, 应用技巧=optional | 递进导向(从基础到前沿) |
| hobby/探索 | 按兴趣点裁剪 | 有趣的概念=core, 枯燥的=optional | 沙盒探索(自由度高) |
| beginner overview(默认,无需用户确认) | 只覆盖入门层,不展开进阶分支 | 最常用/最基础的概念=core, 其余一律=optional | 广度优先(先建立全局轮廓,不深挖任一分支) |

**`beginner overview` 何时用:** 满足以下任一即自动选用,跳过本节选项追问,直接按此策略生成精简 Graph:(a) SKILL.md `brief` 深度档触发且主题是宽泛词(见 SKILL.md PARSE"学习深度"`brief` 触发段);(b) 上方"收敛度判断"命中——主题虽宽泛但不同目标下核心概念集合不变。其余场景走正常选项询问。

**实现方式:** 映射由 LLM 分解时执行,不需要代码,Goal 信息是提示词中的条件分支。`pedagogy` 在 CLARIFY 阶段确定:goal 明显指向某教学策略(如"面试准备"→练习导向)直接写入;否则由主题关键词推断,推断规则见 `pedagogy.md` §类型检测。

**具体交付物的概念扩展:** 用户目标是"具体交付物描述"(如"做一个 XX""搭一个 XX",非抽象动机词)时,在识别核心概念之外,额外自问"完成这个交付物通常还需要哪些跨主题词本身的支撑概念"(如 Dashboard 隐含的数据获取/状态分层/错误处理),追加为候选概念,在 GATE-1 里明确标出"这几条是因为你的目标才加的",让用户确认是否保留,不静默塞入 Graph。

### DECOMPOSE:主题分解

**职责(唯一句):** 把已经清晰的主题/目标(PARSE 读取内容+CLARIFY 确认范围之后)编译为 Learning Graph——核心概念、依赖关系、常见误解、关键洞察节点。**不做:** 不重新判断目标是否清晰(该判断已在 CLARIFY 完成,进入本阶段即视为已确认),不解析原始输入格式(PARSE 已做完)。

#### 4 步分解

1. **识别核心概念** - 构成主题基础的 3-5 个"原子"(纯练习场景可放宽到 3-8 个)。问自己:"如果学习者忘掉其他一切,必须记住什么?"(范围未经 CLARIFY 澄清时,先完成 CLARIFY 再回来分解。)
2. **映射依赖** - 每个概念需要先理解什么?A 依赖 B,B 必须更早出现。
3. **发现常见误解** - 学习者通常在哪里出错?误解比事实更有价值。过程中若发现两个概念本身就容易被互相搞混(而不只是某概念自身有误解),双向写入彼此的 `confused_with` 字段(见 `graph-schema.md`;不是每对概念都有,没有就留空,不强凑)。
4. **定位关键洞察** - 移除某概念后,其余概念是否变得更难理解?若断链或需大幅补前置知识,则是关键洞察候选;无明显单一候选则留空(`meta.key_insight_concept_id` 本就可选)。

#### 概念数量校正(建议,非硬规则)

- < 3 个 → 偏少,考虑扩展;Compiler+主题深度大可切"单弧深潜"(见 §2 学习递进设计);Runtime 直接进主循环
- > 5 个(制品场景)且无自然分组 → 建议裁剪:选"对比最鲜明"的概念,其余列"可选拓展"。练习场景可放宽到 8 个。**deep 模式覆盖此条裁剪建议**(仅 >12 个仍建议分组/拆分)
- > 12 个 → 建议分组为模块或拆分为多个 Graph
- **`brief` 深度档:** 不适用上述裁剪/扩展建议,具体规则见 SKILL.md PARSE"学习深度"

#### 范围控制

- 过宽:选 3-5 个核心概念代表性切片,除非用户要求完整课程
- 过窄:扩展到所在知识领域(如"Docker volumes"→"Docker 数据管理")
- 不适用:身体技能、需实时音频反馈的发音/口语、纯艺术创作、需物理硬件、纯事实记忆、快速变化的 API 细节、安全关键操作(急救/电气等可能造成人身伤害的主题)、需实时人际/表演反馈的技能(演讲/即兴表演/演奏)
- 事实与概念混合(如"Linux 命令大全"):提取可分解概念子集,纯事实部分作参考附录(制品)或跳过(练习)

#### 输出格式

产出 **Learning Graph**,单个 `concepts[]` 数组,字段定义见 `graph-schema.md`。

**内部格式 ≠ GATE-1 展示格式:** 下面 `[LEARNING GRAPH — ...]` 结构骨架(`id:`/`depends_on:`/`misconceptions:` 等字段标签)是机器可读格式,**不原样打印给用户当确认内容**。GATE-1 展示时译成自然语言列表,依赖/误解用一句话带出,不带字段名前缀,用"我们"而非"我打算"开场。

**执行前自检(每次到这一步都要做,不能省——真实使用中出现过跳过此检查直接走文字模板的情况):** 先确认当前会话是否探测到交互式点选组件可用(§平台原生能力)。探测到就直接调用组件展示"继续/需要修改"等选项;没探测到才用下面的纯文字模板。下面这段 `⛔` 格式是**探测不到组件时的降级方案,不是默认展示方式**。

```
我们先学这些来搞懂 Docker:
1. 镜像与容器的关系 —— 基础概念,没有前置
2. 数据持久化(volumes) —— 建立在①之上;容易搞混的地方是以为容器删了数据就没了
3. 网络与端口映射 —— 建立在①之上;和②相对独立,可以并行学

这个顺序可以吗?

⛔ 等待你的确认
```

内部仍按完整字段生成存储,只是展示层友好转写,不降低确认强度。

结构骨架(内部格式,完整模板见 `graph-schema.md`):

```
[LEARNING GRAPH — <topic>]
goal: <用户学习目标,可选>
pedagogy: <教学策略标签>
graph_version: <int>  # 首次生成=1,每次因 PATCH 重新生成 +1,纯 metadata

=== Concepts ===
1. id: <concept_id> | name: <名称> | summary: <一句话定义> | depends_on: [...] | misconceptions: [...] | confused_with: [...] | examples: [...] | counterexamples: [...]
   difficulty: <> | importance: <> | estimated_time: <> | observable_skills: [...] | assessment_items: [...] | review_weight: <>

关键洞察节点: <concept_id 或留空>   # 对应 meta.key_insight_concept_id
```

#### 里程碑结构(project-driven)

**若主题适合项目驱动方式**(存在微型项目/模拟场景,完成过程自然覆盖核心概念,见 §2 "项目驱动 vs 模拟驱动"),附上里程碑结构(Compiler/Runtime 共用,不各自重拆):

```
里程碑:
1. id: m1 | name: <名称> | introduces_concepts: [...] | depends_on: [...] | deliverable_type: real-project | simulation | deliverable_owner: assistant | user
```

`deliverable_owner` 标注产出由谁操作(与 `deliverable_type` 正交):
- `assistant` → Compiler 项目驱动制品:LLM 生成 HTML,用户自己操作
- `user` → 项目实战模式:用户在真实代码环境写,助手陪同(可用当前会话的 shell/执行能力实际运行验证)

交付物类型决定路径:真实项目文件 → §2 "项目驱动"分支(文件脚手架);模拟状态 → "模拟驱动"分支(单 HTML 状态机)。两者不混用。

**顺序提醒:** 这里产出的里程碑列表只是"这个项目大概会用到哪些概念"的清单,**不代表要先把所有概念学完才能动手**——项目实战模式按里程碑逐个解锁教学(当前里程碑做完、下一个里程碑依赖的概念到 Applied 门槛才教下一批),边做边学,顺序细节见 `runtime.md` §7。

对每个概念写一行清单展示给用户确认,避免拆解方向跑偏浪费后续所有轮次。

### C0:Graph 自检

Compiler 生成 Graph 后自行检查。**C0 是 Graph 校验的 canonical 定义。** 无论 Graph 来自哪种输入源(主题词、LLM 自行分解,还是 PARSE 阶段读取的 URL/PDF/粘贴文本等外部资料),C0 一视同仁执行——不因为内容来自外部资料就跳过或降低检查标准。

```
[GRAPH SELF-CHECK — <topic>]
结构检查（有 ✗ 必须返回重新分解）：
  ☑/✗ 无环检测（对每个 concept 沿 depends_on 做 DFS，能回到自己则有环）
  ☑/✗ 无重复 id（所有 concept.id 唯一）
  ☑/✗ 有根节点（至少一个 concept 的 depends_on 为空）
  ☑/✗ 必填字段非空（每个 concept 的 id/name/summary 均非空）
  ☑/✗ Milestone 完整性（若含 milestones：每个 milestone 的 id/name/introduces_concepts/deliverable_type/deliverable_owner 均非空）

结构检查（⚠ 不阻断，但应修正）：
  ☑/⚠ 无孤立节点（每个 concept 要么被依赖，要么是根节点）
  ☑/⚠ 依赖深度合理（最长链 ≤ 8 层）
  ☑/⚠ 覆盖率完整（core concept 都有 observable_skills）

语义检查（⚠ 不阻断，但应修正）：
  ☑/⚠ 依赖合理性（depends_on 中的概念是否真的是前置知识）
  ☑/⚠ Example 一致性（examples 是否展示 concept 的核心特征）
  ☑/⚠ Counterexample 有效性（反例是否能让人意识到 misconception 不成立）
  ☑/⚠ Observable 可观察性（observable_skills 是否可通过任务观察）
```

**判定(二状态):** 有 ✗ → 返回重新分解;无 ✗ → 进 C0.5/C1(有 ⚠ 也可继续,但应修正)。校验是 Compile→Check→Emit 的第二步,不通过时返回重新分解,不形成修复循环。

### C0.5:学习顺序排序

**触发时机:** C0 通过后、C1 前(仅 Runtime 模式需要;Compiler 制品模式跳过,制品内部递进由 C2 决定)。

**职责:** 按 Graph 生成学习顺序+Session 分组,输出 Session 列表作为 Runtime 初始化参数。**Static Planner——只负责初始计划,不做运行时动态调整。**

**输入(从 `concepts[]` 读取):** `depends_on`→学习顺序(拓扑排序);`importance`→优先级(core 必先学,optional 可跳过);`estimated_time`→Session 时长估算;`difficulty`→高难度单独成 Session 或与低难度配对;`meta.goal`→分组倾向(面试→练习导向,生产部署→项目导向)。**从 `meta.learner_profile` 读取:** `pace`→Session 时长上限调整(fast→上限 30min/normal→25min/slow→15min,方向 rationale 见 `graph-schema.md` pace 字段注释;与 `estimated_time` 配合:单个 concept 的 `estimated_time` 超过 pace 上限时,让该 concept **单独成一个 Session** 承载完整时长,不把 concept 本身拆碎——原子概念拆开教破坏完整性,宁可该 Session 略超上限)。

**输出格式:**
```
[LEARNING PLAN — <topic>]
goal: <用户学习目标>
estimated_total: <总预计分钟数>

Session 1 (~15 min): 概念A, 概念B
  - 必须先学: 概念A（被 2 个后续概念依赖）
  - 可跳过: 无

Session 2 (~20 min): 概念C（依赖 A, B）
Session 3 (~15 min): 概念D（可选，可跳过）
```

**输出规则:** Session 数 ≤ 概念数(每 Session 至少 1 个);单 Session 15-25 分钟,不超 30(`pace=fast` 时上限放宽到 30min,`pace=slow` 时收紧到 15min,默认 normal 沿用 25min;唯一例外:单个 concept 的 `estimated_time` 超过 pace 上限时单独成 Session 承载完整时长,允许超上限,见上方 pace 规则);依赖未满足的 concept 不出现在依赖概念之前;`importance=optional` 标记可跳过,不阻塞后续。

**持久化:** 输出作为 Runtime 初始化参数传递,并由 Runtime 写入 progress 文件快照的 `[PLAN]` 段(见 `persistence.md` §2),供学习看板(Dashboard)与跨 session 恢复读取,不另建独立计划文件。

**分组策略:** 默认线性递进——拓扑排序,每 2-3 个概念一组。按目标调整:面试准备→高频考点单独成 Session;生产部署→按部署阶段;学术理解→按理论深度;hobby→按兴趣点。配对规则:高难度与低难度配对;理论与实践配对;核心概念优先靠前。

**与 Runtime 接口:** Planner 输出 Session 列表后 Runtime 按顺序执行主循环,不重新规划——跳过逻辑在 `runtime.md` LOOP CONTROL 处理,不调用 C0.5。**不做:** 不做动态重排;未来 Adaptive 需求作为 Runtime LOOP CONTROL 子机制,不复活独立 Planner。

### C1:格式决策

**职责边界:** C1 决定**输出格式**(制品的物理形态:HTML/Markdown/dashboard 等),C2 决定**递进结构**(内容如何排序:四级递进/时间线/项目驱动等)。两者正交——`simulation`/`project-scaffold` 这两种输出格式不由 C1 显式判断,而是由 C2 的"项目驱动模式"分支隐式确定:当 C2 命中项目驱动 → C1 输出格式随之是 `project-scaffold`(`deliverable_type=real-project`)或 `simulation`(`deliverable_type=simulation`)。

first-match-wins,主题跨多类型选依赖关系最显著的:

```
if 用户要求"学习进度面板/dashboard/一直能看的状态页"(区别于一次性制品) → 持久化面板(见下方"Learning Dashboard")
if 纯证明/论证/叙事(如康德道德哲学、数学证明) → 结构化指南(.md)
if 艺术/视觉分析(如艺术史、解剖学) → 图片集 + 标注(输出格式 `interactive-html`,内容形态为图片集)
if 音乐/声音主题(如音乐理论、声学) → 音频 + 可视化(输出格式 `interactive-html`,内容形态为音频+可视化)
else → 交互式 HTML(默认)
```

输出语言与用户语言一致。

### C2:递进决策

**职责边界:** 决定制品的递进结构(学习者按什么顺序接触概念)。与 C1 正交——C2 命中"项目驱动模式"时,输出格式由 C2 隐式决定为 `project-scaffold`/`simulation`,不再回 C1 判断。

first-match-wins,主题跨多类型选依赖关系最显著的:

```
if 主题为技能类/概念类(编程、系统设计、数学等) → 四级递进(标准)或其变体(见下方"学习递进设计")
if 主题为历史/时间线类 → 时间线穿梭
if 主题为伦理/哲学类 → 多方观点对比
if 主题为艺术/鉴赏类 → 元素拆解→整体感知→风格溯源
if 主题为系统演化类 → 因果链追踪
if 概念少但深度大 → 单弧深潜
if 存在微型项目可覆盖核心概念 → 项目驱动模式
```

**混淆概念对的排序信号:** 若两个概念互相出现在对方 `confused_with` 中(见 `graph-schema.md`),无论上面命中哪种递进结构,排序时都优先让二者相邻呈现(而不是分散在结构两端),便于就地对比;这只是排序权重,不改变已选中的结构类型,`confused_with` 为空时不影响任何现有排序逻辑。

---

## 第二部分:Artifact Builder 层

决定"制品长什么样"和"如何交互"。输入是知识编译层的设计决策(格式+递进结构)和 Graph,输出完整 HTML 文件。**职责边界:** 只接受 Graph 和设计决策,不读原始主题文本。新增输出格式只需在本节新增分支,不动编译层。

### Learning Dashboard(持久化面板)

**和普通制品的区别:** 普通 HTML 制品是一次性交付,交付后不再变化;Dashboard 是可反复生成/刷新的状态页,反映的是 Progress State(见 `persistence.md`)的当前快照,不是 Graph 本体。

**数据来源(只读,不反向写):**
- Mastery 概览:读 `{topic}-progress.md` 里每个 concept 的 state(Unknown/Seen/Understood/Applied/Mastered),按状态计算一个粗粒度概览(如"5/12 概念已 Mastered"),**不做加权数值评分**——这会违反"定性状态机替代数值 mastery"这条设计决策,概览只能是计数/分类展示,不能合成一个综合分数
- 今日学习/待学习:读 Session 列表(progress 文件快照的 `[PLAN]` 段,见 `persistence.md` §2)+ 当前 Session 进度
- 误区记录:读对应 concept 的 `misconceptions` 字段(来自 Graph,只读展示)

**渲染规则:** Dashboard 只是 View,不持有独立状态——刷新靠重新读取 Progress State 后重新生成 HTML,不在页面内嵌入需要回写的交互逻辑(如可勾选打钩后自动同步进度这类功能不做,那等于另开一个和 Progress State 平行的状态源,两边会互相漂移)。允许的页面内交互仅限于纯展示层面的操作(展开/折叠、切换视图),不允许任何会修改 mastery 判断的操作。

**触发时机:** 用户明确要"进度面板/dashboard/一直能看的状态"时生成(C1 判断,见上)。首次生成后,后续每次用户要求刷新,重新走一遍本节流程而非增量更新旧文件。

### 概念地图设计

**需要:** 概念 ≥4 个、依赖复杂、易混淆层次。**可省略:** 概念 ≤3 个、线性递进、用户已熟悉。

根据知识性质选结构:层级型→树;顺序型→流;网络型→图;对比型→矩阵;时序型→时间线。

原则:限制 3-5 个节点,显式展示依赖,突出关键洞察节点(**地图节点 ≠ Graph 全部 concept**,只选最核心的 3-5 个);易误解节点标记警告指示(⚠️/虚线边框,这是质量验收检查项——有 `confused_with` 的概念直接标,没有则按设计者判断);地图应一眼可理解,需端详才懂说明需简化;主题跨多种结构时选最显著的一种作主结构,其余降级为辅助视图,不混用。

### 学习递进设计

#### 标准四级递进

| 级别 | 认知阶段 | 学习者做什么 | 脚手架 |
|------|---------|------------|--------|
| 1. 识别 | 观察与预测 | 观看运行中的系统,改变小数值,预测结果 | 最大 |
| 2. 引导练习 | 部分构建 | 填补缺失部分,遵循给定模式 | 中等 |
| 3. 独立 | 完整构建 | 从需求出发构建,对照验收标准自检 | 最小 |
| 4. 拓展 | 迁移与适应 | 在新情境中应用概念,处理边界情况 | 无 |

**原则:** 级别1让关键洞察可见(改变输入观察,不产出);级别2要求运用洞察(填补依赖此理解的部分);级别3要求从零产出洞察;级别4要求在洞察并非显然适用的情境中应用。相邻级别只跨一个认知步,每个级别必须产出可验证制品。

#### 递进结构变体

**决策规则:** 先按主题类型(见 C2)定主模式——四级递进(技能类)或替代结构(历史/伦理/艺术等)。主模式为默认四级时,按创意发散选定方向从下方 3 种变体选 1 个;主模式为项目驱动时,里程碑内固定用四级模式,不叠加变体。**参与模式**定义"学习者做什么"(动作维度),**递进结构变体**定义"概念如何被排序"(结构维度),正交组合。

##### 单弧深潜(4 阶段,适合概念少但深度大)

| 阶段 | 目标 | 学习者做什么 | 可验证制品 |
|------|------|------------|----------|
| 1. 观察 | 理解属性 | 操作模拟器,观察系统行为 | 能描述观察到的行为 |
| 2. 实验 | 做出选择 | 在约束条件下做 tradeoff 选择 | 能解释选择的后果 |
| 3. 推理 | 迁移应用 | 在真实场景中判断应该怎么做 | 能为场景做出正确判断 |
| 4. 总结 | 形成框架 | 回顾全程,提炼核心原则 | 能用自己的话复述洞察 |

##### 挑战阶梯(4 级,适合算法/数学/游戏类)

| 级别 | 挑战类型 | 学习者做什么 | 揭示方式 |
|------|---------|------------|--------|
| 1. 观察挑战 | 看现象 | 观察系统运行,描述行为 | 直接展示 |
| 2. 预测挑战 | 猜结果 | 预测改变输入后的结果 | 运行验证 |
| 3. 实验挑战 | 找规律 | 主动实验,发现底层规则 | 数据对比 |
| 4. 综合挑战 | 解决问题 | 用所学解决新问题 | 验收标准 |

##### 多轨并行

2-3 条独立路径,中途汇合于关键洞察。适合概念间弱依赖的主题。

##### 项目驱动模式(制品生成版,区别于 `runtime.md` 练习循环用的"项目实战模式",区别见 SKILL.md ROUTE)

**触发条件:** 存在微型项目(初学者 1-2 小时),完成过程自然覆盖核心概念。

**里程碑结构不在这里重拆**——若 DECOMPOSE 已产出共享里程碑列表,直接用,不重新分解;仅本 skill 被单独调用、跳过 DECOMPOSE 时才现场补做。

**项目驱动 vs 模拟驱动**(由 DECOMPOSE 的"交付物类型"字段决定,不现场判断):项目驱动=构建真实产品,里程碑是功能模块;模拟驱动=操作虚拟系统,里程碑是系统状态变化。两者都用里程碑结构,但模拟驱动的"制品"是操作结果而非代码产出。

**模拟驱动的命令集范围:** 设计命令行类模拟器前必读。模拟驱动类制品容易只实现教学重点对应的命令,漏掉完成里程碑所需的前置操作(创建文件、查看状态),导致用户卡在前置任务;命令解析也常要求逐字匹配,放大卡点。

**规则:**
- (a) 设计前先列出含前置操作的完整命令序列(不只是教学重点命令,还包括 `mkdir`/`cd`/`ls`/`cat` 这类前置操作)
- (b) 非教学重点的前置状态优先预置(如假设"已进入正确目录""文件已存在"),不强制用户逐个执行
- (c) 命令解析宽松匹配常见变体,不强制逐字对照(如允许 `git status` 与 `git status -s`、`ls` 与 `ls -la` 都识别为合法);但需在制品内保留一份"正确格式示例"作为对照基准,验收时把该示例原样输入,确认判定为正确、不误报格式错误
- (d) 主题要求高保真操作体验时,回到 SKILL.md ROUTE 选项目实战模式(真实 shell/执行能力运行,无此问题)

1. 无共享里程碑列表时现场选择项目/模拟案例,拆出 3-5 个里程碑,每个引入 1-2 个新概念
2. 里程碑内嵌套四级递进
3. 概念随需引入,不预讲
4. **里程碑间上下文传递:** 每个里程碑输出是下一个的输入,HTML 中用全局 state 对象维护

---

## 第三部分:知识结构设计层

在设计具体交互前先确定整体创意方向和参与模式。

### 创意发散

**何时需要:**
- **可低调处理:** 主题极其具体 / 同一主题短期迭代 / 概念 ≤2 个 / 用户指定风格
- **必须发散:** 首次设计该主题 / 用户未指定风格 / 概念 ≥4 个且复杂

**快速发散(三技术):**

#### 约束反转法

1. 写出最显然的方案(默认方案)
2. 列出其核心假设:"必须按顺序学习"、"需要文字解释"、"学习者主动操作"、"一次交互一个变化"
3. 逐条反转,每个反转生成一个新方案:
   - "必须按顺序" → "自由探索,无固定顺序"
   - "需要文字" → "只用视觉/动画/声音,无文字"
   - "学习者操作" → "系统自动演示,学习者只观察"
   - "一次一变化" → "一次交互触发连锁反应"

#### 极端约束法

- "如果不能用文字解释呢?" → 纯视觉方案
- "如果只有一次交互机会呢?" → 单次决定性交互
- "如果学习者只有 30 秒呢?" → 极速揭示方案
- "如果主题是一个游戏呢?" → 游戏化方案
- "如果是一个谜题呢?" → 解谜驱动方案

#### 跨域映射法

将主题映射到完全不相关的领域,每个领域产生一个新方案:

| 领域 | 映射方式 | 示例(以"闭包"为例) |
|------|---------|-------------------|
| 烹饪 | 过程映射 | 闭包 = 一个密封的调料包(食材在里面,但可以从外面加东西进去) |
| 音乐 | 结构映射 | 闭包 = 一个循环播放的播放列表(记住上次播到哪了) |
| 建筑 | 空间映射 | 闭包 = 一扇能看到隔壁房间的窗户(但隔壁的门已经关了) |
| 侦探 | 信息映射 | 闭包 = 侦探记住的线索(案件结束了,但线索还在脑子里) |
| 游戏 | 机制映射 | 闭包 = 存档点(游戏关了,角色状态还在) |

#### "截然不同"判定标准

任意两方案须在**至少 2 个维度**上不同:
- 叙事框架
- 参与模式
- 情感弧线
- 制品形式

若两方案只在 1 个维度上有差异,视为同一方案的变体,需重新发散。

**收敛评估(按优先级):** 1.惊喜潜力("没想到还能这样学") 2.交互潜力("想再试一次") 3.主题契合度(自然映射核心概念) 4.反模板性(避开默认组合)。两方案接近选更具惊喜潜力的。评分不超过 5 分钟。

### 参与模式选择

| 模式 | 核心动作 | 适合主题 |
|------|---------|---------|
| **解谜驱动** | 解释神秘现象 | 有反直觉结论的主题 |
| **建造模式** | 从零搭建系统 | 编程、工程、系统设计 |
| **沙盒探索** | 自由实验 | 物理模拟、复杂系统、涌现 |
| **故事驱动** | 在叙事中做决策 | 有历史脉络或应用场景丰富的主题 |
| **角色扮演** | 以特定身份做决策 | 架构设计、博弈论、策略类 |
| **失败驱动** | 故意触发失败 | 安全机制、边界条件、鲁棒性 |
| **对比实验** | 并排比较两种方案 | 算法对比、设计模式、架构选型 |
| **逆向工程** | 从成品拆解出原理 | 系统架构、协议、编译原理 |

**多模式都适用时(first-match-wins,不并列选两个):**
```
if 主题有反直觉结论需解释 → 解谜驱动
elif 主题是从零搭建的系统/产品 → 建造模式
elif 主题核心是权衡/选型（有明确备选方案对比） → 对比实验
elif 主题需故意触发失败来理解机制 → 失败驱动
elif 主题有历史脉络或丰富应用场景 → 故事驱动
elif 主题需从成品反推原理 → 逆向工程
elif 主题需在约束下做决策/博弈 → 角色扮演
else（自由探索、涌现行为） → 沙盒探索
```
选完后说明一句"选 X 模式,因为主题 Y"(与 `runtime.md` §9 隐式判断显式化规范一致)。

---

## 第四部分:aha 交互设计

至少设计一个交互,让改变输入能可视化揭示底层概念。

**认知操作原则:** Artifact 是认知操作执行环境,不是内容展示容器。交互必须提供文本无法完成的认知操作——预测(先猜再看结果)、对比(并排比较两种方案)、调试(改参数看系统怎么坏)、模拟(操作系统观察涌现行为)。纯视觉展示(卡片翻转、播放动画、静态节点图)不算 aha,可作辅助视图但不能替代。口诀:**如果删掉交互、只看文字也能得到同样信息,这个交互没有认知价值。**

**认知操作标记:** 每个 aha 交互在其**容器元素**(承载该交互的 DOM 节点,不是 html/body 根元素)标记 `data-cognitive-op` 属性,取值:`predict`/`compare`/`debug`/`simulate` 之一。这是质量验收「教学有效性 gate」的扫描钩子——至少一个交互带合法标记,多个 aha 交互各自标记;辅助视图不需要标记。

**寻找 aha 的 4 个角度:** 隐藏关系变得可见(hash % size→观察键重分布);反直觉结果挑战假设(45°而非90°给出最大射程);边界情况暴露机制(空输入揭示默认行为);前后对比展示因果关系。

**设计原则:** 按参与模式选交互形态——解谜/建造/对比实验→强制预测→揭示流程(先预测→触发交互→对比结果→解释偏差);沙盒探索/角色扮演→自由探索;故事驱动→决策→后果→转折点;单弧深潜→选择→后果;对比实验→竞速对比;模拟驱动→操作→反馈。aha 应是学习者自身行动的产物;交互应在一次操作内产生可观察变化;aha 之后给一句点明概念的解释。默认形态:参数滑块→观察变化。

**主题类型差异化:**

| 主题类型 | 典型洞察 | 典型误解 | 推荐模式 |
|---------|---------|---------|---------|
| 编程概念 | 运行时行为、状态变化 | 混淆声明与赋值 | 建造/失败驱动 |
| 系统原理 | 反馈循环、稳定性条件 | 线性思维、忽略耦合 | 沙盒/对比实验 |
| 数学直觉 | 极限、不变量、变换规律 | 混淆充分与必要条件 | 解谜/单弧深潜 |
| 数据模式 | 分布特征、相关性 | 混淆因果与相关 | 对比实验/挑战阶梯 |

### 预测→揭示 HTML 模板

"预测→揭示"是最核心的 aha 交互模式。以下是通用实现。

#### 基本结构

```html
<!-- 1. 预测区域 -->
<div class="predict-section">
  <label>📝 你的预测：</label>
  <div class="predict-inputs">
    <input class="predict-input" id="pred1" placeholder="预测值1" />
    <input class="predict-input" id="pred2" placeholder="预测值2" />
  </div>
  <div class="predict-result" id="predictResult"></div>
</div>

<!-- 2. 操作区域（必须包含重置按钮） -->
<button class="btn btn-run" onclick="runAndCheck()">▶ 运行并揭晓</button>
<button class="btn btn-outline" onclick="resetPredict()">🔄 重置预测</button>

<!-- 3. 输出区域 -->
<div class="output" id="output">点击"运行"查看结果...</div>
```

#### JavaScript 逻辑

> **占位说明:** 下方 `expected1`/`expected2` 是当前制品应揭示的正确答案,由具体主题决定(如闭包题就是函数实际返回的值),使用前必须用具体值替换占位;若制品带可编辑代码区,可改为由 `executeCode()` 实时计算得出而非硬编码。`executeCode()` 是制品内部的执行函数(运行模拟器/求值表达式等),按主题实现。

```javascript
// 正确答案占位（替换为具体主题的实际值，或从 executeCode() 动态计算）
const expected1 = '<value1>';
const expected2 = '<value2>';

function resetPredict() {
  document.getElementById('pred1').value = '';
  document.getElementById('pred2').value = '';
  document.getElementById('output').textContent = '点击"运行"查看结果...';
  document.getElementById('predictResult').className = 'predict-result';
}

function runAndCheck() {
  // 1. 执行代码/模拟（executeCode 由具体制品实现，返回字符串）
  const result = executeCode();

  // 2. 显示输出
  document.getElementById('output').textContent = result;

  // 3. 检查预测
  const pred1 = document.getElementById('pred1').value.trim();
  const pred2 = document.getElementById('pred2').value.trim();
  const resultEl = document.getElementById('predictResult');

  if (pred1 && pred2) {
    const correct = pred1 === expected1 && pred2 === expected2;
    resultEl.className = 'predict-result show ' + (correct ? 'correct' : 'wrong');
    resultEl.textContent = correct
      ? '✅ 预测完全正确!'
      : `❌ 预测: [${pred1}, ${pred2}] | 实际: [${expected1}, ${expected2}]`;
  }
}
```

#### CSS 样式

```css
.predict-input {
  width: 80px; padding: 0.5rem;
  background: #0c1222; border: 1px solid #334155;
  border-radius: 6px; color: #e2e8f0;
  text-align: center; font-family: monospace;
}
.predict-input:focus { border-color: #fbbf24; outline: none; }

.predict-result { margin-top: 0.75rem; padding: 0.75rem; border-radius: 8px; display: none; }
.predict-result.show { display: block; }
.predict-result.correct { background: rgba(74,222,128,0.1); border: 1px solid #4ade80; color: #4ade80; }
.predict-result.wrong { background: rgba(248,113,113,0.1); border: 1px solid #f87171; color: #f87171; }
```

#### 变体

##### 选项式预测(不需要输入框)

`selectPredict(el, expected)` 实现同 `runAndCheck` 模式:切换 `.selected` 选中态,比对 `expected` 后加 `.correct`/`.wrong` 类。

```html
<div class="predict-options">
  <div class="predict-opt" onclick="selectPredict(this,'A')">选项 A</div>
  <div class="predict-opt" onclick="selectPredict(this,'B')">选项 B</div>
  <div class="predict-opt" onclick="selectPredict(this,'C')">选项 C</div>
</div>

<style>
.predict-opt {
  padding: 0.5rem 1rem; background: #0c1222; border: 1px solid #334155;
  border-radius: 8px; cursor: pointer; font-size: 0.85rem;
}
.predict-opt:hover { border-color: #38bdf8; }
.predict-opt.selected { border-color: #fbbf24; background: rgba(251,191,36,0.1); color: #fbbf24; }
.predict-opt.correct { border-color: #4ade80; background: rgba(74,222,128,0.1); color: #4ade80; }
.predict-opt.wrong { border-color: #f87171; background: rgba(248,113,113,0.1); color: #f87171; }
</style>
```

##### 竞速式预测(并排比较)

```html
<div class="race-container" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem;">
  <div class="race-lane">
    <div class="race-name">方案 A</div>
    <div style="background:#1e293b;border-radius:4px;height:24px;overflow:hidden;">
      <div id="raceA" style="height:100%;background:#f87171;width:0%;transition:width 0.3s;border-radius:4px;"></div>
    </div>
    <div class="race-ops" id="raceAOps">0 ops</div>
  </div>
  <!-- 重复 B、C lane,各自绑定 ops 计数器 -->
</div>
```

完整实现需复制 B/C lane 并各自绑定 ops 计数器。

---

## 第五部分:可视化工具箱

| 知识类型 | 工具 | 使用阶段 |
|---------|------|---------|
| 概念地图 | SVG 或 D3.js | 识别--开篇展示全景 |
| 模拟与动态系统 | Canvas 2D | aha 交互--核心揭示 |
| 数据模式 | Chart.js 或 Recharts | 引导/独立--数据任务 |
| 3D / 空间概念 | Three.js | 识别/aha--空间直觉 |
| 流程 | Mermaid 或 SVG | 识别--流程概览 |
| 数学推导 | manim(3Blue1Brown 风格) | 拓展--补充深度理解,不替代主 HTML 制品 |
| 有机结构/场景类 | ImageGen | 识别(辅助)--具象化抽象概念,base64 内嵌至 HTML |

**选择原则:** 最轻量优先(能用 SVG 不引入 Canvas/D3);能不用 CDN 就不用(纯原生 CSS+JS 最可靠);aha 交互用 Canvas;一制品一主线工具,其余作辅助。本节仅覆盖**离线制品(HTML 文件)**内的可视化工具选型,产出物落盘、要能脱离对话独立打开;**对话中临时演示**(不落盘、不生成文件)走 `runtime.md` §3.4"轻量教学可视化",口诀见头部"与 Runtime 轻量可视化的边界"。

### SVG 模板库

4 种常用模板直接复制、改坐标和文字即可。节点间距线性图统一 80px 步进,网络图自适应。颜色统一:`#38bdf8`(accent)、`#4ade80`(green)、`#f87171`(red)、`#fbbf24`(yellow)。

#### 1. 线性流程图(概念地图 — 顺序型)

```html
<svg viewBox="0 0 700 120" xmlns="http://www.w3.org/2000/svg">
  <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8"/></marker></defs>
  <!-- 修改：节点数量、文字、宽度 -->
  <g class="node"><rect x="10" y="35" width="140" height="50" rx="8" fill="#1e293b" stroke="#38bdf8" stroke-width="2"/><text x="80" y="65" text-anchor="middle" fill="#e2e8f0" font-size="13">① 概念A</text></g>
  <g class="node"><rect x="190" y="35" width="140" height="50" rx="8" fill="#1e293b" stroke="#38bdf8" stroke-width="2"/><text x="260" y="65" text-anchor="middle" fill="#e2e8f0" font-size="13">② 概念B</text></g>
  <g class="node"><rect x="370" y="35" width="140" height="50" rx="8" fill="#1e293b" stroke="#38bdf8" stroke-width="2"/><text x="440" y="65" text-anchor="middle" fill="#e2e8f0" font-size="13">③ 概念C</text></g>
  <g class="node"><rect x="550" y="35" width="140" height="50" rx="8" fill="#1e293b" stroke="#38bdf8" stroke-width="2"/><text x="620" y="65" text-anchor="middle" fill="#e2e8f0" font-size="13">④ 概念D</text></g>
  <!-- 箭头 -->
  <line x1="150" y1="60" x2="185" y2="60" stroke="#94a3b8" stroke-width="1.5" marker-end="url(#arrow)"/>
  <line x1="330" y1="60" x2="365" y2="60" stroke="#94a3b8" stroke-width="1.5" marker-end="url(#arrow)"/>
  <line x1="510" y1="60" x2="545" y2="60" stroke="#94a3b8" stroke-width="1.5" marker-end="url(#arrow)"/>
  <!-- 易误解节点用虚线边框 -->
  <!-- <rect ... stroke="#fbbf24" stroke-dasharray="5,3"/> -->
</svg>
```

#### 2. 网络拓扑图(节点 + 连接线)

```html
<svg viewBox="0 0 300 120" xmlns="http://www.w3.org/2000/svg">
  <!-- 连接线（先画，后画节点覆盖） -->
  <line id="lineAB" x1="60" y1="60" x2="150" y2="60" stroke="#94a3b8" stroke-width="2"/>
  <line id="lineBC" x1="150" y1="60" x2="240" y2="60" stroke="#94a3b8" stroke-width="2"/>
  <!-- 节点 -->
  <g><rect x="20" y="35" width="80" height="50" rx="10" fill="#1e293b" stroke="#4ade80" stroke-width="2"/><text x="60" y="55" text-anchor="middle" fill="#e2e8f0" font-size="12">节点 A</text><text x="60" y="72" text-anchor="middle" fill="#94a3b8" font-size="10">x=1</text></g>
  <g><rect x="110" y="35" width="80" height="50" rx="10" fill="#1e293b" stroke="#4ade80" stroke-width="2"/><text x="150" y="55" text-anchor="middle" fill="#e2e8f0" font-size="12">节点 B</text><text x="150" y="72" text-anchor="middle" fill="#94a3b8" font-size="10">x=1</text></g>
  <g><rect x="200" y="35" width="80" height="50" rx="10" fill="#1e293b" stroke="#4ade80" stroke-width="2"/><text x="240" y="55" text-anchor="middle" fill="#e2e8f0" font-size="12">节点 C</text><text x="240" y="72" text-anchor="middle" fill="#94a3b8" font-size="10">x=1</text></g>
  <!-- 断开连接：stroke-dasharray="5,5" stroke="#f87171" -->
</svg>
```

#### 3. 柱状图(数据可视化)

```html
<!-- JS 动态生成；arr 是数据数组，max 是最大值 -->
<div id="barChart" style="display:flex;align-items:flex-end;gap:2px;height:200px;"></div>
<script>
  const arr = [3, 7, 2, 9, 5]; // 替换为实际数据
  const max = Math.max(...arr);
  document.getElementById('barChart').innerHTML = arr
    .map(v => `<div style="flex:1;height:${v/max*100}%;background:#38bdf8;border-radius:2px 2px 0 0;"></div>`)
    .join('');
</script>
```

#### 4. Git 提交图(有向图)

```html
<svg viewBox="0 0 700 100" xmlns="http://www.w3.org/2000/svg">
  <!-- 提交节点 -->
  <circle cx="50" cy="50" r="12" fill="#4ade80" stroke="#0f172a" stroke-width="2"/>
  <text x="50" y="54" text-anchor="middle" fill="#0f172a" font-size="10" font-weight="700">c1a</text>
  <text x="50" y="35" text-anchor="middle" fill="#94a3b8" font-size="11">init</text>
  <!-- 连接线 -->
  <line x1="62" y1="50" x2="128" y2="50" stroke="#4ade80" stroke-width="2"/>
  <circle cx="140" cy="50" r="12" fill="#4ade80" stroke="#0f172a" stroke-width="2"/>
  <text x="140" y="54" text-anchor="middle" fill="#0f172a" font-size="10" font-weight="700">b2d</text>
  <!-- 分支标签 -->
  <rect x="115" y="70" width="50" height="16" rx="4" fill="#4ade80" opacity="0.2" stroke="#4ade80" stroke-width="1"/>
  <text x="140" y="81" text-anchor="middle" fill="#4ade80" font-size="12" font-weight="600">main</text>
</svg>
```

#### 使用原则

1. 复制模板,修改坐标和文字
2. 节点间距统一用 80px(线性图)或自适应(网络图)
3. 颜色统一用上文色值
4. 交互状态用 CSS class 切换(`.active`、`.broken`、`.sorted`)
5. 易误解节点加 `stroke-dasharray="5,3"`,断开连接用红色虚线

---

## 第六部分:质量验收

**验收强度分级:** 下列清单默认全量适用。当 `meta.goal` 为 hobby/探索,或本次生成由 `brief` 深度档触发(概览式简单制品)时,可精简为核心项——HTML 语法无错误、断网基本可用、移动端不溢出、教学有效性 gate 三项——其余项(CDN 多镜像验证、色盲模式辨认、Tab/Enter 键位可达性等)可跳过,不必逐项声明跳过原因。不确定当前制品是否够"轻量"时,按完整清单执行(保守优先)。

### 自动检查(agent 执行)

- [ ] HTML 语法无错误
- [ ] 断网打开 HTML,确认除 CDN 库外无本地文件依赖(不通过则内联关键库)
- [ ] CDN 链接可达(检查方法见下方"CDN 可达性检查";网络策略本身拒绝 curl 时直接判不可达走内联,不重试)
- [ ] 移动端可用:含 viewport meta;375px 宽度下核心交互区域不溢出、可点击
- [ ] 所有提示默认隐藏,需主动操作才展开
- [ ] 概念地图中易误解节点有警告标记
- [ ] 交互元素有 Pointer Events(非仅 mouse/touch)
- [ ] 关键交互可仅用 Tab/Enter/方向键完成一次
- [ ] 中文字符编码正确(Read 回读无 `\uXXXX`)

### 教学有效性 gate(有 ✗ 必须返回 aha 交互设计步骤重做)

- [ ] **存在性:** 至少一个交互带合法 `data-cognitive-op`(predict/compare/debug/simulate 之一)
- [ ] **一致性:** 该取值与设计期声明的认知操作一致
- [ ] **认知价值自检:** 删掉该交互只看文字是否无法得到同样信息?若能 → 无认知价值,返回重做

任一 ✗ → 返回 aha 交互设计步骤重做一次(不形成修复循环)。

### 用户确认(交付后)

- [ ] 完整走通所有级别,确认每步只跨一个认知步
- [ ] 改变 aha 交互输入,确认产生可观察的视觉变化
- [ ] 实际操作 aha 交互,确认执行的是声明的认知操作,非纯展示
- [ ] **预测→揭示强制检查:** 级别1、2 是否都有预测环节?只有观察没预测则返回补充
- [ ] **命令/表单模拟器最短路径试跑(§2 模拟驱动的命令集范围,真实用户反馈):** 若制品含命令行或表单式模拟器,按题目要求的最短命令/操作序列(含前置步骤)实际走一遍,确认不会在任何一步因缺前置状态而卡住;把 §2 规则 (c) 要求保留的"正确格式示例"原样输入,确认判定为正确、不误报格式错误
- [ ] 每个代码编辑器有重置按钮
- [ ] 警告标记(⚠️)在色盲模式/灰度下仍可辨认
- [ ] **反模板检查:** 设计是否源自创意发散的主动选择?与最近 3 次同主题制品结构雷同则返回重新选择

### CDN 可达性检查

在制品中引用 CDN 资源前,用以下命令验证可达性:

```bash
# 检查单个 CDN（期望 HTTP 200）
curl -sI --connect-timeout 10 "https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js" | head -1
curl -sI --connect-timeout 10 "https://cdn.jsdelivr.net/npm/chart.js" | head -1
curl -sI --connect-timeout 10 "https://cdn.bootcdn.net/ajax/libs/three.js/r128/three.min.js" | head -1
```

Windows 环境若 curl 不可用,用 PowerShell `Invoke-WebRequest -Method Head -TimeoutSec 10` 替代。

**回退策略:**
1. 首选 `cdn.jsdelivr.net` 或 `cdn.bootcdn.net`(延迟通常更低更稳定,尤其中国大陆网络)
2. 备选 `cdnjs.cloudflare.com`(部分区域延迟较高,不作为首选)
3. 若均不可达且库体积小 → 内联库源码
4. 若均不可达且库体积大(如 Three.js/D3 完整版)→ 改用 SVG 静态可视化替代,不内联

**网络受限判定:** 沙盒/容器环境的出网白名单若不含 CDN 域名,curl 会被立即拒绝(而非超时)——这与"网络慢导致超时"是不同失败原因。**直接判不可达,不重试其余 CDN**(结果会一致),按库体积走回退策略第 3 步起。若只是单个 CDN 超时/5xx,仍按原策略依次尝试备选。

**常用库 CDN 链接:**

| 库 | cdnjs | jsdelivr | bootcdn |
|---|-------|----------|---------|
| D3.js v7 | cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js | cdn.jsdelivr.net/npm/d3@7 | cdn.bootcdn.net/ajax/libs/d3/7.8.5/d3.min.js |
| Chart.js v4 | cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js | cdn.jsdelivr.net/npm/chart.js@4 | cdn.bootcdn.net/ajax/libs/Chart.js/4.4.0/chart.umd.min.js |
| Three.js r128 | cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js | cdn.jsdelivr.net/npm/three@0.128.0 | cdn.bootcdn.net/ajax/libs/three.js/r128/three.min.js |
| Mermaid v10 | cdnjs.cloudflare.com/ajax/libs/mermaid/10.6.1/mermaid.min.js | cdn.jsdelivr.net/npm/mermaid@10 | cdn.bootcdn.net/ajax/libs/mermaid/10.6.1/mermaid.min.js |

> bootcdn 链接未逐一实测,使用前按上面的可达性检查方式验证一次。

---

## 第七部分:陷阱

> 每条附一行 **Trigger**(什么情况下会踩到),便于日后做经验性回归核对,不代表需要写自动化测试。

⚠️ 1. **Canvas 颜色** - `strokeStyle`/`fillStyle` 不支持 CSS 变量。用 `getComputedStyle().getPropertyValue('--x').trim()` 或硬编码色值。
   **Trigger:** 制品里用 Canvas 画图且直接把 CSS 变量字符串(如 `var(--accent)`)传给 `strokeStyle`/`fillStyle`,图形不显示颜色或报错。

⚠️ 2. **CDN 可达性** - cdnjs、bootcdn、jsdelivr 随环境波动,实测选择;不可达回退其他两家或内联(详见 §6 CDN 可达性检查)。
   **Trigger:** 制品引入外部 CDN 脚本/样式,在网络受限或墙内环境打开时资源 404 或加载超时,页面白屏或功能缺失。

⚠️ 3. **触摸事件** - 首选 Pointer Events API(`pointerdown`/`pointermove`/`pointerup`),统一处理鼠标/触摸/触控笔。
   **Trigger:** 只监听 `mousedown`/`mousemove` 一类鼠标事件,在移动端/触屏设备上交互完全无响应。

⚠️ 4. **失败模式降级** - manim 渲染失败→降级为 SVG/Canvas 动画或静态图;ImageGen 失败→用 SVG 矢量插图;CDN 全不可达→小型库内联,大型库改 SVG 静态可视化。
   **Trigger:** 环境未预装 manim、ImageGen 工具报错或不可用、或上一条 CDN 场景全部命中失败,且流程里没有为这些失败写明确的下一步。

⚠️ 5. **动画性能** - 限制元素数量(≤50)或提供速度控制;大数组排序等场景用进度条替代逐帧动画。
   **Trigger:** 制品对 100+ 元素做逐帧 DOM/Canvas 动画,低端设备或多标签页情况下明显卡顿掉帧。

⚠️ 6. **eval() 安全性** - 代码编辑器用 eval() 执行用户代码,本地学习制品可接受;**面向公开发布必须改用 sandboxed iframe**,不允许公开制品裸用 eval()。
   **Trigger:** 制品带"运行我的代码"编辑器且用户明确要求能公开分享/部署到网上,此时若仍直接 `eval()` 执行输入,属安全隐患。

   **展开:sandboxed iframe 最小实现**——`eval()` 在学习制品中执行学习者代码是可接受的默认方案,但若制品面向公开发布(他人可自由访问、可能被恶意输入),改用 sandboxed iframe + `postMessage` 隔离执行环境:

   ```html
   <!-- 主页面 -->
   <textarea id="editor">console.log("hello");</textarea>
   <button onclick="runInSandbox()">▶ 运行</button>
   <div id="output"></div>

   <iframe id="sandbox" sandbox="allow-scripts" style="display:none"></iframe>
   ```

   ```javascript
   const sandboxHTML = `
   <script>
     window.addEventListener('message', (e) => {
       const logs = [];
       const fakeConsole = { log: (...args) => logs.push(args.map(String).join(' ')) };
       try {
         // 用 Function 构造器代替全局 eval，限制作用域访问
         const fn = new Function('console', e.data.code);
         fn(fakeConsole);
         parent.postMessage({ type: 'result', logs, error: null }, '*');
       } catch (err) {
         parent.postMessage({ type: 'result', logs, error: err.message }, '*');
       }
     });
   <\/script>
   `;

   const sandboxFrame = document.getElementById('sandbox');
   sandboxFrame.srcdoc = sandboxHTML;

   function runInSandbox() {
     const code = document.getElementById('editor').value;
     sandboxFrame.contentWindow.postMessage({ code }, '*');
   }

   window.addEventListener('message', (e) => {
     if (e.data?.type !== 'result') return;
     const out = document.getElementById('output');
     out.textContent = e.data.error
       ? `❌ 错误: ${e.data.error}`
       : e.data.logs.join('\n') || '(无输出)';
   });
   ```

   **关键点:**
   - `sandbox="allow-scripts"` 不加 `allow-same-origin`:iframe 内脚本无法访问父页面 DOM、cookie、localStorage,即使学习者写出恶意代码也无法逃逸到主页面
   - 用 `postMessage` 通信,不要用 `sandbox.contentWindow.evalInFrame(...)` 之类直接跨 frame 调用——那等于绕过了沙盒
   - iframe 内部仍用 `Function` 构造器而非裸 `eval`,且只暴露一个 `fakeConsole`,不传入真实 `window`/`document`,进一步限制可触达的 API 面
   - 若需限制执行时间(防死循环),可在 postMessage 后设置 `setTimeout`,超时未收到 `result` 消息则提示"执行超时,可能存在死循环"
   - 此方案仍不是完全沙盒(iframe 内脚本仍可发起 `fetch` 请求等),仅阻止对宿主页面的直接篡改。更强隔离考虑 Web Worker 或后端沙盒执行,但已超出本 skill 默认场景(本地学习制品)

   **何时不需要这套方案:** 制品只在本地打开、仅学习者自己使用 → 直接用 `eval()` + 陷阱 #6 的免责说明即可;只有"面向公开发布、可能被陌生人访问"场景才需启用。

⚠️ 7. **单文件约束** - 单制品单文件优先(CSS+JS 内联)。超 55KB 考虑拆分静态资源。
   **Trigger:** 单 HTML 文件体积明显超过 55KB(通常是内嵌了大量 base64 图片或代码),打开变慢或不易分享。

⚠️ 8. **代码编辑器必须有重置功能** - 每个编辑器旁必须有"重置代码"按钮。
   **Trigger:** 制品含可编辑代码区,用户改乱代码后无法一键恢复到初始示例,只能刷新整个页面重来。

⚠️ 9. **模拟器命令集不完整/解析过严格,卡住前置任务**(真实用户反馈)——模拟驱动类制品容易只实现教学重点对应的命令,漏掉完成里程碑所需的前置操作,导致用户卡在前置任务;命令解析也常要求逐字匹配,放大卡点。设计期规则(含 (a)-(d) 与"正确格式示例"要求)见 §2 "模拟驱动的命令集范围"。
   **Trigger:** 命令行模拟类制品(Git/Docker/Linux CLI 教学)里,用户在教学重点之外的前置步骤(如先 `mkdir`/`cd`)卡住,或输入命令因空格/参数顺序差异被判定为"未知命令"。

---

## 第八部分:输出

- **文件:** 保存至用户指定的输出目录；未指定时使用工作区 `outputs/`。命名 `{topic}-interactive.html`(无可运行交互的指南用 `.md`)。同名文件已存在时追加时间戳后缀或先询问是否覆盖
- **媒体资产:** 动画视频存于输出目录,HTML 用 `<video>` 引用；图片优先内嵌或放在同目录，确保制品自包含
- **Learning Graph 文件:** 同时保存 `{topic}-learning-graph.md`(见 SKILL.md ROUTE),供后续 Runtime 复用
- **交付:** 返回客户端支持的可点击工作区路径；不要假设 `file://` 链接或某个固定的文件展示工具一定存在。说明制品教什么、如何交互——各一句。

**IM 场景交付:** 会话是 IM 渠道(飞书/钉钉等)时使用当前渠道的文件交付能力发送制品路径；没有该能力时在正文给出保存位置。

**公开发布安全:** 公开发布的 HTML 制品在 `<head>` 添加 CSP meta 标签作为第一道防线:
```html
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'">
```
配合 sandboxed iframe(见陷阱#6)形成双层防护——CSP 阻内联脚本注入,iframe 隔离执行环境。本地学习制品不需要 CSP。
