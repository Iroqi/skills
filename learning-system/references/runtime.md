# Runtime 模式细节

> **何时读取:** 仅在需要运行对话式练习循环时读取。

**接口契约:**
- **Input:** 已生成/已复用的 Learning Graph(只读)+ 用户在主循环中的作答/反馈/progress 文件
- **Output:** Progress State(state block,跨 session 持久化)+ 达到 confidence 门槛的 PATCH 提议(不直接改 Graph 本体)
- **约束:** 不写 Graph 本体,只提议 PATCH;mastery 更新须基于真实证据(探针/练习/复习的实际作答,不是学习者一句"我懂了")

需要交互式题型、局部 HTML 或学习看板时,同时读取 `ui-patterns.md`。UI 只是当前 Runtime 状态的投影，所有操作先转为事件，再进入本文件的 EVALUATE / ADAPT / UPDATE MASTERY 链路。

承接 SKILL.md ROUTE(判断模式=要现在开始对话式练习/被追问/复习)之后的流程。此时已有 Learning Graph(概念+依赖+误解+关键洞察),从这里初始化状态、运行主循环。

**职责边界:** 不重新做主题分解(见 SKILL.md)。本文件仅负责 Runtime 主循环。

Runtime 负责:学习者交互(诊断/追问/讲解/出题)、误解检测与处理、教学动作选择、进度更新(state 转换判断、写入 progress 文件)。交互 UI 只改变回答的呈现方式，不改变 Runtime 的状态机。

Runtime 不负责:知识结构生成(Graph 由 Compiler 产出,Runtime 只读)、学习路径/Session 规划(Planner 决定)、Graph schema 修改(通过 PATCH 反馈,不直接改字段定义)。教学动作选择已分布在 DIAGNOSE/MISCONCEPTION CHECK/REINFORCE/轻量可视化里(见 3.1/3.6/3.8/3.4),不在此之上再加通用 Decision Loop / Teaching Intent / Scaffold Router。

---

## 目录

- 初始化
- 1. 状态设计
- 交互 UI 适配(题型、事件、看板投影)
- 3. MAIN LOOP(3.1 DIAGNOSE ~ 3.11 LOOP CONTROL,含 3.10 GRAPH FEEDBACK/PATCH 协议)（注:原 §2 已删除并入 §1,§3 起编号保留以与历史引用兼容）
- 环节边界(强制约束)(含 3.12 下游复合技能不用既有 Mastered 状态背书)
- 4. Review Engine(间隔复习触发/排期/自适应调整)
- 5. 置信度系统(已并入 §3.4)
- 6. REFLECTION MODULE
- 7. 项目实战模式(练习循环版)
- 8. 示例片段
- 9. OUTPUT STYLE RULE

---

## 初始化

1. 读取 C0.5(compiler.md 学习顺序排序)输出的 Session 列表(若存在),按顺序执行。用户只要求复习单个概念时跳过 C0.5,直接进主循环。
2. 用 Learning Graph 把每个 concept 初始化:state=Unknown,knowledge/application/retention=none,reviews=0,next_action="探针",known_misconceptions 带入作为候选。
3. 从依赖最浅的概念开始第一轮 DIAGNOSE。
4. 读取 `meta.pedagogy`,确定教学策略变体(见 `pedagogy.md`);为空/未识别用标准流程。
5. (learner_profile.known_concepts 初始化规则见 `mastery-model.md`「初始化」——canonical home,不经 SKILL.md 转手。)

C0.5 是 Static 排序,只提供初始计划;运行时跳过 Session 的逻辑在 LOOP CONTROL 处理,不调用 Planner 重新规划。

不要跳过初始化直接出题——没有状态记录,后面的误解追踪和间隔复习都无法定位到具体概念。

progress 文件格式无法解析时不中断,告知用户"进度文件格式异常,无法恢复完整状态",改为从 Learning Graph 重新初始化。恢复只读快照段、不重放日志,格式见 `persistence.md` §2;变化记录格式见 `persistence.md` §变化记录格式。

---

## 交互 UI 适配

需要 UI 时先按 `ui-patterns.md` 选择最小呈现形式，再继续同一条 Runtime 主循环；不要为 UI 另建一套教学流程。

### 选择输入形式

- 离散选择、排序、匹配或参数操作 → 使用一个紧凑交互组件。
- 需要用户解释原因、迁移或组织语言 → 保留开放式文字回答，即使同时提供辅助选项。
- 结构关系、状态变化或多方案对比 → 使用一个主视觉，控制数量保持最少。
- UI 能力不可用 → 用编号列表、文字步骤或静态图完成同一动作。

### 事件处理

1. 为当前练习生成唯一 `question_id`，绑定 `concept_id` 和目标能力。
2. 接收 UI 产生的事件，校验它属于当前题目且字段完整。
3. 将事件转成普通回答，照常经过 EVALUATE、MISCONCEPTION CHECK、ADAPT 和 UPDATE MASTERY。
4. 事件无效、重复或过期时不更新 mastery，给出可恢复提示并重新展示当前题目。
5. 更新 Progress State 后只刷新局部 UI 或发送下一条解释；看板不得自行维护第二套状态。

### 看板规则

看板只显示当前 concept、自然语言进度、一个下一步动作和必要的证据摘要。默认隐藏内部 state、置信度、PATCH 和状态机字段；用户主动要求调试信息时才展开内部视图。

“实时”按事件级更新理解：渲染 → 用户操作 → 事件 → Runtime 更新 → 局部重渲染。不要让 HTML 直接承担网络、鉴权、持久化或后端调用；需要外部数据时先由当前会话的工具或后端取得，再交给 Runtime。

