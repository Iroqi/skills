# Pedagogy Layer

> **何时读取:** Runtime 模式中根据 `meta.pedagogy` 读取对应变体。
>
> 教学策略变体定义,Runtime 主循环根据学科类型选择对应策略变体。不是可插拔模块系统,
> 是一组预设变体——表格够用,不需要框架级抽象。

## 内容导航

- [核心原则](#核心原则)
- [类型检测](#类型检测)
- [策略变体矩阵](#策略变体矩阵)
- [掌握度驱动调整](#掌握度驱动调整)
- [变体对照示例](#变体对照示例)
- [变体应用方式](#变体应用方式)
- [不做什么](#不做什么)

**接口契约:**
- **Input:** Graph `meta.pedagogy` 标签(由 compiler.md 在 CLARIFY 阶段确定,一经写入全程不变)
- **Output:** TEACH/PRACTICE/REINFORCE 环节的变体调整规则(不产出 Graph 字段,只影响 Runtime 主循环怎么执行既有环节)
- **Invariant:** Graph 不变,Pedagogy 可变——本文件描述"怎么教",不描述"教什么";不做运行时动态切换,不做用户自定义策略
- **职责边界:** 只定义各学科类型的教学变体规则、类型检测关键词表与掌握度驱动调整规则(canonical home)。不定义:Graph 字段结构(见 `graph-schema.md`);state 转换机制(见 `mastery-model.md`);CLARIFY 触发条件与 Goal→Graph 映射(见 `compiler.md`)

---

## 核心原则

Learning Graph 不变,Pedagogy 可变。同一份 Graph,不同学科类型走不同教学流程。Graph 提供"教什么",Pedagogy 提供"怎么教"。

下文各学科的"维度侧重"是**定性说明**——解释哪个维度对该学科更重要,为各变体的 TEACH/PRACTICE/REINFORCE 规则提供理由,不序列化进 Graph、不参与计算。策略调整直接由各变体的环节规则编码。

---

## 类型检测

pedagogy 类型在 compiler.md CLARIFY 阶段确定,判定规则见本节。无法从 Goal 推断则让用户选择。

| 主题关键词 | 学科类型 |
|-----------|---------|
| 编程、框架、工具、系统、网络、数据库 | programming |
| 数学、物理、化学、统计 | math-science |
| 历史、哲学、伦理、政治 | humanities |
| 语言、写作、口语 | language |
| 设计、艺术、音乐、摄影、绘画、乐器、烹饪、舞蹈 | arts |

**跨类型时"主导"判定(first-match-wins):**
1. 用户明确声明学习目标 → 直接采信
2. **核心目标 vs 实现工具:** 问"目标是理解某概念本身,还是使用某工具/语言"(如"用 Python 模拟蒙特卡洛"→目标是概率/采样原理,Python 是载体→math-science;"学 Python 装饰器"→目标就是语言特性本身→programming)
3. 仍不确定 → 选交付物形式更依赖哪种技能(产出代码→programming;产出证明/推导→math-science)

---

## 策略变体矩阵

每个变体定义 TEACH/PRACTICE 环节调整+维度侧重说明。

### programming(编程/系统)

| 维度 | 侧重 | 理由 |
|------|------|------|
| knowledge | 中 | 理解语法和概念是基础 |
| application | 高 | 编程核心在写代码 |
| retention | 低 | 语法遗忘可通过查文档弥补 |

| 环节 | 标准行为 | 变体 |
|------|---------|------|
| TEACH | 最小讲解 | 讲解后增加 1-2 行代码示例 |
| PRACTICE | 单点任务 | 任务包含代码编写/调试,优先使用代码编辑器 |
| REINFORCE | 微练习/对比题/反例 | 优先使用 negative example(给 bug 代码让用户找) |

**特殊规则:** PRACTICE 默认包含可运行代码片段;用户有真实代码环境时走项目实战模式(见 runtime.md §7)。

### math-science(数学/物理)

| 维度 | 侧重 | 理由 |
|------|------|------|
| knowledge | 高 | 理解定理和推导是核心 |
| application | 中 | 应用建立在对原理的理解之上 |
| retention | 低 | 公式可查,但推导逻辑需记忆 |

| 环节 | 标准行为 | 变体 |
|------|---------|------|
| TEACH | 最小讲解 | 讲解后增加推导步骤 |
| PRACTICE | 单点任务 | 任务要求写出推导过程,不只是答案 |
| REINFORCE | 微练习/对比题/反例 | 优先使用 contrast question(对比相似公式/定理) |

**特殊规则:** PRACTICE 要求展示中间步骤;评估检查推导逻辑,不只是最终答案。

### humanities(历史/伦理/哲学)

| 维度 | 侧重 | 理由 |
|------|------|------|
| knowledge | 低 | 事实是载体,不是目标 |
| application | 中 | 能在讨论中运用观点 |
| retention | 高 | 观点和论证框架需长期记忆 |

| 环节 | 标准行为 | 变体 |
|------|---------|------|
| TEACH | 最小讲解 | 讲解后增加多方观点对比 |
| PRACTICE | 单点任务 | 改为讨论题/案例分析,要求用户表达立场和理由 |
| REINFORCE | 微练习/对比题/反例 | 优先使用 contrast question(对比不同学派/时期观点) |

**特殊规则:** 去掉纯练习题改为开放式讨论;评估关注论证质量,不只是事实正确性;置信度系统仍适用(用户对自己论点的确信程度)。

### language(语言/写作)

| 维度 | 侧重 | 理由 |
|------|------|------|
| knowledge | 低 | 语法规则可随时查 |
| application | 中 | 能产出语言是核心 |
| retention | 高 | 语感和常用表达需内化 |

| 环节 | 标准行为 | 变体 |
|------|---------|------|
| TEACH | 最小讲解 | 讲解后增加模仿练习 |
| PRACTICE | 单点任务 | 任务要求产出语言(写/说),不只是选择题 |
| REINFORCE | 微练习/对比题/反例 | 优先使用 micro drill(重复练习同一语言点) |

**特殊规则:** PRACTICE 产出是语言文本,不是代码或数字;评估关注准确性和流畅度。本变体覆盖书面语言学习和口语的认知层面(对话策略/表达结构),不覆盖发音纠正和实时口语操练(属不适用场景)。

### arts(设计/艺术/音乐)

| 维度 | 侧重 | 理由 |
|------|------|------|
| knowledge | 低 | 理论和历史是背景 |
| application | 高 | 创作/分析能力是核心 |
| retention | 中 | 风格记忆重要,但可通过参考弥补 |

| 环节 | 标准行为 | 变体 |
|------|---------|------|
| TEACH | 最小讲解 | 讲解后增加视觉/听觉示例 |
| PRACTICE | 单点任务 | 任务要求创作/分析作品 |
| REINFORCE | 微练习/对比题/反例 | 优先使用对比实验(并排比较不同风格/技法) |

**特殊规则:** 环境支持时用 ImageGen 或音频生成作教学材料;评估关注审美判断和技法运用。

---

## 掌握度驱动调整

基于 mastery-model.md 的三维度诊断结果(knowledge/application/retention 的 none/weak/medium/strong 级别),在三维度出现显著差异时自动调整教学策略。判断时机:每次状态变化后。

| 模式 | 判断条件 | 含义 | 调整动作 |
|------|---------|------|---------|
| 知但不会用 | knowledge ≥ medium, application ≤ weak | 理解概念但缺乏实操经验 | 增加应用导向的 PRACTICE |
| 会用但记不住 | application ≥ medium, retention ≤ weak | 能操作但容易遗忘 | 提高复习频率 |
| 快速学习者 | 连续 2 次状态提升 | 掌握速度快于预期 | 跳过部分 micro drill |
| 全面薄弱 | 三维度都 ≤ weak | 基础不牢 | 放慢节奏,回到 TEACH |

**不做:** 不自动修改 Learning Graph(策略调整是运行时的,不是 Graph 演化);不永久改变 difficulty 或 importance;调整只影响当前 session 的教学行为。三维度级别定义与诊断机制见 `mastery-model.md`。

---

## 变体对照示例

programming 与 math-science 在 TEACH/PRACTICE/REINFORCE 三环节的具体差异见上方各自变体表(§策略变体矩阵),此处只补两表都没覆盖的对照维度:

| 环节 | programming 变体 | math-science 变体 |
|------|----------------|------------------|
| 探针形式 | 预测代码输出 | 计算一个概率值 |
| 误解高发点 | 闭包复制变量 / let vs var | P(A\|B) vs P(B\|A) 混淆 / 基础率忽视 |

两者的状态机、主循环环节、PATCH 协议完全一致——pedagogy 变体只影响 TEACH/PRACTICE/REINFORCE 三个环节的**内容形态**,不影响流程结构。这就是本文件"Learning Graph 不变,Pedagogy 可变"原则的具体体现。

---

## 变体应用方式

写入 `meta.pedagogy` 字段:
```yaml
meta:
  pedagogy: programming  # 或 math-science / humanities / language / arts
```
Runtime 主循环读取该字段,在对应环节应用变体规则。

**默认行为:** 为空/未识别 → 使用标准流程(无变体)。

**"无变体"的边界:** 标准流程指**不主动追加**学科特色内容。主题本身必须的例子(讲循环提一行伪代码)不算"暗地里走了 programming 变体"——变体是**主动选择增强手段**,不是"内容里出现像某学科的东西就算数"。

---

## 不做什么

不做可插拔的 Pedagogy 模块系统(表格够用);不做用户自定义策略;不做运行时动态切换(pedagogy 在分解阶段确定,全程不变)。

真实使用中发现某学科变体需要改进,可直接修改本文件——新增/调整教学法变体不需要额外走什么审批流程。
