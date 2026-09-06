"""解说稿文本处理（从 pipeline.py 拆出）。

核心函数 split_sentences（中文断句）不依赖任何 TTS/ffmpeg/并发相关的重量级
库，纯粹是文本处理。拆成独立模块是因为 build_from_structured.py 只需要分句，
直接依赖这个轻量模块即可，不必连带 import pipeline 的重量级依赖。
"""
import re
import sys


def setup_stdio():
    """stdout/stderr 强制 UTF-8 输出（errors=replace），入口脚本 main() 第一行调用。

    Windows 下 stdout 被重定向/进管道时，Python 按 locale 编码写流（中文系统
    cp936）——pipeline 把用户稿件原文打进 stdout，稿件含 emoji 或任何该编码
    表示不了的字符时，print 到一半裸栈 UnicodeEncodeError，而此时 TTS 已经烧
    掉一半额度。交互控制台因 PEP 528 本就是 UTF-8 不受影响；测试环境替换过
    的假流没有 reconfigure 方法时静默跳过。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


# 中文终止符：。！？＋中文分号＋ASCII 分号＋换行（沿用历史行为）。
# ASCII 的 .!? 由下方扫描逻辑带边界守卫地补充（见 split_sentences）。
_CN_TERMINATORS = "。！？\uFF1B;\n"

# 常见英文缩写词尾：句点即使后面跟着空白也不视为句子结束。全小写比对；
# 含内部点的形式（e.g / i.e / u.s）。维护原则：漏收一个缩写只是"少切一刀"
# （句子偏长、可被显示层切行兜住）；误收一个普通词会把完整句子劈成两半。
_EN_ABBREV_TAILS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "vs", "etc",
    "cf", "al", "fig", "no", "inc", "ltd", "co", "corp", "col", "gen",
    "sen", "rep", "rev", "hon", "univ", "dept", "est", "approx", "ave",
    "blvd", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec", "e.g", "i.e", "a.m", "p.m", "u.s", "u.k",
}
_EN_WORD_TAIL_RE = re.compile(r"[A-Za-z.]+$")


def _is_english_sentence_end(text, i):
    """text[i] 为 ASCII 句点时判断它是否终结一个句子。

    守卫全部朝"宁可少切、不可错切"的方向设计：
    1. 句点后不是空白/换行 → 不切（小数点 3.5、域名、文件名、
       "U.S-China" 这类连字符复合词都落在这类）；
    2. 省略号（前一个字符仍是句点）→ 不切；
    3. 点前单词命中缩写表（Mr./Dr./e.g./U.S.），或点前是单个 ASCII
       字母且再往前非字母数字（人名首字母 J.、缩写链 U.S. 的最后一个
       点）→ 不切。
    """
    n = len(text)
    j = i + 1
    if i >= 1 and text[i - 1] == ".":
        return False  # 省略号中段/尾点
    prev_ch = text[i - 1] if i >= 1 else ""
    if not prev_ch.isalnum():
        return False  # "(...)" 收尾括号点等孤立符号后不切
    if j < n:
        if not text[j].isspace():
            return False  # 小数点/URL/路径：句点后不是空白
        # 单字母尾（首字母 J. / 缩写链 U.S. 的最后一个点）
        if (i >= 2 and not text[i - 2].isalnum()
                and prev_ch.isascii() and prev_ch.isalpha()):
            return False
        # 缩写词表：取句点前连续字母/点组成的最长尾串比对
        m = _EN_WORD_TAIL_RE.search(text[max(0, i - 12):i])
        if m:
            tok = m.group().lower()
            if tok in _EN_ABBREV_TAILS or tok.lstrip(".") in _EN_ABBREV_TAILS:
                return False
        return True
    # 文本末尾的句点：已排除省略号与孤立符号，视为正常句子结束
    return True


def split_sentences(text):
    """Split script text into sentences by terminal punctuation.

    中文按 。！？（含中文分号 ；、ASCII 分号 ;、换行）切分；ASCII 的 .!?
    作为补充终止符带边界守卫地参与（. 需通过 _is_english_sentence_end，
    !? 需后随空白/文末）——中英混合稿里的英文句子不再整段粘成一个"句子"
    （单次 TTS 文本过长、韵律崩坏、45 字超长告警刷屏），而小数点、缩写、
    省略号、人名首字母均不会被误切。纯中文稿的行为与历史版本完全一致。
    """
    text = text.strip()
    if not text:
        return []
    parts = []
    start = 0
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch in _CN_TERMINATORS:
            i += 1
            parts.append(text[start:i])
            start = i
            continue
        if ch == ".":
            if _is_english_sentence_end(text, i):
                i += 1
                parts.append(text[start:i])
                start = i
                continue
        elif ch in "!?":
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt == "" or nxt.isspace():
                i += 1
                parts.append(text[start:i])
                start = i
                continue
        i += 1
    if start < n:
        parts.append(text[start:])
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


def split_subtitle_lines(text, max_chars=28, max_lines=3, hard_cap=40):
    """把一句长字幕切成均衡字幕行（仅显示用）。

    固定宽度贪婪切行：每行尽量填满到 max_chars，断点优先标点/空格，其次 CJK 字符
    间；连续 ASCII 字母数字（英文词）保持完整、不劈开；标点弱化处理。每行
    <= min(max_chars,hard_cap)，杜绝整句交给 CSS 自然换行后溢出成多视觉行。

    Args:
        text: 单句文本（split_sentences 的一个元素）
        max_chars: 单行目标宽度（字符）；行尽量填满到此值
        max_lines: 行数上限（仅 max_lines<=1 时禁用切分；其余仅作极少触发兜底）
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

