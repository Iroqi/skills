#!/usr/bin/env python3
"""Generate Hyperframes HTML composition from timing_manifest.json.

Reads the manifest (with optional segments grouping) and produces a complete
Hyperframes composition with GSAP animations, subtitle sync, and audio track.

Features:
- Segment transition wipe (accent-colored full-screen sweep between segments)
- Accent side bar, background glow pulse, body line stagger (always on)
- Optional image cards via --images (progressive enhancement, no hard dependency)

Optionally accepts an --images JSON file mapping segment IDs to image paths,
which will be embedded as right-side illustration cards with glow + slide-in
animation.

Usage:
  python gen_hyperframes.py -m timing_manifest.json -o hf-project/index.html
  python gen_hyperframes.py -m timing_manifest.json -o hf-project/index.html --images hf-project/images.json

The manifest must contain:
  - sentences[]: {index, text, start_time, duration, speaker?}
    speaker（可选）：双人对话段落的说话人显示标签，来自 build_from_structured.py
    的 dialogue 段落；出现时字幕上方会叠加一行说话人标签并按说话人交替配色。
    绝大多数场景（单人独白）没有这个字段，行为和之前完全一样。
  - total_duration: 测量得到的音频总时长
  - segments[] (optional): {id, title, tagline, body, accent, sentences[]}
    If absent, sentences are auto-grouped into chunks of 5.

images.json format (all paths relative to the HTML output directory):
  {"seg1": {"src": "images/seg1-imo.png"}, "seg2": {"src": "images/seg2-gemini.png"}, ...}
  (string shorthand "images/seg1.png" still accepted — old files / hand-writing)
"""
import argparse
import json
import os
import re
import sys
from urllib.parse import quote


# 主题配色 / 视觉模板已拆到独立模块（_theme / _template），本文件只保留
# generate_html() 真正的组装逻辑，避免继续膨胀成"什么都干"的大文件。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _theme import (get_theme_colors, get_default_accent, list_theme_names,  # noqa: E402
                    darken, hex_to_rgb01, relative_luminance, mix, expand_hex)
from _template import load_template  # noqa: E402
from _assets import ensure_local_gsap, GSAP_CDN_URL  # noqa: E402
from _contracts import (load_timing_manifest, validate_images_json,  # noqa: E402
                        classify_media_path)
from _script_utils import split_subtitle_cues  # noqa: E402
from _ffmpeg import get_ffmpeg, parse_duration  # noqa: E402


DEFAULT_ACCENT = get_default_accent()


def esc(text):
    """Escape HTML special characters (HTML attribute / element text).

    For JS string literals (e.g. subtitle cue text assigned via
    textContent), use json.dumps() instead -- HTML entity escaping there
    would render as literal "&amp;" since textContent does not decode
    entities, and raw newline/quote would break the JS source.
    """
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def fallback_segments(sentences):
    """manifest 没有 segments 字段时的兜底分组：每 5 句一组。

    独立成模块级函数（而不是内联在 generate_html 里）。分组规则只写一遍，
    所有调用方共享同一份 timing_manifest.json 作为权威场景描述。
    """
    segments = []
    chunk = 5
    for i in range(0, len(sentences), chunk):
        group = sentences[i:i + chunk]
        segments.append({
            "id": f"seg{len(segments) + 1}",
            "title": group[0]["text"][:20],
            "tagline": "",
            "body": "",
            "accent": DEFAULT_ACCENT,
            "sentences": group,
            # 不注入 speed —— auto-group 不应假设语速，必须由上游 manifest 显式声明。
        })
    return segments


def _segment_duration(seg):
    """段落时长（秒）：优先取显式 duration 字段（契约允许的可选字段），
    缺失时从 sentences 推算——末句 start_time+duration − 首句 start_time。
    pipeline 产出的 segments 不带 duration 字段，时长基本都走推算路径。
    空 sentences 返回 0。
    """
    d = seg.get("duration")
    if d:
        return d
    sents = seg.get("sentences") or []
    if not sents:
        return 0.0
    return ((sents[-1].get("start_time", 0) + sents[-1].get("duration", 0))
            - sents[0].get("start_time", 0))


# 说话人字幕配色（叠在字幕栏 sub_bar 上）。按字幕栏明暗二选一组：
# 浅字幕栏（cream）配深色系、深字幕栏（dark/tech/alert）配浅色系。
# 收口为模块级常量供 selftest [21] 做 WCAG 对比度断言（单一数据源，
# 改这里测试自动跟进）。
SPK_COLORS_ON_LIGHT = ["#1d4ed8", "#b45309", "#047857", "#7c3aed", "#be123c", "#0e7490"]
SPK_COLORS_ON_DARK = ["#7dd3fc", "#fbbf24", "#6ee7b7", "#c4b5fd", "#fda4af", "#67e8f9"]
# agenda 序号圆里的数字色（叠在各段 accent 色圆底上）。深色数字在色板
# 8 色 accent（相对亮度 0.246-0.697）上对比度 5.5-13.8，序号字号为
# numSize 的一半（横竖屏 30/26px bold，WCAG 大字 AA 3.0）余量充足。
# 同样收口供 selftest 断言，防未来被改成低对比色。
AGENDA_NUM_TEXT_COLOR = "#0a0e14"


