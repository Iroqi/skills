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
                    darken, relative_luminance, mix, expand_hex)
from _template import load_template, get_mode_preset  # noqa: E402
from _assets import ensure_local_gsap, GSAP_CDN_URL  # noqa: E402
from _contracts import (load_timing_manifest, validate_images_json,  # noqa: E402
                        classify_media_path, is_content_sid)
from _script_utils import (split_subtitle_cues, setup_stdio,  # noqa: E402
                           subtitle_params_for)
from _ffmpeg import get_ffmpeg, parse_duration  # noqa: E402


DEFAULT_ACCENT = get_default_accent()


def _file_identical(path_a, path_b):
    """两文件内容是否一致（大小不同直接 False，否则按 1MB 分块哈希比较）。

    音频自动拷贝的去重判定：只比大小会把"同大小不同内容"的旧拷贝误当
    最新音频复用（换稿后 combined.wav 同名同大小是可能的）。
    """
    if not os.path.exists(path_b):
        return False
    if os.path.getsize(path_a) != os.path.getsize(path_b):
        return False
    import hashlib

    def _digest(p):
        h = hashlib.md5()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    try:
        return _digest(path_a) == _digest(path_b)
    except OSError:
        return False


# 第一分支 `&#\d+;` 整体吃掉数字字符引用（esc() 把 `'` 转成 `&#39;`），
# 它必须排在数字分支之前：只靠 `(?<![&#])` 挡不住实体里的第二位数字
# （`&#39;` 的 `9` 前面是 `3`），会把实体撕成 `&#3<span…>9</span>;`，
# 浏览器把 `&#3` 当无分号数字实体解析成控制字符，画面上出现乱码。
_NUM_SPAN_RE = re.compile(r"&#\d+;|(?<![&#])\d[\d,，]*(?:\.\d+)?")


def wrap_numbers(escaped_html, accent):
    """把正文行里已 esc() 的数字串包上 accent 着色 span（仅显示层强调，
    与 scripts/check_facts.py 的抽取正则同源）。

    输入是 esc() 之后的文本，所以数字字符引用（`&#39;`）会混在正常数字里，
    必须整条跳过而不参与着色——命中第一分支时原样返回，剥掉 span 后与输入
    严格相等。`(?<![&#])` 是数字分支的补充守卫，挡住紧跟在 &/# 后面的数字
    （"C#5" 这类极为罕见，漏包一个数字无实害）。
    """
    return _NUM_SPAN_RE.sub(
        lambda m: m.group() if m.group().startswith("&#")
        else f'<span class="num-accent" style="color:{accent}">{m.group()}</span>',
        escaped_html)


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


# 说话人字幕配色（叠在字幕栏 sub_bar 上）与 agenda 序号圆数字色：
# 数据定义在 config/template.json 顶级 speaker 块（JSON 定义、py 生产），
# 这里只负责装载成模块常量——selftest [21] 引用这些名字做 WCAG 对比度
# 断言（单一数据源，改 JSON 测试自动跟进）。运行时按字幕栏明暗二选一：
# 浅字幕栏（cream）配深色系、深字幕栏（dark）配浅色系。
_spk_cfg = load_template()["speaker"]
SPK_COLORS_ON_LIGHT = _spk_cfg["colorsOnLight"]
SPK_COLORS_ON_DARK = _spk_cfg["colorsOnDark"]
AGENDA_NUM_TEXT_COLOR = _spk_cfg["agendaNumText"]


