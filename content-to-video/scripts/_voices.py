#!/usr/bin/env python3
"""预置音色注册表（单一权威来源）。

MiMo TTS 预置的 8 个音色。pipeline.py / run.py 的 --voice-id choices、
以及 _contracts.py 对 segments_source 里 voice_id 的取值校验，都从这里读，
避免"音色列表在 CLI help / 校验 / 文档里各硬编码一份"的漂移（教训见
_theme.py 的 accent 色板收口）。
"""

VOICE_IDS = ["冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean"]


def list_voice_ids():
    """返回全部预置音色 ID（副本），供 CLI --voice-id 的 choices 动态生成。"""
    return list(VOICE_IDS)


def is_valid_voice_id(voice_id):
    """判断 voice_id 是否属于预置音色。"""
    return voice_id in VOICE_IDS
