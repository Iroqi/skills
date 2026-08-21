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
- visual_regression._diff_ratio：相同图/明显不同图/尺寸不一致三种像素 diff 判定（无 Pillow 时跳过）
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
import subprocess
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
    # 自动 body 行预算：5 句 30 字长句（每句占 2 视觉行）→ 8 行预算只放
    # 得下前 4 句；短句不受影响。防止兜底 body 溢出压到字幕栏。
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
          len(_lb.split("\n")) == 4 and "预算五" not in _lb, repr(_lb))

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
    # 回归：含英文专名的稀疏标点长句（2026-08-18 IDX26 类型）——不能整句交
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
    # 显式 verse：本块断言的是 verse 形态的 DOM（横屏默认已改为 bar）
    html = generate_html(manifest, "audio/combined.wav", sub_mode="verse")
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
    check("flow: 无 recap-more 封顶行（条目数 ≤ AGENDA_MAX）",
          "等共" not in html_flow)
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
    # 长句切行测试用 bar 模式：切出的行直接渲染进字幕条（verse 模式行
    # 数据只驱动 si，不进显示层）
    html_long = generate_html(long_cue_manifest, "audio/combined.wav",
                              sub_mode="bar")
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
          and "indexOf('preview')" in pj)

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
                                   theme="cream", sub_mode="bar")
    html_dark_bar = generate_html(dlg_manifest, "audio/combined.wav",
                                  theme="dark", sub_mode="bar")
    check("bar mode: speaker colors differ between light/dark bar themes",
          ".sub-speaker.spk-0{color:#1d4ed8}" in html_cream_bar
          and ".sub-speaker.spk-0{color:#7dd3fc}" in html_dark_bar)
    html_three_bar = generate_html(three_spk_manifest, "audio/combined.wav",
                                   theme="cream", sub_mode="bar")
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
    # 回归：speed<=0 / 非有限值曾经会让第二个 while 死循环（remaining/=0.5 对
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
        check("resolve npx via comspec on Windows",
              os.path.basename(r2[0]).lower() in ("cmd.exe", "cmd")
              and r2[1] == "/c", r2[0])

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
                # landscape_4_3 同一约定——横屏 860×680 槽填充率 95%）
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

    # 16. visual_regression / split_series / gen_cover / budget calibrate
    # 这四个都是纯离线逻辑（不依赖真实 TTS/渲染 API），加进来补上上次
    # review 发现的一个真实教训：--on-fail silence 的 resume 标记丢失 bug
    # 是手工构造场景才抓到的，如果当时就有这层覆盖，本该在第一版就被拦住。
    print("\n[15] visual_regression / split_series / gen_cover / budget calibrate")
    import tempfile as _tf16

    # 16a. visual_regression._diff_ratio
    try:
        from PIL import Image as _VRImage
        HAS_PIL_VR = True
    except ImportError:
        HAS_PIL_VR = False
    if not HAS_PIL_VR:
        print("  [SKIP] 未安装 Pillow，跳过 visual_regression.py 测试")
    else:
        from visual_regression import _diff_ratio
        with _tf16.TemporaryDirectory() as td:
            p_a = os.path.join(td, "a.png")
            p_b = os.path.join(td, "b.png")
            p_c = os.path.join(td, "c.png")
            _VRImage.new("RGB", (50, 50), (100, 100, 100)).save(p_a)
            _VRImage.new("RGB", (50, 50), (100, 100, 100)).save(p_b)
            _VRImage.new("RGB", (50, 50), (200, 0, 0)).save(p_c)
            mean_diff, changed_pct = _diff_ratio(p_a, p_b)
            check("visual_regression: identical images -> 0 diff",
                  mean_diff == 0.0 and changed_pct == 0.0, (mean_diff, changed_pct))
            mean_diff2, changed_pct2 = _diff_ratio(p_a, p_c)
            check("visual_regression: clearly different images -> large diff",
                  mean_diff2 > 50 and changed_pct2 > 0.9, (mean_diff2, changed_pct2))

            p_d = os.path.join(td, "d.png")
            _VRImage.new("RGB", (60, 60), (100, 100, 100)).save(p_d)
            mean_diff3, changed_pct3 = _diff_ratio(p_a, p_d)
            check("visual_regression: size mismatch -> treated as max diff",
                  mean_diff3 == 255.0 and changed_pct3 == 1.0, (mean_diff3, changed_pct3))

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

    # 17. run._image_coverage：缺图拦截的判定核心（H1 回归防护——曾因外层
    # 多套了一个 has_images 条件，导致首跑（images.json 还不存在）时拦截失效，
    # 缺图直接漏进渲染。这里直接测函数本身的契约：images.json 不存在时
    # 全部 news/seg 段落都算 missing，而不是被跳过。）
    from run import _image_coverage

    with tempfile.TemporaryDirectory() as td:
        manifest_path = os.path.join(td, "timing_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({
                "sentences": [{"index": 0, "text": "开场白。", "duration": 1.0}],
                "segments": [
                    {"id": "opening", "title": "本期内容"},
                    {"id": "news1", "title": "第一条"},
                    {"id": "news2", "title": "第二条"},
                    {"id": "seg1", "title": "讲解段"},
                    {"id": "closing", "title": "小结"},
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
            json.dump({"sentences": [{"index": 0, "text": "一句话。", "duration": 1.0}]}, f,
                      ensure_ascii=False)
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
    _am = _re.search(r"AGENDA_MAX = (\d+)", _gh_src)
    check("AGENDA_MAX found in gen_hyperframes", _am is not None)
    if _am:
        _amax = int(_am.group(1))
        _sm = max(_tpl["layout"][a]["agenda"]["shrinkMax"]
                  for a in ("landscape", "vertical"))
        check("template shrinkMax <= AGENDA_MAX", _sm <= _amax,
              f"shrinkMax={_sm} AGENDA_MAX={_amax}")

    # 18c. images.json 的 autoplay 必须被 gen_hyperframes 消费（曾硬编码忽略）
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
    # 不展开深浅判断静默失效（曾导致 dark 的 tagline 走压暗分支掉到 2.1）
    check("_hex_to_rgb01 expands 3-digit hex",
          _hex_to_rgb01("#fff") == (1.0, 1.0, 1.0)
          and _hex_to_rgb01("#4fc3f7") == _hex_to_rgb01("#4fc3f7"))
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
    # pipeline.main() 里曾有一句函数内 import math，把 math 变成局部名，
    # 导致更早执行的 gap 校验 UnboundLocalError（TTS 实跑才触发，
    # selftest 不走 argparse 分支）。源码级断言：main 函数体内无 import math。
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
    with open(os.path.join(_skill_root, "scripts", "gen_hyperframes.py"),
              encoding="utf-8") as _gf:
        _gen_src = _gf.read()
    check("vertical subtitle uses aspect-strict cap (22/1.0)",
          '_sub_cap = 22 if aspect == "vertical" else 28' in _gen_src
          and '_sub_slack = 1.0 if aspect == "vertical" else 1.5' in _gen_src
          and "slack=_sub_slack" in _gen_src)

    # ── 20c. 竖屏 verse 句子流结构与滚动参照系 ───────────────
    # verse-clip 无定位时 verse-line.offsetTop 相对
    # seg-card（≈1500px），滚动公式恒被钳到段落尾部——第一句永远在
    # 窗口外。断言 clip 定位存在 + verse 钉底（所有段落上边界齐平，
    # 标题行数差异由图片弹性高度吸收）+ 开场/收尾标题垂直居中。
    import gen_hyperframes as _gh2
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
    _vhtml = _gh2.generate_html(_vman, "audio/combined.wav",
                                width=1080, height=1920, aspect="vertical")
    check("vertical verse: clip positioned (offsetTop base for scroll)",
          '.verse-clip{position:relative;padding:60px 0' in _vhtml)
    check("vertical verse: pinned to content bottom (uniform top edge)",
          '[data-aspect="vertical"] .verse{position:static!important' in _vhtml
          and 'margin-top:auto!important' in _vhtml)
    check("vertical image: square slot 980×980 (side padding 50 + "
          "height:auto + aspect-ratio 1/1)",
          'padding:220px 50px 190px!important' in _vhtml
          and 'height:auto!important;aspect-ratio:1/1!important' in _vhtml
          and 'flex:0 1 auto!important;margin:48px 0 0!important' in _vhtml)
    check("vertical verse: breathing padding 60px + scroll anchor 60px "
          "(first/last line clear of mask fade)",
          'padding:60px 0' in _vhtml
          and "Math.min(0, 60 - act.top)" in _vhtml)
    check("vertical: opening/closing title vertically centered (margin auto)",
          '#opening .seg-title-wrap,[data-aspect="vertical"] #closing .seg-title-wrap'
          '{text-align:center!important;margin:auto 0!important}' in _vhtml)
    check("vertical verse: lines carry sentence index (data-i)",
          'data-i="1"' in _vhtml and 'data-i="2"' in _vhtml)
    check("vertical verse: cues carry si for runtime highlight",
          "si:1" in _vhtml and "si:2" in _vhtml)
    # 横屏显式 verse（默认已按画幅改为 bar）
    _lhtml = _gh2.generate_html(_vman, "audio/combined.wav",
                                sub_mode="verse")
    # 横屏 verse = bar 布局框架 + 内容框换滚动框：内容段 verse 进
    # title-wrap 文档流（正文卡位置，无图段 870 居中、随标题浮动），
    # 开场/收尾 agenda 保留、verse 钉左列底部
    check("landscape verse: DOM present, no sub-bar in either aspect",
          'class="verse"' in _lhtml
          and 'class="sub-bar"' not in _lhtml
          and 'class="sub-bar"' not in _vhtml
          and ".sub-text{" not in _lhtml)
    check("landscape verse: in-flow window (body slot) + pinned open/close",
          '.verse{position:relative;width:100%;height:300px' in _lhtml
          and ('#opening .verse,#closing .verse{position:absolute;'
               'left:50px;bottom:60px;width:870px}' in _lhtml)
          and 'style="width:870px;margin:' in _lhtml)
    # 有图段 title-wrap 是全宽 1760（标题横跨正文与图片上方），verse 必须
    # 显式钉 870——width:100% 会继承全宽、长句右半截滑到图片底下被遮
    # （layout 检查器以 text_occluded 暴露）。_vman 只有无图段，另造
    # 带图 manifest 断言 has-image 分支；两分支都不允许内联 width:100%。
    _limhtml = _gh2.generate_html(_vman, "audio/combined.wav",
                                  sub_mode="verse",
                                  images={"seg1": {"src": "images/x.jpg"}})
    check("landscape verse: window pinned to body column width "
          "(has-image 870 / no-image 870 centered)",
          'style="width:870px;margin:' in _lhtml
          and 'style="width:870px;margin-top:' in _limhtml
          and 'style="width:100%' not in _lhtml
          and 'style="width:100%' not in _limhtml)
    check("landscape verse: open/close group centered above verse window",
          'calc((100% - 380px)/2)' in _lhtml)
    # verse 替代 body 是 DOM 层不渲染（不是 CSS 隐藏）：源码级断言
    # 拦"改回 display:none 兜底"的回退（display:none 方案会让 GSAP
    # stagger 指向隐藏行、layout 检查器量到不可见卡片的 rect）
    check("verse kills body at DOM level (landscape content + "
          "vertical has-image)",
          '(aspect == "landscape" and not _is_oc)' in _gen_src
          and '(aspect == "vertical" and has_image)' in _gen_src)

    # ── 20d. bar 模式（经典形式）：仅横屏可用；竖屏家族 fail-fast ──
    _bhtml = _gh2.generate_html(_vman, "audio/combined.wav", sub_mode="bar")
    check("bar mode: sub-bar DOM present, verse DOM absent",
          'class="sub-bar"' in _bhtml and 'class="verse"' not in _bhtml)
    check("bar mode: body cards rendered (no verse replacement)",
          '.sub-speaker{' in _bhtml and 'subEls' in _bhtml)
    # 竖屏只有 verse：显式 bar 必须在函数层 raise（fail-fast，不是
    # 静默降级成 verse——静默换模式会让调用方以为拿到了 bar 产物）
    try:
        _gh2.generate_html(_vman, "audio/combined.wav",
                           width=1080, height=1920, aspect="vertical",
                           sub_mode="bar")
        _vbar_err = None
    except ValueError as e:
        _vbar_err = str(e)
    check("vertical rejects bar mode (fail-fast ValueError)",
          _vbar_err is not None and "竖屏" in _vbar_err)

    # ── 20e. portrait 紧凑竖屏（3:4，1080x1440）─────────────────
    # portrait 在 generate_html 本体内归一化为 vertical 布局家族
    # （data-aspect="vertical"）+ v_compact 紧凑参数
    _phtml = _gh2.generate_html(_vman, "audio/combined.wav",
                                width=1080, height=1440, aspect="portrait")
    check("portrait: normalized into vertical layout family",
          'data-aspect="vertical"' in _phtml
          and 'data-width="1080" data-height="1440"' in _phtml)
    check("portrait verse: compact padding + 4:3 image slot",
          'padding:100px 50px 80px!important' in _phtml
          and 'aspect-ratio:4/3!important' in _phtml)
    _bphtml = _gh2.generate_html(_vman, "audio/combined.wav",
                                 width=1080, height=1440, aspect="portrait")
    check("portrait verse: sub-bar absent (vertical family is verse-only)",
          'class="verse"' in _bphtml
          and 'class="sub-bar"' not in _bphtml)

    # ── 20f. sub_mode 默认按画幅解析：横屏 bar、竖屏家族 verse ─────
    # 不传 sub_mode 时 generate_html 自行解析（CLI --sub-mode 默认 None
    # 透传到此）；显式传值不受影响（上方 20d/20e 已覆盖显式两模式）
    _def_l = _gh2.generate_html(_vman, "audio/combined.wav")
    check("default: landscape resolves to bar (sub-bar present, "
          "verse absent)",
          'class="sub-bar"' in _def_l and 'class="verse"' not in _def_l)
    _def_v = _gh2.generate_html(_vman, "audio/combined.wav",
                                width=1080, height=1920, aspect="vertical")
    check("default: vertical resolves to verse",
          'class="verse"' in _def_v and 'class="sub-bar"' not in _def_v)
    _def_p = _gh2.generate_html(_vman, "audio/combined.wav",
                                width=1080, height=1440, aspect="portrait")
    check("default: portrait resolves to verse (follows vertical family)",
          'class="verse"' in _def_p and 'class="sub-bar"' not in _def_p)

    # ── 20b. timing_manifest segments 契约───────────────
    # gen_hyperframes 对 seg["sentences"] 直接下标访问，缺失时炸裸
    # KeyError；契约层应先报对人（R3 手写 fixture 实测暴露的隐性必填）
    print("\\n[20b] manifest segments contract")
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
    # 22e3 run.py 的 sub_mode 按画幅解析（与 gen_hyperframes 同规则），
    # 报告 params 记解析后的显式值而不是 null
    check("run.py: sub_mode default resolves by aspect "
          "(landscape->bar, else verse)",
          'args.sub_mode = ("bar" if args.aspect == "landscape"'
          ' else "verse")' in _run22)
    # 22e2 both 的 check 轮换隔离（hyperframes check 对项目里两个根
    # composition 报 multiple_root_compositions；selftest 不真跑全管线，
    # 这里断言隔离/恢复机制的关键行存在）
    check("run.py: both check isolates each aspect (.disabled rename + restore)",
          ".disabled" in _run22
          and "try:" in _run22
          and "_keep_snapshots(\"landscape\")" in _run22
          and "_keep_snapshots(\"vertical\")" in _run22
          and 'args.aspect == "both"' in _run22)

    # 22f 字幕 DOM 逻辑按模式隔离：verse 输出不含 sub-bar 相关 JS/DOM
    # （含行透明度 lop*op 双重相乘那套逻辑）；bar 输出则完整携带
    check("verse mode: sub-bar DOM/JS logic absent",
          "rows[r].style.opacity" not in html
          and "subEls" not in html
          and ".sub-text{" not in html)
    check("bar mode: sub-bar row stagger opacity logic present",
          "rows[r].style.opacity = lop;" in html_long
          and "lop * op" not in html_long)

    # 22g gen_hyperframes 显式导入 get_ffmpeg（缺 import 会在视频探测处
    # NameError）
    with open(os.path.join(HERE, "gen_hyperframes.py"), encoding="utf-8") as _f22:
        _gh22 = _f22.read()
    check("gen_hyperframes imports get_ffmpeg",
          "from _ffmpeg import get_ffmpeg" in _gh22)

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