def generate_html(manifest, audio_src, images=None,
                  width=1920, height=1080,
                  gsap_src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js",
                  aspect="landscape", theme="cream", fps=24):
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
            默认 CDN 仅是兜底——本文件 main() 实际调用前会用 ensure_local_gsap()
            把脚本拷到 output_dir/vendor/，并把本地路径注入进来；离线/无网
            环境下 ensure_local_gsap() 返回 None，main() 才会回退到这个 CDN 默认。
        aspect: 画幅比例（只有两种）："landscape" 横屏 16:9（1920x1080）/
            "portrait" 竖屏 3:4（1080x1440，紧凑留白 + 图片槽 4:3）。
            portrait 复用竖屏布局家族（内部 data-aspect 写 "vertical"，
            全部竖屏 CSS 规则照常命中）。
        theme: 主题配色（可选值以 config/theme_registry.json 为唯一权威来源，
            当前 cream/dark），影响背景渐变、
            网格、正文文字与 body 背景色，不影响每段 accent 彩色。
        fps: 输出帧率提示（写入 data-fps，渲染命令可用 --fps 覆盖；
            默认 24 比 30 少抓 20% 帧、渲染更快）。

        字幕/内容呈现模式不作为参数暴露——固定按画幅绑定：横屏 bar
        （经典底部字幕条 + 正文要点卡片，图文讲解的常见形态）、竖屏
        verse（歌词式句子流，短视频常见形态）。
    """
    total_dur = manifest["total_duration"]
    sentences = manifest["sentences"]
    segments = manifest.get("segments")
    images = images or {}
    # <script src> 属性上下文转义：CLI 传的本地路径可能含 & 或引号，裸插
    # 会破坏 head 结构（媒体路径都走了 quote/转义；这里不能 URL 编码——
    # CDN 地址的 :/? 会被 quote 破坏）
    gsap_src_attr = gsap_src.replace("&", "&amp;").replace('"', "&quot;")

    # portrait 归一化：竖屏 3:4 复用 vertical 布局家族，data-aspect 写
    # "vertical"（竖屏 CSS 选择器全部照常命中）。归一化放函数本体内而
    # 不是 CLI 层，库调用方传 aspect="portrait" 也能得到正确产物。
    if aspect == "portrait":
        aspect = "vertical"

    # 字幕/内容呈现模式固定按画幅绑定（在 portrait 归一化之后解析，不
    # 作为用户选项暴露）：横屏 bar（经典图文讲解形态）、竖屏 verse
    # （短视频歌词流形态）。
    sub_mode = "bar" if aspect == "landscape" else "verse"

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
    # 背景（sub_bar_rgb）随主题变——cream 是近白底、dark 是
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

    # 从模板提取 CSS 变量值
    _bl = tpl_layout["badge"]
    css_badge_top = f'{_bl["top"]}px'
    css_badge_left = f'{_bl["left"]}px'
    css_badge_size = _bl["size"]
    css_badge_font = f'{_bl["fontSize"]}px'

    _tl = tpl_layout["title"]
    css_title_pad = _tl["padding"]

    _bl2 = tpl_layout["body"]
    css_body_font = f'{_bl2["fontSize"]}px'
    css_body_max = _bl2.get("maxWidth")
    # 横屏配图段正文卡的独立锚点（body 卡不进 title-wrap 文档流）：
    # 取值由标题组最坏情况推导——topNews 80 + 2 行标题高
    # （fontSizeNewsImage 72 × titleLineHeight 1.25 = 180）+ tagline 槽
    # （marginTop 16 + fontSize 44 × lineHeight 1.4 ≈ 62）+ 30px 间距
    # = 368，位置不随标题折几行浮动；tagline 是标题组的固定成员，
    # 必须计入锚点推导，否则有 tagline 的段落正文卡会压上去（tagline
    # 在 title-wrap 流内、随标题下移）。竖屏 body 卡是 flex 流内元素，
    # 不消费该值。
    css_body_top = _bl2.get("top")
    # 横屏配图段左列内容盒（bar 正文卡 / verse 句子流窗口）的三个可调
    # 参数（读模板 landscape.body，缺省回退旧行为）：
    #   leftMargin — 卡片左缘距画面左边（缺省 None=仍与标题文字对齐）
    #   sideGap    — 卡片右缘与图片左缘的间距（默认 50）
    #   titleGap   — 卡片顶与"标题组（标题+tagline）底部"的间距；给出后
    #                top 不再用固定锚点 body.top，而是按标题实际估算行数
    #                动态推导（单行标题不再留两行标题的预留空隙）
    css_body_left_margin = _bl2.get("leftMargin")
    css_body_side_gap = _bl2.get("sideGap", 50)
    css_body_title_gap = _bl2.get("titleGap")

    _sl = tpl_layout["subtitle"]
    # 字号两模式共用（verse 句子流行 / bar 字幕条，横竖屏各自模板值：
    # 横屏 46 / 竖屏 40）；height/padding/maxWidth 仅 bar 模式的
    # sub-bar 读取（verse 无字幕条）
    css_sub_font = f'{_sl["fontSize"]}px'
    css_sub_height = f'{_sl.get("height", 176)}px'
    css_sub_pad = _sl.get("padding", "0 50px")
    css_sub_max = _sl.get("maxWidth", 1600)

    # ── 竖屏紧凑参数（--aspect portrait，3:4 即 1080x1440）──
    # 上下留白收窄 + 图片槽比例读模板 image.aspect（当前 1.4142/1，√2:1
    # 与横屏槽统一——1.5.61 由 4:3 改入；当年收窄图片槽的动机：1:1 方图
    # 加 300px 句子流在 1440 高度里放不下）。
    # 竖屏只有 verse（bar 在上方已被拦下），无模式分支。
    # 全部参数读模板 vertical 块（segCard.padding / image.aspect /
    # image.marginTop / verse.windowHeight / verse.clipPad），改排版只动
    # template.json。
    _v_pad = tpl_layout.get("segCard", {}).get("padding", "100px 50px 80px")
    _v_img_ar = tpl_layout["image"].get("aspect", "4/3")
    _v_img_mt = tpl_layout["image"].get("marginTop", 48)
    _vv = tpl_layout.get("verse", {})
    _v_verse_h = _vv.get("windowHeight", 300)
    _v_verse_clip = _vv.get("clipPad", 60)
    _v_verse_line = _vv.get("linePad", 5)
    # 竖屏图片槽圆角（横屏走 typography.imageBorderRadius=24，竖屏槽更小）
    _v_img_radius = tpl_layout["image"].get("borderRadius", 16)
    # 竖屏图片槽左右外扩边距（px）：槽比正文轴线（50px）更贴边，左右各外扩
    # marginSide（0 = 与内容盒同宽，即收编前的行为）
    _v_img_ms = tpl_layout["image"].get("marginSide", 0)

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
        _sub_css = f"""/* ── verse 句子流（竖屏专用，"正文卡+底部字幕"的融合替代）──
   竖屏 = 大标题/图片槽 + 钉底句子流：窗口高由模板 vertical.verse.
   windowHeight 控制，DOM 渲染在 seg-card 尾部、由 flex margin-top:auto
   钉在内容区底部（见下方 vertical 覆盖块）；开场/收尾段 agenda/chips
   是结构化目录不是句子流，正文保留显示、verse 照常钉底。
   clip 上下各留 vertical.verse.clipPad 内边距且滚动锚点同步同值：
   首句/尾句完整落在 mask 渐隐区（上下各 14%）之外，不被边缘虚化。 */
