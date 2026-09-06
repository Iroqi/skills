---
id: p01-python-version-fiction
type: failure
title: 文档凭印象抬高 Python 版本门槛（声称 3.12+，实测 3.8 即可）
first_seen: 2026-08-31
source: review-1.5.41
confidence: high
status: active
tags: [env, docs, gate]
---

## 现象

`SKILL.md` 的"依赖安装"写着：

> Python 3.12+（含 pip）——`gen_hyperframes.py` 使用了 PEP 701 的 f-string
> 嵌套引号语法，3.11 及以下会 SyntaxError

按这条声明，Python 3.11 的用户会在**安装阶段就被劝退**，而实际上完全能跑。

## 机制

PEP 701（f-string 内可复用同种引号）确实是 3.12 才有的，但**是否存在该用法
不能靠读代码时的一句印象下结论**。用 `ast.parse(src, feature_version=(3,8))`
逐级探测实测：`scripts/` 下全部 21 个脚本在 **3.8** 特性级别即可解析；Python
3.11.1 实跑 `selftest.py` 398/398 全绿。

真正的成因是**文档与代码两份真值、且没人机械比对**——写文档时记住了"某处
f-string 很花"，于是把整条门槛抬到了 3.12。

## 证据

- `ast.parse(feature_version=(3,8))` 对 `scripts/*.py` 全通过
- Python 3.11.1 实跑 `scripts/selftest.py`：398/398 通过
- 本机环境指纹：`py=3.11.1`，`node=v22.13.1`，`ffmpeg=N-124111`

## 处置（已落实）

1. `SKILL.md` 改为「Python 3.9+；全部脚本只用 3.8 兼容语法，实测 3.11 全绿；
   不要凭印象抬高版本门槛」
2. `dev/check.py` 新增 `_check_python_version_claim()`：用 `ast.parse` 逐级
   探测真实下限，与文档声明比对，偏差超过 1 个 minor 即判红

## 复现条件

文档里出现"版本 X+（因为某个具体语法特性）"这类**带着理由的版本声明**时，
该理由必须可机械验证；否则就是本模式的温床。

## 泛化

> 凡"文档声称的环境要求 + 给出技术理由"，都要有一条对应的机械检查。
> 给不出机械检查的理由，就别写进文档——写"以 `--check-env` 输出为准"。
