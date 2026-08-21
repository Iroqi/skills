#!/usr/bin/env python3
"""Content-to-Video — 一键编排（可选，薄组合层）。

把第 3→5 步（TTS → 配图 → HTML → check → 渲染 → 校验）串成一条命令，
内部按依赖顺序调用各子脚本；不改变任何子脚本的独立性，只做组合与默认值。
日常一条命令：

  python scripts/run.py --source segments_source.json -o audio_output

参数：
  --source            segments_source.json 路径（必填）
  -o/--output         音频/中间产物输出目录（默认 audio_output）
  --project           HTML/渲染项目目录（默认 <output 同级>/hf-project）
  --no-images         跳过配图步骤（纯文字版）
  --until {tts,images,html,render}   跑到该步骤为止（用于调试）
  --no-verify         渲染后不跑 verify_render
  --no-resume         TTS 不用 --resume（默认启用）
  --aspect            画幅 landscape/vertical/portrait/both（默认 landscape）
  --sub-mode          字幕/内容呈现模式 verse/bar（默认按画幅：横屏 bar、竖屏 verse；
                      显式传值强制该模式）
  --theme             主题（默认 cream；可选值见 config/theme_registry.json，目前含 cream/dark/tech/alert）
  --fps               输出帧率（默认 24，官方支持 24/30/60）
  --quality           渲染质量 draft/standard/high（默认 standard）
  --workers           渲染抓帧 worker 数（默认 4，避免超出 V8 堆上限）
  --gpu               启用 GPU 硬件编码（NVENC/VideoToolbox/VAAPI/QSV，需本机有编码器）
  --no-check          跳过 hyperframes check --snapshots（仅重渲染且 HTML 未变时使用）
  --speed             语速倍率（透传给 pipeline）
  --voice-id          音色 ID（透传给 pipeline）
  --dry-run           只跑 pipeline --dry-run（不写任何文件）
"""
import argparse
import json
import os
import subprocess
import sys
import time
import datetime
import signal

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)
from render_watch import resolve_command, _try_kill  # noqa: E402  复用 npx/.cmd 的 Windows 解析与进程树收尾
from _theme import list_theme_names  # noqa: E402  --theme choices 与 registry.json 单一数据源同步
from _voices import list_voice_ids  # noqa: E402  --voice-id choices 与音色注册表单一数据源同步
from _audio import validate_speed  # noqa: E402  坏语速 fail-fast

# ── 制作报告：一次 run.py 跑完后，各步骤耗时/配图情况/跳过了什么散落在各
# 步骤自己的 stdout 里，人工翻起来很累。这里不改变任何步骤本身的行为，只是
# 在旁路记一份轻量流水账，跑完打印小结 + 写一份 production_report.json，
# 方便"这次跑得正不正常"一眼判断（例如：TTS 花了很久是不是网络问题、配图
# 是不是大面积没搜到而不是稿件写少了）。任何一步失败退出前也会尽量把已记录
# 的部分写盘，不指望"必须跑到最后才有报告"。
_REPORT = {"steps": [], "images": None, "skipped": []}
# 制作报告的 out 目录：main() 一解析出 -o/--output 就赋值
_REPORT_DIR = None
# --dry-run 承诺"不写任何文件"（见参数 help），旁路制作报告也不能例外
# ——失败路径同样只打印不落盘。main() 解析完参数就置 True。
_DRY_RUN = False
# 整个 run 的起始时刻：total_seconds 用真实墙钟，而不是把各步骤耗时直接
# 相加（TTS 与配图并行时两步各记各的全程，相加会大于真实墙钟）。
_RUN_T0 = None


def _total_seconds():
    """本次 run 的真实墙钟时长；_RUN_T0 未赋值时退回各步骤耗时之和。"""
    if _RUN_T0:
        return round(time.time() - _RUN_T0, 1)
    return round(sum(s["seconds"] for s in _REPORT["steps"]), 1)


