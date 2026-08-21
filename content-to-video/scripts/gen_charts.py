#!/usr/bin/env python3
"""从真实数据点生成图表配图（配图第三种方式：方式 C，区别于 StepFun 文搜图和
ImageGen 生成示意图）。

**什么时候该用这个而不是方式 A/B**：段落讲的是具体数据（财报数字、增长率、
占比、排行）时，真实数据画出来的图表比"搜一张相关照片"或"AI 生成一张意象图"
更准确、更有信息量——意象图传达"感觉"，图表传达"数字本身"。抽象概念（没有
具体数字，比如"反向传播算法"）不适合这条路径，应该用方式 B（ImageGen 生成
示意图）。数学公式本体也走这条（`formula` 类型渲染成 SVG）：公式没有真实
照片可搜（方式 A 文不对题），AI 生图又容易把符号画错（方式 B 不可靠），
本地 mathtext 渲染最准确。函数曲线也走这条（`curve` 类型：表达式自动求值 +
数值 x 轴 + 多曲线同图对比，画 y=x**2 / 1.08**x 这类函数不需要手算采样点）。

输入格式（charts.json）：
{
  "charts": [
    {
      "id": "seg1",                       // 对应段落 id，输出文件名 = <id>.png
      "type": "bar",                      // bar | line | pie | formula | curve（缺省 bar）
      "title": "2026 Q2 营收（亿元）",      // 图表标题，显示在图片顶部
      "labels": ["Q1", "Q2"],             // x 轴标签 / 饼图扇区标签
      "values": [12.3, 15.8],             // 对应数值
      "unit": "亿元",                      // 可选，追加在数值标签后
      "accent": "#64b5f6"                 // 可选，缺省用主题默认色
    },
    {
      "id": "seg2",                       // type=formula 时输出 <id>.svg
      "type": "formula",                  // 公式渲染（mathtext，零额度）
      "formula": "E = mc^2",              // 没写 $ 定界符会自动包一层
      "title": "质能方程"                  // 可选，显示在公式下方
    },
    {
      "id": "seg3",                       // 函数曲线（输出 <id>.png）
      "type": "curve",                    // 多曲线同图对比
      "curves": [                         // 每项一条曲线
        {"expr": "10*1.08**x", "label": "复利"},
        {"expr": "10+0.8*x", "label": "单利"}
      ],
      "x_min": 0, "x_max": 30,            // 数值 x 轴范围
      "x_label": "年",                     // 可选
      "title": "单利 vs 复利"              // 可选
    }
  ]
}

Usage:
  python gen_charts.py -i charts.json -o hf-project/images

产出 <output>/<id>.png（formula 类型为 <id>.svg），可直接在 images.json 里引用（用法和方式 A/B 生成的
图片完全一样，images.json 不区分图片是搜来的、AI 生成的还是图表画出来的）。

**安全区提醒**：Hyperframes 渲染时图片槽是 900×700 的横版卡片，CSS 用
`object-fit:contain` 填充——不裁边，图表永远完整显示。画布固定 4:3（见
FIGSIZE，1200×900），与方式 B 的 landscape_4_3 同一比例约定，在横版槽内
填充率约 96%。本脚本默认仍在画布四周留白（见 SAFE_MARGIN_RATIO），让
图表主体与卡片边缘有呼吸感（纯排版美观考虑，不是防裁切）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _theme import get_default_accent  # noqa: E402

SAFE_MARGIN_RATIO = 0.09  # 画布四周各留 9% 空白（排版呼吸感；contain 填充不裁边）

# 画布 4:3（dpi=100 下 1200×900）：横屏图片槽 900×700（≈1.29:1）contain
# 填充率约 96%，方图（1:1）只有约 78%（两侧各 ~100px
# 模糊底）；与方式 B 的 landscape_4_3（1152×864）同一比例约定。竖屏槽
# 接近正方形时 4:3 仍有约 76% 填充率，是双画幅下的稳妥折中。
FIGSIZE = (12, 9)

# 图表固定用深色底，不跟随 --theme：这跟方式 B（ImageGen）的约定一致——配图
# 本身统一走"dark background + 数字艺术风格"，不需要随视频主题（cream/dark/…）
# 切换，字幕/标题卡片才是跟随主题的部分。避免了解析 registry.json 里那些
# CSS 专用的 rgba()/gradient() 字符串（matplotlib 不认识这些语法）。
CHART_BG = "#0f1420"
CHART_TEXT = "#f2f2f2"
CHART_GRID = "#39415a"


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _lighten(hex_color, amount):
    """把一个 hex 颜色朝白色方向混合 amount（0-1），用于同一 accent 派生出多个色阶（饼图/多柱场景）。"""
    r, g, b = _hex_to_rgb(hex_color)
    r = r + (1 - r) * amount
    g = g + (1 - g) * amount
    b = b + (1 - b) * amount
    return (r, g, b)


def _accent_shades(accent, n):
    """从一个 accent 色派生 n 个由深到浅的色阶（饼图/多系列柱状图用）。"""
    if n <= 1:
        return [_hex_to_rgb(accent)]
    return [_lighten(accent, i * 0.55 / max(n - 1, 1)) for i in range(n)]


def _configure_cjk_font():
    """让 matplotlib 找到一个能渲染中文的字体；找不到就退回默认字体
    （英文标签仍正常，中文会显示成方块——这种情况下会打印一次性警告，
    提示装什么字体，而不是让用户对着一堆 tofu box 摸不着头脑）。
    """
    import matplotlib
    from matplotlib import font_manager

    candidates = [
        "Noto Sans CJK SC", "Noto Sans CJK TC", "Noto Sans SC",
        "WenQuanYi Zen Hei", "PingFang SC", "Microsoft YaHei", "SimHei",
        "Source Han Sans SC", "Source Han Sans CN",
    ]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            matplotlib.rcParams["font.sans-serif"] = [name]
            matplotlib.rcParams["axes.unicode_minus"] = False
            return name
    print("[warn] 系统里没找到常见中文字体（Noto Sans CJK / 文泉驿 / 微软雅黑等），"
          "图表里的中文可能显示为方块。建议安装 Noto Sans CJK 后重跑本脚本："
          "  apt-get install -y fonts-noto-cjk", file=sys.stderr)
    return None


_CJK_FONT_CONFIGURED = False
def render_chart(chart, out_path):
    """渲染单张图表 PNG。chart 是 charts.json 里 'charts' 数组的一项。"""
    import matplotlib
    matplotlib.use("Agg")  # 无显示环境下渲染，必须在 import pyplot 之前设置
    import matplotlib.pyplot as plt

    global _CJK_FONT_CONFIGURED
    if not _CJK_FONT_CONFIGURED:
        _configure_cjk_font()
        _CJK_FONT_CONFIGURED = True

    accent = chart.get("accent") or get_default_accent()
    bg_hex, text_hex, grid_hex = CHART_BG, CHART_TEXT, CHART_GRID

    chart_type = (chart.get("type") or "bar").lower()
    if chart_type == "formula":
        return _render_formula(chart, out_path)
    if chart_type == "curve":
        return _render_curve(chart, out_path)
    title = chart.get("title", "")
    labels = chart.get("labels") or []
    values = chart.get("values") or []
    unit = chart.get("unit", "")
    if len(labels) != len(values) or not labels:
        raise ValueError(f"chart {chart.get('id')!r}: 'labels' 和 'values' "
                          f"长度必须一致且非空（labels={labels!r}, values={values!r}）")
    if chart_type == "pie" and sum(values) <= 0:
        raise ValueError(f"chart {chart.get('id')!r}: 饼图的 'values' 总和必须大于 0"
                          f"（当前 values={values!r}，全 0 或负数时占比没有意义，"
                          f"matplotlib 会除零报警并画出空图）")

    fig = plt.figure(figsize=FIGSIZE, dpi=100, facecolor=bg_hex)
    m = SAFE_MARGIN_RATIO
    ax = fig.add_axes([m, m, 1 - 2 * m, 1 - 2 * m])
    ax.set_facecolor(bg_hex)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors=text_hex, labelsize=22)

    if chart_type == "line":
        ax.plot(labels, values, color=accent, linewidth=5, marker="o", markersize=14,
                markerfacecolor=accent, markeredgecolor=bg_hex, markeredgewidth=2)
        ax.grid(axis="y", color=grid_hex, alpha=0.4, linewidth=1)
        for x, v in zip(labels, values):
            ax.annotate(f"{v}{unit}", (x, v), textcoords="offset points",
                        xytext=(0, 18), ha="center", color=text_hex, fontsize=24,
                        fontweight="bold")
        ax.margins(y=0.25)
    elif chart_type == "pie":
        colors = _accent_shades(accent, len(values))
        wedges, _texts, autotexts = ax.pie(
            values, labels=None, colors=colors, startangle=90,
            autopct=lambda pct: f"{pct:.0f}%", pctdistance=0.75,
            wedgeprops={"edgecolor": bg_hex, "linewidth": 3},
        )
        for at in autotexts:
            at.set_color(bg_hex)
            at.set_fontsize(22)
            at.set_fontweight("bold")
        ax.legend(wedges, labels, loc="lower center", bbox_to_anchor=(0.5, -0.08),
                  ncol=min(len(labels), 3), frameon=False,
                  labelcolor=text_hex, fontsize=20)
        ax.set_aspect("equal")
    else:  # bar（默认）
        colors = _accent_shades(accent, len(values))
        bars = ax.bar(labels, values, color=colors, width=0.55)
        ax.grid(axis="y", color=grid_hex, alpha=0.4, linewidth=1)
        ax.set_axisbelow(True)
        for rect, v in zip(bars, values):
            ax.annotate(f"{v}{unit}", (rect.get_x() + rect.get_width() / 2, rect.get_height()),
                        textcoords="offset points", xytext=(0, 10), ha="center",
                        color=text_hex, fontsize=24, fontweight="bold")
        ax.margins(y=0.2)

    if title:
        ax.set_title(title, color=text_hex, fontsize=30, fontweight="bold", pad=28)

    fig.savefig(out_path, facecolor=bg_hex)
    plt.close(fig)


def _render_formula(chart, out_path):
    """公式配图：matplotlib mathtext 渲染成 SVG（方式 C 的 formula 子类型）。

    公式没有真实照片可搜（方式 A 文不对题），AI 生图容易把符号画错（方式 B
    不可靠），本地渲染最准确且零额度。输出 SVG 矢量图，gen_hyperframes 对
    .svg 按存在性校验后即可引用，用法与 png 图表完全一致。
    """
    import matplotlib.pyplot as plt

    cid = chart.get("id")
    formula = (chart.get("formula") or "").strip()
    if not formula:
        raise ValueError(f"chart {cid!r}: type=formula 需要非空 formula 字段"
                         f"（mathtext 语法，如 E = mc^2；未写 $ 定界符会自动包一层）")
    if "$" not in formula:
        formula = f"${formula}$"

    bg_hex, text_hex = CHART_BG, CHART_TEXT
    fig = plt.figure(figsize=FIGSIZE, dpi=100, facecolor=bg_hex)
    # 字号自适应：从 96 起步往小试，直到公式宽度进入安全区（SAFE_MARGIN_RATIO
    # 留白保持排版呼吸感，与图表类同约定；contain 填充不裁边）；下限 16 保证
    # 极端长公式也有输出（宁可小，不要缺）
    safe_w = 12 * (1 - 2 * SAFE_MARGIN_RATIO)
    fontsize = 96
    try:
        while True:
            t = fig.text(0.5, 0.5, formula, ha="center", va="center",
                         fontsize=fontsize, color=text_hex)
            fig.canvas.draw()
            if t.get_window_extent().width / fig.dpi <= safe_w or fontsize <= 16:
                break
            t.remove()
            fontsize -= 8
    except ValueError as e:
        plt.close(fig)
        raise ValueError(f"chart {cid!r}: 公式 mathtext 解析失败（{formula!r}），"
                         f"检查语法（上下标 ^/_、分式 \\frac{{a}}{{b}}；中文放 "
                         f"title 别放 formula）：{e}")
    title = chart.get("title", "")
    if title:
        fig.text(0.5, 0.18, title, ha="center", va="center",
                 fontsize=30, fontweight="bold", color=text_hex)
    fig.savefig(out_path, facecolor=bg_hex)
    plt.close(fig)


def _render_curve(chart, out_path):
    """函数曲线配图（方式 C 的 curve 子类型）：表达式在受限 numpy 命名空间里
    求值，支持多曲线同图对比——讲解内容的经典视觉就是"曲线的分化即论点"
    （复利指数曲线甩开单利直线、sigmoid 与阶跃的逼近）。

    与 line 类型的区别：line 的 x 轴是分类标签、values 要手工算好；
    curve 是真·数值 x 轴 + 表达式自动求值，画 y=x**2 / sin(x) / 1.08**x
    不需要手算采样点。
    """
    import numpy as np
    import matplotlib.pyplot as plt

    cid = chart.get("id")
    curves = chart.get("curves") or []
    if not curves:
        raise ValueError(f"chart {cid!r}: type=curve 需要非空 curves 数组"
                         f"（每项 {{'expr': '1.08**x', 'label': '复利'}}）")
    try:
        x_min = float(chart.get("x_min", 0))
        x_max = float(chart.get("x_max", 10))
    except (TypeError, ValueError):
        raise ValueError(f"chart {cid!r}: x_min/x_max 必须是数字")
    if x_max <= x_min:
        raise ValueError(f"chart {cid!r}: x_max 必须大于 x_min"
                         f"（当前 {x_min} ~ {x_max}）")

    x = np.linspace(x_min, x_max, 400)
    # 受限命名空间：charts.json 与跑 Python 脚本同一信任域（都是 agent 自己
    # 写的），仍只放数学函数，防表达式误用无关内建
    ns = {"x": x, "np": np, "pi": np.pi, "e": np.e, "sqrt": np.sqrt,
          "sin": np.sin, "cos": np.cos, "tan": np.tan, "exp": np.exp,
          "log": np.log, "log2": np.log2, "log10": np.log10, "abs": np.abs}

    accent = chart.get("accent") or get_default_accent()
    colors = _accent_shades(accent, len(curves))
    bg_hex, text_hex, grid_hex = CHART_BG, CHART_TEXT, CHART_GRID
    fig = plt.figure(figsize=FIGSIZE, dpi=100, facecolor=bg_hex)
    m = SAFE_MARGIN_RATIO
    ax = fig.add_axes([m, m, 1 - 2 * m, 1 - 2 * m])
    ax.set_facecolor(bg_hex)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors=text_hex, labelsize=22)
    ax.grid(color=grid_hex, alpha=0.4, linewidth=1)

    for i, c in enumerate(curves):
        expr = (c.get("expr") or "").strip()
        if not expr:
            raise ValueError(f"chart {cid!r}: curves[{i}] 缺少 expr 字段")
        try:
            with np.errstate(all="ignore"):
                y = eval(expr, {"__builtins__": {}}, ns)  # noqa: S307
            y = np.asarray(y, dtype=float)
            if y.shape != x.shape:
                # 常数函数（y=5）直接广播到整条 x 轴
                y = np.broadcast_to(y, x.shape)
        except Exception as e:
            raise ValueError(f"chart {cid!r}: curves[{i}] 表达式求值失败"
                             f"（{expr!r}）：{e}")
        if not np.any(np.isfinite(y)):
            raise ValueError(f"chart {cid!r}: curves[{i}] 在 "
                             f"x∈[{x_min}, {x_max}] 上全是无效值"
                             f"（{expr!r}——通常是定义域问题，如 log 负数；"
                             f"调整表达式或 x_min/x_max）")
        ax.plot(x, y, color=colors[i], linewidth=5,
                label=c.get("label") or f"y{i + 1}")

    ax.legend(loc="upper left", frameon=False, labelcolor=text_hex, fontsize=24)
    if chart.get("x_label"):
        ax.set_xlabel(chart["x_label"], color=text_hex, fontsize=24)
    if chart.get("y_label"):
        ax.set_ylabel(chart["y_label"], color=text_hex, fontsize=24)
    title = chart.get("title", "")
    if title:
        ax.set_title(title, color=text_hex, fontsize=30, fontweight="bold", pad=28)
    fig.savefig(out_path, facecolor=bg_hex)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description="从数据点生成图表配图（方式 C，区别于 StepFun 文搜图 / ImageGen 生成示意图）")
    parser.add_argument("-i", "--input", required=True, help="charts.json 路径")
    parser.add_argument("-o", "--output", required=True, help="图片输出目录")
    args = parser.parse_args()

    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("[error] 需要 matplotlib（图表配图专用依赖，不在核心依赖里，见 SKILL.md 环境准备）：\n"
              "  pip install matplotlib --break-system-packages", file=sys.stderr)
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)
    charts = data.get("charts", [])
    if not charts:
        print("[error] charts.json 的 'charts' 为空", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output, exist_ok=True)
    for chart in charts:
        cid = chart.get("id")
        if not cid:
            print(f"[error] 有一项 chart 缺少 'id' 字段: {chart}", file=sys.stderr)
            sys.exit(1)
        ext = ".svg" if (chart.get("type") or "bar").lower() == "formula" else ".png"
        out_path = os.path.join(args.output, f"{cid}{ext}")
        try:
            render_chart(chart, out_path)
        except ValueError as e:
            print(f"[error] {e}", file=sys.stderr)
            sys.exit(1)
        print(f"[ok] {cid} ({chart.get('type', 'bar')}) -> {out_path}", file=sys.stderr)

    print(f"\n下一步：把上面生成的文件填进 images.json"
          f"（{{\"seg1\": {{\"src\": \"images/seg1.png\"}}, ...}}），"
          f"和方式 A/B 的用法完全一样。", file=sys.stderr)


if __name__ == "__main__":
    main()
