---
current_version: "1.5.77"
---

# Changelog — content-to-video

> 早期条目（1.1.0 – 1.5.47）的压缩归档 changelog-archive.md 已于 1.5.70 裁撤，完整历史可从 git log 追溯；本文件只保留最近条目。
>
> 发版约定：新条目用 `## 版本号 · 日期` 标题插到最上方；frontmatter `current_version` 与 SKILL.md frontmatter `version` 必须一起改（`test.py` 门禁校验两处一致 + 最新条目与 current_version 一致）。历史条目是当时的动作描述，不回改。

## 1.5.77 · 2026-09-07

test.py 瘦身（用户要求审查无必要测试，ponytail 视角，415→408 断言、3490→~3440 行）：删 6 处无独立价值断言——①[20a]「pipeline.main() 无局部 import math」AST 守卫（一次性事故记忆，changelog 已录，行为无回归面）；②[22g]「gen_hyperframes imports get_ffmpeg」源码 grep（集成层真跑 gen_hyperframes，import 断了会在「退出码 0」处崩，纯重复覆盖）；③④[19]/[21] 两个「all combos pass」聚合断言（逐条 check 已各自失败上报，聚合只重复计数）；⑤[8]「voice registry has 8 presets」（数据条数耦合——加音色必挂测试，符合本项目「计数必然漂移」原则；validity 断言保留）；⑥[12] cmd_estimate 的 stdout 格式测试（测的是 print 字符串非行为，estimate 纯函数 2 断言保留）。改 1 处：[5]「accent palette 8 colors」放宽为 non-empty + 全 #hex（去掉条数耦合，色板空/脏仍有拦截）。**有意保留**（审计后判定承重）：[17f]/[18a]/[22c/d/e] 源码 grep 组 ~28 条——它们是跨文件契约（文档承诺的 CLI、原子写、超时兜底、默认值单一来源）唯一廉价的守护，行为测试够不到且每条背后有真实事故；[20c-e]/[3] 系 CSS 形态断言——模板推导不随排版改值而碎，且版式是本项目历史第一大 bug 源；WCAG 数学断言（改色静默回归的唯一防线）；[25] _assets/编码器/边界纯函数（生产代码或集成层自身逻辑）。验证：python test.py 408/408 全绿（1 项环境 SKIP）。

## 1.5.76 · 2026-09-07

changelog 载体 json→md（用户要求对人类与 agent 都友好，agent 优先原则不变）：changelog.json 改为 CHANGELOG.md——机器约定：YAML frontmatter `current_version` 供门禁解析，`## 版本 · 日期` 标题即条目边界（最新条目必须等于 current_version）；人类收益：版本时间线可扫读、diff 友好、与 references/ 同为纯 markdown。test.py 门禁同步：`json:changelog.json` 换成 `md:changelog.md`（frontmatter 解析 + 最新条目标题一致性 + 各条目有日期），_check_version_consistency 改读 CHANGELOG.md frontmatter。SKILL.md 目录树同步。旧 changelog.json 的 `_archive` 字段转为文首引言。验证：python test.py 415/415 全绿（1 项环境 SKIP：无 Pillow）。

## 1.5.75 · 2026-09-07

目录扁平化（用户指定）：dev/ 整目录裁撤，test.py 与 changelog.json 上移到技能根目录（与 SKILL.md 同级），根目录收敛为 SKILL.md/.env.example/test.py/changelog.json + scripts/config/references 三子目录。test.py 路径基准同步：HERE 由 dev/ 改为技能根目录（_SKILL_ROOT = HERE，不再 dirname 上跳），门禁段 changelog 路径 dev/changelog.json→changelog.json（含 _check_version_consistency 与 json 门禁标签 json:changelog.json）；SKILL.md 项目结构分层描述与目录树同步（根级加 test.py/changelog.json 两行，删 dev/ 三行），rendering.md dev/test.py 提法改 test.py。另有 [3] 段一处 os.path.dirname(os.path.dirname(__file__)) 双重上跳推导 template.json 路径（原 dev/ 布局的两级跳），上移后多跳一层导致 _test_3 崩溃，统一改走 _SKILL_ROOT。changelog 历史条目中的 dev/ 路径是当时的动作描述，不改写。验证：python test.py 415/415 全绿（1 项环境 SKIP：无 Pillow）。

## 1.5.74 · 2026-09-07

