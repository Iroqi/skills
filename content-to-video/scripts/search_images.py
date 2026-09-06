#!/usr/bin/env python3
"""StepFun 文搜图：根据新闻标题自动检索配图。

调用 StepFun search-image API（数据来自百度搜图），为每条新闻下载一张配图，
生成 images.json 供 gen_hyperframes.py --images 使用。

Usage:
  python scripts/search_images.py \
    -m audio_output/timing_manifest.json \
    -o hf-project/images \
    --api-key $STEPFUN_API_KEY

  # 自定义 topk（每张新闻取几张候选，默认 1 张直接下载）
  python scripts/search_images.py \
    -m audio_output/timing_manifest.json \
    -o hf-project/images \
    --topk 3

  # 跳过已有图片的新闻（不重新搜索/下载），并并行加速
  python scripts/search_images.py \
    -m audio_output/timing_manifest.json \
    -o hf-project/images \
    --resume --workers 4

  # 两段式：先下载候选（默认 3 张/条），由"当前对话窗口的模型"审阅，
  # 再用 --pick 指定采用哪张（0=不采用，改 ImageGen 生图）。
  python scripts/search_images.py \
    -m audio_output/timing_manifest.json \
    -o hf-project/images \
    --pick "seg1:2,seg2:1,seg3:0"

  # 直接指定标题（适用于还没跑 pipeline 的场景）
  python scripts/search_images.py \
    --titles "AI模型发布" "安全事件" "开源项目" \
    -o hf-project/images

  # 从 segments_source.json 直接取标题（不必等 TTS 产出 timing_manifest.json，
  # 可以跟 pipeline.py 并行跑，仅适用于 pipeline.py --source 结构化路径）
  python scripts/search_images.py \
    --source segments_source.json \
    -o hf-project/images

输出：
  images/ 目录下 seg1.png, seg2.png, ...
  images.json（映射 segment_id -> 图片相对路径）
"""
import argparse
import concurrent.futures
import glob
import json
import os
import re
import ssl
import sys
import time

from http.client import HTTPSConnection  # noqa: E402
from urllib.parse import urlparse  # noqa: E402

# download_image 用 urlopen 下载：自动跟随重定向（http.client 手动 GET
# 遇到 30x 会判失败，而搜图返回的 contentUrl 常见 302 跳转）
import urllib.error
import urllib.request

# ── 统一密钥查找 ──────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import get_key  # noqa: E402
from _titles import extract_titles_from_manifest  # noqa: E402
from _script_utils import setup_stdio  # noqa: E402  重定向场景 stdout 强制 UTF-8


# ===================================================================
# StepFun API
# ===================================================================

STEPFUN_API_URL = "https://api.stepfun.com/step_plan/v1/search-image"


def _make_ssl_context(relaxed=False):
    """创建 SSL 上下文。

    relaxed=True 时把安全级别降为 SECLEVEL=1（允许旧式密钥交换）——
    某些 Windows 环境默认 SECLEVEL 过高，与 StepFun 握手会失败。降级
    会放宽中间人攻击防线，因此只在默认上下文实际握手失败后作为重试
    手段（见 search_image / download_image 的 SSLError 分支），不做
    无条件全局降级。
    """
    import ssl as _ssl
    ctx = _ssl.create_default_context()
    if relaxed:
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
    return ctx