def generate_html(manifest, audio_src, images=None,
                  width=1920, height=1080,
                  gsap_src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js",
                  aspect="landscape", theme="cream", fps=24, sub_mode=None,
                  v_compact=False):
    """Generate complete Hyperframes HTML composition string.

    Args:
        manifest: dict with sentences[], total_duration, optional segments[]
        audio_src: path to audio file (relative to HTML output dir)
        images: optional dict mapping segment ID -> media object.
            Single object format: {"src": "images/seg1.mp4", "type": "video",
                                   "loop": true, "muted": true, "autoplay": true,
                                   "poster": "images/seg1-poster.png"}
            "type" defaults to "auto" (infer from file extension); static
            images only need "src". String shorthand "images/seg1.png" is
            still accepted (old files / hand-writing), normalized to
            {"src": ...} on load.
        width, height: composition dimensions
        gsap_src: GSAP script URL or local path. Defaults to the jsdelivr CDN;
            pass a relative path (e.g. "vendor/gsap.min.js") for offline /
            air-gapped rendering environments where CDN access is unavailable.
        aspect: 画幅比例 ("landscape" 横屏 16:9 / "vertical" 竖屏 9:16)，
            vertical 时自动调整标题/图片/badge/字幕等布局为竖屏适配。
        theme: 主题配色（可选值以 config/theme_registry.json 为唯一权威来源，
            当前 cream/dark/tech/alert），影响背景渐变、
            网格、正文文字与 body 背景色，不影响每段 accent 彩色。
        fps: 输出帧率提示（写入 data-fps，渲染命令可用 --fps 覆盖；
            默认 24 比 30 少抓 20% 帧、渲染更快）。
        sub_mode: 字幕/内容呈现模式，None（默认）时按画幅解析：
            横屏 bar（经典底部字幕条 + 正文卡，图文讲解的常见形态）、
            竖屏家族 verse（歌词式句子流，短视频常见形态）。竖屏家族
            （vertical/portrait）只有 verse——bar 的"字幕条 + 正文卡
            常驻"在竖屏高度内放不下，显式传 bar 会 raise ValueError。
            横屏两模式可用：verse = bar 布局框架（badge/标题/tagline/
            图片同位）+ 正文卡位置换成固定高 300px 的滚动句子流窗口
            （当前句高亮、已播句淡出、随播报滚动），开场/收尾保留
            agenda、verse 钉在左列底部；bar = 底部字幕条 + 正文要点
            卡片（含说话人标签显示层）。
        v_compact: 竖屏紧凑画布（--aspect portrait，3:4 即 1080x1440）。
            portrait 复用 vertical 的布局家族（data-aspect 仍写
            "vertical"，全部竖屏 CSS 规则照常命中），此标记只切换紧凑
            参数：上下留白收窄（220/190→100/80）、图片槽统一
            4:3——1:1 方图（980x980）加 300px 句子流在 1440 高度里
            放不下，4:3 槽（980x735）才能给标题留出完整空间。
    """
    total_dur = manifest["total_duration"]
    sentences = manifest["sentences"]
    segments = manifest.get("segments")
    images = images or {}

    # portrait 归一化：紧凑竖屏复用 vertical 的布局家族，data-aspect 写
    # "vertical"（竖屏 CSS 选择器全部照常命中），v_compact 只切紧凑参数。
    # 归一化放函数本体内而不是 CLI 层，库调用方传 aspect="portrait"
    # 也能得到正确产物。
    if aspect == "portrait":
        aspect = "vertical"
        v_compact = True

    # sub_mode 默认按画幅解析（在 portrait 归一化之后，portrait 走竖屏
    # 默认 verse）：横屏 bar（经典图文讲解形态）、竖屏 verse（短视频
    # 歌词流形态）。显式传值不受影响。
    if sub_mode is None:
        sub_mode = "bar" if aspect == "landscape" else "verse"

    # 竖屏家族只有 verse：bar 的"底部字幕条 + 正文卡常驻"在竖屏放不下
    # （1440/1920 高度被大标题+图片占满，正文卡与字幕条互相挤压）。
    # fail-fast 报错而不是静默降级成 verse——静默换模式会让 CLI 调用方
    # 以为拿到了 bar 产物。
    if aspect == "vertical" and sub_mode == "bar":
        raise ValueError(
            "竖屏家族（vertical/portrait）仅支持 verse 字幕模式："
            "bar 的底部字幕条 + 正文要点卡在竖屏高度内放不下。"
            "如需 bar 形态请用 --aspect landscape。")

    # ── 配图/视频归一化 ───────────────────────────────────────────
    # 把字符串简写统一成对象格式（对象是唯一标准格式），后续渲染逻辑只需处理一种结构。
    # 文件级校验（存在性/可解码性）不在这层做：main() 的 CLI 路径在调用本
    # 函数前已用 validate_images_files()（本模块唯一的校验实现）fail-fast
    # 过一遍；历史上这里还叠了一份提示级的弱校验——同一张图 PIL verify
    # 最多跑三遍（--aspect both 下本函数被调两次），且弱校验探不出截断的
    # mp4、与强校验两套逻辑会漂移，已删除。库调用方需要校验时自行调
    # validate_images_files()。
    _normalized_images = {}
    for sid, media in images.items():
        if isinstance(media, str):
            media_path = media
            media_type = classify_media_path(media_path)
            media_opts = {}
        elif isinstance(media, dict):
            media_path = media["src"]
            media_type = classify_media_path(
                media_path, media.get("type", "auto"))
            media_opts = {k: v for k, v in media.items()
                          if k not in ("src", "type")}
        else:
            continue
        _normalized_images[sid] = {
            "src": media_path,
            "media_type": media_type,
            "opts": media_opts,
        }
    images = _normalized_images

    # 主题配色（背景/网格/文字/body 背景），accent 色不受主题影响
    theme_colors = get_theme_colors(theme)
    # 按主题主文字色亮度判深浅底（theme_registry 是唯一权威，
    # 新增主题无需改这里的枚举）——tagline 的"同色相只调明度"在深色
    # 底下方向要反过来（提亮而不是压暗）。
    _theme_lum = relative_luminance(theme_colors.get("text_color", "#22262b"))
    _dark_theme = _theme_lum is not None and _theme_lum > 0.5

    # 对话说话人配色（bar 模式显示层）：不能固定"深色底"，因为字幕栏
    # 背景（sub_bar_rgb）随主题变——cream 是近白底、dark/tech/alert 是
    # 深色底。按字幕栏背景相对亮度动态选一组有足够对比度的配色（浅底
    # 深色系、深底浅色系），按说话人首次出现顺序取色、取完循环，支持
    # 3+ 说话人。verse 模式句子流不区分说话人，仅保留 cue 数据层的
    # speaker/spk 字段供下游消费。
    _sub_bar_r, _sub_bar_g, _sub_bar_b = (
        int(x) for x in theme_colors.get("sub_bar_rgb", "0,0,0").split(","))
    _sub_bar_luminance = (0.299 * _sub_bar_r + 0.587 * _sub_bar_g + 0.114 * _sub_bar_b) / 255
    spk_colors = (SPK_COLORS_ON_LIGHT if _sub_bar_luminance > 0.5
                  else SPK_COLORS_ON_DARK)
    spk_color_css = ("\n".join(
        f".sub-speaker.spk-{i}{{color:{c}}}" for i, c in enumerate(spk_colors)
    ) if sub_mode == "bar" else "")

    # 从模板加载布局/动画/字体参数
    tpl = load_template()
    tpl_layout = tpl["layout"].get(aspect, tpl["layout"]["landscape"])
    tpl_anim = tpl["animation"]
    tpl_typo = tpl.get("typography", {})

    # 从模板提取 CSS 变量值（替代原来的 if aspect == "vertical" 硬编码分支）
    _bl = tpl_layout["badge"]
    css_badge_top = f'{_bl["top"]}px'
    css_badge_left = f'{_bl["left"]}px'
    css_badge_size = _bl["size"]
    css_badge_font = f'{_bl["fontSize"]}px'

    _tl = tpl_layout["title"]
    css_title_pad = _tl["padding"]
    css_news_title_size = f'{_tl["fontSizeNews"]}px'
    css_opening_title_size = f'{_tl["fontSizeOther"]}px'

    _bl2 = tpl_layout["body"]
    css_body_font = f'{_bl2["fontSize"]}px'
    css_body_max = _bl2.get("maxWidth")

    _sl = tpl_layout["subtitle"]
    # 字号两模式共用（verse 句子流行 / bar 字幕条，横竖屏各自模板值：
    # 横屏 46 / 竖屏 40）；height/padding/maxWidth 仅 bar 模式的
    # sub-bar 读取（verse 无字幕条）
    css_sub_font = f'{_sl["fontSize"]}px'
    css_sub_height = f'{_sl.get("height", 176)}px'
    css_sub_pad = _sl.get("padding", "0 50px")
    css_sub_max = _sl.get("maxWidth", 1600)

    # ── 竖屏紧凑参数（--aspect portrait，3:4 即 1080x1440）──
    # 上下留白收窄 + 图片槽统一 4:3：1:1 方图（980x980）加 300px 句子流
    # 在 1440 高度里放不下（100+标题139+48+980+300+80 = 1647 溢出），
    # 4:3 槽（980x735）总高 1402，余 38px 由 verse 的 margin-top:auto 吸收。
    # 竖屏只有 verse（bar 在上方已被拦下），无模式分支。
    if v_compact:
        _v_pad = "100px 50px 80px"
        _v_img_ar = "4/3"
    else:
        _v_pad = "220px 50px 190px"
        _v_img_ar = "1/1"

    # ── sub_mode 分支产物：字幕/内容呈现两模式各自的 CSS 与 JS ──
    # verse（默认）= 歌词式句子流；bar = 经典底部字幕条 + 正文卡。
    # 分支只影响显示层；cue 数据层（含 speaker/spk）两模式同构。
    if sub_mode == "bar":
        _sub_css = f""".sub-bar{{position:absolute;bottom:0;left:0;right:0;min-height:{css_sub_height};background:linear-gradient(transparent,rgba({theme_colors["sub_bar_rgb"]},0.92));display:flex;flex-direction:column;align-items:center;justify-content:center;padding:{css_sub_pad}}}
.sub-speaker{{font-size:calc({css_sub_font} * 0.5);font-weight:700;letter-spacing:0.05em;margin-bottom:6px;opacity:0}}
{spk_color_css}
.sub-text{{font-size:{css_sub_font};color:{theme_colors["text_color"]};text-align:center;line-height:1.6;text-shadow:{theme_colors["sub_text_shadow"]};max-width:{css_sub_max}px;opacity:0}}
.sub-line + .sub-line{{margin-top:4px}}"""
        _v_verse_css = ""
        _sub_js = """
  const subEls = Array.from(document.querySelectorAll(".sub-text"));
  const spkEls = Array.from(document.querySelectorAll(".sub-speaker"));
  let curCueKey = null; // 当前已渲染的 cue 标识，避免每帧重建行 DOM
  tl.eventCallback("onUpdate", function() {
    const t = tl.time();
    let found = null;
    for (let i = 0; i < cues.length; i++) {
      if (t >= cues[i].t && t < cues[i].t + Math.max(cues[i].d, 0.01)) { found = cues[i]; break; }
    }
    if (found) {
      // cue 切换时才重建行 DOM；行内容用 textContent（不走 innerHTML），
      // 信源文本里的任何字符都只会被当纯文本渲染。
      const key = found.t + "|" + found.d + "|" + (found.speaker || "");
      if (key !== curCueKey) {
        curCueKey = key;
        for (const subEl of subEls) {
          subEl.textContent = "";
          for (const ln of found.lines) {
            const div = document.createElement("div");
            div.className = "sub-line";
            div.textContent = ln;
            subEl.appendChild(div);
          }
        }
      }
      // found.d 兜底 0.01（与上方 cue 命中判断同一保护）：
      // duration:0 的手写句会得到 Infinity/NaN，字幕透明度落到 0
      const local = (t - found.t) / Math.max(found.d, 0.01);
      const fade = 0.15;
      let op = 1;
      if (local < fade) op = local / fade;
      else if (local > 1 - fade) op = (1 - local) / fade;
      op = Math.max(0, Math.min(1, op));
      for (let i = 0; i < subEls.length; i++) {
        const subEl = subEls[i];
        subEl.style.opacity = op;
        // 多行字幕逐行错峰淡入：第 i 行在 cue 进度 i*0.18 后开始出现；
        // 整体淡入淡出由容器 subEl 的 op 承担，行内只设自身错峰值
        // （行内再乘 op 会 lop*op² 双重相乘，把 fade 曲线平方化）。
        const rows = subEl.children;
        for (let r = 0; r < rows.length; r++) {
          const start = r * 0.18;
          let lop = Math.max(0, Math.min(1, (local - start) / fade));
          rows[r].style.opacity = lop;
        }
        const spkEl = spkEls[i];
        if (found.speaker) {
          if (spkEl.textContent !== found.speaker) spkEl.textContent = found.speaker;
          spkEl.className = "sub-speaker spk-" + (found.spk || 0);
          spkEl.style.opacity = op;
        } else {
          spkEl.style.opacity = 0;
        }
      }
    } else {
      curCueKey = null;
      for (let i = 0; i < subEls.length; i++) {
        subEls[i].style.opacity = 0;
        spkEls[i].style.opacity = 0;
      }
    }
    if (window.__pvUpdate) window.__pvUpdate(); // 预览刷新钩子（无预览时为空）
  });
"""
    else:
        _sub_css = f"""/* ── verse 句子流（横竖屏同款，"正文卡+底部字幕"的融合替代）──
   横屏 = bar 布局框架 + 内容框换滚动框：badge/标题/tagline/图片/进度条
   与 bar 完全同位，正文要点卡的位置（title-wrap 内、tagline 之下）换成
   固定高 300px 的滚动句子流窗口，随标题行数浮动（bar 的正文卡行为），
   DOM 由 Python 侧插进 title-wrap。开场/收尾段例外：agenda/chips 是
   结构化目录不是句子流，正文保留显示，verse 钉在左列底部（left:50 与
   标题同轴线，宽 870；bottom:60 + 高 300 → 窗口 y 720-1020，右缘 920
   不与图片槽 x 970 重叠），标题组居中于窗口上方（title_top 在 Python
   侧改为 calc((100% - 380px)/2)）。
   clip 上下各留 60px 内边距且滚动锚点同为 60px：首句/尾句完整落在
   mask 渐隐区（上下各 14%）之外，不被边缘虚化。竖屏改走 flex 钉底
   （见下方 vertical 覆盖块）。 */
.verse{{position:relative;width:100%;height:300px;overflow:hidden;
  -webkit-mask-image:linear-gradient(transparent,#000 14%,#000 86%,transparent);
  mask-image:linear-gradient(transparent,#000 14%,#000 86%,transparent)}}
#opening .verse,#closing .verse{{position:absolute;left:50px;bottom:60px;width:870px}}
/* clip 必须定位：verse-line.offsetTop 需要相对 clip 而不是绝对定位的
   seg-card（否则首行 offsetTop 巨大，滚动公式恒被钳到段落尾部——
   第一句永远在窗口外） */
.verse-clip{{position:relative;padding:60px 0;transition:transform .45s cubic-bezier(.4,0,.2,1)}}
.verse-line{{font-size:{css_sub_font};line-height:1.5;font-weight:500;color:{theme_colors["text_color"]};opacity:.62;transition:opacity .3s;padding:5px 0;text-align:left}}
.verse-line.active{{opacity:1!important;font-weight:700}}
.verse-line.past{{opacity:.38!important}}"""
        _v_verse_css = (
            '\n[data-aspect="vertical"] .verse{position:static!important;'
            'width:auto!important;'
            'margin-top:auto!important;flex-shrink:0!important}'
        )
        _sub_js = """
  let curCueKey = null; // 当前已处理的 cue 标识，避免每帧重复触发
  tl.eventCallback("onUpdate", function() {
    const t = tl.time();
    let found = null;
    for (let i = 0; i < cues.length; i++) {
      if (t >= cues[i].t && t < cues[i].t + Math.max(cues[i].d, 0.01)) { found = cues[i]; break; }
    }
    if (found) {
      const key = found.t + "|" + found.d + "|" + (found.speaker || "");
      if (key !== curCueKey) {
        curCueKey = key;
        // verse 高亮（si 相同的相邻 cue 重复调用幂等，开销可忽略）
        if (found.si !== undefined && found.si >= 0) verseUpdate(found.si);
      }
    } else {
      // 句间 gap：保持最后一行的状态（高亮停在末句，窗口不回滚）
      curCueKey = null;
    }
    if (window.__pvUpdate) window.__pvUpdate(); // 预览刷新钩子（无预览时为空）
  });
"""

    _al = tpl_layout["agenda"]
    css_agenda_font = f'{_al["fontSize"]}px'
    css_agenda_num = f'{_al["numSize"]}px'
    css_agenda_margin = f'{_al.get("marginTop", 56)}px'

    _cl = tpl_layout["chip"]
    css_chip_font = f'{_cl["fontSize"]}px'
    css_chip_margin = f'{_cl.get("marginTop", 96)}px'
    # chip 内边距读模板 chip.padding（横竖屏各自的值都生效，不硬编码）
    css_chip_pad = _cl.get("padding", "14px 28px")

    _tgl = tpl_layout["tagline"]
    css_tagline_font = f'{_tgl["fontSize"]}px'

    _il = tpl_layout["image"]
    css_img_width = _il["width"]
    css_img_height = _il["height"]
    if _il.get("centerHorizontal"):
        css_img_top = f'{_il["top"]}px' if isinstance(_il["top"], int) else _il["top"]
        css_img_pos = "left:50%;transform:translateX(-50%)"
    else:
        css_img_top = _il.get("top", "50%")
        if isinstance(css_img_top, int):
            css_img_top = f"{css_img_top}px"
        css_img_pos = f'right:{_il.get("right", 100)}px;transform:translateY(-50%)'

    # 其余 CSS 变量（网格/进度条/强调条/字体排印）也从模板读取
    _grid = tpl_layout.get("grid", {})
    css_grid_size = _grid.get("size", 60)
    _prog = tpl_layout.get("progressBar", {})
    css_prog_height = _prog.get("height", 4)
    _abar = tpl_layout.get("accentBar", {})
    css_abar_width = _abar.get("width", 6)
    # 字体排印
    css_title_weight = tpl_typo.get("titleWeight", 900)
    css_title_lh = tpl_typo.get("titleLineHeight", 1.25)
    css_tagline_weight = tpl_typo.get("taglineWeight", 600)
    css_body_weight = tpl_typo.get("bodyWeight", 400)
    css_body_margin_top = _bl2.get("marginTop", 24)
    css_body_pad = _bl2.get("padding", "28px 32px")
    css_body_lh = _bl2.get("lineHeight", 1.55)
    css_body_gap = tpl_typo.get("bodyLineGap", 12)
    css_body_radius = tpl_typo.get("bodyBorderRadius", 16)
    css_body_border_left = tpl_typo.get("bodyBorderLeft", 4)
    css_img_radius = tpl_typo.get("imageBorderRadius", 24)
    css_badge_weight = tpl_typo.get("badgeWeight", 900)
    # v5.0.1: fontFamily 之前只在 template.json 里躺着、CSS 却硬编码——改模板
    # 不生效（pitfalls #17 同款"死字段"病型）。现在实际读模板值。
    css_font_family = tpl_typo.get(
        "fontFamily", '"Microsoft YaHei","PingFang SC","Noto Sans SC",sans-serif')
    css_agenda_gap = _al.get("gap", 20)
    css_tagline_mt = _tgl.get("marginTop", 16)

    # Auto-group if no segments provided.
    if not segments:
        segments = fallback_segments(sentences)

    # flow 自然叙事模式（segments_source.json 顶层 "flow": true，pipeline
    # 透传到 manifest）：不显示段落编号 badge、开场目录不画编号圆、段落
    # 切换不做 accent 色 wipe 扫场（改用更长的 cross-fade）。适合讲解/
    # 叙事类主题——内容之间靠写稿的承接句自然过渡，而不是"第 1 条/第 2 条"
    # 的板块感。手写 manifest 也可以直接设 "flow": true。
    flow_mode = bool(manifest.get("flow"))

    # Calculate clip timing
    clips = []
    for seg in segments:
        seg_sents = seg["sentences"]
        if not seg_sents:
            continue
        start = seg_sents[0]["start_time"]
        end = seg_sents[-1]["start_time"] + seg_sents[-1]["duration"]
        clips.append({
            "seg": seg,
            "start": round(start, 2),
            "duration": round(end - start, 2),
        })

    # 收集所有内容段落的标题（按顺序），用于给开场页生成"内容目录"目录、
    # 结尾页生成"回顾"标签条——这两个页面原本只有大标题+副标题，画面偏空。
    # 数据直接从已有的 segments 里取（标题本来就有），不需要额外的 AI 调用
    # 或新的 manifest 字段。封顶 8 条，避免条数多时列表溢出画面。
    content_agenda = [
        {"title": seg.get("title", ""), "accent": expand_hex(seg.get("accent", DEFAULT_ACCENT))}
        for seg in segments
        if (seg.get("id") or "").startswith("news") or (seg.get("id") or "").startswith("seg")
    ]
    AGENDA_MAX = 8

    # ── Build segment card HTML + GSAP ─────────────────────────────
    seg_cards = []
    gsap_lines = []

    for i, clip in enumerate(clips):
        seg = clip["seg"]
        # 用 .get + 默认 id，避免缺 id 字段时 KeyError 让整个渲染崩溃。
        # 上游 pipeline.py 已保证有 id，但 gen_hyperframes 也要能独立处理
        # 用户手写/外部工具产出的 manifest，做防御性兜底。
        sid = seg.get("id") or f"seg{i+1}"
        s = clip["start"]
        d = clip["duration"]
        # expand_hex 归一化：3 位 accent（如 #fff）拼 alpha 后缀（{ac}40/{ac}15）
        # 会得到非法 8 位颜色、整条 CSS 被浏览器丢弃——统一展开成 6 位
        ac = expand_hex(seg.get("accent", DEFAULT_ACCENT))
        is_news = sid.startswith("news") or sid.startswith("seg")
        has_image = sid in images
        # 首页/尾页是全屏居中布局，右侧视频会遮挡文字，跳过配图
        if sid.startswith("opening") or sid.startswith("closing"):
            has_image = False

        # 动画参数快捷引用（从模板加载，替代硬编码数值）
        a_ = tpl_anim

        # Badge（章节编号圆）。flow 自然叙事模式下不显示——编号是"新闻
        # 条目"的板块语言，叙事/讲解内容的段落之间应该靠承接句自然流动。
        badge = ""
        if is_news and not flow_mode:
            # 编号用正则提取前缀后的数字：replace 链对 "segment1" 这类 id
            # 会把 "seg" 替掉留下 "ment1"（错误编号）；无数字前缀不渲染 badge
            _num_m = re.match(r"^(?:news|seg)(\d+)", sid)
            if _num_m:
                num = _num_m.group(1)
                badge = f'<div class="badge" id="badge-{sid}" style="background:{ac}">{num}</div>'

        # Title positioning — shift to left side when image is present.
        # left_px 读模板：正文段（带 badge）读 leftWithBadge（改模板即
        # 生效；硬编码会让模板字段成死字段，见 references/pitfalls.md
        # #17 同类）；其余配图段（flow 模式内容段/opening/closing）
        # 统一 50px 左边距，与图片槽的 right:50 对称（同一套页边距）。
        if has_image:
            left_px = (
                _tl.get("leftWithBadge", 205) if (is_news and badge) else 50
            )
            title_left = f"{left_px}px"
            title_right = "auto"
            if _il.get("centerHorizontal"):
                # 竖屏配图在标题下方居中，标题宽度不受图片约束，尽量占满
                title_width = f"{max(width - left_px - 60, 200)}px"
            else:
                # 横屏：标题横跨在正文与图片上方，右边界不必跟图片左缘对齐，
                # 给足宽度；正文宽度由模板 body.maxWidth 控制（与图片留清晰边界）。
                title_width = f"{max(width - left_px - 110, 200)}px"
        else:
            # 用显式像素宽度而不是 "auto"（配合 left+right 由浏览器计算）——
            # 旧版 WebKit 渲染引擎（比如 wkhtmltoimage 用的那个内核）在
            # ".seg-card{position:absolute;inset:0}" 这种父级容器下，对嵌套子元素的
            # "left:0;right:0;width:auto" 百分比/auto 宽度计算有 bug，会把可用宽度
            # 算成只有几十像素，导致标题文字每个字都单独换行。显式传一个具体像素值
            # 可以完全避开这个计算路径，在现代浏览器下行为完全等价（标准盒模型），
            # 不是"hack"，只是更保险的写法。
            # TODO（渲染引擎升级路径，见 references/pitfalls.md 第 14 条）：
            # 这个 workaround 长期成立不依赖上游修不修 bug——它本身就是更标准的
            # 写法，不是"绕过 bug 的临时代码"，所以不需要"探测引擎版本后切回
            # auto/%"这种双路径逻辑，那样反而多一条没被充分测过的代码分支。
            # 唯一需要留意的：如果以后确实想把这段改回 auto/%（比如想减少
            # Python 里的像素计算逻辑），必须先跑一遍
            # scripts/visual_regression.py 的关键帧像素回归（对比 baseline），
            # 不能只看生成的 CSS 文本或 DOM 结构断言——第 14 条的教训就是这类
            # 布局 bug 只有真正渲染截图才暴露。
            left_px = (
                _tl.get("leftWithBadge", 205)
                if (is_news and badge)
                else _tl.get("leftWithoutBadge", 0)
            )
            title_left = f"{left_px}px"
            title_right = "0"
            title_width = f"{max(width - left_px, 200)}px"
        # 配图（含动画）时标题一律左对齐——包括 flow 模式（无 badge）：
        # 右侧图片占据画面右半，居中的标题/tagline 会压到图上。
        # 只有无配图时才保留居中版式。
        title_align = "left" if (is_news and badge) or has_image else "center"
        # 标题字号/位置随画幅变化：竖屏整体下移且字号缩小
        # 正文段配图时用更小的字号（模板 fontSizeNewsImage），一行能装更多字，
        # 配合上面的加宽，让长标题晚一点才换行、画面更舒展
        if is_news and has_image:
            title_size = f'{_tl.get("fontSizeNewsImage", int(_tl["fontSizeNews"] * 0.86))}px'
        else:
            title_size = css_news_title_size if is_news else css_opening_title_size
        # 标题 top 读模板 title.topNews/topOther（topNews 是裸数字按 px，
        # topOther 是带单位字符串如 "30%"，按类型分别处理）
        _top_raw = _tl["topNews"] if is_news else _tl["topOther"]
        title_top = f"{_top_raw}px" if isinstance(_top_raw, (int, float)) else str(_top_raw)
        # 左列垂直居中：无 badge 的配图段（flow 模式内容段）
        # 左列内容组在可用区（顶部安全带 ~80px 到字幕条 ~904px，中心
        # 492 ≈ 屏高 46%）垂直居中，消除正文卡片下方的条状空白——卡片
        # 高度随行数变化时也自动均衡。带 badge 段保持 topNews 固定值
        # （badge 钉在左上角，标题要跟它同行）。
        title_transform = ""
        if has_image and not badge and not _il.get("centerHorizontal"):
            title_top = str(_tl.get("topImageCenter", "46%"))
            title_transform = "transform:translateY(-50%);"

        # 横屏 verse 开场/收尾：标题组（含 agenda/chips）垂直居中于底部
        # 钉位的 verse 窗口上方的空间（1080-380=700 的中心 350），而非
        # 全屏中心——高 agenda（最多 8 条）下缘才不会与 verse 窗口重叠
        if (sub_mode == "verse" and aspect == "landscape"
                and not is_news):
            title_top = "calc((100% - 380px)/2)"
            title_transform = "transform:translateY(-50%);"

        # Tagline
        tagline_html = ""
        if seg.get("tagline"):
            # 深色主题向白提亮（无差别 _darken 在 dark/tech/alert 下
            # 对比度只有 ~3.3，不达 WCAG AA）
            _tag_color = (mix(ac, "#ffffff", 0.62) if _dark_theme
                          else darken(ac))
            # 左对齐段落的副标题相对标题缩进一点（模板 tagline.indent），
            # 制造层级差、不和标题左缘对齐；居中版式（opening/closing、
            # 竖屏 centerHorizontal）不缩进——居中下偏移会破坏中轴线
            # （竖屏另有 CSS padding-left:16px 的既有缩进，不走这里）。
            _tag_style = f'color:{_tag_color}'
            if has_image and not _il.get("centerHorizontal"):
                _tag_style += f';padding-left:{_tgl.get("indent", 24)}px'
            tagline_html = (
                f'<div class="tagline" id="tag-{sid}" '
                f'style="{_tag_style}">{esc(seg["tagline"])}</div>'
            )

        # Body text — each line gets its own ID for stagger animation
        body_html = ""
        body_line_count = 0
        agenda_count = 0
        recap_count = 0
        if seg.get("body"):
            # 过滤空行：body 尾部的换行符（手写 manifest 常见）不该渲染出
            # 空的 body-line div 和对应的 GSAP tween。
            body_lines = [l for l in seg["body"].split("\n") if l.strip()]
            body_line_count = len(body_lines)
            body_items = "".join(
                f'<div class="body-line" id="bodyline-{sid}-{j}">{esc(line)}</div>'
                for j, line in enumerate(body_lines)
            )
            # 配图时正文框收窄（max-width 来自模板 body.maxWidth），
            # 与右侧图片保持明显间距；标题仍可占满标题区宽度。
            # 正文卡片左边距与标题对齐（同一套页边距，不用负 margin 近似）。
            if has_image and css_body_max:
                body_style = f' style="max-width:{css_body_max}px"'
            else:
                body_style = ""
            body_html = f'<div class="body-text" id="body-{sid}"{body_style}>{body_items}</div>'
        elif sid == "opening" and content_agenda and flow_mode and seg.get("agenda", True):
            # flow 自然叙事模式的开场预告：轻量 chips 一排，
            # 与收尾页 recap 同一视觉语言——竖排列表+编号圆是章节模式的
            # 语言，flow 不用。manifest 写 "agenda": false
            # 可显式关闭。
            _oc = content_agenda[:AGENDA_MAX]
            chips = [
                f'<div class="recap-chip" id="recapchip-{sid}-{k}" '
                f'style="border-color:{item["accent"]}">{esc(item["title"])}</div>'
                for k, item in enumerate(_oc)
            ]
            # 封顶提示行与 closing recap 同规则：数字索引 id，保证 GSAP
            # stagger 循环能匹配到（见下方 -more 注释）
            if len(content_agenda) > AGENDA_MAX:
                chips.append(
                    f'<div class="recap-chip recap-more" id="recapchip-{sid}-{len(_oc)}">'
                    f'...等共 {len(content_agenda)} 条</div>'
                )
            recap_count = len(chips)
            body_html = f'<div class="recap-chips" id="chiprow-{sid}">{"".join(chips)}</div>'
        elif sid == "opening" and content_agenda and seg.get("agenda", True):
            # 开场页原本只有标题+副标题、画面偏空——补一份"内容目录"目录，
            # 让观众提前知道接下来有哪几条内容。只在段落没有手写 body 时
            # 自动生成，不覆盖 manifest segments 里手动写的内容。
            shown = content_agenda[:AGENDA_MAX]
            n = len(shown)
            # 动态字号：条目越多字号越小，避免 8 条时列表过长、底部挤进
            # 字幕栏（目录被挤到字幕位置、太靠左）。字号随条目数从
            # shrinkThreshold 到 shrinkMax 线性缩小到 minFont；序号圆、行间距等比缩放。
            _ag_base = _al.get("fontSize", 46)
            _ag_min = _al.get("minFont", 32)
            _ag_th = _al.get("shrinkThreshold", 6)
            # shrinkMax 不能超过 AGENDA_MAX（列表封顶 AGENDA_MAX 条，
            # 模板值更大时收缩比例永远到不了 1、字号缩不到 minFont）
            _ag_max = min(_al.get("shrinkMax", AGENDA_MAX), AGENDA_MAX)
            if n <= _ag_th:
                _ag_font = _ag_base
            else:
                _ratio = (n - _ag_th) / max(1, (_ag_max - _ag_th))
                _ag_font = max(_ag_min, round(_ag_base - (_ag_base - _ag_min) * _ratio))
            # 序号圆等比缩放：比例跟随模板 numSize/fontSize（横屏 60/46、
            # 竖屏 52/40 都 ≈1.30）——此前硬编码 1.3 让 numSize 成死字段
            _ag_num = max(36, round(_ag_font * (_al["numSize"] / _al["fontSize"])))
            _ag_gap = max(10, round(_ag_font * 0.75))  # 行间距等比缩放（≈0.75 行）
            _ag_indent = _al.get("indentLeft", 140)    # 左缩进：把目录从屏幕左缘往右挪
            _ag_mt = _al.get("marginTop", 56)          # 距标题上间距：往上挪给列表留空间
            # 竖屏下方空间充裕，不再对任何条目做高度限制（之前的两行/单行
            # 强制槽位都去掉），让条目按内容自然撑开；当前 40px 下 8 条均
            # 单行，序号圆顶部对齐即可保证行列整齐。
            _ag_lh = _al.get("lineHeight", 1.34)
            _ag_slot = 0
            _ag_slot_style = f'min-height:{_ag_slot}px' if _ag_slot else ""
            items = [
                # flow 自然叙事模式下目录不画编号圆（编号是章节模式的语言），
                # 只留标题行；章节模式保持编号圆 + 标题。
                f'<div class="agenda-item" id="agendaitem-{sid}-{k}"'
                + (f' style="{_ag_slot_style}"' if _ag_slot_style else "")
                + '>'
                + (f'<span class="agenda-num" style="background:{item["accent"]};'
                   f'width:{_ag_num}px;height:{_ag_num}px;'
                   f'font-size:{round(_ag_num * 0.5)}px">{k + 1}</span>'
                   if not flow_mode else "")
                + f'<span class="agenda-title" style="font-size:{_ag_font}px;'
                  f'line-height:{_ag_lh}">'
                  f'{esc(item["title"])}</span>'
                f'</div>'
                for k, item in enumerate(shown)
            ]
            # "…等共 N 条"封顶行用数字索引 id（不是 -more 后缀）——动画
            # 循环按数字索引起选择器，-more 永远匹配不到，GSAP 静默跳过，
            # 导致该行从不隐藏、在其他条目 stagger 入场前先闪现
            if len(content_agenda) > AGENDA_MAX:
                items.append(
                    f'<div class="agenda-item agenda-more" id="agendaitem-{sid}-{n}"'
                    + (f' style="{_ag_slot_style}"' if _ag_slot_style else "")
                    + f'><span class="agenda-title" style="font-size:{_ag_font}px;'
                      f'line-height:{_ag_lh}">'
                    f'...等共 {len(content_agenda)} 条</span></div>'
                )
            agenda_count = len(items)
            body_html = (
                f'<div class="agenda-list" id="agendalist-{sid}" '
                f'style="margin-top:{_ag_mt}px;margin-left:{_ag_indent}px;'
                f'gap:{_ag_gap}px">{"".join(items)}</div>'
            )
        elif sid == "closing" and content_agenda and seg.get("recap", not flow_mode):
            # 结尾页同理——用"回顾"标签条复述本期内容要点，帮观众加深印象，
            # 视觉上做成一排 chip 标签（而不是重复开场的竖排列表样式），
            # 跟开场页拉开视觉差异。flow 模式默认关闭（叙事型收尾靠
            # closing 稿件本身，chips+"等共 N 条"的清单语言与叙事气质
            # 不符）；manifest 写 "recap": true 可显式打开。注意与
            # opening 的区别：开场预告 flow 下也默认开，收尾回顾不跟着开。
            shown = content_agenda[:AGENDA_MAX]
            chips = [
                f'<div class="recap-chip" id="recapchip-{sid}-{k}" '
                f'style="border-color:{item["accent"]}">{esc(item["title"])}</div>'
                for k, item in enumerate(shown)
            ]
            # "…等共 N 条"封顶行用数字索引 id（不是 -more 后缀）——动画
            # 循环按数字索引起选择器，-more 永远匹配不到，GSAP 静默跳过，
            # 导致该行从不隐藏、在其他条目 stagger 入场前先闪现
            if len(content_agenda) > AGENDA_MAX:
                # 跟开场目录同款封顶提示：静默截断会让观众以为只有这 8 条
                chips.append(
                    f'<div class="recap-chip recap-more" id="recapchip-{sid}-{len(shown)}">'
                    f'...等共 {len(content_agenda)} 条</div>'
                )
            recap_count = len(chips)
            body_html = f'<div class="recap-chips" id="chiprow-{sid}">{"".join(chips)}</div>'

        # Image / video container (right side, vertically centered).
        image_html = ""
        if has_image:
            media_info = images[sid]
            media_path = media_info["src"]
            media_type = media_info["media_type"]
            media_opts = media_info["opts"]
            if media_type == "video":
                # 视频配图：<video> 自动循环静音播放
                loop = "loop" if media_opts.get("loop", True) else ""
                muted = "muted" if media_opts.get("muted", True) else ""
                playsinline = "playsinline" if media_opts.get("playsinline", True) else ""
                # autoplay 同样读 images.json 选项（与其余 video 选项一致）
                autoplay = "autoplay" if media_opts.get("autoplay", True) else ""
                poster = media_opts.get("poster", "")
                poster_attr = f'poster="{quote(poster)}"' if poster else ""
                image_html = (
                    f'\n    <div class="seg-image" id="img-{sid}" '
                    f'style="box-shadow:0 0 {_il.get("glow", 40)}px {ac}40">\n'
                    f'      <video id="vid-{sid}" src="{quote(media_path)}" '
                    f'data-start="{s}" data-duration="{d}" '
                    f'{loop} {muted} {autoplay} {playsinline} '
                    f'{poster_attr} '
                    f'style="width:100%;height:100%;object-fit:contain;border-radius:{css_img_radius}px">\n'
                    f'      </video>\n'
                    f'    </div>'
                )
            else:
                # 静态图 / 动图：<img> 不变
                image_html = (
                    f'\n    <div class="seg-image" id="img-{sid}" '
                    f'style="box-shadow:0 0 {_il.get("glow", 40)}px {ac}40;--img:url(\'{quote(media_path)}\')">\n'
                    f'      <img src="{quote(media_path)}" alt="">\n'
                    f'    </div>'
                )

        # 配图时标题左对齐、宽度已按图片左缘算好，必须清掉模板里默认的
        # "0 100px" 内边距（那是对无图居中标题留的边距），否则实际文字宽度
        # 会被 padding 吃掉 200px，导致换行过早（标题挤在一起）。
        title_pad_inline = "padding:0;" if has_image else ""
        # 字幕/内容 DOM 按 sub_mode 二选一：
        # - verse：歌词式句子流——该段全部句子按序渲染成静态行（完整
        #   句子，CSS 自动换行），运行时由 cue 的 si 高亮当前句、已播句
        #   淡出、窗口随播报滚动。正文信息由句子流逐句呈现，被替代段落
        #   的 body 卡不再渲染（见 _verse_kills_body）。
        # - bar：经典底部字幕条（sub-bar），正文卡正常显示。
        _is_oc = sid.startswith("opening") or sid.startswith("closing")
        # verse 替代 body 卡的段落：横屏内容段（verse 窗口进 title-wrap
        # 占据正文卡位置）、竖屏有图段（大图+句子流已满高，body 卡放不下
        # ——原来用 CSS display:none 兜，改 DOM 层不渲染，GSAP stagger
        # 不再指向不存在的行）。竖屏无图段与开场/收尾保留 body/agenda：
        # 无图段只有标题太单薄，agenda 是结构化目录不能换成句子流。
        _verse_kills_body = sub_mode == "verse" and (
            (aspect == "landscape" and not _is_oc)
            or (aspect == "vertical" and has_image)
        )
        if _verse_kills_body:
            body_html = ""
            body_line_count = 0
            agenda_count = 0
            recap_count = 0
        # 横屏内容段的 verse 插进 title-wrap 文档流（正文卡位置，随标题
        # 浮动）；竖屏所有段与横屏开场/收尾的 verse 留在 seg-card 尾部
        # （竖屏 flex 钉底 / 横屏开场收尾 CSS 钉底）
        _verse_in_wrap = (sub_mode == "verse" and aspect == "landscape"
                          and not _is_oc)
        verse_html = ""
        sub_bar_html = ""
        if sub_mode == "verse":
            _vlines = [
                f'<div class="verse-line" data-i="{_s2.get("index", _k)}">'
                f'{esc(_s2["text"])}</div>'
                for _k, _s2 in enumerate(seg["sentences"])
            ]
            if _verse_in_wrap:
                # 有图段 title-wrap 是全宽（标题横跨正文与图片上方，见
                # title_width 计算），verse 不能 width:100% 继承——会滑到
                # 右侧图片底下被遮（右缘须 920，与图片槽左缘 970 留 50px
                # 间距）。显式钉左列正文卡宽度（模板 body.maxWidth）；
                # 无图段 title-wrap 全宽居中版式，verse 同宽居中（段落间
                # 视觉宽度不跳变）。margin-top 与 bar 的正文卡同值
                # （css_body_margin_top），保持"内容框位置"的视觉连续。
                _vwin_w = css_body_max or 870
                _vstyle = (f'width:{_vwin_w}px;margin-top:{css_body_margin_top}px'
                           if has_image else
                           f'width:{_vwin_w}px;margin:{css_body_margin_top}px auto 0')
                verse_html = (
                    f'\n      <div class="verse" id="verse-{sid}" '
                    f'style="{_vstyle}" '
                    f'data-layout-allow-overflow data-layout-allow-overlap '
                    f'data-layout-allow-occlusion>'
                    f'<div class="verse-clip">'
                    f'{"".join(_vlines)}</div></div>'
                )
            else:
                verse_html = (
                    # 三个 layout 豁免属性都源于同一误报机制：滚动出窗的
                    # 行视觉上被窗口 overflow:hidden 裁掉，但静态 DOM rect
                    # 仍在原位——越过窗口上缘与标题区相交（content_overlap，
                    # allow-overlap）、越过窗口下缘与底部元素（如进度条）
                    # 相交（text_occluded，allow-occlusion，portrait 底部
                    # 留白只有 80px 时会触发）、整体越出卡片（allow-
                    # overflow）。活动行锚定在窗口内 60px，真实重叠不可能
                    # 发生。
                    f'\n    <div class="verse" id="verse-{sid}" '
                    f'data-layout-allow-overflow data-layout-allow-overlap '
                    f'data-layout-allow-occlusion>'
                    f'<div class="verse-clip">'
                    f'{"".join(_vlines)}</div></div>'
                )
        else:
            sub_bar_html = (
                f'\n    <div class="sub-bar">\n'
                f'      <div class="sub-speaker"></div>\n'
                f'      <div class="sub-text"></div>\n'
                f'    </div>\n'
            )
        seg_cards.append(
            f'  <div id="{sid}" class="clip seg-card{" has-image" if has_image else ""}" '
            f'data-start="{s:.2f}" data-duration="{d:.2f}" '
            f'data-track-index="1" style="opacity:0">\n'
            f'    <div class="seg-glow" id="glow-{sid}" '
            f'style="background:radial-gradient(circle at 50% 50%,{ac}15,transparent 60%)"></div>\n'
            f'    <div class="seg-accent-bar" id="bar-{sid}" style="background:{ac}"></div>\n'
            f'    {badge}\n'
            f'    <div class="seg-title-wrap" style="{title_transform}left:{title_left};'
            f'right:{title_right};width:{title_width};top:{title_top};'
            f'text-align:{title_align};{title_pad_inline}">\n'
            f'      <div class="seg-title" id="title-{sid}" '
            f'style="font-size:{title_size};text-shadow:0 0 40px {ac}40">'
            f'{esc(seg["title"])}</div>\n'
            f'      {tagline_html}\n'
            f'      {body_html}\n'
            f'      {verse_html if _verse_in_wrap else ""}\n'
            f'    </div>'
            f'{image_html}\n'
            f'    <div class="seg-progress" id="prog-{sid}" '
            f'style="background:{ac};width:0"></div>\n'
            f'    {verse_html if not _verse_in_wrap else ""}{sub_bar_html}\n'
            f'  </div>'
        )

        # GSAP animations — transition wipe + fade for clips after first
        is_first = (i == 0)
        a_first = a_.get("firstSegmentFadeIn", {})
        a_wipe = a_.get("transitionWipe", {})
        a_fadein = a_.get("segmentFadeIn", {})
        a_fadeout = a_.get("segmentFadeOut", {})
        if is_first:
            # First clip: simple fade in (no wipe needed)
            gsap_lines.append(
                f'tl.fromTo("#{sid}",{{opacity:0}},{{opacity:1,'
                f'duration:{a_first.get("duration", 0.4)}}},{s:.2f})'
            )
        else:
            if flow_mode:
                # flow 自然叙事模式：不做 accent 色 wipe 扫场（板块切换的
                # 视觉语言），只用更长的 cross-fade——上一段的 fade-out 与
                # 这一段的 fade-in 在边界自然交叠，视觉上"接着讲"而不是
                # "翻到下一条"。
                _fadein_dur = max(a_fadein.get("duration", 0.3), 0.6)
                gsap_lines.append(
                    f'tl.fromTo("#{sid}",{{opacity:0}},'
                    f'{{opacity:1,duration:{_fadein_dur},'
                    f'ease:"{a_fadein.get("ease", "power2.out")}"}},{s:.2f})'
                )
            else:
                # Transition wipe: full-screen accent sweep + cross-fade.
                # set twipe color with explicit position = start-0.2 (same as the
                # wipe animation start). Without a position, tl.set() appends at the
                # timeline's current end, which can be AFTER the wipe animation
                # starts when the previous segment's fade-out ends close to this
                # segment's start. This would make the wipe briefly show the
                # previous segment's accent color.
                # wipe 起播位置下限 0：手写 manifest 首段 start_time < 0.2 时
                # s-0.2 为负，GSAP 时间轴负位置被起点裁掉、转场视觉不完整
                _wipe_at = max(s - 0.2, 0.0)
                gsap_lines.append(
                    f'tl.set("#twipe",{{backgroundColor:"{ac}"}},{_wipe_at:.2f})'
                )
                gsap_lines.append(
                    f'tl.fromTo("#twipe",{{x:0}},'
                    f'{{x:"200%",duration:{a_wipe.get("duration", 0.4)},'
                    f'ease:"{a_wipe.get("ease", "power2.inOut")}"}},{_wipe_at:.2f})'
                )
                _fadein_dur = a_fadein.get("duration", 0.3)
                gsap_lines.append(
                    f'tl.fromTo("#{sid}",{{opacity:0}},'
                    f'{{opacity:1,duration:{_fadein_dur},'
                    f'ease:"{a_fadein.get("ease", "power2.out")}"}},{s:.2f})'
                )
        # Fade out + hard kill (duration < gap to avoid next-clip boundary coincidence)
        gsap_lines.append(
            f'tl.to("#{sid}",{{opacity:0,duration:{a_fadeout.get("duration", 0.3)},'
            f'ease:"{a_fadeout.get("ease", "power2.in")}"}},{s + d:.2f})'
        )
        _fadeout_dur = a_fadeout.get("duration", 0.3)
        gsap_lines.append(
            f'tl.set("#{sid}",{{opacity:0}},{s + d + _fadeout_dur:.2f})'
        )
        # Accent side bar grows from top
        a_bar = a_.get("accentBar", {})
        gsap_lines.append(
            f'tl.from("#bar-{sid}",{{scaleY:0,transformOrigin:"top",'
            f'duration:{a_bar.get("duration", 0.5)},'
            f'ease:"{a_bar.get("ease", "power2.out")}"}},{s:.2f})'
        )
        # Background glow pulse
        a_glow = a_.get("glowPulse", {})
        gsap_lines.append(
            f'tl.fromTo("#glow-{sid}",{{opacity:0}},{{opacity:1,'
            f'duration:{a_glow.get("duration", 0.8)}}},{s:.2f})'
        )
        # Title entrance
        a_title = a_.get("titleEntrance", {})
        gsap_lines.append(
            f'tl.from("#title-{sid}",{{scale:{a_title.get("from", 0.5)},'
            f'duration:{a_title.get("duration", 0.5)},'
            f'ease:"{a_title.get("ease", "back.out(1.7)")}"}},{s:.2f})'
        )
        if badge:
            a_badge = a_.get("badgeEntrance", {})
            gsap_lines.append(
                f'tl.from("#badge-{sid}",{{scale:{a_badge.get("from", 0)},'
                f'duration:{a_badge.get("duration", 0.4)},'
                f'ease:"{a_badge.get("ease", "back.out(2)")}"}},{s:.2f})'
            )
        if seg.get("tagline"):
            a_tag = a_.get("taglineEntrance", {})
            gsap_lines.append(
                f'tl.from("#tag-{sid}",{{opacity:0,y:{a_tag.get("y", 20)},'
                f'duration:{a_tag.get("duration", 0.5)}}},'
                f'{s + a_tag.get("delay", 0.3):.2f})'
            )
        # Body line stagger — each line slides in with incremental delay
        if body_line_count > 0:
            a_body = a_.get("bodyLineStagger", {})
            for j in range(body_line_count):
                gsap_lines.append(
                    f'tl.from("#bodyline-{sid}-{j}",{{opacity:0,'
                    f'x:{a_body.get("x", -20)},'
                    f'duration:{a_body.get("duration", 0.4)}}},'
                    f'{s + a_body.get("startDelay", 0.6) + j * a_body.get("delay", 0.15):.2f})'
                )
        # 开场"内容目录"：每条从左侧滑入，逐条错峰
        if agenda_count > 0:
            a_agenda = a_.get("agendaStagger", {})
            for j in range(agenda_count):
                gsap_lines.append(
                    f'tl.from("#agendaitem-{sid}-{j}",{{opacity:0,'
                    f'x:{a_agenda.get("x", -30)},'
                    f'duration:{a_agenda.get("duration", 0.4)},'
                    f'ease:"{a_agenda.get("ease", "power2.out")}"}},'
                    f'{s + a_agenda.get("startDelay", 0.6) + j * a_agenda.get("delay", 0.12):.2f})'
                )
        # 结尾"回顾"标签条：每个 chip 弹入，逐条错峰
        if recap_count > 0:
            a_chip = a_.get("chipStagger", {})
            for j in range(recap_count):
                gsap_lines.append(
                    f'tl.from("#recapchip-{sid}-{j}",{{opacity:0,'
                    f'scale:{a_chip.get("scaleFrom", 0.8)},'
                    f'duration:{a_chip.get("duration", 0.35)},'
                    f'ease:"{a_chip.get("ease", "back.out(1.8)")}"}},'
                    f'{s + a_chip.get("startDelay", 0.6) + j * a_chip.get("delay", 0.1):.2f})'
                )
        if has_image:
            a_img = a_.get("imageEntrance", {})
            # Vertical: image is centered below title, slide up (y) instead of from right (x)
            if aspect == "vertical":
                _img_ent = f'y:{a_img.get("vert_y", 40)}'
            else:
                _img_ent = f'x:{a_img.get("x", 60)}'
            gsap_lines.append(
                f'tl.from("#img-{sid}",{{opacity:0,'
                f'{_img_ent},'
                f'duration:{a_img.get("duration", 0.8)},'
                f'ease:"{a_img.get("ease", "power2.out")}"}},'
                f'{s + a_img.get("startDelay", 0.2):.2f})'
            )
        gsap_lines.append(
            f'tl.to("#prog-{sid}",{{width:"100%",duration:{d:.2f},ease:"none"}},{s:.2f})'
        )

    # ── Subtitle cues ──────────────────────────────────────────────
    # Subtitle text is assigned to element.textContent in JS, so the value
    # must be a valid JS string literal — NOT HTML-escaped. Using esc() here
    # would (a) render &quot; / &amp; as literal text since textContent does
    # not decode entities, and (b) let raw newlines / quotes break the JS
    # source. json.dumps produces a properly escaped JS string literal and
    # also escapes \n / \" / \\ correctly.
    # 双人对话：句子若带 speaker（build_from_structured.py 的 dialogue 段落
    # 产出），按"首次出现顺序"给不同说话人分配 0/1 交替色，字幕上方叠一行
    # 说话人标签。没有 speaker 字段的句子（绝大多数场景）完全不受影响。
    # 长句二次切行 + 每屏最多两行（用户观感约束）：一句话超过
    # 软上限时先在次要标点处均衡切行，再按每屏两行分组——超出的行顺延到
    # 下一条 cue（时长按字符占比切分、相邻 cue 首尾相接，整组与该句音频
    # 窗口严格对齐）。切行只影响排版，不影响 TTS 断句与句间停顿。
    sub_cues = []
    speaker_order = []

    # json.dumps 产出合法 JS 字符串字面量，但不转义 "/"——信源来自外部
    # 不可控文本（用户粘贴/网页抓取），句子里含字面 "</script>" 时会把
    # 内联脚本提前截断、整页 JS 崩掉。json.dumps 后统一把 "</" 替换成
    # "<\\/"（JS 字符串里等价，浏览器不会当结束标签解析）。
    def _js(s):
        return json.dumps(s, ensure_ascii=False).replace("</", "<\\/")

    # cue 行宽参数（数据层）：行宽按各画幅物理宽度估算——竖屏 980px/40px
    # 字号 ≈22 字（bar 模式字幕条 maxWidth 900 同量级），横屏 870px/46px
    # ≈28 字；宽容度 1.5（最长行 ~42 字），超长靠多切几行兜底。verse 模式
    # 下切分粒度不影响视觉（同一句的多条 cue 携带相同 si，verseUpdate 幂等），
    # bar 模式下每行直接渲染进字幕条。
    _sub_cap = 22 if aspect == "vertical" else 28
    _sub_slack = 1.0 if aspect == "vertical" else 1.5
    # 物理行硬上限：次要标点切不动时按此字符级硬切，保证每行 <= 物理行宽
    # （横屏 ~34 字/行、竖屏 ~22 字/行），1 逻辑行 = 1 物理行，不二次换行。
    _sub_hard = 22 if aspect == "vertical" else 34
    for sent in sentences:
        # 每屏行数上限：bar 模式字幕条每 cue ≤2 行（不盖正文卡）；verse
        # 模式竖屏按整句渲染（内容=字幕，切行只影响 cue 数据不影响视觉）
        _sub_cue_lines = ((99 if aspect == "vertical" else 2)
                          if sub_mode == "verse" else 2)
        groups = split_subtitle_cues(sent["text"], max_chars=_sub_cap,
                                     slack=_sub_slack,
                                     hard_cap=_sub_hard,
                                     cue_max_lines=_sub_cue_lines)
        total_chars = sum(len("".join(g)) for g in groups)
        speaker = sent.get("speaker")
        t0 = sent["start_time"]
        for g in groups:
            js_lines = "[" + ",".join(_js(l) for l in g) + "]"
            extra = ""
            # 说话人标签挂到句子切出的每条 cue（数据层：speaker/spk 字段
            # 按首次出现顺序分配索引并循环，下游消费者按 cue 粒度取值，
            # 只挂首条的话长对话句句中会丢失归属）。
            if speaker:
                if speaker not in speaker_order:
                    speaker_order.append(speaker)
                spk_idx = speaker_order.index(speaker) % len(spk_colors)
                extra = f',speaker:{_js(speaker)},spk:{spk_idx}'
            # 时长按字符占比切分：TTS 逐字速率近似均匀，误差被 cue 渐隐
            # 掩盖；t0 顺次累加保证相邻 cue 首尾相接、组总时长=句时长
            frac = (sum(len(l) for l in g) / total_chars) if total_chars else 1.0
            d = sent["duration"] * frac
            # si = 句子全局序号（manifest sentences[].index）：竖屏 verse
            # 句子流靠它把运行时 cue 对应到静态渲染的 .verse-line 行
            # （横屏不渲染 verse DOM，JS 端自然跳过，字段留着无害）。
            sub_cues.append(
                f'{{t:{t0:.2f},d:{d:.2f},si:{sent.get("index", -1)},lines:{js_lines}{extra}}}'
            )
            t0 += d
    sub_cues_js = ",\n    ".join(sub_cues)

    gsap_code = "\n  ".join(gsap_lines)

    # 浏览器预览：逻辑在独立的 scripts/preview.js（生成 HTML 时复制到输出目录），
    # index.html 只通过外部脚本引用 preview.js。注意：任何注释/文案中都不能出现
    # 字面量“尖括号斜杠 script 尖括号”（会被当作脚本结束标签），否则 hyperframes
    # 打包单文件 HTML 内联脚本时会在此处截断，导致页面报错。

    # ── Assemble HTML ──────────────────────────────────────────────
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width={width},height={height}">
<script src="{gsap_src}"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{width}px;height:{height}px;overflow:hidden;background:{theme_colors["bg_gradient"]}}}
@font-face{{font-family:"Microsoft YaHei";src:local("Microsoft YaHei"),local("MicrosoftYaHei")}}
@font-face{{font-family:"PingFang SC";src:local("PingFang SC"),local("PingFangSC")}}
@font-face{{font-family:"Noto Sans SC";src:local("Noto Sans SC"),local("NotoSansSC")}}
body{{font-family:{css_font_family}}}