测试三入口合一（用户指出 selftest/run_eval/check 三分散入口是手工编程时代软件工程惯性，使用者都是 agent，一键即够）：新建 dev/test.py（单文件 ~3490 行，一条命令 python dev/test.py 跑完三层）——单元层（原 selftest 27 个 _test_* 节，380 断言）+ 集成层（原 run_eval 的 ev_main()，22 断言，真子进程+真 ffmpeg）+ 门禁层（原 check 13 项门禁：JSON/frontmatter/scope/doc_drift/packaging）；末尾打印 __SUMMARY_JSON__ 机器可读汇总，退出码 0/1。合并手法：run_eval 的 check/skip/RESULTS/SKIPPED/main 更名 ev_check/ev_skip/EV_RESULTS/EV_SKIPPED/ev_main 避免与单元层同名冲突，[25] 状态块改用 .clear()+切片还原（避免 UnboundLocalError）；路径基准 HERE 从 scripts/ 改为 dev/，源文件读取统一走 SCRIPTS_DIR（含一处 __file__ 推导的 preview.js 路径）。删除 scripts/selftest.py、dev/run_eval.py、dev/check.py 三文件；SKILL.md 目录树（4 行→1 行 test.py）与 Python 版本描述同步，rendering.md selftest 提法改 dev/test.py。dev/ 收敛为 test.py + changelog.json 两文件。验证：python dev/test.py 415/415 全绿（1 项环境 SKIP：无 Pillow）。

## 1.5.73 · 2026-09-07

砍掉 dev/evals.json（用户质疑其利用率，查实属实）：设计用途是另一个 agent 实例作 grader 做 prompt 级行为评估 forward-test，但该流程从未发生过（changelog 零战绩）；程序化消费仅有 check.py 两个弱门禁（JSON 合法性 + 条数计数），run_eval.py 只在注释里溯源引用。属投机性基础设施（为从未运行的评估流程维护 fixture）。连带：①check.py 删 evals_count 门禁与 evals.json JSON 校验项（gate 17→15），scope_declaration 门禁实现本就不读 evals.json、原样保留，注释与错误信息去掉 case 溯源，门禁标签 non_chinese_input_rejected→non_chinese；②run_eval.py 头部说明改写——去掉 evals.json 溯源，改述与 selftest 的真实分工（selftest=字符串/AST/纯函数级，run_eval=真子进程/真 ffmpeg/run.py 编排的命令链层），纯函数 check_segment_count 保留（selftest [25] 继续测试）；③SKILL.md 目录树删 evals.json 行（该行「10 条」计数也是上轮幽灵回滚的编辑，一并消失）。run_eval.py 有意保留：它是唯一的离线集成层，selftest 抓不到跨脚本 CLI 胶水断裂。验证：selftest 382/382、run_eval 22/22、dev/check 15/15 全绿。

## 1.5.72 · 2026-09-07

配图比例原则成文 + 文档/脚本双收敛（用户三项要求）：① 配图比例原则（四种方式统一优先 4:3 横版）：图框横竖屏统一 √2:1，主流素材里 4:3 最接近（contain 填充率 ~94%，16:9 ~80%、1:1 ~71%）——SKILL.md 第 4 步新增原则块（A 审图优先 4:3 候选/candidates.json 带宽高、B 固定 landscape_4_3、C 画布 4:3、D 生成选横版），image_options.md「比例依据」升级为「配图比例原则」节，gen_charts.py 画布注释同步（残留旧图框 860×700≈1.23:1 推导改为指向模板 image.aspect）。② references 收敛 8→7：internals.md（40 行）并入 pitfalls.md 文末「内部机制速览」节——两者触发场景同为排查问题，单独成文件不值得；SKILL.md 四处路由与 pitfalls.md 头部指引同步，历史 changelog 条目按惯例不改写。③ scripts 收敛：split_series.py + check_series.py 合并为 series.py（split/check 子命令，库函数 parse_sections/plan_episodes/write_skeleton/find_drifts 原样保留），selftest [15]/[20] 导入与标签同步、18a 文件清单 split_series.py→series.py，SKILL.md 目录树两行并一行，rendering.md/run.py 注释同步。有意不合并：render_watch.py（run.py 以子进程隔离渲染超时/进程树收尾，分离是承重结构）、budget/check_facts（领域不同，硬凑是 grab-bag）、12 个 _ helper（单一职责树，上次审计结论不变）。验证：selftest 382/382、run_eval 22/22、dev/check 17/17 全绿。

## 1.5.71 · 2026-09-07