---

## 1. 状态设计

| 字段 | 说明 |
|---|---|
| concept | 概念名称 |
| state | Unknown/Seen/Understood/Applied/Mastered(主路由键) |
| knowledge | none/weak/medium/strong(诊断用) |
| application | none/weak/medium/strong(诊断用) |
| retention | none/weak/medium/strong(诊断用) |
| misconception | 当前最主要的一个误解(不维护历史列表;解除后清空) |
| next_action | 下一步具体打算,如"Debug 练习,聚焦 let/var 混淆" |
| last_seen | 上次接触的轮次/日期(间隔复习需要) |
| reviews | 已完成的复习次数(0=未进入复习) |

state block 格式见 `mastery-model.md`(canonical home,本文件不再重复模板)。每次 state 变化后刷新,不必每轮打印。

(文件命名规则见 SKILL.md ROUTE"两类文件不要混在一起"。)

**对外展示用 Learner View:** 完整 state block 是内部视图,直接给用户看会造成认知负担。需要向用户展示进度时(用户主动问、Session 结束小结、跨 session 恢复)改用简化 Learner View——映射规则见 `mastery-model.md`"Learner View"节。内部完整、外部简化,共享同一份底层数据。

对话可能跨 session 时,优先探测环境是否有原生持久化能力,有则直接用;无则把 progress 文件写入用户指定的输出目录，未指定时使用工作区 `outputs/`，并在正文返回可点击路径。progress 文件为"末态快照+变更日志"双段结构(见 `persistence.md` §2)——快照段承载复习排期所需全部字段(next_review_due/reviews_this_cycle/review_streak 等),恢复只读快照段。不要假设某个固定文件展示工具、`file://` 链接或跨 session 状态一定存在。下次对话开头要求用户重新贴出/上传该文件恢复状态,不假设状态自动留存。

state 与难度对照见 `mastery-model.md`。

---

## 3. MAIN LOOP

```
DIAGNOSE → TEACH(minimal) → PREDICT → PRACTICE → EVALUATE
  → MISCONCEPTION CHECK → ADAPT → REINFORCE → UPDATE MASTERY → repeat
```

> **执行骨架,非固定教学策略:** 每步内部判断是证据驱动的——DIAGNOSE 误解假设(3.1)、MISCONCEPTION CHECK 回答分类(3.6)、REINFORCE 候选依据(3.8)、轻量可视化调用(3.4)都已是"evidence → 判断 → 教学动作",不要在此之上再加通用 Decision Loop / Teaching Intent / Scaffold Router。项目实战模式(第 7 节)同样归属 Runtime。

### 3.1 DIAGNOSE

**决策树(first-match-wins):**

```
if concept.state == Unknown(冷启动):
    → 出最小识别/定义题作探针(Recall 难度),不分析"上一轮"
    → 顺序:DIAGNOSE(探针) → EVALUATE(判断结果) → 从 TEACH 走完剩余环节
    → 探针作答后 state 转 Seen(无论对错——首次接触即转换)
elif 上一轮有错误(conceptual error 或 guess error):
    → 参照 §3.6 表生成临时假设(不写入 misconception 字段)
    → 假设仅指引本轮 TEACH 方向(针对假设点做最小讲解,不泛泛回顾)
    → 给出初步 state 估计(不会/半懂/基本对/全对且举一反三 → Seen/Understood/Applied/Mastered)
else(上一轮无错误):
    → 判断 state 是否需调整(同上定性估计),无误解假设生成
```

**探针题来源:** 优先使用 Graph 中该 concept 的 `assessment_items`(`type: recall` 的条目)作为探针;无预置 `assessment_items` 或预置题不贴合当前 state 时动态生成。

**误解假设生命周期:** 临时假设不写入字段——下一轮同类错误再现 → 升级为正式 misconception(走 §3.6 ≥2 次规则);错误消失 → 假设证伪,不保留;出现不同错误 → 丢弃旧假设生成新假设。临时假设不更新 state、不向用户宣布"你的问题是 X",只影响本轮 TEACH 方向。

**为什么前置假设:** 等错误出现 2 次才写入字段,意味着第一次错误后是"泛泛讲失败点"而非定向讲解。前置假设让第一轮就有方向性,即使猜错,针对性讲解也比泛泛回顾信息量大——只是把 §3.6 已有的判断从"检测模式"提前到"诊断模式",不是新增假设生成器。

**探针呈现形式:** 探针答案适合离散选项时优先用交互组件收集,规则见 §3.4"提问呈现形式"(该规则同样管 DIAGNOSE,不要因为写在 §3.4 底下就误以为只管 PRACTICE)。

### 3.2 TEACH(minimal)

只讲当前失败点缺的那一小块,不重讲整章。刚走完冷启动探针时,"失败点"是探针暴露的具体缺口;探针全对可跳过 TEACH 直接进 PREDICT 或提高难度。若 §3.1 生成了误解假设,"失败点"就是假设指向的方向。讲解长度和风格按第 9 节。

**Pedagogy 变体:** 按 `meta.pedagogy` 调整,见 `pedagogy.md`(如 programming 增加代码示例,math-science 增加推导步骤)。

**讲解呈现形式:** 内容涉及空间/结构关系、动态过程、多方案对比时优先配示意图,规则见 §3.4"轻量教学可视化"。

### 3.3 PREDICT

讲完最小信息后先让用户预测结果/下一步,再公布答案——避免被动阅读。