.bg{{position:absolute;inset:0;background:{theme_colors["bg_gradient"]}}}
.grid{{position:absolute;inset:0;background-image:linear-gradient({theme_colors["grid_color"]} 1px,transparent 1px),linear-gradient(90deg,{theme_colors["grid_color"]} 1px,transparent 1px);background-size:{css_grid_size}px {css_grid_size}px}}

.seg-card{{position:absolute;inset:0}}
.seg-glow{{position:absolute;inset:0;opacity:0;pointer-events:none}}
.seg-accent-bar{{position:absolute;top:0;left:0;width:{css_abar_width}px;height:{height}px;transform-origin:top}}
.badge{{position:absolute;top:{css_badge_top};left:{css_badge_left};width:{css_badge_size}px;height:{css_badge_size}px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:{css_badge_font};font-weight:{css_badge_weight};color:{theme_colors["text_color"]};z-index:5}}
.seg-title-wrap{{position:absolute;right:0;padding:{css_title_pad}}}
.seg-title{{font-weight:{css_title_weight};color:{theme_colors["text_color"]};line-height:{css_title_lh};word-break:break-word}}
.tagline{{font-size:{css_tagline_font};margin-top:{css_tagline_mt}px;font-weight:{css_tagline_weight}}}
.body-text{{margin-top:{css_body_margin_top}px;padding:{css_body_pad};background:{theme_colors["body_bg"]};border-radius:{css_body_radius}px;border-left:{css_body_border_left}px solid {theme_colors["soft_border"]};backdrop-filter:blur(4px)}}
.body-line{{font-size:{css_body_font};color:{theme_colors["body_text"]};line-height:{css_body_lh};font-weight:{css_body_weight}}}
.body-line+.body-line{{margin-top:{css_body_gap}px}}
.agenda-list{{display:flex;flex-direction:column;gap:{css_agenda_gap}px;margin-top:{css_agenda_margin};align-items:flex-start;text-align:left}}
.agenda-item{{display:flex;align-items:flex-start;gap:14px}}
.agenda-num{{flex-shrink:0;width:{css_agenda_num};height:{css_agenda_num};border-radius:50%;
  display:flex;align-items:center;justify-content:center;font-weight:800;
  font-size:calc({css_agenda_num} * 0.5);color:{AGENDA_NUM_TEXT_COLOR}}}
