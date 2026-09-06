#!/usr/bin/env python3
"""content-to-video TTS Pipeline

Generates sentence-level TTS audio with precise timing for video sync.

Key features:
- Mixed Chinese-English text passthrough (MiMo TTS handles both natively)
- Sentence-level TTS generation (consistent voice, precise timing)
- Per-sentence duration measurement via ffmpeg -i
- Audio concatenation with configurable gaps
- Optional BGM mixing
- Resume support (skip already-generated sentences)
- Segment grouping from structured source (timing_manifest.json output)
- Outputs timing_manifest.json for video rendering
- Non-destructive speed change (.orig.wav preserved for re-application)
- Configurable model/base_url via CLI or env (MIMO_TTS_MODEL / MIMO_BASE_URL)
- Dry-run mode (--dry-run) to preview sentence split without API calls
- Environment self-check (--check-env) for first-run setup validation
- Silent audio generation with Python wave fallback (no lavfi dependency)

Usage:
  python pipeline.py --source segments_source.json -o output_dir
  python pipeline.py --source segments_source.json -o output_dir --resume
  python pipeline.py --source segments_source.json -o output_dir --bgm bgm.mp3 --bgm-volume 0.15
  python pipeline.py --source segments_source.json -o output_dir --dry-run
  python pipeline.py --check-env
"""
import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import subprocess
import sys


# Environment / Config (统一从 _env 模块加载，两级查找)
# ===================================================================
# 本地共享模块统一"先补 sys.path 再平铺 import"（与 budget.py 等一致）：
# 直接运行时 Python 本就把脚本目录放进 sys.path[0]，insert 只为覆盖
# 被当作模块从别处导入的场景，无需每组 import 各包一层 try/except。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import load_env, get_key, env_source_info, resolve_model_config  # noqa: E402
from _theme import get_default_accent  # noqa: E402
from _voices import list_voice_ids  # noqa: E402
# 默认倍速/时长估算的单一来源在 _contracts——budget.py 属可选工具，
# 核心管线不反向依赖可选脚本，常量统一从 _contracts 取
from _contracts import (DEFAULT_SPEED, load_segments_source,  # noqa: E402
                        DEFAULT_CHARS_PER_SEC, estimate_sentence_seconds,
                        is_content_sid, validate_speed)
from _ffmpeg import get_ffmpeg  # noqa: E402
from _audio import (measure_duration, generate_silence,  # noqa: E402
                    apply_speed, concat_audio, mix_bgm, apply_loudnorm)
from _tts import synth_sentence  # noqa: E402
from build_from_structured import build_parts  # noqa: E402
from _script_utils import setup_stdio  # noqa: E402  重定向场景 stdout 强制 UTF-8

DEFAULT_ACCENT = get_default_accent()


# ===================================================================
# Environment check
# ===================================================================