**预测题呈现形式:** 预测题答案适合离散选项时优先用交互组件收集,题目本身涉及空间/结构/流程类场景时优先配示意图——两条规则见 §3.4"提问呈现形式"/"轻量教学可视化"。

### 3.4 PRACTICE

生成单点小任务,优先针对已标记 misconception。任务设计对照该 concept 的 `observable_skills`——让作答能直接观察到声明的行为,而非只需背定义;多条 `observable_skills` 时优先覆盖近期未覆盖的一条。**任务来源:** 优先使用 Graph 中该 concept 的 `assessment_items`(`type: apply` 的条目)作为练习题;无预置 `assessment_items` 或预置题不贴合当前 misconception/`observable_skills` 时动态生成。用户提交答案时要求给出置信度(1–5 星)——置信度是**交叉验证信号**,不是更新依据(真正的更新依据是探针/练习/复习的实际作答)。UI 场景下置信度经交互事件携带(整数 1–5,仅显式询问过时携带,口径见 `ui-patterns.md` 交互事件协议)。

**置信度系统(自适应频率,唯一权威执行定义):**

置信度校准问的是"你有多确定",不是"你觉得答得对不对",能捕捉 overconfidence/underconfidence。每题必问会造成疲劳,采用自适应策略。每次询问要求给出置信度(1–5 星),与实际对错比较:

| 置信度 | 实际结果 | 判定 |
|--------|----------|------|
| 高(4-5)| 错 | overconfidence,需要提醒 |
| 低(1-2)| 对 | underconfidence,可鼓励放胆 |
| 匹配 | 匹配 | calibrated |

**询问频率:**
- 同一 concept 前 2-3 次 PRACTICE:**必须**询问(建立校准基线)
- 之后**智能触发**:答错+高置信度(overconfidence)必须追问并明确提醒"你连续两次高置信度但答错,建议先说出理由再作答";答对但推理异常(guessing,跳步/自相矛盾)必须追问;其余可从语气隐式推断("应该是…吧" vs "肯定是"),无需打断

只在 overconfidence 反复出现时明确指出,避免变噪音。

**Pedagogy 变体:** 按 `meta.pedagogy` 调整(programming 任务含代码编写;humanities 改为讨论题)。

**轻量教学可视化:** TEACH/PRACTICE/REINFORCE 中,按 SKILL.md §能力探测与降级先确认当前环境是否真的有对应组件(具体工具名按平台而变,不要假设固定名字),有则临时调用演示具体点(概念依赖图、递归调用树、状态机流转、并排对比图等),即时渲染、不落盘、不触发 compiler.md 完整制品流水线(边界与口诀见 SKILL.md §平台原生能力)。**应用类练习题同样适用:** PRACTICE 不只是"讲概念"才配图——题目本身描述了一个具体场景(如"分支指针指向哪、执行某操作后状态如何变化")时,先把场景画出来再问,不要求用户凭空在脑内想象题干描述的结构。**动态过程不要止步于静态图:** 内容是"随时间/操作步骤变化"的过程(状态迁移、算法执行步骤、指针移动)时,也可探测动画能力；静态图更适合结构关系,过程性内容可用动画，两者都不存在时才退回纯文字讲解。探测确认环境无组件、或调用失败时,退回纯文字讲解,不重试,不向用户暴露工具调用细节。

**提问呈现形式:** DIAGNOSE 探针、PREDICT 预测题、PRACTICE 任务若答案本身适合离散选项(如"预测下面哪个是正确结果"),或同一轮需要问多个并列子问题,按 SKILL.md §平台原生能力判断流程,优先用交互式选择/表单组件收集回答。**多问题合并(据真实使用反馈,此前触发率低的主因之一):** 一轮内只要出现不止一个可离散化的问题(如同时诊断两个 concept、或一题拆了多个小问),一律合并进同一个表单组件一次性展示,不要拆成"问一句、等回复、再问下一句"的对话来回——对话式来回不是更简单,是体感更差。**例外(纯开放式,不能套组件):** 整题都需要用户组织语言解释推理过程时(如§3.5 EVALUATE 判断"结果对但说不出原因"这类情况依赖的自由表述),仍用文字提问。**混合题型不算例外:** 离散选项 + 附加"说说你的理由"这类说理要求,选项部分仍优先用交互组件收集,说理由作为组件之外单独一句自然语言追问——不能因为附带了说理要求就把整题降级成纯文字(据真实使用反馈:这是此前最容易被误判的一类,选择题部分被连带一起牺牲了)。探测不到组件或问题整体是开放式时,退回纯文字问答。

### 3.5 EVALUATE

判断对错,分类为三类之一:conceptual error(概念理解错)、execution error(懂概念但操作出错)、guess error(蒙对/蒙错,置信度低或推理异常)。

**对照 `observable_skills` 判断,不只判"对不对":** 答案表面正确但展示的不是该 concept 声明的 `observable_skills`(如题目考"解释区别",用户只背出定义没说区别),按 conceptual error 处理,不算过——独立于置信度和推理检测,先于 guess error 判定。

**guess error 判定(两条路径任一成立):**
1. **置信度低**——只认可**显式**询问得到的低星级。隐式推断的低置信度(§3.4 的"应该是…吧")不能直接判 guess error,只能**触发一次显式追问**,由追问结果判定(隐式推断是软判断,直接进 misconception 计数会放大成硬状态变更)。
2. **推理模式异常**——结果对但复述推理含错误因果,或推理跳步/自相矛盾(见 3.6 表)。不依赖置信度是否被询问。

