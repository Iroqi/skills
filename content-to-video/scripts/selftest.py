#!/usr/bin/env python3
"""Content-to-Video — 确定性逻辑冒烟测试（不联网、不调 API、不写文件）。

用法：
  python scripts/selftest.py

覆盖：
- _script_utils.split_sentences：标点/换行断句、短句合并
- _script_utils.split_subtitle_lines / split_subtitle_cues：字幕显示层固定宽度
  均衡切行（每行尽量填满到 max_chars；断点优先标点/空格、其次 CJK 字符间；
  英文词保持完整不劈开）
- build_from_structured.build_parts：逐段独立分句（pipeline --source 唯一路径）、
  自动摘要、段索引一致性、flow 模式 agenda 默认值、超长句写作提醒
- build_from_structured 双人对话（dialogue）：turns 区间解析、speakers 音色解析
- gen_hyperframes：无 segments 兜底分组、generate_html 结构完整性、开场目录自动生成、双人对话 cue 的 speaker/spk 注入（数据层）+ bar 模式说话人标签显示层与明暗自适应配色、flow 自然叙事模式（无 badge/无 wipe/开场预告为轻量 chips、收尾 recap 默认关）、长句 cue 切多行 + 每屏两行拆多 cue（时长按字符占比、cue 首尾相接）、字幕/内容双模式（verse 歌词式句子流 / bar 经典底部字幕条+正文卡）
- _env.get_key / resolve_model_config：CLI > 环境变量 > 配置文件
- _theme/_template：主题色板与模板加载
- verify_render.parse_ffmpeg_info：时长/编码解析
- _audio：atempo 过滤链构建
- _tts：合成失败路径（注入 fake client）
- _contracts：segments_source / timing_manifest / images.json 契约校验
- render_watch.resolve_command：Windows 下 npx.cmd 的解析
- search_images.relevance_score：本地相关性提示分
- budget：时长估算函数（单句时长随字数/语速缩放、estimate 子命令输出）+
  cost 子命令（配图 credits 成本区间估算）
- export_extras：章节时间戳/SRT 格式化 + 生成（build_chapters、_fmt_chapter_ts、_fmt_srt_ts）
- gen_charts：图表校验（labels/values 不匹配、饼图全 0 拒绝）+ bar/line/pie/curve 实际渲染与 4:3 画布尺寸断言（无 matplotlib 时跳过，不影响核心流程判定）
- split_series：parse_sections（Markdown 标题切分 / 无标题退化为空行分块 / 空文档报错）+
  plan_episodes（target_seconds 与 n_episodes 两种模式都不拆开原始小节）+
  write_skeleton 的 _series_meta（多集才附加、prev/next 互相链接正确、单集不附加）
- gen_cover：build_title_candidates（去重/封顶 3 条/空 segments）+ _first_sentence/_hex_to_rgb/_gradient_endpoints 几个纯函数
- run._image_coverage：缺图拦截判定（images.json 不存在时全部 news/seg 段落算 missing、
  部分配图只报未覆盖、opening/closing 不参与、无 segments 字段时无义务）

- [22] 深度 review 修复回归组：_tts 残留 sidecar 清理、search_images --pick 审阅决策（0 弃用/越界报错不删候选/未提及保留/已定稿继承）、pipeline --check-env 独立运行、BGM amix normalize=0、run.py --aspect both 双成片、字幕行内透明度不再双重相乘

任何一项失败都会打印 [FAIL] 并以非零码退出。修改上述模块后建议先跑一遍本测试。
"""
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

passed = 0
failed = 0
RESULTS = []


