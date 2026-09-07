"""主题注册表加载（从 gen_hyperframes.py 拆出）。

config/theme_registry.json 是渲染用的唯一配色数据源，包含：
  - "cream" / "dark"：背景/文字等主题配色（get_theme_colors）
  - "_accent_palette"：8 色 accent 色板，供 build_from_structured
    按新闻序号轮询取色（get_accent_palette）

registry.json 是本模块的唯一权威来源，Python 侧所有配色/色板都从这里读。
不做硬编码兜底（双数据源必然漂移），文件缺失或损坏时直接抛错。
"""
import json
import os

_REGISTRY_CACHE = None


def _load_registry():
    """加载 config/theme_registry.json；缺失/损坏时抛错（不做静默回退）。"""
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    registry_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "config", "theme_registry.json",
    )
    if not os.path.isfile(registry_path):
        raise FileNotFoundError(
            f"[theme] 主题注册表不存在: {registry_path}（技能安装不完整，"
            "请重新同步/安装 skill）"
        )
    with open(registry_path, "r", encoding="utf-8") as f:
        try:
            _REGISTRY_CACHE = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"[theme] 主题注册表不是合法 JSON: {registry_path} ({e})"
            ) from e
    _REGISTRY_CACHE.pop("_meta", None)
    return _REGISTRY_CACHE


def get_theme_colors(theme):
    """根据主题名返回主题配色字典。

    影响背景渐变、网格线、文字颜色与 body 背景色；每段 accent 彩色不受影响。

    Args:
        theme: 主题名，取值见 list_theme_names()（当前为 cream/dark）

    Returns:
        dict: 包含 bg_gradient, grid_color, text_color, body_bg, body_text,
              sub_bar_rgb, soft_border, sub_text_shadow 八个键。
              sub_bar_rgb 是底部字幕栏渐变的 "R,G,B" 字符串（不含 alpha）——
              字幕栏本质是一块"渐变到不透明"的色块，颜色必须跟主题背景同色系，
              深色主题用近黑色块、浅色主题用近背景色的浅色块，否则深色文字主题
              配黑色字幕栏、或反过来，文字会几乎读不出来。
              soft_border 是卡片左侧强调边框色，同理需要跟着深浅主题换向。
    """
    registry = _load_registry()
    themes = {k: v for k, v in registry.items() if not k.startswith("_")}
    if theme not in themes:
        # 不做静默回退（与本模块 docstring"不做硬编码兜底"原则一致）：
        # 未知主题名回退 cream 会掩盖调用方传错值。CLI 入口已用
        # list_theme_names() 做 choices 校验，能走到这里说明是编程
        # 调用传错了名字——直接报错并列出可用主题。
        raise ValueError(
            f"[theme] 未知主题名: {theme!r}（可用: {', '.join(sorted(themes))}）")
    return themes[theme]


def list_theme_names():
    """返回 registry.json 里所有可用主题名（按字母排序），供 CLI --theme 的
    choices 动态生成，避免 gen_hyperframes.py / run.py 各硬编码一份列表、
    加主题时漏改其中一处（历史教训见 accent 色板收口到本模块前的重复问题）。
    """
    registry = _load_registry()
    return sorted(k for k in registry.keys() if not k.startswith("_"))


def get_accent_palette():
    """返回 accent 色板（8 色列表），供按新闻序号轮询分配颜色。

    唯一权威来源是 config/theme_registry.json 的 "_accent_palette" 键；
    Python 侧（build_from_structured.py）从本函数读取，不再有第二份硬编码副本。
    """
    registry = _load_registry()
    palette = registry.get("_accent_palette")
    if not palette:
        raise ValueError(
            "[theme] config/theme_registry.json 缺少 '_accent_palette' 键，"
            "无法分配段落强调色"
        )
    return palette


def get_default_accent():
    """返回默认段落强调色（opening/closing 与 fallback 分组使用）。

    唯一权威来源是 config/theme_registry.json 的 "_default_accent" 键
    （单一数据源；散落多处硬编码 "#64b5f6" 改配色时容易漏改）。
    """
    registry = _load_registry()
    default = registry.get("_default_accent")
    if not default:
        raise ValueError(
            "[theme] config/theme_registry.json 缺少 '_default_accent' 键，"
            "无法确定默认段落强调色"
        )
    return default


# ── 颜色数学 ────
# 纯函数、无注册表依赖：gen_hyperframes（tagline 深浅适配/深浅底判断）与
# selftest（WCAG 对比度断言）共用——单一来源，任何改动两边自动感知。


def darken(hex_color, factor=0.6):
    """把 accent 十六进制色压暗一档，用于浅色主题下的小字（如 tagline）。

    保持色相不变、只降低亮度，让文字在米白/浅色背景上达到可读对比度，
    同时不改变该段落 accent 色在大元素（竖条/glow/进度条）上的视觉效果。
    """
    try:
        h = hex_color.lstrip("#")
        if len(h) != 6:
            return hex_color
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        return f"#{int(r * factor):02x}{int(g * factor):02x}{int(b * factor):02x}"
    except ValueError:
        return hex_color


def hex_to_rgb01(hex_color):
    """'#rrggbb'/'#rgb' → (r,g,b) 归一化到 0-1；解析不了返回 None。

    3 位缩写（#fff）先展开成 6 位——theme_registry 里 dark 主题的
    text_color 就是 "#fff"，不展开会解析失败返回 None，深浅主题判断
    静默失效（dark 被当浅色，tagline 走压暗分支，对比度掉到 ~2.1）。
    """
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None


def expand_hex(color):
    """'#abc' → '#aabbcc'（3 位缩写展开）；其余输入原样返回，None 安全。

    供 accent 拼 alpha 后缀（如 f'{ac}40'）前归一化：3 位 hex 拼 2 位
    alpha 会得到非法的 8 位颜色（如 '#fff40'），浏览器把整条 CSS 声明
    丢弃（阴影/发光悄悄消失）。非 hex 的 3 字符串不展开，原样返回。
    """
    if not color:
        return color
    h = color.lstrip("#")
    if len(h) == 3 and all(c in "0123456789abcdefABCDEF" for c in h):
        return "#" + "".join(c * 2 for c in h)
    return color


def relative_luminance(hex_color):
    """WCAG 相对亮度（用于判断主题深浅底）。"""
    rgb = hex_to_rgb01(hex_color)
    if rgb is None:
        return None
    lin = tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                for c in rgb)
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def mix(hex_color, other_hex, ratio):
    """按比例混两色（0=全 hex_color，1=全 other_hex）。"""
    a = hex_to_rgb01(hex_color)
    b = hex_to_rgb01(other_hex)
    if a is None or b is None:
        return hex_color
    mixed = (round(255 * (a[i] * (1 - ratio) + b[i] * ratio)) for i in range(3))
    return "#{:02x}{:02x}{:02x}".format(*mixed)
