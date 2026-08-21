#!/usr/bin/env python3
"""StepFun TTS Synthesizer — step-tts-mini / step-tts-2 / stepaudio-2.5-tts

Usage:
    python synthesize.py --text "你好世界" --voice cixingnansheng --api-key <KEY>
    python synthesize.py -f article.txt --voice boyinnansheng --speed 1.5 --api-key <KEY>
    python synthesize.py --text "..." --model stepaudio-2.5-tts --instruction "愤怒" --api-key <KEY>
    python synthesize.py --text "..." --timestamp --api-key <KEY>

Output Contract:
    Success: exit 0, last stdout line = SAVED_PATH=<path>
    With --timestamp: also outputs <basename>.subtitles.json
    Failure: exit 1, error on stderr
"""
import argparse
import base64
import json
import os
import re
import ssl
import subprocess
import sys
import time
from http.client import HTTPSConnection
from pathlib import Path
from urllib.parse import urlparse

HOST = "api.stepfun.com"
TTS_PATH = "/v1/audio/speech"
MAX_CHARS = 950  # safe limit per request (API max 1000)
API_KEY_ENV = "STEP_API_KEY"


# ── CLI ───────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="StepFun TTS Synthesizer")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--text", "-t", help="Direct text input")
    src.add_argument("--file", "-f", help="Text file path (.txt/.md)")
    p.add_argument("--voice", default="cixingnansheng", help="Voice ID, default cixingnansheng")
    p.add_argument("--model", default="step-tts-mini",
                   choices=["step-tts-mini", "step-tts-2", "stepaudio-2.5-tts"])
    p.add_argument("--speed", type=float, default=1.0, help="Speed 0.5~2.0, default 1.0")
    p.add_argument("--volume", type=float, default=1.0, help="Volume 0.1~2.0, default 1.0")
    p.add_argument("--format", default="mp3", choices=["mp3", "wav", "flac", "opus", "pcm"])
    p.add_argument("--sample-rate", type=int, default=24000,
                   choices=[8000, 16000, 22050, 24000, 48000])
    p.add_argument("--instruction", default=None,
                   help="Global emotion instruction (stepaudio-2.5-tts only, max 200 chars)")
    p.add_argument("--emotion", default=None, help="Emotion tag (non-2.5 models only)")
    p.add_argument("--style", default=None, help="Style tag (non-2.5 models only)")
    p.add_argument("--negative-prompt", default=None, help="Not supported by TTS, ignored")
    p.add_argument("--timestamp", action="store_true", help="Output word-level timestamps JSON")
    p.add_argument("--output", "-o", default=None, help="Output file path")
    p.add_argument("--api-key", default=None, help="StepFun API key (or set STEP_API_KEY)")
    p.add_argument("--max-chars", type=int, default=MAX_CHARS, help="Max chars per segment")
    p.add_argument("--dry-run", action="store_true", help="Show segmentation without calling API")
    return p.parse_args()


# ── SSL ───────────────────────────────────────────────────────

def make_ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    return ctx


# ── Text Processing ───────────────────────────────────────────

def strip_markdown(text):
    """Remove Markdown syntax to avoid TTS reading markup characters."""
    # Code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Images (before links)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    # Links → keep text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Bold / italic
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    # Strikethrough
    text = re.sub(r"~~([^~]+)~~", r"\1", text)
    # Blockquotes
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
    # Horizontal rules
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # List markers
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def segment_text(text, max_chars):
    """Split text into segments ≤ max_chars, preferring sentence boundaries."""
    if len(text) <= max_chars:
        return [text]

    segments = []
    # Split by paragraphs first
    paragraphs = re.split(r"\n\s*\n", text)
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # If single paragraph fits, add it
        if len(current) + len(para) + 1 <= max_chars:
            current = f"{current}\n{para}".strip() if current else para
            continue

        # Flush current buffer
        if current:
            segments.append(current)
            current = ""

        # If paragraph itself is too long, split by sentences
        if len(para) > max_chars:
            sentences = re.split(r"(?<=[。！？；.!?])\s*", para)
            buf = ""
            for sent in sentences:
                if not sent.strip():
                    continue
                if len(buf) + len(sent) <= max_chars:
                    buf += sent
                else:
                    if buf:
                        segments.append(buf)
                    # If single sentence > max_chars, hard split
                    if len(sent) > max_chars:
                        for i in range(0, len(sent), max_chars):
                            segments.append(sent[i:i + max_chars])
                        buf = ""
                    else:
                        buf = sent
            current = buf
        else:
            current = para

    if current:
        segments.append(current)

    return segments if segments else [text[:max_chars]]


