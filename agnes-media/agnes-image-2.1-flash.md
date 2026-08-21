# Agnes Image 2.1 Flash 图像生成规格

升级版图像生成模型，优化高信息密度图像生成，支持文生图、图生图和多图合成工作流。相比 2.0 版本，更适合高信息密度图像、复杂构图和细节丰富的视觉场景。

> 本文件为 `agnes-media` 技能包的图像模型规格。认证、通用错误码、输出下载问题见主入口 `SKILL.md`。

**官方文档**：`https://wiki.agnes-ai.cn/en/docs/agnes-image-21-flash.md`

## 触发场景

- 文生图：根据自然语言提示词生成高质量图像
- 图生图：转换、优化、重绘、风格化现有图像（风格迁移、场景重打光、背景变换）
- 多图合成：使用多张参考图像组合生成新图像
- 高信息密度视觉生成：精细场景、复杂环境、丰富构图
- 典型用途：概念艺术、海报草稿、营销图片、产品照片、社交媒体封面/横幅/缩略图

## API 规范（官方）

- Endpoint：`POST https://api.agnes-ai.cn/v1/images/generations`
- 同步接口，客户端超时建议 `60s - 360s`（生成可能需要数秒到几十秒）
- 价格：`¥0.02 / image`

## 请求参数（官方）

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `model` | string | 是 | 模型名称，使用 `agnes-image-2.1-flash` |
| `prompt` | string | 是 | 图像生成或图像编辑的文本指令 |
| `size` | string | 是 | 输出尺寸档位。推荐 `1K`、`2K`、`3K`、`4K`。兼容 `1024x768` 等历史精确尺寸写法，不支持的尺寸可能被标准化 |
| `ratio` | string | 否 | 与档位式 `size` 配合的宽高比。支持 `1:1`、`3:4`、`4:3`、`16:9`、`9:16`、`2:3`、`3:2`、`21:9`，默认 `1:1` |
| `image` | string[] | 图生图/多图合成必填 | 输入图像数组，支持公共图像 URL 或 Data URI Base64。多图合成时传入多张。**注：官方参数表列此顶层字段，但官方示例实际均使用 `extra_body.image`（官方文档自身不一致），脚本实现采用 `extra_body.image`** |
| `return_base64` | boolean | 否 | 文生图需要以 Base64 返回时使用（官方记载的顶层参数，实测未生效，见文末备注） |
| `extra_body` | object | 否 | 高级工作流的附加参数 |
| `extra_body.response_format` | string | 否 | 输出格式，常见值 `url` 或 `b64_json` |
| `extra_body.image` | string[] | 图生图/多图合成必填 | 官方示例中输入图像放在 `extra_body.image` |

## 重要规则（官方 Important Notes + 接入检查清单）

1. 文生图必填参数为 `model`、`prompt`、`size`。
2. **`response_format` 禁止放在请求体顶层**。URL 输出用 `extra_body.response_format: "url"`；图生图 Base64 输出用 `extra_body.response_format: "b64_json"`。
3. **图生图不需要传递 `tags: ["img2img"]`**，只需在 `extra_body.image` 中提供输入图像。
4. 图生图和多图合成时，`extra_body.image` 为必填项。
5. 为获得可预期的输出尺寸，使用档位式 `size`（如 `2K`）并配合 `ratio`（如 `16:9`）。
6. `1920x1080`、`2560x1440` 是标准显示器分辨率但**不是**原生输出尺寸，可能被标准化（如映射为 16:9 的 1K 输出 `1312x736`）。需要此类素材时请求 `size: "2K"` + `ratio: "16:9"`（输出 `2624x1472`），再在下游裁剪或缩放。
7. 输入图像 URL 必须公开可访问（无需登录、cookie 或私有请求头）；无法公开访问时使用 Data URI Base64（`data:image/png;base64,...`）。
8. 历史精确尺寸写法的宽高必须是 16 的倍数（官方错误码文档），否则可能 500。
9. 若用户未指定尺寸：默认 `size: "2K"` 并按用途选择 `ratio`（壁纸/横幅 `16:9`，通用 `1:1`）。

## 输出尺寸参考（官方）

| Ratio | 1K | 2K | 3K | 4K |
| --- | --- | --- | --- | --- |
| `1:1` | `1024x1024` | `2048x2048` | `3072x3072` | `4096x4096` |
| `3:4` | `864x1152` | `1728x2304` | `2592x3456` | `3456x4608` |
| `4:3` | `1152x864` | `2304x1728` | `3456x2592` | `4608x3456` |
| `16:9` | `1312x736` | `2624x1472` | `3936x2208` | `5248x2944` |
| `9:16` | `736x1312` | `1472x2624` | `2208x3936` | `2944x5248` |
| `2:3` | `832x1248` | `1664x2496` | `2496x3744` | `3328x4992` |
| `3:2` | `1248x832` | `2496x1664` | `3744x2496` | `4992x3328` |
| `21:9` | `1568x672` | `3136x1344` | `4704x2016` | `6272x2688` |

## 请求示例（官方模板）

### 文生图：URL 输出

