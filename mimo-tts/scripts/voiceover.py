"""
MiMo-V2.5-TTS 文章配音工具

将文章/长文本分段合成为语音，拼接输出完整 MP3 音频。

用法:
  python voiceover.py -t "文章正文内容..."                    # 直接传入文本
  python voiceover.py -f article.md                           # 从文件读取
  python voiceover.py -f article.txt -o output.mp3            # 指定输出路径
  python voiceover.py -f article.md -v "温柔女声"              # 指定语音风格
  python voiceover.py -f article.md --max-chars 500           # 自定义分段长度

依赖: pip install openai imageio-ffmpeg
"""

import argparse
import base64
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import xml.sax.saxutils as saxutils
from datetime import datetime

# ─── API 配置 ────────────────────────────────────────────
BASE_URL = "https://api.xiaomimimo.com/v1"
MODEL = "mimo-v2.5-tts"

# ─── 默认参数 ────────────────────────────────────────────
DEFAULT_MAX_CHARS = 300       # 每段最大字符数
DEFAULT_VOICE = "清晰自然的朗读声，语速适中，语气流畅"

# Status file for progress tracking (放在临时目录，不污染技能安装目录)
STATUS_FILE = os.path.join(tempfile.gettempdir(), "tts_voiceover_status.json")


# ─── 状态跟踪 ─────────────────────────────────────────────

def write_status(state, **kwargs):
    """Write current status to JSON file for external monitoring."""
    data = {
        "state": state,
        "updated_at": datetime.now().isoformat(),
        **kwargs,
    }
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def read_status():
    """Read the current status from JSON file."""
    if not os.path.exists(STATUS_FILE):
        return None
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def print_status():
    """Print human-readable status to stdout."""
    s = read_status()
    if not s:
        print("No TTS generation history found.")
        return

    state = s.get("state", "unknown")
    state_labels = {
        "idle": "Idle (no active task)",
        "segmenting": "Segmenting text...",
        "synthesizing": f"Synthesizing... {s.get('current_segment', '?')}/{s.get('total_segments', '?')}",
        "concatenating": "Concatenating audio...",
        "completed": "Completed",
        "failed": "Failed",
    }
    print(f"Status: {state_labels.get(state, state)}")

    if s.get("char_count"):
        print(f"Characters: {s['char_count']}")
    if s.get("total_segments"):
        print(f"Segments: {s.get('current_segment', 0)}/{s['total_segments']}")
    if s.get("output_path") and state == "completed":
        size_kb = 0
        if os.path.exists(s["output_path"]):
            size_kb = os.path.getsize(s["output_path"]) / 1024
        print(f"Output: {s['output_path']} ({size_kb:.1f} KB)")
    if s.get("duration_seconds"):
        print(f"Duration: {s['duration_seconds']:.1f}s")
    if s.get("error"):
        print(f"Error: {s['error']}")
    if s.get("updated_at"):
        print(f"Updated: {s['updated_at']}")


# ─── Toast 通知 ───────────────────────────────────────────

def notify_completion(title, message):
    """Send a Windows toast notification when TTS completes."""
    if platform.system() != "Windows":
        return
    try:
        safe_title = saxutils.escape(title)
        safe_message = saxutils.escape(message)
        ps_script = f'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null
$xml = @"
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>{safe_title}</text>
      <text>{safe_message}</text>
    </binding>
  </visual>
  <audio src="ms-winsoundevent:Notification.Default"/>