def search_image(api_key, query, topk=1, max_retries=3):
    """调用 StepFun 文搜图 API，返回 search_results 列表。

    Args:
        api_key: StepFun API Key
        query: 搜索关键词（建议中文）
        topk: 返回图片数量上限
        max_retries: 重试次数

    Returns:
        list[dict]: 每项含 contentUrl, snippet, width, height 等
    """
    body = json.dumps({"query": query, "topk": topk}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "content-to-video/1.0",
    }

    parsed = urlparse(STEPFUN_API_URL)
    import ssl as _ssl
    ctx = _make_ssl_context()
    relaxed_used = False

    for attempt in range(max_retries):
        try:
            conn = HTTPSConnection(parsed.hostname, parsed.port or 443,
                                   context=ctx, timeout=30)
            conn.request("POST", parsed.path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
            conn.close()

            if resp.status == 429:
                # 最后一次尝试直接返回空结果，不再白等 15s
                if attempt < max_retries - 1:
                    wait = 5 * (attempt + 1)
                    print(f"  [429] Rate limited, waiting {wait}s...",
                          file=sys.stderr)
                    time.sleep(wait)
                    continue
                print("  [429] Rate limited, giving up (last attempt)",
                      file=sys.stderr)
                return []

            if resp.status != 200:
                print(f"  [HTTP {resp.status}] {raw[:200]}", file=sys.stderr)
                if attempt < max_retries - 1:
                    time.sleep(2)
                continue

            # 清理可能的尾逗号（StepFun API 偶尔返回非标准 JSON）
            raw = re.sub(r',\s*([}\]])', r'\1', raw)
            data = json.loads(raw)

            if data.get("code") != 0:
                print(f"  [API error] code={data.get('code')} msg={data.get('msg')}",
                      file=sys.stderr)
                return []

            result = data.get("data", {}).get("result", {})
            if result.get("block"):
                print(f"  [blocked] Query '{query}' hit content filter", file=sys.stderr)
                return []

            return result.get("search_results", [])

        except _ssl.SSLError as e:
            # 默认安全级别握手失败：降级 SECLEVEL=1 重试一次（只降这一
            # 次，避免全局降级扩大所有请求的中间人攻击面）
            if not relaxed_used:
                relaxed_used = True
                ctx = _make_ssl_context(relaxed=True)
                print(f"  [ssl] 握手失败（{e.__class__.__name__}），"
                      f"降级 SECLEVEL=1 重试", file=sys.stderr)
                continue
            print(f"  [ssl error] {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                time.sleep(2)
        except (OSError, ConnectionError) as e:
            print(f"  [net error] {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                time.sleep(2)
        except Exception as e:
            print(f"  [error] {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                time.sleep(2)

    return []


# 单张候选图下载大小上限（20MB）：图片超过这个尺寸没有显示收益
# （optimize_image 还会压到 1040px 长边），只增加下载与内存开销
_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024

# 常见图片格式魔数（WebP 是 RIFF 容器：头 4 字节 RIFF + 偏移 8:12 处 WEBP；
# AVIF 是 ISOBMFF 容器：偏移 4:8 为 ftyp、8:12 为 brand，百度源近年常见）
_IMAGE_MAGICS = (
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",       # JPEG
    b"GIF87a",             # GIF
    b"GIF89a",             # GIF
)


def _looks_like_image(data):
    """按魔数粗判响应体是不是图片（HTML/JSON 错误页会被当候选图存盘）。

    AVIF 按 brand 精确接纳（headless Chrome 可渲染）；同为 ftyp 容器的
    HEIC Chromium 不支持，不进白名单——存下来也只会渲染成裂图。
    """
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    if data[4:8] == b"ftyp":
        return data[8:12][:4] in (b"avif", b"avis")
    return data.startswith(_IMAGE_MAGICS)


def download_image(url, out_path, max_retries=2):
    """下载图片到本地文件。

    Args:
        url: 图片 URL
        out_path: 本地保存路径
        max_retries: 重试次数

    Returns:
        bool: 下载成功返回 True
    """
    import ssl as _ssl
    ctx = _make_ssl_context()
    relaxed_used = False

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "content-to-video/1.0",
            })
            # urlopen 自动跟随重定向；timeout 30s
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                # 大小上限：超大响应整段读进内存有 OOM 面
                cl = resp.headers.get("Content-Length")
                if cl and cl.isdigit() and int(cl) > _MAX_DOWNLOAD_BYTES:
                    print(f"  [warn] Image too large ({cl} bytes), skipping",
                          file=sys.stderr)
                    return False
                data = resp.read(_MAX_DOWNLOAD_BYTES + 1)
            if len(data) > _MAX_DOWNLOAD_BYTES:
                print(f"  [warn] Image exceeds {_MAX_DOWNLOAD_BYTES} bytes, "
                      f"skipping", file=sys.stderr)
                return False
            # 简单校验：至少 1KB 才算有效图片
            if len(data) < 1024:
                print(f"  [warn] Image too small ({len(data)} bytes), skipping",
                      file=sys.stderr)
                return False
            if not _looks_like_image(data):
                # 下载时就查 magic bytes：≥1KB 的 HTML 错误页若被存成
                # .png 候选，要到渲染前的 PIL 校验才暴露，报错时机晚一整轮
                print(f"  [warn] Response is not an image (magic bytes), "
                      f"skipping", file=sys.stderr)
                return False
            with open(out_path, 'wb') as f:
                f.write(data)
            return True
        except _ssl.SSLError as e:
            # 与 search_image 同款：默认安全级别握手失败才降级重试
            if not relaxed_used:
                relaxed_used = True
                ctx = _make_ssl_context(relaxed=True)
                print(f"  [ssl] 握手失败（{e.__class__.__name__}），"
                      f"降级 SECLEVEL=1 重试", file=sys.stderr)
                continue
            print(f"  [ssl error] {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                time.sleep(1)
        except urllib.error.HTTPError as e:
            # 非 2xx 响应（对应原手动 GET 里 status != 200 的分支）
            print(f"  [HTTP {e.code}] Download failed", file=sys.stderr)
            if attempt < max_retries - 1:
                time.sleep(1)
        except (OSError, ConnectionError) as e:
            print(f"  [net error] {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                time.sleep(1)
        except Exception as e:
            print(f"  [error] {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                time.sleep(1)

    return False


def optimize_image(path, max_dim=1040, jpeg_quality=85):
    """下载后原地压缩：缩放到 max_dim（长边）以内 + 无透明通道时转 JPEG。

    HTML 里配图以 520x520 的圆角卡片展示（`references` 里的 gen_hyperframes.py
    生成的布局），没必要保留搜图 API 原样返回的大图——常见能有几千像素、
    单张数 MB。这些多出来的像素在 headless Chrome 逐帧截图时仍要参与
    解码/合成，图片越多、体积越大，越拖慢该图片可见那几秒附近的渲染，
    也让 HTML 输出目录体积不必要地膨胀。

    max_dim=1040 是 520 CSS 尺寸的 2 倍（预留 retina 屏余量），再大没有
    实际显示收益。

    返回最终文件路径（发生格式转换时扩展名会变，比如 .png -> .jpg）。
    GIF 不处理，原样保留（重新编码可能丢帧/失真，用量也很少，不值得处理）。
    Pillow 未安装、图片无法打开等情况下静默跳过优化、返回原路径——
    这一步是锦上添花，不应该因为环境缺依赖就让整条配图流程失败。
    """
    if path.lower().endswith(".gif"):
        return path
    try:
        from PIL import Image
    except ImportError:
        print("  [warn] Pillow 未安装，跳过图片压缩（体积/解码开销未优化；"
              "`pip install Pillow` 后可启用，见 SKILL.md 环境准备）",
              file=sys.stderr)
        return path

    try:
        img = Image.open(path)
        img.load()
    except Exception as e:
        print(f"  [warn] 图片压缩跳过（无法打开 {path}）: {e}", file=sys.stderr)
        return path

    orig_size = os.path.getsize(path)
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                          Image.LANCZOS)

    # 只有真的带透明像素才值得保留 alpha 通道（存成 PNG）；否则统一转 JPEG，
    # 体积通常能再小一个数量级。
    has_real_alpha = False
    if img.mode in ("RGBA", "LA", "PA"):
        alpha = img.convert("RGBA").getchannel("A")
        has_real_alpha = alpha.getextrema() != (255, 255)

    root, _ = os.path.splitext(path)
    try:
        if has_real_alpha:
            out_path = root + ".png"
            img.convert("RGBA").save(out_path, "PNG", optimize=True)
        else:
            out_path = root + ".jpg"
            img.convert("RGB").save(out_path, "JPEG", quality=jpeg_quality, optimize=True)
    except Exception as e:
        print(f"  [warn] 图片压缩保存失败，保留原图: {e}", file=sys.stderr)
        return path

    if out_path != path and os.path.exists(path):
        os.remove(path)

    new_size = os.path.getsize(out_path)
    if orig_size > 0:
        print(f"  [optimize] {orig_size // 1024}KB -> {new_size // 1024}KB "
              f"({100 * new_size // orig_size}%), {w}x{h} -> {img.size[0]}x{img.size[1]}",
              flush=True)
    return out_path


# ===================================================================
# 相关性评估（"不要瞎配图"的关键：候选必须与新闻主题贴合才使用）
# ===================================================================

def relevance_score(title, snippet):
    """标题与摘要的确定性相关度（0-100），不依赖任何 LLM。

    用字符 2-gram 重合度近似：标题的连续双字符有多少出现在摘要里。
    完全本地、零成本；宁缺毋滥——重合度低就判为不合适。
    """
    def bigrams(text):
        chars = [c for c in text.lower() if not c.isspace()]
        return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}

    tb = bigrams(title)
    sb = bigrams(snippet)
    if not tb:
        return 0
    return round(100 * len(tb & sb) / len(tb))


def _apply_picks(cand_data, picks, candidates_dir, existing_map,
                 finalize_candidate=None):
    """把 --pick 的审阅决定应用到候选数据，返回 (images_map, messages)。

    决策规则（多轮审阅友好）：
      - 显式 0：不采用该 sid——删掉它的全部候选文件、从映射里移除
        （走 ImageGen 生图兜底）；
      - 编号越界（负数或大于候选数）：报错、不采用也不删候选，留给下
        一轮修正后重跑（越界当显式 0 处理会静默删光候选）；
      - 未在 picks 里提及的 sid：候选文件与条目原样保留——分轮审阅时
        只 pick 部分段落不会作废其它段落的候选；
      - picks 里出现但 candidates.json 没有的 sid：打警告继续。

    Args:
        cand_data: candidates.json 解析出的 dict
            （sid -> {"title": str, "candidates": [{"file": str, ...}]}）
        picks: {sid: int}，编号 1-based，0 = 不采用
        candidates_dir: 候选文件所在目录（"file" 字段是相对它的文件名）
        existing_map: 已有 images.json 映射（继承已定稿条目，本轮部分
            pick 不会覆盖丢失其它条目）
        finalize_candidate: (sid, 候选绝对路径) -> 最终文件路径或 None。
            缺省为"纯改名"；main() 传入带 optimize 参数的版本（压缩可
            能改变扩展名，映射值以返回值为准）。单测可注入假实现。

    Returns:
        (images_map, messages)：可直接写盘的完整映射（值符合 images.json
        契约：字符串相对路径 "images/<file>"），与人类可读的过程消息列
        表（[OK]/[UNSUITABLE]/[error]/[warn] 前缀标识每条决策，调用方负
        责打印）。
    """
    if finalize_candidate is None:
        def finalize_candidate(sid, tmp_path):
            ext = os.path.splitext(tmp_path)[1]
            final_path = os.path.join(candidates_dir, f"{sid}{ext}")
            try:
                os.replace(tmp_path, final_path)
            except OSError:
                return None
            return final_path

    def _rm(path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    images_map = _normalize_images_map(existing_map)
    messages = []
    for sid in picks:
        if sid not in cand_data:
            messages.append(f"[warn] --pick 里的 {sid} 不在 candidates.json 中，"
                            f"已忽略（候选清单里没有这个段落）")
    for sid, info in cand_data.items():
        cands = info.get("candidates", [])
        if sid not in picks:
            # 未提及：候选文件与条目原样保留（多轮审阅，本轮只 pick 部分
            # 段落时其它段落的候选不被销毁）
            continue
        choice = picks[sid]
        if choice == 0:
            images_map.pop(sid, None)
            for c in cands:
                _rm(os.path.join(candidates_dir, c["file"]))
            messages.append(f"[UNSUITABLE] {sid}（{info.get('title', '')}）未选用，"
                            f"建议 ImageGen 生图")
            continue
        if choice < 0 or choice > len(cands):
            messages.append(
                f"[error] {sid} 的 --pick 编号 {choice} 越界（候选共 "
                f"{len(cands)} 张，有效范围 0-{len(cands)}）；本轮不采用 "
                f"{sid}，候选文件已保留，请修正编号后重跑")
            continue
        chosen = cands[choice - 1]
        final_path = finalize_candidate(
            sid, os.path.join(candidates_dir, chosen["file"]))
        if final_path:
            images_map[sid] = {"src": "images/" + os.path.basename(final_path)}
            messages.append(f"[OK] {sid} 采用候选 {choice}"
                            f"（score={chosen.get('score', '?')}）：{final_path}")
            for j, c in enumerate(cands, 1):
                if j != choice:
                    _rm(os.path.join(candidates_dir, c["file"]))
        else:
            messages.append(f"[warn] {sid} 选中候选无法落盘"
                            f"（{chosen['file']}），候选已保留待重试")
    return images_map, messages


def _normalize_images_map(images_map):
    """images.json 单一格式收敛：把继承来的字符串条目统一成对象格式。

    对象（{"src": "images/seg1.png", ...}）是唯一标准格式（图片/视频统一
    写法）；字符串仍可读取（旧文件/手写简写兼容，见 _contracts），但工具
    落盘一律写对象——避免 images.json 里两种值格式混存误导阅读。
    """
    return {sid: ({"src": val} if isinstance(val, str) else val)
            for sid, val in images_map.items()}


# ===================================================================
# Main
# ===================================================================

def _filter_title_items(title_items, sids):
    """按 --sids 过滤要搜图的段落（路由分组执行）。

    返回 (filtered_items, unknown_sids)：filtered 保序保留 id 在 sids 里的
    条目；unknown 是 sids 里没匹配到任何条目的值（通常是手滑写错 sid，
    调用方应打警告提醒，防『以为搜了其实没搜』）。sids 为空/None 时不过滤。
    """
    if not sids:
        return title_items, []
    wanted = [s.strip() for s in sids.split(",") if s.strip()]
    wanted_set = set(wanted)
    filtered = [it for it in title_items if it.get("id") in wanted_set]
    known = {it.get("id") for it in title_items}
    unknown = [s for s in wanted if s not in known]
    return filtered, unknown


def main():
    setup_stdio()
    parser = argparse.ArgumentParser(
        description="StepFun 文搜图：根据内容段落标题自动检索配图"
    )
    parser.add_argument("-m", "--manifest", default=None,
                        help="timing_manifest.json 路径（从中提取内容段落标题）")
    parser.add_argument("--source", default=None,
                        help="segments_source.json 路径（直接从稿件取标题，不必"
                             "等 TTS 产出 timing_manifest.json；仅适用于 "
                             "pipeline.py --source 结构化路径，段落 id 按位置"
                             "分配为 seg1/seg2/...）")
    parser.add_argument("--titles", nargs="+", default=None,
                        help="直接指定搜索标题列表（空格分隔）")
    parser.add_argument("-o", "--output", required=True,
                        help="图片输出目录（如 hf-project/images）")
    parser.add_argument("--api-key", default=None,
                        help="StepFun API Key（默认按两级优先级查找：环境变量 > ~/.config/ai-video/.env）")
    parser.add_argument("--topk", type=int, default=3,
                        help="每个段落最多下载几张候选图供相关性评估挑选（默认 3）")
    parser.add_argument("--json-output", default=None,
                        help="images.json 输出路径（默认放在 -o 目录下 images.json）")
    parser.add_argument("--min-width", type=int, default=0,
                        help="过滤条件：图片宽度需 >= 此值（滤掉过小的缩略图，0=不过滤）")
    parser.add_argument("--min-height", type=int, default=0,
                        help="过滤条件：图片高度需 >= 此值（滤掉过小的缩略图，0=不过滤）")
    parser.add_argument("--optimize-dim", type=int, default=1040,
                        help="下载后压缩：长边缩放到此像素以内（默认 1040，即 HTML "
                             "里 520px 展示尺寸的 2 倍/retina 余量）")
    parser.add_argument("--jpeg-quality", type=int, default=85,
                        help="转 JPEG 时的压缩质量 1-100（默认 85）")
    parser.add_argument("--no-optimize", action="store_true",
                        help="跳过下载后压缩，保留搜图 API 返回的原始文件"
                             "（体积更大，headless Chrome 渲染时解码开销更高）")
    parser.add_argument("--resume", action="store_true",
                        help="跳过已有图片的段落（不重新搜索/下载），"
                             "复用上次 images.json 的映射")
    parser.add_argument("--workers", type=int, default=4,
                        help="并行搜图/下载线程数（默认 4）")
    parser.add_argument("--pick", default=None,
                        help="审阅后指定采用：'seg1:2,seg2:0'（数字为候选编号 1-based；"
                             "0=不采用，改 ImageGen 生图）。需先不带 --pick 跑一次生成 "
                             "candidates.json")
    parser.add_argument("--sids", default=None,
                        help="只搜这些段落（逗号分隔 sid，如 'seg1,seg3'）——按 "
                             "SKILL.md 第 4 步路由只有部分段落走方式 A 时用，"
                             "其余段落按路由走方式 B/C/D，不要白搜一轮")
    args = parser.parse_args()

    # ── 加载 API Key（两级优先级：环境变量 > ~/.config）────────────
    api_key = get_key("STEPFUN_API_KEY", args.api_key)
    if not args.pick and not api_key:
        print("[error] No API key. Use --api-key or set STEPFUN_API_KEY "
              "(env > ~/.config/ai-video/.env)",
              file=sys.stderr)
        sys.exit(1)

    # ── 提取标题（仅搜索/下载模式需要；--pick 直接读 candidates.json）──
    title_items = []
    if not args.pick:
        if args.titles:
            for i, t in enumerate(args.titles, 1):
                title_items.append({"id": f"seg{i}", "title": t})
        elif args.source and os.path.exists(args.source):
            try:
                from _titles import extract_titles_from_segments_source
                title_items = extract_titles_from_segments_source(args.source)
            except ValueError as e:
                print(f"[error] {e}", file=sys.stderr)
                sys.exit(1)
        elif args.manifest and os.path.exists(args.manifest):
            try:
                title_items = extract_titles_from_manifest(args.manifest)
            except ValueError as e:
                print(f"[error] {e}", file=sys.stderr)
                sys.exit(1)
        else:
            print("[error] Need one of: --manifest, --source, or --titles",
                  file=sys.stderr)
            sys.exit(1)

        if not title_items:
            print("[warn] No segment titles found, nothing to search.", file=sys.stderr)
            sys.exit(0)

        # --sids 路由分组：只搜走方式 A 的段落（过滤掉的按路由走 B/C/D）
        if args.sids:
            _before = len(title_items)
            title_items, _unknown = _filter_title_items(title_items, args.sids)
            _skipped = _before - len(title_items)
            if _skipped:
                print(f"[route] --sids 过滤：跳过 {_skipped} 个段落"
                      f"（按第 4 步路由走方式 B/C/D，不搜图）", file=sys.stderr)
            if _unknown:
                print(f"[warn] --sids 里的未知 sid（段落列表里不存在，请检查"
                      f"是否写错）: {', '.join(_unknown)}", file=sys.stderr)
            if not title_items:
                print("[warn] --sids 过滤后没有可搜的段落。", file=sys.stderr)
                sys.exit(0)

        print(f"[search] {len(title_items)} segment titles to search:", flush=True)
        for item in title_items:
            print(f"  {item['id']}: {item['title']}", flush=True)

    # ── 创建输出目录 ──────────────────────────────────────────────
    os.makedirs(args.output, exist_ok=True)

    json_path = args.json_output or os.path.join(args.output, "..", "images.json")
    json_path = os.path.abspath(json_path)

    # 无条件读上次 images.json（--resume 只控制"跳过已定稿条目的搜索/下载"）：
    # --pick 分支靠它继承已定稿的映射——否则多轮补图时（第一轮 pick 了 seg1、
    # 第二轮只 pick seg3）images.json 会被整体覆盖成"只含 seg3"，第一轮
    # 的结果丢失，重跑 run.py 又报 seg1 缺图，陷入循环。
    existing_map = {}
    if os.path.isfile(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                existing_map = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing_map = {}

    def _existing_file(sid):
        """返回该 sid 已存在的图片文件相对路径（相对 HTML 输出目录），没有则 None。"""
        found = glob.glob(os.path.join(args.output, sid + ".*"))
        # 只认成品命名（{sid}.{ext}），候选临时文件（{sid}_candN.{ext}）不算
        finals = [p for p in found
                  if os.path.basename(p).startswith(sid + ".")]
        if finals:
            return "images/" + os.path.basename(finals[0]).replace("\\", "/")
        return None

    def _safe_remove(path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def _finalize_candidate(sid, tmp_path):
        """把选中的候选临时文件改名为最终文件名并压缩，返回最终路径或 None。"""
        ext = os.path.splitext(tmp_path)[1]
        final_path = os.path.join(args.output, f"{sid}{ext}")
        try:
            os.replace(tmp_path, final_path)
        except OSError as e:
            print(f"  [warn] 无法落盘选中候选 {tmp_path}: {e}", file=sys.stderr)
            return None
        if not args.no_optimize:
            final_path = optimize_image(final_path, max_dim=args.optimize_dim,
                                        jpeg_quality=args.jpeg_quality)
        return final_path

    candidates_path = os.path.join(args.output, "candidates.json")

    # ── --pick 模式：读 candidates.json，按"当前对话模型"审阅结果落盘 ──
    if args.pick:
        if not os.path.isfile(candidates_path):
            print("[error] 未找到 candidates.json，请先不带 --pick 运行一次"
                  "（下载候选供审阅）", file=sys.stderr)
            sys.exit(1)
        with open(candidates_path, "r", encoding="utf-8") as f:
            cand_data = json.load(f)
        picks = {}
        for pair in args.pick.split(","):
            pair = pair.strip().strip("\"'")
            if not pair:
                continue
            if ":" not in pair:
                # 静默吞掉会让审阅结论无声失效：agent 明明判断了"不采用"，
                # pick 没生效、候选被当成"未提及"保留，下一轮又出现
                print(f"[warn] --pick 条目 {pair!r} 缺少冒号分隔（应为 "
                      f"sid:候选编号，如 seg1:2；0=不采用），已忽略",
                      file=sys.stderr)
                continue
            sid, idx = pair.split(":", 1)
            try:
                picks[sid.strip().strip("\"'")] = int(idx.strip().strip("\"'"))
            except ValueError:
                print(f"[warn] --pick 条目 {pair!r} 的候选编号不是整数，已忽略",
                      file=sys.stderr)
                continue
        images_map, messages = _apply_picks(
            cand_data, picks, args.output, existing_map,
            finalize_candidate=_finalize_candidate)
        for m in messages:
            print(m, flush=True)
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(images_map, f, ensure_ascii=False, indent=2)
        rejected = [sid for sid, n in picks.items() if n == 0 and sid in cand_data]
        print(f"\n[done] 本轮 pick 后 images.json 共 {len(images_map)} 条定稿"
              f"（候选清单 {len(cand_data)} 条，未提及的候选已保留）")
        if rejected:
            print(f"[unsuitable] {', '.join(rejected)} 未选用，请用 ImageGen 生图后"
                  f"放进 {args.output}/ 并更新 {json_path}", file=sys.stderr)
        print(f"[json] {json_path}", flush=True)
        sys.exit(0)

    def _search_one(item):
        """下载该新闻的候选图，返回 (sid, candidates 或 None, reused_rel 或 None)。"""
        sid = item["id"]
        # 直接用新闻标题做搜索 query（含英文专名也原样提交；命中差时
        # 由下游 ImageGen 生图兜底，不再做中文实体替换映射）。
        query = item["title"]

        if args.resume:
            rel = _existing_file(sid) or existing_map.get(sid)
            if rel:
                # Handle both string (legacy) and dict (new format) from images.json
                if isinstance(rel, dict):
                    rel = rel.get("src", "")
                if rel:
                    fname = os.path.basename(rel)
                    if os.path.isfile(os.path.join(args.output, fname)):
                        print(f"[{sid}] [skip] 已有图片（--resume）：{rel}", flush=True)
                        return sid, None, rel

        print(f"\n[{sid}] Searching: {query}", flush=True)

        results = search_image(api_key, query, topk=args.topk)

        if not results:
            print(f"  [skip] No results for '{query}'", flush=True)
            return sid, None, None

        # 按尺寸过滤（如果指定了 --min-width / --min-height，滤掉过小的图）
        if args.min_width > 0 or args.min_height > 0:
            filtered = []
            for r in results:
                w = r.get("width", 0)
                h = r.get("height", 0)
                if (args.min_width == 0 or w >= args.min_width) and \
                   (args.min_height == 0 or h >= args.min_height):
                    filtered.append(r)
            if filtered:
                results = filtered
            else:
                print(f"  [warn] All results below size threshold, using original",
                      file=sys.stderr)

        def _ext_of(url):
            url = url.lower()
            if ".jpg" in url or ".jpeg" in url:
                return ".jpg"
            if ".gif" in url:
                return ".gif"
            if ".webp" in url:
                return ".webp"
            return ".png"

        # 下载全部候选，只有带摘要的才有资格被审阅
        candidates = []
        for i, r in enumerate(results[:args.topk], 1):
            url = r.get("contentUrl", "")
            if not url:
                continue
            snippet = (r.get("snippet") or "").strip()
            tmp_path = os.path.join(args.output, f"{sid}_cand{i}{_ext_of(url)}")
            print(f"  Downloading: {url[:80]}... ({r.get('width', '?')}x{r.get('height', '?')})",
                  flush=True)
            if snippet:
                print(f"  Snippet: {snippet[:60]}", flush=True)
            if download_image(url, tmp_path):
                if snippet:
                    candidates.append(
                        (tmp_path, snippet, r.get("width", 0), r.get("height", 0)))
                else:
                    # 无摘要无法判断相关性——按"不要瞎配图"原则直接弃用
                    print(f"  [discard] 候选无摘要，无法判断相关性：{tmp_path}",
                          flush=True)
                    _safe_remove(tmp_path)
        if not candidates:
            print(f"  [UNSUITABLE] '{query}' 无可用候选（下载失败或无摘要），"
                  f"建议 ImageGen 生图", file=sys.stderr)
        return sid, candidates or None, None

    # ── 并行下载候选 ─────────────────────────────────────────────
    images_map = _normalize_images_map(existing_map)
    reused_map = {}
    candidates_by_sid = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(_search_one, item) for item in title_items]
        for future in concurrent.futures.as_completed(futures):
            sid, candidates, rel = future.result()
            if rel:
                reused_map[sid] = rel
            if candidates:
                candidates_by_sid[sid] = candidates

    # ── dump 模式：写 candidates.json，供"当前对话模型"审阅后 --pick ──
    title_by_sid = {it["id"]: it["title"] for it in title_items}
    cand_data = {}
    for sid, cands in candidates_by_sid.items():
        cand_data[sid] = {
            "title": title_by_sid.get(sid, ""),
            "candidates": [
                {"index": j, "file": os.path.basename(p), "snippet": sn,
                 "width": w, "height": h,
                 "score": relevance_score(title_by_sid.get(sid, ""), sn)}
                for j, (p, sn, w, h) in enumerate(cands, 1)
            ],
        }
    # 多轮补图：candidates.json 不能无条件整份覆盖——上一轮已下载/已审
    # 阅段落的 snippet/score 信息会丢。本轮搜到的 sid 覆盖旧值，未涉及
    # 的 sid 保留上一轮的条目。
    merged_cand = {}
    if os.path.isfile(candidates_path):
        try:
            with open(candidates_path, "r", encoding="utf-8") as f:
                merged_cand = json.load(f)
        except (json.JSONDecodeError, OSError):
            merged_cand = {}
    merged_cand.update(cand_data)
    with open(candidates_path, "w", encoding="utf-8") as f:
        json.dump(merged_cand, f, ensure_ascii=False, indent=2)


    # 本轮零新候选（--resume 下全部段落已有成品图，或搜索全部失败）时，
    # [candidates]/[review] 审阅指引没有对象可指，打印纯属噪音——已有图片
    # 的合并写回照常进行，仍未定稿的段落由 run.py 的缺图拦截兜底
    # （[UNSUITABLE] 警告已按段落打过）。
    if cand_data:
        print("\n[candidates] 候选已下载，请用当前对话窗口的模型审阅"
              f"（清单：{candidates_path}）：", file=sys.stderr)
        for sid, info in cand_data.items():
            print(f"  {sid}（{info['title']}）:", file=sys.stderr)
            for c in info["candidates"]:
                print(f"    [{c['index']}] {c['file']} score={c['score']} "
                      f"{c['snippet'][:60]}", file=sys.stderr)
    # --resume 发现的已有成品图（目录里已有 {sid}.{ext}，如人工放的
    # seg1.png）必须合并写回 images.json（只打印 [reused] 不落盘的话，
    # run.py 的缺图拦截会一直 exit 2 死循环）。值格式
    # 遵循 images.json 单一对象格式（{"src": "images/<file>"}，见
    # _contracts.validate_images_json）；已有条目 src 一致的保留原对象
    # （不丢 loop/muted 等视频配置）。
    for sid, rel in reused_map.items():
        cur = images_map.get(sid)
        cur_src = cur.get("src") if isinstance(cur, dict) else cur
        if cur_src != rel:
            images_map[sid] = {"src": rel}
    if reused_map:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(images_map, f, ensure_ascii=False, indent=2)
        print(f"[reused] {len(reused_map)} 条已有图片（--resume）已合并写回 "
              f"{json_path}：{', '.join(reused_map)}", file=sys.stderr)
    if cand_data:
        print("\n[review] 逐条判断候选是否贴合新闻主题，然后执行"
              f"（0=不采用，改 ImageGen 生图）：", file=sys.stderr)
        print(f"  python scripts/search_images.py -o {args.output} "
              f"--pick \"seg1:2,seg2:1,seg3:0\"", file=sys.stderr)
        print("之后重跑 run.py（配图步骤会自动跳过已定稿的 images.json）。",
              file=sys.stderr)
    else:
        print(f"\n[done] 本轮无新候选下载，无需 --pick 审阅；"
              f"images.json 现有 {len(images_map)} 条定稿。",
              file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