文档层冗余与散落数据清理（用户要求，数据驱动化贯穿审计）：① 漂移修复——references/image_options.md 横屏图片槽 860×608→910×644（旧值未跟上模板）、竖屏槽 4:3 980×735→√2:1（1.5.61 改动文档未同步，4:3 侧隙 ~30px 重算）；references/rendering.md body.sideGap 50→25（模板实值，与横屏图片右距 25 同源）、竖屏底部留白 80px→指向 segCard.padding 底值（80 是代码缺省回退值非模板值）、竖屏槽 4:3→√2:1；scripts/gen_hyperframes.py 竖屏参数注释块同步（4:3 推导数字改为指向 image.aspect）。② 散落数据收敛——SKILL.md 横竖屏版式几何（910×644/top 540/顶部 100px 底部 80px/字号 38 vs 46）全部改为指向 template.json layout 块键名，策略留 SKILL、几何只活模板；说话人 6 色板改为指向 template.json speaker 块（色板大小不再写死）。③ 计数去漂移——SKILL.md 三处「18 条踩坑记录」与「10 条评估用例」删掉写死计数（同 1.5.70 changelog 条目计数处理，数字必然漂移删描述才是根治；doc_drift:pitfalls_count 门禁兼容无计数写法，17/17 复验通过）。有意保留：语速 1.2/1.5、句长 15-35/45、workers 默认值等行为常量（selftest 钉在 _contracts/CLI，文档表述与代码同源）。验证：dev/check 17/17 全绿（selftest 382/382 + run_eval 22/22 + 全部 doc_drift 门禁）。

## 1.5.70 · 2026-09-07

代码瘦身 + 数据驱动收口 + 归档裁撤（用户要求）：① gen_hyperframes.py 1854→1836 行——删死变量 css_news_title_size/css_opening_title_size/恒空 title_transform/恒 0 _ag_slot 槽位机制，开场 chips 预告与收尾 recap 两段逐字节重复的构建合并为 _wants_chips 单分支，去重复 import shutil 与 4 处无占位符 f-string；selftest.py 2659→2634 行——新增模块级 _base_manifest() 六句 fixture（[3]/[3b]/[22f] 三份重复时基合一），[20] 内 6 次重复 generate_html 调用收敛为 _lhtml/_phtml 复用，整删与 20c/20d 完全重复的 [20f] 小节（断言 384→382，其余一比一保留），[3] 的 manifest3/html3 重名遮蔽改名。② 数据驱动重构（JSON 定义、py 生产）：新增 config/voices.json（8 音色唯一数据源，_voices.py 改加载器，list_voice_ids/is_valid_voice_id API 不变，pipeline/run/_contracts 零改动）；template.json 新增顶级 subtitle 块（各画幅 maxChars/hardCap/cueMaxLines）与 speaker 块（colorsOnLight/colorsOnDark/agendaNumText）并列入 load_template 必需键；_script_utils.subtitle_params_for 与 gen_hyperframes 的 SPK_COLORS_*/AGENDA_NUM_TEXT_COLOR 改读模板（签名与模块常量名不变，selftest [20b]/[21] 零改动）；有意不迁 _EN_ABBREV_TAILS（断句语言规则）与 DEFAULT_SPEED/LONG_SENTENCE_CHARS（行为常量，selftest 18a 已钉在 _contracts）。③ references 两处过时修正（rendering.md 字幕切行参数改按画幅描述并指向 template.json subtitle 块；tts_pipeline.md 音色表标注 voices.json 为权威来源）；dev/changelog-archive.md 裁撤（完整历史走 git log），SKILL.md 目录树与 _archive 字段同步，changelog 条目计数从描述中移除（计数必然漂移）。验证：selftest 382/382 全绿、pyflakes 对改动文件清零、dev/check 全过。

## 1.5.69 · 2026-09-07

flow/章节解耦到 template（用户指定架构「template 负责定义、gen_hyperframes 负责生产」）：template.json 新增 modes 块（chapter/flow 两个命名预设，五键：numbered/openingPreview/closingRecap/transition/verticalOpeningCover；版本 1.1.0→1.2.0，并列入 load_template 必需顶级键）；_template.py 新增 get_mode_preset(manifest)（manifest 顶层 flow 布尔→预设选择；缺预设/缺键/枚举域外 ValueError，不静默回退）。gen_hyperframes 的 5 处 flow_mode 分支（badge/开场 chips/目录序号圆/收尾回顾默认/转场）全部改读预设键，flow_mode 变量删除；_opening_cover_missing 第三参语义从 flow 布尔改为 required（gen 主流程传 get_mode_preset(manifest)['verticalOpeningCover']）。run.py _image_coverage 的开场封面图义务与 gen 共读同一预设（此前 run 读 manifest.flow、gen 读 manifest.flow 是两处并行判断，现在策略源唯一）。预设是选择单位而非散装开关——组合面仍是 2 个模式，没有 2^5 开关爆炸。纯重构：4 组基线 HTML（portrait/landscape × flow/chapter，真实 manifest+配图）改前改后字节级一致（MD5 相同）。selftest 新增 17e3（8 条断言：预设齐备/取值语义/选择逻辑/三种坏定义拒绝）。文档：rendering.md 模式路由段补 modes 块说明、SKILL.md 项目结构行。

## 1.5.68 · 2026-09-07