def _write_report(path):
    if _DRY_RUN:
        return
    try:
        _REPORT["finished_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        _REPORT["total_seconds"] = _total_seconds()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_REPORT, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[run] 写制作报告失败（不影响主流程产出）：{e}", file=sys.stderr)


def _print_report_summary():
    print("\n" + "=" * 44)
    print("[run] 本次制作报告")
    print("=" * 44)
    for s in _REPORT["steps"]:
        tag = "OK  " if s["ok"] else "FAIL"
        print(f"  [{tag}] {s['name']:<28} {s['seconds']:>7.1f}s")
    total = _total_seconds()
    print(f"  {'合计':<34} {total:>7.1f}s")
    if _REPORT["images"] is not None:
        img = _REPORT["images"]
        if img.get("skipped_reason"):
            print(f"  配图：跳过（{img['skipped_reason']}）")
        else:
            print(f"  配图：{img['matched']}/{img['total']} 条内容已定稿"
                  + (f"，{img['missing']} 条待人工审阅/兜底" if img.get("missing") else ""))
    if _REPORT["skipped"]:
        print("  跳过的步骤：" + "；".join(_REPORT["skipped"]))
    print("=" * 44)


def _script(name):
    return os.path.join(SCRIPTS_DIR, name)


def _run(cmd, cwd=None, step_name=None):
    resolved = resolve_command(cmd)
    print(f"\n>>> {' '.join(resolved)}", flush=True)
    t0 = time.time()
    try:
        subprocess.run(resolved, cwd=cwd, check=True)
    except subprocess.CalledProcessError as e:
        if step_name:
            _REPORT["steps"].append(
                {"name": step_name, "seconds": round(time.time() - t0, 1), "ok": False})
            _write_report(_report_path())
        print(f"[run] 步骤失败（退出码 {e.returncode}）：{' '.join(resolved)}",
              file=sys.stderr)
        sys.exit(e.returncode or 1)
    except (FileNotFoundError, OSError) as e:
        # 命令本身起不来（可执行文件不存在/无权限等）：按步骤失败处理并
        # 报对人，不再裸抛 traceback
        if step_name:
            _REPORT["steps"].append(
                {"name": step_name, "seconds": round(time.time() - t0, 1), "ok": False})
            _write_report(_report_path())
        print(f"[run] 步骤失败（无法启动命令 {resolved[0]}）：{e}",
              file=sys.stderr)
        sys.exit(1)
    if step_name:
        _REPORT["steps"].append(
            {"name": step_name, "seconds": round(time.time() - t0, 1), "ok": True})


def _report_path():
    """制作报告写到哪：优先 out 目录（跟 timing_manifest.json 放一起），
    -o/--output 还没解析出来（极早期失败）时退回当前目录。"""
    return os.path.join(_REPORT_DIR, "production_report.json") if _REPORT_DIR \
        else "production_report.json"


def _image_coverage(manifest_path, images_json):
    """返回 (all_sids, missing_sids)：manifest 中 news/seg 段落的全部 sid，
    以及其中 images.json 里还没配图的那些。

    之前 run.py 里同时存在两份"怎么从 manifest 里挑出 news/seg 段落 id"的
    逻辑——一份在这个函数内部，一份在 main() 里为了统计总数又重新拼了一遍
    （还多读了一次 manifest 文件）。把"news/seg 前缀是配图相关段落"这个
    命名约定收敛到一个地方，main() 只管要总数/缺图列表，不需要知道这个
    约定长什么样——manifest 的 id 命名规则改了，也只用改这一处。
    """
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    sids = [seg.get("id", "") for seg in manifest.get("segments", [])
            if (seg.get("id") or "").startswith(("news", "seg"))]
    if not sids:
        return [], []
    if not os.path.isfile(images_json):
        return sids, sids
    with open(images_json, "r", encoding="utf-8") as f:
        mapping = json.load(f)
    missing = [s for s in sids if s not in mapping]
    return sids, missing


