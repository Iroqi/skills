#!/usr/bin/env python3
"""跨脚本文件契约的显式化：加载 + 校验中间产物。

各脚本通过文件（segments_source.json / timing_manifest.json / images.json）
通信，历史上这些契约是隐式的——字段缺失、类型不对时会以各种奇怪的报错
或静默降级收场。这里把"必需字段"写成校验函数，缺什么报什么。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _audio import validate_speed  # noqa: E402
from _voices import is_valid_voice_id, list_voice_ids  # noqa: E402

# 段落 id 的合法形态：字母开头，只含字母/数字/下划线/连字符。id 会被
# gen_hyperframes 直接拼进 HTML 的 id=/class= 属性和 GSAP 选择器字符串
# （tl.fromTo("#{sid}",...)）——手写 manifest 里带引号/点号/方括号的 sid
# 轻则选择器匹配失败动画静默丢失，重则内联 <script> 整段 SyntaxError、
# 字幕同步与时间轴注册全部死亡且无报错。在契约层收口校验。
_SID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
# accent 写成 hex（# 开头）时必须是 3 或 6 位：手滑的 #12345 会让
# gen_hyperframes 里所有 `{accent}40` alpha 后缀声明成为非法 CSS 被浏览器
# 整条丢弃——glow/shadow 静默消失、不报任何错，只在成品画面上才看得出来。
_ACCENT_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# ── 内容段落 id 约定（单一来源）──────────────────────────────────
# "哪些段落需要配图" 由 sid 前缀决定：opening/closing 是结构性段落，天生
# 不需要配图。这个约定此前散落在 5 处、3 种写法（startswith(元组) 式、
# 两个 or 串联式、还有注释里复述一遍），改命名规则要动 5 个地方且极易
# 漏掉一处——漏的那处会静默改变配图覆盖率统计口径（把不该算的算进去，
# 或该配图的段落被跳过拦截）。收口在这里。
CONTENT_SID_PREFIXES = ("news", "seg")


def is_content_sid(sid):
    """该 sid 是否属于"需要配图的内容段落"（排除 opening/closing）。"""
    return (sid or "").startswith(CONTENT_SID_PREFIXES)


# ── 跨脚本默认值（单一来源）────────────────────────────────────────
# 历史上 --speed 默认值在 pipeline(1.5)/budget(1.0)/split_series(1.0)
# 各写一份且互不一致：estimate 相对 pipeline 实测系统性偏长约 50%、
# split_series 会多切近一半集数（与 --gap 漂移是同款病型）。所有需要
# "默认 TTS 倍速"的入口一律从这里取，改时只改这一处。
DEFAULT_SPEED = 1.5
# opening/closing 段的默认语速：比正文略慢，凸显开场/收尾的节奏。
# build_from_structured 造段配置时写入，budget estimate 复刻同一假设
# ——两处必须一致，所以也收口在这里。
OPENING_CLOSING_DEFAULT_SPEED = 1.2

# ── 时长估算跨脚本默认值（单一来源）──────────────────────────────
# DEFAULT_CHARS_PER_SEC / DEFAULT_GAP / estimate_sentence_seconds 原先
# 散在 budget.py，pipeline（静音兜底时长估算）与 split_series（集数规划）
# 反向依赖这个可选 CLI 工具——核心管线不应依赖可选件，与 DEFAULT_SPEED
# 同款收口到这里。
DEFAULT_CHARS_PER_SEC = 4.3  # "语速适中"参考值，纯启发式
# 与 pipeline.py 的 --gap 默认值保持一致（不一致会让 estimate 相对实测
# 系统性偏移 (n-1)×Δ——两处必须一起改）。
DEFAULT_GAP = 0.4


def estimate_sentence_seconds(sentence, chars_per_sec, speed):
    """单句预计时长（秒）：字数 / 语速 / 倍速。"""
    return len(sentence) / chars_per_sec / max(speed, 0.01)


def _validate_accent(value, where):
    """accent 取值校验（可选字段）：# 开头必须是合法 3/6 位 hex。

    非 # 开头的值（CSS 颜色名如 red）原样放行——基础色仍可用，只有
    alpha 后缀声明会降级；hex 写错则连基础色一起静默失效，必须在
    进管线前报对人。
    """
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} 的 'accent' 必须是非空字符串"
                         f"（如 #64b5f6），实际: {value!r}")
    v = value.strip()
    if v.startswith("#") and not _ACCENT_HEX_RE.match(v):
        raise ValueError(
            f"{where} 的 accent={value!r} 不是合法 hex 颜色"
            f"（应为 #rgb 或 #rrggbb，如 #64b5f6）")


def _validate_sid(sid, where, seen):
    """段落 id 校验：合法字符集 + 跨段唯一。sid 缺失时跳过（手写最小
    manifest 的容错路径由 gen_hyperframes 兜底负责）。"""
    if sid is None or sid == "":
        return
    if not isinstance(sid, str) or not _SID_RE.match(sid):
        raise ValueError(
            f"{where} 的 id={sid!r} 含非法字符或格式不对——id 只允许"
            f"字母开头，字母/数字/下划线/连字符（如 seg1、opening），"
            f"因为它会被拼进 HTML 属性与 GSAP 选择器")
    if sid in seen:
        raise ValueError(f"{where} 的 id={sid!r} 与前面的段落重复"
                         f"（选择器会互相串台）")
    seen.add(sid)


def validate_segments_source(data):
    """segments_source.json：需要非空的 segments 列表，opening/closing 可选。

    每个 segment 需要 'text'（单人独白）或 'dialogue'（双人/多人对话）二选一。
    使用 'dialogue' 时，顶层必须有 'speakers' 声明每个说话人的 voice_id。

    除了结构校验，还做取值校验：speed 必须是 >0 的有限数值、voice_id 必须是
    预置音色之一。这样坏参数在进 TTS 之前就 fail-fast，而不是每句重试到超时。
    """
    if not isinstance(data, dict):
        raise ValueError("segments_source.json 顶层必须是 JSON 对象")
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("segments_source.json 需要非空的 'segments' 列表"
                         "（至少一条内容段落）")

    # flow：自然叙事模式（默认 false = 章节编号模式）。true 时下游不显示
    # 段落编号、开场目录默认关闭、段落切换用柔和 cross-fade 代替 wipe 扫场，
    # 适合讲解/叙事等"内容之间需要自然承接"的主题；新闻速览类仍用默认。
    if data.get("flow") is not None and not isinstance(data.get("flow"), bool):
        raise ValueError("segments_source.json 的 'flow' 必须是布尔值"
                         "（true=自然叙事模式，不设=章节编号模式）")

    # ── 值校验：speed / voice_id ───────────────────────────────────
    for key in ("opening_speed", "closing_speed"):
        if data.get(key) is not None:
            try:
                validate_speed(data[key])
            except ValueError as e:
                raise ValueError(f"segments_source.json 的 '{key}' 非法：{e}") from e

    speakers = data.get("speakers")
    if speakers is not None and not isinstance(speakers, dict):
        raise ValueError("segments_source.json 的 'speakers' 必须是对象"
                         "（{说话人 key: {voice_id, voice_style?, label?}}）")
    if speakers:
        for spk, spk_cfg in speakers.items():
            if not isinstance(spk_cfg, dict):
                raise ValueError(f"segments_source.json 的 speakers['{spk}'] 必须是对象"
                                 "（{voice_id, voice_style?, label?}）")
            vid = spk_cfg.get("voice_id")
            if vid is not None and not is_valid_voice_id(vid):
                raise ValueError(
                    f"segments_source.json 的 speakers['{spk}'].voice_id={vid!r} "
                    f"不在预置音色里（可用：{', '.join(list_voice_ids())}）")

    for i, seg in enumerate(segments, 1):
        if not isinstance(seg, dict):
            raise ValueError(f"segments[{i}] 必须是对象")
        if not seg.get("title"):
            raise ValueError(f"segments[{i}] 缺少 'title' 字段")
        if seg.get("speed") is not None:
            try:
                validate_speed(seg["speed"])
            except ValueError as e:
                raise ValueError(f"segments[{i}]（title={seg.get('title')!r}）"
                                 f"的 'speed' 非法：{e}") from e
        if seg.get("voice_id") is not None and not is_valid_voice_id(seg["voice_id"]):
            raise ValueError(
                f"segments[{i}]（title={seg.get('title')!r}）的 voice_id="
                f"{seg['voice_id']!r} 不在预置音色里"
                f"（可用：{', '.join(list_voice_ids())}）")
        _validate_accent(seg.get("accent"),
                         f"segments[{i}]（title={seg.get('title')!r}）")
        dialogue = seg.get("dialogue")
        # 类型必须在这里拦下：非列表的 truthy dialogue（字符串/对象）若放行，
        # 会绕过 has_dialogue 判定、又在 build_from_structured 的 if dialogue:
        # 真值分支里被当 turns 迭代，turn.get() 直接 AttributeError 裸栈。
        if dialogue is not None and not isinstance(dialogue, list):
            raise ValueError(f"segments[{i}]（title={seg.get('title')!r}）的 "
                             "'dialogue' 必须是数组 [{speaker, text}, ...]")
        has_dialogue = isinstance(dialogue, list) and len(dialogue) > 0
        has_text = bool(seg.get("text"))
        if not has_text and not has_dialogue:
            raise ValueError(f"segments[{i}]（title={seg.get('title')!r}）"
                             "需要 'text'（独白）或 'dialogue'（对话）字段之一")
        if has_text and has_dialogue:
            raise ValueError(f"segments[{i}]（title={seg.get('title')!r}）"
                             "'text' 和 'dialogue' 只能二选一，不要同时提供")
        if has_dialogue:
            if not speakers:
                raise ValueError(f"segments[{i}]（title={seg.get('title')!r}）"
                                 "使用了 'dialogue'，但顶层缺少 'speakers' 字段"
                                 "（需要为每个说话人声明 voice_id）")
            for j, turn in enumerate(dialogue, 1):
                if not isinstance(turn, dict):
                    raise ValueError(f"segments[{i}].dialogue[{j}] 必须是对象 {{speaker, text}}")
                spk = turn.get("speaker")
                if not spk:
                    raise ValueError(f"segments[{i}].dialogue[{j}] 缺少 'speaker' 字段")
                if not turn.get("text"):
                    raise ValueError(f"segments[{i}].dialogue[{j}]（speaker={spk!r}）缺少 'text' 字段")
                spk_cfg = speakers.get(spk)
                if not isinstance(spk_cfg, dict) or not spk_cfg.get("voice_id"):
                    raise ValueError(f"segments[{i}].dialogue[{j}] 引用了说话人 {spk!r}，"
                                     "但顶层 'speakers' 里没有声明它，或声明里缺少 'voice_id'")
    return data


def load_segments_source(path):
    """读取并校验 segments_source.json。

    文件不存在/无权限（OSError 不是 ValueError 子类，调用方的
    except ValueError 接不住会炸裸栈）与 JSON 语法错误统一转成带路径的
    ValueError——路径打错是最常见的用户输入错误。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise ValueError(f"无法读取 {path}: {e}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} 不是合法 JSON: {e}") from e
    return validate_segments_source(data)