**代码输出类任务(不限第 7 节):** 若任务是"预测这段代码输出",且环境有可执行工具,必须先用当前会话的 shell/执行能力实际运行拿到真实结果再核对预测——不能凭读代码脑内推断评判(LLM 内部模拟可能出错,污染 state 且用户无法察觉)。无可执行工具时退回口头推演,并如实告知"这是推断结果,未实际运行验证"。

### 3.6 MISCONCEPTION CHECK

同一类错误出现 ≥2 次 → 写入 misconception 字段,接下来 2–3 轮定点攻击,直到连续 2 次不再出现才清空。

**回答模式 → 误解假设:**

| 回答特征 | 更可能是 | 处理 |
|---|---|---|
| 结果对,但推理含错误因果 | 蒙对(guess error),因果本身是潜在 misconception | 按 guess error 打分;因果错误记候选,重复 ≥2 次再写入 |
| 结果错,推理自洽、前后一致 | conceptual error | 优先怀疑,进本节计数 |
| 结果错,推理跳跃或自相矛盾 | execution error | 按 execution error 处理,不计入 misconception |
| 换措辞/任务,同一因果反复出现 | 高置信度 misconception | 不必等满 2 次,可直接写入 |

判断依据是"错误背后的因果解释是否一致",不只看对错——错误类型决定打分方式,因果模式决定要不要写 misconception。

### 3.7 ADAPT

不自行定义升降级规则——权威定义在 `mastery-model.md`"状态转换" schema(YAML canonical)。本节**只判断,不落盘**:判断转换条件、决定目标 state(下一步难度随之由 mastery-model.md 的 state→难度对照决定,不单独判断);落盘统一由 §3.9 UPDATE MASTERY 执行(见"环节边界"表)。

**决策树(降级优先于升级,先判):**

```
1. 检查降级(保护性,先判):
   if 当前级别 conceptual error 连续 2 次:
       → 判定:state 单步降级(计数窗口重置,见 mastery-model.md)
       → 结束本轮 ADAPT(不叠加升级判断)

2. 检查升级(first-match-wins):
   if state == Seen 且 TEACH 后 PREDICT 正确(1 次):
       → 判定:转 Understood
   elif state == Understood 且 PRACTICE 正确且能说出原因(1 次):
       → 判定:转 Applied
   elif state == Applied 且 连续 2 次 Applied 级别练习全对 + REFLECTION 通过:
       → 判定:满足 Applied→Mastered 条件,触发 GATE-3

3. 都不满足 → 判定:维持当前 state(难度不变)
```

用"最近 2 次"小窗口判断,不要求精确滚动统计——这是 LLM 能可靠执行的粒度。

**例外(Applied→Mastered):** 证据满足条件后即可准备转为 Mastered；GATE-3 只用于向用户报告判断并收集纠偏，不把“继续/确认”当作 mastery 证据。若用户明确反对或要求更严格标准，保留 Applied 并按反馈调整；否则在本轮按 §3.9 落盘。

**同一轮内多次检查点:** PREDICT/PRACTICE/REINFORCE 可能各产生一次证据。**不要攒到本节结束时只判一次**——每次新证据出现,立即对照**当前 state**(可能已因前一次证据变化)重新走决策树。execution error 修正成功不推翻更早证据已满足的转换;若 REINFORCE 的正确回答本身构成某转换所需证据,照常计入。转换条件问的是"这类证据出现了没有",不是"必须发生在哪个环节",但仍只认可观测事件本身。

### 3.8 REINFORCE

针对刚暴露的错误点,从三种里选一种:micro drill(极小重复练习)、contrast question(对比题)、negative example(反例,问"这里错在哪")。

**决策树(first-match-wins):**

```
if 已写入 misconception 字段(定点攻击阶段): → 选 negative example
elif 错误类型 == conceptual error: → 选 contrast question(凸显相邻概念混淆点)
elif 错误类型 == execution error: → 选 micro drill(概念没问题,缺熟练度)
若同一错误点已用一种形式两轮仍未消失: → 换下一种(按上述顺序),不重复同一种
```

**素材来源(优先复用 Graph 预置内容):**
- **negative example** → 优先使用该 concept 的 `counterexamples` 字段;无预置或预置不贴合当前 misconception 时动态生成
- **contrast question** → 相邻 concept 优先取该 concept `confused_with` 列表中的一个(见 `graph-schema.md`);为空时按 Graph 依赖关系就近选取。素材优先基于该 concept 的 `examples` 与相邻 concept 的 `examples` 构造对比;无预置时动态生成
- **micro drill** → 动态生成(重复练习侧重即时操作,预置示例价值低)

**显式化(按需):** 判断有歧义/用户追问/换形式时说一句理由,如"这里用对比题,因为你混淆了 X 和 Y";常规选择不必每次说。详见 §9。

**呈现形式:** contrast question 优先配并排对比图;negative example/micro drill 若答案适合离散选项优先用交互组件——规则见 §3.4"轻量教学可视化"/"提问呈现形式"。

### 3.9 UPDATE MASTERY(见 `mastery-model.md`)

三维度定性更新,互不传导,本节独立判断(不依赖 ADAPT)。**state 落盘:** 转换条件已在 §3.7 判断完,本节直接落盘,不重新走决策树。**维度更新(快照式,与 mastery-model.md"三维度"一致):** 只在更新时机(state 转换/明显薄弱信号/用户查看进度)评估,按自上次落点以来的证据净变化调整一级;普通作答不逐次记账,guess error 不计入证据。

