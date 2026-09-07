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
        dict: 包含 layout (landscape/vertical), modes (chapter/flow 叙事
        预设), animation, typography, subtitle (字幕切分参数),
        speaker (对话说话人配色) 六个顶级键。

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
    for key in ("layout", "animation", "typography", "modes",
                "subtitle", "speaker"):
        if key not in tpl:
            raise ValueError(
                f"[template] 模板文件缺少顶级键 '{key}': {tpl_path}"
            )

    _TEMPLATE_CACHE["default"] = tpl
    return tpl


# 叙事模式预设的必备键（引擎声明的"定义面"）：gen_hyperframes 与 run.py
# 消费这些键做生产，缺任何一个都是坏定义——加新键时同步此元组与
# config/template.json 的两个预设。
_MODE_PRESET_FLAGS = ("numbered", "openingPreview", "closingRecap",
                      "transition", "verticalOpeningCover")


def get_mode_preset(manifest):
    """manifest 的叙事契约（顶层 flow 布尔）→ template.json modes 预设。

    1.5.69 起 flow/章节的全部版式差异定义在 template.json 的 modes 块
    （template 定义、gen/run 生产）：gen_hyperframes 只消费预设渲染，
    run.py 的竖屏开场封面图拦截也读同一预设——模式改什么不再散落两处
    代码分支。manifest 顶层 "flow": true（pipeline 由 segments_source.json
    顶层透传）只负责"选 flow 预设"，选择之外不携带任何视觉决策。

    Args:
        manifest: 已加载的 timing manifest dict。

    Returns:
        dict: 预设（numbered/openingPreview/closingRecap/transition/
              verticalOpeningCover 五键齐全）。

    Raises:
        ValueError: 预设缺失、缺键或枚举键取值非法——不做静默回退，
            坏定义宁可响亮失败（与 load_template/_theme 同一原则）。
    """
    name = "flow" if manifest.get("flow") else "chapter"
    preset = load_template().get("modes", {}).get(name)
    if not isinstance(preset, dict):
        raise ValueError(
            f"[template] modes 块缺少 '{name}' 预设（叙事模式由 manifest "
            "顶层 flow 布尔选定；预设定义见 config/template.json）")
    missing = [k for k in _MODE_PRESET_FLAGS if k not in preset]
    if missing:
        raise ValueError(
            f"[template] 模式预设 '{name}' 缺少定义键: {', '.join(missing)}"
            "（modes 预设必须五键齐全，改 config/template.json 时同步）")
    if preset["openingPreview"] not in ("agenda", "chips"):
        raise ValueError(
            f"[template] 模式预设 '{name}' 的 openingPreview 必须是 "
            f"'agenda' 或 'chips'，当前: {preset['openingPreview']!r}")
    if preset["transition"] not in ("wipe", "crossfade"):
        raise ValueError(
            f"[template] 模式预设 '{name}' 的 transition 必须是 "
            f"'wipe' 或 'crossfade'，当前: {preset['transition']!r}")
    return preset