.agenda-title{{font-size:{css_agenda_font};font-weight:600;color:{theme_colors["body_text"]}}}
.agenda-more .agenda-title{{opacity:0.6;font-weight:400}}
.recap-chips{{display:flex;flex-wrap:wrap;gap:16px;margin-top:{css_chip_margin};justify-content:center;
  max-width:100%}}
.recap-chip{{padding:{css_chip_pad};border-radius:999px;border:2px solid;
  font-size:{css_chip_font};font-weight:600;color:{theme_colors["body_text"]};
  background:rgba(255,255,255,0.04)}}
.recap-chip.recap-more{{opacity:0.6;font-weight:400;border-style:dashed}}
.seg-progress{{position:absolute;bottom:0;left:0;height:{css_prog_height}px}}
#twipe{{position:absolute;top:0;left:-100%;width:100%;height:100%;z-index:50;pointer-events:none;will-change:transform}}
.seg-image{{position:absolute;top:{css_img_top};{css_img_pos};width:{css_img_width}px;height:{css_img_height}px;border-radius:{css_img_radius}px;overflow:hidden;background:{theme_colors["body_bg"]}}}
.seg-image::before{{content:"";position:absolute;inset:-14%;z-index:0;background-image:var(--img);background-size:cover;background-position:center;filter:blur(28px) brightness(0.9);transform:scale(1.15)}}
.seg-image img{{position:relative;z-index:1;width:100%;height:100%;object-fit:contain;display:block}}

