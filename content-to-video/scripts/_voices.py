#!/usr/bin/env python3
"""预置音色注册表加载器（数据定义在 config/voices.json）。

MiMo TTS 预置的 8 个音色。pipeline.py / run.py 的 --voice-id choices、
以及 _contracts.py 对 segments_source 里 voice_id 的取值校验，都从这里读，
避免"音色列表在 CLI help / 校验 / 文档里各硬编码一份"的漂移。

voices.json 是唯一权威数据源；缺失/损坏/为空时响亮报错，不做静默回退
（与 _theme / _template 同一原则）。
"""
import json
import os

_VOICE_CACHE = None


def _load_voices():
    """加载 config/voices.json；缺失/损坏/条目非法时抛错。"""
    global _VOICE_CACHE
    if _VOICE_CACHE is not None:
        return _VOICE_CACHE
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "config", "voices.json",
    )
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"[voices] 音色注册表不存在: {path}（技能安装不完整，"
            "请重新同步/安装 skill）"
        )
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"[voices] 音色注册表不是合法 JSON: {path} ({e})"
            ) from e
    ids = data.get("voice_ids")
    if not ids or not isinstance(ids, list) or not all(
            isinstance(v, str) and v for v in ids):
        raise ValueError(
            f"[voices] 音色注册表缺少非空 'voice_ids' 字符串列表: {path}")
    _VOICE_CACHE = ids
    return _VOICE_CACHE


def list_voice_ids():
    """返回全部预置音色 ID（副本），供 CLI --voice-id 的 choices 动态生成。"""
    return list(_load_voices())


def is_valid_voice_id(voice_id):
    """判断 voice_id 是否属于预置音色。"""
    return voice_id in _load_voices()