def validate_timing_manifest(data):
    """timing_manifest.json：sentences（非空）与 total_duration（数值）必填。"""
    if not isinstance(data, dict):
        raise ValueError("timing_manifest.json 顶层必须是 JSON 对象")
    # flow 与 segments_source.json 同规则：只接受布尔。gen_hyperframes 用
    # bool(manifest.get("flow")) 判断，"false" 这类字符串会被误判为 True。
    if data.get("flow") is not None and not isinstance(data.get("flow"), bool):
        raise ValueError("timing_manifest.json 的 'flow' 字段必须是布尔值"
                         "（true/false），不接受字符串或数字")
    sentences = data.get("sentences")
    if not isinstance(sentences, list) or not sentences:
        raise ValueError("timing_manifest.json 需要非空的 'sentences' 列表")
    if not isinstance(data.get("total_duration"), (int, float)):
        raise ValueError("timing_manifest.json 缺少数值字段 'total_duration'"
                         "（应为 ffmpeg 实测总时长）")
    def _check_sentence_fields(s, where):
        if not isinstance(s, dict):
            raise ValueError(f"{where} 必须是对象")
        for key in ("index", "text", "start_time", "duration"):
            if key not in s:
                raise ValueError(f"{where} 缺少字段 '{key}'"
                                 "（pipeline.py 产出格式）")

    for i, s in enumerate(sentences):
        _check_sentence_fields(s, f"sentences[{i}]")
    # segments 若提供，每段必须自带非空 sentences 列表——
    # gen_hyperframes.py 对 seg["sentences"] 是直接下标访问（分组渲染的
    # 数据源），缺失时炸裸 KeyError: 'sentences'，不指向真正缺的键。
    # 显性化成契约错误，手写/裁剪 manifest 时第一时间报对人。
    segs = data.get("segments")
    if segs is not None:
        if not isinstance(segs, list):
            raise ValueError("timing_manifest.json 的 'segments' 必须是列表")
        _seen_sids = set()
        for i, sg in enumerate(segs):
            if not isinstance(sg, dict):
                raise ValueError(f"segments[{i}] 必须是对象")
            # id 会拼进 HTML 属性与 GSAP 选择器字符串（见 _SID_RE 注释），
            # 手写 manifest 里坏 sid 的失败模式是"动画静默丢失/整段脚本
            # 语法错误"，必须在契约层报对人
            _validate_sid(sg.get("id"), f"segments[{i}]", _seen_sids)
            _validate_accent(sg.get("accent"), f"segments[{i}]（{sg.get('id', '?')}）")
            ss = sg.get("sentences")
            if not isinstance(ss, list) or not ss:
                sid = sg.get("id", f"segments[{i}]")
                raise ValueError(
                    f"timing_manifest.json 的段落 '{sid}' 缺少非空 "
                    f"'sentences' 列表（segments 分组渲染的数据源，"
                    f"pipeline.py 产出格式）")
            # 段内句子与顶层 sentences 走同一份必需字段校验（只查
            # "非空列表"的话，段内缺 start_time 会在 gen_hyperframes/
            # export_extras 下游炸裸 KeyError，不指向真正缺的键）
            for j, s in enumerate(ss):
                _check_sentence_fields(
                    s, f"segments[{i}]（{sg.get('id', '?')}）.sentences[{j}]")
    return data