主题奥卡姆剃刀 4→2（用户决策「剃成 cream+dark、默认 cream」）：删 theme_registry.json 的 tech（与 dark 观感难分，并入 dark）与 alert（语义过窄，警示感改用红色 accent 表达）条目，_meta 4.2.0→5.0.0。registry 是唯一数据源，CLI --theme choices（list_theme_names）与 selftest WCAG 对比度矩阵均动态读取，删条目即全链路生效——代码侧仅同步 4 处注释（gen_hyperframes）、1 处 docstring（_theme）、1 处 help（run）。默认主题 cream 本就如此，无代码改动。文档同步：SKILL.md 主题段/项目结构、rendering.md 主题路由段与速查表（安全类内容改为 dark+红色系 accent）。旧项目若存有 --theme tech/alert 的重渲染命令，CLI 会以 choices 校验明确报错（列出可用值），符合 _theme「不做静默回退」原则。

## 1.5.67 · 2026-09-07

竖屏开场封面图必配范围收窄到 flow 模式（用户指定「flow 模式下默认配图，正常模式下还是目录」）。判定从「portrait/both 即必配」改为「portrait/both + manifest 顶层 flow:true 才必配」：run.py _image_coverage 参数 require_opening→vertical（flow 在函数内部从 manifest 读——pipeline 由 segments_source.json 顶层透传），章节（正常）模式竖屏开场保持目录（agenda 是画面主体）、无配图义务；gen_hyperframes._opening_cover_missing 增加 flow 参数、main() 传 bool(manifest.get("flow"))。拦截/warn 文案同步标注「flow 模式」。横屏 opening 与两画幅 closing 可选性不变；纯文字版兜底豁免不变。selftest：17g 重写为 flow manifest fixture 四态（flow 缺 opening 进 missing / flow 配齐无 missing / 章节模式无义务 / 无 opening 段无义务）、17e2 增章节模式不要求断言。

## 1.5.66 · 2026-09-07

竖屏开场封面图从「可选」升级为「默认必配」（用户要求「竖版默认支持，不用开启」）。run.py _image_coverage 新增 require_opening 参数（portrait/both 且 manifest 含 opening 段时把 "opening" 纳入配图统计），缺 opening 映射走缺图拦截；拦截提示里 opening 拆出单独指路段——不进搜图候选审阅（search_images 只搜内容段落，混进 no_candidate 分支会被误读成 --search-sids 路由段落）。gen_hyperframes.py 新增 _opening_cover_missing() 纯函数 + 分步执行 warn（run.py 有拦截，分步执行时这行 warn 是唯一防线）。边界：自定义手写 manifest 无 opening 段时无义务（没有页面就没有配图义务）；纯文字版（--no-images / 无 Key 兜底）不受影响；closing 与横屏 opening 仍可选。selftest 新增 5 条断言（require_opening 三态 + _opening_cover_missing 三态）。教训：replace 锚点若落在多行表达式的中间续行上会把新代码插进表达式——锚点必须含完整语句边界（本轮 gen_hyperframes.py 已踩坑并即时修复）。

## 1.5.65 · 2026-09-06

开场/收尾页支持配图（用户反馈：竖屏开场页无图版面太空、太难看）。gen_hyperframes.py 删掉 opening/closing 强制 has_image=False 的旧规则，images.json 写 opening/closing 键即生效。竖屏开场页配图后版式=「标题在上+图居中+句子流钉底」与内容段同构（新增 CSS：#opening/#closing.has-image 标题取消垂直居中、锚定卡片顶部，自由空间归图与句子流之间 auto-margin 吸收）；竖屏有图时 agenda/closing_body 让位于图（_verse_kills_body 对 opening/closing 一并生效——垂直预算装不下图与目录/回顾并存）；横屏走既有配图段版式（标题左移、图右置），开场预告/收尾回顾保留；无图时行为与旧版完全一致。搜图工具只搜内容段，开场图手动写进 images.json（文档已注明）。selftest 新增 7 条断言（opening image rendered / 横屏预告保留 / 竖屏预告让位 / has-image class / closing 同理），420/420 全绿。

## 1.5.64 · 2026-09-06

竖屏图框上边距再调（用户指定 45px）：vertical.image.marginTop 38→45。像素实测：图框顶 260→267（+7px）、高 728（√2:1 精确）、verse 距底不变；check --snapshots 0 error（首跑遇偶发 Check failed 无快照，重跑即过，未复现）、dev/check 17/17；成片未重渲。

## 1.5.63 · 2026-09-06

竖屏图框上边距定值（用户指定 38px）：vertical.image.marginTop 32→38。像素实测：图框顶 254→260（+6px）、高 728（√2:1 精确）、verse 距底不变；check --snapshots 0 error、dev/check 17/17；成片未重渲。测量方法修正：按行暗像素数找框顶会被大号标题字形误触发，改为要求行内暗像素横向跨度覆盖框宽（首像素 <60 且末像素 >1000 且数量 >400）再定位框界。

