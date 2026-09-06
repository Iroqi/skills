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

_ENV_CACHE = {}  # path -> 解析结果（.env 在单次 CLI 进程内稳定，缓存避免每次调用重复 3 编码探测）


def _parse_env_file(path):
    """解析一个 KEY=VALUE 格式的 .env 文件，返回 dict（带进程内缓存）。"""
    if path in _ENV_CACHE:
        return _ENV_CACHE[path]
    result = _parse_env_file_raw(path)
    _ENV_CACHE[path] = result
    return result


def _parse_env_file_raw(path):
    """解析一个 KEY=VALUE 格式的 .env 文件，返回 dict。

    编码探测链：utf-8-sig → utf-16 → gb18030，全部失败才放弃并明确指向
    编码问题。UnicodeDecodeError 是 ValueError 子类，不在这里接住就会从
    load_env()/get_key() 裸栈穿透——两个高概率触发路径：PowerShell 5.1
    重定向 `>` 产出 UTF-16LE 带 BOM；记事本把含中文注释的 .env 存成 ANSI
    (GBK)。爆炸点在 get_key() 内部意味着 --check-env / --dry-run 一起瘫痪，
    且报错完全不指向"编码问题"。
    """
    result = {}
    if not os.path.isfile(path):
        return result
    text = None
    for enc in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            with open(path, "r", encoding=enc) as f:
                text = f.read()
            break
        except UnicodeDecodeError:
            continue
        except (OSError, IOError) as e:
            print(f"[warn] 无法读取 {path}: {e}", file=sys.stderr)
            return result
    if text is None:
        print(f"[warn] 无法读取 {path}: 不是可识别的文本编码"
              f"(尝试过 utf-8 / utf-16 / gb18030)。"
              f"请用 UTF-8 重新保存该文件（记事本另存为右下角选 UTF-8）",
              file=sys.stderr)
        return result
    if "\x00" in text:
        # 无 BOM 的 UTF-16 能被 utf-8 "成功"解码成夹 NUL 的字符串：解析不报错、
        # 但 key 全部带 \x00 查表静默 miss，报错还指向"没有 API key"而不是编码
        # 问题。宁可在这里放弃并提示，也不静默吞掉。
        print(f"[warn] {path} 内容含 NUL 字节——多半是无 BOM 的 UTF-16 编码"
              f"(PowerShell 5.1 重定向产物)。请用 UTF-8 重新保存该文件",
              file=sys.stderr)
        return result
    for line in text.splitlines():
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
