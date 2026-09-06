#!/usr/bin/env python3
"""从 segments_source.json 提取一张竖版封面图 + 3 个候选标题——如果这个 skill
产出的成片下游是发布到某个平台，这一步能省掉"打开 PS/临时找图"的手工步骤。

明确说清楚这个脚本做的和不做的：
  - 做的：机械提取（取 opening/第一段文字、跟主题配色联动画一张纯色渐变+文字
    的竖版封面）+ 生成 3 个候选标题的**草稿**。
  - 不做的：不判断"这个标题好不好"、不做真正的"标题党"式改写——那需要对
    内容的语义理解，只能靠 agent/人工在候选草稿基础上再挑一个改一改，本脚本
    只负责省去"从头对着空白想 3 个标题"这一步，产出是起点不是终点。

用法：
  python scripts/gen_cover.py -s segments_source.json -o hf-project --theme dark

产出（默认写进 -o 指定的输出目录）：
  cover.png    竖版封面（默认 1080x1920），主标题用候选标题里的第一个
  titles.json  {"candidates": [标题1, 标题2, 标题3]}
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _contracts import load_segments_source  # noqa: E402
from _theme import get_theme_colors, list_theme_names  # noqa: E402
from _script_utils import setup_stdio  # noqa: E402  重定向场景 stdout 强制 UTF-8

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = ImageDraw = ImageFont = None

# 常见 Linux 发行版里 Noto Sans CJK 的典型路径（fonts-noto-cjk 包），
# 跟 gen_charts.py 假设的字体来源一致，避免另起一套字体发现逻辑。
_CJK_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",  # macOS
    "C:\\Windows\\Fonts\\msyh.ttc",  # Windows 微软雅黑
]


def _find_cjk_font():
    for path in _CJK_FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def _first_sentence(text, max_len=40):
    text = (text or "").strip()
    if not text:
        return ""
    m = re.search(r"[。！？\uFF01\uFF1F]", text)
    s = text[:m.end()] if m else text
    return s[:max_len]


def build_title_candidates(source):
    """规则化生成 3 个候选标题草稿：

    1. 第一段小节的 title 原样（最直接、最不容易"标题党过头"）
    2. "N条XX"式盘点体：用段落数 + 第一段标题
    3. opening 或第一段正文的第一句话（如果比小节标题信息量更大）
    这几条候选故意保持简单机械，供人工/agent 挑一个再改，而不是假装能替代
    真正的标题创作判断。
    """
    segments = source.get("segments", [])
    if not segments:
        return []
    first = segments[0]
    first_title = (first.get("title") or "").strip()
    candidates = []
    if first_title:
        candidates.append(first_title)

    n = len(segments)
    if first_title:
        candidates.append(f"{n}条要点：{first_title}等你都该知道")

    lead_text = source.get("opening") or first.get("text") or ""
    lead_sentence = _first_sentence(lead_text)
    if lead_sentence and lead_sentence not in candidates:
        candidates.append(lead_sentence)

    # 去重、补足到 3 条（不够就不硬凑，宁可少给候选也不给空话占位）
    seen, uniq = set(), []
    for c in candidates:
        if c and c not in seen:
            uniq.append(c)
            seen.add(c)
    return uniq[:3]


def _hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(ch * 2 for ch in hex_color)
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _gradient_endpoints(bg_gradient):
    """从 CSS linear-gradient() 字符串里粗暴抠出第一个和最后一个 #hex 颜色，
    当作竖版封面渐变的起止色——不追求完全还原 CSS 渐变角度/多色停靠点，
    封面图本来就只需要"跟主题配色同色系"这种粗粒度一致，不需要像成片里的
    背景那样像素级还原。"""
    hexes = re.findall(r"#[0-9a-fA-F]{3,6}", bg_gradient)
    if not hexes:
        return (20, 20, 20), (40, 40, 40)
    return _hex_to_rgb(hexes[0]), _hex_to_rgb(hexes[-1])


def render_cover(title, theme_name, out_path, width=1080, height=1920):
    if Image is None:
        print("[gen-cover] 未安装 Pillow，无法生成封面图（pip install Pillow）",
              file=sys.stderr)
        return False

    colors = get_theme_colors(theme_name)
    top_rgb, bottom_rgb = _gradient_endpoints(colors.get("bg_gradient", ""))
    text_rgb = _hex_to_rgb(colors["text_color"]) if colors.get("text_color", "").startswith("#") \
        else (255, 255, 255)

    img = Image.new("RGB", (width, height), top_rgb)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(height - 1, 1)
        r = round(top_rgb[0] + (bottom_rgb[0] - top_rgb[0]) * t)
        g = round(top_rgb[1] + (bottom_rgb[1] - top_rgb[1]) * t)
        b = round(top_rgb[2] + (bottom_rgb[2] - top_rgb[2]) * t)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    font_path = _find_cjk_font()
    if font_path is None:
        print("[gen-cover][warn] 找不到系统 CJK 字体，封面图会跳过标题文字"
              "（背景渐变仍会生成）；可以装 fonts-noto-cjk 或手动在生成的图上"
              "叠字", file=sys.stderr)
    else:
        font_size = 96
        font = ImageFont.truetype(font_path, font_size)
        # 按可用宽度（留 10% 边距）自动换行，字号过大就整体缩小，直到能放下
        max_width = width * 0.86
        max_height = height * 0.5
        while font_size > 32:
            font = ImageFont.truetype(font_path, font_size)
            lines, cur = [], ""
            for ch in title:
                trial = cur + ch
                if draw.textlength(trial, font=font) > max_width and cur:
                    lines.append(cur)
                    cur = ch
                else:
                    cur = trial
            if cur:
                lines.append(cur)
            line_height = font_size * 1.35
            total_height = line_height * len(lines)
            if total_height <= max_height:
                break
            font_size -= 8
        y = (height - total_height) / 2
        for line in lines:
            w = draw.textlength(line, font=font)
            draw.text(((width - w) / 2, y), line, font=font, fill=text_rgb)
            y += line_height

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path)
    return True


def main():
    setup_stdio()
    parser = argparse.ArgumentParser(description="从 segments_source.json 生成封面图 + 候选标题草稿")
    parser.add_argument("-s", "--source", required=True, help="segments_source.json 路径")
    parser.add_argument("-o", "--output", required=True, help="输出目录")
    parser.add_argument("--theme", default="cream", choices=list_theme_names(),
                        help="跟成片保持一致的主题名，决定封面配色")
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--title", default=None,
                        help="手动指定封面主标题（默认用候选标题第一条）")
    args = parser.parse_args()

    try:
        source = load_segments_source(args.source)
    except (OSError, ValueError) as e:
        print(f"[error] 读取/校验 {args.source} 失败：{e}", file=sys.stderr)
        sys.exit(1)

    candidates = build_title_candidates(source)
    if not candidates:
        print("[error] segments_source.json 里没有可用的 segments，"
              "无法提取候选标题", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output, exist_ok=True)
    titles_path = os.path.join(args.output, "titles.json")
    with open(titles_path, "w", encoding="utf-8") as f:
        json.dump({"candidates": candidates}, f, ensure_ascii=False, indent=2)
    print(f"[gen-cover] {len(candidates)} 个候选标题草稿 -> {titles_path}")
    for i, c in enumerate(candidates, 1):
        print(f"  {i}. {c}")

    main_title = args.title or candidates[0]
    cover_path = os.path.join(args.output, "cover.png")
    ok = render_cover(main_title, args.theme, cover_path, args.width, args.height)
    if ok:
        print(f"[gen-cover] 封面图（主标题：{main_title}）-> {cover_path}")
    else:
        print("[gen-cover] 封面图生成失败，仅产出候选标题", file=sys.stderr)


if __name__ == "__main__":
    main()
