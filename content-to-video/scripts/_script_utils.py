"""解说稿文本处理（从 pipeline.py 拆出）。

核心函数 split_sentences（中文断句）不依赖任何 TTS/ffmpeg/并发相关的重量级
库，纯粹是文本处理。拆成独立模块是因为 build_from_structured.py 只需要分句，
直接依赖这个轻量模块即可，不必连带 import pipeline 的重量级依赖。
"""
import re


def split_sentences(text):
    """Split Chinese text into sentences by terminal punctuation."""
    text = text.strip()
    if not text:
        return []
    # Include Chinese semicolon ；(U+FF1B, via \uFF1B to avoid source encoding
    # ambiguity) alongside ASCII ; (U+003B) as sentence terminators.
    parts = re.split(r'(?<=[。！？\uFF1B;\n])', text)
    sentences = []
    for p in parts:
        s = p.strip()
        # len>1 过滤游离标点；单字句（"大家好。好"的"好"）是有效内容，
        # 用 isalnum 放行（纯标点 isalnum()=False 仍被滤掉）
        if s and (len(s) > 1 or s.isalnum()):
            sentences.append(s)
    # Merge very short fragments (< 5 chars) with the next sentence
    merged = []
    buf = ""
    for s in sentences:
        if buf:
            buf += s
            if len(buf) >= 8:
                merged.append(buf)
                buf = ""
        elif len(s) < 5:
            buf = s
        else:
            merged.append(s)
    if buf:
        if merged:
            merged[-1] += buf
        else:
            merged.append(buf)
    return merged


# 字幕二次切行（显示层）：固定宽度均衡切行。
# 断点优先级：标点 / 空格 > CJK 字符间；连续 ASCII 字母数字（英文词）保持
# 完整不劈开。每行尽量填满到 max_chars，保证等宽、且不交给 CSS 二次折行。
# 注意这仅是**显示层**排版辅助——TTS 断句仍只认终止标点（split_sentences），
# 句间停顿 --gap 按"每两句之间"插入，标点处切行不会引入额外停顿。


def _hard_split_chars(text, cap):
    """超长无切点字符串的字符级兜底切分（优先在空格断英文词边界，保留空格）。

    当次要标点切分仍产生超过物理行宽的超长行时调用，保证每行 <= cap，
    避免整句交给 CSS 自然换行后溢出成 3+ 视觉行。行尾保留原串的空格，
    因此 "".join(结果) 与原串完全一致（渲染时每行是独立 div，空格仅用于
    join 校验与内容保真）。纯中文长串（无空格）直接按 cap 字符等切。
    """
    text = text.strip()
    if len(text) <= cap:
        return [text]
    lines, i, n = [], 0, len(text)
    while i < n:
        if i + cap >= n:
            lines.append(text[i:])
            break
        seg = text[i:i + cap]
        sp = seg.rfind(' ')
        if sp > 0:
            # 在空格处断行，空格留在上一行行尾（join 后与原串一致），每行 <= cap
            lines.append(text[i:i + sp + 1])
            i = i + sp + 1
        else:
            # 无空格（超长英文专名 / 纯中文串）→ 字符级硬断
            lines.append(text[i:i + cap])
            i = i + cap
    return lines


def _is_break_after(text, idx):
    """能否在 text[idx] 之后断行（把 text[idx] 纳入当前行、于 idx+1 处断开）。

    断行规则：空白、非 ASCII 字符（CJK/全角宽字符）、ASCII 标点/符号 均
    可在其后断；连续 ASCII 字母/数字（英文词）仅在该词**结尾**（后一字符
    非字母数字）时才可断，从而整词不被劈开。
    """
    ch = text[idx]
    if ch.isspace():
        return True
    if not ch.isascii():
        return True
    if ch.isalnum():
        nxt = text[idx + 1] if idx + 1 < len(text) else ""
        return not (nxt.isascii() and nxt.isalnum())  # 词尾才断
    return True  # 其余 ASCII 符号（标点等）可断


def _wrap_width(text, width, hard_cap):
    """固定宽度贪婪切行：每行尽量填满到 width，断点取窗口内最靠后的合法断点。

    保证 "".join(结果) == text（仅去掉首尾空白），不丢失任何字符（含空格），
    可直接用于需 join 校验 / 内容保真的场景。当 width<=hard_cap 时每行已
    <= width <= hard_cap，杜绝整句交给 CSS 自然换行后溢出成多视觉行。
    """
    text = text.strip()
    if not text:
        return []
    n = len(text)
    if n <= width:
        return [text]
    lines = []
    start = 0
    while start < n:
        end = min(start + width, n)
        if end >= n:
            lines.append(text[start:n])
            break
        # 从 end 往回找最远的合法断点（优先填满，实现等宽）
        brk = -1
        for j in range(end, start, -1):
            if _is_break_after(text, j - 1):
                brk = j
                break
        if brk <= start:
            # 窗口内无断点（超长英文专名占满 width）→ 在 width 处硬切
            brk = start + width
        lines.append(text[start:brk])
        start = brk
    if hard_cap and hard_cap < width:
        out = []
        for ln in lines:
            if len(ln) > hard_cap:
                out.extend(_hard_split_chars(ln, hard_cap))
            else:
                out.append(ln)
        lines = out
    return lines


def split_subtitle_lines(text, max_chars=28, max_lines=3, slack=1.5, hard_cap=40):
    """把一句长字幕切成均衡字幕行（仅显示用）。

    2026-08-18 用户要求：弱化标点、强化等宽、英文词不劈开。改为固定宽度
    贪婪切行——每行尽量填满到 max_chars，断点优先标点/空格，其次 CJK 字符
    间；连续 ASCII 字母数字（英文词）保持完整。每行 <= min(max_chars,hard_cap)，
    杜绝整句交给 CSS 自然换行后溢出成多视觉行。

    Args:
        text: 单句文本（split_sentences 的一个元素）
        max_chars: 单行目标宽度（字符）；行尽量填满到此值
        max_lines: 行数上限（仅 max_lines<=1 时禁用切分；其余仅作极少触发兜底）
        slack: 保留参数以兼容旧调用；新算法按固定宽度切分，slack 不再生效
        hard_cap: 物理行硬上限（字符），最终安全约束

    Returns:
        list[str]: 1..n 行
    """
    text = text.strip()
    if not text:
        return []
    if max_lines is not None and max_lines <= 1:
        return [text]
    if len(text) <= max_chars:
        return [text]
    width = min(max_chars, hard_cap) if hard_cap else max_chars
    lines = _wrap_width(text, width, hard_cap)
    return [l for l in lines if l]

def split_subtitle_cues(text, max_chars=28, cue_max_lines=2, slack=1.5, hard_cap=40):
    """把一句长字幕切成若干"cue 组"：每屏最多 cue_max_lines 行。

    观感约束：同屏字幕最多两行，再多显拥挤——超出
    部分顺延到下一条时间线（cue），时长由调用方按字符占比切分。内部先用
    较大的行数上限做标点均衡切行（保证超长句也能切到每行 <= max_chars*1.5），
    再按 cue_max_lines 顺序分组。

    Returns:
        list[list[str]]: 每个元素是一屏的行列表（1..cue_max_lines 行）
    """
    lines = split_subtitle_lines(text, max_chars=max_chars, max_lines=8,
                                 slack=slack, hard_cap=hard_cap)
    return [lines[i:i + cue_max_lines]
            for i in range(0, len(lines), cue_max_lines)]
