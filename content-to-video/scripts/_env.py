#!/usr/bin/env python3
"""
统一环境变量加载模块（两级查找）。

查找优先级：
  1. 系统环境变量（os.environ）—— CI/CD 场景
  2. ~/.config/ai-video/.env —— 用户级持久化，分享 skill 不泄露

所有需要读密钥的脚本统一调用 get_key() 或 load_env()。
"""

import os
import sys


# 用户级 .env 路径
_USER_ENV_PATH = os.path.join(os.path.expanduser("~"), ".config", "ai-video", ".env")
DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"


def _parse_env_file(path):
    """解析一个 KEY=VALUE 格式的 .env 文件，返回 dict。"""
    result = {}
    if not os.path.isfile(path):
        return result
    try:
        # utf-8-sig 剥掉 Windows 记事本保存的 BOM：不剥的话首行 key 带
        # \ufeff 前缀，MIMO_API_KEY 放第一行时查表永远 miss，报错还指向
        # "key 没生效"而不是编码问题（对无 BOM 文件行为与 utf-8 一致）
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    # 兼容 shell 习惯写法（export KEY=VALUE）：
                    # key 上的 export 前缀剥掉，否则查表永远 miss
                    if k.startswith("export "):
                        k = k[len("export "):].strip()
                    v = v.strip()
                    # 剥离值两端成对的引号（"sk-xxx" / 'sk-xxx'）：
                    # 很多 .env 模板带引号，原样读入会把引号一起带给
                    # OpenAI SDK，得到 401 且报错不指向真正原因
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("\"", "'"):
                        v = v[1:-1]
                    if k:
                        result[k] = v
    except (OSError, IOError) as e:
        print(f"[warn] 无法读取 {path}: {e}", file=sys.stderr)
    return result


def load_env():
    """
    按优先级合并两级 .env 来源，返回合并后的 dict。
    高优先级的值覆盖低优先级：
      ~/.config/ai-video/.env < os.environ
    """
    merged = {}
    user_env = _parse_env_file(_USER_ENV_PATH)
    merged.update(user_env)

    # 最高优先：系统环境变量（只取非空值）
    for k, v in os.environ.items():
        if v:
            merged[k] = v

    return merged


def get_key(name, cli_value=None):
    """
    获取单个密钥值。

    优先级：cli_value > os.environ > ~/.config/ai-video/.env > None

    参数：
        name: 环境变量名（如 MIMO_API_KEY）
        cli_value: 命令行参数传入的值（最高优先）

    返回：
        密钥字符串，或 None（未找到）
    """
    if cli_value:
        return cli_value

    # os.environ
    val = os.environ.get(name)
    if val:
        return val

    # ~/.config/ai-video/.env
    user_env = _parse_env_file(_USER_ENV_PATH)
    val = user_env.get(name)
    if val:
        return val

    return None


def env_source_info(name):
    """
    返回某个 key 实际来自哪个来源（用于诊断 / --check-env）。
    返回字符串如 "os.environ" / "~/.config/ai-video/.env" / "not found"
    """
    if os.environ.get(name):
        return "os.environ"
    user_env = _parse_env_file(_USER_ENV_PATH)
    if name in user_env:
        return os.path.expanduser("~/.config/ai-video/.env")
    return "not found"


def resolve_model_config(cli_model, cli_base_url, model_env_name, default_model):
    """按 CLI > 环境变量/配置文件 > 默认值 解析模型名与 API base URL。

    与密钥一样走"环境变量 > ~/.config/ai-video/.env"两级查找：
      - model: cli_model > env[model_env_name] > default_model
      - base_url: cli_base_url > env[MIMO_BASE_URL] > DEFAULT_BASE_URL

    pipeline.py（TTS 模型）使用此函数，避免各脚本重复实现同一套解析逻辑。
    """
    env = load_env()
    model = cli_model or env.get(model_env_name) or default_model
    base_url = cli_base_url or env.get("MIMO_BASE_URL") or DEFAULT_BASE_URL
    return model, base_url
