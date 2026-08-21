# 信源适配参考

本技能第 1 步（整理信源）不绑定任何特定信源，SKILL.md 正文只给出信源类型的总览。
这里收录各类信源的具体接入方式，按需查阅；哪一种都不是必需的。

## 结构化资讯 API 示例：aihot（AI 日报/热点）

`aihot` 是一个返回"带标题/摘要的新闻条目列表"的公开 JSON 接口，适合"AI 日报视频"
"AI 资讯视频"这类场景。它只是众多可能信源里的一个具体例子——只要某个信源能返回
类似结构（标题 + 摘要 + 可选正文），都可以用同样的方式接入本技能的第 1 步。

```bash
# 优先取「运行当天日期」的日报（9:00 北京时间 = 当日 UTC 日报已生成，内容最新）。
# 取不到（404/超时）再回退到最新日报，并打印日期差提示。
DATE=$(date +%Y-%m-%d)
curl -sS --max-time 20 -H "User-Agent: aihot-skill/0.3.6 (+https://aihot.virxact.com/aihot-skill/)" \
  "https://aihot.virxact.com/api/public/daily/$DATE" \
  || curl -sS --max-time 20 -H "User-Agent: aihot-skill/0.3.6 (+https://aihot.virxact.com/aihot-skill/)" \
  "https://aihot.virxact.com/api/public/daily"
```

Windows PowerShell 下（`curl` 是 `Invoke-WebRequest` 的别名，须写全 `curl.exe`；`$(date)` / `date -d` 语法不可用）：

```powershell
$DATE = Get-Date -Format "yyyy-MM-dd"
curl.exe -sS --max-time 20 -H "User-Agent: aihot-skill/0.3.6 (+https://aihot.virxact.com/aihot-skill/)" `
  "https://aihot.virxact.com/api/public/daily/$DATE"
# 取不到（404/超时）再回退到最新日报：
curl.exe -sS --max-time 20 -H "User-Agent: aihot-skill/0.3.6 (+https://aihot.virxact.com/aihot-skill/)" `
  "https://aihot.virxact.com/api/public/daily"
```

> **日期滞后处理**：aihot `daily` 是按 UTC 日切的固定成品。若在 9:00 自动运行，优先用上面
> `$DATE` 取当日日报即可拿到最新内容；若仍回退到前一日（例如凌晨非计划时段手动跑），属正常，
> 标题/开场统一用**运行当天日期**填充即可，不影响投稿。

**更滚动的实时源（可选补充）**：若想要比日报更"当下"的条目，可用滚动 24 小时精选作为补充信源：

```bash
since=$(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ)
curl -sS --max-time 20 -H "User-Agent: aihot-skill/0.3.6 (+https://aihot.virxact.com/aihot-skill/)" \
  "https://aihot.virxact.com/api/public/items?mode=selected&since=$since&take=50"
```

Windows PowerShell 等价写法：

```powershell
$since = (Get-Date).ToUniversalTime().AddHours(-24).ToString("yyyy-MM-ddTHH:mm:ss'Z'")
curl.exe -sS --max-time 20 -H "User-Agent: aihot-skill/0.3.6 (+https://aihot.virxact.com/aihot-skill/)" `
  "https://aihot.virxact.com/api/public/items?mode=selected&since=$since&take=50"
```

用完 aihot（或任何同类 API）拿到的候选列表后，回到 SKILL.md 第 1 步的"选材判断标准"，
由当前对话模型挑选 5-8 条写进 `segments_source.json`——这一步和信源无关，所有信源类型
共用同一套判断标准和同一份输出格式。

## 其它信源接入方式

- **用户粘贴的文本/大纲**：直接读用户消息内容，无需额外工具；若文本已经有明显的分段
  （小标题、编号列表），可以直接按这些分段切成候选条目，省去自己重新切分的步骤。
- **用户上传的文档**：先用 `file-reading` 技能（及 pdf/docx/pptx 对应技能）判断文件类型
  并读出全文，再通读全文自行归纳出要点。整篇文档结构清晰时（有章节标题），优先按现有
  结构切分，保留原意，不要臆造原文没有的小标题。
  - **学习/讲解类文档**（教材、课程讲义、论文、技术文档）默认走 SKILL.md 里的"完整讲解
    模式"：目标是把文档**讲完整**，不是挑几个亮点——按目录/章节/概念顺序逐一展开，条数
    由文档结构决定，不强求 5-8 条。每段 `text` 可以比新闻模式写得更长、更展开（讲清楚
    "是什么 + 为什么 + 怎么用/举例"），但每句依然要按第 2 步的断句规则收尾。
  - 文档很长（拆完超过约 15-20 段）时，先给用户一个"目录预览"（比如列出打算拆成哪几段/
    哪几讲），确认是要一条长视频还是拆成系列短视频，不要默默压缩成几条草草带过。
  - 论文/报告类文档如果只是想要"精选摘要视频"而不是"逐节讲完"，按精选摘要模式处理即可
    （从全文里挑 5-8 个最核心的结论/发现）——是精选还是逐节讲解，先跟用户确认再动手。
- **网页/文章链接**：用 `web_fetch` 抓取正文；多篇文章要合并为一期视频时，处理方式和
  aihot 多信源场景一致——同一事件/同一论点的多篇报道只算一条，选信息最完整的来源。
- **会议记录/转写稿、读书笔记**：这类信源通常没有现成的"标题"，需要当前模型先通读，
  自己提炼每个要点的标题（一句话概括）再填入 `segments_source.json` 的 `title` 字段；
  `tagline` 字段可以填章节名、发言人、书名等，不强制是公司/机构名。

## 接入新信源的通用检查清单

不管信源是什么，进入第 2 步之前确认：

1. 已经有 5-8 条候选，每条能提炼出一个清晰的"标题"
2. 同一件事/同一个点没有被重复列成多条
3. 每条都能在 2-3 句话内讲清楚"是什么 + 为什么值得看"
4. 信源里明显的营销话术/无关寒暄已经被过滤掉，只保留实质内容

满足以上四点即可按 SKILL.md 第 2 步的格式写 `segments_source.json`，后续 TTS 配音、
配图、渲染四步完全不关心信源来自哪里。
