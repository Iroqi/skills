# Persistence Layer

> **何时读取:** 跨 session 时读取。
>
> 学习状态持久化规范,定义跨 session 文件结构(见下方"文件清单")、读写规则与变化记录格式。跨 session 学习时必须使用,单 session 内可省略。
>
> **命名约定:** 文件扁平化存放于用户指定的输出目录；未指定时优先使用当前工作区的 `outputs/`。如果客户端提供原生会话存储，则优先使用原生存储，不假设某个固定目录一定存在。

## 内容导航

- [文件清单](#文件清单)
- [四类持久化文件](#1-topic-learning-graphmd--learning-graph-快照)
- [变化记录格式](#变化记录格式change-record)
- [触发时机与证据](#触发时机)
- [使用规则](#使用规则)
- [持久化策略与介质选择](#持久化策略与介质选择)
- [不做什么](#不做什么)

**接口契约:**
- **Input:** "继续上次学习"类请求,或用户上传的 `{topic}-progress.md`/`{topic}-learning-graph.md`
- **Output:** 恢复的 Learning Graph + Runtime Progress State(供 SKILL.md ROUTE 之后直接进 runtime.md 主循环);或新写入的 progress 文件
- **约束:** 单 session 内可省略,不强制持久化;文件命名/存放路径与 SKILL.md、runtime.md 保持一致,不另建目录结构
- **职责边界:** 只定义跨 session 文件结构、读写时机与变化记录格式。不定义:Graph 内部字段含义(见 `graph-schema.md`);Progress State 内部字段含义(见 `mastery-model.md`);PATCH 如何生成(见 `runtime.md` §3.10、`graph-schema.md` PATCH 操作契约);state 转换规则本身(见 `mastery-model.md`)

---

> ## 分区一:文件格式规范
> 以下内容定义输出目录下 4 种文件的结构、字段与读写规则。

## 文件清单

```
<output-dir>/
├── {topic}-learning-graph.md    ← Learning Graph 快照（Compiler 输出）
├── {topic}-progress.md          ← Runtime state（末态快照 + 变更日志双段，见 §2）
├── {topic}-feedback.md          ← 已应用的 PATCH 日志（只追加，不修改）
└── {user}-prefs.md              ← 用户学习偏好（pedagogy, goal, learner_profile 覆盖）
```

**命名规则:** `{topic}`=主题 URL-safe 标识(小写、连字符、无空格,如 `javascript-closures`);`{user}`=用户标识(session 标识或用户名);扩展名 `.md`;存放目录为已确定的 `<output-dir>`。

---

## 1. {topic}-learning-graph.md — Learning Graph 快照

**写入时机:** Compiler 输出 Graph 后。**内容:** 完整 Learning Graph(单层 `concepts[]`,格式见 `graph-schema.md` 文本格式)。

**读写规则:** Compiler 写入完整 Graph;Runtime 只读,用于初始化状态;Feedback 不直接改此文件——PATCH 记录写入 `{topic}-feedback.md`,下次 Compiler 运行时合并。

---

## 2. {topic}-progress.md — Runtime 进度

**结构:末态快照 + 变更日志双段。** 快照段承载恢复所需的全部当前状态,每次保存整体重写;日志段只追加、仅供人工回看,恢复时不重放。跨天 Review Engine 依赖的全部计数器(到期时间/复习次数/streak/误解触发数)都在快照段,不从日志推算。

**写入时机:** Runtime 初始化时(写入 PLAN 段+各 concept 初始状态);每次 Session 结束、用户明确要求保存、或 session 即将中断时刷新快照段。

**内容——快照段(每次保存整体重写):**
```
[PROGRESS SNAPSHOT — {topic}]
saved_at: <ISO 8601>

[PLAN]                                # C0.5 输出的 Session 列表(Runtime 初始化时写入,见 SKILL.md PLAN 节),学习看板与跨 session 恢复共用
Session 1 (~15 min): variable-scope, closures
Session 2 (~20 min): promises (依赖 Session 1)

[CONCEPT — closures]
state: Applied
knowledge: medium | application: weak | retention: none
misconception: 闭包复制变量
misconception_hits: 2                 # 当前 misconception 写入以来累计触发次数;字段清空时归零(runtime.md §3.10 触发条件 1 计数)
next_action: Debug 练习,聚焦 let/var 混淆
last_seen: 2026-08-21 (turn 14)       # 日期供跨天排期与优先级判断;轮次供 runtime.md §4.3 单 session 即时复习
reviews: 2                            # 历史累计正式复习次数,降级不清零(参考值)
reviews_this_cycle: 0                 # 自本次进入 Mastered 以来的正式复习次数,排期(第1次:1天后/第2次:3天后/…)依据;重进 Mastered 归零
review_streak: 0                      # 连续复习全对计数,含复合任务等非正式全对证据(延长50%依据);复习出错归零
next_review_due: 2026-08-24           # 仅 Mastered 概念非空;离开 Mastered 时置空
stuck_sessions: 0                     # 连续"Session 结束时 state 仍 Seen 或更低"次数,提升时归零(runtime.md §3.10 SPLIT 触发条件计数)
```

**内容——日志段(只追加,不改历史):**
```
[SESSION — turn N]
concept: closures
state: Unknown → Seen
knowledge: none → weak
application: none → none
retention: none → none
misconception: 闭包复制变量
next_action: 最小应用练习

[CHANGE RECORD — <concept_id>]        # 可选，格式见本文 §变化记录格式（证据驱动，无数值评分）
```

**读写规则:**
- **恢复只读快照段**——按 `[CONCEPT — <id>]` 块直接还原各 concept 末态(含排期与全部计数器),不重放日志;日志段仅供审计回看
- 日志段在 concept 状态变化时追加一条;`[CHANGE RECORD]` 块在 concept 首次转 Mastered 时追加一次(之后复习重新评估则追加新一条,不覆盖旧记录)
- 快照段计数器维护时机:`misconception_hits` 在 runtime.md §3.6 写入/再次触发同一 misconception 时累计,字段清空时归零;`stuck_sessions` 在每次 Session 结束保存时评估刷新(state 仍 Seen 或更低 → +1,有提升 → 归零);`reviews_this_cycle` 在每次正式复习(micro test)完成后 +1、重进 Mastered 归零;`review_streak` 在复习或复合任务全对时 +1、出错归零;`next_review_due` 按 runtime.md §4.2 排期表计算后写入(`reviews_this_cycle` 决定第几次间隔,`review_streak` ≥2 时延长 50%)

---

## 3. {topic}-feedback.md — 已应用的 PATCH 日志

**写入时机:** PATCH 被确认应用时(confidence=high 自动应用,medium/low 用户确认后)。

**与 runtime.md §3.10 格式的关系:** `runtime.md` §3.10 定义 Runtime 生成 PATCH 时的**生成格式**( Runtime 写入时的字段);本节定义 PATCH 被确认应用后持久化到 feedback 文件时的**存储格式**——在生成格式基础上追加 `applied`/`applied_at` 两个审计字段,记录应用状态与时间戳。两种格式不是冲突,是同一 PATCH 记录在生命周期不同阶段的视图:生成时只有 `operation/target/value/reason/confidence`,持久化时补上 `applied/applied_at`。

**内容(存储格式):**
```
[FEEDBACK PATCH — {topic}]
generated_at: <ISO 8601 时间>

[PATCH — #1]
operation: ADD
target: concepts.closures.misconceptions
value: "认为闭包会阻止垃圾回收"
reason: "本 session 中出现 3 次，原 misconceptions 列表无对应项"
confidence: high
applied: true
applied_at: <ISO 8601 时间>
```
(生成格式见 `runtime.md` §3.10,不含 `applied`/`applied_at` 字段。)

**读写规则:** Runtime 在 PATCH 确认应用时追加;Compiler 下次运行时读取并合并到 Graph;只追加,不改历史。

---

## 4. {user}-prefs.md — 用户学习偏好

**写入时机:** CLARIFY 阶段,或用户手动调整时。

**内容:**
```
[USER PREFERENCES — {user}]
pedagogy: programming
goal: 面试准备
learner_profile:
  background: 5年 Python 经验
  pace: fast
updated_at: <ISO 8601 时间>
```

**读写规则:** CLARIFY 写入推断的 pedagogy+goal;用户手动调整时可覆盖任何字段;Runtime 初始化时读取,覆盖 Graph 的 meta 字段。

**known_concepts 不入本文件:** `known_concepts` 是 topic 级数据(concept_id 只在对应主题的 Graph 内有意义),只存在于各主题 Graph 的 `meta.learner_profile`(见 `graph-schema.md`)。若放进 user 级 prefs,换主题时旧 concept_id 恰好复用会把上一主题的未验证自报带入新主题、错误初始化为 Understood,因此明确排除。

**覆盖规则:** preferences 字段优先级高于 Graph 的 meta 字段(pedagogy/goal/background/pace);preferences 未指定则用 Graph meta;Graph meta 也未指定则用默认值(见 `pedagogy.md`)。`known_concepts` 不在覆盖范围——只认当前主题 Graph 的值。

---

> ## 分区二:变化记录格式
> 以下内容定义学习前后变化的 canonical 记录格式(原 evaluation.md 职责,已并入本节)。
> 与分区一的"文件存储"是不同关注点:分区一管"存哪、怎么读写",这里管"记录什么、怎么记录"。

## 变化记录格式(Change Record)

> **不是评分系统。** 不产生任何数值分数,进步由 `before.state → after.state` 的状态跃迁体现,证据由实际作答的文字快照体现。用户自评是 optional reflection,不参与 mastery 判定(与 mastery 更新必须基于真实证据这条原则一致)。

```yaml
change_record:
  concept_id: closures
  before:               # 学习前的证据快照
    state: Unknown
    evidence: "首次探针：预测 makeCounter 输出错误，误以为闭包复制变量值"
  after:                # 达到 Mastered 时的证据快照
    state: Mastered
    evidence: "能预测 let/var 闭包差异并解释绑定机制；REFLECTION 三问通过"
    effective_intervention: "对比题(let vs var 并排执行)"   # 可选，见下文
  sessions_used: 3
  timestamp: "..."
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `concept_id` | string | 对应 `concepts[].id` |
| `before.state` | state | 学习前状态（通常 Unknown 或 Seen） |
| `before.evidence` | string | 学习前的可观测证据（探针/练习的实际作答，不是自评分） |
| `after.state` | state | 学习后状态（通常 Mastered） |
| `after.evidence` | string | 学习后的可观测证据（REFLECTION/综合任务的实际表现） |
| `after.effective_intervention` | string，可选 | 见下方"Teaching Outcome Trace" |
| `sessions_used` | int | 从 before 到 after 使用的 Session 数量 |
| `timestamp` | ISO 8601 | 记录时间 |

**Teaching Outcome Trace(轻量记录约定):** 某个具体教学动作(REINFORCE 的对比题/负例、TEACH 的某种讲法、轻量可视化演示、项目实战里的调试)明显起作用时,在 `after.effective_intervention` 里记一句自由文本(如"对比题(let vs var 并排执行)")。非强制字段,没有起效动作可留空,不倒推不臆测;就是一句人话描述,纯粹是给未来人工回看的历史备注,本身不是证据,是证据之外的一句备注。

---

## 触发时机

**before 记录:** 首次接触 concept 时(state==Unknown 且 last_seen 为空),在 DIAGNOSE 探针之后记录——记录探针的实际作答(对/错+错在哪),不是自评分。

**after 记录:** concept 首次转为 Mastered 时,在 REFLECTION MODULE(`runtime.md` §6)通过后记录——记录三问的实际回答+最后一道综合任务的实际表现。

---

## 证据来源(Evidence-based,优先级)

1. **探针题**——首次接触时的真实作答(冷启动 DIAGNOSE)
2. **实际任务**——PRACTICE/项目实战中代码实际运行的结果(不是读代码推断)
3. **解释能力**——REFLECTION 三问中能否说清楚"错在哪、默认了什么不成立的假设、怎么讲给别人听"

三类都是可观测事件,与状态机转换触发条件一致,不引入额外评判维度。

---

## 用户自评(optional reflection)

"我感觉懂了"/"还是有点虚"可作为 optional reflection 记录在备注里,但:**不参与** mastery 状态转换;**不参与**路由决策(不因"感觉懂了"跳过练习);仅作学习者自我觉察参考和给 Runtime 的弱信号(持续 underconfidence→多鼓励;overconfidence→多给验证题)。

---

## 使用规则

不强制触发(用户可跳过);before 只记一次;after 可多次(复习后 state 再次达到 Mastered 时追加新记录);sessions_used=before 到 after 之间的 Session 数量;不产生数值,进步由状态跃迁体现;不参与 mastery 判定,不回写 Graph 或 Progress State。

---

> ## 分区三:持久化策略
> 以下内容定义介质选择与兜底策略,是跨"存储"与"变化记录"两区的运维层规则。

## 持久化策略与介质选择

**只追加,不修改:** progress 和 feedback 文件只追加新记录,不改历史。

**跨 session 必用:** 对话可能跨天/跨 session 时必须持久化。

**探测顺序(原生优先,文件回退):**
1. 检查环境是否有跨对话记忆或 key-value 存储能力
2. 有 → 直接使用,告知用户"进度已自动保存"
3. 无/不确定 → 回退到文件方案(上方文件清单字段定义不变,变的只是存储介质)——写入后返回客户端支持的可点击工作区路径；不要假设固定的文件展示工具或 `file://` 链接存在

原因:文件往返(用户手动下载→下次手动上传)摩擦过高,导致间隔复习在实践中几乎不触发,原生持久化能消除这一摩擦。

**恢复流程:** 原生持久化 → 下次对话自动恢复;文件方案 → 要求用户重新贴出/上传 progress 文件。恢复完成后"接下来学/复习哪个 concept"不需要新触发逻辑,直接按 `runtime.md` §4.1 复习触发条件走(有到期复习的 concept 优先)——这只是"决定下一步学什么"的众多时刻之一,不是特例。

---

## 不做什么

不做版本控制(PATCH 日志本身是版本历史);不假设原生持久化能力一定存在(探测后不可用则回退文件方案);不强制用户手动操作文件(有原生能力时优先使用,减少摩擦);不做强制测试;不做数值评分(没有 pre_score/post_score/delta);不让用户自评参与 mastery 判定;不做横向比较;不做等级认证;不与状态机平行(状态机已是进步的权威记录,变化记录只是给状态跃迁附一段证据快照)。
