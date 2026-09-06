---
id: p08-default-nobody-enforces
type: failure
title: 写在常量里的默认值，如果没人执行，就不是默认值
first_seen: 2026-08-31
source: 1.5.42 自进化闭环改造（live 撞见）
confidence: high
status: active
---

## 现象

`scripts/_trace.py` 顶部写着：

```python
DEFAULT_MAX_TRACES = 200
DEFAULT_MAX_AGE_DAYS = 180
```

看起来"轨迹最多留 200 条、最多留 180 天"。但 `prune()` **从来没被自动调用过**——
只有人手动跑 `wiki_trace.py prune` 才会执行。实测：一次短时间的调试就在
`~/.config/ai-video/traces/` 里堆了 90 个文件，且没有任何机制会停下来。

代码没报错、测试全绿、门控全绿。**一切指标都好看，约束却根本不存在。**

## 机制

两个叠加失误：

1. **约束与执行分离**：常量声明在 A 处，调用点在 B 处，中间没有任何东西保证
   B 一定发生。写的人脑子里"当然会清"，读的人以为"当然在清"。
2. **默认参数在定义时绑定**：即使补上自动剪枝，若写成
   `def prune(..., max_traces=DEFAULT_MAX_TRACES)` 再在调用处省略该参数，
   那么事后修改 `DEFAULT_MAX_TRACES`（含测试里 monkeypatch）**不会生效**——
   上限被焊死在 import 时刻。这是 Python 的老坑，在"配置常量"场景下尤其致命，
   因为它让常量看起来可配、实际不可配。

这类缺陷的共性：**声明存在 ≠ 约束生效**。文档说有上限、代码里有常量、
但实际行为无界——三者可以同时成立，且不会有任何一处报错。

## 处置

1. `write_record()` 落盘后调用 `_maybe_prune()`：条数超上限才真动手（一次
   `listdir`，平时零开销）。
2. `_maybe_prune()` 里**显式传参**而不是依赖默认参数：
   `prune(d, max_traces=DEFAULT_MAX_TRACES, ...)`。函数体内读模块常量才是
   调用时求值，常量才真的可改、可测。
3. 剪枝放在 `try` **之外**：剪枝失败不能把"本次已经落盘成功"的结果也吞掉。
   旁路设施的最高优先级是不破坏主流程。
4. selftest 钉 3 条断言：超上限会自动滚、保留的是最新的、剪枝抛异常也不
   影响落盘（用 monkeypatch 让 `prune` 抛错来测）。

## 判据

设小 `DEFAULT_MAX_TRACES` 后连续 `write_record()`，目录内文件数不得超过
该上限；且 `prune` 被替换成抛异常的函数时，`write_record()` 仍返回有效路径。

## 横向用法

写下任何"上限/阈值/保留期"常量时，立刻问一句：**谁在什么时候执行它？**
如果答案是"等人手动跑"，那它只是一句注释。同理适用于日志轮转、缓存清理、
临时目录回收、历史记录归档——凡是"应该会清"的地方，都要有确凿的调用点。