## 1.5.62 · 2026-09-06

竖屏图框上边距改大（用户要求「图片框上边距改大一点」）：模板 vertical.image.marginTop 12→32（回到接近 1.5.58 前的 48 设计值，取中间值）。像素实测：图框顶 234→254（+20px）、高 728（√2:1 无回归）、侧隙 29/30px 不变、垂直预算余量仍充裕（ verse 350 + padding-bottom 50 钉底布局不受影响）。check --snapshots 0 error、selftest 断言模板推导自动适配、dev/check 17/17；成片未重渲（横竖屏 MP4 均为 1.5.55 时代旧版，待用户确认后一起重渲）。

## 1.5.61 · 2026-09-06

竖屏图框比例 4:3 → √2:1（用户确认，模板 vertical.image.aspect=「1.4142/1」）：宽 1030 不变、高 772.5→728.3（缩 44.2px）。代价是 4:3 图源从满框零间隙变为左右各 ~29/30px 透明侧隙（与横屏 √2:1 框行为一致，两画幅比例统一）；垂直余量增至 ~78px。像素实测：框高 728、侧隙 29/30px、verse 无变化；check --snapshots 0 error、selftest 断言模板推导自动适配、dev/check 17/17；成片未重渲。

## 1.5.60 · 2026-09-06

竖屏卡片 padding-bottom 0→50（用户指定，1.5.59 贴底的回调）：视窗底边距画布 50px，文字静止区更离底边远一点。预算余量 33.9px 充裕。像素实测：verse 文字范围 y 1118..1355（视窗底 1390）、图框 772 满框零侧隙无回归；check --snapshots 0 error、dev/check 17/17；成片未重渲。

## 1.5.59 · 2026-09-06

竖屏 verse 视窗贴底（用户要求「向底部对齐」）：卡片 padding-bottom 80→0（模板 segCard.padding=「62px 50px 0px」），视窗底边齐画布。此前视窗已是底部锚定（flex margin-top:auto），80px 就是卡片下内边距——归零即贴底。文字静止时距底约 60px（视窗内部 clipPad 渐隐呼吸区），滚动时在底边渐隐。垂直预算更宽裕（内容高 1260→1378，余量 83.9px 由图与视窗之间的 auto-margin 吸收，图框仍满 1030×772.5 零间隙）。像素实测：verse 文字块整体下移恰 80px（顶 1087→1167），图框无回归。⚠ 取舍：原 80px 底部留白有给抖音/快手沉浸式 feed 底部 UI 让位的用途，贴底后当前句更接近平台 UI 覆盖区——已知取舍，用户明确要求。验证：check --snapshots 0 error、dev/check 17/17、rendering.md 留白描述同步；成片未重渲。

## 1.5.58 · 2026-09-06

竖屏 verse 视窗 300→350（用户指定）。350 多占的 50px 会重新击穿垂直预算（1.5.57 修过的 flex 压扁会复发：1294+50-48…即标题区 159.6 + 上间距 24 + 满框 772.5 + 350 = 1306.1 > 内容高 1260），故同步腾挪标题区：模板 segCard.padding 上值 100→62、image.marginTop 24→12，图框保持满 1030×772.5 零间隙（预算 1294.1 ≤ 1298，余量 3.9px 由 verse auto-margin 吸收）；gen 竖屏 badge top 104→66（top = padding 上值 + 3.6，耦合关系已写进 CSS 注释）。像素复测：框高 772 满满 4:3、侧隙 0/1px、图底 1005 vs verse 顶 1010（5px 不重叠）；check --snapshots 0 error。验证：selftest 断言全模板推导自动适配、dev/check 17/17；成片未重渲。注意：badge top 与 segCard.padding 上值是手工耦合，改模板 padding 必须同步改 gen 里的 badge top——已有注释锚点，长期应模板化。

## 1.5.57 · 2026-09-06

修竖屏图框被 flex 压扁导致的意外侧隙（用户预览发现，像素实测实锤）。1.5.56 把竖屏图槽加宽到 1030×772.5 后，竖屏内容预算（1260 = 1440 − 上 100 − 下 80）装不下「标题区 ~160 + 图 margin-top 48 + 图 772.5 + 句流 300 = 1280.5」，flex-shrink 把图框压缩 ~20px 到实际 752 高（比例 1.37 不再是 4:3），4:3 图 contain 后左右各露 ~13px 侧隙——快照 PIL 实测 dark x 38..1041 vs 框 25..1055。修复：模板 layout.vertical.image.marginTop 48→24（满框后预算 1256.5 ≤ 1260，余量 3.5px 归句流 auto-margin 吸收）。修复后实测：内容高 772（满 4:3）、左右间隙 0/1px（1px 为圆角抗锯齿）。教训：改图框尺寸必须连垂直预算一起验（标题区 + margin + 框高 + 句流 ≤ 卡片内容高），缩量小于 ~20px 的 flex 压扁在关键帧目检里不显眼、只有像素测量能抓到。验证：check --snapshots 0 error + 像素复测零间隙；selftest 断言为模板推导无需改、dev/check 17/17；成片未重渲（沿用 1.5.56 预览口径）。

