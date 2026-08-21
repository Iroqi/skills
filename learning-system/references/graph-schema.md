# Learning Graph Schema

> **何时读取:** 分解阶段读取一次。
>
> Learning Graph 完整数据规范。单层 `concepts[]` 数组,字段级可变性标记替代层级拆分。
> 只保存知识结构,不保存学习过程状态——mastery 字段(state/三维度/reviews)不在 Graph 中,
> 由 Runtime 维护在独立 Progress State(见 `mastery-model.md`)。这样同一份 Graph 能被
> 多个学习者复用,不会因为一个人的进度污染知识结构本身。Graph 如何生成见 `compiler.md`。

## 内容导航

- [顶层结构](#顶层结构)
- [Concept 条目](#concept-条目)：字段说明与可变性
- [PATCH 操作契约](#patch-操作契约)
- [Milestone 条目](#milestone-条目可选项目驱动模拟驱动模式使用)
- [文本格式](#文本格式compiler-输出用)
- [兼容性](#兼容性)

---

## 顶层结构

```yaml
meta:
  topic: <主题名称>
  goal: <用户学习目标，可选>
  pedagogy: <教学策略标签，由 CLARIFY 阶段确定>
  key_insight_concept_id: <concept_id>  # 关键洞察节点，可选。Compiler 读取（DECOMPOSE 第 4 步定位 + 概念地图设计标记），Runtime 不读取
  learner_profile:                # 可选，PARSE 从用户表述中提取
    background: <如"5年 Python 经验"/"编程零基础">  # 上下文型 metadata，无显式消费规则——LLM 上下文自然影响 TEACH 风格，不参与路由判断
    known_concepts: [<concept_id>, ...]   # 用户自报已掌握，初始化规则见 `mastery-model.md`「初始化」
    pace: fast | normal | slow      # C0.5 读取，调整 Session 时长上限（fast→30min/normal→25min/slow→15min。方向是故意的：pace 指学习者节奏偏好，节奏快者单次学习坐得住更久故上限更高，偏好短小 Session 者上限更低——不是笔误，勿“修正”方向），不改变 Graph 结构
  generated_at: <生成时间>
  graph_version: <int>  # 可选，纯 metadata。首次生成=1，每次 Compiler 因 PATCH 重新生成时 +1

concepts:
  - <Concept 条目，见下方定义>

milestones:           # 可选：项目驱动/模拟驱动模式使用
  - <Milestone 条目，见下方定义>
```

---

## Concept 条目

每个 concept 是完整对象,含知识字段和教学元数据,字段级可变性标记决定哪些可被 Feedback 修改。

```yaml
concepts:
  - id: <唯一标识符>
    name: <可读名称>
    summary: <一句话定义>
    # —— 结构性知识（immutable，除非知识本身被修正）——
    depends_on: [<concept_id>, ...]
    # —— 可追加的知识字段 ——
    misconceptions:
      - misconception: <描述>
        frequency: high | medium | low
    confused_with: [<concept_id>, ...]   # 可选,与哪些概念容易被混淆(结构关系,非误解内容本身)
    examples:
      - <示例>
    counterexamples:
      - <反例及解释>
    # —— 教学元数据（可调整）——
    difficulty: low | medium | high
    importance: core | supporting | optional
    estimated_time: <整数，分钟数>
    observable_skills:
      - <可观测的技能描述>
    assessment_items:
      - type: recall | apply | transfer
        prompt: <题面>
    review_weight: 1.0 | 1.5 | 2.0
```

### 字段说明

| 字段 | 必填 | 可变性 | 说明 |
|------|------|--------|------|
| `id` | 是 | immutable | 唯一标识符，英文小写+连字符 |
| `name` | 是 | immutable | 人类可读名称 |
| `summary` | 是 | immutable | 一句话定义 |
| `depends_on` | 否 | immutable | 前置 concept 的 id 列表（结构性依赖） |
| `misconceptions` | 否 | mutable-append | 常见误解列表，含频率标记(`frequency` 子字段为描述性 metadata,供设计时参考;Runtime §3.6 的"≥2 次"计数逻辑**不读取**此字段,只看实际错误出现次数) |
| `confused_with` | 否 | mutable-append | 容易混淆的概念 id 列表,与 `misconceptions` 正交——`misconceptions` 描述误解内容本身,`confused_with` 描述"和哪个概念混"这层结构关系,可能其一为空。用途:DECOMPOSE 识别到互相混淆的概念对时双向写入;C2 递进决策据此安排相邻/对比结构(见 `compiler.md` C2);概念地图设计的"易误解节点标记"可据此取得数据来源,不再仅靠主观判断 |
| `examples` | 否 | mutable-append | 正向示例 |
| `counterexamples` | 否 | mutable-append | 反例 |
| `difficulty` | 否 | mutable | 决定初始教学策略。默认 medium |
| `importance` | 否 | mutable | 决定学习优先级。默认 core |
| `estimated_time` | 否 | mutable | 单 concept 预计学习时长（分钟）。默认 15 |
| `observable_skills` | 否 | mutable-append | 评估时可观测的行为描述 |
| `assessment_items` | 否 | mutable-append | 预置评估题模板(Runtime §3.1/§3.4 优先复用 `type: recall`/`type: apply` 条目,无预置时动态生成) |
| `review_weight` | 否 | mutable | 复习权重(1.0/1.5/2.0)。默认 1.0。用作 Review Engine §4.1 优先级判断的第 4 级 tiebreaker(值越大越值得优先复习) |

**可变性规则:** `immutable` 只能由 Compiler 生成或知识修正时改变,不因学习反馈而变;`mutable` 可因 Feedback PATCH 调整;`mutable-append` 可因 PATCH 追加新值。

---

## PATCH 操作契约

Runtime 提出、Compiler 应用的 Graph 反馈机制(触发条件与记录格式见 `runtime.md` §3.10)。四种操作:

- `ADD`:追加 `mutable-append` 字段值(misconceptions/confused_with/counterexamples/examples/assessment_items/observable_skills)
- `REMOVE`:删除字段值(仅 misconceptions 且已被证明不成立时)
- `MODIFY`:修改 `mutable` 字段值(difficulty/importance/estimated_time/review_weight)
- `SPLIT`:拆分 concept,生成新 concept_id;原 concept 的 `depends_on` 允许重新指派给新 id(SPLIT 操作的内在要求),但 `id`/`name`/`summary` 不得修改——原 concept 保留身份,新 concept 走 ADD 流程独立生成

**谁能读写什么:** Compiler 读写全部字段(生成时);Runtime 全部只读,不直接写 Graph 本体(mastery 状态写入独立 Progress State);Feedback 只能改 `mutable`/`mutable-append` 字段,不动 `immutable` 字段。

**Confidence 语义:** `high`→自动应用,下次 Compiler 运行时直接合并;`medium`→展示给用户确认后应用;`low`→仅记录,需用户明确同意才应用。

**外部输入源冲突:** PARSE 阶段读取的外部资料(URL/PDF/粘贴文本)与已有 Graph 描述冲突时,按 MODIFY 走 PATCH 确认流程,不静默覆盖(confidence 通常 `medium`)。外部资料带来的全新 concept 按 `ADD` 处理。

---

## Milestone 条目(可选,项目驱动/模拟驱动模式使用)

```yaml
milestones:
  - id: <唯一标识符>
    name: <里程碑名称>
    introduces_concepts: [<concept_id>, ...]
    depends_on: [<milestone_id>, ...]
    deliverable_type: real-project | simulation
    deliverable_owner: assistant | user   # assistant=Compiler项目驱动制品(用户自操作); user=项目实战模式(助手陪同真实编写)
    acceptance_criteria:
      - <验收标准>
```

| 字段 | 必填 | 可变性 | 说明 |
|------|------|--------|------|
| `id` | 是 | immutable | 唯一标识符，英文小写+连字符 |
| `name` | 是 | immutable | 人类可读名称 |
| `introduces_concepts` | 是 | immutable | 该里程碑覆盖的 concept id 列表 |
| `depends_on` | 否 | immutable | 前置 milestone 的 id 列表 |
| `deliverable_type` | 是 | immutable | `real-project`(真实项目文件) \| `simulation`(单 HTML 内状态机)，决定走 compiler.md 哪条分支 |
| `deliverable_owner` | 是 | immutable | `assistant`(Compiler 项目驱动制品，用户自操作) \| `user`(项目实战模式，助手陪同真实编写)，与 `deliverable_type` 正交，无默认值——必须显式指定 |
| `acceptance_criteria` | 否 | mutable-append | 验收标准列表 |

可变性规则与 concept 字段一致,milestone 字段目前全部为 immutable 或 mutable-append,不存在单值调整的 mutable 字段。

---

## 文本格式(Compiler 输出用)

```
[LEARNING GRAPH — <topic>]
goal: <用户学习目标，可选>
pedagogy: <教学策略标签>
graph_version: <int>

=== Concepts ===
1. id: variable-scope | name: 作用域 | summary: 变量可被访问的代码区域 | depends_on: [] | misconceptions: [混淆词法作用域与动态作用域(frequency: high)] | examples: [函数内部可访问外部变量] | counterexamples: [动态作用域中函数内部看不到外部变量]
   difficulty: medium | importance: core | estimated_time: 15 | observable_skills: [能解释词法作用域与动态作用域的区别] | assessment_items: [type: recall, prompt: "什么是作用域?"] | review_weight: 1.0
2. id: closures | name: 闭包 | summary: 函数连同其词法环境的引用 | depends_on: [variable-scope] | misconceptions: [闭包复制变量(frequency: high)] | examples: [makeCounter 返回的函数记住 count] | counterexamples: [普通函数不记住调用时的环境]
   difficulty: high | importance: core | estimated_time: 20 | observable_skills: [能解释闭包与局部变量的区别] | assessment_items: [type: apply, prompt: "预测 makeCounter 的输出"] | review_weight: 1.5

关键洞察节点: closures

里程碑（可选）:
1. id: m1 | name: 计数器 | introduces_concepts: [closures] | deliverable_type: real-project | deliverable_owner: user
```

**格式规则:** 每个 concept 占两行(知识字段/教学元数据,缩进对齐);空字段省略(依赖默认值);`关键洞察节点` 是 `meta.key_insight_concept_id` 的文本呈现。

---

## 兼容性

遇到不符合本规范的旧格式 Graph 文件,告知用户格式不兼容,按纯主题词重新走完整分解(见 `compiler.md`),不强行迁移损坏数据。
