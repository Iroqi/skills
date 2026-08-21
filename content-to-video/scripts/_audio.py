#!/usr/bin/env python3
"""ffmpeg 音频操作（从 pipeline.py 拆出）。

包含：时长测量、静音生成、atempo 变速、拼接、BGM 混音。
所有函数都只依赖"ffmpeg 路径 + 参数"，不碰 TTS/网络，可脱离 pipeline 单独测试。
"""
import math
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ffmpeg import parse_duration  # noqa: E402


def measure_duration(ffmpeg_path, audio_path):
    """Measure audio duration using ffmpeg -i (ffprobe not available in imageio-ffmpeg).

    Uses a regex to parse the `Duration: HH:MM:SS.xx` line from stderr, which
    is more robust than string-splitting against locale / format variations.
    Returns 0.0 if parsing fails (callers should treat 0.0 as invalid).

    显式捕获 subprocess.TimeoutExpired —— 原裸 `except Exception` 虽也能接住，
    但 30s 超时通常意味着 ffmpeg 卡死（罕见但可能），单独记日志便于诊断。
    """
    try:
        result = subprocess.run(
            [ffmpeg_path, "-i", audio_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30
        )
        stderr = result.stderr or ""
        dur = parse_duration(stderr)
        if dur is not None:
            return dur
    except subprocess.TimeoutExpired:
        print(f"  [duration] ffmpeg -i timed out on {audio_path}",
              file=sys.stderr)
    except Exception as e:
        print(f"  [duration] error measuring {audio_path}: {e}",
              file=sys.stderr)
    return 0.0


def generate_silence(ffmpeg_path, duration, out_path):
    """Generate a silent WAV file of given duration.

    Primary path: ffmpeg lavfi (anullsrc). Fallback: Python wave module —
    used when the resolved ffmpeg is a minimal build (e.g. system PATH
    ffmpeg with `--disable-everything`) that does not support the lavfi
    demuxer, which would otherwise crash concat_audio and abort the whole
    pipeline. The Python fallback produces a standards-compliant 16-bit
    PCM mono WAV at 24kHz, matching the format ffmpeg -ar/-ac would emit.

    两条路都失败时抛 RuntimeError 而不是落一个 0 字节空文件——空文件混进
    concat 要么整链失败要么被静默丢弃，而调用方（pipeline 的静音兜底分支）
    已经按"异常=兜底失败"处理，能正确走 skip 路径，不会带着坏文件错位时间轴。
    """
    # encoding/errors 显式指定：中文 Windows 下 text=True 默认按 cp936 解码
    # ffmpeg stderr（UTF-8），输出路径含中文时会先抛 UnicodeDecodeError 而
    # 不是走兜底。TimeoutExpired 同样落入 wave 兜底（lavfi 卡死 30s 的
    # ffmpeg 写不出比 Python wave 更好的静音）。
    try:
        result = subprocess.run([
            ffmpeg_path, "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", str(duration), "-ar", "24000", "-ac", "1", out_path
        ], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30)
    except subprocess.TimeoutExpired:
        result = None
    if (result is not None and result.returncode == 0
            and os.path.exists(out_path) and os.path.getsize(out_path) > 0):
        return
    # Fallback: write silent WAV via Python wave module (no ffmpeg lavfi needed)
    try:
        import wave as _wave
        sr = 24000
        n_frames = int(duration * sr)
        with _wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)  # 16-bit
            w.setframerate(sr)
            # Silent frames = all zeros（bytes 直乘，比 struct.pack 巨型参数列表便宜得多）
            w.writeframes(b"\x00" * (2 * n_frames))
        return
    except Exception as e:
        raise RuntimeError(
            f"生成静音文件失败（ffmpeg lavfi 与 Python wave 兜底都不可用）: "
            f"{out_path}: {e}") from e


def validate_speed(speed):
    """校验语速倍率必须是 >0 的有限数值，非法时抛 ValueError。

    集中在这里供 _contracts.py（segments_source 里的 speed 字段）和
    build_atempo_filter（最底层）共用，避免两处各写一份判断而漂移。
    """
    if not isinstance(speed, (int, float)) or isinstance(speed, bool):
        raise ValueError(f"speed 必须是数值（收到 {speed!r}）")
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError(f"speed 必须是大于 0 的有限数值（收到 {speed!r}）")
    return speed


def build_atempo_filter(speed):
    """Build an ffmpeg atempo filter chain.

    A single atempo instance only supports 0.5x–2.0x, so decompose a
    wider range into chained instances (e.g. 3.0x -> atempo=2.0,atempo=1.5).

    入口先做一次 validate_speed：speed<=0（或非有限值）时，下面第二个
    `while remaining < 0.5` 会因 `remaining /= 0.5` 对非正数永远不收敛而死循环，
    这里改为立即抛 ValueError，而不是挂死。
    """
    validate_speed(speed)
    if abs(speed - 1.0) < 0.01:
        return None
    factors = []
    remaining = speed
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(round(remaining, 4))
    return ",".join(f"atempo={f}" for f in factors)


