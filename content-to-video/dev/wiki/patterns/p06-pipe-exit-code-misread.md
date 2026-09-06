---
id: p06-pipe-exit-code-misread
type: anti
title: 管道后取 $? 拿到的是最后一个命令的退出码，不是被测命令的
first_seen: 2026-08-31
source: review-1.5.41
confidence: high
status: active
tags: [maintenance, verification, shell]
---

## 现象

验证 `--check-env` 的退出码时执行了：

```bash
python scripts/pipeline.py --check-env 2>&1 | tail -30; echo $?
```

拿到 `0`，据此一度判断"环境自检通过"。实际上 `--check-env` 的退出码是
**1**（环境缺项），`0` 是 `tail` 的。

## 机制

`a | b` 之后 `$?` 是 **b** 的退出码。这是 shell 常识，但在"我只是想少看
几行输出"的随手操作中极易被忽略——因为大部分时候被测命令成功，两者都是 0，
错误长期不暴露，直到某次恰好相反。

## 证据

改为重定向到文件后复测，真实退出码为 1：

```bash
python scripts/pipeline.py --check-env > /tmp/env.log 2>&1; echo $?
# → 1
```

## 处置（本条是反模式，无代码改动）

写在 wiki 里作为维护期的自检提醒。需要"既要过滤输出、又要拿到真实退出码"
时用：

```bash
set -o pipefail        # bash/zsh 均可；管道中任一环节非零则整体非零
# 或
out=$(cmd 2>&1); rc=$?; printf '%s' "$out" | tail -30
```

## 泛化

> 验证类操作有两个常见自欺姿势：**取错退出码**、**只看断言数量不看是否
> 有 skip**。前者会让你以为通过了，后者会让你以为全覆盖了。
> 每次下"验证通过"的结论前，问一句：我拿到的这个数字，真的是被测对象的吗？