def main():
    global _REPORT_DIR, _REPORT, _DRY_RUN, _RUN_T0
    # 同一进程里重复调用 main()（import run 后直接调用的测试/嵌入式用法）
    # 时，模块级 _REPORT 不能累积上一次的 steps/skipped——每次都从空白
    # 报告开始。
    _REPORT = {"steps": [], "images": None, "skipped": []}
    _DRY_RUN = False
    _RUN_T0 = time.time()
    parser = argparse.ArgumentParser(description="信源转视频一键编排（薄组合层）")
    parser.add_argument("--source", required=True, help="segments_source.json 路径")
    parser.add_argument("-o", "--output", default="audio_output",
                        help="音频/中间产物输出目录（默认 audio_output）")
    parser.add_argument("--project", default=None,
                        help="HTML/渲染项目目录（默认 <output 同级>/hf-project）")
    parser.add_argument("--no-images", action="store_true", help="跳过配图")
    parser.add_argument("--refresh-images", action="store_true",
                        help="配图不启用 --resume（重新搜索/下载全部图片）")
    parser.add_argument("--search-sids", default=None,
                        help="只对这些段落跑搜图（逗号分隔 sid，如 'seg1,seg3'，"
                             "透传给 search_images.py --sids）——按第 4 步路由"
                             "只有部分段落走方式 A 时用，其余段落走方式 B/C/D，"
                             "由缺图拦截兜住")
    parser.add_argument("--until", choices=["tts", "images", "html", "render"],
                        default="render", help="跑到该步骤为止（默认 render）")
    parser.add_argument("--no-verify", action="store_true", help="渲染后跳过校验")
    parser.add_argument("--no-resume", action="store_true", help="TTS 不用 --resume")
    parser.add_argument("--aspect", default="landscape",
                        choices=["landscape", "vertical", "portrait", "both"],
                        help="画幅：landscape=1920x1080 横屏（默认），"
                             "vertical=1080x1920 竖屏，portrait=1080x1440 "
                             "紧凑竖屏（3:4，抖音等平台全屏播放零裁切），"
                             "both=横竖两版一次产出")
    parser.add_argument("--theme", default="cream", choices=list_theme_names())
    parser.add_argument("--sub-mode", default=None,
                        choices=["verse", "bar"],
                        help="字幕/内容呈现模式（透传给 gen_hyperframes.py），"
                             "默认按画幅取：横屏 bar（经典底部字幕条 + 正文"
                             "要点卡片）、竖屏家族 verse（歌词式句子流，竖屏"
                             "唯一模式——bar 会报错）。横屏显式传 verse = "
                             "bar 布局 + 正文卡位置换滚动句子流")
    parser.add_argument("--fps", type=int, default=24,
                        help="输出帧率（默认 24）")
    parser.add_argument("--quality", default="standard",
                        choices=["draft", "standard", "high"],
                        help="渲染质量（默认 standard）")
    parser.add_argument("--workers", type=int, default=4,
                        help="渲染抓帧 worker 数（默认 4）")
    parser.add_argument("--gpu", action="store_true",
                        help="启用 GPU 硬件编码（NVENC/VideoToolbox/VAAPI/QSV）")
    parser.add_argument("--no-check", action="store_true",
                        help="跳过 hyperframes check --snapshots（重渲染且 HTML 未变时用）")
    parser.add_argument("--speed", type=float, default=None)
    parser.add_argument("--voice-id", default=None, choices=list_voice_ids())
    parser.add_argument("--dry-run", action="store_true",
                        help="只跑 pipeline --dry-run（不写文件）")
    args = parser.parse_args()
    _DRY_RUN = args.dry_run

    if args.speed is not None:
        try:
            validate_speed(args.speed)
        except ValueError as e:
            parser.error(str(e))

    # sub_mode 默认按画幅解析（与 gen_hyperframes.py 的函数级解析同规则）。
    # 单画幅在这里先解析成显式值：制作报告 params 与 --sub-mode 透传都记
    # 解析后的结果，check_series 的 drift 对比看到的是实际生效的模式。
    # --aspect both 保持 None 透传——单值解析会把两画幅都强制成同一模式
    # （横屏也变 verse），透传则由 gen_hyperframes 按画幅各自解析
    # （横屏 bar + 竖屏 verse）；报告 params 记 None，check_series 按
    # "默认值跳过不比" 处理（与 speed/voice_id 同语义）。
    # 竖屏家族（vertical/portrait）只有 verse：显式 bar 直接报错（both
    # 也不行——竖屏版会失败）。
    if args.sub_mode == "bar" and args.aspect != "landscape":
        parser.error(
            f"--aspect {args.aspect} 不支持 --sub-mode bar：竖屏家族只有 "
            "verse（歌词式句子流），bar 的底部字幕条 + 正文卡在竖屏高度内"
            "放不下。如需 bar 形态请用 --aspect landscape。")
    if args.sub_mode is None and args.aspect != "both":
        args.sub_mode = ("bar" if args.aspect == "landscape" else "verse")

    out = os.path.abspath(args.output)
    project = os.path.abspath(args.project) if args.project else \
        os.path.join(os.path.dirname(out) or ".", "hf-project")
    _REPORT_DIR = out
    _REPORT["source"] = os.path.abspath(args.source)
    _REPORT["output"] = out
    _REPORT["project"] = project
    _REPORT["started_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    # 制作参数进报告——check_series.py 靠它对比各集实际用的
    # theme/voice/speed 等，跨集漂移不用翻命令行历史就能发现。
    # speed/voice_id 为 None 表示用默认值，检查侧跳过不比。
    _REPORT["params"] = {
        "theme": args.theme,
        "aspect": args.aspect,
        "sub_mode": args.sub_mode,
        "fps": args.fps,
        "quality": args.quality,
        "speed": args.speed,
        "voice_id": args.voice_id,
    }

    # ── 第 3 步：TTS ──────────────────────────────────────────────
    tts_cmd = [sys.executable, _script("pipeline.py"),
               "--source", args.source, "-o", out]
    if not args.no_resume:
        tts_cmd.append("--resume")
    if args.speed is not None:
        tts_cmd += ["--speed", str(args.speed)]
    if args.voice_id:
        tts_cmd += ["--voice-id", args.voice_id]
    if args.dry_run:
        tts_cmd.append("--dry-run")
    # ── 第 3/4 步：TTS + 配图 ─────────────────────────────────────
    # 两步都只依赖 segments_source.json，互不等待对方的实际产物——配图标题走
    # search_images.py --source 直接从稿件取（不必等 timing_manifest.json），
    # id 分配规则跟 pipeline.py --source 最终写进 manifest 的完全一致（见
    # _titles.py 的 extract_titles_from_segments_source 说明），因此能真的
    # 并行跑，省下配图搜索那部分的墙钟时间。只有明确不需要配图这一步时
    # （--no-images / --until tts / --dry-run / 没配 STEPFUN_API_KEY）才退回
    # 纯顺序执行。
    manifest = os.path.join(out, "timing_manifest.json")
    images_json = os.path.join(project, "images.json")

    want_images = not args.dry_run and not args.no_images and args.until != "tts"
    has_key = False
    if want_images:
        try:
            sys.path.insert(0, SCRIPTS_DIR)
            from _env import get_key
            has_key = bool(get_key("STEPFUN_API_KEY"))
        except Exception:
            has_key = False

    if want_images and has_key:
        images_cmd = [sys.executable, _script("search_images.py"),
                      "--source", args.source, "-o", os.path.join(project, "images")]
        if not args.refresh_images:
            images_cmd.append("--resume")
        if args.search_sids:
            images_cmd += ["--sids", args.search_sids]
        resolved_tts = resolve_command(tts_cmd)
        resolved_images = resolve_command(images_cmd)
        print(f"\n>>> [并行启动] {' '.join(resolved_tts)}", flush=True)
        print(f">>> [并行启动] {' '.join(resolved_images)}", flush=True)
        _t0 = time.time()
        p_tts = subprocess.Popen(resolved_tts, start_new_session=True)
        p_images = subprocess.Popen(resolved_images, start_new_session=True)
        # POSIX 下 start_new_session 让子进程脱离本进程组：Ctrl+C 只中断
        # run.py，TTS/配图子进程会继续跑（继续烧 TTS 时间）。装 SIGINT
        # handler，中断时先收掉两棵子进程树再退出。Windows 无 SIGINT
        # 信号语义（CTRL_C_EVENT 走另一条路），跳过。
        if os.name != "nt":
            def _on_sigint(signum, frame):
                for _p in (p_tts, p_images):
                    if _p.poll() is None:
                        _try_kill(_p)
                sys.exit(130)
            try:
                signal.signal(signal.SIGINT, _on_sigint)
            except (ValueError, OSError):
                pass
        # 轮询两个子进程而不是先后阻塞 wait()：一来各步耗时在"自身退出"
        # 的时点测量（先后 wait 会把先完成那步的耗时记成"等到另一个也结
        # 束"，两步相加还大于真实墙钟）；二来任一子进程非零退出就立刻收
        # 掉另一个（进程树）并传播失败退出，不再陪跑完剩下的那个。
        tts_ret = images_ret = None
        tts_elapsed = images_elapsed = 0.0
        fail_cmd = fail_rc = None
        while tts_ret is None or images_ret is None:
            if tts_ret is None and p_tts.poll() is not None:
                tts_ret = p_tts.returncode
                tts_elapsed = time.time() - _t0
                if tts_ret != 0 and fail_rc is None:
                    fail_cmd, fail_rc = resolved_tts, tts_ret
            if images_ret is None and p_images.poll() is not None:
                images_ret = p_images.returncode
                images_elapsed = time.time() - _t0
                if images_ret != 0 and fail_rc is None:
                    fail_cmd, fail_rc = resolved_images, images_ret
            if fail_rc is not None:
                for p in (p_tts, p_images):
                    if p.poll() is None:
                        _try_kill(p)
                        try:
                            p.wait(timeout=15)
                        except Exception:
                            pass
                if tts_ret is None:
                    tts_ret = p_tts.returncode
                    tts_elapsed = time.time() - _t0
                if images_ret is None:
                    images_ret = p_images.returncode
                    images_elapsed = time.time() - _t0
                break
            if tts_ret is None or images_ret is None:
                time.sleep(0.2)
        _REPORT["steps"].append({"name": "TTS（与配图并行）", "seconds": round(tts_elapsed, 1),
                                 "ok": tts_ret == 0})
        _REPORT["steps"].append({"name": "配图搜索（与 TTS 并行）",
                                 "seconds": round(images_elapsed, 1), "ok": images_ret == 0})
        if fail_rc is not None:
            _write_report(_report_path())
            print(f"[run] 步骤失败（退出码 {fail_rc}）：{' '.join(fail_cmd)}",
                  file=sys.stderr)
            sys.exit(fail_rc)
    else:
        _run(tts_cmd, step_name="TTS")
        if want_images and not has_key:
            if os.path.isfile(images_json):
                # 无 Key 但已有 images.json（手动走方式 B/C/D 补的图）：
                # 不是纯文字版兜底——稍后照常带 --images 渲染已定稿配图
                print("[run] 未找到 STEFUN_API_KEY，跳过搜图；"
                      "检测到已有 images.json，将使用其中已定稿的配图渲染",
                      flush=True)
            else:
                print("[run] 未找到 STEFUN_API_KEY，跳过配图（纯文字版兜底）",
                      flush=True)
            _REPORT["images"] = {"skipped_reason": "未配置 STEFUN_API_KEY，纯文字版兜底"}
            _REPORT["skipped"].append("配图搜索（无 STEFUN_API_KEY）")
        elif args.no_images:
            _REPORT["images"] = {"skipped_reason": "--no-images（纯文字版）"}
            _REPORT["skipped"].append("配图（--no-images）")

    # images.json 是否可用：有 Key 时由搜图落盘；无 Key 时手动补图（方式
    # B/C/D，不依赖 STEFUN_API_KEY）同样落这里。只要本次想要配图且映射已
    # 存在，第 5 步就带 --images 渲染——此前只在有 Key 的并行分支里赋值，
    # 无 Key 手动补图后重跑会被静默无视、照样出纯文字版。
    has_images = want_images and os.path.isfile(images_json)

    if args.dry_run or args.until == "tts":
        if args.until == "tts":
            _REPORT["skipped"].append("html/render（--until tts）")
        # dry-run 承诺"不写任何文件"：_write_report 内部短路不落盘，只打
        # 印摘要（非 dry-run 的 --until tts 仍是写报告 + 打印）。
        _write_report(_report_path())
        _print_report_summary()
        return

    # 覆盖率统计：有 Key（搜图跑过）或 images.json 已存在（无 Key 时手动走
    # 方式 B/C/D 补图）都算。不能只看 has_images——search_images.py 的 dump
    # 模式（不带 --pick）只写 candidates.json、不写 images.json，首跑时
    # images.json 必然不存在，只看它会静默跳过缺图拦截、直接渲染无图成片；
    # _image_coverage 对"images.json 不存在"已按"全部缺图"处理。
    missing = []
    if want_images and (has_key or has_images):
        # "不要瞎配图"：缺图的段落需要当前对话模型审阅候选并 --pick，
        # 或改用 ImageGen 生图。
        all_sids, missing = _image_coverage(manifest, images_json)
        _REPORT["images"] = {
            "total": len(all_sids), "matched": len(all_sids) - len(missing),
            "missing": len(missing)}

    # --until images：配图搜索跑完即正常结束（exit 0）。必须放在缺图拦截
    # 之前——调试意图是"看看这轮搜到了哪些候选/覆盖率如何"，被 exit 2
    # 劫持就和"要渲染到底但缺图"混成了同一种失败。
    if args.until == "images":
        _REPORT["skipped"].append("html/render（--until images）")
        _write_report(_report_path())
        _print_report_summary()
        return

    if want_images and missing:
        if has_key:
            # 纯独立审阅：run.py 走到缺图分支时，不再生成拼图，而是把每个缺图
            # 段落的候选原图全路径直接列在提示里，让 agent 逐张、全分辨率 Read
            # 判断图↔题贴合度（缩略图/拼图会漏掉水印、角标、图内烧字等细节）。
            candidates_dir = os.path.join(project, "images")
            cand_json = os.path.join(candidates_dir, "candidates.json")
            review_lines = []
            try:
                if os.path.isfile(cand_json):
                    with open(cand_json, encoding="utf-8") as _f:
                        cj = json.load(_f)
                else:
                    cj = {}
                for sid in missing:
                    entry = cj.get(sid)
                    if not entry:
                        continue
                    title = entry.get("title", "")
                    files = [c.get("file") for c in entry.get("candidates", []) if c.get("file")]
                    if not files:
                        continue
                    paths = "  ".join(os.path.join(candidates_dir, f) for f in files)
                    review_lines.append(f"  · {sid}（{title}）候选原图：{paths}")
            except Exception:
                review_lines = []
            if not review_lines:
                # 兜底：candidates.json 缺失时直接枚举 images 目录里的候选文件
                import glob as _glob
                cands = sorted(_glob.glob(os.path.join(candidates_dir, "*_cand*")))
                if cands:
                    review_lines.append("  · 候选原图文件："
                                        + "  ".join(cands))

            if review_lines:
                review_hint = ("请逐张 Read 下列候选原图（全分辨率，不要看缩略图）判断"
                               "图↔题贴合度：标题党 / 带平台角标(如 B站、什么值得买) / "
                               "图内烧字 / 水印图一律不采用，改走 ImageGen 生图。")
            else:
                review_hint = (f"请用当前对话窗口的模型逐张 Read {candidates_dir} 下的"
                               f"候选原图（全分辨率）审阅，或参考 {cand_json}。")

            msg = ("[run] 以下段落还没有定稿配图：" + ", ".join(missing) + "。\n"
                   + review_hint + "\n")
            if review_lines:
                msg += "\n".join(review_lines) + "\n"
            msg += ("然后执行：\n"
                    f"  python scripts/search_images.py -o {candidates_dir} --resume "
                    f"--pick \"seg1:2,seg2:0\"\n"
                    "（0=不采用，改用 ImageGen 生图；--resume 保留 images.json 里"
                    "已定稿的其它条目，多轮补图不会互相覆盖）；完成后重跑本命令。")
            print(msg, file=sys.stderr)
        else:
            # 无 Key 的手动补图流程（方式 B/C/D）没有搜图候选可审阅，拦截
            # 提示直接指路生图/图表，不引导去跑需要 Key 的 search_images。
            print("[run] 以下段落还没有定稿配图：" + ", ".join(missing) + "。\n"
                  "请按第 4 步用 ImageGen 生图（方式 B）或图表（方式 C）补图："
                  f"图片放进 {os.path.join(project, 'images')}，并在 {images_json} "
                  "写好该段映射后重跑本命令（方式 B/C/D 均不依赖 STEFUN_API_KEY）。",
                  file=sys.stderr)
        _write_report(_report_path())
        sys.exit(2)

    # （--until images 的提前返回已上移到缺图拦截之前：配图搜索完成即
    #  正常 return，不再被缺图 exit 2 劫持。）

    # ── 第 5 步 a：生成 composition HTML ──────────────────────────
    html_cmd = [sys.executable, _script("gen_hyperframes.py"),
                "-m", manifest, "-o", os.path.join(project, "index.html"),
                "--aspect", args.aspect, "--theme", args.theme]
    if args.sub_mode:
        # None（--aspect both 未显式指定）不传该旗标：gen_hyperframes
        # 按画幅各自解析默认模式
        html_cmd += ["--sub-mode", args.sub_mode]
    if has_images:
        html_cmd += ["--images", images_json]
    _run(html_cmd, step_name="生成 HTML")

    # 不设独立的版式几何校验步骤（puppeteer 量坐标那类）：正常制作中
    # 模板/布局不变，几何溢出类问题罕见，check --snapshots 关键帧 +
    # agent 逐帧视觉审阅（强制步骤）是同一道防线；为此维护一条 Node
    # 依赖链不值（打包遗漏、环境漂移都从它来）。

    if args.until == "html":
        _REPORT["skipped"].append("check/render（--until html）")
        _write_report(_report_path())
        _print_report_summary()
        return

    # ── 第 5 步 b：检查 HTML + 抓取标注关键帧（快速 QA，不渲染整片）──
    if not args.no_check:
        def _check(step_label):
            _run(["npx", "hyperframes", "check", "--snapshots"], cwd=project,
                 step_name=step_label)

        if args.aspect == "both":
            # hyperframes check 要求项目只有一个根 composition（多个根级
            # HTML 带 data-composition-id 会报 multiple_root_compositions
            # 错——运行时把两个入口都当播放起点，音频双放）。而 --aspect
            # both 恰好生成 index.html + index.vertical.html 两个根；check
            # CLI 又不像 render 那样支持
            # -c 指定入口，只能分两轮跑：每轮把另一画幅的 HTML 改名成
            # .disabled（不再匹配 *.html glob）临时隔离，try/finally 保证
            # 无论成败都恢复。快照按画幅分目录保留（snapshots.landscape/
            # snapshots.vertical），供第 6 步逐帧审阅两版画面。
            # 竖屏轮还有第二层约束：check 只认 index.html 这个文件名作
            # 入口（index.vertical.html 即使是项目里唯一的根也报 No
            # composition found），所以竖屏轮是把 vertical HTML 临时改名
            # 成 index.html 顶替上场，跑完再换回来。
            _idx = os.path.join(project, "index.html")
            _vhtml = os.path.join(project, "index.vertical.html")
            _snaps = os.path.join(project, "snapshots")

            def _isolate(html_path):
                os.replace(html_path, html_path + ".disabled")

            def _restore(html_path):
                os.replace(html_path + ".disabled", html_path)

            def _keep_snapshots(tag):
                if os.path.isdir(_snaps):
                    _dest = f"{_snaps}.{tag}"
                    if os.path.isdir(_dest):
                        import shutil
                        shutil.rmtree(_dest)
                    os.replace(_snaps, _dest)

            _isolate(_vhtml)
            try:
                _check("check --snapshots（横屏）")
                _keep_snapshots("landscape")
            finally:
                _restore(_vhtml)
            _isolate(_idx)
            os.replace(_vhtml, _idx)  # vertical 顶替 index.html 名义
            try:
                _check("check --snapshots（竖屏）")
                _keep_snapshots("vertical")
            finally:
                os.replace(_idx, _vhtml)
                _restore(_idx)
        else:
            _check("check --snapshots")
    else:
        _REPORT["skipped"].append("check --snapshots（--no-check）")

    # ── 第 5 步 c：渲染 ───────────────────────────────────────────
    # --aspect both 时 gen_hyperframes 已产出 index.html + index.vertical.html
    # 两个画幅，这里对应渲染两个成片（out.mp4 / out.vertical.mp4）并各自
    # 校验，报告步骤名区分横竖屏；--no-check/--no-verify 对两个成片一视
    # 同仁。渲染目标是 (HTML 路径, 成片路径, 步骤名后缀, 期望分辨率)。
    if args.aspect == "both":
        render_targets = [
            (os.path.join(project, "index.html"),
             os.path.join(project, "out.mp4"), "横屏", "1920x1080"),
            (os.path.join(project, "index.vertical.html"),
             os.path.join(project, "out.vertical.mp4"), "竖屏", "1080x1920"),
        ]
    elif args.aspect in ("vertical", "portrait"):
        # 竖屏家族单画幅：vertical=1080x1920，portrait=1080x1440（3:4）
        _vh = "1080x1440" if args.aspect == "portrait" else "1080x1920"
        render_targets = [(os.path.join(project, "index.html"),
                           os.path.join(project, "out.mp4"), "", _vh)]
    else:
        render_targets = [(os.path.join(project, "index.html"),
                           os.path.join(project, "out.mp4"), "", "1920x1080")]

    final_mp4s = []
    for html_path, out_mp4, label, expect_size in render_targets:
        render_cmd = [sys.executable, _script("render_watch.py"), "-o", out_mp4, "--",
                      "npx", "hyperframes", "render", "-o", out_mp4,
                      "--quality", args.quality, "--fps", str(args.fps),
                      "--workers", str(args.workers)]
        if os.path.basename(html_path) != "index.html":
            # 竖屏版是独立的 composition 文件；不指定的话 hyperframes 默认
            # 渲染项目根的 index.html（横屏版）
            render_cmd += ["-c", os.path.basename(html_path)]
        if args.gpu:
            render_cmd.append("--gpu")
        _run(render_cmd, cwd=project,
             step_name=f"渲染（{label}）" if label else "渲染")
        final_mp4s.append(out_mp4)

    # ── 第 5 步 d：校验成片 ───────────────────────────────────────
    if not args.no_verify:
        # 画幅/帧率按渲染目标传给 verify_render：竖屏 1080x1920、横屏
        # 1920x1080，both 时两个成片各自对应
        for _, out_mp4, label, expect_size in render_targets:
            _run([sys.executable, _script("verify_render.py"),
                  "-f", out_mp4, "-m", manifest,
                  "--expect-size", expect_size,
                  "--expect-fps", str(args.fps)],
                 step_name=f"校验成片（{label}）" if label else "校验成片")
    else:
        _REPORT["skipped"].append("校验成片（--no-verify）")
    print("\n[run] 完成。成片：", "、".join(final_mp4s), flush=True)
    _write_report(_report_path())
    _print_report_summary()


if __name__ == "__main__":
    main()
