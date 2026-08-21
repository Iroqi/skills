#!/usr/bin/env python3
"""关键帧像素级视觉回归：抓"跟上次比明显变了"，不追求自动判断"好不好看"。

背景（见 references/pitfalls.md 第 14 条）："渲染引擎百分比宽高算错导致标题
挤成一列"这类布局 bug 只有真正渲染截图才暴露；人眼盯每一帧看又容易漏看、
也不会每次改动都真去看。

本脚本不做"这张截图好不好看"的语义判断（那仍然只能靠 agent 视觉能力，见
SKILL.md"渲染命令"一节），只做一件更窄但可以完全自动化的事：跟上一次已确认
没问题的截图（baseline）逐像素比对，超过阈值的才提醒去看，正常改动（文案变了、
配图换了）预期本来就会触发提醒，不是"零 diff 才算过"。

用法：
  # 第一次，或人工确认过当前关键帧没问题后，存一份新的 baseline
  python scripts/visual_regression.py snapshot -i hf-project/snapshots -o dev/visual_baseline

  # 之后每次改完 gen_hyperframes.py/模板/主题，重新抓关键帧后跟 baseline 比对
  python scripts/visual_regression.py diff -i hf-project/snapshots -b dev/visual_baseline

关键帧从哪来：`npx hyperframes check --snapshots` 产出的 PNG 目录，或者任何
其他方式截出来的固定输入的关键帧截图——本脚本不关心截图怎么来的，只关心
"文件名相同的两张图，像素差异有多大"，所以 -i/-b 两个目录下同名文件才会被比较。

退出码：0 = 没有超过阈值的差异（或没有可比较的 baseline）；1 = 发现超阈值差异
（需要人工看一眼）；2 = 环境问题（未装 Pillow、目录不存在等）。
diff 子命令另有 --strict：baseline 目录缺失时按失败处理（exit 1）而不是
警告后跳过——回归 gate 场景下防止"删掉 baseline 就静默通过"。
"""
import argparse
import json
import os
import shutil
import sys

try:
    from PIL import Image, ImageChops
except ImportError:
    Image = None
    ImageChops = None

DEFAULT_THRESHOLD = 0.5  # 平均像素差异（0-255 灰度尺度）超过此值才算"明显变了"


def _diff_ratio(path_a, path_b):
    """返回 (mean_diff, changed_pixel_pct)：

    mean_diff：两张图逐像素灰度差的平均值（0=完全一致，255=天差地别），用来
    判断"整体上变了多少"；
    changed_pixel_pct：差异超过 10（灰度尺度，容忍抗锯齿/字体渲染的轻微抖动）
    的像素占比，用来判断"变化集中还是分散"——同样的 mean_diff，一大块新增的
    UI 元素跟满屏轻微色偏，观感完全不同，只看 mean_diff 容易漏判/误判。
    尺寸不一致时视为"结构性变化"，直接返回 (255.0, 1.0)（必然超阈值）。
    """
    img_a = Image.open(path_a).convert("RGB")
    img_b = Image.open(path_b).convert("RGB")
    if img_a.size != img_b.size:
        return 255.0, 1.0
    diff = ImageChops.difference(img_a, img_b).convert("L")
    hist = diff.histogram()
    total_px = img_a.size[0] * img_a.size[1]
    mean_diff = sum(i * c for i, c in enumerate(hist)) / max(total_px, 1)
    changed = sum(c for i, c in enumerate(hist) if i > 10)
    return mean_diff, changed / max(total_px, 1)


def cmd_snapshot(args):
    if not os.path.isdir(args.input):
        print(f"[visual-regression] 输入目录不存在：{args.input}", file=sys.stderr)
        sys.exit(2)
    pngs = [f for f in os.listdir(args.input) if f.lower().endswith(".png")]
    if not pngs:
        print(f"[visual-regression] {args.input} 下没有 PNG 文件，无法存为 baseline",
              file=sys.stderr)
        sys.exit(2)
    if os.path.isdir(args.output) and not args.force:
        print(f"[visual-regression] baseline 目录已存在：{args.output}\n"
              f"确认当前关键帧已人工看过没问题、要覆盖旧 baseline 的话加 --force",
              file=sys.stderr)
        sys.exit(2)
    os.makedirs(args.output, exist_ok=True)
    for name in pngs:
        shutil.copyfile(os.path.join(args.input, name), os.path.join(args.output, name))
    print(f"[visual-regression] 已存 {len(pngs)} 张关键帧为新 baseline -> {args.output}")