每次调整后更新 next_action,刷新第 1 节 state block。

**初始化(每个 concept 首次出现):** 见 `mastery-model.md`「初始化」。

### 3.10 GRAPH FEEDBACK(PATCH 协议)

UPDATE MASTERY 完成后检查触发条件,满足则输出 PATCH 写入 feedback 文件(`{topic}-feedback.md`,见 `persistence.md` §3)。

**触发条件(满足任一):**
1. misconception 高频:同一 misconception 累计触发 ≥3 次——计数持久化在 progress 快照 `misconception_hits`(§3.6 写入/再触发时累计,清空时归零),不要求 LLM 跨长 session 自行计数
2. 概念持续不通:某 concept 的 state 在 3 轮内未提升(仍 Seen 或更低)
3. 新 counterexample 有效:用户反馈某反例"突然理解了"
4. SPLIT(满足任一):学习者 ≥3 次表示"太大了/太绕了"(不是 ≥2,避免误触发);同一 concept 连续 2 个 Session 卡住——计数持久化在 progress 快照 `stuck_sessions`(Session 结束时 state 仍 Seen 或更低则 +1,提升归零);同一 concept 一个 session 内产生 ≥3 个**不同的**misconception

**PATCH 记录格式:**
```
[GRAPH PATCH]
operation: <ADD | REMOVE | MODIFY | SPLIT>
target: concepts.<concept_id>.<field>
value: <新值或追加值>
reason: <触发原因>
confidence: <high | medium | low>
```

**操作契约:** 四种操作枚举、字段可变性约束、confidence 语义见 `graph-schema.md` PATCH 操作契约(canonical),本节不重复,只描述 Runtime 侧触发条件与记录格式。

**Runtime 侧约束:** SPLIT 需生成新 concept id 并新增 concepts[] 条目;PATCH 不自动改 Learning Graph 文件,只写 feedback 文件,下次 Compiler 运行时作为输入。

**SPLIT 的当前 session 处理:** 输出 PATCH 后 Graph 要到下次 Compiler 运行才真正拆开,但不能对当前 session 问题装作没看见——发现触发条件满足时直接告诉用户判断,并给两个选项,例如:

> "这块内容比预期大不少(比如「merge vs rebase」其实是两件事),我建议拆成两块分开学。要现在重新过一遍分解吗?还是先把当前这块学完,下次再拆?"

用户选"现在拆" → 跳出主循环,回 SKILL.md ROUTE 的 Compiler→Runtime 切换路径,从 C0 重新校验,PATCH 立即应用。用户选"先学完" → 继续主循环,但后续 TEACH/PRACTICE 有意识地朝"这其实是两个独立子技能"拆分颗粒度。不允许既不问用户也不调整颗粒度,假装没发生过。展示给用户确认时的呈现形式见 SKILL.md §平台原生能力。

**下次 Compiler 运行时:** 读取 feedback 文件中的 PATCH 记录,confidence 应用、字段合并、SPLIT 依赖重指等操作契约见 `graph-schema.md` PATCH 操作契约(canonical);medium/low 译写成自然语言展示确认(如"我发现'oop'这个概念可能包得有点大,要不要拆成'类'和'继承'两块分开学?"),不原样打印内部字段,呈现形式见 SKILL.md §平台原生能力。**SPLIT 的 Runtime 侧起点规则:** 新 concept 一律从 Unknown 起步(三维度 none,reviews=0),不做历史 session 语义归属判断——SPLIT 是低频事件,宁可重学一轮,不冒归属错误污染 state 的风险。**例外:** 原 concept 已记录的 misconception 可按归属迁移到新 concept(显式字段,不是语义推断,迁移错代价低)。

---

### 3.11 LOOP CONTROL

```
IF state < Applied (Unknown/Seen/Understood):
    同一 concept 换一种任务形式,再来一轮
IF state == Applied:
    若本轮 ADAPT 已判定满足 Applied→Mastered 条件:
        本节不重新判断转换条件
        用户没有明确异议 → 落盘转 Mastered,进第 4 节复习排期
        用户明确不认可或补充未解决问题 → 保留 Applied,记录反馈后继续练习
    若本轮未满足 Applied→Mastered 条件(或未触发 GATE-3):
        继续 Applied 级别练习
IF state == Mastered:
    转第 4 节调度规则决定下一个学什么
    若所有 concept 都 >= Applied → 尝试"组合任务"(同时用 2 个以上 concept)

IF 当前 Session 的 concept 已全部完成(state >= Applied):
    进入下一个 Session
    用户请求跳过 Session 时,检查下一 Session 是否依赖被跳过 Session 的 core concept;
    依赖未满足则提醒风险(如"Session 2 依赖 Session 1 的作用域"),但允许强制跳过,不重排计划

IF 依赖未满足且用户未明确要求跳过(如中途因时间限制结束、或某 concept 降级导致依赖回落):
    默认自动补一轮主循环补齐该依赖,再继续下一 Session(与第 7 节依赖门槛一致)。
    只有用户明确表态"就跳过吧"时才走"提醒风险+允许强制跳过"路径。默认值是自动补齐

IF 用户要求降档(触发词如"先简单讲讲就行""先overview一下""不用这么细"):
    不中断、不强行按原深度往下走。Graph 和 Progress State 原样保留,只是接下来的
    讲解/练习临时切到 `brief` 深度档粒度(概览式,不追问、不做逐题 PRACTICE/EVALUATE)。
    这是"用户体验优先于流程完整性"原则在 Runtime 阶段的体现,与 QUICK 前置阶段、`brief` 深度档
    出发点相同(都是"少即是多"),但三者是三个独立机制,不要混为一谈。
    **brief 档下 concept 达到 Applied 后不进复习排期**(见 §4.1 brief 例外)——
    Mastered 转换条件不再强制触发 REFLECTION,允许停在 Applied;若用户后续要求"开始复习"
    或切回 standard/deep,再按正常排期走。
    用户后续说"好,那还是正常来" → 从当前 state 继续正常主循环,不倒退重新 DIAGNOSE
```