def apply_speed(ffmpeg_path, wav_path, speed, prev_speed=None):
    """Apply a tempo change to a WAV via ffmpeg atempo.

    Returns True on success. Used to enforce a deterministic speech-speed
    multiplier (e.g. 1.5x) regardless of what the TTS model produces.

    Non-destructive: the original TTS WAV is preserved as `<wav_path>.orig.wav`
    on first invocation. Subsequent re-application (e.g. switching from 1.5x
    to 1.2x in a later run) reads from the original instead of compounding
    atempo on an already-modified file. When speed=1.0 and an original backup
    exists, the backup is restored to wav_path so the audio returns to natural
    pace without re-calling the TTS API.

    `prev_speed`（可选）：当前 wav 已被施加过的语速（来自 .spd sidecar，
    pipeline 的 resume 分支传入）。仅在 `<wav_path>.orig.wav` 原始备份丢失
    时生效：此时无法回到原速再变速，改为补偿变速 atempo(speed/prev_speed)
    ——数学上等价于从原速一次变速到 speed，避免"已 1.5x 的文件被当作原速
    备份、再叠 1.2x 实际变成 1.8x"的静默叠加变速。
    """
    filt = build_atempo_filter(speed)
    orig_path = wav_path + ".orig.wav"

    # 原始备份丢失、但调用方告知当前文件已被变速过：走补偿变速，
    # 且不能再把当前（已变速的）文件备份成 .orig.wav——那会让下一次
    # 换速继续在错误的基础上叠加。
    compensate = (prev_speed is not None and prev_speed > 0
                  and abs(prev_speed - 1.0) > 0.01
                  and not os.path.exists(orig_path))
    if compensate:
        comp_filt = build_atempo_filter(speed / prev_speed)
        if comp_filt is None:
            return True  # speed == prev_speed，文件已在目标速率
        filt = comp_filt

    # speed=1.0 means "restore original pace". If a backup exists (audio was
    # previously sped up), copy it back over wav_path so the file reflects 1.0x.
    # Without this branch, a previous 1.5x run would leave the audio stuck at
    # 1.5x even though the user now requests 1.0x.
    if not filt:
        if os.path.exists(orig_path):
            try:
                import shutil
                shutil.copy2(orig_path, wav_path)
                return True
            except OSError as e:
                print(f"  [speed-restore] failed to restore orig: {e}",
                      file=sys.stderr)
                return False
        return True  # nothing to do (no prior speed change, no backup needed)

    if not compensate:
        # Preserve the original TTS output once, so we can re-apply atempo from
        # a clean source on subsequent runs (avoid double-sped audio).
        if not os.path.exists(orig_path):
            try:
                import shutil
                shutil.copy2(wav_path, orig_path)
            except OSError:
                # If backup fails, continue with in-place (destructive) behavior
                orig_path = wav_path
    tmp = wav_path + ".spd.tmp.wav"
    # 补偿变速必须以当前文件为源（它就是最新状态）；常规路径优先用原速备份
    src = wav_path if compensate else (
        orig_path if os.path.exists(orig_path) and orig_path != wav_path else wav_path)
    try:
        result = subprocess.run([
            ffmpeg_path, "-y", "-i", src,
            "-filter:a", filt,
            "-ar", "24000", "-ac", "1", tmp
        ], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60)
    except subprocess.TimeoutExpired:
        # 超时异常原先直接穿透（tmp 残留）；统一转成 False 走失败清理
        _remove_quiet(tmp)
        print("  [speed-skip] atempo timeout", file=sys.stderr)
        return False
    if result.returncode == 0 and os.path.exists(tmp):
        os.replace(tmp, wav_path)
        return True
    # atempo 失败时及时清掉半写的 tmp（多次失败堆积会留磁盘残渣）
    _remove_quiet(tmp)
    print(f"  [speed-skip] atempo failed: {result.stderr[-200:]}",
          file=sys.stderr)
    return False


