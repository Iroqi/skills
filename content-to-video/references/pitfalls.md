# 关键陷阱参考（18 条踩坑记录）

本文件收录制作解说视频过程中遇到的具体踩坑与解决方案（TTS 混音、字幕同步、Hyperframes 渲染引擎、ffmpeg 细节等）。首次执行工作流前建议通读一遍，之后遇到对应报错或现象时按需回查。


> **路径 / 缓存等内部机制 →** 排查诡异的路径或缓存问题时看 `references/internals.md`（数据流全景、路径解析速记、缓存与断点续跑）；日常制作无需看，第 3 步记得加 `--resume` 即可。

## 目录

- 1. MiMo TTS 中英混合
- 2. 字幕与配音同步
- 3. Hyperframes GSAP 动画陷阱
- 4. Hyperframes lint 要求
- 5. MiMo TTS API 格式
- 6. imageio-ffmpeg 无 ffprobe
- 7. 静音生成需要显式输入源
- 8. ffmpeg concat 必须用绝对路径（Windows）
- 9. ImageGen 配图的 images.json 路径是相对 HTML 的
- 10. 内置动画始终生效（不依赖 ImageGen）
- 11. GSAP exit 动画必须加 hard kill
- 12. 转场 wipe 元素的布局定位技巧
- 13. 浅色主题下字幕栏/卡片边框硬编码深色导致对比度失效
- 14. 渲染引擎对嵌套 position:absolute 元素的百分比/auto 宽高计算错误，导致标题挤成一列
- 15. 结构化路径（`build_from_structured.py`）遗漏了自动摘要，导致画面只有标题没有正文
- 16. `npx hyperframes render` 文件已经写完但进程不退出，导致 agent 卡住
- 17. 开场/结尾标题位置 `topOther` 只在 template.json 里改没用，代码里从没读过它
- 18. `images.json` 引用了不存在的图片 → 渲染出空白裂图，且不报错

---

### 1. MiMo TTS 中英混合

**现状**：MiMo TTS 原生支持中英混合文本，英文术语（OpenAI、ChatGPT 等）直接发送即可，无需音译转换。

**注意**：以英文术语开头的句子（如 "OpenAI与HackingFace联合披露…"），TTS 可能整句用英文语调，这是模型局限，换 TTS 模型可解决。

**voice style 建议**：
- 新闻播报风格：`"专业新闻播报，语速适中，语气沉稳自信，中英文表达流畅自然"`
- 博客讲解风格：`"清晰自然的讲解风格，语速适中，语气亲切流畅，娓娓道来，适合知识科普与资讯解读"`
- 中文为主的视频建议搭配 `--voice-id 冰糖`（中文女声）或 `--voice-id 苏打`（中文男声）

### 2. 字幕与配音同步

**问题**：按字符速率估算字幕时间必然偏移，因为 TTS 实际语速不均匀。

**解决**：本 skill 采用**逐句生成 + 逐句测量**的方案。每句 TTS 独立生成并精确测量时长，`timing_manifest.json` 中的 `start_time` 和 `duration` 是实测值。渲染引擎用这些实测值驱动字幕显示。

**Hyperframes 特别注意**：composition 的 `data-duration` 必须等于 `combined.wav` 的实测时长，不能是 manifest 中累加的理论值（累加值包含最后一个句子后的多余 gap）。用 `ffmpeg -i combined.wav` 实测后写入 manifest 的 `total_duration`。

### 3. Hyperframes GSAP 动画陷阱

**问题**：子元素 CSS 设了 `opacity:0`，GSAP 的 `tl.from({opacity:0})` 从 0 动画回 CSS 当前值（也是 0）= 无效动画，元素永远不可见。另外，GSAP 从 CDN 加载失败会导致 timeline 注册崩溃、视频黑屏（这也是 `_assets.py` 把 GSAP 本地缓存的动机）。

**解决**：子元素（tagline、body-text 等）**不要**在 CSS 中设 `opacity:0`。父级 clip 的 `style="opacity:0"` 已控制整体可见性，子元素靠 GSAP `from` 动画从 0 淡入到 1 即可。

### 4. Hyperframes lint 要求