def subtitle_params_for(aspect="landscape"):
    """按画幅给出字幕切分参数——单一权威来源。

    片内字幕（gen_hyperframes.py）的切行参数唯一权威来源——只此一份，
    不存在两处数值漂移的可能。

    字幕/内容呈现模式固定按画幅绑定：横屏 bar、竖屏 verse（无用户
    选项），因此本函数只按 aspect 区分。

    Returns:
        dict(max_chars=, hard_cap=, cue_max_lines=)
    """
    # portrait 归一化：竖屏 3:4 复用 vertical 家族标识（与 gen_hyperframes
    # 的归一化语义一致），CLI/调用方传 "portrait" 也拿到竖屏切行参数。
    if aspect == "portrait":
        aspect = "vertical"
    # 竖屏画面窄（1080 宽），每行只能放 ~22 字；横屏字幕条 maxWidth 900、
    # 字号 ≈22 字时 870px/46px ≈28 字。
    max_chars = 22 if aspect == "vertical" else 28
    # 物理行硬上限：次要标点切不动时按此字符级硬切，保证 1 逻辑行 = 1
    # 物理行，不二次换行（横屏 ~34 字/行、竖屏 ~22 字/行）。
    hard_cap = 22 if aspect == "vertical" else 34
    # bar（横屏唯一模式）每 cue ≤2 行（再多会盖住正文卡）；verse（竖屏
    # 唯一模式）按整句渲染——内容是字幕本身，切行只影响 cue 数据不影响
    # 视觉。
    cue_max_lines = 99 if aspect == "vertical" else 2
    return {"max_chars": max_chars,
            "hard_cap": hard_cap, "cue_max_lines": cue_max_lines}


def split_subtitle_cues(text, max_chars=28, cue_max_lines=2, hard_cap=40):
    """把一句长字幕切成若干"cue 组"：每屏最多 cue_max_lines 行。

    观感约束：同屏字幕最多两行，再多显拥挤——超出
    部分顺延到下一条时间线（cue），时长由调用方按字符占比切分。内部先用
    较大的行数上限做标点均衡切行（保证超长句也能切到每行 <= max_chars，
    hard_cap 兜底），再按 cue_max_lines 顺序分组。

    Returns:
        list[list[str]]: 每个元素是一屏的行列表（1..cue_max_lines 行）
    """
    lines = split_subtitle_lines(text, max_chars=max_chars, max_lines=8,
                                 hard_cap=hard_cap)
    return [lines[i:i + cue_max_lines]
            for i in range(0, len(lines), cue_max_lines)]