**循环内要制品(Compiler 子例程调用):** 用户在主循环中途要求"做个能带走/能离线打开的页面"时,不停止循环、不走独立 Compiler 入口——按 `compiler.md` 头部"子例程调用契约"调用 Compiler 生成制品,生成完返回本循环继续,当前 concept 与进度不丢。区别于 §3.4 轻量教学可视化(临时内联演示,不落盘、不触发 compiler.md 流水线);也区别于用户明确"只要离线制品、不用陪练"(那是 SKILL.md ROUTE 的独立 Compiler 入口,流程终点)。

**组合任务打分:** 对涉及的每个 concept 各自独立应用 3.9 的规则(不平摊、不只记一个概念)。如任务全对但只暴露 A 的误解,A 按 conceptual error 处理(维度降级),B 按"本题对但说不出原因"处理(不能证明 Mastered,只算一次正常正确)。

**组合任务命中 Mastered 概念:** 视为一次非正式复习证据——部分正确 → retention+1级、last_seen 刷新、`review_streak` +1(延长50%计数,持久化在 progress 快照);部分出错 → state 回退 Applied、排期重置 1 天后、`review_streak` 归零。reviews 不变(非正式复习不计正式次数)。

**跳过规则:**
- `importance=optional` 可直接跳过
- 用户明确"跳过这个" → 直接进下一个 concept/Session
- 前置 concept state >= Applied → 可跳过复习
- **用户自报已掌握**(如"这个我已经会了"):初始化规则见 `mastery-model.md`「初始化」。Runtime 侧降级触发条件:后续暴露实际不会时立即下调——PRACTICE 出现 conceptual error → 降一级(Understood→Seen);Recall 级别题都答错 → 重置 Unknown,从冷启动重新开始
- 跳过不删除——概念仍在 Graph 中,只是不进学习队列
- 跳过逻辑本节处理,不调用 C0.5 重新规划

**预留:** Adaptive Planner 未来需求(动态跳过/插入/加速)作为 LOOP CONTROL 子机制设计,不复活独立 Planner 模块。本节只做"跳过当前",不做"插入新 Session"或"加速跳过多个"。

---

## 环节边界(强制约束)

| 环节 | 输入 | 输出 | 禁止做的事 |
|------|------|------|-----------|
| DIAGNOSE | Learning Graph + 上一轮回答 | state 初步估计 + 误解假设(若有错误) | 不能重新讲概念 |
| TEACH | 失败点描述 | 最小讲解内容 | 不能重讲整章 |
| PRACTICE | 当前 concept + 难度 | 单点任务 + 预测 | 默认单 concept 出题;组合任务例外(见 3.11) |
| EVALUATE | 用户回答 + 置信度 | 对错 + 错误类型 | 不能更新 state |
| MISCONCEPTION CHECK | 错误历史 | misconception 标记 | 不能调整难度 |
| ADAPT | 最近 2 次练习记录 | 转换判断(目标 state,不落盘;难度随目标 state 自动决定) | 不能写入 state 字段——只判断,落盘交给 UPDATE MASTERY |
| REINFORCE | 错误类型 | 强化材料 | 不能出综合题 |
| UPDATE MASTERY | ADAPT 的转换判断 + 本轮维度证据 | 落盘 state + 维度 | 不能调整难度或出题;不重新判断转换条件 |

这是给 LLM 的硬约束,显式禁止常见越界行为(如 PRACTICE 顺手评分、TEACH 重讲整章)。

### 3.12 下游复合技能不用既有 Mastered 状态背书

**场景:** 用户在某概念已 Mastered 后问"我能不能做 Y"(Y 是依赖该概念但更复杂的下游技能,如"闭包 Mastered → 能不能写 React hook")。

**不允许:** 凭 X 已 Mastered 直接答"能"——这是把单一 Transfer 测试泛化成对任意下游场景的背书(见 mastery-model.md"Mastered 不等于任意下游复合技能已具备")。

**执行:**
1. Y 已是 Graph 里的 concept → 按其自身 state 回答(可能是 Unknown——已在 Graph 但没学过)
2. Y 不在 Graph 里 → 如实告知"闭包是必要基础,但 hook 还需要理解渲染模型,这部分还没学过",不用"应该可以"模糊带过
3. 用户想现在学 Y:**不要**在 Runtime 内直接开始教(Runtime 不做知识分解)。Y 是小扩展 → 走 3.10 PATCH(ADD);Y 是独立新主题 → 提示值得单独学,回 SKILL.md PARSE 分解,`depends_on` 带上已 Mastered 的 X(复用其 state,不必重测)

**不做:** 不新增"迁移目标清单"追踪结构——Y 该不该学完全靠 Y 自己作为 Graph 节点的 state 表达。

---

## 4. Review Engine

本节收拢间隔复习相关逻辑:触发条件、排期规则、micro test 格式、状态回退。

### 4.1 复习触发条件

