# Mastery Model

> **何时读取:** 运行时读取一次。
>
> 定性状态替代数值阈值做主路由。三维度降为定性级别(none/weak/medium/strong)仅做诊断,
> 不参与路由。不使用 overall 加权公式、数值阈值、饱和规则(LLM 无法可靠跨轮次做精确算术,
> 事件驱动转换才是可靠执行的粒度)。

## 内容导航

- [状态机](#状态机)
- [状态转换](#状态转换事件驱动不基于数值)
- [三维度](#三维度诊断用不参与路由)
- [State Block 格式](#state-block-格式)
- [Learner View](#learner-view对外简化的用户视角)
- [初始化](#初始化)

**接口契约:**
- **Input:** runtime.md 主循环产生的事件(诊断结果/练习作答/复习表现/误解检测)
- **Output:** state 转换(Unknown→Seen→Understood→Applied→Mastered,含降级路径)+ 三维度定性级别(诊断用,不参与路由)
- **约束:** Progress State 不写入 Graph 本体;状态更新必须基于真实证据(探针/练习/复习的实际作答),不能凭学习者一句"我懂了"。状态机自身约束(事件驱动、降级单步)见下文 schema
- **职责边界:** 只定义 state 转换规则与三维度定性级别。不定义:哪个教学动作触发转换(见 `runtime.md` §3.9);Graph 字段结构(见 `graph-schema.md`);跨 session 如何持久化(见 `persistence.md`);基于 mastery 状态的策略调整见 pedagogy.md §掌握度驱动调整

---

## 状态机

状态是 mastery 的主路由键,所有路由决策(进不进复习、要不要再练、能不能学下一个)都基于状态,不基于数值。

```
Unknown → Seen → Understood → Applied → Mastered
                                          ↑↓
                                     (review cycle)
```

| 状态 | 含义 | 对应难度 | 出题方式 |
|------|------|---------|---------|
| Unknown | 尚未接触 | Recall | 直接提问定义/最小识别任务 |
| Seen | 已接触,能部分复述 | Recall→Apply | 让用户先复述,再做最小应用 |
| Understood | 能解释概念 | Apply | 给一个新场景要求套用 |
| Applied | 能在新场景中使用 | Debug | 给一段有 bug 的例子让用户揪错 |
| Mastered | 已掌握并进入复习排期 | Transfer | 迁移到相邻但不同的场景 |

**Transfer 严格度:** "迁移到相邻但不同的场景"≠换个变量名/数字的表层变体——那只是同一场景重复,不构成 Transfer。真正的 Transfer 要求概念应用到**结构不同**的语境(换编程语言/换问题类型/从"解释代码"换成"用这个概念解决新问题")。判断标准:能靠模式匹配蒙对 = 不算;需重新理解概念在新结构里如何起作用 = 算。只收紧 Mastered 一格的诊断标准,不改变转换规则或新增评估维度。

**Mastered 不等于任意下游复合技能已具备:** Transfer 测试只验证迁移到**某一个**结构不同场景,不代表能迁移到**任意**下游场景(如"闭包 Mastered"≠"能写 React hook"——后者还需闭包之外的知识)。不得凭某概念已 Mastered 就对"我能不能做 Y"直接给肯定答案,处理规则见 `runtime.md` §3.12——不新增追踪维度,用 Graph 依赖机制表达:Y 要么是已诊断的独立 concept,要么应作为新 concept(`depends_on` 含该已 Mastered 概念)走正常分解,不能靠状态推断。

### 状态转换(事件驱动,不基于数值)

**Formal Contract(YAML schema 为 canonical 定义):** 以下 schema 是状态机唯一形式化定义,Runtime/Mastery/Evaluation 全部引用此 schema,不得各自用散文重新解释。冲突时以 schema 为准。

```yaml
# mastery_state_machine — canonical definition
states: [Unknown, Seen, Understood, Applied, Mastered]
initial_state: Unknown

transitions:
  - { from: Unknown,    to: Seen,        event: probe_answered,                    note: "首次接触，无论对错" }
  - { from: Seen,       to: Understood,  event: predict_correct_after_teach,       note: "TEACH 后 PREDICT 正确（1次）" }
  - { from: Understood, to: Applied,     event: practice_correct_with_reason,      note: "PRACTICE 正确且能说出原因（1次）" }
  - { from: Applied,    to: Mastered,    event: applied_correct_twice_plus_reflection, note: "连续2次Applied全对+REFLECTION通过" }
  # 降级（单步回退，保护性）
  - { from: Mastered,   to: Applied,     event: review_failed_once,                note: "复习出错1次，排期重置1天后" }
  - { from: Applied,    to: Understood,  event: conceptual_error_twice,            note: "当前级别conceptual error连续2次" }
  - { from: Understood, to: Seen,        event: conceptual_error_twice,            note: "同上" }
  - { from: Seen,       to: Unknown,     event: conceptual_error_twice,            note: "极少，首探针是蒙的" }

counting_window:
  object: "同一concept、同一错误类型的连续累计"
  reset_on: [state_transition, correct_response_non_guess, concept_switch]
  correct_response_excludes_guess: true   # guess error 不打断 conceptual error 连击
  degradation: single_step                 # 一次只降一级，不存在跨级降

# 状态更新须基于真实证据,不凭感觉；本 schema 只定义状态机自身转换规则
```

(散文说明已并入上面 schema 的 `note` 字段,不再重复一张对照表——冲突时以本 schema 为准。)

**计数窗口规则(消除时序歧义):**

1. **计数对象:** 同一 concept、同一错误类型(复习出错/conceptual error)的连续累计。
2. **重置时机**(满足任一即清零):发生一次状态转换(无论升降);出现一次**正确**响应(打断错误连击);切换了 concept。
   **"正确响应"的最低标准:** 蒙对(EVALUATE 判为 guess error 的正确作答)**不算**"正确响应",不打断 conceptual error 连击——若连蒙对都能清零计数,真实存在的误解可能靠运气蒙对几次就一直不被处理。只有非 guess 的正确作答才打断连击。
3. **单步回退:** 所有降级都是**单步**的,不存在"一次事件降两级"。严重遗忘的级联仍会发生,只是走确定路径:Mastered→(1次复习错)→Applied→(继续 conceptual error 连续2次)→Understood→…,每一步单步、可观测、可重置。

---

## 三维度(诊断用,不参与路由)

保留用于诊断学习者薄弱模式,不用于状态转换或路由。

| 维度 | 级别 | 含义 | 证据类型(净变化评估用) |
|------|------|------|------------------|
| knowledge | none/weak/medium/strong | 能正确解释概念 | 正向:TEACH/PREDICT 正确;负向:conceptual error |
| application | none/weak/medium/strong | 能在新场景中使用 | 正向:PRACTICE 正确;负向:execution error |
| retention | none/weak/medium/strong | 间隔复习后的记忆 | 正向:复习正确;负向:复习出错 |

**级别升降(快照式,非逐次记账):** 只在下方"更新频率"列出的时机评估,每次评估按**自上次落点以来的证据净变化**调整一级——净正向证据多 → +1级(不超 strong);净负向证据多 → -1级(不低于 none)。不逐题累计、不做逐次 ±1 流水账。级别定性,LLM 只需判断"自上次落点以来,这段时间的表现整体变好还是变差"。

**guess error 不映射任何维度:** 蒙对的正确作答不能证明维度提升,蒙错也不提供"哪个维度薄弱"的定向信息——与计数窗口"guess 不打断连击"(见"状态转换"节)立场一致。guess error 的价值在触发 `runtime.md` §3.5 的显式追问,追问结果按实际判定的错误类型正常计入上表。

**更新频率:** 三维度不每轮维护,只在以下时机更新:state 发生转换时(同步刷新)、出现明显薄弱信号时(如 conceptual error 连击)、用户主动要求查看进度时。普通正确作答/小错误不触发更新——三维度是快照式诊断,不是流水账,只在状态变化或明显信号时落点。

**三维度不参与路由**——路由只看状态,三维度只服务于策略调整(见 pedagogy.md §掌握度驱动调整),不要用三维度替代或补充 state 做路由判断。

---

## State Block 格式

每次状态变化后刷新,不必每轮打印,只在状态变化或用户要求时展示。

```
[LEARNING STATE — <topic> — updated turn N]
| concept        | state      | knowledge | application | retention | misconception          | next_action        | last_seen | reviews |
|----------------|------------|-----------|-------------|-----------|------------------------|--------------------|-----------|---------|
| variable scope | Applied    | strong    | medium      | medium    | 混淆 let/var           | Debug 练习         | turn 4    | 0       |
| closures       | Understood | medium    | weak        | none      | -                      | 最小应用练习        | turn 2    | 0       |
| promises       | Mastered   | strong    | strong      | strong    | -                      | 下次复习: 3天后     | turn 8    | 2       |
```

**字段说明:** `concept`←Learning Graph;`state`←Mastery Model(主路由键);`knowledge`/`application`/`retention`←Mastery Model(诊断级别);`misconception`←Runtime(当前最主要误解,无则-);`next_action`←Runtime;`last_seen`←Runtime;`reviews`←Runtime(0=未进入复习)。其中 `next_action`/`last_seen`/`reviews` 三个字段由 Runtime 维护,Mastery Model 只读不写。

**`reviews` 在降级后的含义:** 概念离开 Mastered 后 `reviews` **不清零**——代表历史上一共完成过多少次正式复习,是累计参考值。但排期表(4.2 的"第1次:1天后/第2次:3天后…")**不看 `reviews` 总数**,只看"自本次重新进入 Mastered 以来是第几次复习"——重新达到 Mastered 后固定从"第1次:1天后"重新起跳,不因历史值大就跳到长间隔。两者是独立计数,不共用同一数字。

**与 progress 快照的关系:** state block 是运行时展示/上下文视图;持久化以 `persistence.md` §2 快照段为准——排期计数器(`reviews_this_cycle`/`review_streak`/`next_review_due`,以及 PATCH 触发用的 `misconception_hits`/`stuck_sessions`)都存在快照里,state block 不单列这些字段。

### Learner View(对外简化的用户视角)

完整 state block 是**内部**视图,字段多像调试日志,直接展示会造成认知负担。用户主动问进度/Session 结束小结/跨 session 恢复时,改用下面的 Learner View,不直接打印内部表:

```
[学习进度 — <topic>]
已掌握: 变量作用域、闭包
正在练习: 递归思维（发现薄弱点：递归终止条件）
下一步: 把递归用到实际任务
待复习: Promise（3 天后到期）
```

**映射规则:** state==Mastered 且未到期→"已掌握";Mastered 且复习到期→"待复习"并标注到期时间;state==Applied→"正在练习";Understood/Seen→"正在学";Unknown→不显示;misconception 非空→括注"发现薄弱点:…";next_action→"下一步"一行。

**何时用哪个:** 内部 state block 只在 state 变化时刷新到 progress 文件/Runtime 上下文,不主动展示;Learner View 在用户要求看进度/Session 结束/跨 session 恢复时展示。两者共享同一份底层数据,只是呈现粒度不同。

---

## 初始化

每个 concept 首次出现:`state: Unknown` / `knowledge: none` / `application: none` / `retention: none` / `reviews: 0`。

**用户自报已掌握(known_concepts):** state 设为 Understood(跳过冷启动探针),三维度均设为 medium,next_action 标注"用户自报,未验证"。后续表现暴露实际不会时允许立即下调(降至 Seen 或 Unknown),不必等到复习周期。