## 1.5.56 · 2026-09-06

实例预览反馈的 3 处版式调整。①竖屏 badge 与标题同行：1.5.55 的流内堆叠（badge 悬在标题上方）用户不满意——恢复绝对定位但对齐标题首行（top = 卡片上内边距 100 + (72×1.35−90)/2 ≈ 104，left 对齐卡片左内边距 50），紧随 badge 的标题盒用真实盒宽避开（.badge + .seg-title-wrap{margin-left:110px;width:calc(100% - 110px)}）；注意 hyperframes content_overlap 按元素盒测量，仅靠 padding/text-indent 缩进不会缩小盒、仍会误报，必须用 margin 收窄盒本身。②竖屏图框左右边距 25px：新增模板键 layout.vertical.image.marginSide（默认 0 = 原行为），槽宽 calc(100%+2·marginSide) + 负左外边距外扩（本实例 25 → 槽 1030×772.5，仍 4:3）；横屏图框本就是 25px 屏边距、不动。③图片框 letterbox 填充由模糊改透明：删除 .seg-image::before 的 blur(28px) 背景填充层（横竖屏同一规则一并生效），contain 留出的空隙透出 body_bg 底色。selftest 同步：竖屏图槽断言改为模板推导 marginSide（新增 width calc 与负外边距两个子断言）。验证：两画幅 check --snapshots 0 error（竖屏 layout 9 samples 全过、对比度 45/45 + 1 个既有开场字幕条警告）、selftest 398/398、dev/check 17/17；成片未重渲（按用户要求只出预览）。

## 1.5.55 · 2026-09-06

实例制作（竖屏）时发现的竖屏版式 bug：内容段 badge（章节编号圆）在竖屏会压住标题首字。根因：竖屏 .seg-title-wrap 被覆盖为流式（position:relative;width:100%，忽略 leftWithBadge），但 .badge 仍绝对定位（left:80;top:60），两者碰撞——hyperframes check 在全部 6 个内容段报 content_overlap #badge-segN in #title-segN（7 errors，视频会有一处字被遮挡的半成品）。修法（gen_hyperframes.py 竖屏 CSS 块新增一条）：竖屏 .badge 改为流内元素（position:relative;align-self:flex-start;margin-bottom:16px），塞进 flex 列顶部、与标题垂直堆叠，彻底消除重叠且不改变竖屏信息密度。验证：check --snapshots 竖屏 0 error（layout 9 samples 全过、对比度 45/45，剩 1 个开场字幕条 contrast 警告为既有项）、成片 verify_render 7/7（H.264+AAC、1080×1440、24fps、时长偏差 0.02s）；selftest 398/398、dev/check 17/17。

## 1.5.54 · 2026-09-06

Ponytail 审查余下的 5 处次级问题（第 5 步相关）。①gen_hyperframes.py 标题估算注释：旧写「上缘 292、两行标题不再压图」与 references/rendering.md 的「上缘 218、两行标题轻微交叠」正面矛盾——改为正确几何（上缘 = 1080/2 − 644/2 = 218，位于两行标题底 260 之上约 42px，与标题轻微交叠，乃已知取舍）。②dev/check.py image_size 门禁：原把 SKILL.md + rendering.md 拼成一个字符串判包含，一份正确即可放行另一份漂移；改为逐文件判，缺哪份报哪份。③doc_drift:pitfalls_count（SKILL.md）门禁：原 pattern「N 条具体踩坑记录」永远匹配不到 SKILL.md 实际写的「18 条踩坑记录」，等于长期 skip；改为「N 条踩坑记录」，与 pitfalls.md 标题 pattern 对齐，现可真校验（18 == 实际 18 小节）。④run.py 并行阶段 SIGINT handler 装完不恢复：后续 check/render 阶段 Ctrl-C 被吞（sys.exit(130) 且子进程树已无），长任务想停停不下来；改为捕获并保存原 handler、并行阶段结束即恢复默认。⑤新增 doc_drift:cli_aspect_choices 门禁：交叉校验 SKILL.md 声明的 --aspect 取值 ⊆ run.py argparse choices，从根上堵住「文档声明了代码已删的 CLI 选项」（--aspect vertical 存活一整版即此洞）。验证：dev/check 17/17（原 16 + 新增 1）、selftest 399/399。