def run_env_check():
    """Run environment self-check. Prints results, returns True if all pass.

    Checks (in order of pipeline dependency):
    1. Python deps: openai, imageio-ffmpeg (imageio-ffmpeg optional but recommended)
    2. ffmpeg: resolves path, runs -version, checks version string parseable
    3. ffmpeg lavfi support: tests `-f lavfi -i anullsrc` (used by generate_silence)
       — if missing, generate_silence will fall back to Python wave, but warn
    4. ffmpeg wav demuxer: tests reading a tiny WAV (used by measure_duration / atempo)
       — critical: without it, the pipeline cannot process TTS output at all
    5. API key: MIMO_API_KEY in .env or --api-key, plus a lightweight TCP
       reachability probe against the MiMo API host (提示级，不阻断——key 存在
       不代表额度充足/服务未故障，但至少能提前发现"host 不可达"这类问题)
    6. Node.js 22+: needed for the Hyperframes CLI (npx hyperframes) at the render step
    7. StepFun API 可达性（配图用，提示级，不计入 checks_passed）
    """
    import shutil
    import tempfile
    checks_passed = True

    def status(ok, msg):
        nonlocal checks_passed
        checks_passed = checks_passed and ok
        icon = "[OK]" if ok else "[FAIL]"
        print(f"  {icon} {msg}", flush=True)

    print("=== Environment self-check ===", flush=True)
    TOTAL_CHECKS = 7

    # 1. Python deps
    print(f"\n[1/{TOTAL_CHECKS}] Python dependencies:", flush=True)
    try:
        import openai
        status(True, f"openai {getattr(openai, '__version__', '?')} installed")
    except ImportError:
        status(False, "openai not installed — run: pip install \"openai>=1.0.0,<2.0\"")

    try:
        import imageio_ffmpeg
        status(True, "imageio-ffmpeg installed (bundled ffmpeg available as fallback)")
    except ImportError:
        # 非必装：只要系统 PATH 上有可用的 ffmpeg，脚本就会优先用它（见 _ffmpeg.py）。
        # 具体是否可用由下面的 [2/6] ffmpeg executable 检查判定。
        status(True, "imageio-ffmpeg not installed — 将优先使用系统 ffmpeg（无需安装，"
              "除非下面的 ffmpeg 检查失败）")

    # 2. ffmpeg resolves + runs
    print(f"\n[2/{TOTAL_CHECKS}] ffmpeg executable:", flush=True)
    ff = None  # 解析失败时下方 [3]/[4] 按显式跳过处理，不再 NameError 误报
    try:
        ff = get_ffmpeg()
        r = subprocess.run([ff, "-version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10)
        if r.returncode == 0 and r.stdout:
            first_line = r.stdout.split("\n")[0]
            status(True, f"resolved: {ff} ({first_line})")
        else:
            status(False, f"ffmpeg at {ff} exited {r.returncode}")
    except FileNotFoundError:
        status(False, f"ffmpeg not found (resolved path: {ff})")
    except Exception as e:
        status(False, f"ffmpeg check error: {e}")

    # 3. ffmpeg lavfi support (for generate_silence)
    print(f"\n[3/{TOTAL_CHECKS}] ffmpeg lavfi support (silent audio generation):", flush=True)
    if ff is None:
        status(False, f"ffmpeg 未解析成功（见 [2/{TOTAL_CHECKS}]），跳过 lavfi 检查")
    else:
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                test_wav = tf.name
            try:
                r = subprocess.run(
                    [ff, "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                     "-t", "0.1", "-ar", "24000", "-ac", "1", test_wav],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=10
                )
                if r.returncode == 0:
                    status(True, "lavfi supported (anullsrc) — native silent audio generation")
                else:
                    status(True, "lavfi NOT supported — generate_silence will use Python wave fallback "
                          "(acceptable, just slightly slower)")
            finally:
                # finally 清理：subprocess 超时/异常时临时 wav 不再泄漏
                try:
                    os.unlink(test_wav)
                except OSError:
                    pass
        except Exception as e:
            status(False, f"lavfi check error: {e}")

    # 4. ffmpeg WAV demuxer (critical — needed by measure_duration + atempo)
    print(f"\n[4/{TOTAL_CHECKS}] ffmpeg WAV demuxer (critical for TTS output processing):", flush=True)
    if ff is None:
        status(False, f"ffmpeg 未解析成功（见 [2/{TOTAL_CHECKS}]），跳过 WAV demuxer 检查")
    else:
        try:
            import wave as _wave
            import struct as _struct
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                test_wav = tf.name
            try:
                with _wave.open(test_wav, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(24000)
                    w.writeframes(_struct.pack("<h", 0))
                r = subprocess.run([ff, "-i", test_wav], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   timeout=10)
                # ffmpeg -i exits non-zero on success (no output specified), but stderr
                # should contain "Duration:" if WAV was readable.
                if "Duration:" in (r.stderr or ""):
                    status(True, "WAV demuxer works — TTS output can be measured/processed")
                else:
                    status(False, "ffmpeg cannot read WAV files — pipeline will fail at "
                          "measure_duration. This ffmpeg build is too minimal. "
                          "Fix: pip install imageio-ffmpeg to get a full ffmpeg.")
            finally:
                try:
                    os.unlink(test_wav)
                except OSError:
                    pass
        except Exception as e:
            status(False, f"WAV demuxer check error: {e}")

    # 5. API key
    print(f"\n[5/{TOTAL_CHECKS}] MiMo TTS API key:", flush=True)
    env = load_env()
    api_key = env.get("MIMO_API_KEY")
    if api_key:
        source = env_source_info("MIMO_API_KEY")
        status(True, f"MIMO_API_KEY found ({source}) (sk-...{api_key[-4:]})")
        # key 存在只说明"配置了"，不代表服务真的可达（host 故障/网络策略/
        # DNS 污染都不会在这一步之前暴露）。做一次轻量 TCP 探测，提示级、
        # 不消耗 API 额度、不计入 checks_passed（真正的额度/鉴权问题仍要
        # 靠实际调用暴露，这里只提前排除"网络根本不通"这一类）。
        try:
            import socket
            from urllib.parse import urlparse
            _, base_url = resolve_model_config(None, None, "MIMO_TTS_MODEL", None)
            host = urlparse(base_url).hostname or "api.xiaomimimo.com"
            sock = socket.create_connection((host, 443), timeout=5)
            sock.close()
            print(f"  [OK] {host} 可达", flush=True)
        except Exception as e:
            print(f"  [warn] MiMo API host 不可达（网络/防火墙？）：{e}", flush=True)
    else:
        status(False, "MIMO_API_KEY not set — TTS calls will fail. "
              "Create ~/.config/ai-video/.env with: MIMO_API_KEY=sk-xxxxx")

    # 6. Node.js (needed for Hyperframes CLI at the render step)
    print(f"\n[6/{TOTAL_CHECKS}] Node.js 22+ (needed for Hyperframes CLI rendering):", flush=True)
    node = shutil.which("node")
    if node:
        r = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            ver = r.stdout.strip()
            major = 0
            try:
                major = int(ver.lstrip("vV").split(".")[0])
            except (ValueError, IndexError):
                pass
            if major >= 22:
                status(True, f"Node.js {ver} at {node} (>=22, 满足 Hyperframes CLI 要求)")
            else:
                # Hyperframes CLI 明确要求 Node.js 22+：只检查"node 存在"
                # 不够，18.x/20.x 会导致渲染步骤失败，应如实标 FAIL。
                status(False, f"Node.js {ver} at {node} — Hyperframes CLI 要求 "
                      f"Node.js 22+，当前版本过旧，渲染步骤将无法运行。"
                      f"请升级 Node.js（仅跑 TTS 不受影响）")
        else:
            status(False, f"node at {node} exited {r.returncode}")
    else:
        # 只打印 [SKIP] 不计入失败会误导（最终仍输出 "All critical
        # checks passed"，但渲染步骤实际会失败）。Node 是完整流程（渲染）
        # 的必需依赖，缺失时应如实标 FAIL。
        status(False, "Node.js not found — 渲染步骤将无法运行；"
               "仅跑 TTS 不受影响。安装 Node.js 22+ 后再跑完整流程")

    # 7. StepFun 可达性（配图用，提示级，不计入 checks_passed）
    print(f"\n[7/{TOTAL_CHECKS}] StepFun API 可达性（配图用，仅提示不阻断）:", flush=True)
    stepfun_key = get_key("STEPFUN_API_KEY")
    if not stepfun_key:
        print("  [SKIP] 未配置 STEPFUN_API_KEY（配图需在 "
              "~/.config/ai-video/.env 配置）", flush=True)
    else:
        try:
            import socket
            sock = socket.create_connection(("api.stepfun.com", 443), timeout=5)
            sock.close()
            print("  [OK] api.stepfun.com 可达", flush=True)
        except Exception as e:
            print(f"  [warn] api.stepfun.com 不可达（网络/防火墙？）：{e}",
                  flush=True)

    print("\n=== Summary ===", flush=True)
    if checks_passed:
        print("All critical checks passed. Ready to run pipeline.", flush=True)
    else:
        print("Some checks failed. See [FAIL] items above.", flush=True)
        print("Quick fix for most issues: pip install \"openai>=1.0.0,<2.0\" \"Pillow>=10.0.0\"", flush=True)
    return checks_passed


# ===================================================================
# Main pipeline
# ===================================================================

def main():
    setup_stdio()
    parser = argparse.ArgumentParser(description="content-to-video TTS Pipeline")
    # 不用 required=True：--check-env 需要独立运行（文档承诺），必填项
    # 放到 check-env 早退之后再校验
    parser.add_argument("--source", default=None,
                        help="结构化 segments_source.json 路径。"
                             "逐段独立分句，直接产出带 segments 分组的 manifest")
    parser.add_argument("-o", "--output", default=None, help="Output directory")
    parser.add_argument("--api-key", default=None,
                        help="MiMo TTS API key (default: reads MIMO_API_KEY from .env)")
    parser.add_argument("--voice-id", default="冰糖", choices=list_voice_ids(),
                        help="Voice ID (default: 冰糖): "
                             "冰糖(zh,female,中文日报默认推荐), 茉莉(zh,female), "
                             "苏打(zh,male), 白桦(zh,male), "
                             "Mia(en,female), Chloe(en,female), "
                             "Milo(en,male), Dean(en,male)")
    parser.add_argument("--voice-style",
                        default="专业新闻播报，语速适中，语气沉稳自信，中英文表达流畅自然",
                        help="Voice style description")
    parser.add_argument("--gap", type=float, default=0.4,
                        help="Silence gap between sentences (seconds)")
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED,
                        help="Speech speed multiplier via ffmpeg atempo "
                             "(1.0=normal, 1.5=faster). Default follows "
                             "_contracts.DEFAULT_SPEED (single source).")
    parser.add_argument("--loudness", type=float, default=None,
                        help="响度归一化目标（LUFS，如 -16）。默认不做归一化；"
                             "设置后对最终音频做单遍 loudnorm")
    parser.add_argument("--resume", action="store_true",
                        help="Skip sentences whose WAV already exists with valid duration")
    parser.add_argument("--bgm", default=None,
                        help="Background music file path (mp3/wav/ogg)")
    parser.add_argument("--bgm-volume", type=float, default=0.15,
                        help="BGM volume relative to voice (0.0-1.0, default 0.15)")
    parser.add_argument("--model", default=None,
                        help="TTS model name (default: MIMO_TTS_MODEL env var or "
                             "'mimo-v2.5-tts')")
    parser.add_argument("--base-url", default=None,
                        help="MiMo TTS API base URL (default: MIMO_BASE_URL env var "
                             "or 'https://api.xiaomimimo.com/v1')")
    parser.add_argument("--api-timeout", type=float, default=30.0,
                        help="Per-call TTS API timeout in seconds (default 30)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only split sentences + detect segments; print preview "
                             "without calling TTS API or writing audio. Useful for "
                             "verifying script formatting without spending credits.")
    parser.add_argument("--check-env", action="store_true",
                        help="Run environment self-check (Python deps, ffmpeg, lavfi, "
                             "API key, Node.js) and exit. Use before first run or when "
                             "debugging setup issues. Exits 0 if all checks pass, 1 otherwise.")
    parser.add_argument("--workers", type=int, default=4,
                        help="并行 TTS 调用数（default 4）")
    parser.add_argument("--on-fail", choices=["skip", "silence"], default="skip",
                        help="单句 TTS 反复失败（如触发内容审核）后的处理方式："
                             "skip（默认，旧行为）直接丢弃该句，不进入成片；"
                             "silence 改为该句降级为静音（时长按 budget.py 同款"
                             "字数/语速启发式估算），保留在成片时间轴与字幕位置"
                             "上，不阻断整条视频，manifest 对应句子会带 "
                             "\"synth_failed\": true，方便事后定位要不要人工补录。")
    args = parser.parse_args()

    # ── speed 值校验（fail-fast：坏语速进入 atempo 会死循环或白烧额度）──
    try:
        validate_speed(args.speed)
    except ValueError as e:
        parser.error(str(e))

    # ── gap/workers 校验（同款 fail-fast）───────────────────────────
    # 负 gap 会让 manifest 时间轴（无条件累加 args.gap）与实际拼接（gap<=0
    # 不插静音）系统性脱节；workers<=0 会让 ThreadPoolExecutor 直接 ValueError。
    if not math.isfinite(args.gap) or args.gap < 0:
        parser.error(f"--gap 必须是非负有限数（句间静音秒数，收到 {args.gap}）；"
                     "要无间隙拼接请显式传 0")
    # --loudness 直接拼进 ffmpeg 滤镜串，NaN/Inf 会产出非法滤镜再报一串
    # 迷惑性 stderr；跟 --bgm-volume 的处理对齐，提前拦下
    if args.loudness is not None and not math.isfinite(args.loudness):
        parser.error(f"--loudness 必须是有限数值（LUFS，收到 {args.loudness}）")
    # --bgm-volume 同样会拼进 ffmpeg 滤镜串（BGM 混音段），NaN/Inf
    # 在这里提前拦下——原先拖到混音阶段才报错，TTS 额度已经白烧一遍
    if not math.isfinite(args.bgm_volume):
        parser.error(f"--bgm-volume 必须是有限数值（0.0-1.0，收到 {args.bgm_volume}）")
    if args.workers < 1:
        parser.error(f"--workers 至少为 1（收到 {args.workers}）")
    # --bgm 指向不存在的文件时提前警告并忽略，而不是静默跳过混音——
    # 用户以为加了 BGM，成片里却没有，排查起来非常绕。
    if args.bgm and not os.path.exists(args.bgm):
        print(f"[warn] --bgm 文件不存在，已忽略 BGM 混音：{args.bgm}",
              file=sys.stderr)
        args.bgm = None

    # ── Environment self-check ─────────────────────────────────────
    if args.check_env:
        ok = run_env_check()
        sys.exit(0 if ok else 1)

    # ── Validate required args (after --check-env early exit) ─────
    if not args.output:
        parser.error("the following arguments are required: -o/--output "
                     "(not needed for --check-env)")
    if not args.source:
        parser.error("the following arguments are required: --source "
                     "(not needed for --check-env)")

    # ── Resolve API key ────────────────────────────────────────────
    api_key = get_key("MIMO_API_KEY", args.api_key)
    if not api_key and not args.dry_run:
        print("[error] No API key. Use --api-key or set MIMO_API_KEY in .env",
              file=sys.stderr)
        sys.exit(1)

    # ── Resolve model + base_url (CLI > env/config > default) ──────
    model, base_url = resolve_model_config(
        args.model, args.base_url, "MIMO_TTS_MODEL", "mimo-v2.5-tts")

    # ── Read script（--source 结构化输入，逐段独立分句）───────────────
    try:
        source_data = load_segments_source(args.source)
        sentences, seg_config = build_parts(source_data, default_speed=args.speed)
    except ValueError as e:
        print(f"[error] 结构化稿件无效：{e}", file=sys.stderr)
        sys.exit(1)
    if not sentences:
        print("[error] 结构化稿件分句为空，请检查 segments_source.json 的文本",
              file=sys.stderr)
        sys.exit(1)

    print(f"[script] {sum(len(s) for s in sentences)} chars", flush=True)

    print(f"[split] {len(sentences)} sentences", flush=True)
    for i, s in enumerate(sentences):
        preview = s[:35] + "..." if len(s) > 35 else s
        print(f"  {i+1}. {preview}", flush=True)

    print(f"\n[source] 结构化输入：{len(seg_config)} 个段落，逐段独立分句"
          f"（不存在跨段短句合并问题）", flush=True)

    if args.dry_run:
        print("\n[dry-run] Sentence split + segment detection complete. "
              "No TTS calls made, no audio written.", flush=True)
        return

    # ── Setup output ───────────────────────────────────────────────
    # -o 指到已存在的同名文件（手滑把文件路径当目录传）时，makedirs 会
    # 裸抛 FileExistsError 不指向真正原因——提前拦下
    if os.path.isfile(args.output):
        print(f"[error] 输出路径 {args.output} 是一个已存在的文件，"
              f"--output 需要的是目录路径", file=sys.stderr)
        sys.exit(1)
    os.makedirs(args.output, exist_ok=True)
    sentences_dir = os.path.join(args.output, "sentences")
    os.makedirs(sentences_dir, exist_ok=True)

    ffmpeg_path = get_ffmpeg()

    # ── Initialize OpenAI client ───────────────────────────────────
    from openai import OpenAI
    # max_retries=0：SDK 内部默认还会静默重试 2 次，叠加本模块自己的
    # 3 次应用层重试 = 单句最多 6 次 billable 请求。重试策略统一收口到
    # _tts.synth_sentence（带退避抖动），SDK 层关掉。
    client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
    print(f"[api] model={model} base_url={base_url}", flush=True)

    # ── Build per-sentence speed / voice mapping ────────────────────
    # 每句动态语速：若段落指定了 speed 则覆盖全局 args.speed，
    # 没有指定 speed 的段落用全局 args.speed。voice 同理——不同段落
    # （比如开场用甲音色、正文用乙音色）可以分别指定 voice_id/voice_style。
    sentence_speeds = {}
    sentence_voices = {}  # index -> (voice_id, voice_style)
    sentence_speaker_labels = {}  # index -> 说话人显示标签（仅双人对话段落有值）
    if seg_config:
        for seg in seg_config:
            start_idx = seg.get("start", 0)
            end_idx = seg.get("end", len(sentences))
            seg_speed = seg.get("speed")
            if seg_speed is not None:
                for si in range(start_idx, min(end_idx, len(sentences))):
                    sentence_speeds[si] = seg_speed
            seg_voice_id = seg.get("voice_id")
            seg_voice_style = seg.get("voice_style")
            if seg_voice_id or seg_voice_style:
                for si in range(start_idx, min(end_idx, len(sentences))):
                    sentence_voices[si] = (
                        seg_voice_id or args.voice_id,
                        seg_voice_style if seg_voice_style is not None else args.voice_style,
                    )
            # ── 双人对话（turns）：比 segment 更细的子区间，按句覆盖 ────
            # segment 级设置——同一段里 A/B 两人交替发言，各自用各自的音色。
            # build_from_structured.py 已把 speakers 解析成每个 turn 自带的
            # voice_id/voice_style，这里不需要再反查任何 speakers 字典。
            for turn in seg.get("turns", []):
                t_start, t_end = turn.get("start", start_idx), turn.get("end", end_idx)
                t_voice_id = turn.get("voice_id")
                t_voice_style = turn.get("voice_style")
                t_label = turn.get("label") or turn.get("speaker")
                for si in range(t_start, min(t_end, len(sentences))):
                    if t_voice_id or t_voice_style:
                        base_id, base_style = sentence_voices.get(
                            si, (args.voice_id, args.voice_style))
                        sentence_voices[si] = (
                            t_voice_id or base_id,
                            t_voice_style if t_voice_style is not None else base_style,
                        )
                    if t_label:
                        sentence_speaker_labels[si] = t_label

    # ── Generate TTS per sentence (parallel) ──────────────────────
    sentence_data = []
    failed = []
    cached_count = 0
    pending_tasks = []  # 待合成的句子任务

    def _sentence_hash(text, voice_id=None, voice_style=None, model=None):
        """句子 TTS 输入（文本+音色+风格+模型）的内容指纹（resume 缓存键）。

        缓存 wav 文件名只有句序号（s005.wav），不含内容——改了第 5 句文案后
        带 --resume 重跑会复用旧音频，而 manifest 的 text 用新稿，配音与
        字幕从此错位。合成成功时把该句输入的 sha1 写进 s005.wav.sha sidecar，
        resume 时比对：不一致视为缓存失效，删掉重合成。
        指纹除文本外还覆盖 voice_id/voice_style/model——只含文本时
        换音色后带 --resume 重跑会静默复用旧音色音频（manifest 声明的
        是新音色、实际音频是旧音色，且无任何警告）。旧缓存（纯文本指纹）
        会统一失配重合成（MiMo TTS 不计费，只费时间不烧额度）。
        """
        payload = "\x1f".join([text, voice_id or "", voice_style or "", model or ""])
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _write_sentence_sidecars(out_path, text, speed, speed_applied=True,
                                 voice_id=None, voice_style=None, model=None):
        """合成/兜底成功后统一落 sidecar：内容指纹 .sha + 已施加语速 .spd。

        speed_applied=False 表示本次 atempo 没落上（音频仍在原速）——
        此时绝不能写 .spd（否则下次 --resume 会判"已在目标速率"而跳过
        重变速，该句被永久钉在原速），还要清掉残留的旧 marker。
        """
        with open(out_path + ".sha", "w", encoding="utf-8") as f:
            f.write(_sentence_hash(text, voice_id, voice_style, model))
        if abs(speed - 1.0) > 0.01:
            if speed_applied:
                with open(out_path + ".spd", "w", encoding="utf-8") as f:
                    f.write(str(round(speed, 4)))
            else:
                try:
                    if os.path.exists(out_path + ".spd"):
                        os.remove(out_path + ".spd")
                except OSError:
                    pass
        elif os.path.exists(out_path + ".spd"):
            try:
                os.remove(out_path + ".spd")
            except OSError:
                pass

    def _drop_stale_cache(out_path):
        """内容指纹不符时清掉旧缓存及其全部 sidecar，避免带着旧状态续跑。

        返回是否全部清干净：Windows 下文件被占用（杀毒扫描/正在播放）会
        删失败，此时调用方不能再按"文件存在=缓存可用"续跑（否则已判定
        失效的旧音频配上新稿字幕），必须强制进重合成队列覆盖。
        """
        ok = True
        for suffix in ("", ".sha", ".spd", ".spd.tmp.wav", ".failed", ".orig.wav"):
            p = out_path + suffix
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    ok = False
        return ok

    # 第一遍：收集 resume 可跳过的缓存句子（直接加入 sentence_data），
    # 其余句子加入 pending_tasks 待并行处理。
    for i, sent_tts in enumerate(sentences):
        out_path = os.path.join(sentences_dir, f"s{i+1:03d}.wav")
        label = f"s{i+1:03d}/{len(sentences):03d}"
        sent_speed = sentence_speeds.get(i, args.speed)
        sent_voice_id, sent_voice_style = sentence_voices.get(i, (args.voice_id, args.voice_style))

        # Resume: skip if file exists and has valid duration
        if args.resume and os.path.exists(out_path):
            # 内容指纹校验：缓存必须属于当前这份稿子的这句话。
            # 没有 .sha sidecar 的旧缓存（升级前生成的）按可信处理，
            # 避免升级瞬间把所有历史缓存作废、重烧一遍 TTS 额度。
            sha_path = out_path + ".sha"
            if os.path.exists(sha_path):
                try:
                    with open(sha_path, encoding="utf-8") as f:
                        cached_sha = f.read().strip()
                except (OSError, UnicodeDecodeError):
                    # sidecar 被写成非 UTF-8（手工编辑过）与读不到同罪：
                    # 按指纹失配处理，触发缓存清理与重合成
                    cached_sha = ""
                if cached_sha != _sentence_hash(sent_tts, sent_voice_id,
                                                sent_voice_style, model):
                    print(f"  [{label}] 稿件内容或音色/模型已变化，缓存失效，重新合成",
                          flush=True)
                    if not _drop_stale_cache(out_path):
                        # 删失败但缓存已判定失效：强制重合成覆盖，绝不带着
                        # 旧音频续跑（os.path.exists 会误判"缓存仍有效"）
                        print(f"  [{label}][warn] 旧缓存删除失败（文件被占用？），"
                              f"将重新合成覆盖", file=sys.stderr)
                        pending_tasks.append({
                            "index": i, "text_tts": sent_tts,
                            "out_path": out_path, "label": label, "speed": sent_speed,
                            "voice_id": sent_voice_id, "voice_style": sent_voice_style,
                        })
                        continue
            if not os.path.exists(out_path):
                pending_tasks.append({
                    "index": i, "text_tts": sent_tts,
                    "out_path": out_path, "label": label, "speed": sent_speed,
                    "voice_id": sent_voice_id, "voice_style": sent_voice_style,
                })
                continue
            dur = measure_duration(ffmpeg_path, out_path)
            if dur > 0:
                # Align cached audio to the requested speed (atempo is
                # idempotent only within a run, so track applied speed
                # with a sidecar marker to avoid double-applying).
                marker = out_path + ".spd"
                applied = None
                if os.path.exists(marker):
                    try:
                        with open(marker, encoding="utf-8") as f:
                            applied = float(f.read().strip())
                    except (OSError, ValueError):
                        applied = None
                # Re-apply speed if the cached audio's applied speed differs
                # from the requested speed. This covers two cases:
                #   (a) sent_speed != 1.0 and cached audio is at a different
                #       speed (e.g. previously 1.5x, now 1.2x) — re-apply atempo.
                #   (b) sent_speed == 1.0 and cached audio was previously sped
                #       up (applied != 1.0) — restore from .orig.wav backup.
                # apply_speed handles both: returns True for speed=1.0 by
                # copying the backup, returns True for speed!=1.0 by atempo.
                needs_reapply = (applied is None) or (applied != round(sent_speed, 4))
                if needs_reapply:
                    # prev_speed=applied：.orig.wav 原始备份被清理过时，
                    # apply_speed 会改用补偿变速（atempo(speed/prev)），
                    # 而不是把已变速的文件再备份一遍、叠加变速。
                    if apply_speed(ffmpeg_path, out_path, sent_speed,
                                   prev_speed=applied):
                        # Only persist marker for non-1.0 speeds; for 1.0 the
                        # marker should be removed so future runs know the
                        # audio is at natural pace (no .orig.wav needed).
                        # ±0.01 阈值与 build_atempo_filter 一致：1.0±ε 视作
                        # 原速（atempo 不施变速），marker 不得宣称已变速
                        if abs(sent_speed - 1.0) > 0.01:
                            try:
                                with open(marker, 'w', encoding="utf-8") as f:
                                    f.write(str(round(sent_speed, 4)))
                            except OSError as e:
                                # marker 写失败（文件被占用）时绝不能中断
                                # resume：音频本身已变速成功，只是下次
                                # resume 会多做一次补偿变速（幂等）
                                print(f"  [{label}][warn] .spd marker 写入"
                                      f"失败（{e}），下次 --resume 会重新"
                                      f"对齐语速", file=sys.stderr)
                        elif os.path.exists(marker):
                            try:
                                os.remove(marker)
                            except OSError:
                                pass
                    else:
                        print(f"  [{label}][warn] atempo re-apply failed; "
                              f"audio kept at previous speed", file=sys.stderr)
                    dur = measure_duration(ffmpeg_path, out_path)
                # 旧缓存没有 .sha sidecar（本次升级前生成）：按可信处理过了，
                # 这里补写一份，让下一次改稿能被指纹校验发现。
                if not os.path.exists(sha_path):
                    try:
                        with open(sha_path, "w", encoding="utf-8") as f:
                            f.write(_sentence_hash(sent_tts, sent_voice_id,
                                                   sent_voice_style, model))
                    except OSError:
                        pass
                sd = {
                    "index": i, "text": sentences[i],
                    "file": out_path, "duration": round(dur, 3)
                }
                if i in sentence_speaker_labels:
                    sd["speaker"] = sentence_speaker_labels[i]
                if os.path.exists(out_path + ".failed"):
                    # 上次是 --on-fail silence 兜底出来的静音文件，resume 缓存
                    # 也要把这个标记带回来，不然人工补录提示会悄悄消失。
                    sd["synth_failed"] = True
                sentence_data.append(sd)
                print(f"  [{label}][skip] {dur:.2f}s (cached)", flush=True)
                cached_count += 1
                continue

        pending_tasks.append({
            "index": i, "text_tts": sent_tts,
            "out_path": out_path, "label": label, "speed": sent_speed,
            "voice_id": sent_voice_id, "voice_style": sent_voice_style,
        })

    # 进度预估（基于平均句时长 + 20% 重试余量；句均 ~13 字，按
    # DEFAULT_CHARS_PER_SEC 推导，与静音兜底/estimate 同一套假设）
    pending_count = len(pending_tasks)
    est_per_sentence = round(13 / DEFAULT_CHARS_PER_SEC, 1)
    est_total = pending_count * est_per_sentence * 1.2 / max(args.workers, 1)
    print(f"[est] {pending_count} sentences to synthesize, ~{est_total:.0f}s "
          f"with up to {args.workers} workers", flush=True)

    # 并行 TTS 合成：用线程池并行调用 synth_sentence
    def _tts_worker(task):
        """单个句子的 TTS 合成任务（供线程池调用）。"""
        ok, speed_applied = synth_sentence(
            client, task["text_tts"], task["voice_id"], task["voice_style"],
            task["out_path"], ffmpeg_path, task["speed"],
            sentence_label=task["label"], model=model,
            api_timeout=args.api_timeout,
        )
        return task, ok, speed_applied

    new_results = []
    if pending_tasks:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(_tts_worker, t) for t in pending_tasks]
            done_count = 0
            for future in concurrent.futures.as_completed(futures):
                task, ok, speed_applied = future.result()
                done_count += 1
                idx = task["index"]
                sent_orig = sentences[idx]
                out_path = task["out_path"]
                label = task["label"]
                preview = sent_orig[:30] + "..." if len(sent_orig) > 30 else sent_orig

                if ok and os.path.exists(out_path):
                    dur = measure_duration(ffmpeg_path, out_path)
                    if dur > 0:
                        # 落 sidecar：内容指纹 .sha（resume 校验用）+
                        # 已施加语速 .spd（下次 resume 不必重放 atempo）
                        _write_sentence_sidecars(
                            out_path, task["text_tts"], task["speed"],
                            speed_applied=speed_applied,
                            voice_id=task["voice_id"],
                            voice_style=task["voice_style"], model=model)
                        sd = {
                            "index": idx, "text": sent_orig,
                            "file": out_path, "duration": round(dur, 3)
                        }
                        if idx in sentence_speaker_labels:
                            sd["speaker"] = sentence_speaker_labels[idx]
                        new_results.append(sd)
                        # 这次是真的合成成功了——如果这句之前被 --on-fail silence
                        # 兜底过、留了个 .failed marker（比如用户手动删了旧的静音
                        # mp3 之后重跑、这次没再失败），清掉 marker，不然下次
                        # resume 会把这个已经补录好的句子又标成 synth_failed。
                        stale_marker = out_path + ".failed"
                        if os.path.exists(stale_marker):
                            try:
                                os.remove(stale_marker)
                            except OSError:
                                pass
                        print(f"[TTS {done_count}/{pending_count}] {label} "
                              f"{preview} -> {dur:.2f}s", flush=True)
                        continue

                if args.on_fail == "silence":
                    # 降级：该句反复失败（如触发内容审核）时不再直接丢弃，改为
                    # 生成一段静音占位，时长用 budget.py 同款启发式（字数/语速）
                    # 估算——没有真实语速可测，只能估，跟 budget.py estimate 的
                    # 假设保持一致（DEFAULT_CHARS_PER_SEC），乘上该句实际 speed。
                    fallback_dur = max(
                        estimate_sentence_seconds(sent_orig, DEFAULT_CHARS_PER_SEC, task["speed"]),
                        0.3,
                    )
                    try:
                        generate_silence(ffmpeg_path, fallback_dur, out_path)
                        # 落一个 sidecar marker（跟已有的 .spd 速度 marker 同一套
                        # 模式），不然下次 --resume 时这句会走"文件已存在=缓存"
                        # 分支重建 sentence_data，那个分支不知道这个文件其实是
                        # 静音兜底——synth_failed 标记会悄悄从 manifest 里消失，
                        # 事后再也看不出这句需要人工补录（这个坑是加 --on-fail
                        # silence 时最先漏掉、后来自查发现的）。
                        open(out_path + ".failed", "w",
                             encoding="utf-8").close()
                        # 静音时长估算已除过 speed（estimate_sentence_seconds），
                        # 必须写 .spd marker 声明"已施加语速"——否则下次 --resume
                        # 会把这段静音再 atempo 一遍，占位时长被压缩到 2/3。
                        # .sha 内容指纹也一并落盘，跟正常合成句子的缓存语义一致。
                        # （静音时长已按 speed 折算，speed_applied 默认 True：
                        # 这里的 .spd 是"声明已施加语速"，与合成句语义一致）
                        _write_sentence_sidecars(
                            out_path, task["text_tts"], task["speed"],
                            voice_id=task["voice_id"],
                            voice_style=task["voice_style"], model=model)
                        sd = {
                            "index": idx, "text": sent_orig,
                            "file": out_path, "duration": round(fallback_dur, 3),
                            "synth_failed": True,
                        }
                        if idx in sentence_speaker_labels:
                            sd["speaker"] = sentence_speaker_labels[idx]
                        new_results.append(sd)
                        print(f"[TTS {done_count}/{pending_count}] {label} "
                              f"{preview} [FAILED -> silence fallback "
                              f"~{fallback_dur:.2f}s, 建议事后人工补录]", flush=True)
                        continue
                    except Exception as e:
                        print(f"[TTS {done_count}/{pending_count}] {label} "
                              f"{preview} [FAILED，静音兜底也失败: {e}]", flush=True)
                        print(f"[warn] 句 {label}：--on-fail silence 承诺的"
                              f"“保留在时间轴与字幕位置”无法兑现，该句将从"
                              f"成片与字幕中完全丢失", file=sys.stderr)

                failed.append(idx)
                print(f"[TTS {done_count}/{pending_count}] {label} "
                      f"{preview} [FAILED]", flush=True)

    # 合并结果并按原始 index 排序，保证 sentence_data 顺序正确
    sentence_data.extend(new_results)
    sentence_data.sort(key=lambda s: s["index"])

    if not sentence_data:
        print("[error] All sentences failed", file=sys.stderr)
        sys.exit(1)
    if failed:
        print(f"\n[warn] {len(failed)} failed, skipped: {[f+1 for f in failed]}",
              flush=True)
    silence_fallback_count = sum(1 for s in sentence_data if s.get("synth_failed"))
    if silence_fallback_count:
        print(f"\n[warn] {silence_fallback_count} 句无有效配音（TTS 失败后的"
              f"静音占位，含历史 run 缓存复用），成片对应位置为静音，建议核对 "
              f"timing_manifest.json 中 \"synth_failed\": true 的句子并考虑补录",
              flush=True)

    # ── Concatenate ────────────────────────────────────────────────
    print(f"\n[concat] {len(sentence_data)} clips (gap {args.gap}s)...", flush=True)
    audio_files = [s["file"] for s in sentence_data]
    combined_path = os.path.join(args.output, "combined.wav")
    concat_ok = concat_audio(ffmpeg_path, audio_files, args.gap, combined_path)

    if not concat_ok:
        print("[error] Audio concat failed", file=sys.stderr)
        sys.exit(1)

    total_dur = measure_duration(ffmpeg_path, combined_path)
    if not total_dur or total_dur <= 0:
        # 测量失败时 total_dur=0 仍能通过契约的数值校验，产出"合法但废掉"
        # 的 manifest（data-duration=0 的 composition 渲染出无声空片）——
        # 在这里拦下比让废品流到渲染端好
        print("[error] combined.wav 时长测量失败（0.0s）——ffmpeg 无法读取"
              "拼接产物？检查磁盘空间与 ffmpeg 可用性", file=sys.stderr)
        sys.exit(1)
    print(f"[done] Total audio: {total_dur:.2f}s", flush=True)

    # ── Calculate start times ──────────────────────────────────────
    # 提前到 BGM 混音之前算，因为混音后的 manifest 需要每句的 start_time。
    cumulative = 0.0
    for i, sd in enumerate(sentence_data):
        sd["start_time"] = round(cumulative, 3)
        cumulative += sd["duration"]
        if i < len(sentence_data) - 1:
            cumulative += args.gap

    # ── Optional BGM mix ───────────────────────────────────────────
    if args.bgm and os.path.exists(args.bgm):
        # Validate bgm_volume: clamp to [0, 1] to prevent clipping (>1 amplifies
        # into distortion) and reject nonsense values. Also guard against
        # filter-string injection: bgm_volume is interpolated directly into the
        # ffmpeg filter_complex string, so a malicious value like "1,aecho"
        # would inject arbitrary filters. type=float already rejects non-numeric
        # input；NaN/Inf 已在 argparse 阶段 parser.error 拦下（早于 TTS 不烧
        # 额度），这里只需 clamp 越界值。
        if args.bgm_volume < 0 or args.bgm_volume > 1:
            clamped = max(0.0, min(1.0, args.bgm_volume))
            print(f"[warn] --bgm-volume {args.bgm_volume} out of [0,1], "
                  f"clamped to {clamped}", file=sys.stderr)
            args.bgm_volume = clamped
        print(f"[bgm] Mixing {args.bgm} at volume {args.bgm_volume}...", flush=True)
        mixed_path = os.path.join(args.output, "combined_bgm.wav")
        if mix_bgm(ffmpeg_path, combined_path, args.bgm, args.bgm_volume, mixed_path):
            combined_path = mixed_path
            print(f"  [OK] {mixed_path}", flush=True)
        else:
            print("  [warn] BGM mix failed, using voice-only audio", flush=True)

    # ── Optional loudness normalization ────────────────────────────
    if args.loudness is not None:
        loud_path = os.path.join(args.output, "combined_loud.wav")
        if apply_loudnorm(ffmpeg_path, combined_path, loud_path, args.loudness):
            combined_path = loud_path
            total_dur = measure_duration(ffmpeg_path, combined_path)
            print(f"  [loudness] normalized to {args.loudness} LUFS -> {loud_path}",
                  flush=True)
        else:
            print("  [warn] loudness normalization failed, using un-normalized audio",
                  flush=True)

    # ── Build manifest ─────────────────────────────────────────────
    manifest_sentences = []
    for s in sentence_data:
        entry = {
            "index": s["index"],
            "text": s["text"],
            "start_time": s["start_time"],
            "duration": s["duration"],
        }
        if s.get("speaker"):
            entry["speaker"] = s["speaker"]  # 双人对话段落：说话人显示标签
        if s.get("synth_failed"):
            entry["synth_failed"] = True  # TTS 失败降级为静音占位（见 --on-fail）
        manifest_sentences.append(entry)

    manifest = {
        "sentences": manifest_sentences,
        "total_duration": round(total_dur, 3),
        "gap": args.gap,
        "voice_id": args.voice_id,
        "combined_audio": os.path.abspath(combined_path),
    }
    # flow 自然叙事模式（segments_source.json 顶层 "flow": true）：
    # gen_hyperframes 读这个标记决定"无编号 + 无 wipe + 开场目录默认关"。
    if source_data.get("flow"):
        manifest["flow"] = True

    # ── Optional segment grouping (seg_config loaded earlier) ─────
    if seg_config:
        grouped = []
        dropped = []
        for seg in seg_config:
            start_idx = seg["start"]   # 0-based sentence index (inclusive)
            end_idx = seg["end"]       # exclusive
            seg_sentences = [
                s for s in manifest_sentences
                if start_idx <= s["index"] < end_idx
            ]
            _seg_id = seg.get("id", f"seg{len(grouped)+1}")
            if not seg_sentences:
                # 该段所有句子都没产出音频（TTS 连续失败 + --on-fail skip，
                # 或段落本身被上游丢空）。空段落进 manifest 会被 _contracts
                # 的校验直接拒收（"缺少非空 sentences 列表"），
                # gen_hyperframes 随之退出——一次失败就让整条视频出不来。
                # 剔除并报对人，而不是写出一份下游必然拒收的 manifest。
                dropped.append(_seg_id)
                continue
            # tagline 兜底：只对 news/seg 内容段落兜底为 "补充阅读"，避免画面
            # 缺字；opening/closing 是结构性段落，留空即不显示小标题，不塞
            # 通用标签。兜底文案必须是内容中立词（本技能信源不限于 AI 资讯），
            # 与 SKILL.md 字段说明保持一致。
            _tagline = seg.get("tagline", "")
            if not _tagline and is_content_sid(_seg_id):
                _tagline = "补充阅读"
            seg_out = {
                "id": _seg_id,
                "title": seg.get("title", ""),
                "tagline": _tagline,
                "body": seg.get("body", ""),
                "accent": seg.get("accent", DEFAULT_ACCENT),
                "sentences": seg_sentences,
            }
            # 透传可选字段：speed（段落级语速）、voice_id/voice_style（段落级音色）。
            if seg.get("speed") is not None:
                seg_out["speed"] = seg["speed"]
            if seg.get("voice_id") is not None:
                seg_out["voice_id"] = seg["voice_id"]
            if seg.get("voice_style") is not None:
                seg_out["voice_style"] = seg["voice_style"]
            if seg.get("agenda") is not None:
                seg_out["agenda"] = seg["agenda"]
            if seg.get("recap") is not None:
                seg_out["recap"] = seg["recap"]
            if seg.get("turns"):
                seg_out["turns"] = seg["turns"]
            grouped.append(seg_out)
        manifest["segments"] = grouped
        if dropped:
            # 静默剔除会让"我写了 8 段、成片只有 6 段"变成无解的困惑；
            # 报出被剔除的 sid 与原因，让失败可归因。
            print(f"\n[warn] {len(dropped)} 个段落没有任何可用音频，"
                  f"已从 manifest 剔除：{', '.join(dropped)}\n"
                  f"       常见原因是这几段 TTS 连续失败且 --on-fail skip"
                  f"（默认）——检查一下上面这些句子的 [fail] 日志，修掉后"
                  f"重跑即可自动补回；--on-fail silence 会生成静音占位、"
                  f"不会触发剔除。", file=sys.stderr, flush=True)

    # ── Write manifest ─────────────────────────────────────────────
    # 原子写：timing_manifest.json 是下游（gen_hyperframes /
    # verify_render）唯一的时间轴数据源，写到一半被 Ctrl-C 打断会留下一份
    # 截断的 JSON——下次 --resume 直接崩在 json.load，且堆栈完全不指向
    # "上次中断了，重跑一遍就好"。先写 .tmp 再 replace，要么完整要么不存在。
    manifest_path = os.path.join(args.output, "timing_manifest.json")
    _tmp = manifest_path + ".tmp"
    with open(_tmp, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(_tmp, manifest_path)

    print(f"\n[manifest] {manifest_path}", flush=True)
    print(f"[stats] {len(sentence_data)}/{len(sentences)} sentences OK "
          f"({cached_count} cached)", flush=True)
    print(f"[duration] {total_dur:.2f}s", flush=True)


if __name__ == "__main__":
    main()