{_sub_css}

/* ── Vertical (9:16) 大标题 + 图片 + 歌词式句子流（竖屏唯一模式）──
   Only activates when #root has data-aspect="vertical".
   portrait（3:4，1080x1440）复用本块全部规则，仅 _v_pad/_v_img_ar
   两处紧凑取值不同（见上方 v_compact 分支：上下留白 100/80、图片槽
   4:3）。
   竖屏只有 verse（bar 在 generate_html 入口已被拦下）。结构：大标题
   区（顶部，边距 220）→ 图片（正方形槽：占满内容宽度、
   aspect-ratio 1:1，980×980）→ verse 句子流（钉在内容区底部，窗口
   300px）。verse/clip/line 的通用规则在上方基础块，这里只覆盖竖屏的
   定位差异：verse 从横屏的"title-wrap 内文档流/开场收尾钉底"改回
   seg-card flex 流内钉底（margin-top:auto）。
   verse 对齐规则：所有段落（含开场/收尾）的 verse 上边界钉在内容区
   底部同一位置——内容段图片是固定正方形，标题行数差异由图片与句子流
   之间的留白吸收（verse 的 margin-top:auto），不传导给 verse 位置；
   开场/收尾标题垂直居中于句子上方的空间（margin:auto 0——flex 的
   自由空间先被 auto margin 均分，标题居中的同时 verse 仍钉底与内容
   段齐平；文字仍居中）。侧边距统一 50px（标题/图片/句子流对齐同一
   轴线；1080−50×2=980 即图片槽的宽度）。 */