def check(name, cond, detail=""):
    global passed, failed
    RESULTS.append((name, bool(cond)))
    if cond:
        passed += 1
        print(f"  [OK] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


def main():
    print("=== content-to-video selftest ===")

    # 1. split_sentences
    from _script_utils import split_sentences
    print("\n[1] split_sentences")
    s = split_sentences("大家好呀。这是第二句！第三句呀？")
    check("basic terminators", s == ["大家好呀。", "这是第二句！", "第三句呀？"], repr(s))
    s = split_sentences("这是第一句；这是第二句。\n这是第三行。")
    check("semicolon + newline split", len(s) == 3, repr(s))
    s = split_sentences("短。后面长句子合并进来。")
    check("short fragment merged",
          len(s) == 1 and s[0] == "短。后面长句子合并进来。", repr(s))
    check("empty input", split_sentences("") == [])

    # 2. build_from_structured（pipeline --source 用的分句/分组库）
    print("\n[2] build_from_structured")
    from _theme import get_accent_palette
    from build_from_structured import build_parts
    palette = get_accent_palette()
    src = {
        "opening": "大家好，欢迎收看今天的AI日报。",
        "closing": "感谢收看，明天见。",
        "segments": [
            {"title": "A", "text": "第一条新闻内容，值得关注。这是补充信息，也很重要。"},
            {"title": "B", "text": "第二条新闻内容，同样重要。后续进展，值得跟踪。"},
        ],
    }
    sentences, segments = build_parts(src)
    check("segment ids",
          [x["id"] for x in segments] == ["opening", "seg1", "seg2", "closing"],
          [x["id"] for x in segments])
    check("sentence count", len(sentences) == 6, len(sentences))
    check("opening speed default 1.2", segments[0].get("speed") == 1.2)
    check("closing speed default 1.2", segments[-1].get("speed") == 1.2)
    check("opening title default neutral", segments[0]["title"] == "本期内容",
          segments[0]["title"])
    check("closing title default neutral", segments[-1]["title"] == "小结",
          segments[-1]["title"])
    titled_src = dict(src, opening_title="欧拉恒等式", closing_title="本期回顾")
    _, titled_segs = build_parts(titled_src)
    check("custom opening/closing title honored",
          titled_segs[0]["title"] == "欧拉恒等式"
          and titled_segs[-1]["title"] == "本期回顾",
          [titled_segs[0]["title"], titled_segs[-1]["title"]])
    check("opening/closing tagline default empty",
          segments[0].get("tagline", "") == ""
          and segments[-1].get("tagline", "") == "",
          [segments[0].get("tagline"), segments[-1].get("tagline")])
    tagged_src = dict(src, opening_tagline="每日要闻", closing_tagline="明天见")
    _, tagged_segs = build_parts(tagged_src)
    check("custom opening/closing tagline honored",
          tagged_segs[0]["tagline"] == "每日要闻"
          and tagged_segs[-1]["tagline"] == "明天见",
          [tagged_segs[0].get("tagline"), tagged_segs[-1].get("tagline")])
    check("opening agenda / closing recap default true",
          segments[0].get("agenda") is True and segments[-1].get("recap") is True,
          [segments[0].get("agenda"), segments[-1].get("recap")])
    clean_src = dict(src, opening_agenda=False, closing_recap=False)
    _, clean_segs = build_parts(clean_src)
    check("opening agenda / closing recap disabled",
          clean_segs[0].get("agenda") is False and clean_segs[-1].get("recap") is False,
          [clean_segs[0].get("agenda"), clean_segs[-1].get("recap")])
    body = segments[1].get("body", "")
    _seg = segments[1]
    _seg_sents = sentences[_seg["start"]:_seg["end"]]
    check("auto body = all sentences (one per line)",
          body.split("\n") == _seg_sents, repr(body))
    check("accent cycles palette",
          segments[1]["accent"] == palette[0] and segments[2]["accent"] == palette[1])
    check("opening/closing body kept empty",
          segments[0].get("body", "") == "" and segments[-1].get("body", "") == "")
    src_ob = dict(src, opening_body="课程简介第一行\n课程简介第二行",
                  closing_body="收尾寄语第一行")
    _, ob_segs = build_parts(src_ob)
    check("opening_body/closing_body honored",
          ob_segs[0].get("body") == "课程简介第一行\n课程简介第二行"
          and ob_segs[-1].get("body") == "收尾寄语第一行",
          [ob_segs[0].get("body"), ob_segs[-1].get("body")])
    # 自动 body 行预算：5 句 30 字长句（每句占 2 视觉行）→ 7 行预算只放
    # 得下前 3 句；短句不受影响。防止兜底 body 溢出压到字幕栏。
    long_seg_src = {
        "opening": "", "closing": "",
        "segments": [{"title": "长段", "text":
                      "这是一句三十个字左右的长句子用来测试预算一。"
                      "这是一句三十个字左右的长句子用来测试预算二。"
                      "这是一句三十个字左右的长句子用来测试预算三。"
                      "这是一句三十个字左右的长句子用来测试预算四。"
                      "这是一句三十个字左右的长句子用来测试预算五。"}],
    }
    _, long_segs = build_parts(long_seg_src)
    _lb = long_segs[0].get("body", "")
    check("auto body capped by visual-line budget",
          len(_lb.split("\n")) == 3 and "预算四" not in _lb, repr(_lb))

    # 各段独立分句：段内短句只在本段内合并，不跨段错位
    bad_src = {
        "opening": "",
        "closing": "",
        "segments": [
            {"title": "A", "text": "苹果发布了新手机。真棒。"},
            {"title": "B", "text": "OpenAI 发布了新模型。"},
        ],
    }
    sents3, segs3 = build_parts(bad_src)
    check("independent per-segment split",
          sents3 == ["苹果发布了新手机。真棒。", "OpenAI 发布了新模型。"], repr(sents3))
    check("segment indices consistent",
          segs3[0]["start"] == 0 and segs3[0]["end"] == 1
          and segs3[1]["start"] == 1 and segs3[1]["end"] == 2)

    # 2b. 双人对话（dialogue）段落
    print("\n[2b] build_from_structured dialogue（双人对话）")
    dialogue_src = {
        "speakers": {
            "host": {"voice_id": "voiceA", "label": "主播"},
            "guide": {"voice_id": "voiceB", "label": "讲解", "voice_style": "耐心讲解"},
        },
        "segments": [
            {
                "title": "反向传播算法",
                "tagline": "深度学习基础",
                "dialogue": [
                    {"speaker": "host", "text": "反向传播听起来很复杂，能简单说说吗？"},
                    {"speaker": "guide", "text": "简单说，就是把误差从输出层往回传，一层层调整参数。这样模型才能越训越准。"},
                ],
            },
        ],
    }
    sents_d, segs_d = build_parts(dialogue_src)
    check("dialogue sentence count", len(sents_d) == 3, len(sents_d))
    seg_d = segs_d[0]
    check("dialogue segment has turns", "turns" in seg_d and len(seg_d["turns"]) == 2,
          seg_d.get("turns"))
    turn0, turn1 = seg_d["turns"]
    check("dialogue turn0 range + voice resolved",
          turn0["start"] == 0 and turn0["end"] == 1
          and turn0["voice_id"] == "voiceA" and turn0["label"] == "主播", turn0)
    check("dialogue turn1 range + voice_style resolved",
          turn1["start"] == 1 and turn1["end"] == 3
          and turn1["voice_id"] == "voiceB" and turn1["voice_style"] == "耐心讲解", turn1)

    # text 和 dialogue 同时提供 / 都不提供 —— 走 _contracts 校验（见 [8]）

    # 2c. flow 自然叙事模式 + 超长句写作提醒
    print("\n[2c] build_from_structured flow 模式 + 超长句提醒")
    from build_from_structured import LONG_SENTENCE_CHARS
    flow_src = dict(src, flow=True)
    _, flow_segs = build_parts(flow_src)
    check("flow: opening agenda defaults on (chips 预告)",
          flow_segs[0].get("agenda") is True, flow_segs[0].get("agenda"))
    check("flow: closing recap defaults off",
          flow_segs[-1].get("recap") is False, flow_segs[-1].get("recap"))
    flow_src_on = dict(src, flow=True, opening_agenda=True, closing_recap=True)
    _, flow_segs_on = build_parts(flow_src_on)
    check("flow: explicit opening_agenda honored",
          flow_segs_on[0].get("agenda") is True)
    check("flow: explicit closing_recap honored",
          flow_segs_on[-1].get("recap") is True)
    _, default_segs = build_parts(src)
    check("no flow: agenda defaults on (chapters)",
          default_segs[0].get("agenda") is True)
    try:
        from _contracts import validate_segments_source as _vss
        _vss(dict(src, flow="yes"))
        check("flow: non-bool rejected", False, "no exception")
    except ValueError as e:
        check("flow: non-bool rejected", "flow" in str(e), str(e)[:60])
    long_sent_src = dict(src, segments=[
        {"title": "长句段",
         "text": "这是一个明显超过四十五个字符上限的超长句子用来触发写作提醒，"
                 "因为一句话念完会喘不过气，字幕也需要切成多行显示才放得下。"}])
    import contextlib
    err_buf = io.StringIO()
    with contextlib.redirect_stderr(err_buf):
        build_parts(long_sent_src)
    check("long sentence (>45 chars) triggers warn",
          "[warn]" in err_buf.getvalue() and "45" in err_buf.getvalue(),
          err_buf.getvalue()[:80])

    # 2d. split_subtitle_lines：字幕显示层二次切行（纯函数，确定性）
    print("\n[2d] _script_utils.split_subtitle_lines")
    from _script_utils import split_subtitle_lines as ssl
    check("ssl: short stays single line", ssl("短句。") == ["短句。"])
    check("ssl: max_lines=1 disables splitting",
          ssl("很长，很长，很长的句子。", max_lines=1) == ["很长，很长，很长的句子。"])
    long = ("今天我们要讲一个很长的主题，它包含三个部分的内容，"
            "分别是背景原理和实际应用，我们会逐一展开说明。")
    lines = ssl(long)
    check("ssl: long splits into 2-3 lines", 2 <= len(lines) <= 3, lines)
    check("ssl: join preserves original text", "".join(lines) == long)
    check("ssl: each line within width (<=28)",
          max(len(l) for l in lines) <= 28, [len(l) for l in lines])
    check("ssl: lines reasonably balanced (equal-width)",
          (max(len(l) for l in lines) - min(len(l) for l in lines)) <= 18,
          [len(l) for l in lines])
    # 英文词不劈开：含英文专名的句子，每个英文词完整出现在某一行
    import re as _re_ssl
    eng = ("Google 开源了 Agent Development Kit 与 Gemini 模型，"
           "用于构建零信任客服与退货智能体示例。")
    el = ssl(eng)
    ewords = _re_ssl.findall(r"[A-Za-z0-9][A-Za-z0-9.\-/]*", eng)
    check("ssl: no english word split across lines",
          all(any(w in l for l in el) for w in ewords), (ewords, el))
    check("ssl: english sentence join preserves text", "".join(el) == eng)
    chunks = ssl("一" * 60)
    check("ssl: no-punct long hard-splits to <=hard_cap (no overflow)",
          all(len(c) <= 40 for c in chunks) and "".join(chunks) == "一" * 60, chunks)
    # 2e. split_subtitle_cues：每屏最多两行的 cue 分组（纯函数，确定性）
    print("\n[2e] _script_utils.split_subtitle_cues")
    from _script_utils import split_subtitle_cues as ssc
    check("ssc: short sentence single cue", ssc("短句。") == [["短句。"]])
    cues_g = ssc(long)
    check("ssc: every cue has at most 2 lines",
          all(len(g) <= 2 for g in cues_g), cues_g)
    huge = "、".join(f"第{i}个要点需要展开说明" for i in range(1, 25)) + "。"
    hcues = ssc(huge)
    check("ssc: huge sentence splits into multiple cues", len(hcues) >= 2, hcues)
    check("ssc: huge sentence every cue <= 2 lines",
          all(len(g) <= 2 for g in hcues), [len(g) for g in hcues])
    check("ssc: every line within soft cap x1.5",
          all(len(l) <= 28 * 1.5 for g in hcues for l in g),
          [[len(l) for l in g] for g in hcues])
    check("ssc: join preserves original text",
          "".join("".join(g) for g in hcues) == huge)
    hc = ssc("一" * 60)
    hc_flat = [l for g in hc for l in g]
    check("ssc: no-punct long hard-splits across cues <=hard_cap",
          all(len(l) <= 40 for l in hc_flat) and "".join(hc_flat) == "一" * 60, hc)
    # 回归：含英文专名的稀疏标点长句——不能整句交
    # CSS 换行溢出成 3+ 视觉行；次要标点切不动时按硬上限字符级切分。
    sparse = ("Google 开源了基于 Agent Development Kit 和 Gemini 的零信任客服与"
              "退货智能体示例，专门演示如何防御提示注入。")
    sp_cues = ssc(sparse, hard_cap=34)
    sp_flat = [l for g in sp_cues for l in g]
    check("ssc: sparse-punct long sentence never overflows (all lines <=34)",
          all(len(l) <= 34 for l in sp_flat), sp_flat)
    check("ssc: sparse-punct join preserves original text",
          "".join(sp_flat) == sparse, sp_flat)

    # 3. gen_hyperframes
    print("\n[3] gen_hyperframes")
    from gen_hyperframes import fallback_segments, generate_html
    manifest = {
        "sentences": [
            {"index": i, "text": f"第{['一', '二', '三', '四', '五', '六'][i]}句。",
             "start_time": i * 2.4, "duration": 2.0}
            for i in range(6)
        ],
        "total_duration": 14.5,
    }
    segs = fallback_segments(manifest["sentences"])
    check("fallback chunk size 5", len(segs) == 2
          and all(len(s["sentences"]) <= 5 for s in segs), len(segs))
    check("fallback ids", [s["id"] for s in segs] == ["seg1", "seg2"])
    from _theme import get_default_accent
    default_accent = get_default_accent()
    check("fallback accent", all(s["accent"] == default_accent for s in segs))
    # 横屏默认 bar（模式已固定按画幅绑定，不可传参）
    html = generate_html(manifest, "audio/combined.wav")
    check("html has audio id", 'id="main-audio"' in html)
    check("html has timeline", 'window.__timelines["main"]' in html)
    check("html duration matches manifest",
          f'data-duration="{manifest["total_duration"]:.2f}"' in html)
    check("html has subtitle cues", "const cues" in html and "第一句。" in html)

    # 每屏两行拆 cue：长句 → 多条 cue（时长按字符占比、首尾相接）
    import re as _re
    long3 = "，".join(f"第{i}个要点需要展开说明" for i in range(1, 25)) + "。"
    manifest3 = {
        "sentences": [{"index": 0, "text": long3,
                       "start_time": 0.0, "duration": 6.0}],
        "total_duration": 6.5,
    }
    html3 = generate_html(manifest3, "audio/combined.wav")
    # cue 格式为 {t,d,si,lines}（si=句子全局序号，竖屏 verse
    # 高亮用）；正则容忍 si 段缺省以兼容旧格式输出
    cue_blocks = _re.findall(
        r"\{t:([\d.]+),d:([\d.]+),(?:si:-?\d+,)?lines:\[(.*?)\]\}", html3)
    check("cue split: long sentence yields multiple cues",
          len(cue_blocks) >= 2, cue_blocks)
    import json as _json
    _cue_line_counts = []
    for _b in cue_blocks:
        try:
            _cue_line_counts.append(len(_json.loads("[" + _b[2] + "]")))
        except Exception:
            _cue_line_counts.append(-1)
    check("cue split: every cue has at most 2 lines",
          all(c <= 2 for c in _cue_line_counts), _cue_line_counts)
    _ts = [float(b[0]) for b in cue_blocks]
    _ds = [float(b[1]) for b in cue_blocks]
    check("cue split: cues are contiguous",
          all(abs(_ts[i + 1] - (_ts[i] + _ds[i])) < 0.02
              for i in range(len(_ts) - 1)), list(zip(_ts, _ds)))
    check("cue split: total span matches sentence window",
          abs((_ts[0] + sum(_ds)) - 6.0) < 0.02, (_ts, _ds))

    manifest2 = {
        **manifest,
        "segments": [{
            "id": "news1", "title": "标题", "tagline": "公司",
            "body": "第一行\n第二行", "accent": "#ffd54f",
            "sentences": manifest["sentences"][:3],
        }],
    }
    html2 = generate_html(manifest2, "audio/combined.wav",
                          images={"news1": "images/news1.jpg"})
    check("image card rendered",
          'id="img-news1"' in html2 and "images/news1.jpg" in html2)
    check("news body renders without agenda", 'id="agendalist-' not in html2)

    manifest3 = {
        **manifest,
        "segments": [
            {"id": "opening", "title": "AI 日报", "tagline": "", "body": "",
             "accent": default_accent, "sentences": manifest["sentences"][:1]},
            {"id": "news1", "title": "标题一", "tagline": "", "body": "",
             "accent": "#ffd54f", "sentences": manifest["sentences"][1:4]},
        ],
    }
    html3 = generate_html(manifest3, "audio/combined.wav")
    check("opening agenda auto-generated", 'id="agendalist-' in html3)
    manifest4 = {
        **manifest,
        "segments": [
            {"id": "opening", "title": "开场", "tagline": "", "body": "",
             "accent": default_accent, "agenda": False,
             "sentences": manifest["sentences"][:1]},
            {"id": "news1", "title": "标题一", "tagline": "", "body": "",
             "accent": "#ffd54f", "sentences": manifest["sentences"][1:4]},
            {"id": "closing", "title": "结尾", "tagline": "", "body": "",
             "accent": default_accent, "recap": False,
             "sentences": manifest["sentences"][4:5]},
        ],
    }
    html4 = generate_html(manifest4, "audio/combined.wav")
    check("opening agenda suppressed", 'id="agendalist-' not in html4)
    check("closing recap suppressed", 'id="chiprow-' not in html4)
    # 目录/回顾软上限 = 模板 agenda.maxItems（默认 7）：超出取前 N 条
    # 显示并打 warn（stderr，不进画面）；"确有必要突破" = 调大模板值。
    # 不加"等共 N 条"提示行——那是画面上的脏东西。
    many_sents = [
        {"index": i, "text": f"第{i}个要点的内容。",
         "start_time": i * 2.0, "duration": 1.8}
        for i in range(12)
    ]
    manifest_many = {
        "sentences": many_sents,
        "total_duration": 24.5,
        "segments": (
            [{"id": "opening", "title": "开场", "tagline": "", "body": "",
              "accent": default_accent, "sentences": many_sents[:1]}]
            + [{"id": f"news{k}", "title": f"第{k}条", "tagline": "", "body": "",
                "accent": "#ffd54f", "sentences": [many_sents[k]]}
               for k in range(1, 11)]
            + [{"id": "closing", "title": "收尾", "tagline": "", "body": "",
                "accent": default_accent, "sentences": [many_sents[11]]}]
        ),
    }
    html_many = generate_html(manifest_many, "audio/combined.wav")
    _ag_cap = json.load(open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "template.json"),
        encoding="utf-8"))["layout"]["landscape"]["agenda"]["maxItems"]
    check(f"agenda 超过 maxItems({_ag_cap}) 截断到上限",
          f'id="agendaitem-opening-{_ag_cap - 1}"' in html_many
          and f'id="agendaitem-opening-{_ag_cap}"' not in html_many)
    check(f"closing recap 同受 maxItems({_ag_cap}) 截断",
          f'id="recapchip-closing-{_ag_cap - 1}"' in html_many
          and f'id="recapchip-closing-{_ag_cap}"' not in html_many)
    check("无 '等共 N 条' 封顶提示行", "等共" not in html_many)

    # 3b. flow 自然叙事模式渲染：无编号 badge、无 wipe 扫场、开场预告是
    # 轻量 chips（默认生成，竖排编号目录仍不出现）、closing
    # 无 recap、长句 cue 切多行（手写 manifest 也不带 agenda 键，验证
    # gen 层默认值）
    print("\n[3b] gen_hyperframes flow 模式")
    flow_manifest = {
        **manifest,
        "flow": True,
        "segments": [
            {"id": "opening", "title": "讲解", "tagline": "", "body": "",
             "accent": default_accent, "sentences": manifest["sentences"][:1]},
            {"id": "news1", "title": "第一部分", "tagline": "", "body": "",
             "accent": "#ffd54f", "sentences": manifest["sentences"][1:4]},
            {"id": "news2", "title": "第二部分", "tagline": "", "body": "",
             "accent": "#4dd0e1", "sentences": manifest["sentences"][4:6]},
        ],
    }
    html_flow = generate_html(flow_manifest, "audio/combined.wav")
    check("flow: no numbered badge rendered", 'class="badge"' not in html_flow)
    check("flow: no wipe sweep animation", 'tl.set("#twipe"' not in html_flow)
    check("flow: 竖排编号目录不出现（chips 替代）", 'id="agendalist-' not in html_flow)
    check("flow: opening chips 预告默认生成",
          'id="chiprow-opening"' in html_flow)
    check("flow: chips 预告带 accent 描边",
          'recapchip-opening-0' in html_flow and 'border-color' in html_flow)
    check("flow: 无 '等共 N 条' 封顶提示行", "等共" not in html_flow)
    flow_manifest_off = {
        **manifest,
        "flow": True,
        "segments": [
            {"id": "opening", "title": "讲解", "tagline": "", "body": "",
             "accent": default_accent, "agenda": False,
             "sentences": manifest["sentences"][:1]},
            {"id": "news1", "title": "第一部分", "tagline": "", "body": "",
             "accent": "#ffd54f", "sentences": manifest["sentences"][1:4]},
            {"id": "news2", "title": "第二部分", "tagline": "", "body": "",
             "accent": "#4dd0e1", "sentences": manifest["sentences"][4:6]},
        ],
    }
    html_flow_off = generate_html(flow_manifest_off, "audio/combined.wav")
    check("flow: agenda=false 显式关闭 chips 预告",
          'id="chiprow-opening"' not in html_flow_off)
    check("chapters: wipe sweep present (control)",
          'tl.set("#twipe"' in html3)
    long_cue_manifest = {
        "sentences": [
            {"index": 0, "text": "这是一个特别长的句子，包含很多修饰成分和并列信息、还有顿号列举，"
                                "让观众一口气读完会很累。", "start_time": 0.0, "duration": 5.0},
        ],
        "total_duration": 5.0,
    }
    # 长句切行测试用横屏（bar）：切出的行直接渲染进字幕条
    html_long = generate_html(long_cue_manifest, "audio/combined.wav")
    cue_zone = html_long[html_long.find("const cues"):html_long.find("const cues") + 500]
    check("long sentence cue carries multiple lines",
          'lines:["' in cue_zone and '","' in cue_zone, cue_zone[:160])
    check("bar mode: sub-line css present", ".sub-line + .sub-line" in html_long)
    check("preview externalized to preview.js",
          'src="preview.js"' in html and "__pvUpdate" in html)
    pj = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "preview.js"), encoding="utf-8").read()
    check("preview.js gated for headless",
          "navigator.webdriver" in pj and "__pvUpdate" in pj
          and "HeadlessChrome" in pj and "has('preview')" in pj)

    # 4b. 双人对话数据层：speaker 字段 -> cues 里的 speaker/spk（显示层
    # 已随 sub-bar 移除，verse 句子流不区分说话人；数据保留供下游消费）
    dlg_manifest = {
        "sentences": [
            {"index": 0, "text": "问题句。", "start_time": 0.0, "duration": 2.0, "speaker": "主播"},
            {"index": 1, "text": "回答句。", "start_time": 2.0, "duration": 2.0, "speaker": "讲解"},
            {"index": 2, "text": "普通句。", "start_time": 4.0, "duration": 2.0},
        ],
        "total_duration": 6.0,
    }
    html_dlg = generate_html(dlg_manifest, "audio/combined.wav")
    check("dialogue cues carry speaker+spk",
          'speaker:"主播",spk:0' in html_dlg and 'speaker:"讲解",spk:1' in html_dlg,
          html_dlg[html_dlg.find("const cues"):html_dlg.find("const cues") + 300])
    check("non-dialogue sentence has no speaker key in its cue",
          'lines:["普通句。"]}' in html_dlg)

    # 3+ 说话人：按首次出现顺序分配 0/1/2/...，取完循环（%2 会让第三个
    # 说话人复用第一个的颜色）。用 4 个说话人验证 spk:0/1/2/3 都被正确分配。
    three_spk_manifest = {
        "sentences": [
            {"index": 0, "text": "甲说。", "start_time": 0.0, "duration": 1.0, "speaker": "甲"},
            {"index": 1, "text": "乙说。", "start_time": 1.0, "duration": 1.0, "speaker": "乙"},
            {"index": 2, "text": "丙说。", "start_time": 2.0, "duration": 1.0, "speaker": "丙"},
            {"index": 3, "text": "丁说。", "start_time": 3.0, "duration": 1.0, "speaker": "丁"},
        ],
        "total_duration": 4.0,
    }
    html_three = generate_html(three_spk_manifest, "audio/combined.wav", theme="cream")
    check("3+ speaker spk indices distinct",
          'speaker:"甲",spk:0' in html_three
          and 'speaker:"乙",spk:1' in html_three
          and 'speaker:"丙",spk:2' in html_three
          and 'speaker:"丁",spk:3' in html_three)

    # 说话人配色显示层（bar 模式）：要随字幕栏背景明暗切换，不能固定同
    # 一对颜色——浅色字幕栏（默认 cream）配固定亮色会看不清。锁死两种
    # 主题下应拿到不同颜色，防止这个具体问题再犯。
    html_cream_bar = generate_html(dlg_manifest, "audio/combined.wav",
                                   theme="cream")
    html_dark_bar = generate_html(dlg_manifest, "audio/combined.wav",
                                  theme="dark")
    check("bar mode: speaker colors differ between light/dark bar themes",
          ".sub-speaker.spk-0{color:#1d4ed8}" in html_cream_bar
          and ".sub-speaker.spk-0{color:#7dd3fc}" in html_dark_bar)
    html_three_bar = generate_html(three_spk_manifest, "audio/combined.wav",
                                   theme="cream")
    check("bar mode: speaker color css has >= 6 classes",
          ".sub-speaker.spk-5{color:" in html_three_bar)

    # 5. _env key priority + model config resolution
    print("\n[4] _env key priority")
    from _env import get_key, resolve_model_config
    old = os.environ.get("MIMO_API_KEY")
    os.environ["MIMO_API_KEY"] = "env-test"
    try:
        check("env var picked up", get_key("MIMO_API_KEY") == "env-test")
        check("cli value highest priority",
              get_key("MIMO_API_KEY", "cli-test") == "cli-test")
    finally:
        if old is None:
            os.environ.pop("MIMO_API_KEY", None)
        else:
            os.environ["MIMO_API_KEY"] = old
    old_model = os.environ.get("MIMO_CHAT_MODEL")
    old_url = os.environ.get("MIMO_BASE_URL")
    os.environ["MIMO_CHAT_MODEL"] = "env-model"
    os.environ["MIMO_BASE_URL"] = "https://env.example/v1"
    try:
        m, u = resolve_model_config(None, None, "MIMO_CHAT_MODEL", "default-model")
        check("model env override", m == "env-model", m)
        check("base url env override", u == "https://env.example/v1", u)
        m2, u2 = resolve_model_config("cli-model", "https://cli.example/v1",
                                      "MIMO_CHAT_MODEL", "default-model")
        check("cli model/base highest",
              m2 == "cli-model" and u2 == "https://cli.example/v1", (m2, u2))
    finally:
        if old_model is None:
            os.environ.pop("MIMO_CHAT_MODEL", None)
        else:
            os.environ["MIMO_CHAT_MODEL"] = old_model
        if old_url is None:
            os.environ.pop("MIMO_BASE_URL", None)
        else:
            os.environ["MIMO_BASE_URL"] = old_url

    # 6. theme + template loading
    print("\n[5] theme + template")
    from _theme import get_accent_palette, get_theme_colors, list_theme_names
    pal = get_accent_palette()
    check("accent palette 8 colors",
          len(pal) == 8 and all(c.startswith("#") for c in pal))
    cream = get_theme_colors("cream")
    check("theme keys present",
          {"bg_gradient", "text_color", "sub_bar_rgb", "soft_border"} <= set(cream))
    # 每个 registry.json 里注册的主题都要有完整 8 个键，缺一个就会在渲染时
    # 崩在某处 f-string KeyError（或更糟——留一个 undefined 悄悄进了 CSS）。
    # 加新主题时最容易漏这个，这里做穷举校验而不是只测 cream。
    _theme_required_keys = {"bg_gradient", "grid_color", "text_color", "body_bg",
                            "body_text", "sub_bar_rgb", "soft_border", "sub_text_shadow"}
    theme_names = list_theme_names()
    check("at least cream/dark registered", {"cream", "dark"} <= set(theme_names), theme_names)
    for name in theme_names:
        colors = get_theme_colors(name)
        check(f"theme '{name}' has all required keys",
              _theme_required_keys <= set(colors),
              f"missing: {_theme_required_keys - set(colors)}")
    from _template import load_template
    tpl = load_template()
    check("template layout keys", {"landscape", "vertical"} <= set(tpl["layout"]))
    check("vertical body maxWidth set",
          tpl["layout"]["vertical"]["body"].get("maxWidth", 0) > 0)

    # 7. verify_render parsing
    print("\n[6] verify_render")
    from verify_render import parse_ffmpeg_info
    sample = """ffmpeg version ...
  Duration: 00:00:42.03, start: 0.000000, bitrate: 1234 kb/s
    Stream #0:0(und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(tv, bt709), 1920x1080 [SAR 1:1 DAR 16:9], 25 fps
    Stream #0:1(und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, fltp, 128 kb/s
"""
    dur, video, audio = parse_ffmpeg_info(sample)
    check("verify parse duration", dur is not None and abs(dur - 42.03) < 0.001, dur)
    check("verify parse codecs", video == "h264" and audio == "aac", (video, audio))

    # 8. _audio + _tts
    print("\n[7] _audio + _tts")
    from _audio import build_atempo_filter
    check("atempo 1.0 -> None", build_atempo_filter(1.0) is None)
    check("atempo 1.5", build_atempo_filter(1.5) == "atempo=1.5",
          build_atempo_filter(1.5))
    check("atempo 3.0 chained", build_atempo_filter(3.0) == "atempo=2.0,atempo=1.5",
          build_atempo_filter(3.0))
    check("atempo 0.25 chained",
          build_atempo_filter(0.25) == "atempo=0.5,atempo=0.5",
          build_atempo_filter(0.25))
    # 回归：speed<=0 / 非有限值会让第二个 while 死循环（remaining/=0.5 对
    # 非正数永不收敛），现在必须在入口就抛 ValueError。
    for bad_speed in (0, -1, 0.0, -0.5):
        try:
            build_atempo_filter(bad_speed)
            check(f"atempo rejects non-positive speed {bad_speed}", False,
                  "no exception")
        except ValueError:
            check(f"atempo rejects non-positive speed {bad_speed}", True)
    for bad_speed in (float("nan"), float("inf"), float("-inf")):
        try:
            build_atempo_filter(bad_speed)
            check(f"atempo rejects non-finite speed {bad_speed}", False,
                  "no exception")
        except ValueError:
            check(f"atempo rejects non-finite speed {bad_speed}", True)

    from _audio import build_loudnorm_filter
    check("loudnorm filter default",
          build_loudnorm_filter() == "loudnorm=I=-16.0:TP=-1.5:LRA=11",
          build_loudnorm_filter())
    check("loudnorm filter custom",
          build_loudnorm_filter(-14) == "loudnorm=I=-14:TP=-1.5:LRA=11",
          build_loudnorm_filter(-14))

    from _tts import synth_sentence

    class _FakeFailClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("boom")

    _synth_ok, _synth_spd = synth_sentence(
        _FakeFailClient(), "测试句子。", "冰糖", "风格",
        "unused.wav", max_retries=1)
    check("synth failure returns ok=False without retries", _synth_ok is False)
    check("synth failure returns speed_applied=False", _synth_spd is False)

    # 9. _contracts
    print("\n[8] _contracts")
    from _contracts import (validate_segments_source, validate_timing_manifest,
                            validate_images_json)
    good_src = {"opening": "好。", "segments": [{"title": "A", "text": "内容。"}]}
    check("segments source valid",
          validate_segments_source(good_src) is good_src)
    for bad in [{"segments": []},
                {"segments": [{"title": "A"}]},
                {"segments": [{"title": "", "text": "x"}]},
                {}]:
        try:
            validate_segments_source(bad)
            check("segments source rejects invalid", False, repr(bad))
        except ValueError:
            check("segments source rejects invalid", True)

    # dialogue（双人对话）相关校验
    good_dialogue_src = {
        "speakers": {"host": {"voice_id": "茉莉"}, "guide": {"voice_id": "苏打"}},
        "segments": [{
            "title": "A", "tagline": "T",
            "dialogue": [{"speaker": "host", "text": "问题。"},
                         {"speaker": "guide", "text": "回答。"}],
        }],
    }
    check("dialogue segments source valid",
          validate_segments_source(good_dialogue_src) is good_dialogue_src)
    for bad in [
        # dialogue 段落但顶层没有 speakers
        {"segments": [{"title": "A",
                       "dialogue": [{"speaker": "host", "text": "x"}]}]},
        # dialogue 引用了未声明的说话人
        {"speakers": {"host": {"voice_id": "v1"}},
         "segments": [{"title": "A",
                       "dialogue": [{"speaker": "guide", "text": "x"}]}]},
        # 说话人声明缺 voice_id
        {"speakers": {"host": {}},
         "segments": [{"title": "A",
                       "dialogue": [{"speaker": "host", "text": "x"}]}]},
        # text 和 dialogue 同时提供
        {"speakers": {"host": {"voice_id": "v1"}},
         "segments": [{"title": "A", "text": "x",
                       "dialogue": [{"speaker": "host", "text": "x"}]}]},
        # dialogue 轮次缺 text
        {"speakers": {"host": {"voice_id": "v1"}},
         "segments": [{"title": "A", "dialogue": [{"speaker": "host"}]}]},
    ]:
        try:
            validate_segments_source(bad)
            check("dialogue segments source rejects invalid", False, repr(bad))
        except ValueError:
            check("dialogue segments source rejects invalid", True)

    # 取值校验：speed 必须 >0 的有限数值、voice_id 必须是预置音色
    for bad in [
        {"segments": [{"title": "A", "text": "内容。", "speed": 0}]},
        {"segments": [{"title": "A", "text": "内容。", "speed": -1.5}]},
        {"segments": [{"title": "A", "text": "内容。", "speed": "快"}]},
        {"segments": [{"title": "A", "text": "内容。", "voice_id": "不存在的音色"}]},
        {"opening_speed": 0, "segments": [{"title": "A", "text": "内容。"}]},
        {"closing_speed": float("inf"), "segments": [{"title": "A", "text": "内容。"}]},
        {"speakers": {"host": {"voice_id": "bad"}},
         "segments": [{"title": "A",
                       "dialogue": [{"speaker": "host", "text": "问题。"}]}]},
    ]:
        try:
            validate_segments_source(bad)
            check("segments source rejects bad speed/voice value", False, repr(bad))
        except ValueError:
            check("segments source rejects bad speed/voice value", True)

    # 音色注册表 + 段落级 voice_id/voice_style 透传（--source 路径）
    from _voices import list_voice_ids, is_valid_voice_id
    check("voice registry has 8 presets",
          len(list_voice_ids()) == 8, list_voice_ids())
    check("voice registry valid id",
          is_valid_voice_id("冰糖") and is_valid_voice_id("Mia"))
    check("voice registry rejects unknown", not is_valid_voice_id("fake"))
    from build_from_structured import build_parts
    seg_voice_src = {"segments": [
        {"title": "A", "text": "内容。", "voice_id": "茉莉", "voice_style": "温柔"},
    ]}
    _sv_sents, _sv_segs = build_parts(seg_voice_src)
    check("segment voice_id/voice_style propagated",
          _sv_segs[0].get("voice_id") == "茉莉"
          and _sv_segs[0].get("voice_style") == "温柔", _sv_segs[0])

    good_manifest = {
        "sentences": [{"index": 0, "text": "好。", "start_time": 0.0,
                       "duration": 1.0}],
        "total_duration": 1.0,
    }
    check("manifest valid", validate_timing_manifest(good_manifest) is good_manifest)
    for bad in [{"sentences": [], "total_duration": 1.0},
                {"sentences": [{"index": 0}], "total_duration": 1.0},
                {"sentences": [{"index": 0, "text": "好。", "start_time": 0.0,
                                "duration": 1.0}]},
                {"sentences": good_manifest["sentences"],
                 "total_duration": 1.0, "flow": "false"}]:
        try:
            validate_timing_manifest(bad)
            check("manifest rejects invalid", False, repr(bad))
        except ValueError:
            check("manifest rejects invalid", True)
    try:
        validate_images_json({"news1": 123})
        check("images json rejects non-string", False)
    except ValueError:
        check("images json rejects non-string", True)

    # 10. render_watch.resolve_command
    print("\n[9] render_watch.resolve_command")
    from render_watch import resolve_command
    r = resolve_command(["node", "--version"])
    check("resolve node executable",
          os.path.basename(r[0]).lower().startswith("node"), r[0])
    if os.name == "nt":
        r2 = resolve_command(["npx", "hyperframes", "--version"])
        # 首选形态：解析 npx.shim 直调 node + npx-cli.js（绕开 cmd.exe 的
        # 引号二次解析——历史教训：/d /s /c + 外层引号被 Popen list2cmdline
        # 二次转义成 \" 开头，cmd 不剥引号、整串被当命令名，渲染全挂）
        _npx_direct = os.path.isfile(os.path.join(
            os.path.dirname(os.path.abspath(shutil.which("npx") or
                                            shutil.which("npx.cmd") or "")),
            "node_modules", "npm", "bin", "npx-cli.js"))
        if _npx_direct:
            check("resolve npx via node+npx-cli.js direct spawn",
                  r2[0].lower().endswith("node.exe")
                  and r2[1].lower().endswith("npx-cli.js")
                  and r2[2:] == ["hyperframes", "--version"], r2)
        else:
            check("resolve npx via comspec fallback",
                  os.path.basename(r2[0]).lower() in ("cmd.exe", "cmd")
                  and r2[1] == "/c", r2)

    # 11. search_images deterministic relevance (no LLM)
    print("\n[10] search_images deterministic relevance")
    from search_images import relevance_score
    check("relevance high on matching snippet",
          relevance_score("OpenAI 发布新一代推理模型",
                          "openai新一代模型o4发布强化学习") >= 60)
    check("relevance low on unrelated snippet",
          relevance_score("国产大模型集体降价", "今日天气晴转多云") < 40)
    check("relevance zero on empty title", relevance_score("", "任意摘要") == 0)


    # extract_titles_from_segments_source：直接从 segments_source.json 取标题
    # 的路径（不经过 timing_manifest.json），是 run.py 能把第 3 步 TTS 和
    # 第 4 步配图并行跑的关键——必须跟 build_from_structured.py 实际分配的
    # id 完全一致，否则并行跑出来的 images.json 里的 key 会跟 gen_hyperframes.py
    # 从 manifest 读到的 segment id 对不上。
    from _titles import extract_titles_from_segments_source
    from build_from_structured import build_parts
    import tempfile as _tempfile

    titles_src = {
        "opening": "开场白。", "closing": "结尾语。",
        "speakers": {"甲": {"voice_id": "冰糖"}, "乙": {"voice_id": "苏打"}},
        "segments": [
            {"title": "第一条新闻", "text": "内容一。"},
            {"title": "双人对话段落", "dialogue": [
                {"speaker": "甲", "text": "你怎么看？"},
                {"speaker": "乙", "text": "我觉得不错。"},
            ]},
            {"title": "第三条新闻", "text": "内容三。"},
        ],
    }
    with _tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                       encoding="utf-8") as f:
        json.dump(titles_src, f, ensure_ascii=False)
        titles_src_path = f.name
    try:
        direct = extract_titles_from_segments_source(titles_src_path)
        _, seg_config = build_parts(titles_src)
        real = [{"id": s["id"], "title": s["title"]} for s in seg_config
                if s["id"].startswith(("news", "seg"))]
        check("extract_titles_from_segments_source id/title 与 "
              "build_from_structured 实际分配的完全一致（含 dialogue 段落）",
              direct == real, f"{direct} vs {real}")
    finally:
        os.unlink(titles_src_path)

    # 12. budget（时长预算：估算函数）
    print("\n[12] budget")
    import tempfile
    from budget import cmd_estimate
    from _contracts import estimate_sentence_seconds

    check("estimate_sentence_seconds scales with length",
          estimate_sentence_seconds("一二三四五六七八九十", 5.0, 1.0) == 2.0)
    check("estimate_sentence_seconds scales with speed",
          estimate_sentence_seconds("一二三四五", 5.0, 2.0) == 0.5)

    budget_src = {
        "opening": "大家好，欢迎收看。",
        "closing": "感谢收看。",
        "segments": [{"title": "A", "text": "第一条内容。第二句话。"}],
    }
    with tempfile.TemporaryDirectory() as td:
        src_path = os.path.join(td, "src.json")
        with open(src_path, "w", encoding="utf-8") as f:
            json.dump(budget_src, f, ensure_ascii=False)
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            class _Args:
                input = src_path
                chars_per_sec = 4.3
                speed = 1.0
                gap = 0.3
            cmd_estimate(_Args())
        finally:
            sys.stdout = old_stdout
        out = buf.getvalue()
        check("budget estimate prints result", "[estimate]" in out, out)
        check("budget estimate reports sentence count",
              "共 4 句" in out, out)

    # 14. export_extras（章节时间戳 + SRT）
    print("\n[13] export_extras")
    from export_extras import build_chapters, write_chapters, write_srt, _fmt_chapter_ts, _fmt_srt_ts

    check("_fmt_chapter_ts under an hour", _fmt_chapter_ts(65) == "1:05")
    check("_fmt_chapter_ts over an hour", _fmt_chapter_ts(3725) == "1:02:05")
    check("_fmt_srt_ts format", _fmt_srt_ts(3725.123) == "01:02:05,123")

    extras_manifest = {
        "sentences": [
            {"index": 0, "text": "开场句。", "start_time": 0.0, "duration": 3.0},
            {"index": 1, "text": "对话第一句。", "start_time": 3.3, "duration": 3.0, "speaker": "主播"},
            {"index": 2, "text": "对话第二句。", "start_time": 6.6, "duration": 4.0, "speaker": "讲解"},
            {"index": 3, "text": "结尾句。", "start_time": 70.9, "duration": 2.0},
        ],
        "total_duration": 72.9,
        "segments": [
            {"id": "opening", "title": "", "sentences": [
                {"index": 0, "text": "x", "start_time": 0.0, "duration": 3.0}]},
            {"id": "news1", "title": "反向传播算法", "sentences": [
                {"index": 1, "text": "x", "start_time": 3.3, "duration": 3.0},
                {"index": 2, "text": "x", "start_time": 6.6, "duration": 4.0}]},
            {"id": "closing", "title": "", "sentences": [
                {"index": 3, "text": "x", "start_time": 70.9, "duration": 2.0}]},
        ],
    }
    chapters = build_chapters(extras_manifest)
    check("build_chapters count", len(chapters) == 3, chapters)
    check("build_chapters uses title / id fallback",
          chapters[0][1] == "开场" and chapters[1][1] == "反向传播算法" and chapters[2][1] == "结尾",
          chapters)
    check("build_chapters start times",
          chapters[0][0] == 0.0 and chapters[1][0] == 3.3 and chapters[2][0] == 70.9, chapters)
    check("build_chapters no segments -> empty",
          build_chapters({"sentences": []}) == [])

    with tempfile.TemporaryDirectory() as td:
        ch_path = os.path.join(td, "chapters.txt")
        srt_path = os.path.join(td, "captions.srt")
        write_chapters(extras_manifest, ch_path)
        write_srt(extras_manifest, srt_path)
        ch_text = open(ch_path, encoding="utf-8").read()
        srt_text = open(srt_path, encoding="utf-8").read()
        check("chapters.txt content", "0:00 开场" in ch_text and "0:03 反向传播算法" in ch_text, ch_text)
        check("captions.srt has speaker prefix", "[主播] 对话第一句。" in srt_text, srt_text)
        check("captions.srt timestamp format", "00:00:00,000 --> 00:00:03,000" in srt_text, srt_text)
        # 长句 SRT 拆条与视频同规则（每条最多两行、时间按占比切分）
        long3 = "，".join(f"第{i}个要点需要展开说明" for i in range(1, 25)) + "。"
        srt2 = os.path.join(td, "captions2.srt")
        write_srt({"sentences": [{"index": 0, "text": long3,
                                  "start_time": 1.0, "duration": 8.0}],
                   "total_duration": 9.5}, srt2)
        srt2_text = open(srt2, encoding="utf-8").read()
        _blocks = [b for b in srt2_text.split("\n\n") if b.strip()]
        check("srt split: long sentence yields multiple entries",
              len(_blocks) >= 2, srt2_text)
        check("srt split: every entry has at most 2 text lines",
              all(len(b.strip().split("\n")) <= 4 for b in _blocks), srt2_text)

    # 15. gen_charts（配图方式 C：图表；无 matplotlib 时跳过）
    print("\n[14] gen_charts")
    try:
        import matplotlib  # noqa: F401
        HAS_MPL = True
    except ImportError:
        HAS_MPL = False
    if not HAS_MPL:
        print("  [SKIP] 未安装 matplotlib，跳过 gen_charts.py 测试"
              "（图表配图是可选功能，不影响核心流程）")
    else:
        from gen_charts import render_chart
        from matplotlib.image import imread

        for bad in [
            {"id": "x", "type": "bar", "labels": [], "values": []},
            {"id": "x", "type": "bar", "labels": ["a", "b"], "values": [1]},
        ]:
            try:
                render_chart(bad, "/dev/null")
                check("gen_charts rejects mismatched labels/values", False, repr(bad))
            except ValueError:
                check("gen_charts rejects mismatched labels/values", True)

        try:
            render_chart({"id": "x", "type": "pie", "labels": ["a"], "values": [0]}, "/dev/null")
            check("gen_charts rejects all-zero pie values", False)
        except ValueError as e:
            check("gen_charts rejects all-zero pie values", "总和必须大于 0" in str(e))

        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "test.png")
            for chart_type, labels, values in [
                ("bar", ["Q1", "Q2"], [12.3, 15.8]),
                ("line", ["1月", "2月", "3月"], [3.1, 3.8, 4.5]),
                ("pie", ["个人", "企业"], [55, 45]),
            ]:
                render_chart({"id": "x", "type": chart_type, "title": "标题",
                             "labels": labels, "values": values}, out_path)
                check(f"gen_charts renders {chart_type} PNG",
                      os.path.exists(out_path) and os.path.getsize(out_path) > 1000,
                      os.path.getsize(out_path) if os.path.exists(out_path) else "missing")
                # 画布比例断言（方式 C 输出 4:3，与方式 B 的
                # landscape_4_3 同一约定——横屏 860×700 槽填充率 92%）
                h_px, w_px = imread(out_path).shape[:2]
                check(f"gen_charts {chart_type} canvas is 4:3 (1200×900)",
                      (w_px, h_px) == (1200, 900), f"{w_px}×{h_px}")
                os.remove(out_path)

            # formula 类型：公式渲染成 SVG（mathtext）
            try:
                render_chart({"id": "x", "type": "formula"},
                             os.path.join(td, "f.svg"))
                check("gen_charts rejects empty formula", False)
            except ValueError as e:
                check("gen_charts rejects empty formula", "formula" in str(e))
            svg_path = os.path.join(td, "formula.svg")
            render_chart({"id": "x", "type": "formula", "formula": "E = mc^2",
                          "title": "质能方程"}, svg_path)
            check("gen_charts renders formula SVG",
                  os.path.exists(svg_path) and os.path.getsize(svg_path) > 500,
                  os.path.getsize(svg_path) if os.path.exists(svg_path) else "missing")
            os.remove(svg_path)

            # curve 类型：函数曲线（表达式求值 + 多曲线对比）
            try:
                render_chart({"id": "x", "type": "curve"},
                             os.path.join(td, "c.png"))
                check("gen_charts rejects empty curves", False)
            except ValueError as e:
                check("gen_charts rejects empty curves", "curves" in str(e))
            try:
                render_chart({"id": "x", "type": "curve",
                              "curves": [{"expr": "x"}],
                              "x_min": 5, "x_max": 5},
                             os.path.join(td, "c.png"))
                check("gen_charts rejects x_max <= x_min", False)
            except ValueError as e:
                check("gen_charts rejects x_max <= x_min", "x_max" in str(e))
            try:
                render_chart({"id": "x", "type": "curve",
                              "curves": [{"expr": "log(x)"}],
                              "x_min": -2, "x_max": -1},
                             os.path.join(td, "c.png"))
                check("gen_charts rejects all-NaN curve (domain error)", False)
            except ValueError as e:
                check("gen_charts rejects all-NaN curve (domain error)",
                      "无效值" in str(e))
            curve_path = os.path.join(td, "curve.png")
            render_chart({"id": "x", "type": "curve", "title": "单利 vs 复利",
                          "curves": [{"expr": "10*1.08**x", "label": "复利"},
                                     {"expr": "10+0.8*x", "label": "单利"}],
                          "x_min": 0, "x_max": 30, "x_label": "年"}, curve_path)
            check("gen_charts renders 2-curve PNG",
                  os.path.exists(curve_path) and os.path.getsize(curve_path) > 1000,
                  os.path.getsize(curve_path) if os.path.exists(curve_path) else "missing")
            ch_px, cw_px = imread(curve_path).shape[:2]
            check("gen_charts curve canvas is 4:3 (1200×900)",
                  (cw_px, ch_px) == (1200, 900), f"{cw_px}×{ch_px}")
            os.remove(curve_path)

    # 16. split_series / gen_cover / budget calibrate
    # 这四个都是纯离线逻辑（不依赖真实 TTS/渲染 API），加进来补上上次
    # review 发现的一个真实教训：--on-fail silence 的 resume 标记丢失 bug
    # 是手工构造场景才抓到的，如果当时就有这层覆盖，本该在第一版就被拦住。
    print("\n[15] split_series / gen_cover / budget calibrate")
    import tempfile as _tf16

    # 16b. split_series.parse_sections / plan_episodes
    from split_series import parse_sections, plan_episodes

    md_doc = ("# 一\n短内容。\n\n# 二\n" + "这句话会重复很多次用来撑长这一节的估算时长。" * 8
             + "\n\n# 三\n短内容。")
    sections = parse_sections(md_doc)
    check("split_series: markdown heading split finds 3 sections",
          len(sections) == 3 and [t for t, _b in sections] == ["一", "二", "三"],
          sections)

    no_heading_doc = "第一段内容在这里。\n\n第二段内容在这里，完全不同的话题。"
    sections2 = parse_sections(no_heading_doc)
    check("split_series: no-heading doc falls back to blank-line blocks",
          len(sections2) == 2, sections2)

    try:
        parse_sections("   \n\n  ")
        check("split_series: blank doc raises ValueError", False)
    except ValueError:
        check("split_series: blank doc raises ValueError", True)

    episodes = plan_episodes(sections, target_seconds=8.0)
    # 核心不变量：只在小节边界切，每个原始小节必须完整出现在恰好一集里，
    # 不允许被拆开、也不允许丢失或重复。
    flat_titles = [t for ep in episodes for t, _b, _d in ep]
    check("split_series: target_seconds mode never splits a section",
          flat_titles == ["一", "二", "三"], flat_titles)
    check("split_series: long section 二 gets its own episode",
          any(len(ep) == 1 and ep[0][0] == "二" for ep in episodes), episodes)

    episodes_n = plan_episodes(sections, n_episodes=2)
    check("split_series: n_episodes mode respects requested count",
          len(episodes_n) == 2, len(episodes_n))
    flat_titles_n = [t for ep in episodes_n for t, _b, _d in ep]
    check("split_series: n_episodes mode also never splits a section",
          sorted(flat_titles_n) == sorted(["一", "二", "三"]), flat_titles_n)

    # 16c. gen_cover.build_title_candidates / _first_sentence / _hex_to_rgb
    from gen_cover import build_title_candidates, _first_sentence, _hex_to_rgb, \
        _gradient_endpoints

    src = {
        "opening": "今天带来两条重要资讯。",
        "segments": [
            {"title": "标题A", "text": "正文A"},
            {"title": "标题B", "text": "正文B"},
        ],
    }
    cands = build_title_candidates(src)
    check("gen_cover: first candidate is first segment title",
          cands and cands[0] == "标题A", cands)
    check("gen_cover: candidates are deduplicated and capped at 3",
          len(cands) <= 3 and len(cands) == len(set(cands)), cands)
    check("gen_cover: empty segments -> no candidates",
          build_title_candidates({"segments": []}) == [])
    check("gen_cover: _first_sentence stops at terminal punctuation",
          _first_sentence("第一句。第二句。") == "第一句。")
    check("gen_cover: _hex_to_rgb parses 3-digit and 6-digit hex",
          _hex_to_rgb("#fff") == (255, 255, 255) and _hex_to_rgb("#ff0000") == (255, 0, 0))
    check("gen_cover: _gradient_endpoints extracts first/last hex",
          _gradient_endpoints("linear-gradient(135deg,#000000 0%,#ffffff 100%)")
          == ((0, 0, 0), (255, 255, 255)))
    check("gen_cover: _gradient_endpoints falls back on no hex found",
          _gradient_endpoints("none") == ((20, 20, 20), (40, 40, 40)))

    # 16g. split_series: _series_meta 只在多集时附加，且 prev/next 链接正确
    with tempfile.TemporaryDirectory() as td:
        from split_series import write_skeleton
        ep_a = [("集A", "内容A", 5.0)]
        ep_b = [("集B", "内容B", 5.0)]
        out_a = os.path.join(td, "s1.json")
        out_b = os.path.join(td, "s2.json")
        write_skeleton(ep_a, out_a, episode_index=1, total_episodes=2,
                       prev_title=None, next_title="集B")
        write_skeleton(ep_b, out_b, episode_index=2, total_episodes=2,
                       prev_title="集A", next_title=None)
        with open(out_a, encoding="utf-8") as f:
            meta_a = json.load(f)["_series_meta"]
        with open(out_b, encoding="utf-8") as f:
            meta_b = json.load(f)["_series_meta"]
        check("split_series: episode 1 has no prev, links to next",
              meta_a["prev_episode_title"] is None and meta_a["next_episode_title"] == "集B",
              meta_a)
        check("split_series: episode 2 links back to prev, no next",
              meta_b["prev_episode_title"] == "集A" and meta_b["next_episode_title"] is None,
              meta_b)

        out_single = os.path.join(td, "single.json")
        write_skeleton(ep_a, out_single, episode_index=None, total_episodes=1)
        with open(out_single, encoding="utf-8") as f:
            check("split_series: single-episode skeleton has no _series_meta",
                  "_series_meta" not in json.load(f))

    # 17. run._image_coverage：缺图拦截的判定核心（回归防护——若外层多套
    # 一个 has_images 条件，首跑时 images.json 还不存在，拦截会失效、缺图
    # 直接漏进渲染。这里直接测函数本身的契约：images.json 不存在时
    # 全部 news/seg 段落都算 missing，而不是被跳过。）
    from run import _image_coverage

    with tempfile.TemporaryDirectory() as td:
        # fixture 走 load_timing_manifest 契约校验，必须长得像 pipeline 真
        # 产出的 manifest（total_duration + 每段非空 sentences + 句子四字段
        # 齐全）。以前只有 sentences/segments 两个字段时裸 json.load 能过，
        # 换成契约加载器就炸——那是 fixture 不像真产物，不是被测代码的问题。
        def _sent(i, text):
            return {"index": i, "text": text, "start_time": float(i),
                    "duration": 1.0}

        manifest_path = os.path.join(td, "timing_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({
                "total_duration": 5.0,
                "sentences": [_sent(i, f"第{i}句。") for i in range(5)],
                "segments": [
                    {"id": "opening", "title": "本期内容",
                     "sentences": [_sent(0, "开场白。")]},
                    {"id": "news1", "title": "第一条",
                     "sentences": [_sent(1, "第一条内容。")]},
                    {"id": "news2", "title": "第二条",
                     "sentences": [_sent(2, "第二条内容。")]},
                    {"id": "seg1", "title": "讲解段",
                     "sentences": [_sent(3, "讲解内容。")]},
                    {"id": "closing", "title": "小结",
                     "sentences": [_sent(4, "小结内容。")]},
                ],
            }, f, ensure_ascii=False)

        # 17a. images.json 不存在（首跑）：所有 news/seg 段落都缺，不能返回空
        images_json = os.path.join(td, "images.json")
        sids, missing = _image_coverage(manifest_path, images_json)
        check("run._image_coverage: first run (no images.json) -> all content sids missing",
              sids == ["news1", "news2", "seg1"] and missing == ["news1", "news2", "seg1"],
              (sids, missing))

        # 17b. 部分配图：只报没配上的
        with open(images_json, "w", encoding="utf-8") as f:
            json.dump({"news1": "images/news1.png"}, f, ensure_ascii=False)
        sids, missing = _image_coverage(manifest_path, images_json)
        check("run._image_coverage: partial mapping -> only uncovered sids reported",
              sids == ["news1", "news2", "seg1"] and missing == ["news2", "seg1"],
              (sids, missing))

        # 17c. 全部配齐：missing 为空（opening/closing 不参与配图统计）
        with open(images_json, "w", encoding="utf-8") as f:
            json.dump({"news1": "images/news1.png", "news2": "images/news2.png",
                       "seg1": "images/seg1.png"}, f, ensure_ascii=False)
        sids, missing = _image_coverage(manifest_path, images_json)
        check("run._image_coverage: full coverage -> no missing, opening/closing excluded",
              sids == ["news1", "news2", "seg1"] and missing == [], (sids, missing))

        # 17d. manifest 没有 segments 字段：无配图义务
        bare_path = os.path.join(td, "bare_manifest.json")
        with open(bare_path, "w", encoding="utf-8") as f:
            json.dump({"total_duration": 1.0, "sentences": [
                {"index": 0, "text": "一句话。", "start_time": 0.0,
                 "duration": 1.0}]}, f, ensure_ascii=False)
        sids, missing = _image_coverage(bare_path, images_json)
        check("run._image_coverage: manifest without segments -> no sids, no missing",
              sids == [] and missing == [], (sids, missing))

    # 17e. gen_hyperframes 缺图覆盖率提示（分步执行时的唯一缺图防线）
    from gen_hyperframes import _uncovered_content_sids
    _cov_manifest = {"segments": [
        {"id": "opening", "title": "开场"},
        {"id": "news1", "title": "第一条"},
        {"id": "seg1", "title": "讲解段"},
        {"id": "closing", "title": "小结"},
    ]}
    _cov_partial = _uncovered_content_sids(_cov_manifest, {"news1": "images/news1.png"})
    check("gen_hyperframes._uncovered: partial mapping -> only uncovered content sids",
          _cov_partial == ["seg1"], _cov_partial)
    _cov_full = _uncovered_content_sids(
        _cov_manifest, {"news1": "images/news1.png", "seg1": "images/seg1.png"})
    check("gen_hyperframes._uncovered: full coverage -> empty (opening/closing excluded)",
          _cov_full == [], _cov_full)
    _cov_none = _uncovered_content_sids({"sentences": []}, {"news1": "x.png"})
    check("gen_hyperframes._uncovered: manifest without segments -> empty",
          _cov_none == [], _cov_none)

    # 17f. search_images --sids 路由分组过滤（只搜走方式 A 的段落）
    from search_images import _filter_title_items
    _ti = [{"id": "seg1", "title": "a"}, {"id": "seg2", "title": "b"},
           {"id": "seg3", "title": "c"}]
    _f1, _u1 = _filter_title_items(_ti, "seg1,seg3")
    check("search_images._filter: keeps only listed sids in order",
          [x["id"] for x in _f1] == ["seg1", "seg3"] and _u1 == [], (_f1, _u1))
    _f2, _u2 = _filter_title_items(_ti, "seg2, oops ")
    check("search_images._filter: unknown sid reported (typo guard)",
          [x["id"] for x in _f2] == ["seg2"] and _u2 == ["oops"], (_f2, _u2))
    _f3, _u3 = _filter_title_items(_ti, None)
    check("search_images._filter: no sids -> no filtering",
          _f3 == _ti and _u3 == [], (_f3, _u3))

    # ── 17f. 外部 review 修复的回归防线 ──────────────────────────────
    # 这组断言固化 2026-09 那轮深度 review 修掉的缺陷。每条都对应一个
    # 真实故障场景，不是"看着该有"的形式化检查。
    print("\n[17f] review-fix regression guards")
    _skill_root17 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _src(name):
        with open(os.path.join(_skill_root17, "scripts", name),
                  encoding="utf-8") as _f:
            return _f.read()

    _run_src = _src("run.py")

    # ① SKILL.md 曾把 --loudness 写在 run.py 参数表里，但 argparse 里根本
    #   没有——照文档敲命令直接 unrecognized arguments。
    check("run.py 实现了文档承诺的 --loudness",
          '"--loudness"' in _run_src and "args.loudness is not None" in _run_src)

    # ② check 步骤是裸 npx hyperframes（不经 render_watch 的 --max-wait），
    #   Chrome 卡在 "Timed out closing browser" 时会永久阻塞且无日志。
    check("check 步骤带墙钟上限（Chrome 挂死兜底）",
          "timeout=CHECK_TIMEOUT" in _run_src
          and "subprocess.TimeoutExpired" in _run_src)

    # ③ TTS/配图并行阶段曾有 5Hz 无限轮询：子进程卡住时 run.py 既不退出
    #   也不报错，终端看着"还在跑"。
    check("并行阶段有整体 deadline",
          "PARALLEL_TIMEOUT" in _run_src and "deadline = time.time()" in _run_src)

    # ④ 被信号终止时 returncode 是负数，sys.exit(-15) 在 shell 显示成 241。
    check("子进程退出码做了正数规范化",
          "_norm_rc(fail_rc)" in _run_src)

    # ⑤ --aspect both 期间被强杀会留下 *.html.disabled，下次运行直接 exit 1
    #   且报错不提真正原因。
    check("--aspect both 会自愈上次中断残留的 .disabled",
          ".disabled" in _run_src and "已恢复上次中断残留的隔离文件" in _run_src)

    # ⑥ 关键产物被打断写一半 → 截断 JSON。下游要么崩 traceback，要么更糟：
    #   当有效缓存用。
    check("production_report / render_cache 走原子写",
          "_write_json_atomic" in _run_src)
    _pipe_src = _src("pipeline.py")
    check("timing_manifest.json 走原子写（tmp + os.replace）",
          'manifest_path + ".tmp"' in _pipe_src and "os.replace(_tmp" in _pipe_src)
    check("index.html 走原子写（tmp + os.replace）",
          'output_path + ".tmp"' in _src("gen_hyperframes.py"))

    # ⑦ 段落全部句子 TTS 失败时写出空 sentences，会被 _contracts 直接拒收
    #   ——一次失败就让整条视频出不来。
    check("pipeline 剔除无音频的空段落而不是写出坏 manifest",
          "dropped.append(_seg_id)" in _pipe_src
          and "已从 manifest 剔除" in _pipe_src)

    # ⑧ 首句豁免行数预算：一句 300 字把预算打成负数、挤掉后续句子并溢出。
    _bfs_src = _src("build_from_structured.py")
    check("自动 body 的首句不再豁免行数预算",
          "if not kept and budget > 0:" in _bfs_src
          and "if kept and need > budget" not in _bfs_src)

    # ⑨ export_extras 曾写死横屏切分参数，竖屏项目导出的 SRT 与片内字幕
    #   切行不一致（本文件承诺"逐条对齐"）。
    _exp_src17 = _src("export_extras.py")
    check("export_extras 按画幅/字幕模式取切分参数",
          '"--aspect"' in _exp_src17 and "aspect=args.aspect" in _exp_src17
          and "subtitle_params_for(" in _exp_src17
          and 'split_subtitle_cues(s["text"])' not in _exp_src17)

    # ⑩ "哪些段落需要配图" 的 news/seg 前缀约定曾散落在 5 处、3 种写法
    #   （startswith(元组) / 两个 or 串联 / 注释里复述），改命名规则要动 5
    #   个地方且极易漏一处——漏的那处会静默改变配图覆盖率统计口径（把不该
    #   算的算进去，或该配图的段落被跳过拦截）。已收口到 _contracts。
    from _contracts import is_content_sid, CONTENT_SID_PREFIXES
    check("is_content_sid 语义正确（内容段 vs 结构性段落）",
          is_content_sid("news1") and is_content_sid("seg3")
          and not is_content_sid("opening") and not is_content_sid("closing")
          and not is_content_sid("") and not is_content_sid(None),
          CONTENT_SID_PREFIXES)
    _hardcoded = []
    for _n in ("gen_hyperframes.py", "pipeline.py", "run.py", "_titles.py"):
        _s = _src(_n)
        for _pat in ('startswith(("news"', 'startswith("news") or',
                     'startswith("news",'):
            if _pat in _s:
                _hardcoded.append(f"{_n}:{_pat}")
    check("news/seg 前缀判定不再硬编码散落（统一走 is_content_sid）",
          not _hardcoded, _hardcoded)

    # ── 18. 防回归断言（源码级固化）────────────────────────
    print("\n[18] anti-regression")
    _skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _read_src(rel):
        with open(os.path.join(_skill_root, rel), encoding="utf-8") as f:
            return f.read()

    import re as _re

    # 18a. 默认值单一来源：DEFAULT_SPEED / OPENING_CLOSING_DEFAULT_SPEED
    _contracts_src = _read_src("scripts/_contracts.py")
    check("DEFAULT_SPEED defined in _contracts", "DEFAULT_SPEED = 1.5" in _contracts_src)
    check("OPENING_CLOSING_DEFAULT_SPEED defined in _contracts",
          "OPENING_CLOSING_DEFAULT_SPEED = 1.2" in _contracts_src)
    for _f in ("scripts/pipeline.py", "scripts/budget.py", "scripts/split_series.py"):
        _s = _read_src(_f)
        _base = os.path.basename(_f)
        check(f"{_base} uses DEFAULT_SPEED", "DEFAULT_SPEED" in _s)
        check(f"{_base} has no speed default=1.0 drift",
              _re.search(r'"--speed"[^)]*default=1\.0', _s, _re.S) is None)
    check("build_from_structured uses OPENING_CLOSING_DEFAULT_SPEED",
          "OPENING_CLOSING_DEFAULT_SPEED" in _read_src("scripts/build_from_structured.py"))

    # 18b. 模板字段消费完整性（layout 叶子字段必须被 gen_hyperframes 引用，
    # 防 chip.padding/numSize 这类"改模板不生效"的死字段复发）
    with open(os.path.join(_skill_root, "config/template.json"),
              encoding="utf-8") as f:
        _tpl = json.load(f)
    _gh_src = _read_src("scripts/gen_hyperframes.py")
    _lay_keys = set()
    for _aspect in ("landscape", "vertical"):
        for _blk_cfg in _tpl["layout"][_aspect].values():
            _lay_keys.update(_blk_cfg.keys())
    _dead = [k for k in sorted(_lay_keys) if ('"%s"' % k) not in _gh_src]
    check("template layout keys all consumed by gen_hyperframes",
          not _dead, f"dead keys: {_dead}")
    # agenda 字号收缩刻度必须有序（shrinkThreshold <= shrinkMax，
    # 相等 = 到达 shrinkMax 前不收缩，如竖屏模板 8/8），
    # 否则线性插值区间为负、字号语义失效
    _sm_mono = all(
        _tpl["layout"][a]["agenda"]["shrinkThreshold"]
        <= _tpl["layout"][a]["agenda"]["shrinkMax"]
        for a in ("landscape", "vertical"))
    check("template agenda shrinkThreshold <= shrinkMax", _sm_mono)

    # 18c. images.json 的 autoplay 必须被 gen_hyperframes 消费（防死字段复发）
    import tempfile as _tf
    import gen_hyperframes as _gh
    with _tf.TemporaryDirectory() as _td:
        os.makedirs(os.path.join(_td, "images"))
        for _n in ("a.mp4", "b.mp4"):
            with open(os.path.join(_td, "images", _n), "wb") as f:
                f.write(b"\x00" * 2048)
        with open(os.path.join(_td, "audio.wav"), "wb") as f:
            f.write(b"\x00" * 1024)
        _mani = {
            "total_duration": 2.0,
            "sentences": [
                {"index": 0, "text": "第一句。", "start_time": 0.0, "duration": 1.0},
                {"index": 1, "text": "第二句。", "start_time": 1.0, "duration": 1.0},
            ],
            "segments": [
                {"id": "seg1", "title": "A", "tagline": "t1", "accent": "#4fc3f7",
                 "start": 0, "end": 1,
                 "sentences": [{"index": 0, "text": "第一句。",
                                "start_time": 0.0, "duration": 1.0}]},
                {"id": "seg2", "title": "B", "tagline": "t2", "accent": "#81c784",
                 "start": 1, "end": 2,
                 "sentences": [{"index": 1, "text": "第二句。",
                                "start_time": 1.0, "duration": 1.0}]},
            ],
        }
        _imgs = {"seg1": {"src": "images/a.mp4"},
                 "seg2": {"src": "images/b.mp4", "autoplay": False}}
        _html = _gh.generate_html(_mani, "audio.wav", images=_imgs)
        _v1 = _re.search(r'<video id="vid-seg1"[^>]*>', _html)
        _v2 = _re.search(r'<video id="vid-seg2"[^>]*>', _html)
        check("video tags rendered for both segments",
              _v1 is not None and _v2 is not None)
        if _v1 and _v2:
            check("video autoplay defaults to true", "autoplay" in _v1.group(0))
            check("video autoplay:false is honored", "autoplay" not in _v2.group(0))

    # ── 19. WCAG 对比度全组合断言─────────────────────────
    # tagline 色（浅色主题 _darken(accent) / 深色主题 _mix(accent,white,0.62)）
    # 对全调色板 × 全主题背景渐变最坏段的对比度。tagline 字号横竖屏
    # 44/36px（≥24px 属 WCAG 大字），浅色按大字 AA 3.0 卡；深色提亮后
    # 余量大（实测 10+）按正文 AA 4.5 卡。调色板/主题/混色函数任何一处
    # 改动，这里自动重新全组合验证。
    print("\n[19] WCAG contrast (tagline x palette x themes)")
    from _theme import (darken as _darken, hex_to_rgb01 as _hex_to_rgb01,
                        relative_luminance as _relative_luminance,
                        mix as _mix)
    from _theme import get_theme_colors, list_theme_names

    def _contrast_ratio(c1, c2):
        l1 = _relative_luminance(c1)
        l2 = _relative_luminance(c2)
        if l1 is None or l2 is None:
            return None
        hi, lo = max(l1, l2), min(l1, l2)
        return (hi + 0.05) / (lo + 0.05)

    _hex6 = _re.compile(r"#[0-9a-fA-F]{6}")
    # 3 位缩写展开（#fff == #ffffff）：dark 主题 text_color 就是 "#fff"，
    # 不展开深浅判断静默失效（否则 dark 的 tagline 会走压暗分支、对比度掉到 2.1）
    check("_hex_to_rgb01 expands 3-digit hex",
          _hex_to_rgb01("#fff") == (1.0, 1.0, 1.0)
          and _hex_to_rgb01("#4fc3f7") == (79 / 255, 195 / 255, 247 / 255))
    check("_hex_to_rgb01 rejects garbage",
          _hex_to_rgb01("#ff") is None
          and _hex_to_rgb01("not-a-color") is None)
    _wcag_fail = 0
    for _tname in list_theme_names():
        _tc = get_theme_colors(_tname)
        _stops = _hex6.findall(_tc.get("bg_gradient", ""))
        check(f"theme '{_tname}' bg gradient has >=2 stops", len(_stops) >= 2)
        _tl = _relative_luminance(_tc.get("text_color", "#22262b"))
        _is_dark = _tl is not None and _tl > 0.5
        _need = 4.5 if _is_dark else 3.0
        for _ac in get_accent_palette():
            _tag = (_mix(_ac, "#ffffff", 0.62) if _is_dark
                    else _darken(_ac))
            _ratios = [_contrast_ratio(_tag, s) for s in _stops]
            _ratios = [r for r in _ratios if r is not None]
            check(f"tagline {_ac} on {_tname} >= {_need}",
                  bool(_ratios) and min(_ratios) >= _need,
                  f"worst={min(_ratios):.2f}" if _ratios else "no stops")
            if not _ratios or min(_ratios) < _need:
                _wcag_fail += 1
    check("WCAG tagline all combos pass", _wcag_fail == 0)

    # ── 20. check_series.find_drifts（系列一致性纯函数）────────────
    print("\n[20] check_series.find_drifts")
    from check_series import find_drifts
    check("single episode: nothing to compare",
          find_drifts([{"dir": "a", "opening_title": "X"}]) == [])
    check("consistent series has no drifts",
          find_drifts([
              {"dir": "a", "opening_title": "吃透PID", "flow": True,
               "params": {"theme": "cream", "voice_id": "冰糖", "speed": None}},
              {"dir": "b", "opening_title": "吃透PID", "flow": True,
               "params": {"theme": "cream", "voice_id": "冰糖", "speed": None}}]) == [])
    _d = find_drifts([
        {"dir": "a", "opening_title": "吃透PID", "flow": True,
         "params": {"theme": "cream", "voice_id": "冰糖"}},
        {"dir": "b", "opening_title": "自动调节", "flow": False,
         "params": {"theme": "dark", "voice_id": "苏打"}}])
    check("drift detected: opening_title", any("opening_title" in x for x in _d))
    check("drift detected: flow", any("flow" in x for x in _d))
    check("drift detected: params.theme", any("params.theme" in x for x in _d))
    check("drift detected: params.voice_id", any("params.voice_id" in x for x in _d))
    check("missing params field skipped without crash",
          find_drifts([{"dir": "a", "opening_title": "X", "params": {}},
                       {"dir": "b"}]) == [])
    check("flow None vs False normalized (both chapter mode)",
          find_drifts([{"dir": "a", "flow": None},
                       {"dir": "b", "flow": False}]) == [])

    # ── 20a. main() 内不得局部 import math（真回归）────────
    # 防回归：main() 内局部 import math 会把 math 变成局部名，导致更早
    # 执行的 gap 校验 UnboundLocalError（TTS 路径才触发，selftest 不走
    # argparse 分支）。源码级断言：main 函数体内无 import math。
    print("\n[20a] no local import math in main()")
    import ast as _ast
    with open(os.path.join(_skill_root, "scripts", "pipeline.py"),
              encoding="utf-8") as _pf:
        _tree = _ast.parse(_pf.read())
    _main_fn = next(_n for _n in _tree.body
                    if isinstance(_n, _ast.FunctionDef) and _n.name == "main")
    _local_math_imports = [
        _n for _n in _ast.walk(_main_fn)
        if isinstance(_n, _ast.Import)
        and any(_a.name == "math" for _a in _n.names)]
    check("pipeline.main() has no local 'import math'",
          not _local_math_imports)

    # ── 20b. 竖屏 cue 行宽必须按画幅收紧（cap22/slack1.0）───────────────
    # 竖屏 verse 物理行宽 ≈22 字（980px/40px）。若调用点回退到横屏默认
    # （28 字 + slack1.5 → 最长行 42 字），行数据会超物理宽度。
    # 以前这条断言直接 grep 源码里的 `_sub_cap = 22 if aspect == "vertical"
    # else 28` 字符串——那是"断言实现写法"，不是"断言行为"：把数值收口到
    # 一个共用函数（等价重构）也会红，反而阻碍正常的去重。改成行为断言：
    # 直接问共用函数要参数，再校验两个调用点确实接了这份参数。
    from _script_utils import subtitle_params_for as _sub_params
    # 模式已按画幅绑定，切分参数只按画幅区分
    _vp = _sub_params("vertical")
    _lp = _sub_params("landscape")
    check("vertical subtitle uses aspect-strict cap (22/1.0)",
          (_vp["max_chars"], _vp["slack"], _vp["hard_cap"]) == (22, 1.0, 22)
          and (_lp["max_chars"], _lp["slack"], _lp["hard_cap"]) == (28, 1.5, 34),
          (_vp, _lp))

    # 两个消费者必须共用同一份参数，否则"导出的 SRT 与片内字幕逐条对齐"
    # 这个承诺会悄悄失效（竖屏/verse 下切行不同）。这是参数收口的回归
    # 防线——比逐行 grep 数值更抗重构。
    with open(os.path.join(_skill_root, "scripts", "gen_hyperframes.py"),
              encoding="utf-8") as _gf:
        _gen_src = _gf.read()
    with open(os.path.join(_skill_root, "scripts", "export_extras.py"),
              encoding="utf-8") as _ef:
        _exp_src = _ef.read()
    check("gen_hyperframes 与 export_extras 都从 subtitle_params_for 取参数",
          "subtitle_params_for(" in _gen_src
          and "subtitle_params_for(" in _exp_src
          and "split_subtitle_cues(" in _exp_src,
          (("subtitle_params_for(" in _gen_src),
           ("subtitle_params_for(" in _exp_src)))
    check("export_extras 已不依赖 split_subtitle_cues 的横屏默认值",
          "split_subtitle_cues(s[\"text\"])" not in _exp_src)

    # ── 20c. 竖屏 verse 句子流结构与滚动参照系 ───────────────
    # verse-clip 无定位时 verse-line.offsetTop 相对
    # seg-card（≈1500px），滚动公式恒被钳到段落尾部——第一句永远在
    # 窗口外。断言 clip 定位存在 + verse 钉底（所有段落上边界齐平，
    # 标题行数差异由图片弹性高度吸收）+ 开场/收尾标题垂直居中。
    import gen_hyperframes as _gh2
    # 竖屏几何断言全部从模板推导（与 gen_hyperframes 同一来源）——
    # 改 template.json 不该再牵动本文件
    _tpl_v = _tpl["layout"]["vertical"]
    _v_pad_t = _tpl_v["segCard"]["padding"]
    _v_ar_t = _tpl_v["image"]["aspect"]
    _v_mt_t = _tpl_v["image"]["marginTop"]
    _v_vh_t = _tpl_v["verse"]["windowHeight"]
    _v_vc_t = _tpl_v["verse"]["clipPad"]
    _vman = {
        "total_duration": 12.0,
        "sentences": [
            {"index": 0, "text": "开场第一句。", "start_time": 0.0, "duration": 3.0},
            {"index": 1, "text": "内容第一句，比较长会折行的那种句子。", "start_time": 3.0, "duration": 4.0},
            {"index": 2, "text": "内容第二句。", "start_time": 7.0, "duration": 3.0},
        ],
        "segments": [
            {"id": "opening", "title": "开场", "tagline": "", "body": "",
             "sentences": [{"index": 0, "text": "开场第一句。",
                            "start_time": 0.0, "duration": 3.0}]},
            {"id": "seg1", "title": "内容", "tagline": "", "body": "",
             "sentences": [
                 {"index": 1, "text": "内容第一句，比较长会折行的那种句子。",
                  "start_time": 3.0, "duration": 4.0},
                 {"index": 2, "text": "内容第二句。",
                  "start_time": 7.0, "duration": 3.0}]},
        ],
    }
    # 竖屏 = portrait（3:4，1080x1440，紧凑留白 + 图片槽 4:3）；
    # 9:16 非紧凑画幅已从产品形态移除（画幅只留 16:9 横屏 + 3:4 竖屏）
    _vhtml = _gh2.generate_html(_vman, "audio/combined.wav",
                                width=1080, height=1440, aspect="portrait")
    check("vertical verse: clip positioned (offsetTop base for scroll)",
          f'.verse-clip{{position:relative;padding:{_v_vc_t}px 0' in _vhtml)
    check("vertical verse: pinned to content bottom (uniform top edge)",
          '[data-aspect="vertical"] .verse{position:static!important' in _vhtml
          and 'margin-top:auto!important' in _vhtml)
    check("vertical image: template-driven slot (compact padding + "
          "aspect + margin-top)",
          f'padding:{_v_pad_t}!important' in _vhtml
          and f'height:auto!important;aspect-ratio:{_v_ar_t}!important' in _vhtml
          and f'flex:0 1 auto!important;margin:{_v_mt_t}px 0 0!important' in _vhtml)
    check("vertical verse: breathing padding + scroll anchor synced "
          "(first/last line clear of mask fade)",
          f'padding:{_v_vc_t}px 0' in _vhtml
          and f"Math.min(0, {_v_vc_t} - act.top)" in _vhtml)
    check("vertical: opening/closing title vertically centered (margin auto)",
          '#opening .seg-title-wrap,[data-aspect="vertical"] #closing .seg-title-wrap'
          '{text-align:center!important;margin:auto 0!important}' in _vhtml)
    check("vertical verse: lines carry sentence index (data-i)",
          'data-i="1"' in _vhtml and 'data-i="2"' in _vhtml)
    check("vertical verse: cues carry si for runtime highlight",
          "si:1" in _vhtml and "si:2" in _vhtml)
    # 模式固定按画幅绑定（不可传参）：横屏产物 = bar（sub-bar 存在、
    # 无 verse DOM），竖屏产物 = verse（反之）。本断言同时拦"重新暴露
    # 模式选项"的回退。
    _lhtml = _gh2.generate_html(_vman, "audio/combined.wav")
    check("aspect-bound modes: landscape=bar / portrait=verse",
          'class="sub-bar"' in _lhtml
          and ".sub-text{" in _lhtml
          and 'class="verse"' not in _lhtml
          and 'class="sub-bar"' not in _vhtml
          and 'class="verse"' in _vhtml)
    # 横屏配图版式（20c2 的图片槽断言用）
    _limhtml = _gh2.generate_html(_vman, "audio/combined.wav",
                                  images={"seg1": {"src": "images/x.jpg"}})
    # verse 替代 body 是 DOM 层不渲染（不是 CSS 隐藏）：源码级断言
    # 拦"改回 display:none 兜底"的回退（display:none 方案会让 GSAP
    # stagger 指向隐藏行、layout 检查器量到不可见卡片的 rect）
    check("verse kills body at DOM level (vertical has-image)",
          'sub_mode == "verse" and has_image' in _gen_src)

    # ── 20c2. 横屏图片框 √2:1（整屏垂直居中）────────────────────
    # 槽 910×644（左右各距屏 25px、910/√2 ≈ 644）：4:3 生图 contain
    # 后左右留边恰约 25px；槽左缘 985，与正文卡（左 50、宽 910）间
    # 距 sideGap 25px。垂直居中相对整个画面：top = 1080/2 = 540——
    # 已知取舍：两行标题（底 260）会与图上缘 218 轻微交叠，单行标题
    # 无碍；改槽宽/边距时同步重推高（=宽/√2）。
    _tl_l = _tpl["layout"]["landscape"]["title"]
    _img_l = _tpl["layout"]["landscape"]["image"]
    check("landscape image slot is √2:1 (910×644, 25px screen margins)",
          1.41 < _img_l["width"] / _img_l["height"] < 1.42
          and _img_l["right"] == 25
          and 1920 - _img_l["right"] - _img_l["width"]
              - (_tpl["layout"]["landscape"]["body"]["leftMargin"]
                 + _tpl["layout"]["landscape"]["body"]["maxWidth"])
              == _tpl["layout"]["landscape"]["body"]["sideGap"])
    check("landscape image vertically centered on full frame",
          _img_l["top"] == 1080 // 2)
    check("landscape title.topImageCenter removed (centering branch gone)",
          "topImageCenter" not in _tl_l
          and "topImageCenter" not in _gen_src)
    # 带图横屏 HTML 落位：图片槽 top/尺寸直接来自模板（整屏垂直居中），
    # 标题顶部锚定 topNews（无 translateY 居中）；右缘缩进 rightInset
    check("landscape image slot frame-centered (values from template)",
          f'top:{_img_l["top"]}px' in _limhtml
          and f'height:{_img_l["height"]}px' in _limhtml
          and f'width:{_img_l["width"]}px' in _limhtml)
    check("landscape image segment title top-anchored at topNews "
          "(no vertical centering)",
          f'left:{_tl_l["leftWithBadge"]}px;right:auto;'
          f'width:{1920 - _tl_l["leftWithBadge"] - _tl_l["rightInset"]}px;'
          f'top:{_tl_l["topNews"]}px' in _limhtml
          and "top:46%" not in _limhtml)
    # 标题行数估算保留（供正文卡动态 top 推导）；图框 √2:1 后上缘 292
    # 已在两行标题底 260 之下，"第二行伸入图片区"告警随其前提一并移除
    check("landscape title-line estimation kept for body top derivation",
          "_est_lines = 1 if _est_w <= _tw_px else 2" in _gen_src
          and "_l2_safe" not in _gen_src)

    # ── 20c3. 横屏配图段内容框独立于标题框 ─────────────────────────
    # 标题框（title-wrap）与内容框（body 卡 / verse 窗口）不用同一容器
    # 约束：body 卡是独立绝对定位盒，top = 标题组底部 + body.titleGap
    # （标题组底部按标题估算行数动态推导——单行标题不留两行标题的空隙；
    # body.top 保留为 titleGap 缺省时的回退锚点）；tagline 是标题组固定
    # 成员、必须计入推导（漏算会让 tagline 压进 body 卡，layout 检查器
    # 以 content_overlap 暴露）；左缘 = body.leftMargin，宽度右缘与图片
    # 左缘保持 body.sideGap（badge / flow 版式几何一致）。挂载点必须是
    # seg-card 直接子元素（挂 title-wrap 里会以它为定位参照系，top 被
    # 二次偏移）。
    _bd_l = _tpl["layout"]["landscape"]["body"]
    _tgl_l = _tpl["layout"]["landscape"]["tagline"]
    check("landscape body.titleGap configured (dynamic top), body.top "
          "kept as fallback anchor",
          "titleGap" in _bd_l and "leftMargin" in _bd_l
          and "sideGap" in _bd_l and _bd_l.get("top") is not None)
    check("landscape tagline has explicit lineHeight (deterministic "
          "slot height for body top derivation)",
          "lineHeight" in _tgl_l and isinstance(_tgl_l["lineHeight"],
                                                (int, float)))
    _bimg_l = 1920 - _img_l["right"] - _img_l["width"]
    _w_badge = min(_bd_l["maxWidth"],
                   _bimg_l - _bd_l["leftMargin"] - _bd_l["sideGap"])
    _w_flow = _w_badge
    # 动态 top：单行标题（两个 fixture 的标题都是单行）的标题组底部
    # + titleGap，与生成器公式一致
    _btop = int(_tl_l["topNews"]
                + _tl_l["fontSizeNewsImage"] * _tpl["typography"]["titleLineHeight"]
                + _tgl_l["marginTop"]
                + _tgl_l["fontSize"] * _tgl_l["lineHeight"]
                + _bd_l["titleGap"])
    _bleft = _bd_l["leftMargin"]
    _bman = {
        "total_duration": 9.0,
        "sentences": [
            {"index": 0, "text": "短句。", "start_time": 0.0,
             "duration": 3.0},
            {"index": 1, "text": "另起短句。", "start_time": 3.0,
             "duration": 3.0},
            {"index": 2, "text": "第三短句。", "start_time": 6.0,
             "duration": 3.0},
        ],
        "segments": [
            {"id": "seg1", "title": "短标题", "tagline": "",
             "body": "第一行要点\n第二行要点",
             "sentences": [
                 {"index": 0, "text": "短句。", "start_time": 0.0,
                  "duration": 3.0},
                 {"index": 1, "text": "另起短句。", "start_time": 3.0,
                  "duration": 3.0},
                 {"index": 2, "text": "第三短句。", "start_time": 6.0,
                  "duration": 3.0}]},
        ],
    }
    # badge 版式（无 flow 键）：body 卡 left = body.leftMargin、宽右缘
    # 距图片左缘 sideGap、top = 标题组底部 + titleGap（tagline 为空也
    # 计入 tagline 槽——它是标题组的固定成员）
    _bdh = _gh2.generate_html(_bman, "audio/combined.wav",
                              images={"seg1": {"src": "images/x.jpg"}})
    check("landscape body card detached (absolute, "
          f"left {_bleft} / top {_btop} / width {_w_badge})",
          f'style="position:absolute;left:{_bleft}px;top:{_btop}px;'
          f'width:{_w_badge}px;margin-top:0"' in _bdh)
    # DOM 独立：body 卡在 title-wrap 之外（图片元素之后、seg-card 直挂）
    check("landscape body card mounted outside title-wrap "
          "(after seg-image in DOM)",
          _bdh.find('class="seg-image"') < _bdh.find('id="body-seg1"')
          and _bdh.find('class="seg-title-wrap"')
          < _bdh.find('class="seg-image"'))
    # flow 版式（无 badge）：几何与 badge 版式一致（leftMargin 统一左缘，
    # 宽右缘 960 不压图片左缘 1010）
    _bman_flow = dict(_bman, flow=True)
    _bfh = _gh2.generate_html(_bman_flow, "audio/combined.wav",
                              images={"seg1": {"src": "images/x.jpg"}})
    check("landscape flow body card detached (left "
          f"{_bleft} / top {_btop} / width {_w_flow})",
          f'style="position:absolute;left:{_bleft}px;top:{_btop}px;'
          f'width:{_w_flow}px;margin-top:0"' in _bfh
          and 'class="badge"' not in _bfh)

    # ── 20d. bar 形态（经典形式）：横屏固定模式 ─────────────────
    _bhtml = _gh2.generate_html(_vman, "audio/combined.wav")
    check("bar mode: sub-bar DOM present, verse DOM absent",
          'class="sub-bar"' in _bhtml and 'class="verse"' not in _bhtml)
    check("bar mode: body cards rendered (no verse replacement)",
          '.sub-speaker{' in _bhtml and 'subEls' in _bhtml)

    # ── 20e. portrait 紧凑竖屏（3:4，1080x1440）─────────────────
    # portrait 在 generate_html 本体内归一化为 vertical 布局家族
    # （data-aspect="vertical"）+ v_compact 紧凑参数
    _phtml = _gh2.generate_html(_vman, "audio/combined.wav",
                                width=1080, height=1440, aspect="portrait")
    check("portrait: normalized into vertical layout family",
          'data-aspect="vertical"' in _phtml
          and 'data-width="1080" data-height="1440"' in _phtml)
    check("portrait verse: compact padding + image slot (from template)",
          f'padding:{_v_pad_t}!important' in _phtml
          and f'aspect-ratio:{_v_ar_t}!important' in _phtml)
    _bphtml = _gh2.generate_html(_vman, "audio/combined.wav",
                                 width=1080, height=1440, aspect="portrait")
    check("portrait verse: sub-bar absent (vertical family is verse-only)",
          'class="verse"' in _bphtml
          and 'class="sub-bar"' not in _bphtml)

    # ── 20f. 模式固定按画幅绑定：横屏 bar、竖屏 verse ────────────
    # 模式不是用户选项，generate_html 内部按画幅解析（上方 20c 已断言
    # 不可传参；这里补默认调用的两画幅产物形态）
    _def_l = _gh2.generate_html(_vman, "audio/combined.wav")
    check("default: landscape resolves to bar (sub-bar present, "
          "verse absent)",
          'class="sub-bar"' in _def_l and 'class="verse"' not in _def_l)
    _def_v = _gh2.generate_html(_vman, "audio/combined.wav",
                                width=1080, height=1440, aspect="portrait")
    check("default: vertical resolves to verse",
          'class="verse"' in _def_v and 'class="sub-bar"' not in _def_v)

    # ── 20c. timing_manifest segments 契约（原 20b：与竖屏字幕节重号）──
    # gen_hyperframes 对 seg["sentences"] 直接下标访问，缺失时炸裸
    # KeyError；契约层应先报对人（隐性必填，手写 fixture 易踩）
    print("\n[20c] manifest segments contract")
    from _contracts import validate_timing_manifest as _vtm
    _ok_base = {"total_duration": 10.0,
                "sentences": [{"index": 0, "text": "a。",
                               "start_time": 0.0, "duration": 10.0}]}
    try:
        _vtm({**_ok_base, "segments": [{"id": "seg1", "duration": 10.0,
                                        "sentences": _ok_base["sentences"]}]})
        check("segments with sentences accepted", True)
    except ValueError as e:
        check("segments with sentences accepted", False, str(e))
    try:
        _vtm({**_ok_base, "segments": [{"id": "seg1", "duration": 10.0}]})
        check("segment missing sentences rejected", False, "no error raised")
    except ValueError as e:
        check("segment missing sentences rejected", "sentences" in str(e), str(e))

    # ── 21. WCAG 扩展：字幕层/说话人/序号圆/正文层────────
    # [19] 只覆盖 tagline；这里把渲染 HTML 里其余"文字叠底色"色对全部
    # 锁住。字号依据：字幕 46/40px、说话人 =字幕一半 23/20px bold、
    # 序号 =numSize 一半 30/26px bold、正文 44/38px——全部 ≥ WCAG 大字
    # 标准（bold ≥18.66px），按大字 AA 3.0 卡。说话人色组与序号数字色
    # 引用 gen_hyperframes 模块常量（单一数据源，改色自动跟进断言）。
    print("\n[21] WCAG extended (sub/speaker/agenda-num/body)")

    def _hex01(h):
        h = h.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def _rgba01(s):
        """'rgba(r,g,b,a)' / '#hex' → (r,g,b 0-1, a)；解析失败返回 None。"""
        s = s.strip()
        if s.startswith("#"):
            c = _hex01(s)
            return (c[0], c[1], c[2], 1.0) if c else None
        m = _re.match(r"rgba?\(([^)]+)\)", s)
        if not m:
            return None
        parts = [p.strip() for p in m.group(1).split(",")]
        if len(parts) < 3:
            return None
        try:
            r, g, b = (float(parts[i]) / 255 for i in range(3))
            a = float(parts[3]) if len(parts) > 3 else 1.0
        except ValueError:
            return None
        return (r, g, b, a)

    def _lin01(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def _lum01(rgb):
        lin = [_lin01(c) for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    def _cr01(a, b):
        la, lb = _lum01(a), _lum01(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)

    def _composite(fg, bg):
        """fg=(r,g,b,a) 半透明叠在 bg=(r,g,b) 上 → 合成后的不透明色。"""
        return tuple(fg[i] * fg[3] + bg[i] * (1 - fg[3]) for i in range(3))

    import gen_hyperframes as _gh19  # [21] 说话人/序号配色常量的家
    _wcag_ext_fail = 0
    for _tname in list_theme_names():
        _tc = get_theme_colors(_tname)
        _stops = _hex6.findall(_tc.get("bg_gradient", ""))
        _worst_bg = (min((_hex01(s) for s in _stops), key=_lum01)
                     if _stops else None)
        # 21a 字幕主文字 vs 字幕栏
        _sub = tuple(int(x) / 255
                     for x in _tc.get("sub_bar_rgb", "0,0,0").split(","))
        _txt = _rgba01(_tc.get("text_color", "#22262b"))
        _r = _cr01(_txt[:3], _sub) if _txt else None
        check(f"sub text on sub_bar [{_tname}] >= 3.0",
              _r is not None and _r >= 3.0,
              f"{_r:.2f}" if _r is not None else "parse fail")
        if _r is None or _r < 3.0:
            _wcag_ext_fail += 1
        # 21b 说话人色 vs 字幕栏（复刻 generate_html 的明暗分组选择）
        _grp = (_gh19.SPK_COLORS_ON_LIGHT
                if 0.299 * _sub[0] + 0.587 * _sub[1] + 0.114 * _sub[2] > 0.5
                else _gh19.SPK_COLORS_ON_DARK)
        for _c in _grp:
            _r = _cr01(_hex01(_c), _sub)
            check(f"speaker {_c} on sub_bar [{_tname}] >= 3.0",
                  _r >= 3.0, f"{_r:.2f}")
            if _r < 3.0:
                _wcag_ext_fail += 1
        # 21d 正文 vs body 卡片底（body_bg 半透明叠 bg 最坏段，正文色
        # 自身也带 alpha，两层合成才是在屏幕上的有效色对）
        _bb = _rgba01(_tc.get("body_bg", "rgba(0,0,0,0.2)"))
        _bt = _rgba01(_tc.get("body_text", "rgba(0,0,0,0.8)"))
        if _worst_bg and _bb and _bt:
            _card = _composite(_bb, _worst_bg)
            _eff = _composite(_bt, _card)
            _r = _cr01(_eff, _card)
            check(f"body text on card [{_tname}] >= 3.0",
                  _r >= 3.0, f"{_r:.2f}")
            if _r < 3.0:
                _wcag_ext_fail += 1
        else:
            check(f"body text on card [{_tname}] parse ok", False,
                  "rgba parse fail")
            _wcag_ext_fail += 1

    # 21c agenda 序号数字 vs accent 圆底（8 色 × 固定数字色）
    for _ac in get_accent_palette():
        _r = _cr01(_hex01(_gh19.AGENDA_NUM_TEXT_COLOR), _hex01(_ac))
        check(f"agenda num on {_ac} >= 3.0", _r >= 3.0, f"{_r:.2f}")
        if _r < 3.0:
            _wcag_ext_fail += 1
    check("WCAG extended all combos pass", _wcag_ext_fail == 0)

    # 22. 深度 review 修复回归组（批次 A-D 修复的防退化沉淀）
    print()
    print("[22] review-fix regression coverage")
    import tempfile as _tf22
    from _tts import _clear_stale_sidecars
    from search_images import _apply_picks

    # 22a sidecar 清理：改稿不带 --resume 重跑时，残留的旧 .orig.wav 会让
    # apply_speed 把新音频整体丢弃（成片念旧稿配新字幕）
    with _tf22.TemporaryDirectory() as _td22a:
        _wav22 = os.path.join(_td22a, "s0001.wav")
        open(_wav22, "wb").write(b"RIFF")
        for _sfx22 in (".orig.wav", ".spd", ".spd.tmp.wav", ".failed", ".sha"):
            open(_wav22 + _sfx22, "wb").write(b"x")
        _clear_stale_sidecars(_wav22)
        check("sidecar: stale .orig/.spd/.failed removed before re-synth",
              all(not os.path.exists(_wav22 + _sfx22) for _sfx22 in
                  (".orig.wav", ".spd", ".spd.tmp.wav", ".failed")))
        check("sidecar: main wav and .sha cache untouched",
              os.path.exists(_wav22) and os.path.exists(_wav22 + ".sha"))
        try:
            _clear_stale_sidecars(_wav22)
            check("sidecar: idempotent when nothing to remove", True)
        except Exception as _e22:
            check("sidecar: idempotent when nothing to remove", False, repr(_e22))

    # 22b search_images --pick 审阅决策：显式 0 弃用 / 越界报错不删候选 /
    # 未提及原样保留（多轮审阅）/ 已定稿条目继承
    with _tf22.TemporaryDirectory() as _td22b:
        for _f22 in ("seg1_cand1.png", "seg1_cand2.png", "seg2_cand1.png"):
            open(os.path.join(_td22b, _f22), "wb").write(b"img")
        _cand22 = {
            "seg1": {"title": "t1", "candidates": [
                {"file": "seg1_cand1.png"}, {"file": "seg1_cand2.png"}]},
            "seg2": {"title": "t2", "candidates": [{"file": "seg2_cand1.png"}]},
        }
        _exist22 = {"seg0": "images/seg0.png"}
        _pmap22, _pmsg22 = _apply_picks(_cand22, {"seg1": 2}, _td22b, _exist22)
        check("pick: chosen candidate finalized under sid name",
              _pmap22.get("seg1") == {"src": "images/seg1.png"}
              and os.path.exists(os.path.join(_td22b, "seg1.png")))
        check("pick: unchosen candidates removed",
              not os.path.exists(os.path.join(_td22b, "seg1_cand1.png")))
        check("pick: unmentioned segment keeps candidates (multi-round)",
              os.path.exists(os.path.join(_td22b, "seg2_cand1.png"))
              and "seg2" not in _pmap22)
        # _exist22 故意用字符串简写：验证继承时被归一化成对象落盘
        check("pick: existing entries inherited & normalized to object",
              _pmap22.get("seg0") == {"src": "images/seg0.png"})
        _pmap22b, _pmsg22b = _apply_picks(_cand22, {"seg2": 0}, _td22b, _exist22)
        check("pick 0: dropped from map and candidates deleted",
              "seg2" not in _pmap22b
              and not os.path.exists(os.path.join(_td22b, "seg2_cand1.png")))
        check("pick 0: UNSUITABLE message",
              any("UNSUITABLE" in m for m in _pmsg22b))
        _pmap22c, _pmsg22c = _apply_picks(_cand22, {"seg1": 5}, _td22b, _exist22)
        check("pick out-of-range: error + finalized file kept",
              any("越界" in m for m in _pmsg22c)
              and os.path.exists(os.path.join(_td22b, "seg1.png")))
        _pmap22d, _pmsg22d = _apply_picks(_cand22, {"seg9": 1}, _td22b, _exist22)
        # 未知 sid 不增删条目；字符串继承条目归一化成对象（单一格式落盘）
        check("pick unknown sid: warn and map unchanged (normalized)",
              any("seg9" in m for m in _pmsg22d)
              and _pmap22d == {"seg0": {"src": "images/seg0.png"}})

    # 22c pipeline --check-env 可独立运行（--source 不再 required；必填校验
    # 在 check-env 早退之后）
    with open(os.path.join(HERE, "pipeline.py"), encoding="utf-8") as _f22:
        _pipe22 = _f22.read()
    check("check-env: --source optional",
          'parser.add_argument("--source", default=None' in _pipe22)
    check("check-env: required-args validated after early exit",
          "if args.check_env:" in _pipe22
          and "if not args.source:" in _pipe22
          and _pipe22.index("if args.check_env:")
          < _pipe22.index("if not args.source:"))

    # 22d BGM 混音 amix normalize=0（默认 normalize 会把两路各乘 1/2，
    # 人声被无感衰减 -6dB）
    with open(os.path.join(HERE, "_audio.py"), encoding="utf-8") as _f22:
        _audio22 = _f22.read()
    check("mix_bgm: amix normalize=0 (voice not halved)",
          "amix=inputs=2" in _audio22 and "normalize=0" in _audio22)

    # 22e run.py --aspect both 渲染两个成片并分别校验（只渲染横屏的话
    # 竖屏 HTML 白做）
    with open(os.path.join(HERE, "run.py"), encoding="utf-8") as _f22:
        _run22 = _f22.read()
    check("run.py: --aspect both renders out.mp4 + out.vertical.mp4",
          'args.aspect == "both"' in _run22
          and "out.vertical.mp4" in _run22
          and "index.vertical.html" in _run22)
    # 22e1 run.py 接入 portrait 画幅：choices 认它 + 渲染目标按 1080x1440
    # 校验（portrait 不进 both 轮换，只走单画幅路径）
    check("run.py: portrait accepted with 1080x1440 verify size",
          '"portrait"' in _run22
          and '"1080x1440"' in _run22)
    # 22e3 模式固定按画幅绑定：run.py 不再暴露 --sub-mode 选项（横屏
    # bar、竖屏 verse 由 gen_hyperframes 内部按画幅决定）
    check("run.py: --sub-mode removed (aspect-bound modes)",
          "--sub-mode" not in _run22
          and "横屏 bar、竖屏 verse" in _run22)
    # 22e2 both 的 check 轮换隔离（hyperframes check 对项目里两个根
    # composition 报 multiple_root_compositions；selftest 不真跑全管线，
    # 这里断言隔离/恢复机制的关键行存在——注意不要用 "try:" 这类几乎
    # 恒真的子串撑门面，要断言具体行为语句）
    check("run.py: both check isolates each aspect (.disabled rename + restore)",
          'os.replace(html_path, html_path + ".disabled")' in _run22
          and 'os.replace(html_path + ".disabled", html_path)' in _run22
          and "_keep_snapshots(\"landscape\")" in _run22
          and "_keep_snapshots(\"vertical\")" in _run22
          and "index.vertical.html" in _run22
          and 'args.aspect == "both"' in _run22)

    # 22f 字幕 DOM 逻辑按画幅隔离：verse（竖屏）输出不含 sub-bar 相关
    # JS/DOM（含行透明度 lop*op 双重相乘那套逻辑）；bar（横屏）输出则
    # 完整携带
    _verse_dom_html = _gh2.generate_html(manifest, "audio/combined.wav",
                                         width=1080, height=1440,
                                         aspect="portrait")
    check("verse mode: sub-bar DOM/JS logic absent",
          "rows[r].style.opacity" not in _verse_dom_html
          and "subEls" not in _verse_dom_html
          and ".sub-text{" not in _verse_dom_html)
    check("bar mode: sub-bar row stagger opacity logic present",
          "rows[r].style.opacity = lop;" in html_long
          and "lop * op" not in html_long)

    # 22g gen_hyperframes 显式导入 get_ffmpeg（缺 import 会在视频探测处
    # NameError）
    with open(os.path.join(HERE, "gen_hyperframes.py"), encoding="utf-8") as _f22:
        _gh22 = _f22.read()
    check("gen_hyperframes imports get_ffmpeg",
          "from _ffmpeg import get_ffmpeg" in _gh22)

    # ── 23. 本轮修复的守护断言（英文断句 / .env 编码 / wrap_numbers /
    # TTS fail-fast / WAV 样本精确时长 / sid+accent 契约）────────────
    print("\n[23] review-fix guards")

    # 23a 英文句终符：中英混合稿的英文句子能被切开，小数/缩写/省略号/
    # 人名首字母不被误切；纯中文行为不变
    s = split_sentences("OpenAI released GPT-5. It costs $20 per month. Really!")
    check("en: ASCII .!? split mixed-content sentences",
          s == ["OpenAI released GPT-5.", "It costs $20 per month.", "Really!"],
          repr(s))
    s = split_sentences("The U.S. market grew 3.5 percent, e.g. in Q1. Dr. Smith agrees.")
    check("en: decimals/abbrevs/initials not over-split",
          len(s) == 2 and s[0].startswith("The U.S.") and "3.5" in s[0]
          and s[1].startswith("Dr."), repr(s))
    s = split_sentences("他说要等一下...然后走了。")
    check("en: ellipsis mid-text stays one sentence", len(s) == 1, repr(s))

    # 23b _env 编码容错：UTF-16（PS5.1 重定向产物）与 GBK 的 .env 不再
    # 裸栈，key 能正常解析出来
    import tempfile as _tmp23
    from _env import _parse_env_file
    with _tmp23.TemporaryDirectory() as _td23:
        _p16 = os.path.join(_td23, "utf16.env")
        with open(_p16, "wb") as f:
            f.write(b"\xff\xfe" + "MIMO_API_KEY=sk-u16\n".encode("utf-16-le"))
        r16 = None
        try:
            r16 = _parse_env_file(_p16)
        except Exception as e:
            check("env: UTF-16 .env parsed (no crash)", False, repr(e))
        check("env: UTF-16 .env key resolved",
              r16 is not None and r16.get("MIMO_API_KEY") == "sk-u16", r16)
        _pgbk = os.path.join(_td23, "gbk.env")
        with open(_pgbk, "wb") as f:
            f.write("# 注释\nMIMO_API_KEY=sk-gbk\n".encode("gb18030"))
        rgbk = None
        try:
            rgbk = _parse_env_file(_pgbk)
        except Exception as e:
            check("env: GBK .env parsed (no crash)", False, repr(e))
        check("env: GBK .env key resolved",
              rgbk is not None and rgbk.get("MIMO_API_KEY") == "sk-gbk", rgbk)

    _tdir23 = tempfile.TemporaryDirectory()
    _tmpdir = _tdir23.name

    # 23c wrap_numbers：esc() 产生的数字字符引用（&#39;）里的数字不被误包，
    # 正常文本里的数字照常包裹
    from gen_hyperframes import esc, wrap_numbers
    wrapped = wrap_numbers(esc("Apple's 5G chip costs $399"), "#4fc3f7")
    check("wrap_numbers skips numeric char refs",
          "&#<span" not in wrapped and "<span" in wrapped
          and ">39</span>" not in wrapped, wrapped)
    check("wrap_numbers still wraps plain numbers",
          '<span class="num-accent"' in wrap_numbers("增长15亿", "#4fc3f7"))

    # 23d _tts 无音频响应 fail-fast：BadAudioResponseError 归为确定性失败
    from _tts import BadAudioResponseError, _is_non_retryable
    check("tts: bad audio response is non-retryable",
          _is_non_retryable(BadAudioResponseError("no audio")))
    class _FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kw):
                    import types
                    msg = types.SimpleNamespace(audio=None)
                    return types.SimpleNamespace(
                        choices=[types.SimpleNamespace(message=msg)])
    _synth_ok, _spd = synth_sentence(_FakeClient(), "测试。", "冰糖",
                                     "", os.path.join(_tmpdir, "tts_badresp.wav"),
                                     max_retries=3)
    check("tts: audio-less response gives up without retries", _synth_ok is False)

    # 23e WAV 时长样本精确：wave 可读的文件不再有 10ms 量化误差
    from _audio import measure_duration, _wav_duration
    _probe = os.path.join(_tmpdir, "dur_probe.wav")
    import wave as _w23
    with _w23.open(_probe, "wb") as _wf23:
        _wf23.setnchannels(1)
        _wf23.setsampwidth(2)
        _wf23.setframerate(24000)
        _wf23.writeframes(b"\x00\x00" * 24001)  # 恰好 1.000041666..s
    _exact = 24001 / 24000.0
    check("wav duration is sample-accurate (not 10ms quantized)",
          abs(measure_duration("ffmpeg", _probe) - _exact) < 1e-9,
          measure_duration("ffmpeg", _probe))
    check("_wav_duration returns None for non-wav",
          _wav_duration(os.path.join(HERE, "preview.js")) is None)

    # 23f 契约层：坏 sid / 坏 accent hex 在进管线前报错
    from _contracts import validate_timing_manifest as _vtm23
    _base_m23 = {"sentences": [{"index": 0, "text": "好。", "start_time": 0.0,
                                "duration": 1.0}],
                 "total_duration": 1.0}
    for _bad_seg in (
            {"id": 'seg"1', "sentences": [_base_m23["sentences"][0]]},
            {"id": "seg;drop", "sentences": [_base_m23["sentences"][0]]},
            {"id": "seg1", "accent": "#12345",
             "sentences": [_base_m23["sentences"][0]]},
    ):
        try:
            m = dict(_base_m23, segments=[_bad_seg])
            _vtm23(m)
            check("contract rejects bad sid/accent", False, repr(_bad_seg.get("id")))
        except ValueError:
            check("contract rejects bad sid/accent", True)
    _dup = dict(_base_m23, segments=[
        {"id": "seg1", "sentences": [_base_m23["sentences"][0]]},
        {"id": "seg1", "accent": "#4fc3f7",
         "sentences": [_base_m23["sentences"][0]]}])
    try:
        _vtm23(_dup)
        check("contract rejects duplicate sid", False)
    except ValueError:
        check("contract rejects duplicate sid", True)
    # 合法值不受影响：opening/seg1/closing 与合法 hex 照常通过
    _ok = dict(_base_m23, segments=[
        {"id": "seg1", "accent": "#4fc3f7",
         "sentences": [_base_m23["sentences"][0]]}])
    check("contract accepts valid sid + accent hex", _vtm23(_ok) is _ok)

    # 23g 渲染流程优化：check 缓存命中/失效逻辑 + 成片复用键
    import run as _runmod23
    with _tmp23.TemporaryDirectory() as _td23g:
        _html23 = os.path.join(_td23g, "index.html")
        with open(_html23, "w", encoding="utf-8") as f:
            f.write("<html>v1</html>")
        _snaps23 = os.path.join(_td23g, "snapshots")
        os.makedirs(_snaps23)
        # 首次：无缓存 → miss
        check("check cache: cold start misses",
              not _runmod23._check_cache_hit(_td23g, _html23, _snaps23, False))
        _runmod23._check_cache_put(_td23g, _html23, _snaps23)
        # 写入后命中
        check("check cache: hit after put",
              _runmod23._check_cache_hit(_td23g, _html23, _snaps23, False))
        # HTML 变化 → miss（任何影响画面的 gen 输入都落在 HTML 字节里）
        with open(_html23, "a", encoding="utf-8") as f:
            f.write("<!-- changed -->")
        check("check cache: html change invalidates",
              not _runmod23._check_cache_hit(_td23g, _html23, _snaps23, False))
        _runmod23._check_cache_put(_td23g, _html23, _snaps23)
        # 快照目录被删 → miss（防"删快照静默跳过审帧"）
        shutil.rmtree(_snaps23)
        check("check cache: missing snapshots dir invalidates",
              not _runmod23._check_cache_hit(_td23g, _html23, _snaps23, False))
        # force 无条件 miss
        os.makedirs(_snaps23)
        _runmod23._check_cache_put(_td23g, _html23, _snaps23)
        check("check cache: --force-check always misses",
              not _runmod23._check_cache_hit(_td23g, _html23, _snaps23, True))
        # 成片复用键：HTML/manifest/参数任一变化键都变
        class _A23:
            fps, quality, workers, gpu = 24, "standard", 6, False
        _m23f = os.path.join(_td23g, "m.json")
        with open(_m23f, "w", encoding="utf-8") as f:
            f.write("{}")
        _k1 = _runmod23._render_cache_key(_html23, _m23f, _A23)
        _k2 = _runmod23._render_cache_key(_html23, _m23f, _A23)
        check("render cache key: stable for same inputs", _k1 == _k2)
        with open(_html23, "a", encoding="utf-8") as f:
            f.write("x")
        check("render cache key: html change changes key",
              _runmod23._render_cache_key(_html23, _m23f, _A23) != _k1)
        with open(_html23, "w", encoding="utf-8") as f:
            f.write("<html>v1</html>")
        _A23.workers = 8
        check("render cache key: workers change changes key",
              _runmod23._render_cache_key(_html23, _m23f, _A23) != _k1)
        # run.py 新旗标在位
        with open(os.path.join(HERE, "run.py"), encoding="utf-8") as f:
            _run_src23g = f.read()
        check("run.py: --force-check / --reuse-render flags present",
              "--force-check" in _run_src23g and "--reuse-render" in _run_src23g)
        check("run.py: workers default bumped to 6",
              '"--workers", type=int, default=6' in _run_src23g)

        # ── 24. 产物不得落进技能目录 / 缺图提示按"有无候选"分流 ──────────
        # 两条都是文档约定被机械化的防线，缺了会静默退化：
        #   · 守卫没了 → 照抄文档示例命令就把 audio_output/、hf-project/
        #     建在技能仓库里，污染仓库且多次制作串台；
        #   · 分流没了 → --search-sids 部分路由场景下，对"从没搜过图"的
        #     段落也提示 --pick，agent 会原地打转。
        check("run.py: 技能目录污染守卫在位",
              "_guard_not_in_skill_dir" in _run_src23g
              and "SKILL_DIR" in _run_src23g)
        check("run.py: 守卫只对非 dry-run 生效（dry-run 承诺不写文件）",
              "if not args.dry_run:" in _run_src23g
              and "_guard_not_in_skill_dir(" in _run_src23g)

        _inside = _runmod23._is_inside(
            os.path.join(_runmod23.SKILL_DIR, "audio_output"),
            _runmod23.SKILL_DIR)
        check("_is_inside: 技能目录内的产物路径被认出", _inside)
        check("_is_inside: 技能目录外的产物路径放行",
              not _runmod23._is_inside(os.path.join(_td23g, "audio_output"),
                                       _runmod23.SKILL_DIR))
        # 技能目录本身（相等）也要算"在内"
        check("_is_inside: 路径等于技能目录时也判为在内",
              _runmod23._is_inside(_runmod23.SKILL_DIR, _runmod23.SKILL_DIR))

        # 缺图分流：seg1 有候选待 --pick，seg2/seg3 本轮没候选需走 B/C/D
        _cj24 = os.path.join(_td23g, "candidates.json")
        with open(_cj24, "w", encoding="utf-8") as f:
            json.dump({"seg1": {"title": "A", "candidates": [
                {"file": "seg1_cand1.jpg"}, {"file": "seg1_cand2.jpg"}]}}, f)
        _pick, _nocand, _lines = _runmod23._split_missing_by_candidates(
            ["seg1", "seg2", "seg3"], _cj24, os.path.join(_td23g, "images"))
        check("缺图分流: 有候选的段落进 --pick 组", _pick == ["seg1"])
        check("缺图分流: 无候选的段落进补图组", _nocand == ["seg2", "seg3"])
        check("缺图分流: 候选行带 sid 与全路径",
              len(_lines) == 1 and "seg1" in _lines[0]
              and "seg1_cand1.jpg" in _lines[0])
        # candidates.json 缺失/损坏 → 全部归入补图组，不臆造候选
        _pick2, _nocand2, _lines2 = _runmod23._split_missing_by_candidates(
            ["seg1"], os.path.join(_td23g, "nope.json"),
            os.path.join(_td23g, "images"))
        check("缺图分流: candidates.json 缺失时全归补图组",
              _pick2 == [] and _nocand2 == ["seg1"] and _lines2 == [])
        # 分流函数被 main 的缺图分支实际调用（防"加了函数没接线"）
        check("run.py: 缺图分支调用了分流函数",
              "_split_missing_by_candidates(" in _run_src23g)

    # 25. 门控摘要口径（wiki 自进化体系已于 1.5.51 奥卡姆剃刀砍除；
    # parse_summary_totals 内联进 dev/check.py，这里钉住接线与语义）
    print("\n[25] 门控摘要口径")

    def _read_src25(name):
        with open(os.path.join(HERE, name), "r", encoding="utf-8") as f:
            return f.read()

    _cs25 = _read_src25(os.path.join("..", "dev", "check.py"))
    check("check.py: 子进程结果带上了自报明细",
          'entry["passed"], entry["total"] = p2, t2' in _cs25)
    check("check.py: 摘要解析内联实现（取最后一行 __SUMMARY_JSON__）",
          'SUMMARY_PREFIX = "__SUMMARY_JSON__"' in _cs25
          and "def parse_summary_totals(" in _cs25)

    # ── 收尾：覆盖率扫描剩下的两个既有模块也补上 ──
    # 它俩都在本次改造之前就存在，但"零覆盖"这条结论同样成立。扫描报出来是
    # 线索，既然扫到了就没理由绕过去（见 patterns/p11）。
    import urllib.request as _urlreq25
    # run_eval/_assets 在 dev/，补一条路径（wiki 段删除后这里只剩 scripts/）
    _DEV_DIR25 = os.path.abspath(os.path.join(HERE, "..", "dev"))
    if _DEV_DIR25 not in sys.path:
        sys.path.insert(0, _DEV_DIR25)
    import run_eval as _RE25
    import _assets as _AS25

    # ── run_eval.py：只测不需要真实 ffmpeg / 网络的确定性部分 ──
    check("run_eval: segments 条数在区间内判过",
          _RE25.check_segment_count({"segments": [{}] * 6}) == (True, 6))
    check("run_eval: 条数低于下限判不过（含边界 4）",
          _RE25.check_segment_count({"segments": [{}] * 4}) == (False, 4))
    check("run_eval: 条数高于上限判不过（含边界 9）",
          _RE25.check_segment_count({"segments": [{}] * 9}) == (False, 9))
    check("run_eval: 空 segments 判不过且如实报 0",
          _RE25.check_segment_count({}) == (False, 0))
    check("run_eval: 区间可自定义",
          _RE25.check_segment_count({"segments": [{}] * 3},
                                    expect_range=(3, 3)) == (True, 3))

    import tempfile as _tf25
    # 静音包装：run_eval 的 check/skip 会直接 print，测试里不想刷屏
    import contextlib as _cl25
    import io as _io25

    def _quiet25(fn, *a, **k):
        with _cl25.redirect_stdout(_io25.StringIO()):
            return fn(*a, **k)

    # check/skip 写的是模块级 RESULTS/SKIPPED，测完必须还原
    _ores25, _oskip25 = _RE25.RESULTS, _RE25.SKIPPED
    try:
        _RE25.RESULTS, _RE25.SKIPPED = [], []
        _quiet25(_RE25.check, "x", True)
        _quiet25(_RE25.check, "y", False, "细节")
        _quiet25(_RE25.skip, "z", "本机缺编码器")
        check("run_eval: check/skip 分得清通过、失败与环境跳过",
              _RE25.RESULTS == [("x", True), ("y", False)]
              and _RE25.SKIPPED == [("z", "本机缺编码器")],
              (_RE25.RESULTS, _RE25.SKIPPED))
    finally:
        _RE25.RESULTS, _RE25.SKIPPED = _ores25, _oskip25

    # pick_h264_encoder：用假 ffmpeg 替身，免得依赖本机装了哪些编码器
    def _fake_ffmpeg25(body, tmpdir):
        """造一个只打印编码器清单的假 ffmpeg（该函数只解析 stdout）。

        Windows 的 CreateProcess 无法直接执行 .sh，替身按平台出
        .bat（多行清单拆成多条 echo）——否则枚举永远走"拿不到清单"
        分支，这两条断言就成了永久红。
        """
        if os.name == "nt":
            p = os.path.join(tmpdir, "fake_ffmpeg.bat")
            _lines = ["@echo off"] + ["echo " + ln.strip(" ")
                                       for ln in body.split("\n")]
            _content = "\r\n".join(_lines) + "\r\n"
        else:
            p = os.path.join(tmpdir, "fake_ffmpeg.sh")
            _content = "#!/bin/sh\necho '%s'\n" % body
        with open(p, "w", encoding="utf-8") as _f25:
            _f25.write(_content)
        if os.name != "nt":
            os.chmod(p, 0o755)
        return p

    with _tf25.TemporaryDirectory() as _td25:
        check("run_eval: 挑到本机唯一可用的 h264 编码器",
              _RE25.pick_h264_encoder(_fake_ffmpeg25(
                  " V..... libopenh264  OpenH264 H.264 encoder ", _td25))
              == "libopenh264")
        check("run_eval: 多个可用时按优先级取 libx264",
              _RE25.pick_h264_encoder(_fake_ffmpeg25(
                  " V..... libopenh264  OpenH264 \n"
                  " V..... libx264  libx264 H.264 ", _td25)) == "libx264")
        check("run_eval: 一个 h264 都没有时返回 None（应 SKIP 而非 FAIL）",
              _RE25.pick_h264_encoder(_fake_ffmpeg25(
                  " V..... vp9  VP9 encoder ", _td25)) is None)
        check("run_eval: 拿不到清单时退回 libx264（让真报错如实冒出来）",
              _RE25.pick_h264_encoder(os.path.join(_td25, "not-exists"))
              == "libx264")

    # ── _assets.py：GSAP 本地缓存，全程不碰真实网络 ──
    class _FakeResp25(object):
        def __init__(self, data):
            self._d = data

        def read(self):
            return self._d

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def _boom25(*_a, **_k):
        raise OSError("no network")

    check("_assets: CDN URL 钉住版本号",
          _AS25.GSAP_VERSION in _AS25.GSAP_CDN_URL, _AS25.GSAP_CDN_URL)

    _ouo25 = _urlreq25.urlopen
    _ocd25, _ocp25, _olp25 = (_AS25._CACHE_DIR, _AS25._CACHE_PATH,
                              _AS25._LEGACY_CACHE_PATH)
    try:
        with _tf25.TemporaryDirectory() as _td25:
            _cache25 = os.path.join(_td25, "cache")
            _AS25._CACHE_DIR = _cache25
            _AS25._CACHE_PATH = os.path.join(_cache25, "gsap.min.js")
            _AS25._LEGACY_CACHE_PATH = os.path.join(_td25, "legacy",
                                                    "gsap.min.js")

            # ① 快路径：输出目录已备好 → 直接复用，不拷缓存也不联网
            _out1 = os.path.join(_td25, "out1", "vendor")
            os.makedirs(_out1)
            with open(os.path.join(_out1, "gsap.min.js"), "w",
                      encoding="utf-8") as _f25:
                _f25.write("/* gsap */" * 300)

            def _no_net25(*_a, **_k):
                raise AssertionError("快路径不该联网")

            _urlreq25.urlopen = _no_net25
            check("_assets: 已备好的输出目录直接复用（不重新拷贝、不联网）",
                  _AS25.ensure_local_gsap(os.path.join(_td25, "out1"))
                  == "vendor/gsap.min.js")

            # ② 下载内容过小（CDN 返回错误页）→ 拒绝，不留半成品
            _urlreq25.urlopen = lambda *_a, **_k: _FakeResp25(
                b"<html>404</html>")
            _r_small = _AS25.ensure_local_gsap(
                os.path.join(_td25, "out2"), timeout=1)
            _tmps25 = [n for n in os.listdir(_cache25) if n.endswith(".tmp")] \
                if os.path.isdir(_cache25) else []
            check("_assets: 下载内容 <1000 字节判为无效（不写入缓存）",
                  _r_small is None and not os.path.isfile(_AS25._CACHE_PATH))
            check("_assets: 下载失败后不留 .tmp 残留",
                  not _tmps25, _tmps25)

            # ③ 无缓存且网络不可用 → None，由调用方回退 CDN（尽力而为）
            _urlreq25.urlopen = _boom25
            check("_assets: 无缓存且下载失败时返回 None（调用方回退 CDN）",
                  _AS25.ensure_local_gsap(os.path.join(_td25, "out3"),
                                          timeout=1) is None)

            # ④ 缓存命中 → 拷到输出目录
            os.makedirs(_cache25, exist_ok=True)
            with open(_AS25._CACHE_PATH, "w", encoding="utf-8") as _f25:
                _f25.write("/* cached gsap */" * 300)
            _out4 = os.path.join(_td25, "out4")
            check("_assets: 缓存命中时拷贝到输出目录",
                  _AS25.ensure_local_gsap(_out4) == "vendor/gsap.min.js"
                  and os.path.getsize(os.path.join(_out4, "vendor",
                                                   "gsap.min.js")) > 1000)

            # ⑤ 旧路径缓存迁移：老用户不必重新联网下载一次
            os.remove(_AS25._CACHE_PATH)
            os.makedirs(os.path.dirname(_AS25._LEGACY_CACHE_PATH))
            with open(_AS25._LEGACY_CACHE_PATH, "w", encoding="utf-8") as _f25:
                _f25.write("/* legacy gsap */" * 300)
            _urlreq25.urlopen = _boom25      # 迁移成功就不该走到联网
            _out5 = os.path.join(_td25, "out5")
            check("_assets: 旧缓存路径会被迁移过来（迁移成功就不联网）",
                  _AS25.ensure_local_gsap(_out5) == "vendor/gsap.min.js"
                  and os.path.isfile(_AS25._CACHE_PATH))
    finally:
        _urlreq25.urlopen = _ouo25
        _AS25._CACHE_DIR, _AS25._CACHE_PATH = _ocd25, _ocp25
        _AS25._LEGACY_CACHE_PATH = _olp25

    print(f"\n=== {passed} passed, {failed} failed ===")
    summary = {
        "tool": "selftest",
        "passed": passed,
        "failed": failed,
        "total": passed + failed,
        "results": [{"name": name, "ok": ok} for name, ok in RESULTS],
    }
    print("__SUMMARY_JSON__ " + json.dumps(summary, ensure_ascii=False))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