```bash
curl https://api.agnes-ai.cn/v1/images/generations \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agnes-image-2.1-flash",
    "prompt": "A luminous floating city above a misty canyon at sunrise, cinematic realism",
    "size": "2K",
    "ratio": "16:9",
    "extra_body": { "response_format": "url" }
  }'
```

### 文生图：Base64 输出（本地保存推荐）

```json
{
  "model": "agnes-image-2.1-flash",
  "prompt": "A clean product photo of a glass cube on a white studio background, soft shadows, high detail",
  "size": "1K",
  "extra_body": { "response_format": "b64_json" }
}
```

### 图生图：URL 输入 + URL 输出

```json
{
  "model": "agnes-image-2.1-flash",
  "prompt": "Transform the scene into a rain-soaked cyberpunk night with neon reflections while preserving the original composition",
  "size": "2K",
  "extra_body": {
    "image": ["https://example.com/input-image.png"],
    "response_format": "url"
  }
}
```

### 图生图：Base64 输出

```json
{
  "model": "agnes-image-2.1-flash",
  "prompt": "Make the object orange while preserving the original composition",
  "size": "2K",
  "extra_body": {
    "image": ["https://example.com/input-image.png"],
    "response_format": "b64_json"
  }
}
```

### 多图合成

```json
{
  "model": "agnes-image-2.1-flash",
  "prompt": "Combine the two characters into an intense fantasy battle scene, dynamic lighting, detailed background, cinematic composition",
  "size": "2K",
  "extra_body": {
    "image": [
      "https://example.com/character-1.png",
      "https://example.com/character-2.png"
    ],
    "response_format": "url"
  }
}
```

### Data URI Base64 输入

```json
{
  "model": "agnes-image-2.1-flash",
  "prompt": "Make the object matte black while preserving the original composition",
  "size": "2K",
  "extra_body": {
    "image": ["data:image/png;base64,BASE64_HERE"],
    "response_format": "b64_json"
  }
}
```

## 响应格式（官方）

URL 输出与 Base64 输出互斥，取值路径为 `data[0].url` 或 `data[0].b64_json`：

```json
{
  "created": 1780000000,
  "data": [
    { "url": "https://storage.googleapis.com/agnes-aigc/xxx.png", "b64_json": null, "revised_prompt": null }
  ]
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `created` | integer | 请求创建时间戳 |
| `data` | array | 生成图像结果列表 |
| `data[].url` | string / null | 生成图像 URL。Base64 输出时通常为 `null` |
| `data[].b64_json` | string / null | Base64 图像数据。URL 输出时通常为 `null` |
| `data[].revised_prompt` | string / null | 修订后的提示词（如有），否则 `null` |

## 推荐提示词结构（官方 Prompting Guide）

- **文生图**：`[主体] + [场景/环境] + [风格] + [光照] + [构图] + [质量要求]`
  - 示例：日出时分薄雾峡谷上方的发光浮空城市，电影级写实风格，广角构图，丰富的建筑细节，柔和的金色光线，高视觉密度
- **图生图**：`[改变要求] + [新风格/场景] + [需要添加或移除的元素] + [需要保留的元素]`，清晰描述什么要变、什么不变
- **多图合成**：`[参考图角色] + [目标场景] + [图像之间的关系] + [风格/光照/构图]`，说明每张参考图的角色及组合方式
- **高信息密度**：清晰描述视觉层次结构（主要主体、背景环境、重要次要细节、风格、光照、构图约束）
  - 示例：建在悬崖上的大型奇幻港口城市，数百艘小船，层叠的石桥，发光的窗户，远山，多云的日落天空，电影级奇幻写实风格，广角构图，丰富的建筑细节，高视觉密度

## 运行时执行入口

本模型的执行代码为 `scripts/gen_image.py`（用法见主入口 `SKILL.md` 的"脚本执行入口"）。脚本已内置上述全部官方规则与实测修正：

- `response_format` 固定放 `extra_body`，默认 `b64_json`
- 图生图/多图合成自动构造 `extra_body.image` 数组，本地文件自动转 data URI（20MB 上限，MIME 按文件头魔数判定）
- 输出经魔数校验（PNG/JPEG/WEBP/GIF/BMP），成功输出 `SAVED_PATH=<绝对路径>`，失败输出 `ERROR=<原因>`
- 重试：429/503 退避 2s/4s；网络错误 POST 不自动重试（防重复计费），详见主入口"重试策略"

仅当脚本无法覆盖的特殊场景（如需要 `3K`/`4K` 档位组合特殊 ratio 的批量请求）才参考上文官方模板直接调用 API。

## 实测备注（非官方文档内容）

2026-08-16 实测发现，与官方文档存在差异（脚本已按实测行为处理）：

- `return_base64: true` 实测未生效，仍返回 URL；`extra_body.response_format: "b64_json"` 实测可靠。需要 Base64 输出时优先用后者。
- 实测 `1K`（无 ratio）输出为 `1024x1024` PNG，与官方尺寸表一致。
- 2026-08-16 脚本化验证：`gen_image.py` 文生图 1K 全链路通过（b64 解码 → 魔数校验 → SAVED_PATH 输出）。
