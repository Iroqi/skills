---
id: p07-gate-report-misleading
type: failure
title: 门控汇报了假数字——看起来在检查，其实没查到点上
first_seen: 2026-08-31
source: 1.5.42 自进化闭环改造（live 撞见，非事后归纳）
confidence: high
status: active
---

## 现象

`wiki_gate.py` 跑完打印 `selftest 1/1 · run_eval 1/1 · check 16/16 → 绿`。
数字是绿的，但 `selftest 1/1` **不是**"跑了 1 项断言并通过"——它是
"1 个子进程条目通过了"。真实断言数是 443/443。

读数的人会得出两个错误结论：

1. 这套门控弱得可怜（才 1 项检查）
2. 既然只有 1 项，那"全绿"没什么含金量

两个结论都错，而且**都指向"可以不用太当回事"**——这比门控失灵更危险。

## 机制

`dev/check.py` 自己是**超集门控**：它内部以子进程方式跑 selftest 与 run_eval，
再把结果并进自己的 `__SUMMARY_JSON__`。问题出在这一层：

```python
results.append({"name": "selftest", "ok": passed, "exit_code": r.returncode})
```

只记了"这个子进程过了"（`ok`），**没带上子进程自报的 `passed/total`**。
下游 `_wiki_common.run_gate()` 按"数条目"统计分组，于是 1 个条目 = 1/1。

子进程其实一直老老实实在最后一行打着自己的 `__SUMMARY_JSON__`
（内含 `"passed": 443, "total": 443`），信息就在那里，只是没被透传上来。

**这类缺陷的共同形状**：检查在跑、结论也对，但**汇报层把信息量压缩掉了**。
`s01-gate-injection-validation` 说的是"新门控要双向验证能不能拦住"，这条是它的
补集——**还要验证它汇报的是不是人话**。一个永远绿但什么都查不到的检查是坏的，
一个查到了但汇报得让人误读的检查，同样坏。

## 处置

1. `dev/check.py` 用 `_wiki_common.parse_summary_totals()` 解析子脚本输出的
   `__SUMMARY_JSON__`，把 `passed/total` 塞进自己的 results 条目。
2. `_wiki_common._count_group()` 对 selftest/run_eval 优先用子脚本明细，
   取不到才退回"数条目"粗口径——**宁可显示得粗略，也不许编数字**
   （解析不到返回 `(None, None)`，绝不猜）。
3. 解析实现只留一份（`_wiki_common`），`check.py` import 它。两处各写一遍
   必然漂移，见 `p04-doc-value-drift`。
4. selftest 第 25 节钉了 9 条断言：明细能透传、缺字段返回 `(None, None)`、
   红了也要如实显示、`check.py` 确实装配了明细。断言数 443 → 452。

## 判据（怎么知道这条修好了）

`python dev/check.py` 头部两行必须是带明细的形态：

```text
[OK] selftest (exit 0 · 452/452)
[OK] run_eval (exit 0 · 28/28)
```

门控摘要必须是 `selftest 452/452 · run_eval 28/28 · check 16/16`
（三组相加 = `check.py` 合计 18 项）。若再看到 `1/1`，说明透传链断了。

## 横向用法

任何"父进程汇总子进程结果"的地方都适用这条：**汇总时把子进程的真实规模
一起带上来**，别只带布尔值。同理适用于「N 个文件检查通过」这类汇报——
说清是几个文件、多少项，别只说"通过"。
