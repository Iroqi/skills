#!/usr/bin/env python3
"""离线集成测试：串起"稿件 → manifest → HTML → 导出 → 校验"这条确定性链路，
不依赖真实 TTS/StepFun/ImageGen API、不需要网络、不需要 Chrome/Hyperframes 渲染
环境，几秒钟跑完、零 API 花费。

## 这个脚本解决什么问题

`evals.json` 的 `_readme` 里说得很清楚：这些测试用例是 prompt 层面的行为评估
素材，没接断言逻辑，因为"跑通一次完整流程需要真实 API/网络/npx 拉取 Hyperframes
CLI，成本和副作用较高"。这仍然成立——`basic_daily_run`/`no_recent_news_fallback`/
`all_images_rejected`/`non_chinese_input_rejected` 等条目本质是**agent 行为
评估**（"给定这个 prompt，agent 会不会做出正确的取材/降级/拒绝判断"），没有
脚本能替 agent 做这类语义判断，仍然需要另一个 agent 实例（作为 grader）在 fresh
thread 里 forward-test 一遍——这是给 AI 用的评估，不是给真人手动勾的清单。
（当前条数以 dev/evals.json 为准，不要在本注释里写死数字——历史上就漂移过。）

但 `evals.json` 建议的验证维度里，有三条其实是纯机械检查，不需要语义判断，
可以在没有真实 API/Chrome 的环境里离线验证：

  (1) 输出 mp4 是否存在且时长与音频总长匹配   → 本脚本用纯 ffmpeg 合成一个
      时长可控的黑屏测试视频代替真实渲染，跑 verify_render.py 校验
  (2) 编码是否为 H.264/AAC                    → 同上，合成视频时故意用这套编码
  (4) segments_source.json 条数在 5-8 条范围内 → 纯字段计数，不需要语义判断

  (3) 抽帧截图确认无裂图/文字溢出 —— 本质是视觉语义判断，这里依然做不到，
      不冒充能自动化，继续由 agent 在真实渲染环境里用视觉确认。

本脚本额外验证两层 `evals.json` 覆盖不到的东西：

1. 稿件写完之后、真调 TTS/图片 API 之前，脚本链路本身是否完整跑得通——用
   静音代替 TTS 配音（`_audio.py` 的 `generate_silence`，pipeline.py 自己也用
   同一个函数处理句间停顿，不是本脚本现造的东西），换出一份字段结构与真实
   `timing_manifest.json` 完全一致的 manifest，再喂给 `gen_hyperframes.py`/
   `export_extras.py`/`verify_render.py` 走一遍真实调用（真子进程，不是 mock
   断言），能提前抓住"改了某个脚本的输出字段，另一个脚本没跟着改"这类跨脚本
   契约问题。
2. `run.py` 把第 3 步(TTS)和第 4 步(配图)并行跑的编排逻辑——用两个睡眠时长
   不同的假脚本代替真 `pipeline.py`/`search_images.py`，
   用总耗时反推两个子进程是不是真的同时起（而非伪装成并行、实际顺序执行），
   并验证任一步失败时退出码能正确传播，不会被并发结构吞掉。

## 用法

  python dev/run_eval.py

不需要参数、不需要任何 API Key、不会访问网络。退出码 0=全过，非 0=有失败项。
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile

DEV_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(os.path.dirname(DEV_DIR), "scripts")
sys.path.insert(0, SCRIPTS_DIR)

from _ffmpeg import get_ffmpeg  # noqa: E402
from _audio import generate_silence, concat_audio, measure_duration  # noqa: E402
from _script_utils import split_sentences  # noqa: E402
from _contracts import (validate_segments_source, estimate_sentence_seconds,  # noqa: E402
                        DEFAULT_CHARS_PER_SEC)

RESULTS = []
# 环境缺能力（而非本技能有回归）时记到这里：不计入 pass/fail，不把环境问题
# 伪装成失败，也不伪装成通过——维护者一眼能分辨"没跑"和"跑了没过"。
SKIPPED = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    mark = "OK" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))
    return cond


def skip(name, reason):
    SKIPPED.append((name, reason))
    print(f"  [SKIP] {name} — {reason}")


# ── 对应 evals.json 维度 (4)：segments 条数在 5-8 条范围内的机械检查 ──────
# 这条本身不需要语义判断，是纯计数；真正"该不该挑这 5-8 条"是语义判断，
# 那部分仍然是 evals.json 里 agent 行为评估要覆盖的，这里不重复。
def check_segment_count(source, expect_range=(5, 8)):
    n = len(source.get("segments", []))
    lo, hi = expect_range
    return lo <= n <= hi, n


# ── 构造一份字段结构与 pipeline.py 真实产出完全一致的 timing_manifest.json，
# 只是用静音代替 TTS 配音——省下真实 TTS API 调用，但走的是同一套
# generate_silence/concat_audio/measure_duration（pipeline.py 自己也用这三个
# 函数），不是另起一套 mock 逻辑，跟真实产物的字段/类型不会跑偏。
def build_fake_manifest(source, out_dir, gap=0.3, chars_per_sec=DEFAULT_CHARS_PER_SEC):
    ffmpeg_path = get_ffmpeg()
    sentences_dir = os.path.join(out_dir, "sentences")
    os.makedirs(sentences_dir, exist_ok=True)

    manifest_sentences = []
    grouped_segments = []
    idx = 0
    audio_files = []

    def emit_block(text, speed):
        nonlocal idx
        out = []
        for s in split_sentences(text):
            dur = round(estimate_sentence_seconds(s, chars_per_sec, speed or 1.0), 3)
            wav_path = os.path.join(sentences_dir, f"s{idx:04d}.wav")
            generate_silence(ffmpeg_path, max(dur, 0.05), wav_path)
            audio_files.append(wav_path)
            entry = {"index": idx, "text": s, "start_time": 0.0, "duration": dur}
            manifest_sentences.append(entry)
            out.append(entry)
            idx += 1
        return out

    emit_block(source.get("opening", ""), source.get("opening_speed"))
    for i, seg in enumerate(source.get("segments", []), 1):
        seg_sents = emit_block(seg.get("text", ""), seg.get("speed"))
        grouped_segments.append({
            "id": seg.get("id", f"news{i}"),
            "title": seg.get("title", f"segment{i}"),
            "tagline": seg.get("tagline") or "补充阅读",
            "body": seg.get("body", ""),
            "accent": seg.get("accent", "#3b82f6"),
            "sentences": seg_sents,
        })
    emit_block(source.get("closing", ""), source.get("closing_speed"))

    if not audio_files:
        raise ValueError("没有任何句子，无法构造 manifest（segments 全空？）")

    combined_path = os.path.join(out_dir, "combined.wav")
    if not concat_audio(ffmpeg_path, audio_files, gap, combined_path):
        raise RuntimeError("concat_audio 失败——检查 ffmpeg 是否可用")
    total_dur = measure_duration(ffmpeg_path, combined_path)

    cumulative = 0.0
    for i, sd in enumerate(manifest_sentences):
        sd["start_time"] = round(cumulative, 3)
        cumulative += sd["duration"]
        if i < len(manifest_sentences) - 1:
            cumulative += gap

    manifest = {
        "sentences": manifest_sentences,
        "total_duration": round(total_dur, 3),
        "gap": gap,
        "voice_id": "mock-silence",
        "combined_audio": os.path.abspath(combined_path),
        "segments": grouped_segments,
    }
    manifest_path = os.path.join(out_dir, "timing_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest_path, manifest


# ── 对应 evals.json 维度 (1)(2)：不经过 Hyperframes/Chrome，直接用 ffmpeg
# 合成一段时长匹配、编码为 H.264+AAC 的黑屏测试视频，喂给 verify_render.py。
# 校验的是 verify_render.py 的判断逻辑本身，不是真实画面内容——画面对不对
# 是维度 (3)，仍然需要人看，这里不冒充能测。
def pick_h264_encoder(ffmpeg_path):
    """挑一个本机可用的 H.264 编码器，返回编码器名；一个都没有时返回 None。

    写死 libx264 会让"ffmpeg 能跑但不含 libx264"这类环境（minimal 构建、
    部分 Docker/conda 包）把环境限制报成本技能回归。这里按可用性降序回退，
    拿不到编码器列表时退回 libx264（让真正的报错从合成那一步如实冒出来）。
    """
    try:
        r = subprocess.run([ffmpeg_path, "-hide_banner", "-encoders"],
                           capture_output=True, text=True, timeout=30)
        encoders = r.stdout or ""
    except Exception:
        return "libx264"
    for enc in ("libx264", "libopenh264", "h264_nvenc", "h264_qsv",
                "h264_vulkan", "h264_vaapi", "h264_amf", "h264_v4l2m2m"):
        if f" {enc} " in encoders:
            return enc
    return None


def build_fake_render(ffmpeg_path, audio_path, duration, out_path):
    """返回 True=合成成功 / False=脚本链路问题 / None=环境缺编码器（应 SKIP）。

    失败时把 ffmpeg 的 stderr 尾部打出来：吞掉报错的话，维护者只能看到一行
    "[FAIL] 测试视频合成成功"，无从判断是回归还是本机 ffmpeg 少编码器。
    """
    enc = pick_h264_encoder(ffmpeg_path)
    if enc is None:
        return None
    r = subprocess.run([
        ffmpeg_path, "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=320x240:d={duration}",
        "-i", audio_path,
        "-c:v", enc, "-c:a", "aac",
        "-shortest", out_path,
    ], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60)
    if r.returncode != 0 or not os.path.isfile(out_path):
        print(f"      合成失败（编码器 {enc}，退出码 {r.returncode}）：", flush=True)
        for line in (r.stderr or "").strip().splitlines()[-5:]:
            print(f"        {line}", flush=True)
        return False
    return True


FIXTURE = {
    "opening": "大家好，欢迎收看今天的AI日报。",
    "closing": "感谢收看，我们明天见。",
    "segments": [
        {"title": "A公司发布新模型", "text": "第一条内容简介。这里补一句细节。"},
        {"title": "B公司完成新一轮融资", "text": "第二条内容简介。"},
        {"title": "C团队公布研究成果", "text": "第三条内容简介。这里也补一句。"},
        {"title": "D平台上线新功能", "text": "第四条内容简介。"},
        {"title": "E机构发布行业报告", "text": "第五条内容简介。"},
    ],
}


def main():
    print("=== dev/run_eval.py：离线集成测试（不调用真实 API，不需要 Chrome）===\n")

    print("[1] segments_source.json 结构校验 + 条数机械检查")
    source = validate_segments_source(FIXTURE)
    ok, n = check_segment_count(source)
    check("segments 条数落在 5-8 条范围内", ok, f"实际 {n} 条")

    with tempfile.TemporaryDirectory() as td:
        print("\n[2] 构造 manifest（静音代替 TTS 配音，复用 pipeline.py 同款 _audio.py 函数）")
        try:
            manifest_path, manifest = build_fake_manifest(source, td)
            check("timing_manifest.json 写出成功", os.path.isfile(manifest_path))
            check("total_duration 是正数", manifest["total_duration"] > 0,
                  str(manifest["total_duration"]))
            check("segments 分组数与稿件一致",
                  len(manifest.get("segments", [])) == len(source["segments"]))
        except Exception as e:
            check("manifest 构造未抛异常", False, str(e))
            _summarize()
            return

        print("\n[3] gen_hyperframes.py 生成 HTML（真子进程，真实调用）")
        html_path = os.path.join(td, "index.html")
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "gen_hyperframes.py"),
                             "-m", manifest_path, "-o", html_path],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("gen_hyperframes.py 退出码 0", r.returncode == 0, r.stderr[-500:])
        check("index.html 已生成", os.path.isfile(html_path))
        if os.path.isfile(html_path):
            with open(html_path, encoding="utf-8") as f:
                html = f.read()
            for seg in source["segments"]:
                check(f"HTML 包含标题「{seg['title']}」", seg["title"] in html)

        print("\n[4] export_extras.py 导出章节/字幕（真子进程，真实调用）")
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "export_extras.py"),
                             "-m", manifest_path, "-o", td],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("export_extras.py 退出码 0", r.returncode == 0, r.stderr[-500:])
        check("chapters.txt 已生成", os.path.isfile(os.path.join(td, "chapters.txt")))
        check("captions.srt 已生成", os.path.isfile(os.path.join(td, "captions.srt")))

        print("\n[5] 合成测试用 mp4（纯 ffmpeg，不经过 Hyperframes/Chrome）+ verify_render.py 校验")
        ffmpeg_path = get_ffmpeg()
        fake_mp4 = os.path.join(td, "fake_render.mp4")
        built = build_fake_render(ffmpeg_path, manifest["combined_audio"],
                                   manifest["total_duration"], fake_mp4)
        if built is None:
            # 维度 (1)(2) 要求成片是 H.264，本机 ffmpeg 一个 H.264 编码器
            # 都没有时无从合成——这是环境限制，不是本技能回归，按 SKIP 处理。
            skip("测试视频合成（含 verify_render 校验）",
                 "本机 ffmpeg 不含任何 H.264 编码器"
                 "（libx264/libopenh264/h264_nvenc…），换一个完整 ffmpeg 后自动恢复")
        else:
            check("测试视频合成成功", built)
            if built:
                r = subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "verify_render.py"),
                                     "-f", fake_mp4, "-m", manifest_path],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace")
                check("verify_render.py 判定通过（时长匹配 + H.264/AAC）",
                      r.returncode == 0, (r.stdout + r.stderr)[-500:])

    print("\n[6] run.py 第3/4步并行编排（假脚本代替真 TTS/配图 API，验证真并发+失败传播）")
    check_run_parallel_orchestration()

    print("\n[7] gen_hyperframes.py 配图引用完整性 fail-fast（缺失/损坏/正常三种情况）")
    check_image_integrity_validation()

    print("\n[8] run.py 缺图提示改为逐张独立审阅（列出候选原图全路径，不再生成拼图）")
    check_missing_image_independent_review_hint()

    _summarize()


# ── gen_hyperframes.py 的配图引用校验（缺失文件见 5.6.1，损坏/截断文件
# 同测）：该校验逻辑在 main() 的 CLI 参数解析分支里，
# 不是独立可 import 的函数，所以用真子进程调用来测，跟 [3]/[4] 同样的方式。
def check_image_integrity_validation():
    with tempfile.TemporaryDirectory() as td:
        manifest_path, manifest = build_fake_manifest(
            {"opening": "开场。", "closing": "结尾。",
             "segments": [{"title": "t1", "tagline": "tag1", "text": "内容一。"}]},
            td)
        html_path = os.path.join(td, "index.html")
        img_dir = os.path.join(td, "images")
        os.makedirs(img_dir, exist_ok=True)

        # (a) 缺失文件：images.json 指向一个不存在的路径
        images_json = os.path.join(td, "images_missing.json")
        with open(images_json, "w", encoding="utf-8") as f:
            json.dump({"news1": {"src": "images/does_not_exist.png"}}, f)
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "gen_hyperframes.py"),
                             "-m", manifest_path, "-o", html_path, "--images", images_json],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("缺失图片：退出码非 0", r.returncode != 0)
        check("缺失图片：报错信息指出具体路径",
              "does_not_exist.png" in r.stderr, r.stderr[-300:])

        # (b) 损坏文件：文件存在但不是合法图片（模拟下载中断/截断）
        corrupt_path = os.path.join(img_dir, "news1.png")
        with open(corrupt_path, "wb") as f:
            f.write(b"not a real png, truncated download")
        images_json2 = os.path.join(td, "images_corrupt.json")
        with open(images_json2, "w", encoding="utf-8") as f:
            json.dump({"news1": {"src": "images/news1.png"}}, f)
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "gen_hyperframes.py"),
                             "-m", manifest_path, "-o", html_path, "--images", images_json2],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("损坏图片：退出码非 0", r.returncode != 0)
        check("损坏图片：报错信息标明「损坏/无法解码」",
              "损坏" in r.stderr or "无法解码" in r.stderr, r.stderr[-300:])

        # (c) 正常对照组：合法的最小 PNG 应该正常通过、生成 HTML
        try:
            from PIL import Image as _PILImage
            _PILImage.new("RGB", (4, 4), color="red").save(corrupt_path)
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "gen_hyperframes.py"),
                                 "-m", manifest_path, "-o", html_path, "--images", images_json2],
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
            check("正常图片：退出码为 0", r.returncode == 0, r.stderr[-300:])
            check("正常图片：HTML 已生成", os.path.isfile(html_path))
        except ImportError:
            check("正常对照组：Pillow 未安装，已跳过（不计入失败）", True)


# ── run.py 把 TTS 和配图搜索并行跑——这里不调真实
# pipeline.py/search_images.py（要真实 API），而是用两个假脚本替身验证：
# (a) 两个子进程真的同时起、不是伪装成并行实际顺序跑（用耗时差反推）
# (b) 任一步失败时 run.py 能正确以对应退出码终止，不会吞掉错误
def check_run_parallel_orchestration():
    import importlib
    import time as _time

    # 假脚本 fixtures 放进独立的临时目录，而不是 dev/ 目录：避免两个 run_eval
    # 并发跑时互相删除对方还在用的 fixture 文件（dev/ 下共享路径会踩）。
    fixture_tmp = tempfile.TemporaryDirectory(prefix="run_eval_fixtures_")
    fixture_dir = fixture_tmp.name
    fake_pipeline = os.path.join(fixture_dir, "_fixtures_fake_pipeline.py")
    fake_search = os.path.join(fixture_dir, "_fixtures_fake_search_images.py")
    fake_pipeline_fail = os.path.join(fixture_dir, "_fixtures_fake_pipeline_fail.py")

    for path, body in [
        (fake_pipeline, FAKE_PIPELINE_SRC),
        (fake_search, FAKE_SEARCH_IMAGES_SRC),
        (fake_pipeline_fail, FAKE_PIPELINE_FAIL_SRC),
    ]:
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)

    try:
        os.environ["STEPFUN_API_KEY"] = "fake-key-for-run-eval"
        sys.path.insert(0, SCRIPTS_DIR)
        run_mod = importlib.import_module("run")

        src = {"opening": "开场。", "closing": "结尾。",
               "segments": [{"title": "t1", "text": "内容。"}]}

        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "segments_source.json")
            with open(src_path, "w", encoding="utf-8") as f:
                json.dump(src, f, ensure_ascii=False)

            # (a) 真并发：fake_pipeline 睡 1.2s，fake_search 睡 0.8s。
            # 顺序执行约 2.0s，并行执行约 max(1.2,0.8)=1.2s。
            run_mod._script = lambda name: {
                "pipeline.py": fake_pipeline,
                "search_images.py": fake_search,
            }.get(name, os.path.join(SCRIPTS_DIR, name))
            out_dir = os.path.join(td, "audio_output")
            sys.argv = ["run.py", "--source", src_path, "-o", out_dir, "--until", "images"]
            t0 = _time.time()
            run_mod.main()
            elapsed = _time.time() - t0
            check("TTS+配图真并行（耗时接近较慢一步，而非两步相加）",
                  elapsed < 1.8, f"实际耗时 {elapsed:.2f}s（顺序应约 2.0s，并行应约 1.2s）")

        # (b) 失败传播：TTS 假脚本以退出码 7 失败，run.py 应该原样传播退出码
        with tempfile.TemporaryDirectory() as td2:
            src_path2 = os.path.join(td2, "segments_source.json")
            with open(src_path2, "w", encoding="utf-8") as f:
                json.dump(src, f, ensure_ascii=False)
            run_mod._script = lambda name: {
                "pipeline.py": fake_pipeline_fail,
                "search_images.py": fake_search,
            }.get(name, os.path.join(SCRIPTS_DIR, name))
            out_dir2 = os.path.join(td2, "audio_output")
            sys.argv = ["run.py", "--source", src_path2, "-o", out_dir2, "--until", "images"]
            try:
                run_mod.main()
                check("TTS 失败时 run.py 以非零退出码终止", False, "main() 正常返回，没有退出")
            except SystemExit as e:
                check("TTS 失败时 run.py 正确传播退出码 7", e.code == 7, f"实际退出码 {e.code}")
    finally:
        os.environ.pop("STEPFUN_API_KEY", None)
        fixture_tmp.cleanup()


FAKE_PIPELINE_SRC = '''import sys, time, os, json
out = sys.argv[sys.argv.index("-o") + 1]
os.makedirs(out, exist_ok=True)
time.sleep(1.2)
# fixture 必须满足 timing_manifest 契约（顶层 sentences 非空、每段
# sentences 非空、句子四字段齐全）——run.py 的 _image_coverage 走
# load_timing_manifest 校验，空列表会被当成坏产物拒收。
_sent = {"index": 0, "text": "内容。", "start_time": 0.0, "duration": 1.0}
with open(os.path.join(out, "timing_manifest.json"), "w") as f:
    json.dump({"sentences": [_sent], "total_duration": 1.0,
               "segments": [{"id": "news1", "title": "t1", "sentences": [_sent]}]}, f)
'''

FAKE_SEARCH_IMAGES_SRC = '''import sys, time, os, json
out = sys.argv[sys.argv.index("-o") + 1]
os.makedirs(out, exist_ok=True)
time.sleep(0.8)
with open(os.path.join(out, "..", "images.json"), "w") as f:
    json.dump({"news1": {"src": "images/news1.png"}}, f)
'''

FAKE_PIPELINE_FAIL_SRC = '''import sys, time
time.sleep(0.2)
print("[fake_pipeline] 模拟失败", file=sys.stderr)
sys.exit(7)
'''

FAKE_PIPELINE_SRC_2NEWS = '''import sys, time, os, json
out = sys.argv[sys.argv.index("-o") + 1]
os.makedirs(out, exist_ok=True)
time.sleep(0.1)
# 同上：契约要求顶层与每段的 sentences 都非空。
_s1 = {"index": 0, "text": "内容一。", "start_time": 0.0, "duration": 1.0}
_s2 = {"index": 1, "text": "内容二。", "start_time": 1.0, "duration": 1.0}
with open(os.path.join(out, "timing_manifest.json"), "w") as f:
    json.dump({"sentences": [_s1, _s2], "total_duration": 2.0,
               "segments": [{"id": "news1", "title": "t1", "sentences": [_s1]},
                            {"id": "news2", "title": "t2", "sentences": [_s2]}]}, f)
'''

FAKE_SEARCH_IMAGES_MISSING_SRC = '''import sys, time, os, json
out = sys.argv[sys.argv.index("-o") + 1]
os.makedirs(out, exist_ok=True)
time.sleep(0.1)
# news1 有定稿配图，news2 只有候选、没定稿——复现 run.py "缺图" 分支。
with open(os.path.join(out, "..", "images.json"), "w") as f:
    json.dump({"news1": {"src": "images/news1.png"}}, f)
# 1x1 蓝色 PNG 的最小字节序列（硬编码，零依赖）：候选图落盘不依赖 Pillow，
# 保证离线 gate 在没有 PIL 的机器上也能验证"缺图提示列出候选路径"这条链路。
# 注意双反斜杠是刻意的：这段源码会被原样写进假脚本文件再执行，外层字符串
# 不能把十六进制转义提前解释成真实字节（0x89 单独出现会让假脚本的
# UTF-8 源非法）
_MIN_PNG = (b"\\x89PNG\\r\\n\\x1a\\n\\x00\\x00\\x00\\rIHDR\\x00\\x00\\x00\\x01"
            b"\\x00\\x00\\x00\\x01\\x08\\x02\\x00\\x00\\x00\\x90wS\\xde"
            b"\\x00\\x00\\x00\\x0cIDATx\\x9cc``\\xf8\\x0f\\x00\\x01"
            b"\\x03\\x01\\x00\\x08\\x89\\xc2\\xec\\x00\\x00\\x00\\x00IEND\\xaeB`\\x82")
for i in range(1, 3):
    with open(os.path.join(out, f"news2_cand{i}.png"), "wb") as f:
        f.write(_MIN_PNG)
'''


# ── 纯独立审阅：run.py 缺图提示不再生成拼图，而是列出每个缺图段落的候选
# 原图全路径，让 agent 逐张、全分辨率 Read 判断图↔题贴合度。验证：
# 缺图时退出码 2；提示里包含"全分辨率"独立审阅指引且不再引用 review_grid；
# 并把候选原图文件路径列出来（假 search 只落候选图文件、不写 candidates.json，
# 新提示从 images 目录枚举 *_cand* 兜底）。
def check_missing_image_independent_review_hint():
    import importlib

    fixture_tmp = tempfile.TemporaryDirectory(prefix="run_eval_fixtures_")
    fixture_dir = fixture_tmp.name
    fake_pipeline2 = os.path.join(fixture_dir, "_fixtures_fake_pipeline_2news.py")
    fake_search_missing = os.path.join(fixture_dir, "_fixtures_fake_search_missing.py")
    for path, body in [
        (fake_pipeline2, FAKE_PIPELINE_SRC_2NEWS),
        (fake_search_missing, FAKE_SEARCH_IMAGES_MISSING_SRC),
    ]:
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)

    try:
        os.environ["STEPFUN_API_KEY"] = "fake-key-for-run-eval"
        sys.path.insert(0, SCRIPTS_DIR)
        run_mod = importlib.import_module("run")
        # 只替身 pipeline.py/search_images.py（假 API 调用）；其它脚本走真实路径。
        run_mod._script = lambda name: {
            "pipeline.py": fake_pipeline2,
            "search_images.py": fake_search_missing,
        }.get(name, os.path.join(SCRIPTS_DIR, name))

        src = {"opening": "开场。", "closing": "结尾。",
               "segments": [{"title": "t1", "text": "内容一。"},
                            {"title": "t2", "text": "内容二。"}]}

        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "segments_source.json")
            with open(src_path, "w", encoding="utf-8") as f:
                json.dump(src, f, ensure_ascii=False)
            out_dir = os.path.join(td, "audio_output")
            project_dir = os.path.join(td, "hf-project")
            # --until html（不是 images）：--until images 是"看完覆盖率
            # 即止"的调试语义，在缺图拦截之前正常退出 0；要验证缺图
            # exit 2 提示，得让流程走到拦截分支（html/render 路径都经过）。
            sys.argv = ["run.py", "--source", src_path, "-o", out_dir,
                        "--project", project_dir, "--until", "html"]

            buf = io.StringIO()
            exit_code = "未触发 SystemExit"
            with contextlib.redirect_stderr(buf):
                try:
                    run_mod.main()
                except SystemExit as e:
                    exit_code = e.code

            stderr_text = buf.getvalue()
            check("缺图时退出码为 2（等待 agent 审阅）", exit_code == 2,
                  f"实际 {exit_code}；stderr: {stderr_text[-300:]}")
            check("提示不再生成/引用 review_grid 拼图",
                  "review_grid" not in stderr_text, stderr_text[-300:])
            check("提示要求逐张 Read 候选原图（全分辨率独立审阅）",
                  "全分辨率" in stderr_text and "Read" in stderr_text,
                  stderr_text[-500:])
            # 假 search 只落候选图文件（news2_cand*.png）、不写 candidates.json；
            # 新提示从 images 目录枚举候选原图路径，便于 agent 直接 Read。
            check("提示列出了候选原图文件路径（可逐张 Read）",
                  "news2_cand1.png" in stderr_text
                  and "news2_cand2.png" in stderr_text,
                  stderr_text[-500:])
    finally:
        os.environ.pop("STEPFUN_API_KEY", None)
        fixture_tmp.cleanup()


def _summarize():
    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n=== {passed}/{total} passed ===")
    if SKIPPED:
        print(f"（另有 {len(SKIPPED)} 项因本机环境缺能力而 SKIP，非本技能回归）")
    print("\n提醒：本脚本只验证脚本链路本身跑得通、维度(1)(2)(4)的机械检查——")
    print("evals.json 里全部 agent 行为评估用例（能不能正确取材/降级/拒绝越界请求）")
    print("agent 行为评估仍由另一个 agent 实例（作为 grader）forward-test，"
          "本脚本不替代它们。")
    summary = {
        "tool": "run_eval",
        "passed": passed,
        "failed": total - passed,
        "total": total,
        "skipped": len(SKIPPED),
        "results": [{"name": name, "ok": ok} for name, ok in RESULTS],
        "skips": [{"name": name, "reason": reason} for name, reason in SKIPPED],
    }
    print("__SUMMARY_JSON__ " + json.dumps(summary, ensure_ascii=False))
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