## 1.5.53 · 2026-09-06

Ponytail 视角审查发现的三处修复（门禁全绿但问题真实存在）。①wrap_numbers 撕开 HTML 实体：esc() 把 ' 转成 &#39;，原正则 `(?<![&#])` 只挡住紧跟 &/# 的首位数字，`9`（前一位是 `3`）被包进 span，实体断成 `&#3<span…>9</span>;`，浏览器把 `&#3` 当无分号数字实体解析成控制字符，画面正文出现乱码（中英混合稿的 Apple's / O'Brien 即可触发）。改为正则先整条吃掉 `&#\d+;` 分支、命中即原样返回，剥掉 span 后与 esc() 输入严格相等。②run.py 超时收尾在 POSIX 上杀死自己：_run() 的 subprocess.run 未开独立会话，_try_kill 的 killpg(getpgid(child)) 打到 run.py 自己的进程组，用户看到 exit 143 而不是"步骤超时"提示；改为仅带 timeout 的步骤（check）开 start_new_session，不带 timeout 的保持同组以保留 Ctrl-C 传递。③SKILL.md 第 5 步文档了不存在的 `--aspect vertical`(9:16 1080×1920)——1.5.52 画幅收敛时已从 CLI 删除，choices 只有 landscape/portrait/both，agent 照抄必报 invalid choice；该节删除，竖屏节改为 portrait 为唯一竖屏画幅，并修正同段遗留的旧几何"860×700"→"910×644"与已删除的"两行标题告警"表述，references/rendering.md 的交叉引用同步。④selftest [23c] 守护断言收紧：原断言只查 `>39</span>` 与 `&#<span`，漏掉真实污染形态 `>9</span>`，故 bug 存在时仍全绿；改为「剥掉 span 标记后必须 == esc() 原文」的不变量断言（不写死污染形态）+ 实体形态断言。验证：selftest 398/398、dev/check 16/16。

## 1.5.52 · 2026-09-06

砍掉两个平时生成不用的可选模块（用户点名）：①export_extras.py（章节时间戳 chapters.txt + SRT 字幕导出）——timing_manifest.json 本身就有逐句起止时间，B 站投稿也不靠 SRT；②gen_cover.py（竖版封面 + 候选标题草稿）——实际出片从未用过。同步清理：SKILL.md 文件树/工作流命令/封面段落、references 两处、run_eval [4] 真子进程测试与 docstring、selftest [13] 整节 + 16c + ⑨/两消费者共用参数断言（收窄为 gen_hyperframes 单消费者）、_script_utils/gen_hyperframes/pipeline/_contracts 注释口径。subtitle_params_for 保留（gen_hyperframes 仍在用）。验证：dev/check 16/16、selftest 全绿、run_eval 全绿。

## 1.5.51 · 2026-09-06

奥卡姆剃刀瘦身（用户要求审视全部功能模块哪些可砍，砍除判据=真实使用记录为零）：①砍 wiki 自进化全家桶——dev/wiki_{gate,maintain,propose,trace}.py、_wiki_common.py、scripts/_trace.py（404 行轨迹落盘）与 dev/wiki/ 目录全删；check.py 拆掉 wiki 三道门禁、parse_summary_totals 内联；run.py 拆掉 --no-trace/atexit/_note_outcome×12 轨迹旁路；selftest 删 ~110 条 wiki 断言、visual_regression 测试块，补 dev/ 路径注入与 _quiet25/_tf25 辅助。四阶段闭环仅人工跑过 1 次、proposals 仅 1 条、日常制作链路对它零消费。②砍 visual_regression.py（199 行零调用）。③run_eval 稳定性：假 ffmpeg 替身 #!/bin/sh 在 Windows 无法执行→平台感知（nt 出 .bat），h264 枚举两条永久红转真绿；损坏图检查加 Pillow 条件守卫（无 Pillow 时 gen_hyperframes 按设计降级为 warn，检查转 SKIP）；TTS+配图并行计时用例拉大 sleep 窗口并重试 3 次（check.py 上下文 Defender 扫描临时文件致 spawn 变慢，曾稳定假红）。④changelog 压缩到最近 5 条完整记录，更早的按一行摘要进 changelog-archive.md。⑤事故记录：SKILL.md 曾被编辑脚本贪婪正则截断（567→71 行），从 git HEAD（1.5.47）恢复后重放当日增量，toc_anchors/scope_declaration/tagline_fallback 等门禁据此全数恢复。验证：dev/check 16/16、selftest 419/419、run_eval 28/28（venv 有 Pillow 与裸运行时双口径）。

## 1.5.50 · 2026-09-06

