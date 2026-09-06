---
id: p12-copied-logic-two-symptoms
type: failure
title: 同一段逻辑复制两份，同一个 bug 就呈现出两种症状
first_seen: 2026-09-02
source: 1.5.44 打包前 review（live，测试撞出来的）
confidence: high
status: active
---

## 现象

`--since-last`（"只看上次蒸馏之后的轨迹"）在两个 CLI 里各写了一遍，都是拿
标记的 mtime 跟轨迹的 `ts` 比：

```python
# wiki_trace.py
mt = datetime.datetime.fromtimestamp(os.path.getmtime(MARKER))
if ts and ts < mt: ...          # → 直接崩

# wiki_maintain.py
try:
    if ts and datetime.datetime.fromisoformat(ts) < cutoff: ...
except (ValueError, TypeError):
    pass                        # → 不崩，静默不过滤
```

`ts` 是 `astimezone().isoformat()` 生成的**带时区**时间，`fromtimestamp()`
给的是**朴素**时间，一比就 `TypeError: can't compare offset-naive and
offset-aware datetimes`。

于是同一个根因得到两种完全不同的症状：

| 脚本 | 症状 | 可发现性 |
|---|---|---|
| `wiki_trace.py` | 崩溃，栈直指问题 | 高——一跑就炸 |
| `wiki_maintain.py` | `--since-last` 静默退化成"不过滤" | **零**——输出看起来完全正常 |

两个都从没被测过，所以两个都活到了打包前。是补测试时撞出来的，不是设计检查
发现的。

## 机制

三层原因，任何一层单独存在都不致命：

1. **时区来源不统一**：`fromtimestamp()` 默认给朴素本地时间，而整条轨迹链路
   用的都是 aware 时间。混用是 Python 的经典陷阱，且**只在比较运算时才炸**
   ——单独 print、单独存储都正常，所以写的时候看不出问题。
2. **同一逻辑复制两份**：MARKER 路径、mtime 读取、ts 解析，两边各写一遍。
   复制的代价不是多几行，是**修一处漏一处**——就算当时发现并修好了崩的那个，
   静默的那个依然会继续错下去，而且再也不会有人去看它。
3. **`except (ValueError, TypeError): pass` 给缺陷发了隐身衣**：崩溃是信号，
   静默是伪装。宽泛捕获把"我写错了"翻译成了"这种情况不需要处理"。

**复制代码 × 宽泛 except = 同一个缺陷有两种命运，其中一种永远不会被发现。**

## 处置

1. 在 `_wiki_common.py` 里收成唯一实现：`marker_path()` / `marker_cutoff()` /
   `parse_trace_ts()`。两个 CLI 只调用，不自己算。
2. `parse_trace_ts()` 对**朴素**的 ts 按本地时区补齐，容忍老轨迹与手工导入
   的数据——契约是"返回的一定是 aware 或 None"。
3. `marker_cutoff()` 向下取整到秒。轨迹的 `ts` 精度只到秒，拿带亚秒的 mtime
   去比会把"跟标记同一秒落盘"的轨迹判成旧的。**不确定归属时保留而不是丢弃**：
   重复蒸馏一条已蒸馏过的轨迹是幂等的，漏掉一条没蒸馏过的则是永久丢失。
4. 断言不只测"不崩"，还测**真的过滤了**：打标记 → 写新轨迹 → 断言只留下新的
   且 id 对得上。只测不崩的话，静默不过滤的回归照样能全绿溜过去。

## 判据

```python
parse_trace_ts("2026-08-31T10:00:00+08:00").tzinfo is not None   # aware
parse_trace_ts("2026-08-31T10:00:00").tzinfo is not None         # 朴素也被补齐
marker_cutoff(path).microsecond == 0                             # 取到整秒
parse_trace_ts(ts) < marker_cutoff(path)   # 不再抛 TypeError
_collect(since_last=True) 里只剩标记之后写的那条
```

## 横向用法

**只要同一段逻辑出现了第二份，就把它抬到共享模块里——不是因为复用，是因为
复制会稀释 bug 的可发现性。** N 份实现意味着同一个缺陷有 N 种症状，其中至少
一种会是静默的。

配套两条硬规矩：

- **禁止 `except: pass` 与过宽的 `except (ValueError, TypeError)`。** 真要
  容错，就显式列出"哪种输入算正常跳过"，剩下的让它炸。静默吞掉的异常不会
  消失，只是转成了错误的行为。
- **时间比较前先对齐精度与 tzaware。** 精度不匹配（秒 vs 亚秒）的边界丢数据，
  比时区不匹配更难查，因为它只在"同一秒内发生"时才出现。

最后一条通用判据：**给"过滤/裁剪/清理"类功能写测试时，必须有一条断言验证
"该保留的留下了"**，而不只是"没报错"或"该丢的丢了"。
