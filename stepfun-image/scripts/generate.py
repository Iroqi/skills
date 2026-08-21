#!/usr/bin/env python3
"""StepFun Unified Image Generator — txt2img / img2img / edit

Usage:
    python generate.py txt2img --prompt "星空下的狐狸" --api-key <KEY>
    python generate.py img2img --prompt "宫崎骏风格" --source photo.jpg --api-key <KEY>
    python generate.py edit --prompt "戴上帽子" --image photo.png --api-key <KEY>

Output Contract:
    Success: exit 0, last stdout line = SAVED_PATH=<path>
    Failure: exit 1, error on stderr
"""
import argparse
import base64
import io
import json
import os
import re
import ssl
import sys
import time
import uuid
from http.client import HTTPSConnection
from pathlib import Path
from urllib.parse import urlparse

HOST = "api.stepfun.com"
API_KEY_ENV = "STEP_API_KEY"

# ── CLI ───────────────────────────────────────────────────────

def parse_args():
    top = argparse.ArgumentParser(description="StepFun Image Generator")
    top.add_argument("--api-key", default=None, help="StepFun API key (or set STEP_API_KEY)")
    top.add_argument("--output", default=".", help="Output directory")
    top.add_argument("--filename", default=None, help="Custom filename (no extension)")
    top.add_argument("--seed", type=int, default=0)
    top.add_argument("--steps", type=int, default=None)
    top.add_argument("--cfg-scale", type=float, default=None)

    sub = top.add_subparsers(dest="mode", required=True)

    # txt2img
    t2 = sub.add_parser("txt2img")
    t2.add_argument("--prompt", required=True)
    t2.add_argument("--model", default="step-image-edit-2",
                     choices=["step-image-edit-2", "step-2x-large"])
    t2.add_argument("--size", default="1024x1024")
    t2.add_argument("--negative-prompt", default=None)
    t2.add_argument("--text-mode", action="store_true")

    # img2img
    i2 = sub.add_parser("img2img")
    i2.add_argument("--prompt", required=True)
    i2.add_argument("--source", required=True, help="Source image URL or local path")
    i2.add_argument("--weight", type=float, default=0.5)
    i2.add_argument("--size", default="1024x1024")

    # edit
    ed = sub.add_parser("edit")
    ed.add_argument("--prompt", required=True)
    ed.add_argument("--image", required=True, help="Image file path to edit")
    ed.add_argument("--negative-prompt", default=None)
    ed.add_argument("--text-mode", action="store_true")

    return top.parse_args()

# ── SSL ───────────────────────────────────────────────────────

def make_ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    return ctx

# ── Multipart Encoder (stdlib only) ──────────────────────────

def encode_multipart(fields, files):
    """Build multipart/form-data body. fields={name:str}, files={name:(filename,data,ctype)}"""
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    for k, v in fields.items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        body.write(f"{v}\r\n".encode())
    for k, (fname, fdata, ctype) in files.items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'.encode())
        body.write(f"Content-Type: {ctype}\r\n\r\n".encode())
        body.write(fdata if isinstance(fdata, bytes) else fdata.encode())
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", body.getvalue()

# ── Source Loading ─────────────────────────────────────────────

