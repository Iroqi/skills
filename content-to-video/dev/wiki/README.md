# dev/wiki — 自进化闭环（WikiSkill 三层架构）

本目录是本技能的 **Wiki Layer + 门控基础设施**，只在**维护/迭代本技能**时用到，
**不参与任何一次视频制作**。做法来自 WikiSkill（arXiv 2608.27454，
Google Research + Virginia Tech）。

## 一句话

> 每次跑完留下证据 → 从证据里蒸馏经验 → 每次只改一处 → 门控不过就回滚。
> **skill 会回滚，经验不会。**

## 三层与落点

| 层 | 落点 | 回滚规则 | 谁产生 |
|---|---|---|---|
| **Raw Layer** | `~/.config/ai-video/traces/*.json`（可用 `CTV_TRACE_DIR` 改） | 只追加；可剪枝 | `scripts/run.py` 每次实跑结束时旁路写一条 |
| **Wiki Layer** | 本目录（patterns/、logs.md、skill-impact.md） | **永不回滚** | 维护 agent + `wiki_maintain.py` |
| **Skill Layer** | `SKILL.md`、`references/`、`config/`、`scripts/` | 门控红即回滚 | `wiki_propose.py` + `wiki_gate.py` |

> 回滚**一律用文件级快照**（`.snapshots/`），**不用 `git checkout --`**——技能目录里
> 可能有维护者未提交的在制品，checkout 会把它们一起抹掉。git 只记录当时的 HEAD
> 供事后对照，不参与回滚动作。

**Raw Layer 为什么不在技能目录里**：`SKILL.md` 明确要求"产物一律写项目目录，
不写技能目录"。trace 是机器高频产生的运行数据，塞进 `dev/` 既违反本技能自己的
约定，又会在技能重装/更新时被冲掉。用户级目录与既有的 `~/.config/ai-video/.env`
同级，符合本技能"全局配置放用户目录"的既有惯例。想把 trace 收进仓库可设
`CTV_TRACE_DIR=<技能目录>/dev/wiki/traces`，此时务必把它加进 `.gitignore`。

## ⚠️ 执行态隔离（消融实验的硬约束）

论文消融给了个反直觉结论：

| 配置 | 得分 |
|---|---|
| 完整 WikiSkill | **68.1%** |
| 去掉 wiki 层 | 63.7% |
| **把 wiki 直接塞给执行态 agent** | **60.9%** |

**wiki 是离线优化侧的资产，不是运行时提示词。** 因此：

- 执行本技能做视频时（SKILL.md 的五步工作流），**禁止读取 `dev/wiki/**`**
- `SKILL.md` 的核心工作流区**不得引用** `dev/wiki/traces` 或 `patterns/`
  ——这条由 `dev/check.py` 的 `执行层纯度` 检查机械把关
- 想让"历史教训"影响执行行为，正确做法是**把它蒸馏成 SKILL.md / references/
  里的一条具体规则**，而不是把整个 wiki 挂进上下文

## 一次迭代的四步

```bash
# ① 推理（执行态，不看 wiki）：正常做一次视频，或跑 dev/evals.json 的 forward-test
python scripts/run.py --source segments_source.json -o audio_output

# ② 蒸馏：把新轨迹聚成一份有界摘要（≤5 失败 + ≤3 成功，每条 ≤15000 字符）
python dev/wiki_maintain.py --brief --since-last
#    → 由维护 agent 照摘要写/改 dev/wiki/patterns/*.md
python dev/wiki_maintain.py --commit        # 校验 pattern 契约

# ③ 提议：读 patterns，产出**恰好一处**原子改动（默认只落盘，不改 SKILL.md）
#    -o 落盘；不给 -o 则打到 stdout。--from 可重复，--target 预填目标文件
python dev/wiki_propose.py --scaffold --from p03-ffmpeg-encoder-absent \
       --target scripts/pipeline.py --risk low -o dev/wiki/proposals/xxx.json
#    → 填完 rationale / old / new / expected_impact 后
python dev/wiki_propose.py --validate dev/wiki/proposals/xxx.json

# ④ 门控：应用 → 跑 gate → 绿则留痕，红则回滚 skill 层（wiki 层保留）
python dev/wiki_gate.py --apply dev/wiki/proposals/xxx.json --gate fast
```

## 目录内容

```
dev/wiki/
├── README.md              本文件（三层契约 + 操作流程）
├── PURPOSE.md             技能目标与不可破的不变量（改动的最终裁决依据）
├── logs.md                每次维护的追加流水（只增不改）
├── skill-impact.md        每条 skill 改动 → 门控结果对照表
├── patterns/              蒸馏出的经验，一类一文件（frontmatter 有契约）
├── proposals/             待应用的原子改动提案
│   └── incubating/        被门控拒掉的提案（不删，可复活）
├── .snapshots/            无 git 时的 skill 层文件级回滚快照
```

> `traces/` 目录已于 1.5.47 移除：轨迹默认落 `~/.config/ai-video/traces/`（见
> 上表），`CTV_TRACE_DIR` 指向任意目录时 `scripts/_trace.py` 会自建
> （`os.makedirs`）。包内留一个"默认永远为空"的目录只是负担，它当时还附带
> 一条"记得 gitignore"的提醒——而 `.gitignore` 里从没有这一条，说明该提醒
> 从未被需要过。
>
> `proposals/`、`proposals/incubating/`、`.snapshots/` 三个目录在包内也不再
> 随 `.gitkeep` 分发——`wiki_gate.py` / `wiki_propose.py` / `_wiki_common.py`
> 都会 `os.makedirs(..., exist_ok=True)` 自行创建，且 zip 本就不含空目录。

## 冷启动说明

`patterns/` 下的首批条目（p01–p06、s01–s02，共 8 条）来自 `review-1.5.41`
的**人工 review**，不是从 trace 蒸馏来的，因此条数超过了"每轮 ≤5 失败 + ≤3
成功"的迭代上限。**上限约束的是每轮蒸馏的增量，不是冷启动种子**——这一点在
`logs.md` 里也有记录，避免后人误判为契约违规。

之后的 p07–p12 是闭环跑起来后**真撞出来的**（不是设计阶段推演的）：

| 条目 | 来源 | 触发方式 |
|---|---|---|
| `p07` 门控汇报假数字 | 1.5.43 | 跑门控时发现摘要退化成 `selftest 1/1` |
| `p08` 写了但没人执行的默认值 | 1.5.43 | 调试时看见 traces 目录堆了 90 个文件 |
| `p09` 检查方法本身错了 | 1.5.43 | 我拿有盲区的 grep 下结论，改坏了对的文档 |
| `p10` 上限粒度错 | 1.5.44 | 逐条核对"自称的约束有没有被执行" |
| `p11` 门控全绿 ≠ 覆盖到 | 1.5.44 | 扫"哪些模块从来没被测过" |
| `p12` 复制两份逻辑 → 两种症状 | 1.5.44 | 给零覆盖模块补测试时**当场撞出崩溃** |

注意后三条的共同点：**都不是写代码时想到的，都是靠"检查覆盖率/核对约束"这类
元动作挖出来的**。这正好说明为什么 wiki 里值得留一条 `p11`——门控不会替你
回答"这块有没有被测到"。
