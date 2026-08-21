"""视觉模板加载（从 gen_hyperframes.py 拆出）。

模板文件位于 config/template.json，描述布局尺寸 + 动画参数 + 字体排印。

config/template.json 是本模块的唯一权威数据源。历史上这里还有一份
硬编码的 `_default_template_fallback`，声称"与 template.json 保持一致"，但两处
很容易漂移（例如改 template.json 忘了同步 fallback），一旦模板文件缺失/损坏，
布局会**静默回退到旧参数**，还完全不报错——正是"双数据源"问题。现在删掉
fallback：模板缺失/损坏时直接抛错，宁可失败得响亮，也不要静默用错参数。
"""
import json
import os

_TEMPLATE_CACHE = {}


def load_template():
    """加载视觉模板配置（布局尺寸 + 动画参数 + 字体排印）。

    模板文件是唯一权威数据源；缺失/损坏时抛出明确错误（不静默回退）。

    Returns:
        dict: 包含 layout (landscape/vertical), animation, typography 三个顶级键。

    Raises:
        FileNotFoundError: 模板文件不存在。
        ValueError: 模板文件不是合法 JSON，或缺少必要顶级键。
    """
    if "default" in _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE["default"]

    tpl_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "config", "template.json",
    )
    if not os.path.isfile(tpl_path):
        raise FileNotFoundError(
            f"[template] 模板文件不存在: {tpl_path}（技能安装不完整，"
            "请重新同步/安装 skill）"
        )
    with open(tpl_path, "r", encoding="utf-8") as f:
        try:
            tpl = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"[template] 模板文件不是合法 JSON: {tpl_path} ({e})"
            ) from e
    for key in ("layout", "animation", "typography"):
        if key not in tpl:
            raise ValueError(
                f"[template] 模板文件缺少顶级键 '{key}': {tpl_path}"
            )

    _TEMPLATE_CACHE["default"] = tpl
    return tpl
