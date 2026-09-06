#!/usr/bin/env python3
"""Content-to-Video — 结构化输入（segments_source.json）→ 句子列表 + 段落分组。

pipeline.py --source 直接调用本模块的 build_parts()：逐段独立分句，跨段
短句合并结构上不可能发生，不需要任何跨段校验。

结构化输入格式（segments_source.json）：
{
  "opening_title": "欧拉恒等式",              // 可选，开场大标题；不传则回退到顶层 title 或 "本期内容"
  "opening_tagline": "",                     // 可选，开场标题下方的小标题；不传则不显示小标题
  "closing_title": "内容回顾",                 // 可选，结尾大标题；不传默认 "小结"
  "closing_tagline": "",                     // 可选，结尾标题下方的小标题；不传则不显示小标题
  "opening_agenda": true,                    // 可选，开场是否自动生成 "内容目录"（默认 true）
  "closing_recap": true,                     // 可选，结尾是否自动生成 "回顾" 标签条（默认 true）
  "opening": "大家好，欢迎收看今天的AI日报。",
  "closing": "感谢收看，明天见。",
  "segments": [
    {
      "title": "OpenAI 发布新一代模型",
      "tagline": "OpenAI",                    // 公司/机构名（自动化流程必填；缺省由 pipeline 兜底为 "补充阅读"）
      "body": "",                             // 可选，画面上标题下方的正文摘要；
                                               // 不传或传 "" 时自动取 text 的前
                                               // 2 句拼成摘要（见 _collect_blocks 内说明）
      "accent": "#64b5f6",                    // 可选，缺省按顺序取调色板
      "text": "OpenAI 发布了新一代模型，...",    // 正文，会被分句
      "speed": 1.0                            // 可选，缺省用 --speed
    }
  ]
}

双人对话段落（可选，用 "dialogue" 代替 "text"）：
{
  "speakers": {                               // 顶层声明，dialogue 段落必需
    "host": {"voice_id": "茉莉", "label": "主播"},
    "guide": {"voice_id": "苏打", "label": "讲解", "voice_style": "耐心讲解，逻辑清晰"}
  },
  "segments": [
    {
      "title": "反向传播算法",
      "tagline": "深度学习基础",
      "dialogue": [
        {"speaker": "host", "text": "反向传播听起来很复杂，能简单说说吗？"},
        {"speaker": "guide", "text": "简单说，就是把预测误差从输出层往回传，一层层告诉每个参数该怎么调整。"}
      ]
    }
  ]
}
一个段落里的 dialogue 轮次会按顺序拼成该段的完整句子列表（用于字幕/时间轴），
每轮的说话人各自使用 speakers 里声明的 voice_id/voice_style 合成语音，互不影响。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import split_sentences  # noqa: E402  复用同一份分句逻辑，杜绝两边漂移
from _theme import get_accent_palette, get_default_accent  # noqa: E402
from _contracts import OPENING_CLOSING_DEFAULT_SPEED  # noqa: E402  开场/结尾默认语速单一来源

ACCENT_PALETTE = get_accent_palette()
DEFAULT_ACCENT = get_default_accent()

# 超长句提醒阈值：字幕二次切行（split_subtitle_lines）是显示层兜底，
# 念稿节奏的根治方式还是写稿时控制在一句一口气能念完的长度。
LONG_SENTENCE_CHARS = 45
# 自动 body（无显式 body 时的兜底）的视觉行预算：横屏模板正文区每行约
# 20 字（maxWidth 880px / 字号 44px），配图段正文卡锚定标题组最坏情况
# （2 行标题 + tagline 槽，y 368）之下到字幕栏（y 904）约 7 行空间。
AUTO_BODY_CHARS_PER_LINE = 20
AUTO_BODY_MAX_LINES = 7


def _sentences_of(text):
    return split_sentences(text.strip())


def _collect_dialogue_sentences(dialogue, speakers, seg_index, seg_title):
    """把一个段落的 'dialogue'（多轮对话）拆成扁平句子列表 + 局部 turns。

    每一轮独立调用 split_sentences（同样杜绝"短句被并入下一轮"这类跨轮错位），
    turns 记录每一轮在本段内的局部句子区间 + 说话人信息（voice_id/voice_style
    在这里就近解析好，pipeline.py 不需要再反查 speakers 字典）。
    """
    sents = []
    turns = []  # {start, end, speaker, label, voice_id, voice_style} 局部区间（段内 0-based）
    for j, turn in enumerate(dialogue, 1):
        spk = turn.get("speaker")
        t_text = (turn.get("text") or "").strip()
        if not t_text:
            raise ValueError(f"第 {seg_index} 段（title={seg_title!r}）"
                              f"dialogue[{j}]（speaker={spk!r}）的 'text' 为空")
        t_sents = _sentences_of(t_text)
        if not t_sents:
            raise ValueError(f"第 {seg_index} 段（title={seg_title!r}）"
                              f"dialogue[{j}]（speaker={spk!r}）分句后为空，"
                              "请检查文本是否以终止标点（。！？）结尾")
        spk_cfg = speakers.get(spk, {}) if speakers else {}
        start = len(sents)
        sents.extend(t_sents)
        turns.append({
            "start": start, "end": len(sents), "speaker": spk,
            "label": spk_cfg.get("label") or spk,
            "voice_id": spk_cfg.get("voice_id"),
            "voice_style": spk_cfg.get("voice_style"),
        })
    return sents, turns


def _collect_blocks(source, default_speed=None):
    """把结构化 source 组装成内部 blocks。

    blocks 每项为 (id, title, tagline, accent, sentences, extra_fields, body, turns)。
    turns 为空列表表示普通独白段落；非空表示这段是"双人/多人对话"，每个 turn
    记录该轮在本段句子列表里的局部区间 + 说话人（voice_id/voice_style 已解析）。
    """
    blocks = []  # (id, title, tagline, accent, sentences, extra_fields, body, turns)
    speakers = source.get("speakers") or {}

    # 开场/结尾的大标题：可配置，避免所有视频都顶着同样的默认标题。
    # 默认 "本期内容"/"小结" 是中性兜底，agent 写稿时应按视频主题显式设置
    # opening_title（例如数学视频设成 "欧拉恒等式"）。
    opening_title = (source.get("opening_title")
                     or source.get("title")
                     or "本期内容")
    closing_title = source.get("closing_title") or "小结"
    opening_tagline = (source.get("opening_tagline") or "").strip()
    closing_tagline = (source.get("closing_tagline") or "").strip()
    # opening_agenda 对 flow 也默认 true：flow 的开场预告由 gen 层渲染成
    # 轻量 chips 一排（不是竖排编号目录——编号圆是章节模式语言；"剧透"
    # 顾虑也轻：chips 只露各段标题关键词）。
    opening_agenda = source.get("opening_agenda", True)
    # 结尾回顾标签条默认开（板块化内容的"要点复述"语言）；flow 自然叙事
    # 模式保持默认关——chips 是清单语言，叙事型内容的收尾靠
    # closing 稿件本身。
    closing_recap = source.get("closing_recap",
                               not bool(source.get("flow")))

    # opening_body / closing_body：开场/结尾标题下方的简介/寄语卡片
    # （与朗读稿独立，是提炼不是重复）。给了 body 就优先显示 body，
    # 不再自动生成开场目录/结尾回顾——flow 模式关掉目录/chips 后，
    # 这就是开场结尾页的内容来源（光靠大标题会太单调）。
    opening_body = (source.get("opening_body") or "").strip()
    opening_text = source.get("opening", "").strip()
    if opening_text:
        sents = _sentences_of(opening_text)
        blocks.append(("opening", opening_title, opening_tagline, DEFAULT_ACCENT, sents,
                        {"speed": source.get("opening_speed", OPENING_CLOSING_DEFAULT_SPEED),
                         "agenda": opening_agenda}, opening_body, []))

    raw_segments = source.get("segments", [])
    if not raw_segments:
        raise ValueError("source 中 'segments' 为空，至少需要一条内容段落")

    for i, seg in enumerate(raw_segments, 1):
        title = seg.get("title", "")
        dialogue = seg.get("dialogue")
        turns_local = []
        if dialogue:
            sents, turns_local = _collect_dialogue_sentences(dialogue, speakers, i, title)
        else:
            text = (seg.get("text") or "").strip()
            if not text:
                raise ValueError(f"第 {i} 段（title={title!r}）的 'text' 字段为空")
            sents = _sentences_of(text)
            if not sents:
                raise ValueError(f"第 {i} 段（title={title!r}）分句后为空，"
                                  f"请检查文本是否以终止标点（。！？）结尾")
        accent = seg.get("accent") or ACCENT_PALETTE[(i - 1) % len(ACCENT_PALETTE)]
        extra = {}
        if seg.get("speed") is not None:
            extra["speed"] = seg["speed"]
        elif default_speed is not None:
            extra["speed"] = default_speed
        # 段落级音色覆盖（SKILL.md 文档过的可选字段）：_contracts 里校验之后
        # 由这里透传给下游，pipeline.py 读 seg_config 后即可生效。
        if seg.get("voice_id") is not None:
            extra["voice_id"] = seg["voice_id"]
        if seg.get("voice_style") is not None:
            extra["voice_style"] = seg["voice_style"]
        # ── 正文（body）──────────────────────────────────────────────
        # 画面上标题下方常驻的正文，跟底部字幕（逐句显示 sents）是两套独立
        # 的东西：字幕是"这一刻正在念的那句话"，body 是"这一段的完整讲义"
        # （每句一行、逐行 stagger 入场）——画面承载全段内容、字幕跟踪朗读
        # 进度，形态互补而不是重复。
        # 优先级：显式 seg["body"] > 自动逐行放整段句子（对话段落也一样，
        # 直接取扁平化后的全部句子，不区分是谁说的）。
        # 自动 body 有视觉行预算：横屏模板 body maxWidth 880px / 字号 44px
        # ≈ 每行 20 字，标题区之下到字幕栏之上只有约 8 行空间——句子太长/
        # 太多时全放会溢出压到字幕栏（这种与字幕栏的重叠靠视觉审帧发现，
        # 自动几何检查查不到）。按"预算内能放几句放几句"截断，超出的句子反正字幕里还会
        # 念到，信息不丢；要放全段细节请手写显式 body（推荐做法）。
        explicit_body = (seg.get("body") or "").strip()
        if explicit_body:
            body = explicit_body
        else:
            budget = AUTO_BODY_MAX_LINES
            kept = []
            truncated = False
            for s in sents:
                need = max(1, -(-len(s) // AUTO_BODY_CHARS_PER_LINE))  # ceil
                if need > budget:
                    # 剩下的预算装不下这一整句。以前 `if kept and need >
                    # budget: break` 让首句豁免预算——一句 300 字会把
                    # budget 打成负数、挤掉后面所有句子，还溢出压到字幕栏
                    # （这类重叠只能靠肉眼审帧发现，几何检查查不出）。
                    # 现在首句超预算时按可用行数截断（+ 省略号）：宁可显示
                    # 半句也不能让 body 全空只剩标题。后续句子则直接停——
                    # 已经有两三行内容了，再挂半句反而显得没说完。
                    if not kept and budget > 0:
                        _take = budget * AUTO_BODY_CHARS_PER_LINE
                        kept.append(s[:_take].rstrip() + "……")
                        truncated = True
                    break
                kept.append(s)
                budget -= need
            body = "\n".join(kept)
            if truncated:
                # 不提示的话，"画面正文被砍了一半"是无头悬案。
                print(f"[warn] seg{i} 的首句长度超出画面正文行数预算"
                      f"（{AUTO_BODY_MAX_LINES} 行 × "
                      f"{AUTO_BODY_CHARS_PER_LINE} 字），已截断并加省略号；"
                      f"想完整展示请把长句拆短或手写该段 body。",
                      file=sys.stderr)
        # 段落 id 用中性前缀 seg（skill 已不限于新闻制作）；下游
        # startswith(("news","seg")) 兼容旧 news 前缀的 manifest。
        blocks.append((f"seg{i}", title, seg.get("tagline", ""),
                        accent, sents, extra, body, turns_local))

    closing_body = (source.get("closing_body") or "").strip()
    closing_text = source.get("closing", "").strip()
    if closing_text:
        sents = _sentences_of(closing_text)
        blocks.append(("closing", closing_title, closing_tagline, DEFAULT_ACCENT, sents,
                        {"speed": source.get("closing_speed", OPENING_CLOSING_DEFAULT_SPEED),
                         "recap": closing_recap}, closing_body, []))
    return blocks


def _segments_from_blocks(blocks):
    """按顺序累加 start/end 生成 segments。"""
    segments = []
    cursor = 0
    for seg_id, title, tagline, accent, sents, extra, body, turns_local in blocks:
        start, end = cursor, cursor + len(sents)
        cursor = end
        seg_obj = {
            "id": seg_id, "title": title, "tagline": tagline, "body": body,
            "accent": accent, "start": start, "end": end,
        }
        seg_obj.update(extra)
        if turns_local:
            # 局部区间 -> 全局区间（加上本段的 start 偏移），供 pipeline.py
            # 按句覆盖 voice_id/voice_style + 记录说话人标签。
            seg_obj["turns"] = [
                {
                    "start": start + t["start"], "end": start + t["end"],
                    "speaker": t["speaker"], "label": t["label"],
                    **({"voice_id": t["voice_id"]} if t["voice_id"] else {}),
                    **({"voice_style": t["voice_style"]} if t["voice_style"] else {}),
                }
                for t in turns_local
            ]
        segments.append(seg_obj)
    return segments, cursor


def build_parts(source, default_speed=None):
    """把结构化 source 转成 (sentences, segments)，供 pipeline --source 使用。

    对每个段落**独立**分句（不拼接成整篇再重分句）——结构化输入下每段是
    独立字符串，跨段短句合并结构上不可能发生，没有对应的失败模式。

    超长句（> LONG_SENTENCE_CHARS 字）只打 [warn] 不拦截：显示层会做次要
    标点切行兜底（gen_hyperframes 的 split_subtitle_lines），但 45+ 字的
    一句话念出来也偏喘不过气，根治方式是写稿时拆成两句——warn 就是提醒
    agent 这么做。

    Returns:
        sentences: list[str]，按段落顺序排列的全部句子
        segments: 段落分组（id/title/tagline/body/accent/start/end/
                  可选 speed/voice_id/voice_style/turns）
    """
    blocks = _collect_blocks(source, default_speed)
    sentences = []
    for _seg_id, _title, _tagline, _accent, sents, _extra, _body, _turns in blocks:
        sentences.extend(sents)
    segments, _ = _segments_from_blocks(blocks)
    for idx, s in enumerate(sentences, 1):
        if len(s) > LONG_SENTENCE_CHARS:
            print(f"[warn] 第 {idx} 句长达 {len(s)} 字（>{LONG_SENTENCE_CHARS}），"
                  f"念稿易喘不过气、字幕也需要切多行：{s[:24]}…"
                  "建议写稿时在逗号处拆成两句", file=sys.stderr)
    return sentences, segments
