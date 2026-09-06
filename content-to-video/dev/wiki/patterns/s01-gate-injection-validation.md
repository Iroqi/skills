---
id: s01-gate-injection-validation
type: success
title: 新门控必须双向验证——注入原 bug 看它拦不拦得住，再构造合法反例看会不会误报
first_seen: 2026-08-31
source: review-1.5.41
confidence: high
status: active
tags: [gate, methodology]
---

## 做法

给 `dev/check.py` 加任何一条新检查后，做两步验证，缺一不可：

1. **反向（能拦住）**：把该检查本该拦住的错误**注回**源码/文档，跑 gate，
   确认它判红，且报错信息里能定位到具体行
2. **正向（不误伤）**：构造几个**合法**的同类写法，确认 gate 仍然全绿

## 实例

`_check_python_version_claim()` 与 `_check_workers_default()` 两条新检查：

- 反向：把 `SKILL.md` 的版本声明注回「Python 3.12+」→ 被拦，报出实测下限
  是 3.8；把 `rendering.md` 注回 `--workers 4` → 被拦，报出行号
- 正向：`search_images.py` / `pipeline.py` 文档里合法的 `--workers 4`
  （下载线程/TTS 并行）→ 未误报

## 为什么值得固化

第一版 `workers_default` 检查**是绿的**——在 bug 还躺在文档里的时候绿的。
如果只跑一遍"现在全不通过"就收工，这条检查就是纯装饰：它永远绿，直到
有人真的去读它到底在查什么。

## 泛化

> 一条从没红过的检查，等于没有检查。新门控的交付标准不是"接进去了"，
> 而是"我亲眼见过它变红，也亲眼见过它放过合法用法"。