- `<audio>` 元素必须有 `id` 属性
- 同 track 的 clip 不能时间重叠
- 系统字体（Microsoft YaHei 等）需 `@font-face { src: local(...) }` 声明
- 先运行 `npx hyperframes check` 确认 0 error 再渲染

### 5. MiMo TTS API 格式

**问题**：MiMo TTS 不支持 `client.audio.speech.create()` 接口，调用会返回错误。

**解决**：MiMo TTS 使用 `client.chat.completions.create()` 格式。user 角色传语音风格描述，assistant 角色传要合成的文本，`audio` 参数指定 `{"format": "wav", "voice": "冰糖"}`。响应中 base64 编码的音频在 `completion.choices[0].message.audio.data`。模型名和 API 基地址可通过 `--model` / `--base-url` 参数或 `MIMO_TTS_MODEL` / `MIMO_BASE_URL` 环境变量覆盖（默认 `mimo-v2.5-tts` 与 `https://api.xiaomimimo.com/v1`）。

### 6. imageio-ffmpeg 无 ffprobe

**问题**：`imageio-ffmpeg` 打包的二进制文件名不是 `ffmpeg.exe`，且没有附带的 `ffprobe.exe`。

**解决**：`pipeline.py` 使用 `ffmpeg -i <file>` 命令，从 stderr 中解析 `Duration:` 行来测量时长，不依赖 ffprobe。

### 7. 静音生成需要显式输入源

**问题**：ffmpeg 的 `-f lavfi` 格式必须配合 `-i` 指定输入源，否则报错。

**解决**：`generate_silence` 函数使用 `-f lavfi -i anullsrc=r=24000:cl=mono` 作为输入，再用 `-t` 控制时长。

### 8. ffmpeg concat 必须用绝对路径（Windows）

**问题**：ffmpeg concat demuxer 的 `file 'xxx'` 列表中如果使用相对路径，在 Windows 上会静默失败（生成 0 字节输出或不报错但内容错误）。

**解决**：`_audio.py` 的 `concat_audio()` 函数（`pipeline.py` 调用）已强制将所有路径转为 `os.path.abspath()` 并统一为正斜杠。手动调用 ffmpeg concat 时也必须使用绝对路径。

### 9. ImageGen 配图的 images.json 路径是相对 HTML 的

**问题**：`gen_hyperframes.py --images images.json` 中的图片路径是相对于 HTML 输出目录的，不是相对于 images.json 本身。

**解决**：images.json 中的路径如 `"images/seg1.png"` 表示图片位于 `{HTML输出目录}/images/seg1.png`。如果 HTML 在 `hf-project/index.html`，图片应在 `hf-project/images/` 下。

### 10. 内置动画始终生效（不依赖 ImageGen）

`gen_hyperframes.py` 生成的 HTML 始终包含以下动画，无论是否传入 `--images`：
- **accent 竖条**：左侧 6px 竖条，从顶部生长（`scaleY`）
- **背景 glow**：径向渐变光晕，淡入效果
- **body 逐行 stagger**：每行 `body-line` 有独立 ID，依次滑入（行间 0.15s 延迟）

当 `--images` 传入时，额外启用：图片容器 + 右滑入动画 + 标题左移布局。没有配图时视频仍有丰富的动效，纯文字版不单调。

### 11. GSAP exit 动画必须加 hard kill

**问题**：Hyperframes linter 检测到 `tl.to("#clip",{opacity:0,duration:X})` exit 动画结束后没有 `tl.set("#clip",{opacity:0})` hard kill，会报 `gsap_exit_missing_hard_kill` 错误。非线性 seek（跳转播放）可能落在 fade 之后，导致旧段落残留可见。

**解决**：每段 fade-out 后必须紧跟一个 `tl.set` hard kill：
```js
tl.to("#clip",{opacity:0,duration:0.3,ease:"power2.in"},end_time);
tl.set("#clip",{opacity:0},end_time + 0.3);  // hard kill
```
且 fade-out duration（0.3s）必须 < 段落间 gap（默认 0.4s），否则 exit 结束时间会与下一段 start 重合，linter 仍会报错。

### 12. 转场 wipe 元素的布局定位技巧