def load_source_as_base64(path):
    """Local file → base64 data URI for img2img."""
    p = Path(path)
    if not p.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)
    if p.stat().st_size > 10 * 1024 * 1024:
        print(f"Error: File too large ({p.stat().st_size / 1024 / 1024:.1f}MB > 10MB)", file=sys.stderr)
        sys.exit(1)
    suffix = p.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    with open(p, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"

def resolve_source(source):
    """URL → pass through; local → base64."""
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        return source
    return load_source_as_base64(source)

# ── HTTP Helpers ──────────────────────────────────────────────

def http_post_json(path, body_dict, api_key, max_retries=3):
    """POST JSON, return parsed response dict. Retries on 429 / connection errors."""
    payload = json.dumps(body_dict).encode("utf-8")
    for attempt in range(max_retries):
        try:
            ctx = make_ssl_ctx()
            conn = HTTPSConnection(HOST, context=ctx, timeout=180)
            conn.request("POST", path, body=payload, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "StepFun-Image/1.0",
            })
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
            conn.close()
            if resp.status == 200:
                return json.loads(raw)
            if resp.status == 429:
                wait = _retry_delay(raw, attempt)
                print(f"Rate limited (429), retry {attempt+1}/{max_retries} after {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            _fail(f"API error {resp.status}: {_err_msg(raw)}")
        except (OSError, ConnectionError, ssl.SSLError) as e:
            if attempt < max_retries - 1:
                wait = 5 * (attempt + 1)
                print(f"Connection error: {e}, retry {attempt+1}/{max_retries} after {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            _fail(f"Connection failed after {max_retries} retries: {e}")
    _fail(f"Failed after {max_retries} retries")

def http_post_multipart(path, fields, files, api_key, max_retries=3):
    """POST multipart/form-data, return parsed response dict."""
    content_type, body = encode_multipart(fields, files)
    for attempt in range(max_retries):
        try:
            ctx = make_ssl_ctx()
            conn = HTTPSConnection(HOST, context=ctx, timeout=180)
            conn.request("POST", path, body=body, headers={
                "Content-Type": content_type,
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "StepFun-Image/1.0",
            })
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
            conn.close()
            if resp.status == 200:
                return json.loads(raw)
            if resp.status == 429:
                wait = _retry_delay(raw, attempt)
                print(f"Rate limited (429), retry {attempt+1}/{max_retries} after {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            _fail(f"API error {resp.status}: {_err_msg(raw)}")
        except (OSError, ConnectionError, ssl.SSLError) as e:
            if attempt < max_retries - 1:
                wait = 5 * (attempt + 1)
                print(f"Connection error: {e}, retry {attempt+1}/{max_retries} after {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            _fail(f"Connection failed after {max_retries} retries: {e}")
    _fail(f"Failed after {max_retries} retries")

def _retry_delay(raw, attempt):
    try:
        err = json.loads(raw)
        ds = err.get("error", {}).get("retryDelay", "")
        m = re.search(r"(\d+)", ds)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 10 * (attempt + 1)

def _err_msg(raw):
    try:
        return json.loads(raw).get("error", {}).get("message", raw[:500])
    except Exception:
        return raw[:500]

def _fail(msg):
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)

# ── Save Result ───────────────────────────────────────────────

def save_result(data, output_dir, filename=None):
    os.makedirs(output_dir, exist_ok=True)
    items = data.get("data", [])
    if not items:
        _fail("No image data in response")
    item = items[0] if isinstance(items, list) else items
    finish = item.get("finish_reason", "")
    if finish != "success":
        print(f"Generation stopped: finish_reason={finish}", file=sys.stderr)
        if finish == "content_filtered":
            print("Content filtered by safety check", file=sys.stderr)
        sys.exit(1)

    seed = item.get("seed", 0)
    ext = ".png"

    if "b64_json" in item and item["b64_json"]:
        img_bytes = base64.b64decode(item["b64_json"])
        if img_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            ext = ".png"
        elif img_bytes[:2] == b"\xff\xd8":
            ext = ".jpg"
        base = filename or f"stepfun_{seed}"
        save_path = _unique_path(output_dir, base, ext)
        with open(save_path, "wb") as f:
            f.write(img_bytes)
    elif "url" in item and item["url"]:
        img_url = item["url"]
        parsed = urlparse(img_url)
        pl = parsed.path.lower()
        ext = ".jpg" if pl.endswith((".jpg", ".jpeg")) else ".png"
        base = filename or f"stepfun_{seed}"
        save_path = _unique_path(output_dir, base, ext)
        _download(img_url, save_path)
    else:
        _fail("No b64_json or url in response")

    status = {"status": "completed", "seed": seed, "finish_reason": finish, "output": str(save_path.resolve())}
    with open(Path(output_dir) / "status.json", "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)
    return str(save_path.resolve())

def _unique_path(d, base, ext):
    p = Path(d) / f"{base}{ext}"
    c = 2
    while p.exists():
        p = Path(d) / f"{base}_{c}{ext}"
        c += 1
    return p

def _download(url, dest):
    parsed = urlparse(url)
    ctx = make_ssl_ctx()
    try:
        conn = HTTPSConnection(parsed.netloc, context=ctx, timeout=60)
        conn.request("GET", parsed.path + ("?" + parsed.query if parsed.query else ""))
        resp = conn.getresponse()
        if resp.status != 200:
            _fail(f"Download failed: HTTP {resp.status}")
        with open(dest, "wb") as f:
            f.write(resp.read())
        conn.close()
    except Exception:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "StepFun-Image/1.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
            with open(dest, "wb") as f:
                f.write(r.read())

# ── Mode Handlers ─────────────────────────────────────────────

def do_txt2img(args, api_key):
    body = {
        "model": args.model,
        "prompt": args.prompt[:512],
        "size": args.size,
        "seed": args.seed,
        "response_format": "b64_json",
    }
    if args.steps is not None:
        body["steps"] = args.steps
    if args.cfg_scale is not None:
        body["cfg_scale"] = args.cfg_scale
    if args.negative_prompt and args.model == "step-image-edit-2":
        body["negative_prompt"] = args.negative_prompt[:512]
    if args.text_mode and args.model == "step-image-edit-2":
        body["text_mode"] = True
    print(f"[txt2img] model={args.model}, prompt='{args.prompt[:60]}...'", file=sys.stderr)
    return http_post_json("/v1/images/generations", body, api_key)

def do_img2img(args, api_key):
    source = resolve_source(args.source)
    body = {
        "model": "step-2x-large",
        "prompt": args.prompt[:1024],
        "source_url": source,
        "source_weight": args.weight,
        "size": args.size,
        "seed": args.seed,
        "response_format": "b64_json",
    }
    if args.steps is not None:
        body["steps"] = args.steps
    if args.cfg_scale is not None:
        body["cfg_scale"] = args.cfg_scale
    print(f"[img2img] prompt='{args.prompt[:60]}...', weight={args.weight}", file=sys.stderr)
    return http_post_json("/v1/images/image2image", body, api_key)

def do_edit(args, api_key):
    img_path = Path(args.image)
    if not img_path.exists():
        _fail(f"Image file not found: {args.image}")
    suffix = img_path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    with open(img_path, "rb") as f:
        img_data = f.read()

    fields = {
        "model": "step-image-edit-2",
        "prompt": args.prompt[:512],
        "response_format": "b64_json",
    }
    if args.seed:
        fields["seed"] = str(args.seed)
    if args.steps is not None:
        fields["steps"] = str(args.steps)
    if args.cfg_scale is not None:
        fields["cfg_scale"] = str(args.cfg_scale)
    if args.negative_prompt:
        fields["negative_prompt"] = args.negative_prompt[:512]
    if args.text_mode:
        fields["text_mode"] = "true"

    files = {"image": (img_path.name, img_data, mime)}
    print(f"[edit] prompt='{args.prompt[:60]}...', image={img_path.name}", file=sys.stderr)
    return http_post_multipart("/v1/images/edits", fields, files, api_key)

# ── Main ──────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()

    api_key = args.api_key or os.environ.get(API_KEY_ENV, "")
    if not api_key:
        _fail(f"API key required. Use --api-key or set {API_KEY_ENV}")

    handler = {"txt2img": do_txt2img, "img2img": do_img2img, "edit": do_edit}[args.mode]
    data = handler(args, api_key)
    saved = save_result(data, args.output, args.filename)
    print(f"Image saved to: {saved}", file=sys.stderr)
    print(f"SAVED_PATH={saved}")

if __name__ == "__main__":
    main()
