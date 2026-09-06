#!/usr/bin/env python3
"""Shared title extraction utilities.

Single source of truth for extracting segment titles from timing manifests.
All scripts that need title extraction should import from here:

    from _titles import extract_titles_from_manifest
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _contracts import (load_timing_manifest, load_segments_source,  # noqa: E402
                        is_content_sid)


def extract_titles_from_manifest(manifest_path):
    """从 timing_manifest.json 提取内容段落（seg/news 前缀）标题。

    Returns:
        list[dict]: [{"id": "seg1", "title": "..."}, ...]
    """
    manifest = load_timing_manifest(manifest_path)

    segments = manifest.get("segments", [])
    titles = []
    for seg in segments:
        sid = seg.get("id", "")
        # 为 news/seg 段落提取标题（跳过 opening/closing）
        if is_content_sid(sid):
            title = seg.get("title", "").strip()
            if title:
                # 清理标题：去掉尾部标点（API 对尾部标点敏感）
                title = title.rstrip("，。！？、；：“”‘’\"'（）【】")
                titles.append({"id": sid, "title": title})
    return titles


def extract_titles_from_segments_source(source_path):
    """从 segments_source.json 直接提取标题，不依赖 timing_manifest.json。

    只覆盖 `pipeline.py --source`（结构化输入）这一条路径——run.py 的 TTS 命令
    固定走这条路径，本函数的 id 分配规则严格对应 build_from_structured.py 的
    `_collect_blocks`：段落 id 永远是按位置分配的 `seg{i}`（1-based，不读取
    自定义 'id' 字段，dialogue 段落也一样），跟 pipeline.py --source 最终写进
    timing_manifest.json 的 id 完全一致，因此本函数的输出可以放心跟
    extract_titles_from_manifest() 的输出互换使用。

    存在的意义：让配图（search_images.py）不必等 TTS（pipeline.py）跑完产出
    timing_manifest.json 才能拿到标题去搜图——两步只要都不依赖对方的实际产物，
    run.py 就可以把它们并行跑，省下配图搜索那部分的墙钟时间。

    Returns:
        list[dict]: [{"id": "seg1", "title": "..."}, ...]
    """
    source = load_segments_source(source_path)
    titles = []
    for i, seg in enumerate(source.get("segments", []), 1):
        title = (seg.get("title") or "").strip()
        if title:
            title = title.rstrip("，。！？、；：“”‘’\"'（）【】")
            titles.append({"id": f"seg{i}", "title": title})
    return titles