**问题**：wipe 元素如果用 `inset:0`（覆盖全屏）+ `transform:translateX(-100%)`（移到屏幕外），Hyperframes Layout checker 不会识别 transform，认为元素始终覆盖全屏，报 `text_occluded` 错误。但如果改用 `left` 属性做动画，Motion checker 又会报 `gsap_non_transform_motion`（要求用 transform 而非 layout 属性做动画）。

**解决**：CSS 用 `left:-100%` 把布局位置设到屏幕外（骗过 Layout checker），GSAP 用 `x` transform 做动画从 `0` 到 `200%`（满足 Motion checker）：
```css
#twipe{position:absolute;top:0;left:-100%;width:100%;height:100%;z-index:50}
```
```js
tl.fromTo("#twipe",{x:0},{x:"200%",duration:0.4,ease:"power2.inOut"},start-0.2);
```

### 13. 浅色主题下字幕栏/卡片边框硬编码深色导致对比度失效

**问题**：新增 `light`/`paper`/`cream` 等浅色主题时，发现底部字幕栏渐变（`.sub-bar`）、字幕文字投影（`.sub-text` text-shadow）、卡片左侧强调边框（`.body-text` border-left）这三处一直是**写死的黑色/白色**，没有跟着 `theme_colors` 走。深色主题（`dark`/`neon`）下这些硬编码值刚好"恰好正确"（黑底配白字投影、白色卡片边框在深色背景上能看清），于是这个问题被藏了很久没暴露——直到真正认真测一遍浅色主题才发现：字幕栏渐变到不透明黑色，配上浅色主题的深色字幕文字，几乎读不出来；卡片的白色边框在米白/浅色背景上也几乎不可见。

**解决**：给每个主题的配色字典都加上 `sub_bar_rgb`（字幕栏渐变的基础色，不含 alpha）、`sub_text_shadow`（字幕文字投影，深色主题用深色投影衬白字，浅色主题用浅色投影衬深色字）、`soft_border`（卡片边框色）三个 key，`.sub-bar`/`.sub-text`/`.body-text` 都从硬编码值改成读 `theme_colors[...]`。**以后新增主题时，这三个 key 必须一起配齐**，漏配任何一个都可能导致对应元素在这个新主题下文字读不清、边框看不见——不会报错，只会在实际画面里才看出来，容易被忽略。

### 14.【重大】渲染引擎对嵌套 position:absolute 元素的百分比/auto 宽高计算错误，导致标题挤成一列

**问题**：`.seg-title-wrap`（标题容器）是 `.seg-card{position:absolute;inset:0}` 的子元素，且用 `width:auto`（配合 `left:0;right:0`）来"占满父容器"——这是完全标准、随处可见的 CSS 写法。但在某个基于老旧 WebKit 内核的离线渲染工具下会整体计算错误：子元素的可用宽度被算成只有几十像素，导致标题文字每个字/每个单词都单独换行成一列（"AI日报"四个字变成四行）；`left:50%`/`top:52%` 这类百分比定位同理会算错基准。这个 bug **只在真正截图查看渲染结果时才会暴露**——检查生成的 HTML/CSS 字符串、跑 DOM 结构断言、数值一致性校验都测不出来，因为这些方法看的是"生成的 CSS 文本对不对"，不是"浏览器实际怎么布局"。

排查过程本身也是一个教训：一开始怀疑过字体缺失、`word-break` 属性、`box-sizing`，都不是根因；排查中还发现测试脚本自己在改动 CSS 做隔离渲染时，用了粗暴的 `* { transform: none !important }` 把居中用的 `translate(-50%,-50%)` 也一起干掉了，导致"修复后截图"一度看起来还是错的——干扰了排查判断。**教训是二分排查时每次只改一个变量，同时要留意自己的调试脚本有没有引入新的干扰变量。**

**解决**：
1. `.seg-title-wrap` 不再用 `width:auto` 配合 `left:0;right:0`，改成用 Python 在生成时直接算出显式像素宽度（`width - left_偏移量`）写进 `style` 属性。
2. `.seg-accent-bar` 的 `height:100%` 同理改成显式 `{height}px`。
3. `transform:translate(-50%,-50%)`（元素自身居中技巧）**不用改**——这个百分比是相对元素自身尺寸算的，不受这个 bug 影响，前提是元素自身的 `width`/`height` 已经是显式像素值。