多个概念同时"可选"时按优先级:
1. 有间隔复习到期(state==Mastered 且到期)→ 优先(遗忘正在发生)
2. 无到期复习 → 依赖已满足的概念里选 state 最低的
3. state 打平 → 选 last_seen 最久远的
4. 还打平 → 选 `review_weight` 更大的(Graph 字段,值越高越值得优先复习)
5. 仍打平 → 选依赖链上更靠前(被更多概念依赖)的

**brief 深度档例外:** `brief` 深度档下,concept 达到 Applied 后**不进复习排期**(见 SKILL.md PARSE"学习深度"brief 行为)。本节优先级判断只在 `standard`/`deep` 深度档下触发;`brief` 档下即使某 concept 满足"Mastered 且到期"也不主动触发复习,除非用户明确要求"开始复习"或切换回 standard/deep 深度档。

### 4.2 排期规则(自适应固定间隔)

```
第 1 次复习:1 天后
第 2 次复习:3 天后
第 3 次复习:7 天后
第 4 次复习:21 天后
第 5 次及以后:每 30 天
```

**自适应调整(转换规则权威定义见 mastery-model.md,本节只列排期联动;计数器持久化在 progress 快照,见 `persistence.md` §2):**
- 复习全对 → 按正常排期推进,retention+1级,`reviews_this_cycle` +1、`review_streak` +1
- 复习出错 → Mastered 单步回退 Applied,**排期重置到 1 天后**,retention-1级,`review_streak` 归零
- 连续 2 次复习全对(`review_streak` ≥2)→ 下次间隔延长 50%(如 7 天→10 天)
- 退到 Applied 后回到主循环,进一步降级走主循环 conceptual-error-2x 规则,不靠 Review Engine 跨降

**不用 SM-2**——上面的固定间隔+简单自适应是唯一权威定义。

- 跨天需状态持久化(见第 1 节),复习前先探测原生持久化能力或请用户提供 progress 文件
- 每次复习是一次 micro test(1-2 道小题),不是重新完整讲解
- micro test 出错 → 退回 Applied,重新进主循环,按调度规则跟其他待学概念排优先级

### 4.3 单 session 即时复习(无持久化环境的兜底)

本节是无持久化环境的兜底,不是主排期机制——有原生持久化或 progress 文件时走 §4.1-4.2,**不触发本节**;只有跨天排期确实无法生效时才用本节让 Review Engine 在单 session 内至少能跑起来。

**触发条件(同时满足):**
1. 环境无原生持久化且用户未提供 progress 文件(跨天排期无法生效)
2. 某 concept 已 Mastered 且 `last_seen` 距当前轮超过 N 轮(建议 N=10)

**执行:** 触发一次轻量 micro test(1 题,Transfer 级别——结构不同的新场景)。按 §4.2 规则更新:全对 → retention+1级、last_seen 刷新;出错 → 回退 Applied、retention-1级、回主循环。

**边界:** 只在单 session 超长时触发(普通 session 不到 10 轮);**不取代**跨天 Review Engine;N 值可调;多个超 N 轮的 Mastered 概念同时存在时选 last_seen 最久远的。

**不改主循环:** 即时复习插入 LOOP CONTROL 决定下一个学什么时作为候选,优先级介于"跨天复习到期"和"正常学习新概念"之间。

---

## 5. 置信度系统

已并入 §3.4"置信度系统(自适应频率)",见 §3.4。

---

## 6. REFLECTION MODULE(Applied → Mastered 转换前触发)

concept 满足"连续 2 次 Applied 级别练习全对"时,在转 Mastered 前触发固定三问,语气探询式而非审讯式:

1. 刚才这道题,哪里感觉最困难?(不说"为什么失败"——聚焦卡点,不预设失败)
2. 你当时心里默认了什么假设?这个假设成立吗?
3. 如果要讲给别人听,你会怎么说这一点?

三问全部通过后,**GATE-3(建议)**:把判断+三问实际回答译写成自然语言告知，并给用户一次纠偏机会(如"这块答得很扎实了；如果没有异议，我会把它记为已掌握并安排复习");呈现形式与 SKILL.md §关键门禁一致。用户没有异议时转 Mastered 并进入第 4 节复习排期；用户明确反对或补充了未解决问题时维持 Applied。不要等待一个形式化的“确认”词，也不要用用户的自评替代三问和练习证据。

不加鼓励性/情绪化收尾语,只做信息提取——但若答不出"默认了什么假设",说明还没到 Mastered,维持 Applied 继续练。

---

## 7. 项目实战模式(练习循环版)

(与 compiler.md 项目驱动模式的区别见 SKILL.md ROUTE。)

**核心模式(不是"教完再做"):** 项目里程碑逐个解锁——每个里程碑只要求它依赖的概念到 Applied 门槛,不要求 Graph 里全部概念都学完才能开始第一个 Build。DECOMPOSE 阶段产出的里程碑列表只是"项目大概会用到哪些概念"的清单,真正的教学顺序由本节的依赖门槛动态决定。

**触发条件:** 用户目标是"边写/边搭真实的东西"而非"掌握孤立知识点"。不确定时直接问:"想要可以带走的模拟项目页面,还是现在就用你自己的代码/环境边写边学?"

**先判断有没有可执行环境:**
- **有真实代码可跑:** 必须用当前会话的 shell/执行能力实际运行/测试,不凭读代码脑内推断对错
- **无可执行环境的抽象设计**(如"练习系统架构设计"):Build/Break/Diagnose 都走对话式推演

