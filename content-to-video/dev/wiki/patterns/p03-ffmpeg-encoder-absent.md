---
id: p03-ffmpeg-encoder-absent
type: failure
title: 硬编码 libx264 + 吞掉 stderr，把环境限制误报成代码回归
first_seen: 2026-08-31
source: review-1.5.41
confidence: high
status: active
tags: [env, ffmpeg, gate, error-handling]
---

## 现象

`dev/check.py` 一键 gate 报 13/14：`[FAIL] 测试视频合成成功`。看上去是刚改的
代码把渲染链路搞坏了，实际是本机 ffmpeg（`N-124111`）根本没编译 `libx264`。

## 机制

两个缺陷叠加，把**环境缺陷**伪装成了**代码缺陷**：

1. `dev/run_eval.py::build_fake_render` 硬编码 `-c:v libx264`
2. 失败时 `subprocess.run(..., capture_output=True)` 的 stderr **被完全吞掉**，
   只返回一个 False

于是"这台机器缺编码器"和"我刚写错了参数"在报告里长得一模一样。这类误报的
代价不只是浪费时间——它会让维护者去回滚一段其实正确的改动。

## 证据

- `ffmpeg -encoders` 中无 `libx264`，有 `libopenh264`
- 放开 stderr 后末 5 行显示 `Unknown encoder 'libx264'`

## 处置（已落实）

1. `pick_h264_encoder()`：按 `libx264 → libopenh264 → h264_nvenc → h264_qsv
   → h264_vulkan → h264_vaapi → h264_amf → h264_v4l2m2m` 顺序探测第一个可用
2. 失败时打印 stderr 末 5 行
3. 引入 **SKIP 语义**：环境完全没有 h264 编码器时返回 `None`，该项记为
   skip 而非 fail。修完 gate 结果 28/28（回退到 `libopenh264`）

## 复现条件

任何 `subprocess.run(capture_output=True)` + 只判 returncode 的调用点，都是
本模式的候选。

## 泛化

> **环境限制 ≠ 代码回归。** 门控必须能区分二者，否则维护者会被假警报训练成
> 忽略警报。凡是"依赖本机装了什么"的检查，都要有 skip 这一档。
>
> 且：`capture_output=True` 而不在失败时回显 stderr，等于主动销毁证据。