[data-aspect="vertical"] .seg-card{{display:flex!important;flex-direction:column!important;padding:{_v_pad}!important}}
[data-aspect="vertical"] .seg-accent-bar{{display:none!important}}
[data-aspect="vertical"] .seg-title-wrap{{position:relative!important;left:auto!important;right:auto!important;top:auto!important;transform:none!important;width:100%!important;padding:0!important;text-align:left!important;flex-shrink:0!important}}
[data-aspect="vertical"] .seg-title{{line-height:1.35!important}}
[data-aspect="vertical"] .tagline{{margin-top:12px!important;padding-left:16px}}
[data-aspect="vertical"] .seg-image{{position:relative!important;top:auto!important;left:auto!important;right:auto!important;transform:none!important;width:100%!important;max-width:100%!important;height:auto!important;aspect-ratio:{_v_img_ar}!important;flex:0 1 auto!important;margin:48px 0 0!important;border-radius:16px!important}}
[data-aspect="vertical"] .seg-image img,[data-aspect="vertical"] .seg-image video{{width:100%!important;height:100%!important;object-fit:contain!important}}
[data-aspect="vertical"] .body-text{{margin:24px 0 0!important;padding:20px 24px!important;max-width:100%!important;margin-left:0!important}}
[data-aspect="vertical"] .agenda-list{{margin-left:0!important;padding-left:16px}}
[data-aspect="vertical"] .recap-chips{{margin-top:24px!important;justify-content:flex-start}}
[data-aspect="vertical"] #opening .seg-title-wrap,[data-aspect="vertical"] #closing .seg-title-wrap{{text-align:center!important;margin:auto 0!important}}
[data-aspect="vertical"] #opening .recap-chips,[data-aspect="vertical"] #closing .recap-chips{{justify-content:center!important}}
[data-aspect="vertical"] #opening .agenda-list{{margin-left:0!important;padding-left:0}}
[data-aspect="vertical"] .seg-glow{{display:none}}{_v_verse_css}