**如果以后要加新的绝对定位元素**：只要它是 `.seg-card`（或其他 `position:absolute;inset:0` 的容器）的子元素，且用了 `width:auto`/百分比宽高/百分比 `left`/`top`，就有触发同样问题的风险——生成时用 Python 直接算出显式像素值写进 `style`，不要指望浏览器帮你用 `auto`/`%` 算对。加完之后**务必实际渲染截图看一眼**，不要只检查生成的 CSS 文本或跑字符串层面的断言——这类布局 bug 只有在真正渲染出画面时才会暴露。

### 15. 结构化路径（`build_from_structured.py`）遗漏了自动摘要，导致画面只有标题没有正文

**问题**：v4.0 从"文本标记 + 正则解析"切到结构化路径时，正文摘要的行为在迁移时被漏掉——`body` 被无条件写死成 `""`，导致除非用户自己在 `segments_source.json` 里手写 `body` 字段，否则整条视频每个内容段落画面上只有一个大标题，没有任何正文内容，观感"干巴巴"。因为 `gen_hyperframes.py` 对"`body` 为空就不渲染摘要区块"的处理是正常设计（`opening`/`closing` 故意留空好触发自动目录/回顾），所以这个问题**不会报任何错**，只有实际看渲染出来的视频才会发现内容"消失了"。

**解决**：`build_from_structured.py` 现在会在 `seg["body"]` 未显式提供（或为空字符串）时自动兜底——整段句子按视觉行预算（约 7 行、每行约 20 字）逐行放入、放不下时截断；显式传了非空 `body`，仍然优先用手写内容，不会被兜底覆盖。兜底规则的完整说明以 SKILL.md 第 2 步 `body` 字段说明为家，此处不重复。`opening`/`closing` 段落**保持不变**（`body` 仍留空），因为这两段依赖 `body` 为空才会触发"本期看点"/"回顾"自动目录（见 SKILL.md 第 2 步 `opening_body`/`closing_body` 字段说明）。

### 16. `npx hyperframes render` 文件已经写完但进程不退出，导致 agent 卡住

**问题**：SKILL.md 第 5 步的渲染命令是直接前台跑 `npx hyperframes render -o out.mp4`，等它自己退出。在某些环境下观察到的现象是：MP4 文件已经完整写出（文件大小已经稳定不再变化），但背后的 Node/Puppeteer 进程没有退出（大概率是 headless Chrome 的 `browser.close()` 没有被正确 `await`，或者有残留的定时器/文件监听把 Node 事件循环挂住了）。如果 agent 是用一个会阻塞等待命令返回的工具跑这条命令，就会在视频其实已经渲染好之后仍然一直等，看起来像"没反应"，实际上只是拿不到这个已经没意义的"进程退出"信号。

**解决**：改用 `scripts/render_watch.py` 包装实际的渲染命令，不等进程退出，而是轮询目标输出文件——文件出现且大小连续几次轮询不再变化就判定渲染完成，直接返回（并尝试温和地收尾掉后台进程，收尾失败也不影响已经产出的文件）：

```bash
python scripts/render_watch.py -o out/video.mp4 -- \
  npx hyperframes render -o out/video.mp4 --quality standard
```

**这不是"进程可能异常退出"的兜底**——`render_watch.py` 仍然会检查进程真的提前退出且返回码非零的情况并如实报错；它只是不再把"进程退出"当成判断"渲染是否完成"的必要条件，改成直接看产物文件本身有没有写完。

**v5 补充**：早期版本在 Windows 上"收尾后台进程"实际是空操作（`os.killpg` 只存在于 POSIX），导致 Node/Chrome 进程残留、下一次操作变慢。现在 Windows 分支用 `taskkill /T /F` 按进程树强制结束，渲染完成后不再残留进程。

### 17. 开场/结尾标题位置 `topOther` 只在 template.json 里改没用，代码里从没读过它