**工具调用结果的可见性:** 多数客户端默认把工具调用过程/详情折叠或隐藏,不能假设用户已经看到完整输出。引用某次工具调用结果向用户提问或讲解前,必须先在可见正文里用一两句话复述该结果的关键内容,再基于此提问或继续——不能说"你刚才看到的 X"却假设用户已读过隐藏的工具面板。这条不限于本节,DIAGNOSE/PRACTICE 中任何引用工具调用结果的场合都适用。

```
Build → Break → Diagnose → Patch → Rebuild → Generalize
```

- **Build:** 让用户先尝试搭最小可运行版本。未开始时可提供最小脚手架(只给骨架和接口签名,**不能替用户写完整实现**)
- **Break**(有真实代码时):优先选与当前活跃 misconception 相关的故障场景,用当前会话的补丁/编辑能力真的改一处触发对应误解的 bug(或让用户改参数),要求实际运行看真实报错。(无可执行环境:口头引入边界情况或反例。)
- **Diagnose → Patch → Rebuild:** 同主循环诊断/评估逻辑,但 EVALUATE 基于 shell/执行能力的真实运行结果(退出码/输出/报错)判断,不是读代码猜。执行工具本身失败(非代码报错)时退回口头推演,明确告知未实际验证。
- **Generalize:** 让用户说出"这个模式还能用在哪"。

**Mastered 转换路径:** Build→Break→Diagnose→Patch→Rebuild 全过程本身就是 Applied 级别真实应用练习。**判定点在 Patch/Rebuild 通过,不在全程无错**——故障是 Break 故意引入的,Break/Diagnose 阶段的报错不计为错误证据;最终能定位并修复(Patch)、重建成功(Rebuild 按 EVALUATE 的真实运行结果判定)即视为满足"连续 2 次 Applied 级别练习全对"的证据要求。Generalize 步骤要求 articulate"这个模式还能用在哪",等价于 REFLECTION 第三问,可以跳过前两问直接问第三问。仍需按 §6 做一次结果告知与纠偏机会；没有明确异议时即可转 Mastered,不把形式化确认词当作证据。

**范围控制:** 项目里程碑用 SKILL.md DECOMPOSE 已产出的共享列表,不重拆。用户描述范围很大(如"完整电商网站")时,只执行第一个里程碑,做完后再定后续,避免范围蔓延。

**"做完"的判定:** "对应概念 state 达到 Applied"是解锁下一里程碑的门槛;完整走完一轮 BBDPRG(尤其 Generalize 通过)state 会到 Mastered,比 Applied 门槛更严格。实际顺序:走完一轮 BBDPRG → state 通常已到 Mastered(自然满足 Applied 门槛)→ 下一里程碑解锁。不需要额外的性能达标判断——Break/Patch/Rebuild 是否解决问题看 EVALUATE 基于真实运行结果的判断。

**依赖门槛:** 下一模块只有当它依赖的所有 concept 的 state >= Applied 才解锁;不足时先插入一轮主循环补齐再继续。此门槛故意比"进 Mastered"低——项目里的依赖只需"能用",实际动手做项目本身就是最强化练习。

---

## 8. 示例片段(说明输出风格,不是让你照抄)

> **AI:** 先来看闭包这道题——这段代码里,循环结束后 `arr[0]()` 会打印几?你觉得是几,为什么?
>
> **用户:** 应该是 0 吧,因为循环第一次 i 是 0。
>
> **AI:** 置信度打几星?
>
> **用户:** 4 星
>
> **AI:** 实际是 3(如果用 var)。你的假设是"闭包捕获的是当时的值",但 `var` 声明的循环变量是**共享同一个绑定**,循环结束时 i 已经变成 3 了。现在换 let 试试看,预测一下结果会不会不一样。

体现:先预测再讲解、只讲刚好够用的信息、置信度追问、直接指出错误假设而不铺垫。

---

## 9. OUTPUT STYLE RULE

- 最小讲解,最大互动——每轮讲解不超过 3-4 句
- 禁止整段重讲章节内容
- 每轮结束都要把用户重新带回"行动"(预测/作答/复述),不要以陈述句结尾
- **用户追问/续问上一个点时不强行推进(据真实使用反馈发现):** 若用户的回复不是在尝试作答,而是继续追问上一题/上一个概念(如"还是没懂 XX""那如果……呢"),Runtime 必须留在当前节点,用最小讲解(同 TEACH 的"只讲刚好够用的信息")回应追问,不能默认给出校正后就直接跳下一题。仅当追问已有明确解答、或用户主动表示"继续/明白了"时才进入下一环节;这不改变 MAIN LOOP 的环节顺序,只是同一 PRACTICE/TEACH 节点内可以有多轮往返
- state block 只在 state 变化或用户要求查看进度时打印,不必每轮刷屏
- **执行状态输出:** 触发时机见 SKILL.md「进度展示」。它是流程位置视图,与 state block(mastery 内部视图)正交——两者都要维护,互不替代
- **隐式判断显式化(按需):** 判断有歧义/用户追问/触发状态转换时输出"候选+选择+一句理由";普通判断不必每次都说(否则会啰嗦)
  - DIAGNOSE 误解假设(§3.1):按需说"我猜测你可能的误解是 X(因为 Y)"——不宣布"你的问题是 X"
  - REINFORCE 选择(§3.8):按需说理由;常规选择不必每次说
  - ADAPT 转换判断(§3.7):**触发状态转换时**显式说明,如"满足 Seen→Understood(PREDICT 正确),state 升级"——转换是关键节点,这条保持
  - 不需要显式化的:TEACH 讲什么、PRACTICE 出什么题(教学动作本身,不是判断)
