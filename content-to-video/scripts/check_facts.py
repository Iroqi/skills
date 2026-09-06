#!/usr/bin/env python3
"""数字漂移检查：从 segments_source.json 机械抽出全部数字表述清单。

SKILL.md 第 2 步规则 6（事实可回溯）要求正文里每个数字、百分比、金额都能在
信源里找到出处，宁可不写数字也不能编造——编造数字被定义为比配错图更严重的
事故。本脚本产出一份不依赖记忆的核对清单，供当前对话模型/人工逐条对照信源。

边界：
  - 脚本只负责"抽全、不漏"，不做任何真实性判断——判断数字是否符合信源
    需要读信源原文，是语义工作，由当前对话模型完成
  - 抽取对象是所有会进入成片的文本：opening/closing 朗读稿与 body、各段
    title/tagline/text/body/dialogue 每一轮
  - 匹配规则：连续数字串（含千分位逗号与小数点，全角数字归一化后匹配），
    打印前后若干字上下文；不解析计量单位（15亿/30% 的含义在上下文里自明）

用法：
  python scripts/check_facts.py -s segments_source.json
  python scripts/check_facts.py -s segments_source.json --context 20

退出码：
  0 = 正常完成（检出多少条数字都算成功——这是核对清单，不是失败信号）
  2 = 输入问题（文件读不到 / JSON 非法 / 结构校验不过，与 pipeline 同口径）

extract_numbers / collect_fields 为纯函数，可独立测试。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _contracts import load_segments_source  # noqa: E402
from _script_utils import setup_stdio  # noqa: E402  重定向场景 stdout 强制 UTF-8

# 全角数字 → ASCII（稿件里偶发全角写法，统一归一化后扫描，避免漏检）
_FULLWIDTH_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")
_NUM_RE = re.compile(r"\d[\d,，]*(?:\.\d+)?")


def extract_numbers(text):
    """返回 [(num_str, start_idx), ...]。空文本安全。"""
    if not text or not isinstance(text, str):
        return []
    return [(m.group(), m.start()) for m in _NUM_RE.finditer(text.translate(_FULLWIDTH_TRANS))]


def _ctx(text, idx, width=12):
    """取匹配位置的上下文片段，换行压成空格，两端截断处加省略号。"""
    lo = max(0, idx - width)
    hi = min(len(text), idx + width)
    frag = text[lo:hi].replace("\n", " ").strip()
    return ("…" if lo > 0 else "") + frag + ("…" if hi < len(text) else "")


def collect_fields(data):
    """按固定顺序产出 (字段路径, 文本)。覆盖所有会进成片的文本，
    与 build_from_structured._collect_blocks 的消费范围对齐：
    opening/closing 朗读稿、opening_body/closing_body、每段
    title/tagline/text/body、dialogue 每轮 text。"""
    fields = []
    opening = data.get("opening")
    if isinstance(opening, str) and opening.strip():
        fields.append(("opening.text", opening))
    for key in ("opening_body", "closing_body"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            fields.append((key, v))
    for i, seg in enumerate(data.get("segments") or [], 1):
        sid = f"seg{i}"
        for key in ("title", "tagline", "text", "body"):
            v = seg.get(key)
            if isinstance(v, str) and v.strip():
                fields.append((f"{sid}.{key}", v))
        for j, turn in enumerate(seg.get("dialogue") or [], 1):
            v = turn.get("text") if isinstance(turn, dict) else None
            if isinstance(v, str) and v.strip():
                spk = turn.get("speaker", "?")
                fields.append((f"{sid}.dialogue[{j}]{spk}", v))
    closing = data.get("closing")
    if isinstance(closing, str) and closing.strip():
        fields.append(("closing.text", closing))
    return fields


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="抽出稿件中全部数字表述，供对照信源核对")
    ap.add_argument("-s", "--source", required=True, help="segments_source.json 路径")
    ap.add_argument("--context", type=int, default=12, help="每个数字显示的前后文宽度（默认 12 字）")
    args = ap.parse_args()

    try:
        data = load_segments_source(args.source)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2

    total = 0
    lines = []
    for path, text in collect_fields(data):
        hits = extract_numbers(text)
        if not hits:
            continue
        nums = []
        for num, idx in hits:
            nums.append(f"{num}（{_ctx(text, idx, args.context)}）")
        total += len(hits)
        lines.append(f"  {path} [{len(hits)} 处]: {'、'.join(nums)}")

    if total == 0:
        print("[ok] 未检出数字表述（无事实回溯负担）")
    else:
        print(f"[facts] 共检出 {total} 处数字表述——请逐条对照信源确认出处"
              f"（宁可不写数字也不能编造）：")
        for ln in lines:
            print(ln)
        print("[done] 清单完毕。脚本只抽取、不判真伪；对照信源由当前对话模型完成")

    print("__SUMMARY_JSON__ " + json.dumps(
        {"tool": "check_facts", "source": args.source, "total": total},
        ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
