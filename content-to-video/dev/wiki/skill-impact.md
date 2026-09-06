# skill-impact — 每次 skill 改动 → 门控结果对照

WikiSkill 的核心设定：**skill 层会回滚，wiki 层不会**。本表就是"为什么回滚"
和"为什么保留"的永久记录——即使某次改动被回滚了，它留下的证据仍然有效。

门控分两档：

- **fast**（默认，秒级）：`scripts/selftest.py` + `dev/run_eval.py` + `dev/check.py`
- **full**（人工触发）：追加 `dev/evals.json` 的 10 条 forward-test（需另一个
  agent 实例 + 真实 API）

## 基线与口径

> **本技能没有准确率这类标量指标。** WikiSkill 论文里的 +18.6 分来自"有标准
> 答案、机器可判分"的基准任务，这里不具备同等条件。所以本表记录的不是
> "涨了多少分"，而是**每处改动有没有机械证据支撑、有没有被门控验证、
> 失败后有没有真的回滚**。别拿这张表去对标论文的提升曲线。

| 通过标准 | 口径 |
|---|---|
| selftest | 断言全绿，且**新增/修改处有对应断言** |
| run_eval | 全绿；环境缺项为 SKIP 而非 FAIL |
| check | 全绿（1.5.43 起 19 项：原 16 项 + `wiki:execution_purity` + `wiki:pattern_contract` + `wiki:cli_flags`） |
| forward-test | 10 条用例的 expect 全满足、reject 全未触发 |

**门控汇总口径**：`selftest` / `run_eval` 显示的是子脚本自报的断言数
（如 452/452），`check` 显示的是 `check.py` 自己的检查项数（17/17）；
三者相加 = `check.py` 的合计 19 项。

> **老行里的 `selftest 1/1` 是显示缺陷，不是只跑了 1 项检查。** 2026-08-31 之前
> 写入的行里，子脚本的真实明细数没有透传上来，摘要退化成了"1 个子进程项通过"。
> 已修（`dev/check.py` 透传 + `_wiki_common._count_group` 优先用明细），并加了
> 断言钉住。按"只增不改"原则，历史数字原样保留，在此统一说明。

## 记录

| 日期 | 版本 | 改动摘要 | 依据 pattern | 门控 | 结果 |
|---|---|---|---|---|---|
| 2026-08-31 | 1.5.41 | Python 门槛 3.12+ → 3.9+；加 `_check_python_version_claim` | `p01` | fast: selftest 408/408 · run_eval 28/28 · check 16/16 | ✅ 保留 |
| 2026-08-31 | 1.5.41 | 产物路径守卫 `_guard_not_in_skill_dir` | `p02` | fast: 同上 + 第 24 节 10 条新断言 | ✅ 保留 |
| 2026-08-31 | 1.5.41 | ffmpeg 编码器回退链 + stderr 回显 + SKIP 语义 | `p03` | fast: run_eval 由 13/14 → 28/28（回退 libopenh264） | ✅ 保留 |
| 2026-08-31 | 1.5.41 | `--workers` 示例 4 → 6；加 `_check_workers_default` | `p04` | fast: 双向验证（注入 `--workers 4` 被拦；其他工具合法的 4 不误报） | ✅ 保留 |
| 2026-08-31 | 1.5.41 | 缺图提示按"有候选/无候选"拆分 | `p05` | fast: 第 24 节 4 条拆分断言 | ✅ 保留 |
| 2026-08-31 | 1.5.42 | 引入 WikiSkill 三层自进化闭环（本次改造） | 论文 + 上述全部 | fast: selftest 443/443 · run_eval 28/28 · check 16/16 | ✅ 保留 |
| 2026-08-31 | 1.5.43 | 门控汇报口径修复（子脚本明细透传）+ 新增 `wiki:cli_flags` 门控（文档里的维护脚本参数必须真实存在）+ 沉淀 `p07` | `p07`,`s02` | fast: selftest 452/452 · run_eval 28/28 · check 17/17 ｜ 双向验证：注入 `--bogus-flag` 被拦、还原后不误报 | ✅ 保留 |
| 2026-09-02 | 1.5.44 | 打包前 review：①补 4 个新增脚本 CLI 的零覆盖（门控当时照样 19/19 全绿）②单条字符上限的粒度由"整篇"改回"每条"③`--since-last` 的时区崩溃（同一逻辑两份实现：一个崩、一个静默失效） | `p10`,`p11`,`p12` | fast: selftest 516/516 · run_eval 28/28 · check 17/17 ｜ 新增 39 条断言；p12 除"不崩"外另钉"真的过滤了" | ✅ 保留 |
| 2026-09-02 | 1.5.45 | 打包前清理：删演示残留（proposals + .snapshots，目录保留 .gitkeep）；补完最后 2 个零覆盖模块（`run_eval.py` / `_assets.py`），覆盖率扫描归零 | `p11` | fast: selftest 533/533 · run_eval 28/28 · check 17/17 ｜ 新增 17 条断言；另更正 p11 判据里『已知缺口』的说法 | ✅ 保留 |
<!-- AUTO-ROWS-BELOW -->
| 2026-08-31 | 1.5.42 | internals.md 开篇自称是排查诡异路径/缓存问题的首选入口，却没提跑批会在项目目录之外 | s02-doc-drift-as-mechanical-gate,p02-artifact-path-pollution | fast: selftest 452/452 · run_eval 28/28 · check 16/16 | ✅ 保留 |
| 2026-08-31 | 1.5.42 | （已回滚）**门控有效性哨兵**：故意把 --workers 6 注回 4，验证门控会拦住并回滚 | p04-doc-value-drift | selftest 1/1 · run_eval 1/1 · check 13/14 ｜ 红项：doc_drift:workers_default | ❌ 已回滚 |

> ⏳ 表示已写入但尚未跑门控。跑完把这一行改成 ✅ 保留 或 ❌ 已回滚，
> **不要删行**——回滚记录本身就是 wiki 的价值所在。
>
> `<!-- AUTO-ROWS-BELOW -->` 之下的行由 `dev/wiki_gate.py` 自动插入，
> 插在该标记正下方（新行在前）。手写的行请放在标记**之上**，避免被覆盖顺序
> 打乱——虽然脚本不会删任何已有行。

## 关于回滚的记录

**不另开一张表。** 回滚就记在上面那张表的"结果"列里，标 ❌ 已回滚；被拒的
提案进 `proposals/incubating/`，快照留在 `.snapshots/<提案 id>/`，流水追加到
`logs.md`。四处合起来就是完整的回滚档案，再开一张表只会多出一处需要同步、
又必然漂移的地方（见 `patterns/p04-doc-value-drift`）。

**wiki 永远保留**——这是 WikiSkill 与"试错 + 重来"的根本区别：skill 层的改动
可以被撤销，但"我们试过什么、为什么失败"这条知识不能跟着一起蒸发。