排版与代码彻底解耦（用户追问：改排版是否还要动 py/selftest——此前 selftest 断言写死模板数值，改模板必挂测试）。①selftest 排版断言全部改为模板推导：竖屏 segCard.padding/image.aspect/image.marginTop/verse.clipPad（含 JS 滚动锚点字符串）、横屏图片槽 top/width/height、标题 left/width/top（leftWithBadge/rightInset/topNews）不再写字面量，与 gen_hyperframes 读同一份 template.json；'整屏垂直居中'判据从 ==540 改为 ==1080/2（表达语义而非钉值）。②gen_hyperframes 最后一批硬编码排版常量收编模板：title.rightInset（横屏 110/竖屏 60，标题宽度右缘缩进）、agenda.itemGap（条目内序号↔文字间距）、chip.gap（回顾 chips 间距）、verse.linePad（句子流行距）、vertical.image.borderRadius（竖屏图片槽圆角 16，横屏仍走 typography.imageBorderRadius 24）；非 badge 配图段标题左缘从写死 50 改为对齐 body.leftMargin（同轴线语义）。③验收实验：脚本化改模板（图片槽 900×636、clipPad 66、itemGap 18 等 10 处）后 selftest 零改动全绿——仅剩的拦截是 √2:1 关系不变式检查（图片槽宽/正文卡 maxWidth/sideGap 的几何关系），属设计守护而非数值耦合；自洽改动下全过。注意事项：模板键消费检查要求 layout 叶子值为 dict、gen 源码中以双引号引用键名（f-string 内层字符串用单引号会漏检）。selftest 523/525（2 个既有 h264 环境项）。

## 1.5.49 · 2026-09-06

去 PPT 感 + 排版参数收编模板（用户反馈：①改排版要同时动 template 和多个 py，耦合太高；②内容框文字与字幕字号几乎一致，太像 PPT）。①字号层级：横屏 body.fontSize 44 → 38（竖屏本就 38），与字幕 46 拉开档次——口播字幕是画面主导文字、正文卡片退居辅助信息层。曾一并试过正文去卡片化（裸文字直接排背景上），用户目检否决（太丑）——保留底色卡片为默认外观，新增 template body.card 开关（默认 true）仅留作切换入口。②排版参数收编 template.json（改排版不再动脚本）：竖屏 segCard.padding（原脚本硬编码 100px 50px 80px）、image.aspect（原 4/3 硬编码）、image.marginTop（原 48 硬编码）、verse.windowHeight/clipPad（原 300/60 硬编码，含 JS 滚动锚点同步插值）。③对比度加固：num-accent 数字原色 accent（#ef5350 红）在 cream 底上 2.79:1 不达 WCAG 大字 3:1（check 工具在快照上画违规标注框），改为与 tagline 同规则——浅底 darken 加深、深底向白提亮，check 51/51 全过。④selftest 模板键消费检查要求 layout 叶子值为 dict，segCard/verse 均按子块组织。验证：selftest 523/525（2 个既有 h264 环境项）、hyperframes check 0 error / 对比度 51/51 / 布局 0 issues，mcp-intro 快照逐帧目检通过。

## 1.5.48 · 2026-09-06

砍掉横屏 verse 模式，字幕/内容呈现不再有模式区分（用户决策：横屏 verse 实际出片不好看）：模式固定按画幅绑定——横屏恒为 bar（底部字幕条 + 正文要点卡）、竖屏恒为 verse（歌词式句子流），不再是用户可选项。①gen_hyperframes.py：--sub-mode 参数与对应 argparse 守卫删除，generate_html 去掉 sub_mode 形参改为函数内按 aspect 派生（竖屏 ValueError 拦截随之失去存在意义，一并删除）；横屏 verse 专属死代码清除——verse 开场/收尾标题居中分支、#opening/#closing .verse 绝对定位 CSS、有图段独立绝对定位盒/无图段 title-wrap 流内居中两套 verse 窗口定位（verse DOM 统一渲染在 seg-card 尾部由竖屏 flex 钉底）、verse_kills_body 的横屏分支。②run.py/export_extras.py：--sub-mode 参数、解析与透传全删；subtitle_params_for 收窄为只按 aspect 区分（切分参数本就只随画幅变）。③check_series.py：params.sub_mode 退出跨集 drift 对比（aspect 一致则模式必然一致），旧报告残留字段忽略。④selftest 断言同步：横屏 verse 专属测试（独立盒几何/流内居中/开场收尾钉底/标题居中/竖屏显式 bar fail-fast）删除或改写为『横屏产物=bar、竖屏产物=verse』的形态断言 + run.py 源码级『--sub-mode 已移除』断言，533 → 525 条。⑤SKILL.md/rendering.md 模式章节改写为画幅绑定语义。净效果：selftest 523/525（2 个既有 h264 环境项）、dev/check 17/19（同 2 项），门控口径与改动前一致。