def _remove_quiet(path):
    """尽力删文件（失败清理用，删不掉也不吭声）。"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def concat_audio(ffmpeg_path, file_list, gap_sec, out_path):
    """Concatenate audio files with silence gaps. Uses absolute paths (Windows safe)."""
    out_dir = os.path.dirname(os.path.abspath(out_path))
    list_file = os.path.join(out_dir, "_concat_list.txt")
    silence_file = os.path.join(out_dir, "_silence.wav")

    if gap_sec > 0:
        try:
            generate_silence(ffmpeg_path, gap_sec, silence_file)
        except RuntimeError as e:
            # generate_silence 现在失败时抛错（不再落 0 字节空文件）；
            # concat 的错误契约是返回 bool，这里转成 False 而不是裸栈。
            print(f"  [concat] gap 静音生成失败: {e}", file=sys.stderr)
            return False

    with open(list_file, 'w', encoding='utf-8') as f:
        for i, fp in enumerate(file_list):
            # Always use absolute paths — relative paths fail silently on Windows
            abs_fp = os.path.abspath(fp).replace("\\", "/")
            # 路径含单引号时按 ffmpeg concat demuxer 规则转义（'\'' =
            # 关引号-转义引号-重开引号），英文用户名 O'Brien 这类会炸
            _esc = abs_fp.replace("'", "'\\''")
            f.write(f"file '{_esc}'\n")
            if i < len(file_list) - 1 and gap_sec > 0:
                abs_silence = os.path.abspath(silence_file).replace("\\", "/")
                f.write(f"file '{abs_silence}'\n")

    # encoding/errors 显式指定（理由同 generate_silence）；TimeoutExpired
    # 单独接住——原先直接穿透，跳过下方临时文件清理且裸栈到 main。
    result = None
    try:
        result = subprocess.run([
            ffmpeg_path, "-y", "-f", "concat", "-safe", "0",
            "-i", list_file, "-c", "copy", out_path
        ], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120)
    except subprocess.TimeoutExpired:
        print("  [concat] ffmpeg concat 超时（copy 路径），尝试重编码",
              file=sys.stderr)

    if result is None or result.returncode != 0:
        # Fallback: re-encode (handles codec mismatch)
        try:
            result = subprocess.run([
                ffmpeg_path, "-y", "-f", "concat", "-safe", "0",
                "-i", list_file, "-ar", "24000", "-ac", "1", out_path
            ], capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120)
        except subprocess.TimeoutExpired:
            print("  [concat] ffmpeg concat 超时（重编码路径）", file=sys.stderr)
            result = None
        if result is not None and result.returncode != 0:
            print(f"  [concat stderr] {result.stderr[-500:]}", file=sys.stderr)

    # Cleanup temp files
    for tmp in [list_file, silence_file]:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    return result is not None and result.returncode == 0


def mix_bgm(ffmpeg_path, voice_path, bgm_path, bgm_volume, out_path):
    """Mix background music under voice audio. BGM loops to match voice duration.

    全程使用固定的 bgm_volume。
    """
    volume_filter = f"volume={bgm_volume}"
    # normalize=0：amix 默认把每路输入各乘 1/inputs（两路即人声 -6dB），
    # 带 BGM 的成片会系统性比不带的一半响度；关掉 normalize 后音量
    # 关系完全交给 volume_filter 控制
    try:
        result = subprocess.run([
            ffmpeg_path, "-y",
            "-i", voice_path,
            "-i", bgm_path,
            "-filter_complex",
            # aloop size expects an integer; 2e+09 (Python float literal) would
            # be passed verbatim into the ffmpeg filter string and may fail to
            # parse on some ffmpeg builds, causing BGM loop to silently break.
            f"[1:a]{volume_filter},aloop=loop=-1:size=2000000000[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=3:normalize=0",
            "-ar", "24000", "-ac", "1", out_path
        ], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300)
    except subprocess.TimeoutExpired:
        print("  [BGM mix failed] ffmpeg 混音超时", file=sys.stderr)
        return False
    if result.returncode != 0:
        print(f"  [BGM mix failed] {result.stderr[-300:]}", file=sys.stderr)
        return False
    return True


def build_loudnorm_filter(target_lufs=-16.0):
    """构建 ffmpeg loudnorm 滤镜串（单遍，目标整体响度 target_lufs LUFS）。

    目标 -16 LUFS 是网络视频/播客常见响度；TP/LRA 用固定值即可。
    """
    return f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"


def apply_loudnorm(ffmpeg_path, in_path, out_path, target_lufs=-16.0):
    """对整条音频做响度归一化，输出到 out_path。返回是否成功。

    用于把逐句 TTS 拼出来的音频统一到目标响度（跨句/跨视频音量一致）。在 concat
    之后、对 combined 整段做，loudnorm 只做增益、不做变速，不改变句子间相对时序，
    字幕时间轴仍按 timing_manifest.json 的实测值对齐。
    """
    filt = build_loudnorm_filter(target_lufs)
    try:
        result = subprocess.run([
            ffmpeg_path, "-y", "-i", in_path,
            "-af", filt,
            "-ar", "24000", "-ac", "1", out_path,
        ], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120)
    except subprocess.TimeoutExpired:
        # 同模块其它 ffmpeg 封装都显式接 TimeoutExpired，唯独这里漏了：
        # ffmpeg 卡死时用户直接吃裸栈
        print("  [loudnorm] ffmpeg timeout (120s)", file=sys.stderr)
        return False
    if result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return True
    print(f"  [loudnorm] failed: {result.stderr[-200:]}", file=sys.stderr)
    return False