.verse{{position:relative;width:100%;height:{_v_verse_h}px;overflow:hidden;
  -webkit-mask-image:linear-gradient(transparent,#000 14%,#000 86%,transparent);
  mask-image:linear-gradient(transparent,#000 14%,#000 86%,transparent)}}
/* clip 必须定位：verse-line.offsetTop 需要相对 clip 而不是绝对定位的
   seg-card（否则首行 offsetTop 巨大，滚动公式恒被钳到段落尾部——
   第一句永远在窗口外） */
.verse-clip{{position:relative;padding:{_v_verse_clip}px 0;transition:transform .45s cubic-bezier(.4,0,.2,1)}}
.verse-line{{font-size:{css_sub_font};line-height:1.5;font-weight:600;color:{theme_colors["text_color"]};opacity:.62;transition:opacity .3s,color .3s;padding:{_v_verse_line}px 0;text-align:left}}
.verse-line.active{{opacity:1!important}}
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
    # 条目内序号圆与标题文字的间距（.agenda-list 的条目间 gap 用 agenda.gap）
    css_agenda_item_gap = f'{_al.get("itemGap", 14)}px'

    _cl = tpl_layout["chip"]
    css_chip_font = f'{_cl["fontSize"]}px'
    css_chip_margin = f'{_cl.get("marginTop", 96)}px'
    # chip 内边距读模板 chip.padding（横竖屏各自的值都生效，不硬编码）
    css_chip_pad = _cl.get("padding", "14px 28px")
    css_chip_gap = f'{_cl.get("gap", 16)}px'

    _tgl = tpl_layout["tagline"]
    css_tagline_font = f'{_tgl["fontSize"]}px'
    # tagline 显式行高：body 卡锚点（css_body_top）按"标题带 + tagline 槽"
    # 推导，行高必须是确定值才能算出确定的锚点——依赖浏览器 normal
    # 行高（约 1.14-1.2，随字体实现浮动）会让几何不可推导。
    css_tagline_lh = _tgl.get("lineHeight", 1.4)

    _il = tpl_layout["image"]
    css_img_width = _il["width"]
    css_img_height = _il["height"]
    # 横屏图片槽左缘（右缘锚定 + 定宽）：左列内容盒（body 卡/verse 窗口）
    # 的右边界与宽度推导都以此为准，保持 50px 间距。
    css_img_left_edge = width - _il.get("right", 100) - css_img_width
    if _il.get("centerHorizontal"):
        css_img_top = f'{_il["top"]}px' if isinstance(_il["top"], int) else _il["top"]
        css_img_pos = "left:50%;transform:translateX(-50%)"
    else:
        # 横屏 top 是垂直中线（translateY(-50%)），取值由 √2:1 比例与
        # 整屏垂直居中推导：槽 910×644（右缘距屏幕 right 25px、
        # 910/√2 ≈ 644）——4:3 生图 contain 后左右留边恰约 25px；槽
        # 左缘 985，与正文卡（左 50、宽 910）之间留 sideGap 25px。
        # 垂直居中相对整个画面：top = 1080/2 = 540。注意取舍：两行
        # 标题（底 260）会与图上缘 218 轻微交叠——单行标题无碍，长
        # 标题应在写稿阶段控制（或调回低 top）。改槽宽/右距时同步
        # 重推高（=宽/√2）。
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
    # 正文外观开关（template body.card）：true（默认）= 经典卡片（底色
    # 圆角盒 + 左边框 + 毛玻璃）；false = 裸文字。2026-09-06 实测过默认
    # 裸文字：直接放在网格背景上太丑，用户否决——卡片保留为默认值，开
    # 关仅留给特殊需要。PPT 感的治理改走字号层级：body.fontSize 38 <
    # subtitle.fontSize 46，口播字幕是画面主导文字、正文是辅助信息层。
    if _bl2.get("card", True):
        css_body_chrome = (f'padding:{css_body_pad};'
                           f'background:{theme_colors["body_bg"]};'
                           f'border-radius:{css_body_radius}px;'
                           f'border-left:{css_body_border_left}px solid '
                           f'{theme_colors["soft_border"]};'
                           f'backdrop-filter:blur(4px)')
    else:
        css_body_chrome = 'padding:0'
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

    # 叙事模式预设（1.5.69 起 template 定义、gen 生产）：config/
    # template.json 的 modes 块是 flow/章节全部版式差异的唯一定义源
    # （编号 badge/开场预告形态/收尾回顾默认/段落转场/竖屏开场封面图
    # 义务），manifest 顶层 "flow": true（pipeline 由 segments_source.json
    # 透传；手写 manifest 也可以直接设）只负责选 flow 预设。下面所有
    # 分支只读预设键——模板负责定义，本函数只负责生产。
    mode_preset = get_mode_preset(manifest)

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
    # 或新的 manifest 字段。条目软上限 = 模板 agenda.maxItems（默认 7）：
    # 超出时取前 N 条显示并打 warn——写稿阶段应把条目控制在 7 条以内，
    # 确有必要突破时调大模板 agenda.maxItems；条数多时仍靠动态字号收缩
    # 适配（见 agenda 分支），chips 靠 flex 换行容纳。
    content_agenda = [
        {"title": seg.get("title", ""), "accent": expand_hex(seg.get("accent", DEFAULT_ACCENT))}
        for seg in segments
        if is_content_sid(seg.get("id"))
    ]
    _ag_cap = tpl_layout.get("agenda", {}).get("maxItems", 7)
    if len(content_agenda) > _ag_cap:
        print(f"[warn] 目录条目共 {len(content_agenda)} 条，超过上限 {_ag_cap}："
              f"开场目录/结尾回顾只显示前 {_ag_cap} 条。写稿时应把内容段控制在 "
              f"{_ag_cap} 条以内；确有必要突破时调大 config/template.json 的 "
              f"agenda.maxItems。", file=sys.stderr)
        content_agenda = content_agenda[:_ag_cap]

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
        is_news = is_content_sid(sid)
        # 开场/收尾页也支持配图（images.json 写 "opening"/"closing" 键）：
        # 竖屏开场页只有标题+预告+句子流时画面偏空（用户反馈"太难看"），
        # 配图后竖屏为「标题在上 + 图居中 + 句子流钉底」，与内容段同一
        # 版式语言；竖屏有图时 agenda/closing_body 让位于图（_verse_kills_body
        # 对 opening/closing 一并生效——垂直预算装不下图与目录/回顾并存）。
        # 横屏走既有配图段版式（标题左移、图右置）。无图时行为与旧版完全
        # 一致（开场预告/收尾回顾照常生成）。
        has_image = sid in images

        # 动画参数快捷引用（从模板加载，替代硬编码数值）
        a_ = tpl_anim

        # Badge（章节编号圆）。预设 numbered=false（flow）时不显示——编号
        # 是"新闻条目"的板块语言，叙事/讲解内容的段落之间应该靠承接句
        # 自然流动。
        badge = ""
        if is_news and mode_preset["numbered"]:
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
            # 标题右缘缩进读模板 title.rightInset（横屏 110/竖屏 60）
            _tri = _tl.get("rightInset", 110)
            left_px = (
                _tl.get("leftWithBadge", 205) if (is_news and badge)
                # 非 badge 配图段与正文卡同轴线（body.leftMargin），同一套页边距
                else _bl2.get("leftMargin", 50)
            )
            title_left = f"{left_px}px"
            title_right = "auto"
            if _il.get("centerHorizontal"):
                # 竖屏配图在标题下方居中，标题宽度不受图片约束，尽量占满
                title_width = f"{max(width - left_px - _tri, 200)}px"
            else:
                # 横屏：标题横跨在正文与图片上方，右边界不必跟图片左缘对齐，
                # 给足宽度；正文宽度由模板 body.maxWidth 控制（与图片留清晰边界）。
                title_width = f"{max(width - left_px - _tri, 200)}px"
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
            # Python 里的像素计算逻辑），必须先跑一遍渲染快照
            # （npx hyperframes check --snapshots）逐帧目检，
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
            _title_font_px = _tl.get("fontSizeNewsImage",
                                     int(_tl["fontSizeNews"] * 0.86))
        else:
            _title_font_px = (_tl["fontSizeNews"] if is_news
                              else _tl["fontSizeOther"])
        title_size = f"{_title_font_px}px"
        # 标题行数估算（供正文卡动态 top 推导用）：字宽按 CJK/全角 1em、
        # 其余 0.62em 估。图框改 √2:1（910×644）后整屏垂直居中，上缘
        # = 1080/2 − 644/2 = 218，位于两行标题底（260）之上约 42px——
        # 与标题有轻微交叠（references/rendering.md 已记为已知取舍），
        # 早年"第二行伸入图片区"告警即因此前提移除：交叠是刻意接受的
        # 结果，而非已消除的风险。
        if aspect == "landscape" and has_image:
            _tw_px = max(width - left_px - 110, 200)
            _est_w = int(sum(
                1.0 if (ord(c) >= 0x2E80 or 0xFF00 <= ord(c) <= 0xFFEF)
                else 0.62 for c in seg["title"]) * _title_font_px)
        # 标题 top 读模板 title.topNews/topOther（topNews 是裸数字按 px，
        # topOther 是带单位字符串如 "30%"，按类型分别处理）。横屏配图段
        # 一律顶部锚定（topNews）：图片框上边缘固定在"topNews + 1 行标题
        # 高 + 30px"的标题带之下（推导见上方 image top/height 注释），
        # 单行标题完整避开图片；两行标题第二行伸入图片区时生成期告警。
        # flow 模式配图段不做左列内容组垂直居中——46% 居中会让标题落进
        # 图片纵向区间、长标题直接压图，与新闻段共用 topNews 顶部锚定。
        _top_raw = (_tl["topNews"]
                    if (is_news or (has_image and aspect == "landscape"))
                    else _tl["topOther"])
        title_top = f"{_top_raw}px" if isinstance(_top_raw, (int, float)) else str(_top_raw)

        # Tagline
        tagline_html = ""
        if seg.get("tagline"):
            # 深色主题向白提亮（无差别 _darken 在 dark 下
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

        # 横屏配图段左列内容盒（bar 正文卡 / verse 句子流窗口）统一几何：
        # 左缘 = body.leftMargin（缺省仍对齐标题文字 left_px）；宽度使右缘
        # 与图片左缘保持 body.sideGap；顶部 = "标题组（标题+tagline）底部"
        # + body.titleGap——标题组底部按标题估算行数动态推导，单行标题
        # 不再预留两行标题的空隙；titleGap 未配置时回退固定锚点 body.top
        # （旧模板兼容，位置不随标题折行浮动）。
        _box_left, _box_top, _box_w = left_px, css_body_top, None
        if aspect == "landscape" and has_image:
            _lm = (css_body_left_margin if css_body_left_margin is not None
                   else left_px)
            _box_left = _lm
            _box_w = max(300, min(css_body_max or 870,
                                  css_img_left_edge - _lm - css_body_side_gap))
            if css_body_title_gap is not None:
                _est_lines = 1 if _est_w <= _tw_px else 2
                _tgroup_bottom = (int(_tl["topNews"])
                                  + _est_lines * _title_font_px * css_title_lh
                                  + css_tagline_mt
                                  + _tgl["fontSize"] * css_tagline_lh)
                _box_top = int(_tgroup_bottom + css_body_title_gap)

        # Body text — each line gets its own ID for stagger animation
        body_html = ""
        body_line_count = 0
        agenda_count = 0
        recap_count = 0
        numpop_ids = set()
        # 开场预告与收尾回顾共用同一 chips 视觉语言（渲染分支合一，条件各自判断）：
        # - 开场：预设 openingPreview == "chips"（flow）时是轻量 chips 一排；
        #   竖排列表+编号圆是章节模式（"agenda"）的语言。manifest 写
        #   "agenda": false 可显式关闭。
        # - 收尾：默认值读预设 closingRecap（flow 为 false：叙事型收尾靠
        #   closing 稿件本身，chips 的清单语言与叙事气质不符）；manifest 写
        #   "recap": true 可显式打开。
        _wants_chips = (
            (sid == "opening" and content_agenda
             and mode_preset["openingPreview"] == "chips"
             and seg.get("agenda", True))
            or (sid == "closing" and content_agenda
                and seg.get("recap", mode_preset["closingRecap"])))
        if seg.get("body"):
            # 过滤空行：body 尾部的换行符（手写 manifest 常见）不该渲染出
            # 空的 body-line div 和对应的 GSAP tween。
            body_lines = [l for l in seg["body"].split("\n") if l.strip()]
            body_line_count = len(body_lines)
            _body_parts = []
            for j, line in enumerate(body_lines):
                # num-accent 用对比度安全色：正文裸排在页面底上（card:false
                # 后尤其如此），原色 accent（如 #ef5350 红）在浅色主题米白
                # 底上只有 ~2.79:1，不过 WCAG 大字 3:1——与 tagline 同一
                # 处理：浅底用 darken 加深、深底向白提亮。
                _num_color = (mix(ac, "#ffffff", 0.62) if _dark_theme
                              else darken(ac))
                _wrapped = wrap_numbers(esc(line), _num_color)
                if "num-accent" in _wrapped:
                    numpop_ids.add(f"{sid}-{j}")
                _body_parts.append(
                    f'<div class="body-line" id="bodyline-{sid}-{j}">{_wrapped}</div>'
                )
            body_items = "".join(_body_parts)
            # 配图时正文框收窄（上限来自模板 body.maxWidth），与右侧图片
            # 保持明显间距；标题仍可占满标题区宽度。
            if aspect == "landscape" and has_image:
                # 横屏配图段：正文卡是独立绝对定位盒，不进 title-wrap
                # 文档流——几何（left/top/width）由上方 _box_* 统一推导
                # （body.leftMargin / titleGap / sideGap，见彼处注释）。
                # 竖屏配图段保持 flex 流内元素（vertical CSS 全量覆盖
                # .body-text），走 max-width 分支。
                body_style = (f' style="position:absolute;left:{_box_left}px;'
                              f'top:{_box_top}px;width:{_box_w}px;'
                              f'margin-top:0"')
            elif has_image and css_body_max:
                body_style = f' style="max-width:{css_body_max}px"'
            else:
                body_style = ""
            body_html = f'<div class="body-text" id="body-{sid}"{body_style}>{body_items}</div>'
        elif _wants_chips:
            chips = [
                f'<div class="recap-chip" id="recapchip-{sid}-{k}" '
                f'style="border-color:{item["accent"]}">{esc(item["title"])}</div>'
                for k, item in enumerate(content_agenda)
            ]
            recap_count = len(chips)
            body_html = f'<div class="recap-chips" id="chiprow-{sid}">{"".join(chips)}</div>'
        elif sid == "opening" and content_agenda and seg.get("agenda", True):
            # 开场页原本只有标题+副标题、画面偏空——补一份"内容目录"目录，
            # 让观众提前知道接下来有哪几条内容。只在段落没有手写 body 时
            # 自动生成，不覆盖 manifest segments 里手动写的内容。
            shown = content_agenda
            n = len(shown)
            # 动态字号：条目越多字号越小，避免条数多时列表过长、底部挤进
            # 字幕栏（目录被挤到字幕位置、太靠左）。字号随条目数从
            # shrinkThreshold 到 shrinkMax 线性缩小到 minFont；序号圆、行间距等比缩放。
            _ag_base = _al.get("fontSize", 46)
            _ag_min = _al.get("minFont", 32)
            _ag_th = _al.get("shrinkThreshold", 6)
            # shrinkMax 是字号缩到 minFont 的条目数刻度；条数超过后由
            # minFont 兜底不再继续缩（32px 是可读下限，再小序号圆里的
            # 数字就糊了）
            _ag_max = _al.get("shrinkMax", 8)
            if n <= _ag_th:
                _ag_font = _ag_base
            else:
                _ratio = (n - _ag_th) / max(1, (_ag_max - _ag_th))
                _ag_font = max(_ag_min, round(_ag_base - (_ag_base - _ag_min) * _ratio))
            # 序号圆等比缩放：比例必须跟随模板 numSize/fontSize（横屏 60/46、
            # 竖屏 52/40 都 ≈1.30）——硬编码比例会让 numSize 成死字段
            _ag_num = max(36, round(_ag_font * (_al["numSize"] / _al["fontSize"])))
            _ag_gap = max(10, round(_ag_font * 0.5))  # 行间距等比缩放（≈0.5 行）
            _ag_indent = _al.get("indentLeft", 140)    # 左缩进：把目录从屏幕左缘往右挪
            _ag_mt = _al.get("marginTop", 36)          # 距标题上间距：往上挪给列表留空间
            # 条目不做高度限制，按内容自然撑开；序号圆顶部对齐保证行列整齐。
            _ag_lh = _al.get("lineHeight", 1.34)
            items = [
                # 目录序号圆跟随预设 numbered（编号是章节模式的语言），
                # 只留标题行；章节模式保持编号圆 + 标题。
                f'<div class="agenda-item" id="agendaitem-{sid}-{k}">'
                + (f'<span class="agenda-num" style="background:{item["accent"]};'
                   f'width:{_ag_num}px;height:{_ag_num}px;'
                   f'font-size:{round(_ag_num * 0.5)}px">{k + 1}</span>'
                   if mode_preset["numbered"] else "")
                + f'<span class="agenda-title" style="font-size:{_ag_font}px;'
                  f'line-height:{_ag_lh}">'
                  f'{esc(item["title"])}</span>'
                f'</div>'
                for k, item in enumerate(shown)
            ]
            agenda_count = len(items)
            body_html = (
                f'<div class="agenda-list" id="agendalist-{sid}" '
                f'style="margin-top:{_ag_mt}px;margin-left:{_ag_indent}px;'
                f'gap:{_ag_gap}px">{"".join(items)}</div>'
            )

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
        # 字幕/内容 DOM 按模式二选一（模式由画幅固定：横屏 bar、竖屏 verse）：
        # - verse：歌词式句子流——该段全部句子按序渲染成静态行（完整
        #   句子，CSS 自动换行），运行时由 cue 的 si 高亮当前句、已播句
        #   淡出、窗口随播报滚动。正文信息由句子流逐句呈现，被替代段落
        #   的 body 卡不再渲染（见 _verse_kills_body）。
        # - bar：经典底部字幕条（sub-bar），正文卡正常显示。
        # verse 替代 body 卡的段落：竖屏有图段（大图+句子流已满高，body
        # 卡放不下——原来用 CSS display:none 兜，改 DOM 层不渲染，GSAP
        # stagger 不再指向不存在的行）。竖屏无图段与开场/收尾保留
        # body/agenda：无图段只有标题太单薄，agenda 是结构化目录不能
        # 换成句子流。
        _verse_kills_body = sub_mode == "verse" and has_image
        if _verse_kills_body:
            body_html = ""
            body_line_count = 0
            agenda_count = 0
            recap_count = 0
        # body 卡规则：横屏配图段独立于 title-wrap（标题框与内容框
        # 各自锚定，互不约束——内容框位置不随标题折行浮动）。开场/收尾
        # 与无图段的 agenda/body 是居中版式的组成部分，保留流内布局。
        # verse DOM（竖屏）统一渲染在 seg-card 尾部，由竖屏 flex
        # margin-top:auto 钉底。
        _body_detached = (aspect == "landscape" and has_image
                          and bool(body_html))
        verse_html = ""
        sub_bar_html = ""
        if sub_mode == "verse":
            _vlines = [
                # data-i 兜底与 cue 侧 si 保持一致（缺 index 都落 -1）：
                # 两边兜底值不一致时，库调用传入无 index 句子会让 JS 高亮
                # 永久失灵或错行——宁可都不高亮，也不错误高亮
                f'<div class="verse-line" data-i="{_s2.get("index", -1)}">'
                f'{esc(_s2["text"])}</div>'
                for _k, _s2 in enumerate(seg["sentences"])
            ]
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
                f'<div class="verse-clip" data-accent="{ac}">'
                f'{"".join(_vlines)}</div></div>'
            )
        else:
            sub_bar_html = (
                '\n    <div class="sub-bar">\n'
                '      <div class="sub-speaker"></div>\n'
                '      <div class="sub-text"></div>\n'
                '    </div>\n'
            )
        seg_cards.append(
            f'  <div id="{sid}" class="clip seg-card{" has-image" if has_image else ""}" '
            f'data-start="{s:.2f}" data-duration="{d:.2f}" '
            f'data-track-index="1" style="opacity:0">\n'
            f'    <div class="seg-glow" id="glow-{sid}" '
            f'style="background:radial-gradient(circle at 50% 50%,{ac}15,transparent 60%)"></div>\n'
            f'    <div class="seg-accent-bar" id="bar-{sid}" style="background:{ac}"></div>\n'
            f'    {badge}\n'
            f'    <div class="seg-title-wrap" style="left:{title_left};'
            f'right:{title_right};width:{title_width};top:{title_top};'
            f'text-align:{title_align};{title_pad_inline}">\n'
            f'      <div class="seg-title" id="title-{sid}" '
            f'style="font-size:{title_size};text-shadow:0 0 40px {ac}40">'
            f'{esc(seg["title"])}</div>\n'
            f'      {tagline_html}\n'
            f'      {body_html if not _body_detached else ""}\n'
            f'    </div>'
            f'{image_html}\n'
            # 横屏配图段的 body 卡是独立绝对定位盒（不进 title-wrap），
            # 直接挂 seg-card——挂在 title-wrap 里会以它为定位参照系
            # （title-wrap 自身 position:absolute，top:80），top 值会被
            # 二次偏移。
            f'    {body_html if _body_detached else ""}\n'
            f'    <div class="seg-progress" id="prog-{sid}" '
            f'style="background:{ac};width:0"></div>\n'
            f'    {verse_html}{sub_bar_html}\n'
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
            if mode_preset["transition"] == "crossfade":
                # 预设 transition="crossfade"（flow）：不做 accent 色 wipe
                # 扫场（板块切换的视觉语言），只用更长的 cross-fade——上一
                # 段的 fade-out 与这一段的 fade-in 在边界自然交叠，视觉上
                # "接着讲"而不是"翻到下一条"。
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
                # 方向按段落序号奇偶交替：连续同向擦除会产生机械感，
                # 左右交替打破节奏（#twipe 静态位 left:-100%，两个方向
                # 覆盖屏幕的行程对称）
                if i % 2 == 0:
                    gsap_lines.append(
                        f'tl.fromTo("#twipe",{{x:0}},'
                        f'{{x:"200%",duration:{a_wipe.get("duration", 0.4)},'
                        f'ease:"{a_wipe.get("ease", "power2.inOut")}"}},{_wipe_at:.2f})'
                    )
                else:
                    gsap_lines.append(
                        f'tl.fromTo("#twipe",{{x:"200%"}},'
                        f'{{x:0,duration:{a_wipe.get("duration", 0.4)},'
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
        # 入场动效预算随段长归一化：短段整体压缩入场节奏（下限 0.45
        # 避免快到看不清），长段维持原速。转场（wipe/fade）与进度条
        # 不参与——前者承担段间衔接语义，后者必须与音频严格同步
        _k = min(1.0, max(0.45, d / 4.0))
        # Accent side bar grows from top
        a_bar = a_.get("accentBar", {})
        gsap_lines.append(
            f'tl.from("#bar-{sid}",{{scaleY:0,transformOrigin:"top",'
            f'duration:{a_bar.get("duration", 0.5) * _k:.2f},'
            f'ease:"{a_bar.get("ease", "power2.out")}"}},{s:.2f})'
        )
        # Background glow pulse
        a_glow = a_.get("glowPulse", {})
        gsap_lines.append(
            f'tl.fromTo("#glow-{sid}",{{opacity:0}},{{opacity:1,'
            f'duration:{a_glow.get("duration", 0.8) * _k:.2f}}},{s:.2f})'
        )
        # glow 呼吸：淡入完成后极轻往复（只动 opacity，合成层友好），
        # 消除长段落中后段的"死屏"感；有限 repeat 保证时间轴长度确定
        a_gb = a_.get("glowBreath", {})
        _gb_min = float(a_gb.get("min", 0.85))
        if _gb_min < 1.0:
            _period = float(a_gb.get("period", 1.6))
            _g_fade = a_glow.get("duration", 0.8) * _k
            _n = int(min(10, max(0, (d - _g_fade) / (_period * 2))))
            if _n > 0:
                gsap_lines.append(
                    f'tl.to("#glow-{sid}",{{opacity:{_gb_min},'
                    f'duration:{_period:.2f},ease:"sine.inOut",yoyo:true,repeat:{_n}}},'
                    f'{s + _g_fade:.2f})'
                )
        # Title entrance
        a_title = a_.get("titleEntrance", {})
        gsap_lines.append(
            f'tl.from("#title-{sid}",{{scale:{a_title.get("from", 0.5)},'
            f'duration:{a_title.get("duration", 0.5) * _k:.2f},'
            f'ease:"{a_title.get("ease", "back.out(1.7)")}"}},{s:.2f})'
        )
        if badge:
            a_badge = a_.get("badgeEntrance", {})
            gsap_lines.append(
                f'tl.from("#badge-{sid}",{{scale:{a_badge.get("from", 0)},'
                f'duration:{a_badge.get("duration", 0.4) * _k:.2f},'
                f'ease:"{a_badge.get("ease", "back.out(2)")}"}},{s:.2f})'
            )
        if seg.get("tagline"):
            a_tag = a_.get("taglineEntrance", {})
            gsap_lines.append(
                f'tl.from("#tag-{sid}",{{opacity:0,y:{a_tag.get("y", 20)},'
                f'duration:{a_tag.get("duration", 0.5) * _k:.2f}}},'
                f'{s + a_tag.get("delay", 0.3) * _k:.2f})'
            )
        # Body line stagger — each line slides in with incremental delay
        if body_line_count > 0:
            a_body = a_.get("bodyLineStagger", {})
            for j in range(body_line_count):
                gsap_lines.append(
                    f'tl.from("#bodyline-{sid}-{j}",{{opacity:0,'
                    f'x:{a_body.get("x", -20)},'
                    f'duration:{a_body.get("duration", 0.4) * _k:.2f}}},'
                    f'{s + a_body.get("startDelay", 0.6) * _k + j * a_body.get("delay", 0.15) * _k:.2f})'
                )
                # 数字 pop：行内数字在行入场完成后轻微放大回落一次，
                # 强调数据类段落的核心信息；transform 不触发重排
                if f"{sid}-{j}" in numpop_ids:
                    a_np = a_.get("numPop", {})
                    gsap_lines.append(
                        f'tl.fromTo("#bodyline-{sid}-{j} .num-accent",{{scale:1}},'
                        f'{{scale:{a_np.get("scale", 1.12)},'
                        f'duration:{a_np.get("duration", 0.18):.2f},'
                        f'ease:"power2.out",yoyo:true,repeat:1}},'
                        f'{s + a_body.get("startDelay", 0.6) * _k + j * a_body.get("delay", 0.15) * _k + a_body.get("duration", 0.4) * _k:.2f})'
                    )
        # 开场"内容目录"：每条从左侧滑入，逐条错峰
        if agenda_count > 0:
            a_agenda = a_.get("agendaStagger", {})
            for j in range(agenda_count):
                gsap_lines.append(
                    f'tl.from("#agendaitem-{sid}-{j}",{{opacity:0,'
                    f'x:{a_agenda.get("x", -30)},'
                    f'duration:{a_agenda.get("duration", 0.4) * _k:.2f},'
                    f'ease:"{a_agenda.get("ease", "power2.out")}"}},'
                    f'{s + a_agenda.get("startDelay", 0.6) * _k + j * a_agenda.get("delay", 0.12) * _k:.2f})'
                )
        # 结尾"回顾"标签条：每个 chip 弹入，逐条错峰
        if recap_count > 0:
            a_chip = a_.get("chipStagger", {})
            for j in range(recap_count):
                gsap_lines.append(
                    f'tl.from("#recapchip-{sid}-{j}",{{opacity:0,'
                    f'scale:{a_chip.get("scaleFrom", 0.8)},'
                    f'duration:{a_chip.get("duration", 0.35) * _k:.2f},'
                    f'ease:"{a_chip.get("ease", "back.out(1.8)")}"}},'
                    f'{s + a_chip.get("startDelay", 0.6) * _k + j * a_chip.get("delay", 0.1) * _k:.2f})'
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
                f'duration:{a_img.get("duration", 0.8) * _k:.2f},'
                f'ease:"{a_img.get("ease", "power2.out")}"}},'
                f'{s + a_img.get("startDelay", 0.2) * _k:.2f})'
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
    # 切分参数来自 _script_utils.subtitle_params_for（同一份参数来源，
    # 导出的 SRT 共用同一份，保证片内字幕与外挂字幕逐条对齐）。
    _sub_p = subtitle_params_for(aspect)
    _sub_cap = _sub_p["max_chars"]
    _sub_hard = _sub_p["hard_cap"]
    _sub_cue_lines = _sub_p["cue_max_lines"]
    for sent in sentences:
        groups = split_subtitle_cues(sent["text"], max_chars=_sub_cap,
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
<script src="{gsap_src_attr}"></script>
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
.tagline{{font-size:{css_tagline_font};margin-top:{css_tagline_mt}px;line-height:{css_tagline_lh};font-weight:{css_tagline_weight}}}
.body-text{{margin-top:{css_body_margin_top}px;{css_body_chrome}}}
.body-line{{font-size:{css_body_font};color:{theme_colors["body_text"]};line-height:{css_body_lh};font-weight:{css_body_weight}}}
.body-line+.body-line{{margin-top:{css_body_gap}px}}
.num-accent{{display:inline-block;font-weight:700}}
.agenda-list{{display:flex;flex-direction:column;gap:{css_agenda_gap}px;margin-top:{css_agenda_margin};align-items:flex-start;text-align:left}}
.agenda-item{{display:flex;align-items:flex-start;gap:{css_agenda_item_gap}}}
.agenda-num{{flex-shrink:0;width:{css_agenda_num};height:{css_agenda_num};border-radius:50%;
  display:flex;align-items:center;justify-content:center;font-weight:800;
  font-size:calc({css_agenda_num} * 0.5);color:{AGENDA_NUM_TEXT_COLOR}}}
.agenda-title{{font-size:{css_agenda_font};font-weight:600;color:{theme_colors["body_text"]}}}
.recap-chips{{display:flex;flex-wrap:wrap;gap:{css_chip_gap};margin-top:{css_chip_margin};justify-content:center;
  max-width:100%}}
.recap-chip{{padding:{css_chip_pad};border-radius:999px;border:2px solid;
  font-size:{css_chip_font};font-weight:600;color:{theme_colors["body_text"]};
  background:rgba(255,255,255,0.04)}}
.seg-progress{{position:absolute;bottom:0;left:0;height:{css_prog_height}px}}
#twipe{{position:absolute;top:0;left:-100%;width:100%;height:100%;z-index:50;pointer-events:none;will-change:transform}}
.seg-image{{position:absolute;top:{css_img_top};{css_img_pos};width:{css_img_width}px;height:{css_img_height}px;border-radius:{css_img_radius}px;overflow:hidden;background:{theme_colors["body_bg"]}}}
.seg-image img{{position:relative;z-index:1;width:100%;height:100%;object-fit:contain;display:block}}

{_sub_css}

/* ── Vertical (9:16) 大标题 + 图片 + 歌词式句子流（竖屏唯一模式）──
   Only activates when #root has data-aspect="vertical".
   portrait（3:4，1080x1440）复用本块全部规则，紧凑取值来自模板
   vertical 块（compactPadding / image.aspect / image.marginTop /
   verse.windowHeight / verse.clipPad，见上方参数读取处）。
   竖屏只有 verse。结构：大标题
   区（顶部，边距 220）→ 图片（模板 image.aspect 控制槽比例，默认
   4:3）→ verse 句子流（钉在内容区底部，窗口 vertical.verse.
   windowHeight）。verse/clip/line 的通用规则在上方基础块，这里只覆盖竖屏的
   定位差异：verse 渲染在 seg-card 尾部、flex 流内钉底（margin-top:auto）。
   verse 对齐规则：所有段落（含开场/收尾）的 verse 上边界钉在内容区
   底部同一位置——内容段图片槽高度随 aspect 推导，标题行数差异由图片
   与句子流之间的留白吸收（verse 的 margin-top:auto），不传导给 verse
   位置；开场/收尾标题垂直居中于句子上方的空间（margin:auto 0——flex
   的自由空间先被 auto margin 均分，标题居中的同时 verse 仍钉底与内容
   段齐平；文字仍居中）。侧边距统一 50px（标题/图片/句子流对齐同一
   轴线；1080−50×2=980 即图片槽的宽度）。 */
[data-aspect="vertical"] .seg-card{{display:flex!important;flex-direction:column!important;padding:{_v_pad}!important}}
[data-aspect="vertical"] .seg-accent-bar{{display:none!important}}
[data-aspect="vertical"] .seg-title-wrap{{position:relative!important;left:auto!important;right:auto!important;top:auto!important;transform:none!important;width:100%!important;padding:0!important;text-align:left!important;flex-shrink:0!important}}
/* 竖屏 badge 与标题同行（用户要求，1.5.56）：标题是 width:100% 流式（忽略
   leftWithBadge），badge 恢复绝对定位但对齐标题首行——top = 卡片上内边距 62
   + (标题行盒 72×1.35≈97 − badge 90)/2 ≈ 66，left 对齐卡片左内边距 50。
   紧随 badge 的标题盒用真实盒宽右移避开圆（margin-left 110 → 文字起点 160 =
   badge 右缘 140 + 间距 20）；hyperframes content_overlap 按元素盒测量，
   仅靠 padding/text-indent 缩进盒不变小、仍会误报。
   ⚠ top 与 segCard.padding 上值耦合（top = padding + 3.6），改模板 padding
   上值时必须同步改这里（1.5.57 前后 100→62 / 104→66 各一次）。 */
[data-aspect="vertical"] .badge{{position:absolute!important;top:66px!important;left:50px!important;margin:0!important}}
[data-aspect="vertical"] .badge + .seg-title-wrap{{margin-left:110px!important;width:calc(100% - 110px)!important}}
[data-aspect="vertical"] .seg-title{{line-height:1.35!important}}
[data-aspect="vertical"] .tagline{{margin-top:12px!important;padding-left:16px}}
[data-aspect="vertical"] .seg-image{{position:relative!important;top:auto!important;left:auto!important;right:auto!important;transform:none!important;width:calc(100% + {_v_img_ms * 2}px)!important;height:auto!important;aspect-ratio:{_v_img_ar}!important;flex:0 1 auto!important;margin:{_v_img_mt}px 0 0 -{_v_img_ms}px!important;border-radius:{_v_img_radius}px!important}}
[data-aspect="vertical"] .seg-image img,[data-aspect="vertical"] .seg-image video{{width:100%!important;height:100%!important;object-fit:contain!important}}
[data-aspect="vertical"] .body-text{{margin:24px 0 0!important;padding:20px 24px!important;max-width:100%!important;margin-left:0!important}}
[data-aspect="vertical"] .agenda-list{{margin-left:0!important;padding-left:16px}}
[data-aspect="vertical"] .recap-chips{{margin-top:24px!important;justify-content:flex-start}}
[data-aspect="vertical"] #opening .seg-title-wrap,[data-aspect="vertical"] #closing .seg-title-wrap{{text-align:center!important;margin:auto 0!important}}
[data-aspect="vertical"] #opening .recap-chips,[data-aspect="vertical"] #closing .recap-chips{{justify-content:center!important}}
[data-aspect="vertical"] #opening .agenda-list{{margin-left:0!important;padding-left:0}}
/* 开场/收尾页配图后（images.json 的 opening/closing 键）：标题取消垂直居中、
   回到卡片顶部锚定——竖屏版式变为「标题在上 + 图居中 + 句子流钉底」，
   与内容段同一结构语言；自由空间全部由图与句子流之间的 auto-margin 吸收
   （verse 的 margin-top:auto），标题不再悬在图上方半空。选择器特异性比上面的
   margin:auto 0 规则高一档（多个 .has-image 类），不依赖书写顺序。 */
[data-aspect="vertical"] #opening.has-image .seg-title-wrap,[data-aspect="vertical"] #closing.has-image .seg-title-wrap{{margin:0 0!important}}
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
  // verse 句子流：静态行集合（竖屏 verse 渲染；横屏 bar 集合为
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
        c = {{clip: clip, accent: clip.getAttribute("data-accent") || "", winH: v.clientHeight, clipH: clip.scrollHeight, lines: []}};
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
        // 当前句用段落 accent 着色（字重恒定，避免 500/700 切换时
        // 字形宽度跳变）；非活动行恢复主题色，transition 平滑过渡
        L.el.style.color = (L === act && c.accent) ? c.accent : "";
        // past 只在同一 verse（同段落）内比较，跨段句序无先后语义
        L.el.classList.toggle("past", !!act && di < si);
      }}
      if (act) {{
        // 歌词式滚动：active 行滚到窗口顶部往下 clipPad px（= clip 顶部
        // 内边距，与 .verse-clip 的 padding 保持一致——首句/尾句完整
        // 落在 mask 渐隐区之外，不被边缘虚化）；首行不滚出顶、末行
        // 不露底白（clip 比窗口矮时整体不滚）
        const y = c.clipH <= c.winH ? 0
          : Math.max(c.winH - c.clipH, Math.min(0, {_v_verse_clip} - act.top));
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
            if is_content_sid(seg.get("id"))
            and seg.get("id", "") not in images]


def _opening_cover_missing(images, aspect, required):
    """竖屏开场页是否缺封面图（必配与否由模式预设定义）。

    开场封面图不走搜图（search_images 只搜内容段落），由 agent 在第 4 步
    用 ImageGen 等生成后写 images.json 的 "opening" 键。必配范围按叙事模式
    分流（用户指定，1.5.67）：flow 模式开场只有轻量 chips 预告，竖屏配图后
    版式=「标题+图+句子流」更饱满（chips 让位于图）；章节（正常）模式开场
    的目录（agenda）本身就是画面主体，保持目录、无配图义务——两个预设的
    verticalOpeningCover 分别为 true/false（1.5.69 起定义在 template.json
    modes 块，gen 主流程与 run.py 共读同一预设）。横屏开场配图与 closing
    （两画幅）仍可选。required 由调用方传预设值。
    """
    return (aspect in ("portrait", "both") and required
            and "opening" not in images)


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
        print("[warn] Pillow 未安装，跳过配图完整性（损坏/截断）校验，"
              "仅做了文件存在性校验。`pip install Pillow` 后可启用完整"
              "校验，见 SKILL.md 环境准备", file=sys.stderr)
    return missing_imgs, corrupt_imgs


def main():
    setup_stdio()
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
                        choices=["landscape", "portrait", "both"],
                        help="画幅比例 (landscape=1920x1080 横屏 16:9, "
                             "portrait=1080x1440 竖屏 3:4（复用竖屏布局家族、"
                             "上下留白收窄、图片槽 4:3；抖音/快手等沉浸式 feed "
                             "按宽度适配保留完整画面），both=一次性生成两个文件，"
                             "自动覆盖 --width/--height；both 模式下竖屏版文件名"
                             "在 -o 指定的文件名基础上插入 .vertical 后缀)")
    parser.add_argument("--theme", default="cream",
                        choices=list_theme_names(),
                        help="主题配色 (背景/网格/文字/body 背景)，默认 cream（米白色科技风："
                             "暖米白背景 + 冷蓝灰网格线 + 石墨黑文字）。可选主题见 "
                             "config/theme_registry.json；如何按内容基调选主题见 SKILL.md「主题选择」")
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
                    print("[error] 请检查 images.json 与实际图片文件是否一致"
                          "（常见原因：改了图片扩展名/替换图片后未重跑 "
                          "gen_hyperframes.py 重新生成 HTML）。", file=sys.stderr)
                if corrupt_imgs:
                    print("[error] 请重新下载/生成对应图片后再重跑本脚本"
                          "（常见原因：下载中途网络中断、磁盘写满导致文件"
                          "截断）。", file=sys.stderr)
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

    # 竖屏开场封面图必配（1.5.69 起由模式预设 verticalOpeningCover 定义，
    # flow 预设为 true）：portrait/both 且 images 无 "opening" 键时提示；
    # 章节预设该键为 false——开场保持目录、无配图义务。run.py 一键编排读
    # 同一预设拦截，分步执行时这行 warn 是唯一防线。纯文字版（--images
    # 未传/文件不存在）同样提示——竖屏纯文字版是"配图来源全部不可用"的
    # 兜底，能配就该配。
    if _opening_cover_missing(images, args.aspect,
                              get_mode_preset(manifest)["verticalOpeningCover"]):
        print("[warn] 竖屏开场页缺少封面图（当前叙事模式默认必配，无需手动"
              "开启）：请用 ImageGen 生成一张点题封面图（风格与内容段配图"
              "一致），放进 HTML 输出目录的 images/，并在 images.json 写 "
              '"opening": {"src": "images/opening.png"} 后重跑；'
              "仅当配图来源全部不可用（纯文字版兜底）时才保留无图开场。"
              "（章节模式开场保持目录，无配图义务，无需处理本提示的变体）",
              file=sys.stderr)

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
        """渲染单个画幅版本。--aspect both 会对 landscape/portrait 各调用一次。"""
        import shutil  # 局部导入：音频拷贝与 preview.js 复制共用
        w, h = width, height
        out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
        if aspect == "portrait":
            # 竖屏固定画幅 1080x1440（3:4）；用户显式传了非默认
            # --width/--height 会被静默忽略——打警告说明，而不是无声吞掉
            if (args.width, args.height) not in ((1920, 1080), (1080, 1440)):
                print(f"[warn] --aspect portrait 固定使用 1080x1440 画幅，"
                      f"显式传入的 --width/--height（{args.width}x{args.height}）"
                      f"对竖屏版不生效。", file=sys.stderr)
            w, h = 1080, 1440
        # CSS layout uses fixed offsets (padding:0 100px, right:0, etc.) that
        # assume a 1920x1080 (16:9) frame. Non-16:9 ratios will misalign titles
        # and image cards. Warn instead of silently producing broken layouts.
        # 竖屏 3:4 portrait 为有意为之的画幅，无需警告。
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
                    if not _file_identical(audio_abs, dst):
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
                             aspect=aspect, theme=args.theme, fps=args.fps)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        # 原子写：index.html 是渲染输入，写到一半被打断会留下半份 HTML——
        # check/render 会报莫名其妙的语法错，而不是"上次生成中断了，重跑"。
        _tmp_html = output_path + ".tmp"
        with open(_tmp_html, 'w', encoding='utf-8') as f:
            f.write(html)
            f.flush()
            os.fsync(f.fileno())
        os.replace(_tmp_html, output_path)

        # 复制预览脚本到 HTML 输出目录（index.html 引用同目录 preview.js）
        preview_js = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "preview.js")
        if os.path.isfile(preview_js):
            _dst_preview = os.path.join(out_dir, "preview.js")
            # copy2 会保留源文件的只读权限位——技能目录的 preview.js 是
            # r--r--r--，目标已存在时二次生成必然 PermissionError（复发性
            # 坑：首次生成成功、重跑就炸）。覆盖前先确保可写。
            if os.path.exists(_dst_preview):
                os.chmod(_dst_preview, 0o644)
            shutil.copy2(preview_js, _dst_preview)
            os.chmod(_dst_preview, 0o644)
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
        # 竖屏版固定 3:4（1080x1440）；文件名沿用 .vertical 后缀
        # （"竖屏版"含义，历史契约不变）
        _render_one("portrait", 1080, 1440, vertical_path)
    else:
        _render_one(args.aspect, args.width, args.height, args.output)


if __name__ == "__main__":
    main()