def cmd_diff(args):
    if Image is None:
        print("[visual-regression] 未安装 Pillow（pip install Pillow），"
              "无法做像素比对", file=sys.stderr)
        sys.exit(2)
    if not os.path.isdir(args.input):
        print(f"[visual-regression] 输入目录不存在：{args.input}", file=sys.stderr)
        sys.exit(2)
    if not os.path.isdir(args.baseline):
        # baseline 缺失在回归 gate 语义下是危险信号：删掉 baseline 目录就
        # 能让比对静默通过。默认仍 exit 0（首跑建基准前的正常路径），但
        # 警告必须醒目；--strict（CI/gate 场景）下缺失直接判失败。
        print(f"[visual-regression] !!! 警告：没有 baseline（{args.baseline} 不存在），"
              f"本次跳过像素比对——没有 baseline 时回归 gate 会静默通过，"
              f"请先跑一次 `visual_regression.py snapshot` 建立基准",
              file=sys.stderr)
        if args.strict:
            print("[visual-regression] --strict：baseline 缺失按失败处理（exit 1）",
                  file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    baseline_files = {f for f in os.listdir(args.baseline) if f.lower().endswith(".png")}
    current_files = {f for f in os.listdir(args.input) if f.lower().endswith(".png")}
    common = sorted(baseline_files & current_files)
    only_new = sorted(current_files - baseline_files)
    only_old = sorted(baseline_files - current_files)

    if not common:
        print("[visual-regression] baseline 与当前关键帧没有同名文件可比对"
              "（文件名/关键帧数量变了，跳过像素比对，这种结构性变化本身就该去看）")

    results = []
    flagged = []
    for name in common:
        mean_diff, changed_pct = _diff_ratio(
            os.path.join(args.baseline, name), os.path.join(args.input, name))
        is_flagged = mean_diff > args.threshold
        results.append({
            "file": name, "mean_diff": round(mean_diff, 3),
            "changed_pixel_pct": round(changed_pct * 100, 2), "flagged": is_flagged,
        })
        if is_flagged:
            flagged.append(name)

    print(f"[visual-regression] 比对 {len(common)} 帧（阈值 mean_diff > {args.threshold}）")
    for r in results:
        tag = "[DIFF]" if r["flagged"] else "[ok]  "
        print(f"  {tag} {r['file']:<40} mean_diff={r['mean_diff']:>6.2f} "
              f"changed_px={r['changed_pixel_pct']:>5.1f}%")
    if only_new:
        print(f"[visual-regression] baseline 里没有、当前新增的关键帧："
              f"{', '.join(only_new)}（新内容，无从比对，建议人工看一眼后收进 baseline）")
    if only_old:
        print(f"[visual-regression] baseline 里有、当前缺失的关键帧："
              f"{', '.join(only_old)}（可能改动删掉了某段，或抓帧参数变了）")

    if args.json:
        print(json.dumps({
            "threshold": args.threshold, "results": results,
            "only_in_current": only_new, "only_in_baseline": only_old,
        }, ensure_ascii=False))

    if flagged:
        print(f"\n[visual-regression] {len(flagged)} 帧超过阈值，建议用 Read 打开对比"
              f"一下（{args.input} vs {args.baseline}）再判断是预期改动还是回归 bug；"
              f"确认没问题后重跑 `snapshot --force` 更新 baseline。")
        sys.exit(1)
    print("\n[visual-regression] 未发现超阈值的像素差异。")


def main():
    parser = argparse.ArgumentParser(description="关键帧像素级视觉回归（baseline diff）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_snap = sub.add_parser("snapshot", help="把当前关键帧存为新 baseline")
    p_snap.add_argument("-i", "--input", required=True, help="关键帧 PNG 所在目录")
    p_snap.add_argument("-o", "--output", required=True, help="baseline 存放目录")
    p_snap.add_argument("--force", action="store_true", help="baseline 已存在时覆盖")
    p_snap.set_defaults(func=cmd_snapshot)

    p_diff = sub.add_parser("diff", help="当前关键帧与 baseline 逐像素比对")
    p_diff.add_argument("-i", "--input", required=True, help="当前关键帧 PNG 所在目录")
    p_diff.add_argument("-b", "--baseline", required=True, help="baseline 目录")
    p_diff.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"mean_diff 超过此值才标记为需要人工看（默认 {DEFAULT_THRESHOLD}）")
    p_diff.add_argument("--json", action="store_true", help="额外输出机器可读 JSON")
    p_diff.add_argument("--strict", action="store_true",
                        help="baseline 缺失时按失败处理（exit 1）而不是警告后跳过"
                             "（回归 gate 场景用，防止删掉 baseline 静默通过）")
    p_diff.set_defaults(func=cmd_diff)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