def load_timing_manifest(path):
    """读取并校验 timing_manifest.json。

    与 load_segments_source 同理：OSError / JSON 语法错误统一转成
    带路径的 ValueError，调用方只接一种异常。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise ValueError(f"无法读取 {path}: {e}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} 不是合法 JSON: {e}") from e
    return validate_timing_manifest(data)


def validate_images_json(data):
    """images.json：{segment_id: 图片/视频路径}。

    单一对象格式（图片/视频统一写法）：
        {"seg1": {"src": "images/seg1.png"},
         "seg2": {"src": "images/seg2.mp4", "type": "video", "loop": true,
                  "muted": true, "autoplay": true, "poster": "...png"}}
      "type" 可选（auto=按扩展名判断；image/video/gif 显式指定），其余字段均为
      可选（静态图只需 src）。字符串简写 "images/seg1.png" 仍可读取（旧文件/
      手写兼容，等价于 {"src": ...}），工具落盘一律写对象。
    """
    if not isinstance(data, dict):
        raise ValueError("images.json 顶层必须是对象 {segment_id: 图片路径或媒体对象}")
    for key, value in data.items():
        if isinstance(value, str):
            if not value:
                raise ValueError(f"images.json 的 '{key}' 路径不能为空字符串")
        elif isinstance(value, dict):
            if "src" not in value:
                raise ValueError(f"images.json 的 '{key}' 对象格式缺少 'src' 字段（媒体路径）")
            if not value["src"]:
                raise ValueError(f"images.json 的 '{key}' 的 'src' 不能为空")
            # type 字段取值校验（仅在有值时检查）
            if "type" in value and value["type"] not in ("auto", "image", "video", "gif"):
                raise ValueError(
                    f"images.json 的 '{key}' 的 'type' 必须是 auto/image/video/gif"
                    f"（实际: {value['type']!r}）")
        else:
            raise ValueError(
                f"images.json 的 '{key}' 必须是字符串路径或对象（实际: {type(value).__name__}）")
    return data


MEDIA_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}


def classify_media_path(path, explicit_type="auto"):
    """根据文件扩展名和显式 type 判断媒体类型。

    Returns:
        "video" | "image" | "gif"
    """
    if explicit_type != "auto":
        return explicit_type
    ext = os.path.splitext(path.lower())[1]
    if ext in MEDIA_VIDEO_EXTS:
        return "video"
    if ext == ".gif":
        return "gif"
    return "image"