# ── API Call ──────────────────────────────────────────────────

def call_tts(body, api_key, max_retries=3):
    """Call TTS API, return (audio_bytes, subtitles_or_None)."""
    payload = json.dumps(body).encode("utf-8")

    for attempt in range(max_retries):
        try:
            ctx = make_ssl_ctx()
            conn = HTTPSConnection(HOST, context=ctx, timeout=180)
            conn.request("POST", TTS_PATH, body=payload, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "StepFun-TTS/1.0",
            })
            resp = conn.getresponse()

            if resp.status == 200:
                content_type = resp.getheader("Content-Type", "")
                if "application/json" in content_type:
                    # JSON response (timestamp mode with return_url)
                    raw = resp.read().decode("utf-8")
                    conn.close()
                    data = json.loads(raw)
                    audio_url = data.get("data", {}).get("url", "")
                    subtitles = data.get("data", {}).get("subtitles")
                    audio_bytes = download_audio(audio_url)
                    return audio_bytes, subtitles
                else:
                    # Binary audio response
                    audio_bytes = resp.read()
                    conn.close()
                    return audio_bytes, None

            if resp.status == 429:
                raw = resp.read().decode("utf-8")
                conn.close()
                wait = 10 * (attempt + 1)
                try:
                    err = json.loads(raw)
                    ds = err.get("error", {}).get("retryDelay", "")
                    m = re.search(r"(\d+)", ds)
                    if m:
                        wait = int(m.group(1))
                except Exception:
                    pass
                print(f"Rate limited (429), retry {attempt+1}/{max_retries} after {wait}s",
                      file=sys.stderr)
                time.sleep(wait)
                continue

            raw = resp.read().decode("utf-8")
            conn.close()
            try:
                msg = json.loads(raw).get("error", {}).get("message", raw[:500])
            except Exception:
                msg = raw[:500]
            print(f"API error {resp.status}: {msg}", file=sys.stderr)
            sys.exit(1)

        except (OSError, ConnectionError, ssl.SSLError) as e:
            if attempt < max_retries - 1:
                wait = 5 * (attempt + 1)
                print(f"Connection error: {e}, retry {attempt+1}/{max_retries} after {wait}s",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"Connection failed after {max_retries} retries: {e}", file=sys.stderr)
            sys.exit(1)

    print(f"Failed after {max_retries} retries", file=sys.stderr)
    sys.exit(1)


def download_audio(url):
    """Download audio from URL with SSL workaround."""
    parsed = urlparse(url)
    ctx = make_ssl_ctx()
    try:
        conn = HTTPSConnection(parsed.netloc, context=ctx, timeout=120)
        conn.request("GET", parsed.path + ("?" + parsed.query if parsed.query else ""))
        resp = conn.getresponse()
        if resp.status != 200:
            print(f"Download failed: HTTP {resp.status}", file=sys.stderr)
            sys.exit(1)
        data = resp.read()
        conn.close()
        return data
    except Exception as e:
        print(f"Download error: {e}", file=sys.stderr)
        sys.exit(1)


# ── Audio Concatenation ──────────────────────────────────────

def concat_audio(segments_data, output_path, fmt):
    """Concatenate multiple audio segments. Try ffmpeg, fallback to byte concat."""
    if len(segments_data) == 1:
        with open(output_path, "wb") as f:
            f.write(segments_data[0])
        return

    # Try ffmpeg
    try:
        tmp_dir = output_path.parent / "_tts_tmp"
        tmp_dir.mkdir(exist_ok=True)
        list_file = tmp_dir / "concat_list.txt"
        seg_files = []

        for i, data in enumerate(segments_data):
            seg_path = tmp_dir / f"seg_{i}.{fmt}"
            with open(seg_path, "wb") as f:
                f.write(data)
            seg_files.append(seg_path)

        with open(list_file, "w", encoding="utf-8") as f:
            for sp in seg_files:
                f.write(f"file '{sp.as_posix()}'\n")

        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-c", "copy", str(output_path)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            # Cleanup temp
            for sp in seg_files:
                sp.unlink(missing_ok=True)
            list_file.unlink(missing_ok=True)
            tmp_dir.rmdir()
            return
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    # Fallback: byte concatenation (works for MP3)
    print("ffmpeg not available, using byte concatenation", file=sys.stderr)
    with open(output_path, "wb") as f:
        for data in segments_data:
            f.write(data)


