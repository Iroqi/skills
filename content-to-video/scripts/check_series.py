#!/usr/bin/env python3
"""系列一致性检查：跨集参数 drift 检测（可选，纯本地）。

系列视频（chapters/chXX-<名称>/ 每集一个子目录）最大的隐性风险是
"跨集漂移"：某一集忘了开 flow、换了音色或主题——单集看没问题，
连着看风格突变，返工要重渲染。本脚本把所有集的关键字段摆到一起
对比，渲染前就能发现。

用法（在项目目录下）：
  python <skill>/scripts/check_series.py                # 扫 ./chapters/*/
  python <skill>/scripts/check_series.py -r chapters    # 指定系列根目录
  python <skill>/scripts/check_series.py -r .           # 扫当前目录（含两层子目录）

检查项：
  - segments_source.json: opening_title（系列名应恒定）、flow 叙事模式
  - production_report.json 的 params: theme / voice_id / speed / aspect /
    sub_mode / fps / quality（run.py 写入；旧格式报告缺 params 时跳过并提示）

只发现一个集时无可对比，直接通过。发现 drift 退出码 1。
"""
import argparse
import glob
import json
import os
import sys

# find_drifts 里"整个记录没有该字段"（源文件缺失/解析失败）的哨兵，
# 区别于"字段值为 None"（源 JSON 里手滑漏写该字段）——前者没有数据、
# 跳过不比；后者参与对比，漏写本身可能就是 drift。
_ABSENT = object()


def iter_episode_dirs(root):
    """返回含 segments_source.json 的集目录列表（root 自身 + 一层/两层子目录）。"""
    seen = []
    for pat in ("segments_source.json",
                os.path.join("*", "segments_source.json"),
                os.path.join("*", "*", "segments_source.json")):
        for p in sorted(glob.glob(os.path.join(root, pat))):
            d = os.path.dirname(os.path.abspath(p))
            if d not in seen:
                seen.append(d)
    return seen


def collect_episode(ep_dir):
    """读一个集目录的系列相关字段。文件缺失时对应部分为空，不抛错。"""
    rec = {"dir": ep_dir}
    src_path = os.path.join(ep_dir, "segments_source.json")
    if os.path.isfile(src_path):
        try:
            with open(src_path, "r", encoding="utf-8") as f:
                src = json.load(f)
            # flow 未写与显式 false 同为章节模式，规范化成布尔再比
            rec["opening_title"] = src.get("opening_title")
            rec["flow"] = bool(src.get("flow", False))
        except (OSError, json.JSONDecodeError) as e:
            rec["_source_error"] = str(e)
    # 固定两个路径之外再兜底扫一层：用户 -o 自定义输出目录时报告在
    # ep_dir/<自定义目录>/production_report.json，只认固定路径会让 drift
    # 检测静默失效（读不到报告 → params 没对比也不提示）。
    rpt_candidates = [os.path.join(ep_dir, "audio_output", "production_report.json"),
                      os.path.join(ep_dir, "production_report.json")]
    rpt_candidates += sorted(glob.glob(
        os.path.join(ep_dir, "*", "production_report.json")))
    for rpt in rpt_candidates:
        if os.path.isfile(rpt):
            try:
                with open(rpt, "r", encoding="utf-8") as f:
                    rpt_data = json.load(f)
                params = rpt_data.get("params")
                if isinstance(params, dict):
                    rec["params"] = params
            except (OSError, json.JSONDecodeError):
                pass
            break
    return rec


def find_drifts(episodes):
    """纯函数：给定 collect_episode 结果列表，返回 drift 描述列表。

    少于 2 集时无可对比返回 []。某集缺某字段（旧格式报告）时不参与
    该字段对比——只在有值的集之间互比，避免"新集有 params、旧集
    没有"被误报成 drift。opening_title 是例外：它是系列名，"部分集
    有、部分集漏写"本身就是 drift（手滑漏字段要在渲染前暴露出来），
    缺失以 <缺失> 参与对比；只有整个记录都没有该键时才跳过。
    """
    if len(episodes) < 2:
        return []
    drifts = []

    def _cmp(field, getter):
        vals = []
        for e in episodes:
            v = getter(e)
            if v is not None:
                vals.append((os.path.basename(e["dir"]), v))
        if len(vals) >= 2 and len({repr(v) for _, v in vals}) > 1:
            drifts.append(
                f"{field} 不一致: " + "; ".join(f"{d}={v!r}" for d, v in vals))

    def _cmp_required(field, getter):
        # "手滑漏字段"检测：字段值为 None（源 JSON 没写该字段，
        # collect_episode 会显式落成 None）也算一个值参与对比——部分集
        # 有、部分集没有本身就是 drift。整个记录没有该键（源文件缺失/
        # 解析失败，getter 返回 _ABSENT）的集仍跳过：没有数据，无从对比。
        vals = []
        for e in episodes:
            v = getter(e)
            if v is not _ABSENT:
                vals.append((os.path.basename(e["dir"]), v))
        if len(vals) >= 2 and len({repr(v) for _, v in vals}) > 1:
            drifts.append(
                f"{field} 不一致: " + "; ".join(
                    f"{d}={'<缺失>' if v is None else repr(v)}" for d, v in vals))

    _cmp_required("opening_title",
                  lambda e: e["opening_title"] if "opening_title" in e else _ABSENT)
    _cmp("flow", lambda e: e.get("flow"))
    for f in ("theme", "voice_id", "speed", "aspect", "sub_mode", "fps", "quality"):
        # None 表示用默认值（run.py 未显式传），跳过不比
        _cmp(f"params.{f}", lambda e, _f=f: (e.get("params") or {}).get(_f))
    return drifts


def main():
    ap = argparse.ArgumentParser(description="系列一致性检查（跨集参数 drift）")
    ap.add_argument("-r", "--root", default=os.path.join(".", "chapters"),
                    help="系列根目录（默认 ./chapters；集目录为其下子目录）")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        raise SystemExit(f"[check_series] 目录不存在: {root}")

    eps = [collect_episode(d) for d in iter_episode_dirs(root)]
    print(f"[check_series] {root}: 发现 {len(eps)} 个集目录")
    for e in eps:
        notes = []
        if "_source_error" in e:
            notes.append(f"segments_source.json 读取失败: {e['_source_error']}")
        if "params" not in e:
            notes.append("无 production_report params（旧格式报告或未用 run.py），跳过参数对比")
        print(f"  - {os.path.basename(e['dir'])}"
              + (f"（{'；'.join(notes)}）" if notes else ""))

    drifts = find_drifts(eps)
    if not drifts:
        print("[check_series] 一致性通过（或集数不足 2 无可对比）")
        return
    print(f"\n[check_series] 发现 {len(drifts)} 处跨集不一致：")
    for d in drifts:
        print(f"  [drift] {d}")
    sys.exit(1)


if __name__ == "__main__":
    main()
