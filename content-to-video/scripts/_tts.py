#!/usr/bin/env python3
"""MiMo TTS 单句合成（从 pipeline.py 拆出）。

只做一件事：把一句文本经 chat.completions + audio 参数合成为 WAV，
并在需要时用 _audio.apply_speed 做确定性语速变速。可注入 fake client 单测。
"""
import base64
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _audio import apply_speed  # noqa: E402


class BadAudioResponseError(Exception):
    """TTS 响应里没有音频（chat.completions 返回了纯文本）。

    几乎总是 --base-url/--model 指向了不支持 audio 参数的网关或模型，
    重试 N 次结果完全一样。必须整句放弃并让调用方尽早终止——否则 100 句
    × 每句 3 次重试 = 300 次 billable 调用全部白烧后才以
    "All sentences failed" 收场，且中间的报错是迷惑性的
    'NoneType' object has no attribute 'data'。
    """


def _is_non_retryable(exc):
    """判断异常是否属于"重试也不会好"的确定性失败。

    openai SDK 的 APIStatusError 及其子类都带 status_code 属性：
    400（参数/内容审核拒绝）、401/403（密钥错误/无权限）、404（模型不存在）、
    422（请求不合法）这类错误重试 N 次结果完全一样——每句烧满 3 次重试
    只会浪费额度和时间（100 句 × 无效 key = 300 次无效调用 + 每句多等 6s），
    直接放弃。响应不含音频（BadAudioResponseError）同理：端点/模型配错了，
    换一句再试也是同样的纯文本响应。连接/超时/429 限流类不带 status_code
    或带可重试码，仍走重试。
    """
    if isinstance(exc, BadAudioResponseError):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in (400, 401, 403, 404, 422)
    return False


def _clear_stale_sidecars(out_path):
    """新合成前清掉上一轮残留的变速/兜底 sidecar。

    这些文件属于上一次运行的稿件/参数：.orig.wav 是旧音频的原速备份，
    .spd/.spd.tmp.wav/.failed 是旧状态标记。新音频落盘后若不清理，
    apply_speed 会把旧 .orig.wav 当作原速源做变速，新音频被整体丢弃
    （改稿后不带 --resume 重跑即触发，成片念旧稿配新字幕）。

    返回是否全部清理干净（或本来就没有残留）。删除失败（Windows 下
    杀毒扫描/播放器占用文件）时先尝试改名隔离（加 .stale 后缀，隔离后
    不会再被任何流程按原名读到）；改名也失败才返回 False——调用方必须
    据此跳过变速，否则残留的旧 .orig.wav 会顶掉新合成的音频。
    """
    ok = True
    for suffix in (".orig.wav", ".spd", ".spd.tmp.wav", ".failed"):
        p = out_path + suffix
        if not os.path.exists(p):
            continue
        try:
            os.remove(p)
        except OSError:
            try:
                os.replace(p, p + ".stale")
                print(f"    [sidecar] {os.path.basename(p)} 被占用无法删除，"
                      f"已改名隔离为 {os.path.basename(p)}.stale",
                      file=sys.stderr, flush=True)
            except OSError:
                ok = False
                print(f"    [sidecar][warn] {os.path.basename(p)} 删除与改名"
                      f"隔离均失败（文件被占用？）", file=sys.stderr, flush=True)
    return ok


