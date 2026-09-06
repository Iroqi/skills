---
id: p04-doc-value-drift
type: failure
title: 文档里的默认值/示例值与代码实际默认值漂移
first_seen: 2026-08-31
source: review-1.5.41
confidence: high
status: active
tags: [docs, gate, drift]
---

## 现象

`references/rendering.md` 的渲染示例写 `--workers 4`，而 `scripts/run.py` 的
`--workers` 默认值已经是 6（2026-08 实测 6 比 4 快约 10%）。照文档抄命令的
用户会拿不到最优默认，且不会有任何报错提示。

## 机制

默认值存在于两处：代码里的 `parser.add_argument(default=...)` 和文档里的
示例/说明。二者没有机械关联，改了一个忘了另一个是必然事件，不是意外。

同类漂移还有：文档声称的条数（"18 条踩坑记录"）vs 实际条数、文档声称的
主题数 vs `theme_registry.json` 实际条目数。

## 证据

- `run.py` `--workers` default=6；`rendering.md` 示例为 4
- 已存在的 `_declared_count_check` 类检查覆盖"计数型"声明；"默认值型"声明
  此前**无人把关**

## 处置（已落实）

1. `references/rendering.md` 示例改为 `--workers 6`
2. `dev/check.py` 新增 `_check_workers_default()`：比对文档声明的 workers
   值与 `run.py` 实际 default
3. 同类思路已固化为 `doc_drift` 检查族（含 Python 版本声明、目录树、TOC
   锚点）

## 踩过的坑（写门控时）

第一版 `workers_default` 检查**查不出**注入的 `--workers 4`——因为那个值在
一段裸命令行里，而检查只认"默认 N"句式。改成裸 `--workers N` 也匹配后，
又误伤了另外两个工具（`search_images`、`pipeline`）里合法的 `--workers 4`。
最终加了作用域收敛：命中行若含"搜图/下载线程/TTS/配音/并行 TTS"等关键词，
或属调优建议语境（"降回/降到/低配/崩溃/堆上限"），则跳过。

## 泛化

> 门控的三个常见失败姿势：**查不出注入的 bug**、**误伤合法用法**、
> **只在快乐路径上绿**。写完门控后必须双向验证——注入原 bug 看它拦不拦得住，
> 再构造合法反例看它会不会误报。
