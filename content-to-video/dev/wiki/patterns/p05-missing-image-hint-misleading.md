---
id: p05-missing-image-hint-misleading
type: failure
title: 缺图拦截对"从未搜过图的段落"也给出 --pick 提示（不可能执行的下一步）
first_seen: 2026-08-31
source: review-1.5.41
confidence: high
status: active
tags: [images, ux, error-handling]
---

## 现象

`scripts/run.py` 缺图拦截（exit 2）对所有缺图段落统一提示：

> 用 `--pick` 审阅候选后重跑

但对**从未搜过图**的段落（没有 `candidates.json` 条目），`--pick` 无从下手
——提示指向了一个不存在的操作。用户只会看到一个跑不通的建议，然后卡住。

## 机制

两类缺图被塞进了同一个分支：

| 类别 | 成因 | 正确的下一步 |
|---|---|---|
| 有候选、待人工审阅定稿 | 搜图跑过，但没 `--pick` | `--pick`（且提示里带真实 sid） |
| 从未搜过图 / 无候选 | 无 Key、该段被跳过、搜索失败 | 走方式 B/C/D 手动补图 |

统一提示 = 对其中一类必然错误。

## 证据

`--pick` 的帮助文本要求 candidates 已存在；对 `candidates.json` 里没有的
sid 传 `--pick`，行为未定义且不会产出图片。

## 处置（已落实）

1. 新增 `_split_missing_by_candidates(missing, cand_json_path, candidates_dir)`
   → `(pending_pick, no_candidate, review_lines)`
2. exit 2 分支改为分两段提示：
   - `pending_pick`：给出**用真实 sid 拼好**的 `--pick` 示例命令
   - `no_candidate`：给方式 B（ImageGen）/ C（matplotlib 图表）/ D（视频）
     的补图指引，并说明"均不依赖 STEPFUN_API_KEY"

## 泛化

> 错误提示的验收标准不是"说了点什么"，而是"用户照做能不能走通"。
> 任何给出下一步建议的分支，都要先分清**用户此刻处在哪种状态**。