</toast>
"@
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("TTS Voiceover")
$toast = New-Object Windows.UI.Notifications.ToastNotification $doc
$notifier.Show($toast)
'''
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except Exception:
        try:
            if platform.system() == "Windows":
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass


# ─── 工具函数 ─────────────────────────────────────────────

def get_ffmpeg_exe() -> str:
    """获取 ffmpeg 可执行文件路径。"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        # 回退到系统 ffmpeg
        return "ffmpeg"


def read_input(text, file_path) -> str:
    """从文本参数或文件中读取内容。"""
    if text:
        return text.strip()
    if file_path:
        if not os.path.isfile(file_path):
            print(f"[错误] 文件不存在: {file_path}", file=sys.stderr)
            sys.exit(1)
        # 尝试 UTF-8，失败则 fallback GBK
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                with open(file_path, "r", encoding=enc) as f:
                    return f.read().strip()
            except (UnicodeDecodeError, UnicodeError):
                continue
        print(f"[错误] 无法解码文件: {file_path}", file=sys.stderr)
        sys.exit(1)
    print("[错误] 必须提供 --text 或 --file 参数", file=sys.stderr)
    sys.exit(1)


def strip_markdown(text: str) -> str:
    """
    清理 Markdown 标记，转为纯文本。
    避免 #、**、[]() 等语法字符被 TTS 读出。
    """
    # 移除代码块（保留代码内容）
    text = re.sub(r'```[\w]*\n?', '', text)
    # 移除行内代码（保留内容）
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # 移除图片标记
    text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', '', text)
    # 链接：保留文本，移除 URL
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    # 移除标题标记 (# ## ### 等)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # 移除粗体/斜体标记 (** 和 *)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    # 斜体下划线：仅匹配非单词字符边界，避免破坏 variable_name 等标识符
    text = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'\1', text)
    # 移除列表标记 (- * +)
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    # 移除有序列表标记 (1. 2. 等)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    # 移除引用标记 (>)
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
    # 移除水平线 (---, ***, ___)
    text = re.sub(r'^\s*([-*_]\s*){3,}\s*$', '', text, flags=re.MULTILINE)
    # 移除 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def segment_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list:
    """
    智能分段：优先按段落切分，段落过大时按句子切分。

    策略:
    1. 按空行/段落分隔符拆分
    2. 若单段 > max_chars，按句号/问号/感叹号等句末标点再拆
    3. 若单句仍 > max_chars，按逗号/分号拆
    4. 合并相邻短段，确保每段尽量接近 max_chars
    """
    # 清理多余空白
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 第一步：按段落拆分
    paragraphs = re.split(r'\n\s*\n|\n', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    segments = []
    for para in paragraphs:
        if len(para) <= max_chars:
            segments.append(para)
        else:
            # 段落过长，按句末标点拆分
            sentences = re.split(r'(?<=[。！？；\.\!\?;])\s*', para)
            sentences = [s.strip() for s in sentences if s.strip()]

            current = ""
            for sent in sentences:
                if len(current) + len(sent) <= max_chars:
                    current += sent
                else:
                    if current:
                        segments.append(current)
                    # 单句超长，按逗号/分号再拆
                    if len(sent) > max_chars:
                        clauses = re.split(r'(?<=[，,、：:])', sent)
                        current = ""
                        for clause in clauses:
                            if len(current) + len(clause) <= max_chars:
                                current += clause
                            else:
                                if current:
                                    segments.append(current)
                                current = clause
                    else:
                        current = sent
            if current:
                segments.append(current)

    # 合并相邻短段（中文段落间不加空格）
    merged = []
    buffer = ""
    for seg in segments:
        if len(buffer) + len(seg) <= max_chars:
            buffer = buffer + seg if buffer else seg
        else:
            if buffer:
                merged.append(buffer)
            buffer = seg
    if buffer:
        merged.append(buffer)

    # 最终保障：无标点可拆时按字符数硬切分
    final = []
    for seg in merged:
        if len(seg) <= max_chars:
            final.append(seg)
        else:
            for start in range(0, len(seg), max_chars):
                final.append(seg[start:start + max_chars])

    return final


def synthesize_segment(client, text: str, voice_desc: str, index: int,
                       max_retries: int = 3, voice_id: str = None) -> bytes:
    """合成单个音频段，返回 WAV 字节。含重试逻辑。"""
    # MiMo TTS 约定：合成文本 → assistant 角色，风格描述 → user 角色
    messages = []
    if voice_desc:
        messages.append({"role": "user", "content": voice_desc})
    messages.append({"role": "assistant", "content": text})

    audio_params = {"format": "wav"}
    if voice_id:
        audio_params["voice"] = voice_id

    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                audio=audio_params,
            )
            audio_data = completion.choices[0].message.audio.data
            audio_bytes = base64.b64decode(audio_data)
            size_kb = len(audio_bytes) / 1024
            print(f"  [OK] 段 {index+1}: {size_kb:.1f} KB")
            return audio_bytes
        except Exception as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 3
                print(f"  [重试] 段 {index+1} 失败({e})，{wait}s 后重试...")
                time.sleep(wait)
            else:
                raise RuntimeError(f"段 {index+1} 合成失败 (重试 {max_retries} 次后放弃): {e}") from e

    # Should not reach here
    raise RuntimeError(f"段 {index+1} 合成失败")


def get_audio_duration(ffmpeg_exe: str, path: str) -> float:
    """获取音频文件时长（秒）。"""
    result = subprocess.run(
        [ffmpeg_exe, "-i", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    for line in result.stderr.split("\n"):
        if "Duration" in line:
            parts = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = parts.split(":")
            return float(h) * 3600 + float(m) * 60 + float(s)
    return 0.0


def escape_ffmpeg_path(path: str) -> str:
    """Escape file path for ffmpeg concat demuxer list file.

    The concat demuxer expects paths wrapped in single quotes.
    Single quotes inside the path must be escaped as '\\'' .
    """
    # Normalize path separators
    normalized = path.replace(os.sep, "/")
    # Escape single quotes for ffmpeg concat format
    escaped = normalized.replace("'", "'\\''")
    return escaped


def concatenate_audio(ffmpeg_exe: str, wav_segments: list,
                      output_path: str) -> str:
    """
    将多段 WAV 字节拼接为单个 MP3 文件。
    API 直接返回 WAV，省去 MP3→WAV 中转步骤。
    采样率 24kHz / mono / pcm_s16le — MiMo TTS 默认输出规格。
    """
    import shutil
    tmpdir = tempfile.mkdtemp(prefix="tts_voiceover_")

    try:
        # WAV 字节 -> 临时 WAV 文件
        wav_paths = []
        for i, wav_bytes in enumerate(wav_segments):
            wav_path = os.path.join(tmpdir, f"seg_{i:03d}.wav")
            with open(wav_path, "wb") as f:
                f.write(wav_bytes)
            wav_paths.append(wav_path)

        # 拼接 WAV
        list_file = os.path.join(tmpdir, "concat.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for p in wav_paths:
                f.write(f"file '{escape_ffmpeg_path(p)}'\n")

        combined_wav = os.path.join(tmpdir, "combined.wav")
        cmd = [
            ffmpeg_exe, "-y", "-f", "concat", "-safe", "0",
            "-i", list_file, "-c", "copy", combined_wav,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError(f"WAV 拼接失败: {r.stderr}")

        # WAV -> MP3
        cmd = [
            ffmpeg_exe, "-y", "-i", combined_wav,
            "-c:a", "libmp3lame", "-b:a", "128k", output_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError(f"MP3 编码失败: {r.stderr}")

    finally:
        # 清理临时文件
        shutil.rmtree(tmpdir, ignore_errors=True)

    duration = get_audio_duration(ffmpeg_exe, output_path)
    print(f"  [完成] 拼接音频: {duration:.1f}s")
    return output_path


def validate_mp3(file_path: str) -> bool:
    """Check if the output file appears to be a valid MP3."""
    if not os.path.exists(file_path):
        return False
    file_size = os.path.getsize(file_path)
    if file_size < 512:  # Less than 512 bytes is suspicious for audio
        print(f"Warning: Output file is only {file_size} bytes, may be corrupt",
              file=sys.stderr)
        return False
    # Check for MP3 magic bytes: ID3 tag or frame sync (0xFF 0xFB / 0xFF 0xF3 / 0xFF 0xF2)
    with open(file_path, "rb") as f:
        header = f.read(4)
    if header[:3] == b'ID3':
        return True
    if header[0] == 0xFF and (header[1] & 0xE0) == 0xE0:
        return True
    print(f"Warning: File does not appear to be a valid MP3 (header: {header[:4].hex()})",
          file=sys.stderr)
    return False


def handle_filename_collision(output_path: str) -> str:
    """If output file already exists, append _2, _3 etc."""
    if not os.path.exists(output_path):
        return output_path
    base, ext = os.path.splitext(output_path)
    for n in range(2, 100):
        new_path = f"{base}_{n}{ext}"
        if not os.path.exists(new_path):
            return new_path
    return output_path


# ─── 主流程 ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MiMo-V2.5-TTS 文章配音工具 — 将文章文本合成为 MP3 语音"
    )
    input_group = parser.add_mutually_exclusive_group(required=False)
    input_group.add_argument("-t", "--text", help="直接传入文本内容")
    input_group.add_argument("-f", "--file", help="从文件读取文本 (.txt, .md 等)")

    parser.add_argument("-o", "--output", default="voiceover.mp3",
                        help="输出 MP3 文件路径 (默认: voiceover.mp3)")
    parser.add_argument("-v", "--voice", default=DEFAULT_VOICE,
                        help=f'语音风格描述 (默认: "{DEFAULT_VOICE}")')
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS,
                        help=f"每段最大字符数，短文(≤此值)整段合成 (默认: {DEFAULT_MAX_CHARS})")
    parser.add_argument("--no-segment", action="store_true",
                        help="强制不分段，整篇一次合成（仅适合短文）")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅分析分段结果，不实际调用 API")
    parser.add_argument("--status", action="store_true",
                        help="查看当前配音状态")
    parser.add_argument("--api-key", help="MiMo API Key（也可通过 MIMO_API_KEY 环境变量传入）")
    parser.add_argument("--voice-id", default=None,
                        help="预设音色 ID（Milo=男声, Chloe=女声, mimo_default=默认）。不指定则由模型自动选择")

    args = parser.parse_args()

    if args.status:
        print_status()
        return

    # --status 以外的操作都需要输入
    if not args.text and not args.file:
        parser.error("必须提供 -t/--text 或 -f/--file 参数")

    # 解析 API Key：--api-key 参数优先，fallback 到 MIMO_API_KEY 环境变量
    api_key = args.api_key or os.environ.get("MIMO_API_KEY")

    # 1. 读取输入并清理 Markdown
    text = read_input(args.text, args.file)
    if not text:
        print("[错误] 输入文本为空", file=sys.stderr)
        sys.exit(1)

    # 清理 Markdown 标记（#、**、[]()等），避免语法字符被 TTS 读出
    cleaned = strip_markdown(text)
    if len(cleaned) != len(text):
        removed = len(text) - len(cleaned)
        print(f"[清理] 移除 {removed} 个 Markdown 标记字符")
    text = cleaned

    print(f"[输入] {len(text)} 字符")
    write_status("segmenting", char_count=len(text))

    # 2. 分段
    if args.no_segment or len(text) <= args.max_chars:
        segments = [text]
        print(f"[分段] 整段合成 (1 段)")
    else:
        segments = segment_text(text, args.max_chars)
        print(f"[分段] 拆分为 {len(segments)} 段:")
        for i, seg in enumerate(segments):
            preview = seg[:50].replace("\n", "\\n")
            print(f"  段 {i+1}: {len(seg)} 字 | {preview}...")

    write_status("segmenting", char_count=len(text), total_segments=len(segments))

    if args.dry_run:
        print("\n[dry-run] 分段预览完成，未调用 API")
        sys.exit(0)

    # Handle filename collision
    output_path = handle_filename_collision(args.output)
    if output_path != args.output:
        print(f"[注意] {args.output} 已存在，改用: {output_path}")

    # Ensure output directory exists
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # 3. 初始化 API 客户端
    if not api_key:
        print("[错误] 未提供 API Key。请通过 --api-key 参数或 MIMO_API_KEY 环境变量传入。", file=sys.stderr)
        sys.exit(1)
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=BASE_URL, timeout=120)
    ffmpeg_exe = get_ffmpeg_exe()

    # 4. 分段合成
    print(f"\n[合成] 开始合成 {len(segments)} 段语音...")
    write_status("synthesizing", char_count=len(text), total_segments=len(segments),
                 current_segment=0, started_at=time.time())

    audio_segments = []
    failed_segments = []
    for i, seg in enumerate(segments):
        write_status("synthesizing", char_count=len(text), total_segments=len(segments),
                     current_segment=i + 1, started_at=time.time())
        try:
            audio_bytes = synthesize_segment(client, seg, args.voice, i,
                                              voice_id=args.voice_id)
            audio_segments.append(audio_bytes)
        except RuntimeError as e:
            print(f"  [跳过] {e}", file=sys.stderr)
            failed_segments.append(i + 1)
        # 段间延迟 0.5s，避免密集请求触发限流
        if i < len(segments) - 1:
            time.sleep(0.5)

    # Check if we got enough segments
    if not audio_segments:
        err_msg = "所有段落合成失败"
        print(f"\n[错误] {err_msg}", file=sys.stderr)
        write_status("failed", char_count=len(text), error=err_msg)
        notify_completion("TTS 配音失败", err_msg)
        sys.exit(1)

    if failed_segments:
        print(f"\n[警告] 以下段落合成失败，已跳过: {failed_segments}")

    # 5. 拼接输出
    print(f"\n[拼接] 合并 {len(audio_segments)} 段音频...")
    write_status("concatenating", char_count=len(text), total_segments=len(segments),
                 current_segment=len(segments))

    try:
        if len(audio_segments) == 1:
            # 单段：WAV 字节 -> 临时 WAV -> MP3
            import shutil
            tmpdir = tempfile.mkdtemp(prefix="tts_voiceover_")
            try:
                tmp_wav = os.path.join(tmpdir, "single.wav")
                with open(tmp_wav, "wb") as f:
                    f.write(audio_segments[0])
                cmd = [
                    ffmpeg_exe, "-y", "-i", tmp_wav,
                    "-c:a", "libmp3lame", "-b:a", "128k", output_path,
                ]
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace")
                if r.returncode != 0:
                    raise RuntimeError(f"单段 MP3 编码失败: {r.stderr}")
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
            duration = get_audio_duration(ffmpeg_exe, output_path)
            print(f"  [完成] {duration:.1f}s")
        else:
            concatenate_audio(ffmpeg_exe, audio_segments, output_path)
            duration = get_audio_duration(ffmpeg_exe, output_path)
    except RuntimeError as e:
        err_msg = f"音频拼接失败: {e}"
        print(f"\n[错误] {err_msg}", file=sys.stderr)
        write_status("failed", char_count=len(text), error=err_msg)
        notify_completion("TTS 配音失败", "音频拼接失败")
        sys.exit(1)

    # Validate output
    validate_mp3(output_path)

    size_kb = os.path.getsize(output_path) / 1024
    print(f"\n[全部完成]")
    print(f"  输出: {output_path}")
    print(f"  大小: {size_kb:.1f} KB")
    print(f"  段数: {len(audio_segments)}/{len(segments)}")
    if duration:
        print(f"  时长: {duration:.1f}s")
    print(f"\nSAVED_PATH={output_path}")

    write_status("completed", char_count=len(text), total_segments=len(segments),
                 output_path=output_path, file_size_kb=round(size_kb, 1),
                 duration_seconds=round(duration, 1),
                 failed_segments=failed_segments)
    notify_completion("TTS 配音完成!", f"{duration:.0f}s - {os.path.basename(output_path)}")


if __name__ == "__main__":
    main()