**问题**：`gen_hyperframes.py` 里 `title_top`（开场/结尾大标题"AI 日报"/"感谢收看"的垂直位置）一直是硬编码的 `"80px"/"100px"/"30%"`（按 `aspect`/`is_news` 三选一），从没读过 `tpl_layout["title"]["topNews"]`/`["topOther"]`——即便 `_tl = tpl_layout["title"]` 这个变量已经取出来了、也确实被用来读 `fontSizeNews`/`fontSizeOther`/`padding`，唯独 top 这两个字段被漏掉，改 `template.json` 里的这两个值完全不会生效。默认的 `30%`（横屏）本身也偏低——开场页/结尾页的标题占到画面近 1/3 高度才开始出现下方内容，导致"本期看点"目录、"回顾"标签条被挤到画面下半部分，条目多的时候显得拥挤甚至可能超出画面。

**解决**：`title_top` 改成实际读 `_tl["topNews"]`/`_tl["topOther"]`（裸数字按 px 处理，字符串按原样使用，因为 `topOther` 本来就是带 `%` 单位的字符串）；同时把 `topOther` 默认值从横屏 `30%`/竖屏 `25%` 分别下调到 `16%`/`14%`，给标题下方的摘要/目录/标签条留出更多空间。**以后想调开场/结尾标题的垂直位置，改 `config/template.json` 的 `title.topOther`/`topNews` 就该生效了**——如果发现改了没反应，先检查是不是这类"模板字段取出来了但某个具体用法还是走的硬编码分支"的老问题。

**同类排查**：标题左偏移 `left_px` 硬编码 `250/120/0`、字幕栏 `.sub-text` 的 `max-width:1600px` 硬编码，都属同类问题（模板的 `title.leftWithBadge`/`subtitle.maxWidth` 成死字段），均已改成读模板值。**排查模板字段失效时，直接全局搜该字段名在 `gen_hyperframes.py`（或其他脚本）里是否真的被读取，而不是只看模板文件里有没有这个键。**

### 18. `images.json` 引用了不存在的图片 → 渲染出空白裂图，且不报错

**问题**：`gen_hyperframes.py` 加载 `images.json` 后，会把每条 `images[sid]` 直接写进 `<img src>`，**完全不校验文件是否存在**。一旦引用的图片在磁盘上缺失，HTML 照常生成，headless Chrome 渲染时该 `<img>` 加载失败 → 该段落变成空白裂图（画面只剩标题/字幕，右侧图位空）。由于不报错，这条坏视频会静默渲染完成、甚至被当成"成功"投稿出去。

**典型触发场景**：手动改了 `images.json`（例如换图时把 `seg5.jpg` 换成 `seg5.png` 却只改了 json、忘了重跑 `gen_hyperframes.py` 重新生成 HTML），或替换图片文件时改了扩展名。`run.py` 的正常流程每次都会重跑 `gen_hyperframes.py`，所以**走 run.py 不会踩**；这个坑只在绕开 run.py、手动拼装各步骤时出现。

**解决（5.6.1）**：`gen_hyperframes.py` 已加 fail-fast 校验——生成 HTML 前逐条检查 `images.json` 声明的图片是否在 HTML 输出目录下真实存在，任一缺失就直接 `[error]` 退出（指出具体 segment 与路径），不再静默出片。**任何 `images.json` / 图片文件的改动后，务必重跑 `gen_hyperframes.py` 再渲染**（走 run.py 则自动包含这一步）。

**排查空白图**：渲染后抽帧发现某段图位空白，先 `grep` 该段 `<img src` 确认路径，再 `ls` 对应文件是否存在；多半是扩展名/路径不一致，补上文件或修正 json 后重生成 HTML 即可。

**补充**：文件存在但打不开（下载中断/拷贝中断导致截断）是同一个坑的另一种表现——`gen_hyperframes.py` 现在在存在性校验之后追加了可解码性校验（`PIL.Image.verify()`），同样会在生成 HTML 前 fail-fast 并指出具体 segment，不会等到渲染完抽帧才发现。

## 反模式提醒（不要回退）

- **不要主动去除 ImageGen 的"AI生成"水印**：该水印是生成内容的免责声明，保留它既诚实又安全。曾尝试用 `clean_images.py` 裁剪右下角去除，弊大于利——裁剪参数（bottom/right）不对会误伤正常内容，且多一步流程就多一处潜在 bug。配图治理应聚焦"内容是否贴合新闻"（偏题 / 标题党 / 裂图），而非装饰性处理。