def synth_sentence(client, text, voice_id, voice_style, out_path,
                  ffmpeg_path=None, speed=1.0, max_retries=3,
                  sentence_label="", model="mimo-v2.5-tts", api_timeout=30):
    """Call MiMo TTS API for a single sentence. Returns (ok, speed_applied).

    ok：TTS 音频是否成功落盘；speed_applied：atempo 变速是否落上
    （ok=True 而 speed_applied=False 时音频有效但仍是原速——调用方
    不得写 .spd marker，否则下次 --resume 会把这句永久钉在原速）。

    MiMo TTS uses chat completions format:
    - user role: voice style description (optional)
    - assistant role: text to synthesize
    - audio param: {"format": "wav", "voice": voice_id}
    - Response: base64-encoded WAV in choices[0].message.audio.data

    `speed` (default 1.0) applies a deterministic tempo change via
    ffmpeg atempo after synthesis, so the spoken rate is exactly Nx
    regardless of the TTS model's natural pace.

    `sentence_label` is included in retry/error logs for easier diagnosis
    when processing long scripts with many sentences.

    `api_timeout` (default 30s) prevents the pipeline from hanging on a
    single slow API call.
    """
    messages = []
    if voice_style:
        messages.append({"role": "user", "content": voice_style})
    messages.append({"role": "assistant", "content": text})

    audio_params = {"format": "wav"}
    if voice_id:
        audio_params["voice"] = voice_id

    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                audio=audio_params,
                timeout=api_timeout,
            )
            # 显式检查响应结构而不是直接下钻 .data：端点/模型配错时
            # message.audio 为 None，AttributeError 不带 status_code 会被
            # 归为可重试——每句白烧满 3 次 billable 调用才放弃。这里转成
            # 确定性失败（见 BadAudioResponseError），首次即整句放弃。
            audio_obj = getattr(completion.choices[0].message, "audio", None)
            audio_data = getattr(audio_obj, "data", None) if audio_obj else None
            if not audio_data:
                raise BadAudioResponseError(
                    "TTS 响应不含音频（chat.completions 返回了纯文本）——"
                    "检查 --model/--base-url 是否指向支持 audio 参数的"
                    " TTS 模型（默认 mimo-v2.5-tts），不要指向普通对话模型")
            audio_bytes = base64.b64decode(audio_data)
            with open(out_path, 'wb') as f:
                f.write(audio_bytes)
            break  # API 成功、音频已落盘，跳出重试循环
        except Exception as e:
            label = sentence_label or (text[:30] + "...")
            if _is_non_retryable(e):
                print(f"    [{label}][fatal] {e}（确定性失败，不重试）",
                      flush=True)
                return False, False
            print(f"    [{label}][retry {attempt+1}/{max_retries}] {e}",
                  flush=True)
            if attempt < max_retries - 1:
                # 线性退避 + 随机抖动：多 worker 在 429 下若同步休眠同步
                # 唤醒，会一起撞上限流窗口反复踩踏；抖动把重试时间打散
                time.sleep(2 * (attempt + 1) + random.uniform(0.0, 1.0))
    else:
        return False, False

    # 新合成 = 全新内容：先清上一轮残留 sidecar（.sha 由调用方在 synth
    # 成功后重写，不在此处动），理由见 _clear_stale_sidecars docstring。
    # 清理失败时绝不能继续 apply_speed：残留的旧 .orig.wav 会被当作原速
    # 源做变速（speed=1.0 的还原分支同理会拿旧备份覆盖新音频），新音频
    # 被整体丢弃。跳过变速只损失语速且可恢复——speed_applied=False 时
    # 调用方不写 .spd marker，下次 --resume 会重试 atempo。
    if not _clear_stale_sidecars(out_path):
        print(f"    [{sentence_label or text[:30] + '...'}][speed-skip] "
              f"残留 sidecar 无法清理，跳过变速以保护新音频（原速可用）",
              file=sys.stderr, flush=True)
        return True, False

    # Enforce deterministic speech speed via ffmpeg atempo in place.
    # 注意：这一步在 API 重试循环之外——音频已经落盘，atempo 抛异常
    # （如 subprocess 超时）时重新调 TTS 只会白烧额度。变速失败保留
    # 原速音频即可，时长由 pipeline 实测，字幕时间轴仍然准确。
    speed_applied = True
    if ffmpeg_path and abs(speed - 1.0) > 0.01:
        try:
            speed_applied = apply_speed(ffmpeg_path, out_path, speed)
        except Exception as e:
            speed_applied = False
            label = sentence_label or (text[:30] + "...")
            print(f"    [{label}][speed-skip] atempo 变速失败，保留原始语速: {e}",
                  flush=True)
    return True, speed_applied
