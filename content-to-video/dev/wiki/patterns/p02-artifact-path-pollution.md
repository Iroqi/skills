---
id: p02-artifact-path-pollution
type: failure
title: 照文档示例跑会把产物建进技能目录（违反同一份文档里的禁令）
first_seen: 2026-08-31
source: review-1.5.41
confidence: high
status: active
tags: [paths, docs, guard]
---

## 现象

`SKILL.md` 第 122 行明确写着"所有生成产物必须写入用户的项目目录（技能目录
之外）"。但文档自己的示例命令是：

```bash
python scripts/run.py --source segments_source.json -o audio_output
```

从技能目录执行时（文档正文中就是这么要求的："从其他目录调用时先 `cd` 到
技能目录"），相对路径 `audio_output` 会解析到**技能目录内**——照文档做，
正好踩中同一份文档的禁令。

## 机制

三个因素叠加：

1. 示例用**相对路径**，而示例的前提动作是 `cd` 进技能目录
2. 约定写在散文里，**没有机械拦截**
3. 违反后没有任何症状，只是慢慢污染仓库、跨次制作串台

这类"文档自相矛盾"的 bug 靠人工 review 极难发现——两段话在文件里相隔
几百行，读的时候不会互相激活。

## 证据

按示例执行后，技能目录内出现 `audio_output/`、`hf-project/`、`out/`，
与 `PROJECT` 不变量第 1 条直接冲突。

## 处置（已落实）

1. `scripts/run.py` 新增 `_is_inside()` + `_guard_not_in_skill_dir()`：
   解析出 `-o/--output`、`--project` 后立刻比对 `realpath`，命中技能目录
   就 `SystemExit` 并给出"cd 到项目目录 + 用脚本绝对路径调用"的具体命令
2. `--dry-run` 承诺不写文件，故豁免该守卫
3. `scripts/selftest.py` 第 24 节加了 10 条断言覆盖守卫存在性、dry-run
   豁免、`_is_inside` 三种边界

## 泛化

> 散文写的约定 = 没有约定。凡是能被一句话描述的禁令，就值得做成一条
> 提前拦截 + 一组断言。文档要做的不是重复禁令，而是**解释拦截为什么存在**。