</style>
</head>
<body>
<div id="root" data-composition-id="main" data-aspect="{aspect}" data-start="0" data-duration="{total_dur:.2f}" data-width="{width}" data-height="{height}" data-fps="{fps}">
  <div class="bg"></div>
  <div class="grid"></div>

{chr(10).join(seg_cards)}

  <div id="twipe" data-layout-allow-occlusion></div>

  <audio id="main-audio" data-start="0" data-duration="{total_dur:.2f}" data-track-index="10" src="{quote(audio_src)}"></audio>
</div>

<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{paused:true}});
  {gsap_code}

  // Sentence-flow sync via onUpdate
  const cues = [
    {sub_cues_js}
  ];
  // verse 句子流：静态行集合（verse 模式横竖屏都渲染；bar 模式集合为
  // 空、verseUpdate 自然跳过——机制保留是为了两模式共用同一份 cue 驱动）。
  // 渲染器逐帧 seek 触发 onUpdate（非线性顺序），测量做懒缓存——布局值
  // （offsetTop/clientHeight/scrollHeight）不受 transform 与 seek 顺序影
  // 响、字体就绪后恒定，首帧测量一次即可；transform 赋绝对值幂等，乱序
  // seek 重复执行结果一致（seek-safe）。
  const verses = Array.from(document.querySelectorAll(".verse"));
  const verseCache = new Map();
  // 字体异步加载会改变行高：就绪后清缓存，下次触发时用最终字形重测
  // （期间已设的 transform 按旧测量算，偏差在下一次 cue 切换即修正）
  if (document.fonts && document.fonts.ready) {{
    document.fonts.ready.then(() => verseCache.clear());
  }}
  const verseUpdate = (si) => {{
    for (const v of verses) {{
      let c = verseCache.get(v);
      if (!c) {{
        const clip = v.querySelector(".verse-clip");
        c = {{clip: clip, winH: v.clientHeight, clipH: clip.scrollHeight, lines: []}};
        for (const ln of v.querySelectorAll(".verse-line")) {{
          c.lines.push({{el: ln, top: ln.offsetTop}});
        }}
        verseCache.set(v, c);
      }}
      let act = null;
      for (const L of c.lines) {{
        if (parseInt(L.el.getAttribute("data-i"), 10) === si) {{ act = L; break; }}
      }}
      for (const L of c.lines) {{
        const di = parseInt(L.el.getAttribute("data-i"), 10);
        L.el.classList.toggle("active", L === act);
        // past 只在同一 verse（同段落）内比较，跨段句序无先后语义
        L.el.classList.toggle("past", !!act && di < si);
      }}
      if (act) {{
        // 歌词式滚动：active 行滚到窗口顶部往下 60px（= clip 顶部
        // 内边距，与 .verse-clip 的 padding 保持一致——首句/尾句完整
        // 落在 mask 渐隐区之外，不被边缘虚化）；首行不滚出顶、末行
        // 不露底白（clip 比窗口矮时整体不滚）
        const y = c.clipH <= c.winH ? 0
          : Math.max(c.winH - c.clipH, Math.min(0, 60 - act.top));
        c.clip.style.transform = "translateY(" + y + "px)";
      }}
    }}
  }};
{_sub_js}
// 注册放脚本末尾（同步执行流内，行为与紧跟构建后注册等价）：hyperframes
// 的 lint 规则按源码位置判定"注册是否早于 document.fonts.ready 异步构建"
// （window.__timelines[ 出现点必须在 fonts.ready 之后），bar 模式 JS 里
// Array.from 的 ".from(" 会被规则的 GSAP 补间正则误判，注册线在 fonts.ready
// 之前就会触发 gsap_timeline_registered_before_async_build 误报。
window.__timelines["main"] = tl;</script>
<script src="preview.js"></script>
</body>
</html>'''

    return html


def _uncovered_content_sids(manifest, images):
    """manifest 中没有配图映射的内容段落（news/seg 前缀）sid 列表。

    与 run.py _image_coverage 的 news/seg 前缀约定同一口径
    （opening/closing 不参与配图统计）。gen_hyperframes 对缺图只提示
    不拦截：横屏按需配图允许内容性质不需要图的段落留空，但
    "搜图无结果/漏配"与"按需不需要"极易混淆，静默无图会让 agent
    把缺图当正常（run.py 一键编排有缺图拦截，分步执行没有）。
    """
    return [seg.get("id", "") for seg in manifest.get("segments", [])
            if (seg.get("id") or "").startswith(("news", "seg"))
            and seg.get("id", "") not in images]


def validate_images_files(images, out_dir, seg_durs=None):
    """校验 images.json 引用的媒体文件存在且可解码（本模块唯一实现）。

    检测项（依赖缺失时优雅降级，只降强度不改行为）：
    - 存在性：所有类型（含 svg）；
    - 图片可解码：PIL.Image.verify()（svg 是文本格式，PIL 打不开必然
      报错，跳过——存在性已足够）；
    - 视频可解码：ffmpeg 全解码探测（能同时发现 moov 缺失、头部损坏
      与尾部截断——下载中断的典型形态），并解析视频时长，比段落长时
      打"渲染只显示前段"提示（信息级，不阻断）。

    Args:
        images: images.json 映射（字符串简写或对象格式均可）
        out_dir: HTML 输出目录（相对路径的解析基准）
        seg_durs: {segment_id: 段落时长秒}，视频截断提示用；None 跳过提示

    Returns:
        (missing, corrupt) 两个列表：missing=[(sid, media_path)]，
        corrupt=[(sid, media_path, reason)]。是否 fail-fast 由调用方决定
        （main() 对非空结果报错退出）。
    """
    missing_imgs = []
    corrupt_imgs = []
    try:
        from PIL import Image as _PILImage
        _pil_available = True
    except ImportError:
        _pil_available = False
    # 视频探测用 ffmpeg（PIL 打不开 mp4/webm）；找不到
    # ffmpeg 可执行文件时降级为存在性校验（与 Pillow 缺失同型）
    _ffmpeg_probe = None
    try:
        _ffmpeg_probe = get_ffmpeg()
    except Exception:
        _ffmpeg_probe = None

    def _probe_video_ok(path):
        """ffmpeg 解码探测。配图视频都是短片（通常 <15s），全解码
        成本低，能同时发现 moov 缺失、头部损坏与尾部截断（下载中断
        的典型形态）。-v info 让 stderr 携带 Duration 行，成功时顺带
        解析出视频时长（用于"视频比段落长会被截断"提示）。
        返回 (ok, reason, duration|None)。"""
        import subprocess as _sp
        try:
            r = _sp.run(
                [_ffmpeg_probe, "-v", "info", "-i", path,
                 "-f", "null", "-"],
                capture_output=True, timeout=15)
        except _sp.TimeoutExpired:
            return False, "probe timeout(15s)", None
        except OSError:
            # ffmpeg 可执行不存在（get_ffmpeg 找不到时返回字面
            # "ffmpeg" 兜底串，subprocess 抛 FileNotFoundError）——
            # 降级为存在性校验通过（文件存在在调用前已查过），
            # 不当损坏处理，与 Pillow 缺失同型降级
            return True, "", None
        err_text = (r.stderr or b"").decode("utf-8", "replace")
        if r.returncode != 0:
            return False, (err_text.strip()[-160:] or
                           f"exit {r.returncode}"), None
        return True, "", parse_duration(err_text)

    for sid, rel in images.items():
        # 原始 images.json 用 "type" 字段；归一化后才变成 "media_type"
        if isinstance(rel, dict):
            media_path = rel.get("src", "")
            media_type = rel.get("type", rel.get("media_type", "auto"))
        else:
            media_path = rel or ""
            media_type = "auto"
        # type 未写时按扩展名推断——字符串简写直接写
        # .mp4 路径若被当图片送 PIL 会误报"损坏"，推断后再分流
        if media_type in ("auto", None):
            media_type = classify_media_path(media_path)
        if not media_path:
            continue
        p = media_path if os.path.isabs(media_path) else os.path.join(out_dir, media_path)
        if not os.path.exists(p):
            missing_imgs.append((sid, media_path))
            continue
        # 视频用 ffmpeg 解码探测（只查存在性不够：
        # 截断/损坏的 mp4 要到渲染时才炸，白烧一整轮渲染时间）
        if media_type == "video":
            if _ffmpeg_probe:
                ok, reason, vdur = _probe_video_ok(p)
                if not ok:
                    corrupt_imgs.append((sid, media_path,
                                         f"视频解码失败: {reason}"))
                elif vdur is not None:
                    seg_dur = (seg_durs or {}).get(sid, 0)
                    if seg_dur > 0 and vdur > seg_dur + 0.05:
                        print(f"[warn] 视频 {vdur:.1f}s 超过段落 "
                              f"'{sid}' 时长 {seg_dur:.1f}s，渲染只显示"
                              f"前 {seg_dur:.1f}s（尾部内容会被截断）。"
                              f"请剪短视频或换到更长的段落。",
                              file=sys.stderr)
            continue
        # svg 是文本格式，PIL 打不开，存在性校验已足够
        if os.path.splitext(p.lower())[1] == ".svg":
            continue
        if _pil_available:
            try:
                with _PILImage.open(p) as im:
                    im.verify()
            except Exception as e:
                corrupt_imgs.append((sid, media_path, str(e)))
    if not _pil_available and images:
        print(f"[warn] Pillow 未安装，跳过配图完整性（损坏/截断）校验，"
              f"仅做了文件存在性校验。`pip install Pillow` 后可启用完整"
              f"校验，见 SKILL.md 环境准备", file=sys.stderr)
    return missing_imgs, corrupt_imgs


def main():
    parser = argparse.ArgumentParser(
        description="Generate Hyperframes composition from timing manifest"
    )
    parser.add_argument("-m", "--manifest", required=True,
                        help="Path to timing_manifest.json")
    parser.add_argument("-o", "--output", required=True,
                        help="Output HTML file path")
    parser.add_argument("--audio", default=None,
                        help="Audio src path in HTML (default: auto-detect from manifest)")
    parser.add_argument("--images", default=None,
                        help="Path to images.json (maps segment ID -> image path relative to HTML)")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--aspect", default="landscape",
                        choices=["landscape", "vertical", "portrait", "both"],
                        help="画幅比例 (landscape=1920x1080 横屏, "
                             "vertical=1080x1920 竖屏, portrait=1080x1440 "
                             "紧凑竖屏（3:4，复用竖屏布局、上下留白收窄、"
                             "图片槽 4:3），both=一次性生成两个文件，"
                             "自动覆盖 --width/--height；both 模式下竖屏版文件名"
                             "在 -o 指定的文件名基础上插入 .vertical 后缀)")
    parser.add_argument("--theme", default="cream",
                        choices=list_theme_names(),
                        help="主题配色 (背景/网格/文字/body 背景)，默认 cream（米白色科技风："
                             "暖米白背景 + 冷蓝灰网格线 + 石墨黑文字）。可选主题见 "
                             "config/theme_registry.json；如何按内容基调选主题见 SKILL.md「主题选择」")
    parser.add_argument("--sub-mode", default=None,
                        choices=["verse", "bar"],
                        help="字幕/内容呈现模式，默认按画幅取：横屏 bar（经典"
                             "形式：底部字幕条 + 正文要点卡片 + 说话人标签）、"
                             "竖屏家族 verse（歌词式句子流：当前句高亮、已播句"
                             "淡出、随播报滚动）。竖屏家族只有 verse（bar 放不"
                             "下会报错）；横屏显式传 verse = bar 布局框架 + "
                             "正文卡位置换滚动句子流窗口")
    parser.add_argument("--fps", type=int, default=24,
                        help="输出帧率（写入 HTML 的 data-fps；渲染时可用 --fps 覆盖，"
                             "默认 24，官方支持 24/30/60）")
    parser.add_argument("--gsap-src", default=None,
                        help="GSAP script URL or local path. Default: auto-cache locally "
                             "(downloads once to ~/.cache/content-to-video/vendor/ and copies "
                             "into the output dir's vendor/, falling back to the jsdelivr "
                             "CDN only if that download fails). Pass an explicit value "
                             "(URL or relative path) to override.")
    args = parser.parse_args()

    # help 自述官方支持 24/30/60，别的值渲染端也不认——fail-fast，
    # 而不是静默写进 data-fps 等渲染时才炸
    if args.fps not in (24, 30, 60):
        parser.error(f"--fps 仅支持 24/30/60（官方支持值），收到: {args.fps}")

    # 竖屏家族只有 verse（generate_html 函数层会再拦一道，这里是 CLI
    # 侧的人话报错：argparse.error 带用法说明、退出码 2）。both 也不行
    # ——both 会产出竖屏版，竖屏版没有 bar 可用。
    if (args.aspect != "landscape" and args.sub_mode == "bar"):
        parser.error(
            f"--aspect {args.aspect} 不支持 --sub-mode bar："
            "竖屏家族高度被大标题+图片占满，底部字幕条 + 正文卡放不下，"
            "仅支持 verse（歌词式句子流）。如需 bar 形态请用 "
            "--aspect landscape。")

    try:
        manifest = load_timing_manifest(args.manifest)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    # Load images mapping
    images = {}
    if args.images:
        if os.path.exists(args.images):
            try:
                with open(args.images, 'r', encoding='utf-8') as f:
                    images = validate_images_json(json.load(f))
            except ValueError as e:
                print(f"[error] {e}", file=sys.stderr)
                sys.exit(1)
            # 引用完整性校验（fail-fast）：images.json 声明的图片若磁盘上
            # 不存在，渲染会静默产出空白裂图——典型场景是手动改了 images.json
            # 或替换图片改了扩展名，却忘了重跑本脚本重新生成 HTML。在生成
            # HTML 前就报错，避免把坏图渲染进成片。
            out_dir = os.path.dirname(os.path.abspath(args.output)) or "."
            # 段落时长映射——视频配图比段落长时渲染只显示前段
            # （尾部被截断），要在这里就给出提示而不是等成片后才发现
            _seg_durs = {seg.get("id", ""): _segment_duration(seg)
                         for seg in manifest.get("segments", [])}
            missing_imgs, corrupt_imgs = validate_images_files(
                images, out_dir, _seg_durs)
            if missing_imgs or corrupt_imgs:
                for sid, rel in missing_imgs:
                    print(f"[error] 配图引用缺失: segment '{sid}' -> "
                          f"'{rel}' 在 {out_dir} 下不存在。", file=sys.stderr)
                for sid, rel, reason in corrupt_imgs:
                    print(f"[error] 配图文件已损坏/无法解码: segment '{sid}' -> "
                          f"'{rel}'（{reason}）。", file=sys.stderr)
                if missing_imgs:
                    print(f"[error] 请检查 images.json 与实际图片文件是否一致"
                          f"（常见原因：改了图片扩展名/替换图片后未重跑 "
                          f"gen_hyperframes.py 重新生成 HTML）。", file=sys.stderr)
                if corrupt_imgs:
                    print(f"[error] 请重新下载/生成对应图片后再重跑本脚本"
                          f"（常见原因：下载中途网络中断、磁盘写满导致文件"
                          f"截断）。", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"[warn] --images 文件不存在: {args.images}，本次渲染将不带配图"
                  f"（纯文字版兜底）。请确认路径是否正确（示例："
                  f"--images hf-project/images.json）。", file=sys.stderr)

    # 配图覆盖率提示（只提示不阻断）：内容段落（news/seg）没有配图映射时，
    # agent 容易把"漏配/搜图无结果"误当"按需配图不需要图"——run.py 一键
    # 编排有缺图拦截，分步执行时这行 warn 是唯一防线（竖屏按第 5 步要求
    # 内容段落全覆盖配图）
    _uncovered = _uncovered_content_sids(manifest, images)
    if _uncovered:
        print(f"[warn] {len(_uncovered)} 个内容段落没有配图映射: "
              f"{', '.join(_uncovered)}——若是搜图无结果或漏配，请按第 4 步"
              f"转 ImageGen 生图/方式 C 图表补图后重跑；仅当段落内容性质"
              f"确实不需要图时才保留无图。", file=sys.stderr)

    # Resolve GSAP src: explicit CLI value wins; otherwise try local caching
    # (first run downloads once to ~/.cache/content-to-video/vendor/, later runs
    # and other output dirs just copy from that cache — no repeated network
    # round-trips), falling back to the CDN URL only if caching fails.
    if args.gsap_src:
        gsap_src = args.gsap_src
    else:
        out_dir_for_gsap = os.path.dirname(os.path.abspath(args.output)) or "."
        gsap_src = ensure_local_gsap(out_dir_for_gsap) or GSAP_CDN_URL

    def _render_one(aspect, width, height, output_path):
        """渲染单个画幅版本。--aspect both 会对 landscape/vertical 各调用一次。"""
        import shutil  # 函数内局部导入（音频拷贝分支也有 import shutil，统一局部作用域）
        w, h = width, height
        out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
        if aspect in ("vertical", "portrait"):
            # 竖屏家族固定画幅（vertical=1080x1920 / portrait=1080x1440）；
            # 用户显式传了非默认 --width/--height 会被静默忽略——打警告
            # 说明，而不是无声吞掉
            _vh = 1440 if aspect == "portrait" else 1920
            if (args.width, args.height) not in ((1920, 1080), (1080, 1920),
                                                 (1080, 1440)):
                print(f"[warn] --aspect {aspect} 固定使用 1080x{_vh} 画幅，"
                      f"显式传入的 --width/--height（{args.width}x{args.height}）"
                      f"对竖屏版不生效。", file=sys.stderr)
            w, h = 1080, _vh
        # CSS layout uses fixed offsets (padding:0 100px, right:0, etc.) that
        # assume a 1920x1080 (16:9) frame. Non-16:9 ratios will misalign titles
        # and image cards. Warn instead of silently producing broken layouts.
        # 竖屏家族（9:16 与 3:4 portrait）为有意为之的画幅，无需警告。
        if aspect == "landscape" and w > 0 and h > 0:
            ratio = w / h
            if abs(ratio - (16 / 9)) > 0.01:
                print(f"[warn] Aspect ratio {w}x{h} (≈{ratio:.3f}) is not 16:9. "
                      f"CSS layout uses fixed offsets designed for 1920x1080; "
                      f"titles and image cards may misalign.", file=sys.stderr)

        if args.audio:
            audio_src = args.audio
            # --audio 路径是相对 HTML 输出目录解析的；相对当前工作目录找不到时
            # 打一个提示性警告（文件可能只相对 HTML 目录存在，此时是正常情况）。
            if not os.path.exists(audio_src):
                print(f"[warn] --audio 路径相对当前目录不存在: {audio_src}。音频 src "
                      f"是相对 HTML 输出目录解析的，若文件确实不在该位置，渲染出的视频"
                      f"会没有声音；不传 --audio 可自动从 manifest 推导。",
                      file=sys.stderr)
        else:
            audio_abs = manifest.get("combined_audio", "")
            out_dir = os.path.dirname(os.path.abspath(output_path))
            if audio_abs and os.path.exists(audio_abs):
                try:
                    rel = os.path.relpath(audio_abs, out_dir).replace("\\", "/")
                except ValueError:
                    # 跨盘符（音频在 D:、输出在 C:）时 relpath 直接抛
                    # ValueError：与"音频在项目根之外"同型，走下面的
                    # 拷贝进 <out_dir>/audio/ 分支
                    rel = ".."
                if rel.startswith(".."):
                    # Hyperframes 要求资源路径不能越出项目根（"../" 会被
                    # lint 判为 invalid_parent_traversal_in_asset_path 且
                    # 渲染无声）。音频在项目根之外时自动拷贝进
                    # <HTML 输出目录>/audio/，再以根相对路径引用。
                    audio_dir = os.path.join(out_dir, "audio")
                    os.makedirs(audio_dir, exist_ok=True)
                    dst = os.path.join(audio_dir, os.path.basename(audio_abs))
                    if (not os.path.exists(dst)
                            or os.path.getsize(dst) != os.path.getsize(audio_abs)):
                        import shutil
                        shutil.copy2(audio_abs, dst)
                    print(f"[audio] 音频在项目根之外，已自动拷贝到 {dst}",
                          file=sys.stderr)
                    audio_src = "audio/" + os.path.basename(dst)
                else:
                    audio_src = rel
            else:
                # manifest 没提供可用的 combined_audio（字段为空，或指向的
                # 文件已被移走/删除）：静默落到默认路径会让成片无声拖到
                # 最后人工看片才发现。与 --audio 分支的警告对称。
                print(f"[warn] manifest 未提供可用的 combined_audio"
                      f"（{audio_abs or '字段为空'}），HTML 将引用默认路径 "
                      f"audio/combined.wav——若该文件不存在，成片将没有声音。",
                      file=sys.stderr)
                audio_src = "audio/combined.wav"

        html = generate_html(manifest, audio_src, images=images,
                             width=w, height=h, gsap_src=gsap_src,
                             aspect=aspect, theme=args.theme, fps=args.fps,
                             sub_mode=args.sub_mode,
                             v_compact=(aspect == "portrait"))

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)

        # 复制预览脚本到 HTML 输出目录（index.html 引用同目录 preview.js）
        preview_js = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "preview.js")
        if os.path.isfile(preview_js):
            shutil.copy2(preview_js, os.path.join(out_dir, "preview.js"))
        else:
            print("[warn] 未找到 scripts/preview.js，浏览器预览不可用"
                  "（渲染不受影响）", file=sys.stderr)

        seg_count = len(manifest.get("segments", []))
        print(f"[OK] {output_path} ({len(html)} bytes)")
        print(f"     Duration: {manifest['total_duration']}s")
        print(f"     Segments: {seg_count if seg_count else 'auto-grouped'}")
        print(f"     Sentences: {len(manifest['sentences'])}")
        print(f"     Aspect: {aspect} ({w}x{h})")
        print(f"     Theme: {args.theme}")
        print(f"     FPS: {args.fps}")
        print(f"     Images: {len(images)}")
        print(f"     Audio src: {audio_src}")
        print(f"     GSAP src: {gsap_src}")

    if args.aspect == "both":
        root, ext = os.path.splitext(args.output)
        vertical_path = f"{root}.vertical{ext or '.html'}"
        _render_one("landscape", args.width, args.height, args.output)
        print()
        _render_one("vertical", 1080, 1920, vertical_path)
    else:
        _render_one(args.aspect, args.width, args.height, args.output)


if __name__ == "__main__":
    main()