# ── Main ──────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()

    api_key = args.api_key or os.environ.get(API_KEY_ENV, "")
    if not api_key:
        print(f"Error: API key required. Use --api-key or set {API_KEY_ENV}", file=sys.stderr)
        sys.exit(1)

    # Load text
    if args.file:
        fp = Path(args.file)
        if not fp.exists():
            print(f"Error: File not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        text = fp.read_text(encoding="utf-8")
        if fp.suffix.lower() in (".md", ".markdown"):
            text = strip_markdown(text)
    else:
        text = args.text

    text = text.strip()
    if not text:
        print("Error: Empty text", file=sys.stderr)
        sys.exit(1)

    # Segment
    segments = segment_text(text, args.max_chars)
    print(f"Text: {len(text)} chars, {len(segments)} segment(s)", file=sys.stderr)

    if args.dry_run:
        for i, seg in enumerate(segments):
            print(f"  Segment {i+1}: {len(seg)} chars — {seg[:60]}...")
        return

    # Build API body template
    body_tpl = {
        "model": args.model,
        "voice": args.voice,
        "response_format": args.format,
        "sample_rate": args.sample_rate,
        "speed": args.speed,
        "volume": args.volume,
    }

    # Model-specific params
    if args.model == "stepaudio-2.5-tts":
        if args.instruction:
            body_tpl["instruction"] = args.instruction[:200]
        if args.emotion or args.style:
            print("Warning: emotion/style tags not supported by stepaudio-2.5-tts, "
                  "use --instruction instead", file=sys.stderr)
    else:
        if args.instruction:
            print("Warning: --instruction only works with stepaudio-2.5-tts, ignoring",
                  file=sys.stderr)
        voice_label = {}
        if args.emotion:
            voice_label["emotion"] = args.emotion
        if args.style:
            voice_label["style"] = args.style
        if voice_label:
            body_tpl["voice_label"] = voice_label

    # Timestamp mode
    if args.timestamp:
        body_tpl["return_url"] = True
        body_tpl["timestamp"] = True

    # Synthesize each segment
    all_audio = []
    all_subtitles = []
    time_offset = 0

    for i, seg in enumerate(segments):
        body = dict(body_tpl)
        body["input"] = seg
        print(f"Synthesizing segment {i+1}/{len(segments)} ({len(seg)} chars)...", file=sys.stderr)

        audio_bytes, subtitles = call_tts(body, api_key)
        all_audio.append(audio_bytes)

        if subtitles:
            # Adjust timestamps by accumulated duration
            for sub in subtitles:
                for item in sub.get("items", []):
                    item["start_time"] += time_offset
                    item["end_time"] += time_offset
                all_subtitles.append(sub)
            # Estimate duration from last timestamp
            if subtitles and subtitles[-1].get("items"):
                last = subtitles[-1]["items"][-1]
                time_offset = last["end_time"]

    # Determine output path
    if args.output:
        out_path = Path(args.output)
    else:
        ext = f".{args.format}"
        out_path = Path(f"stepfun_tts_{int(time.time())}{ext}")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Concatenate / save
    concat_audio(all_audio, out_path, args.format)

    # Save subtitles if requested
    if args.timestamp and all_subtitles:
        sub_path = out_path.with_suffix(".subtitles.json")
        with open(sub_path, "w", encoding="utf-8") as f:
            json.dump(all_subtitles, f, indent=2, ensure_ascii=False)
        print(f"Subtitles saved to: {sub_path}", file=sys.stderr)

    # Status file
    status = {
        "status": "completed",
        "model": args.model,
        "voice": args.voice,
        "segments": len(segments),
        "output": str(out_path.resolve()),
        "format": args.format,
        "sample_rate": args.sample_rate,
        "speed": args.speed,
    }
    with open(out_path.parent / "status.json", "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)

    file_size = out_path.stat().st_size
    print(f"Output: {out_path} ({file_size / 1024:.1f} KB)", file=sys.stderr)
    print(f"SAVED_PATH={out_path.resolve()}")


if __name__ == "__main__":
    main()
