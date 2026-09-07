#!/usr/bin/env python3
"""Content-to-Video — 一键测试：确定性断言 + 离线集成 + 文档门禁（三合一）。

用法（改完代码/文档后唯一要跑的命令）：
  python dev/test.py

三段内容（1.5.74 起由原 scripts/selftest.py + dev/run_eval.py + dev/check.py
合并，使用者只有 agent 一个，不再维持三个入口三套摘要）：
  [1] 确定性断言——模块函数/产物结构/契约校验，字符串与 AST 级，
      不联网不调 API（_test_1 … _test_25 各章节）
  [2] 离线集成——真子进程 + 真 ffmpeg 合成 H.264/AAC 测试视频走
      verify_render.py 校验；fake fixture 跑 run.py 并行编排与缺图拦截；
      抓"跨脚本 CLI 胶水"断裂（断言层够不到的一层）
  [3] 文档门禁——frontmatter/version 一致性、SKILL.md 锚点、声明-实际
      交叉校验（--gap 默认、图片尺寸、踩坑条数、CLI choices 等防漂移）

任何一项失败以非零码退出；末尾打一行 __SUMMARY_JSON__ 供 agent 程序化解析。
语义/视觉层（取材判断、裂图/溢出审帧）不在此冒充自动化，由 agent 在真实
制作与审帧流程里完成。
"""

import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))  # 技能根目录（本文件与 SKILL.md 同级）
_SKILL_ROOT = HERE
SCRIPTS_DIR = os.path.join(_SKILL_ROOT, "scripts")
sys.path.insert(0, SCRIPTS_DIR)
import re  # noqa: E402  门禁段需要
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import contextlib  # noqa: E402
from _script_utils import setup_stdio  # noqa: E402  门禁段要打印子进程捕获输出

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


# 跨章节共享状态：_TPL/_SKILL_ROOT 由 main() 启动时预加载，章节内只读。
# 让 [18] 之前的章节也能安全访问（万一 [18] 崩溃，[20+] 仍能跑）。
_TPL = None


def _load_template():
    """预加载 template.json（[18]/[20+] 共用），加载失败返回 None。"""
    global _TPL
    try:
        with open(os.path.join(_SKILL_ROOT, "config", "template.json"),
                  encoding="utf-8") as _f:
            _TPL = json.load(_f)
    except Exception:
        _TPL = None


def _base_manifest():
    """六句基础 manifest fixture：[3]/[3b]/[22f] 共用时基，改时基只动这里。"""
    return {
        "sentences": [
            {"index": i, "text": f"第{['一', '二', '三', '四', '五', '六'][i]}句。",
             "start_time": i * 2.4, "duration": 2.0}
            for i in range(6)
        ],
        "total_duration": 14.5,
    }


def _test_1_split_sentences():
    print("\n[1] split_sentences")
    from _script_utils import split_sentences
    s = split_sentences("大家好呀。这是第二句！第三句呀？")
    check("basic terminators", s == ["大家好呀。", "这是第二句！", "第三句呀？"], repr(s))
    s = split_sentences("这是第一句；这是第二句。\n这是第三行。")
    check("semicolon + newline split", len(s) == 3, repr(s))
    s = split_sentences("短。后面长句子合并进来。")
    check("short fragment merged",
          len(s) == 1 and s[0] == "短。后面长句子合并进来。", repr(s))
    check("empty input", split_sentences("") == [])


def _test_2_build_structured():
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
            {"title": "A", "text": "苹果发布新手机。真棒。"},
            {"title": "B", "text": "OpenAI 发布新模型。"},
        ],
    }
    sents3, segs3 = build_parts(bad_src)
    check("independent per-segment split",
          sents3 == ["苹果发布新手机。真棒。", "OpenAI 发布新模型。"], repr(sents3))
    check("segment indices consistent",
          segs3[0]["start"] == 0 and segs3[0]["end"] == 1
          and segs3[1]["start"] == 1 and segs3[1]["end"] == 2)


def _test_2b_dialogue():
    # 2b. 双人对话（dialogue）段落
    print("\n[2b] build_from_structured dialogue（双人对话）")
    from build_from_structured import build_parts
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


def _test_2c_flow():
    # 2c. flow 自然叙事模式 + 超长句写作提醒
    print("\n[2c] build_from_structured flow 模式 + 超长句提醒")
    from build_from_structured import build_parts
    flow_src = dict({
        "opening": "大家好，欢迎收看今天的AI日报。",
        "closing": "感谢收看，明天见。",
        "segments": [
            {"title": "A", "text": "第一条新闻内容，值得关注。这是补充信息，也很重要。"},
            {"title": "B", "text": "第二条新闻内容，同样重要。后续进展，值得跟踪。"},
        ],
    }, flow=True)
    _, flow_segs = build_parts(flow_src)
    check("flow: opening agenda defaults on (chips 预告)",
          flow_segs[0].get("agenda") is True, flow_segs[0].get("agenda"))
    check("flow: closing recap defaults off",
          flow_segs[-1].get("recap") is False, flow_segs[-1].get("recap"))
    flow_src_on = dict(flow_src, opening_agenda=True, closing_recap=True)
    _, flow_segs_on = build_parts(flow_src_on)
    check("flow: explicit opening_agenda honored",
          flow_segs_on[0].get("agenda") is True)
    check("flow: explicit closing_recap honored",
          flow_segs_on[-1].get("recap") is True)
    default_src = {
        "opening": "大家好，欢迎收看今天的AI日报。",
        "closing": "感谢收看，明天见。",
        "segments": [
            {"title": "A", "text": "第一条新闻内容，值得关注。这是补充信息，也很重要。"},
            {"title": "B", "text": "第二条新闻内容，同样重要。后续进展，值得跟踪。"},
        ],
    }
    _, default_segs = build_parts(default_src)
    check("no flow: agenda defaults on (chapters)",
          default_segs[0].get("agenda") is True)
    try:
        from _contracts import validate_segments_source as _vss
        _vss(dict(default_src, flow="yes"))
        check("flow: non-bool rejected", False, "no exception")
    except ValueError as e:
        check("flow: non-bool rejected", "flow" in str(e), str(e)[:60])
    long_sent_src = dict(default_src, segments=[
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


def _test_2d_subtitle_lines():
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


def _test_2e_subtitle_cues():
    # 2e. split_subtitle_cues：每屏最多两行的 cue 分组（纯函数，确定性）
    print("\n[2e] _script_utils.split_subtitle_cues")
    from _script_utils import split_subtitle_cues as ssc
    long = ("今天我们要讲一个很长的主题，它包含三个部分的内容，"
            "分别是背景原理和实际应用，我们会逐一展开说明。")
    check("ssc: short sentence single cue", ssc("短句。") == [["短句。"]])
    cues_g = ssc(long)
    check("ssc: every cue has at most 2 lines",
          all(len(g) <= 2 for g in cues_g), cues_g)
    huge = "、".join(f"第{i}个要点需要展开说明" for i in range(1, 25)) + "。"
    hcues = ssc(huge)
    check("ssc: huge sentence splits into multiple cues", len(hcues) >= 2, hcues)
    check("ssc: huge sentence every cue <= 2 lines",
          all(len(g) <= 2 for g in hcues), [len(g) for g in hcues])
    # 行宽硬约束（max_chars=28，hard_cap 兜底）——slack 参数已移除
    check("ssc: every line within hard_cap",
          all(len(l) <= 28 for g in hcues for l in g),
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


def _test_3_hyperframes():
    # 3. gen_hyperframes
    print("\n[3] gen_hyperframes")
    from gen_hyperframes import fallback_segments, generate_html
    from _theme import get_default_accent
    manifest = _base_manifest()
    segs = fallback_segments(manifest["sentences"])
    check("fallback chunk size 5", len(segs) == 2
          and all(len(s["sentences"]) <= 5 for s in segs), len(segs))
    check("fallback ids", [s["id"] for s in segs] == ["seg1", "seg2"])
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

    manifest_open = {
        **manifest,
        "segments": [
            {"id": "opening", "title": "AI 日报", "tagline": "", "body": "",
             "accent": default_accent, "sentences": manifest["sentences"][:1]},
            {"id": "news1", "title": "标题一", "tagline": "", "body": "",
             "accent": "#ffd54f", "sentences": manifest["sentences"][1:4]},
        ],
    }
    html_open = generate_html(manifest_open, "audio/combined.wav")
    check("opening agenda auto-generated", 'id="agendalist-' in html_open)
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
        _SKILL_ROOT,
        "config", "template.json"),
        encoding="utf-8"))["layout"]["landscape"]["agenda"]["maxItems"]
    check(f"agenda 超过 maxItems({_ag_cap}) 截断到上限",
          f'id="agendaitem-opening-{_ag_cap - 1}"' in html_many
          and f'id="agendaitem-opening-{_ag_cap}"' not in html_many)
    check(f"closing recap 同受 maxItems({_ag_cap}) 截断",
          f'id="recapchip-closing-{_ag_cap - 1}"' in html_many
          and f'id="recapchip-closing-{_ag_cap}"' not in html_many)
    check("无 '等共 N 条' 封顶提示行", "等共" not in html_many)

    # 开场/收尾页配图（1.5.65）：images.json 写 "opening"/"closing" 键即生效。
    # 横屏（bar）开场预告保留；竖屏（verse）预告/回顾让位于图
    # （_verse_kills_body 对 opening/closing 一并生效，垂直预算装不下并存）。
    manifest_oc = {
        **manifest,
        "segments": [
            {"id": "opening", "title": "开场", "tagline": "", "body": "",
             "accent": default_accent, "sentences": manifest["sentences"][:1]},
            {"id": "news1", "title": "标题一", "tagline": "", "body": "",
             "accent": "#ffd54f", "sentences": manifest["sentences"][1:4]},
        ],
    }
    html_oc = generate_html(manifest_oc, "audio/combined.wav",
                            images={"opening": "images/opening.jpg"})
    check("opening image rendered", 'id="img-opening"' in html_oc)
    check("landscape opening agenda kept with image",
          'id="agendalist-opening"' in html_oc)
    html_ocv = generate_html(manifest_oc, "audio/combined.wav",
                             images={"opening": "images/opening.jpg"},
                             aspect="portrait")
    check("portrait opening image rendered",
          'id="img-opening"' in html_ocv and "images/opening.jpg" in html_ocv)
    check("portrait opening agenda yields to image",
          'id="agendalist-opening"' not in html_ocv
          and 'id="chiprow-opening"' not in html_ocv)
    check("portrait opening has-image class",
          'id="opening" class="clip seg-card has-image"' in html_ocv)
    manifest_oc2 = {
        **manifest,
        "segments": [
            {"id": "news1", "title": "标题一", "tagline": "", "body": "",
             "accent": "#ffd54f", "sentences": manifest["sentences"][1:4]},
            {"id": "closing", "title": "收尾", "tagline": "", "body": "",
             "accent": default_accent, "sentences": manifest["sentences"][4:5]},
        ],
    }
    html_ocv2 = generate_html(manifest_oc2, "audio/combined.wav",
                              images={"closing": "images/closing.jpg"},
                              aspect="portrait")
    check("portrait closing image rendered, recap yields",
          'id="img-closing"' in html_ocv2
          and 'id="chiprow-closing"' not in html_ocv2)


def _test_3b_hyperframes_flow():
    # 3b. flow 自然叙事模式渲染：无编号 badge、无 wipe 扫场、开场预告是
    # 轻量 chips（默认生成，竖排编号目录仍不出现）、closing
    # 无 recap、长句 cue 切多行（手写 manifest 也不带 agenda 键，验证
    # gen 层默认值）
    print("\n[3b] gen_hyperframes flow 模式")
    from gen_hyperframes import generate_html
    from _theme import get_default_accent
    default_accent = get_default_accent()
    manifest = _base_manifest()
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
    manifest_chapters = {
        **manifest,
        "segments": [
            {"id": "opening", "title": "AI 日报", "tagline": "", "body": "",
             "accent": default_accent, "sentences": manifest["sentences"][:1]},
            {"id": "news1", "title": "标题一", "tagline": "", "body": "",
             "accent": "#ffd54f", "sentences": manifest["sentences"][1:4]},
        ],
    }
    html_chapters = generate_html(manifest_chapters, "audio/combined.wav")
    check("chapters: wipe sweep present (control)",
          'tl.set("#twipe"' in html_chapters)
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
          'src="preview.js"' in html_chapters and "__pvUpdate" in html_chapters)
    pj = io.open(os.path.join(SCRIPTS_DIR,
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


def _test_4_env():
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


def _test_5_theme_template():
    # 6. theme + template loading
    print("\n[5] theme + template")
    from _theme import get_accent_palette, get_theme_colors, list_theme_names
    from _template import load_template
    pal = get_accent_palette()
    check("accent palette non-empty, all #hex",
          bool(pal) and all(c.startswith("#") and len(c) == 7 for c in pal),
          pal)
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
    tpl = load_template()
    check("template layout keys", {"landscape", "vertical"} <= set(tpl["layout"]))
    check("vertical body maxWidth set",
          tpl["layout"]["vertical"]["body"].get("maxWidth", 0) > 0)


def _test_6_verify_render():
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


def _test_7_audio_tts():
    # 8. _audio + _tts
    print("\n[7] _audio + _tts")
    from _audio import build_atempo_filter, build_loudnorm_filter
    from _tts import synth_sentence
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

    check("loudnorm filter default",
          build_loudnorm_filter() == "loudnorm=I=-16.0:TP=-1.5:LRA=11",
          build_loudnorm_filter())
    check("loudnorm filter custom",
          build_loudnorm_filter(-14) == "loudnorm=I=-14:TP=-1.5:LRA=11",
          build_loudnorm_filter(-14))

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


def _test_8_contracts():
    # 9. _contracts
    print("\n[8] _contracts")
    from _contracts import (validate_segments_source, validate_timing_manifest,
                            validate_images_json)
    from _voices import is_valid_voice_id
    from build_from_structured import build_parts
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
    check("voice registry valid id",
          is_valid_voice_id("冰糖") and is_valid_voice_id("Mia"))
    check("voice registry rejects unknown", not is_valid_voice_id("fake"))
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


def _test_9_render_watch():
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


def _test_10_search_images():
    # 11. search_images deterministic relevance (no LLM)
    print("\n[10] search_images deterministic relevance")
    from search_images import relevance_score
    check("relevance high on matching snippet",
          relevance_score("OpenAI 发布新一代推理模型",
                          "openai新一代模型o4发布强化学习") >= 60)
    check("relevance low on unrelated snippet",
          relevance_score("国产大模型集体降价", "今日天气晴转多云") < 40)
    check("relevance zero on empty title", relevance_score("", "任意摘要") == 0)


def _test_12_budget():
    # 12. budget（时长预算：估算函数；cmd_estimate 的 stdout 格式不测——
    # 测的是 print 字符串，不是行为）
    print("\n[12] budget")
    from _contracts import estimate_sentence_seconds

    check("estimate_sentence_seconds scales with length",
          estimate_sentence_seconds("一二三四五六七八九十", 5.0, 1.0) == 2.0)
    check("estimate_sentence_seconds scales with speed",
          estimate_sentence_seconds("一二三四五", 5.0, 2.0) == 0.5)


def _test_14_gen_charts():
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
        import tempfile
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


def _test_15_series_split():
    # 16. series split / budget calibrate
    # 这四个都是纯离线逻辑（不依赖真实 TTS/渲染 API），加进来补上上次
    # review 发现的一个真实教训：--on-fail silence 的 resume 标记丢失 bug
    # 是手工构造场景才抓到的，如果当时就有这层覆盖，本该在第一版就被拦住。
    print("\n[15] series.split / budget calibrate")
    import tempfile

    # 16b. series.split parse_sections / plan_episodes
    from series import parse_sections, plan_episodes

    md_doc = ("# 一\n短内容。\n\n# 二\n" + "这句话会重复很多次用来撑长这一节的估算时长。" * 8
             + "\n\n# 三\n短内容。")
    sections = parse_sections(md_doc)
    check("series.split: markdown heading split finds 3 sections",
          len(sections) == 3 and [t for t, _b in sections] == ["一", "二", "三"],
          sections)

    no_heading_doc = "第一段内容在这里。\n\n第二段内容在这里，完全不同的话题。"
    sections2 = parse_sections(no_heading_doc)
    check("series.split: no-heading doc falls back to blank-line blocks",
          len(sections2) == 2, sections2)

    try:
        parse_sections("   \n\n  ")
        check("series.split: blank doc raises ValueError", False)
    except ValueError:
        check("series.split: blank doc raises ValueError", True)

    episodes = plan_episodes(sections, target_seconds=8.0)
    # 核心不变量：只在小节边界切，每个原始小节必须完整出现在恰好一集里，
    # 不允许被拆开、也不允许丢失或重复。
    flat_titles = [t for ep in episodes for t, _b, _d in ep]
    check("series.split: target_seconds mode never splits a section",
          flat_titles == ["一", "二", "三"], flat_titles)
    check("series.split: long section 二 gets its own episode",
          any(len(ep) == 1 and ep[0][0] == "二" for ep in episodes), episodes)

    episodes_n = plan_episodes(sections, n_episodes=2)
    check("series.split: n_episodes mode respects requested count",
          len(episodes_n) == 2, len(episodes_n))
    flat_titles_n = [t for ep in episodes_n for t, _b, _d in ep]
    check("series.split: n_episodes mode also never splits a section",
          sorted(flat_titles_n) == sorted(["一", "二", "三"]), flat_titles_n)

    # 16g. split_series: _series_meta 只在多集时附加，且 prev/next 链接正确
    from series import write_skeleton
    with tempfile.TemporaryDirectory() as td:
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
        check("series.split: episode 1 has no prev, links to next",
              meta_a["prev_episode_title"] is None and meta_a["next_episode_title"] == "集B",
              meta_a)
        check("series.split: episode 2 links back to prev, no next",
              meta_b["prev_episode_title"] == "集A" and meta_b["next_episode_title"] is None,
              meta_b)

        out_single = os.path.join(td, "single.json")
        write_skeleton(ep_a, out_single, episode_index=None, total_episodes=1)
        with open(out_single, encoding="utf-8") as f:
            check("series.split: single-episode skeleton has no _series_meta",
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

        # 17g. vertical + flow（竖屏 flow 模式开场封面图默认必配，1.5.67）：
        # flow manifest 缺 opening 键 → 进 missing；写上后不再缺；无 flow
        # （章节模式）→ 开场保持目录、无配图义务；manifest 没有 opening 段
        # （自定义手写 manifest）→ 没有页面就没有配图义务。
        flow_path = os.path.join(td, "flow_manifest.json")
        with open(flow_path, "w", encoding="utf-8") as f:
            json.dump({
                "total_duration": 5.0, "flow": True,
                "sentences": [_sent(i, f"第{i}句。") for i in range(5)],
                "segments": [
                    {"id": "opening", "title": "本期内容",
                     "sentences": [_sent(0, "开场白。")]},
                    {"id": "news1", "title": "第一条",
                     "sentences": [_sent(1, "第一条内容。")]},
                ],
            }, f, ensure_ascii=False)
        sids, missing = _image_coverage(flow_path, images_json, vertical=True)
        check("run._image_coverage: vertical+flow -> opening counted as missing",
              sids == ["news1", "opening"] and missing == ["opening"],
              (sids, missing))

        with open(images_json, "w", encoding="utf-8") as f:
            json.dump({"news1": "images/news1.png",
                       "opening": "images/opening.png"}, f, ensure_ascii=False)
        sids, missing = _image_coverage(flow_path, images_json, vertical=True)
        check("run._image_coverage: vertical+flow + opening mapped -> no missing",
              sids == ["news1", "opening"] and missing == [], (sids, missing))

        # 章节模式（manifest 无 flow 字段）：开场保持目录，无配图义务。
        # images.json 先恢复全配齐——上一条断言刚把它改写成只含 news1+opening，
        # 残留状态会让本条的 missing 混进无关段落（fixture 状态污染）。
        with open(images_json, "w", encoding="utf-8") as f:
            json.dump({"news1": "images/news1.png", "news2": "images/news2.png",
                       "seg1": "images/seg1.png"}, f, ensure_ascii=False)
        sids, missing = _image_coverage(manifest_path, images_json, vertical=True)
        check("run._image_coverage: vertical chapter mode -> opening not required",
              sids == ["news1", "news2", "seg1"] and missing == [], (sids, missing))

        no_open_path = os.path.join(td, "no_opening.json")
        with open(no_open_path, "w", encoding="utf-8") as f:
            json.dump({
                "total_duration": 2.0, "flow": True,
                "sentences": [_sent(0, "第一句。"), _sent(1, "第二句。")],
                "segments": [{"id": "seg1", "title": "讲解",
                              "sentences": [_sent(0, "第一句。")]}],
            }, f, ensure_ascii=False)
        with open(images_json, "w", encoding="utf-8") as f:
            json.dump({"seg1": "images/seg1.png"}, f, ensure_ascii=False)
        sids, missing = _image_coverage(no_open_path, images_json, vertical=True)
        check("run._image_coverage: vertical+flow but no opening segment -> not required",
              sids == ["seg1"] and missing == [], (sids, missing))

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

    # 17e2. 竖屏 flow 模式开场封面图默认必配判定（1.5.67）：portrait/both
    # + flow 无 opening 即缺；有 opening 键即满足；landscape 可选；竖屏
    # 章节模式（flow=False）开场保持目录，无配图义务。
    from gen_hyperframes import _opening_cover_missing
    check("gen_hyperframes._opening_cover_missing: portrait/both+flow without opening -> True",
          _opening_cover_missing({}, "portrait", True)
          and _opening_cover_missing({}, "both", True))
    check("gen_hyperframes._opening_cover_missing: portrait+flow with opening -> False",
          not _opening_cover_missing({"opening": "images/opening.png"},
                                     "portrait", True))
    check("gen_hyperframes._opening_cover_missing: landscape+flow opening stays optional",
          not _opening_cover_missing({}, "landscape", True))
    check("gen_hyperframes._opening_cover_missing: portrait chapter mode (no flow) -> not required",
          not _opening_cover_missing({}, "portrait", False))

    # 17e3. 模式预设（template 定义、gen/run 生产，1.5.69）：modes 块两个
    # 预设齐全且五键取值符合语义；选择逻辑 flow→flow / 无 flow→chapter；
    # 坏定义（预设缺失/缺键/枚举域外）响亮报错不静默回退。
    import _template as _tpl_mod
    from _template import get_mode_preset, load_template
    _modes = load_template()["modes"]
    check("template modes: chapter/flow presets both defined",
          set(_modes) == {"chapter", "flow"}, sorted(_modes))
    check("template modes: flow preset flag values",
          _modes["flow"] == {"numbered": False, "openingPreview": "chips",
                             "closingRecap": False, "transition": "crossfade",
                             "verticalOpeningCover": True}, _modes["flow"])
    check("template modes: chapter preset flag values",
          _modes["chapter"] == {"numbered": True, "openingPreview": "agenda",
                                "closingRecap": True, "transition": "wipe",
                                "verticalOpeningCover": False},
          _modes["chapter"])
    check("get_mode_preset: manifest flow=true -> flow preset",
          get_mode_preset({"flow": True}) is _modes["flow"])
    check("get_mode_preset: manifest without flow -> chapter preset",
          get_mode_preset({}) is _modes["chapter"])
    for _bad_tpl, _why in (
        ({"modes": {}}, "missing preset"),
        ({"modes": {"flow": {"numbered": False}}}, "missing flags"),
        ({"modes": {"flow": {"numbered": False, "openingPreview": "wrong",
                             "closingRecap": False, "transition": "crossfade",
                             "verticalOpeningCover": True}}}, "bad enum"),
    ):
        _tpl_mod._TEMPLATE_CACHE["default"] = _bad_tpl
        try:
            get_mode_preset({"flow": True})
            check(f"get_mode_preset: {_why} rejected", False, "no raise")
        except ValueError:
            check(f"get_mode_preset: {_why} rejected", True, None)
    _tpl_mod._TEMPLATE_CACHE.clear()

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


def _test_17f_review_fix():
    # ── 17f. 外部 review 修复的回归防线 ──────────────────────────────
    # 这组断言固化 2026-09 那轮深度 review 修掉的缺陷。每条都对应一个
    # 真实故障场景，不是"看着该有"的形式化检查。
    print("\n[17f] review-fix regression guards")
    _skill_root = _SKILL_ROOT

    def _src(name):
        with open(os.path.join(_skill_root, "scripts", name),
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


def _test_18_anti_regression():
    # ── 18. 防回归断言（源码级固化）────────────────────────
    print("\n[18] anti-regression")
    _skill_root = _SKILL_ROOT

    def _read_src(rel):
        with open(os.path.join(_skill_root, rel), encoding="utf-8") as f:
            return f.read()

    import re as _re

    # 18a. 默认值单一来源：DEFAULT_SPEED / OPENING_CLOSING_DEFAULT_SPEED
    _contracts_src = _read_src("scripts/_contracts.py")
    check("DEFAULT_SPEED defined in _contracts", "DEFAULT_SPEED = 1.5" in _contracts_src)
    check("OPENING_CLOSING_DEFAULT_SPEED defined in _contracts",
          "OPENING_CLOSING_DEFAULT_SPEED = 1.2" in _contracts_src)
    for _f in ("scripts/pipeline.py", "scripts/budget.py", "scripts/series.py"):
        _s = _read_src(_f)
        _base = os.path.basename(_f)
        check(f"{_base} uses DEFAULT_SPEED", "DEFAULT_SPEED" in _s)
        check(f"{_base} has no speed default=1.0 drift",
              _re.search(r'"--speed"[^)]*default=1\.0', _s, _re.S) is None)
    check("build_from_structured uses OPENING_CLOSING_DEFAULT_SPEED",
          "OPENING_CLOSING_DEFAULT_SPEED" in _read_src("scripts/build_from_structured.py"))

    # 18b. 模板字段消费完整性（layout 叶子字段必须被 gen_hyperframes 引用，
    # 防 chip.padding/numSize 这类"改模板不生效"的死字段复发）
    _gh_src = _read_src("scripts/gen_hyperframes.py")
    if _TPL is not None:
        _lay_keys = set()
        for _aspect in ("landscape", "vertical"):
            for _blk_cfg in _TPL["layout"][_aspect].values():
                _lay_keys.update(_blk_cfg.keys())
        _dead = [k for k in sorted(_lay_keys) if ('"%s"' % k) not in _gh_src]
        check("template layout keys all consumed by gen_hyperframes",
              not _dead, f"dead keys: {_dead}")
        # agenda 字号收缩刻度必须有序（shrinkThreshold <= shrinkMax，
        # 相等 = 到达 shrinkMax 前不收缩，如竖屏模板 8/8），
        # 否则线性插值区间为负、字号语义失效
        _sm_mono = all(
            _TPL["layout"][a]["agenda"]["shrinkThreshold"]
            <= _TPL["layout"][a]["agenda"]["shrinkMax"]
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


def _test_19_wcag():
    # ── 19. WCAG 对比度全组合断言─────────────────────────
    # tagline 色（浅色主题 _darken(accent) / 深色主题 _mix(accent,white,0.62)）
    # 对全调色板 × 全主题背景渐变最坏段的对比度。tagline 字号横竖屏
    # 44/36px（≥24px 属 WCAG 大字），浅色按大字 AA 3.0 卡；深色提亮后
    # 余量大（实测 10+）按正文 AA 4.5 卡。调色板/主题/混色函数任何一处
    # 改动，这里自动重新全组合验证。
    print("\n[19] WCAG contrast (tagline x palette x themes)")
    import re as _re
    from _theme import (darken as _darken, hex_to_rgb01 as _hex_to_rgb01,
                        relative_luminance as _relative_luminance,
                        mix as _mix)
    from _theme import get_theme_colors, get_accent_palette, list_theme_names

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


def _test_20_series_check():
    # ── 20. series.check find_drifts（系列一致性纯函数）────────────
    # ── 20a. main() 内不得局部 import math（真回归）────────
    # ── 20b. 竖屏 cue 行宽必须按画幅收紧────────────────
    # ── 20c. 竖屏 verse 句子流结构与滚动参照系 ───────────────
    # ── 20c2. 横屏图片框 √2:1────────────────────
    # ── 20c3. 横屏配图段内容框独立于标题框 ─────────────────────────
    # ── 20d. bar 形态（经典形式）：横屏固定模式 ─────────────────
    # ── 20e. portrait 紧凑竖屏（3:4，1080x1440）─────────────────
    print("\n[20] series.check find_drifts")
    from series import find_drifts
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

    # ── 20b. 竖屏 cue 行宽必须按画幅收紧────────────────
    # 竖屏 verse 物理行宽 ≈22 字（980px/40px）。若调用点回退到横屏默认
    # （28 字/行）也会超物理宽度。改成行为断言：
    # 直接问共用函数要参数，再校验两个调用点确实接了这份参数。
    # （旧 slack 参数已从 subtitle_params_for 移除——现在只校验 max_chars/hard_cap）
    from _script_utils import subtitle_params_for as _sub_params
    _vp = _sub_params("vertical")
    _lp = _sub_params("landscape")
    check("vertical subtitle uses aspect-strict cap (22/22)",
          (_vp["max_chars"], _vp["hard_cap"]) == (22, 22)
          and (_lp["max_chars"], _lp["hard_cap"]) == (28, 34),
          (_vp, _lp))

    # 消费者（gen_hyperframes）必须从 subtitle_params_for 取参数——切行
    # 参数唯一权威来源的回归防线，比逐行 grep 数值更抗重构。
    with open(os.path.join(_SKILL_ROOT, "scripts", "gen_hyperframes.py"),
              encoding="utf-8") as _gf:
        _gen_src = _gf.read()
    check("gen_hyperframes 从 subtitle_params_for 取切分参数",
          "subtitle_params_for(" in _gen_src)

    # ── 20c. 竖屏 verse 句子流结构与滚动参照系 ───────────────
    import gen_hyperframes as _gh2
    if _TPL is None:
        return
    _tpl = _TPL
    _tpl_v = _tpl["layout"]["vertical"]
    _v_pad_t = _tpl_v["segCard"]["padding"]
    _v_ar_t = _tpl_v["image"]["aspect"]
    _v_mt_t = _tpl_v["image"]["marginTop"]
    _v_ms_t = _tpl_v["image"].get("marginSide", 0)
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
          "aspect + margin-top + margin-side)",
          f'padding:{_v_pad_t}!important' in _vhtml
          and f'height:auto!important;aspect-ratio:{_v_ar_t}!important' in _vhtml
          and f'flex:0 1 auto!important;margin:{_v_mt_t}px 0 0 -{_v_ms_t}px!important' in _vhtml
          and f'width:calc(100% + {_v_ms_t * 2}px)!important' in _vhtml)
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
    # 槽 910×644（左右各距屏 25px、910/√2  644）：4:3 生图 contain
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
    # 复用 20c 的默认横屏产物 _lhtml（同一调用，不重复生成）
    check("bar mode: sub-bar DOM present, verse DOM absent",
          'class="sub-bar"' in _lhtml and 'class="verse"' not in _lhtml)
    check("bar mode: body cards rendered (no verse replacement)",
          '.sub-speaker{' in _lhtml and 'subEls' in _lhtml)

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
    check("portrait verse: sub-bar absent (vertical family is verse-only)",
          'class="verse"' in _phtml
          and 'class="sub-bar"' not in _phtml)


def _test_20c_manifest():
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


def _test_21_wcag_ext():
    # ── 21. WCAG 扩展：字幕层/说话人/序号圆/正文层────────
    # [19] 只覆盖 tagline；这里把渲染 HTML 里其余"文字叠底色"色对全部
    # 锁住。字号依据：字幕 46/40px、说话人 =字幕一半 23/20px bold、
    # 序号 =numSize 一半 30/26px bold、正文 44/38px——全部 ≥ WCAG 大字
    # 标准（bold ≥18.66px），按大字 AA 3.0 卡。说话人色组与序号数字色
    # 引用 gen_hyperframes 模块常量（单一数据源，改色自动跟进断言）。
    print("\n[21] WCAG extended (sub/speaker/agenda-num/body)")
    import re as _re
    from _theme import get_theme_colors, get_accent_palette, list_theme_names

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
    _hex6 = _re.compile(r"#[0-9a-fA-F]{6}")
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
        # 21b 说话人色 vs 字幕栏（复刻 generate_html 的明暗分组选择）
        _grp = (_gh19.SPK_COLORS_ON_LIGHT
                if 0.299 * _sub[0] + 0.587 * _sub[1] + 0.114 * _sub[2] > 0.5
                else _gh19.SPK_COLORS_ON_DARK)
        for _c in _grp:
            _r = _cr01(_hex01(_c), _sub)
            check(f"speaker {_c} on sub_bar [{_tname}] >= 3.0",
                  _r >= 3.0, f"{_r:.2f}")
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
        else:
            check(f"body text on card [{_tname}] parse ok", False,
                  "rgba parse fail")

    # 21c agenda 序号数字 vs accent 圆底（8 色 × 固定数字色）
    for _ac in get_accent_palette():
        _r = _cr01(_hex01(_gh19.AGENDA_NUM_TEXT_COLOR), _hex01(_ac))
        check(f"agenda num on {_ac} >= 3.0", _r >= 3.0, f"{_r:.2f}")


def _test_22_review_fix():
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
    with open(os.path.join(SCRIPTS_DIR, "pipeline.py"), encoding="utf-8") as _f22:
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
    with open(os.path.join(SCRIPTS_DIR, "_audio.py"), encoding="utf-8") as _f22:
        _audio22 = _f22.read()
    check("mix_bgm: amix normalize=0 (voice not halved)",
          "amix=inputs=2" in _audio22 and "normalize=0" in _audio22)

    # 22e run.py --aspect both 渲染两个成片并分别校验（只渲染横屏的话
    # 竖屏 HTML 白做）
    with open(os.path.join(SCRIPTS_DIR, "run.py"), encoding="utf-8") as _f22:
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
    import gen_hyperframes as _gh22f
    manifest_basic = _base_manifest()
    _verse_dom_html = _gh22f.generate_html(manifest_basic, "audio/combined.wav",
                                           width=1080, height=1440,
                                           aspect="portrait")
    long_cue_manifest = {
        "sentences": [
            {"index": 0, "text": "这是一个特别长的句子，包含很多修饰成分和并列信息、还有顿号列举，"
                                "让观众一口气读完会很累。", "start_time": 0.0, "duration": 5.0},
        ],
        "total_duration": 5.0,
    }
    html_long = _gh22f.generate_html(long_cue_manifest, "audio/combined.wav")
    check("verse mode: sub-bar DOM/JS logic absent",
          "rows[r].style.opacity" not in _verse_dom_html
          and "subEls" not in _verse_dom_html
          and ".sub-text{" not in _verse_dom_html)
    check("bar mode: sub-bar row stagger opacity logic present",
          "rows[r].style.opacity = lop;" in html_long
          and "lop * op" not in html_long)


def _test_23_review_fix_guards():
    # ── 23. 本轮修复的守护断言（英文断句 / .env 编码 / wrap_numbers /
    # TTS fail-fast / WAV 样本精确时长 / sid+accent 契约）────────────
    print("\n[23] review-fix guards")
    import tempfile

    # 23a 英文句终符：中英混合稿的英文句子能被切开，小数/缩写/省略号/
    # 人名首字母不被误切；纯中文行为不变
    from _script_utils import split_sentences
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

    # 23c wrap_numbers：esc() 产生的数字字符引用（&#39;）整条跳过不被误包，
    # 正常文本里的数字照常包裹
    import re as _re23
    from gen_hyperframes import esc, wrap_numbers
    _acc = "#4fc3f7"
    _esc_src = esc("Apple's 5G chip costs $399")
    wrapped = wrap_numbers(_esc_src, _acc)
    # 不变量一：实体内部不得插入任何标签（`&#` 到 `;` 之间不能出现 `<`）。
    # 注意"剥掉 span 后与原文本相等"是**无效**不变量——插标签不改变字符
    # 序列，只改变浏览器看到的 token 划分，剥完照样相等。
    check("wrap_numbers skips numeric char refs",
          "&#39;" in wrapped
          and _re23.search(r"&#[^;]*<", wrapped) is None
          and "<span" in wrapped, wrapped)
    # 不变量二：esc() 产出的每个数字实体在产物中出现次数不变（旧 bug 下
    # `&#39;` 被拆成 `&#3<…>9</span>;`，计数从 1 掉到 0）
    _ents = _re23.findall(r"&#\d+;", _esc_src)
    check("wrap_numbers 不拆开 HTML 实体（每个 &#N; 出现次数不变）",
          bool(_ents) and all(wrapped.count(e) == _esc_src.count(e)
                              for e in _ents),
          f"{_ents} -> {wrapped}")
    check("wrap_numbers still wraps plain numbers",
          '<span class="num-accent"' in wrap_numbers("增长15亿", _acc))

    # 23d _tts 无音频响应 fail-fast：BadAudioResponseError 归为确定性失败
    from _tts import BadAudioResponseError, _is_non_retryable, synth_sentence
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
          _wav_duration(os.path.join(SCRIPTS_DIR, "preview.js")) is None)

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
        with open(os.path.join(SCRIPTS_DIR, "run.py"), encoding="utf-8") as f:
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


def _test_25_gating():
    # 25. 测试基建自身的确定性部分（_assets GSAP 缓存 + 集成层纯函数）。
    # 集成层的真子进程/真 ffmpeg/run.py 编排部分在 main() 第二段整跑。
    print("\n[25] 测试基建（_assets / 集成层纯函数）")

    import urllib.request as _urlreq25
    import _assets as _AS25

    # ── run_eval.py：只测不需要真实 ffmpeg / 网络的确定性部分 ──
    check("run_eval: segments 条数在区间内判过",
          check_segment_count({"segments": [{}] * 6}) == (True, 6))
    check("run_eval: 条数低于下限判不过（含边界 4）",
          check_segment_count({"segments": [{}] * 4}) == (False, 4))
    check("run_eval: 条数高于上限判不过（含边界 9）",
          check_segment_count({"segments": [{}] * 9}) == (False, 9))
    check("run_eval: 空 segments 判不过且如实报 0",
          check_segment_count({}) == (False, 0))
    check("run_eval: 区间可自定义",
          check_segment_count({"segments": [{}] * 3},
                                    expect_range=(3, 3)) == (True, 3))

    import tempfile as _tf25
    # 静音包装：run_eval 的 check/skip 会直接 print，测试里不想刷屏
    import contextlib as _cl25
    import io as _io25

    def _quiet25(fn, *a, **k):
        with _cl25.redirect_stdout(_io25.StringIO()):
            return fn(*a, **k)

    # ev_check/ev_skip 写的是模块级 EV_RESULTS/EV_SKIPPED，测完必须还原
    _ores25, _oskip25 = EV_RESULTS[:], EV_SKIPPED[:]
    try:
        EV_RESULTS.clear()
        EV_SKIPPED.clear()
        _quiet25(ev_check, "x", True)
        _quiet25(ev_check, "y", False, "细节")
        _quiet25(ev_skip, "z", "本机缺编码器")
        check("run_eval: check/skip 分得清通过、失败与环境跳过",
              EV_RESULTS == [("x", True), ("y", False)]
              and EV_SKIPPED == [("z", "本机缺编码器")],
              (EV_RESULTS, EV_SKIPPED))
    finally:
        EV_RESULTS[:] = _ores25
        EV_SKIPPED[:] = _oskip25

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
              pick_h264_encoder(_fake_ffmpeg25(
                  " V..... libopenh264  OpenH264 H.264 encoder ", _td25))
              == "libopenh264")
        check("run_eval: 多个可用时按优先级取 libx264",
              pick_h264_encoder(_fake_ffmpeg25(
                  " V..... libopenh264  OpenH264 \n"
                  " V..... libx264  libx264 H.264 ", _td25)) == "libx264")
        check("run_eval: 一个 h264 都没有时返回 None（应 SKIP 而非 FAIL）",
              pick_h264_encoder(_fake_ffmpeg25(
                  " V..... vp9  VP9 encoder ", _td25)) is None)
        check("run_eval: 拿不到清单时退回 libx264（让真报错如实冒出来）",
              pick_h264_encoder(os.path.join(_td25, "not-exists"))
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
    try:
        with _tf25.TemporaryDirectory() as _td25:
            # 缓存路径走参数注入（_assets 的模块全局保持只读，不再改写）
            _cache25 = os.path.join(_td25, "cache")
            _cpath25 = os.path.join(_cache25, "gsap.min.js")
            _legacy25 = os.path.join(_td25, "legacy", "gsap.min.js")

            def _gsap25(out_dir, **_kw):
                return _AS25.ensure_local_gsap(
                    out_dir, cache_dir=_cache25, cache_path=_cpath25,
                    legacy_cache_path=_legacy25, **_kw)

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
                  _gsap25(os.path.join(_td25, "out1")) == "vendor/gsap.min.js")

            # ② 下载内容过小（CDN 返回错误页）→ 拒绝，不留半成品
            _urlreq25.urlopen = lambda *_a, **_k: _FakeResp25(
                b"<html>404</html>")
            _r_small = _gsap25(os.path.join(_td25, "out2"), timeout=1)
            _tmps25 = [n for n in os.listdir(_cache25) if n.endswith(".tmp")] \
                if os.path.isdir(_cache25) else []
            check("_assets: 下载内容 <1000 字节判为无效（不写入缓存）",
                  _r_small is None and not os.path.isfile(_cpath25))
            check("_assets: 下载失败后不留 .tmp 残留",
                  not _tmps25, _tmps25)

            # ③ 无缓存且网络不可用 → None，由调用方回退 CDN（尽力而为）
            _urlreq25.urlopen = _boom25
            check("_assets: 无缓存且下载失败时返回 None（调用方回退 CDN）",
                  _gsap25(os.path.join(_td25, "out3"), timeout=1) is None)

            # ④ 缓存命中 → 拷到输出目录
            os.makedirs(_cache25, exist_ok=True)
            with open(_cpath25, "w", encoding="utf-8") as _f25:
                _f25.write("/* cached gsap */" * 300)
            _out4 = os.path.join(_td25, "out4")
            check("_assets: 缓存命中时拷贝到输出目录",
                  _gsap25(_out4) == "vendor/gsap.min.js"
                  and os.path.getsize(os.path.join(_out4, "vendor",
                                                   "gsap.min.js")) > 1000)

            # ⑤ 旧路径缓存迁移：老用户不必重新联网下载一次
            os.remove(_cpath25)
            os.makedirs(os.path.dirname(_legacy25))
            with open(_legacy25, "w", encoding="utf-8") as _f25:
                _f25.write("/* legacy gsap */" * 300)
            _urlreq25.urlopen = _boom25      # 迁移成功就不该走到联网
            _out5 = os.path.join(_td25, "out5")
            check("_assets: 旧缓存路径会被迁移过来（迁移成功就不联网）",
                  _gsap25(_out5) == "vendor/gsap.min.js"
                  and os.path.isfile(_cpath25))
    finally:
        _urlreq25.urlopen = _ouo25



# ═══════════════════ 第二段：离线集成（原 dev/run_eval.py，1.5.74 并入）═══════════════════
# 与上面断言层的分工：这里跑真子进程/真 ffmpeg/run.py 编排，抓跨脚本 CLI 胶水
# 断裂。ev_check/ev_skip 写模块级 EV_RESULTS/EV_SKIPPED，由 main() 统一汇总。
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile


from _ffmpeg import get_ffmpeg  # noqa: E402
from _audio import generate_silence, concat_audio, measure_duration  # noqa: E402
from _script_utils import split_sentences  # noqa: E402
from _contracts import (validate_segments_source, estimate_sentence_seconds,  # noqa: E402
                        DEFAULT_CHARS_PER_SEC)

EV_RESULTS = []
# 环境缺能力（而非本技能有回归）时记到这里：不计入 pass/fail，不把环境问题
# 伪装成失败，也不伪装成通过——维护者一眼能分辨"没跑"和"跑了没过"。
EV_SKIPPED = []


def ev_check(name, cond, detail=""):
    EV_RESULTS.append((name, bool(cond)))
    mark = "OK" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))
    return cond


def ev_skip(name, reason):
    EV_SKIPPED.append((name, reason))
    print(f"  [SKIP] {name} — {reason}")


# ── segments 条数在 5-8 条范围内的机械检查 ──────────────────────────────
# 纯计数不需要语义判断；真正"该不该挑这 5-8 条"是语义判断，由 agent 在
# 写稿阶段完成（SKILL.md 第 1 步选材标准），这里不重复。
def check_segment_count(source, expect_range=(5, 8)):
    n = len(source.get("segments", []))
    lo, hi = expect_range
    return lo <= n <= hi, n


# ── 构造一份字段结构与 pipeline.py 真实产出完全一致的 timing_manifest.json，
# 只是用静音代替 TTS 配音——省下真实 TTS API 调用，但走的是同一套
# generate_silence/concat_audio/measure_duration（pipeline.py 自己也用这三个
# 函数），不是另起一套 mock 逻辑，跟真实产物的字段/类型不会跑偏。
def build_fake_manifest(source, out_dir, gap=0.3, chars_per_sec=DEFAULT_CHARS_PER_SEC):
    ffmpeg_path = get_ffmpeg()
    sentences_dir = os.path.join(out_dir, "sentences")
    os.makedirs(sentences_dir, exist_ok=True)

    manifest_sentences = []
    grouped_segments = []
    idx = 0
    audio_files = []

    def emit_block(text, speed):
        nonlocal idx
        out = []
        for s in split_sentences(text):
            dur = round(estimate_sentence_seconds(s, chars_per_sec, speed or 1.0), 3)
            wav_path = os.path.join(sentences_dir, f"s{idx:04d}.wav")
            generate_silence(ffmpeg_path, max(dur, 0.05), wav_path)
            audio_files.append(wav_path)
            entry = {"index": idx, "text": s, "start_time": 0.0, "duration": dur}
            manifest_sentences.append(entry)
            out.append(entry)
            idx += 1
        return out

    emit_block(source.get("opening", ""), source.get("opening_speed"))
    for i, seg in enumerate(source.get("segments", []), 1):
        seg_sents = emit_block(seg.get("text", ""), seg.get("speed"))
        grouped_segments.append({
            "id": seg.get("id", f"news{i}"),
            "title": seg.get("title", f"segment{i}"),
            "tagline": seg.get("tagline") or "补充阅读",
            "body": seg.get("body", ""),
            "accent": seg.get("accent", "#3b82f6"),
            "sentences": seg_sents,
        })
    emit_block(source.get("closing", ""), source.get("closing_speed"))

    if not audio_files:
        raise ValueError("没有任何句子，无法构造 manifest（segments 全空？）")

    combined_path = os.path.join(out_dir, "combined.wav")
    if not concat_audio(ffmpeg_path, audio_files, gap, combined_path):
        raise RuntimeError("concat_audio 失败——检查 ffmpeg 是否可用")
    total_dur = measure_duration(ffmpeg_path, combined_path)

    cumulative = 0.0
    for i, sd in enumerate(manifest_sentences):
        sd["start_time"] = round(cumulative, 3)
        cumulative += sd["duration"]
        if i < len(manifest_sentences) - 1:
            cumulative += gap

    manifest = {
        "sentences": manifest_sentences,
        "total_duration": round(total_dur, 3),
        "gap": gap,
        "voice_id": "mock-silence",
        "combined_audio": os.path.abspath(combined_path),
        "segments": grouped_segments,
    }
    manifest_path = os.path.join(out_dir, "timing_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest_path, manifest


# ── 不经过 Hyperframes/Chrome，直接用 ffmpeg
# 合成一段时长匹配、编码为 H.264+AAC 的黑屏测试视频，喂给 verify_render.py。
# 校验的是 verify_render.py 的判断逻辑本身，不是真实画面内容——画面对不对
# 是语义/视觉层，仍然需要人看，这里不冒充能测。
def pick_h264_encoder(ffmpeg_path):
    """挑一个本机可用的 H.264 编码器，返回编码器名；一个都没有时返回 None。

    写死 libx264 会让"ffmpeg 能跑但不含 libx264"这类环境（minimal 构建、
    部分 Docker/conda 包）把环境限制报成本技能回归。这里按可用性降序回退，
    拿不到编码器列表时退回 libx264（让真正的报错从合成那一步如实冒出来）。
    """
    try:
        r = subprocess.run([ffmpeg_path, "-hide_banner", "-encoders"],
                           capture_output=True, text=True, timeout=30)
        encoders = r.stdout or ""
    except Exception:
        return "libx264"
    for enc in ("libx264", "libopenh264", "h264_nvenc", "h264_qsv",
                "h264_vulkan", "h264_vaapi", "h264_amf", "h264_v4l2m2m"):
        if f" {enc} " in encoders:
            return enc
    return None


def build_fake_render(ffmpeg_path, audio_path, duration, out_path):
    """返回 True=合成成功 / False=脚本链路问题 / None=环境缺编码器（应 SKIP）。

    失败时把 ffmpeg 的 stderr 尾部打出来：吞掉报错的话，维护者只能看到一行
    "[FAIL] 测试视频合成成功"，无从判断是回归还是本机 ffmpeg 少编码器。
    """
    enc = pick_h264_encoder(ffmpeg_path)
    if enc is None:
        return None
    r = subprocess.run([
        ffmpeg_path, "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=320x240:d={duration}",
        "-i", audio_path,
        "-c:v", enc, "-c:a", "aac",
        "-shortest", out_path,
    ], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60)
    if r.returncode != 0 or not os.path.isfile(out_path):
        print(f"      合成失败（编码器 {enc}，退出码 {r.returncode}）：", flush=True)
        for line in (r.stderr or "").strip().splitlines()[-5:]:
            print(f"        {line}", flush=True)
        return False
    return True


FIXTURE = {
    "opening": "大家好，欢迎收看今天的AI日报。",
    "closing": "感谢收看，我们明天见。",
    "segments": [
        {"title": "A公司发布新模型", "text": "第一条内容简介。这里补一句细节。"},
        {"title": "B公司完成新一轮融资", "text": "第二条内容简介。"},
        {"title": "C团队公布研究成果", "text": "第三条内容简介。这里也补一句。"},
        {"title": "D平台上线新功能", "text": "第四条内容简介。"},
        {"title": "E机构发布行业报告", "text": "第五条内容简介。"},
    ],
}


def ev_main():
    print("=== 集成层：离线集成测试（不调用真实 API，不需要 Chrome）===\n")

    print("[1] segments_source.json 结构校验 + 条数机械检查")
    source = validate_segments_source(FIXTURE)
    ok, n = check_segment_count(source)
    ev_check("segments 条数落在 5-8 条范围内", ok, f"实际 {n} 条")

    with tempfile.TemporaryDirectory() as td:
        print("\n[2] 构造 manifest（静音代替 TTS 配音，复用 pipeline.py 同款 _audio.py 函数）")
        try:
            manifest_path, manifest = build_fake_manifest(source, td)
            ev_check("timing_manifest.json 写出成功", os.path.isfile(manifest_path))
            ev_check("total_duration 是正数", manifest["total_duration"] > 0,
                  str(manifest["total_duration"]))
            ev_check("segments 分组数与稿件一致",
                  len(manifest.get("segments", [])) == len(source["segments"]))
        except Exception as e:
            ev_check("manifest 构造未抛异常", False, str(e))
            return

        print("\n[3] gen_hyperframes.py 生成 HTML（真子进程，真实调用）")
        html_path = os.path.join(td, "index.html")
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "gen_hyperframes.py"),
                             "-m", manifest_path, "-o", html_path],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        ev_check("gen_hyperframes.py 退出码 0", r.returncode == 0, r.stderr[-500:])
        ev_check("index.html 已生成", os.path.isfile(html_path))
        if os.path.isfile(html_path):
            with open(html_path, encoding="utf-8") as f:
                html = f.read()
            for seg in source["segments"]:
                ev_check(f"HTML 包含标题「{seg['title']}」", seg["title"] in html)

        print("\n[5] 合成测试用 mp4（纯 ffmpeg，不经过 Hyperframes/Chrome）+ verify_render.py 校验")
        ffmpeg_path = get_ffmpeg()
        fake_mp4 = os.path.join(td, "fake_render.mp4")
        built = build_fake_render(ffmpeg_path, manifest["combined_audio"],
                                   manifest["total_duration"], fake_mp4)
        if built is None:
            # 维度 (1)(2) 要求成片是 H.264，本机 ffmpeg 一个 H.264 编码器
            # 都没有时无从合成——这是环境限制，不是本技能回归，按 SKIP 处理。
            ev_skip("测试视频合成（含 verify_render 校验）",
                 "本机 ffmpeg 不含任何 H.264 编码器"
                 "（libx264/libopenh264/h264_nvenc…），换一个完整 ffmpeg 后自动恢复")
        else:
            ev_check("测试视频合成成功", built)
            if built:
                r = subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "verify_render.py"),
                                     "-f", fake_mp4, "-m", manifest_path],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace")
                ev_check("verify_render.py 判定通过（时长匹配 + H.264/AAC）",
                      r.returncode == 0, (r.stdout + r.stderr)[-500:])

    print("\n[6] run.py 第3/4步并行编排（假脚本代替真 TTS/配图 API，验证真并发+失败传播）")
    check_run_parallel_orchestration()

    print("\n[7] gen_hyperframes.py 配图引用完整性 fail-fast（缺失/损坏/正常三种情况）")
    check_image_integrity_validation()

    print("\n[8] run.py 缺图提示改为逐张独立审阅（列出候选原图全路径，不再生成拼图）")
    check_missing_image_independent_review_hint()



# ── gen_hyperframes.py 的配图引用校验（缺失文件见 5.6.1，损坏/截断文件
# 同测）：该校验逻辑在 main() 的 CLI 参数解析分支里，
# 不是独立可 import 的函数，所以用真子进程调用来测，跟 [3]/[4] 同样的方式。
def check_image_integrity_validation():
    with tempfile.TemporaryDirectory() as td:
        manifest_path, manifest = build_fake_manifest(
            {"opening": "开场。", "closing": "结尾。",
             "segments": [{"title": "t1", "tagline": "tag1", "text": "内容一。"}]},
            td)
        html_path = os.path.join(td, "index.html")
        img_dir = os.path.join(td, "images")
        os.makedirs(img_dir, exist_ok=True)

        # (a) 缺失文件：images.json 指向一个不存在的路径
        images_json = os.path.join(td, "images_missing.json")
        with open(images_json, "w", encoding="utf-8") as f:
            json.dump({"news1": {"src": "images/does_not_exist.png"}}, f)
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "gen_hyperframes.py"),
                             "-m", manifest_path, "-o", html_path, "--images", images_json],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        ev_check("缺失图片：退出码非 0", r.returncode != 0)
        ev_check("缺失图片：报错信息指出具体路径",
              "does_not_exist.png" in r.stderr, r.stderr[-300:])

        # (b) 损坏文件：文件存在但不是合法图片（模拟下载中断/截断）
        corrupt_path = os.path.join(img_dir, "news1.png")
        with open(corrupt_path, "wb") as f:
            f.write(b"not a real png, truncated download")
        images_json2 = os.path.join(td, "images_corrupt.json")
        with open(images_json2, "w", encoding="utf-8") as f:
            json.dump({"news1": {"src": "images/news1.png"}}, f)
        # 损坏探测靠 PIL（gen_hyperframes 无 Pillow 时降级为 warn + exit 0，
        # 那是设计好的降级不是回归）——本解释器没有 Pillow 就 SKIP 这两条
        try:
            from PIL import Image as _pil_probe  # noqa: F401
            _has_pil = True
        except ImportError:
            _has_pil = False
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "gen_hyperframes.py"),
                             "-m", manifest_path, "-o", html_path, "--images", images_json2],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if _has_pil:
            ev_check("损坏图片：退出码非 0", r.returncode != 0)
            ev_check("损坏图片：报错信息标明「损坏/无法解码」",
                  "损坏" in r.stderr or "无法解码" in r.stderr, r.stderr[-300:])
        else:
            ev_skip("损坏图片：退出码非 0（连同类目「报错信息标明」）",
                 "本解释器无 Pillow，gen_hyperframes 按设计降级为 warn，"
                 "损坏探测无从谈起（装 Pillow 后自动恢复）")

        # (c) 正常对照组：合法的最小 PNG 应该正常通过、生成 HTML
        try:
            from PIL import Image as _PILImage
            _PILImage.new("RGB", (4, 4), color="red").save(corrupt_path)
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "gen_hyperframes.py"),
                                 "-m", manifest_path, "-o", html_path, "--images", images_json2],
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
            ev_check("正常图片：退出码为 0", r.returncode == 0, r.stderr[-300:])
            ev_check("正常图片：HTML 已生成", os.path.isfile(html_path))
        except ImportError:
            ev_check("正常对照组：Pillow 未安装，已跳过（不计入失败）", True)


# ── run.py 把 TTS 和配图搜索并行跑——这里不调真实
# pipeline.py/search_images.py（要真实 API），而是用两个假脚本替身验证：
# (a) 两个子进程真的同时起、不是伪装成并行实际顺序跑（用耗时差反推）
# (b) 任一步失败时 run.py 能正确以对应退出码终止，不会吞掉错误
def check_run_parallel_orchestration():
    import importlib
    import time as _time

    # 假脚本 fixtures 放进独立的临时目录，而不是 dev/ 目录：避免两个 run_eval
    # 并发跑时互相删除对方还在用的 fixture 文件（dev/ 下共享路径会踩）。
    fixture_tmp = tempfile.TemporaryDirectory(prefix="run_eval_fixtures_")
    fixture_dir = fixture_tmp.name
    fake_pipeline = os.path.join(fixture_dir, "_fixtures_fake_pipeline.py")
    fake_search = os.path.join(fixture_dir, "_fixtures_fake_search_images.py")
    fake_pipeline_fail = os.path.join(fixture_dir, "_fixtures_fake_pipeline_fail.py")

    for path, body in [
        (fake_pipeline, FAKE_PIPELINE_SRC),
        (fake_search, FAKE_SEARCH_IMAGES_SRC),
        (fake_pipeline_fail, FAKE_PIPELINE_FAIL_SRC),
    ]:
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)

    try:
        os.environ["STEPFUN_API_KEY"] = "fake-key-for-run-eval"
        sys.path.insert(0, SCRIPTS_DIR)
        run_mod = importlib.import_module("run")

        src = {"opening": "开场。", "closing": "结尾。",
               "segments": [{"title": "t1", "text": "内容。"}]}

        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "segments_source.json")
            with open(src_path, "w", encoding="utf-8") as f:
                json.dump(src, f, ensure_ascii=False)

            # (a) 真并发：fake_pipeline 睡 2.5s，fake_search 睡 1.0s。
            # 顺序执行约 3.5s，并行执行约 max(2.5,1.0)=2.5s。sleep 取大是
            # 为了让"并行远小于顺序"的判定窗口吸收子进程 spawn 开销
            # （venv/Defender 下实测稳定 +0.6s：曾用 1.2s 基准在 2.3 窗口
            # 连续假红——开销是稳定的，重试救不了，只能拉大绝对时长）。
            run_mod._script = lambda name: {
                "pipeline.py": fake_pipeline,
                "search_images.py": fake_search,
            }.get(name, os.path.join(SCRIPTS_DIR, name))
            out_dir = os.path.join(td, "audio_output")
            sys.argv = ["run.py", "--source", src_path, "-o", out_dir, "--until", "images"]
            # 墙钟计时对机器负载敏感（门控里 selftest 刚跑完，Windows
            # Defender 对新建临时文件的扫描会让子进程 spawn 慢 0.5s+，
            # 曾在 check.py 上下文稳定假红、单跑全绿）——墙钟类断言重试
            # 3 次取任一通过，判定窗口仍是"并行远小于顺序"的宽区间。
            elapsed = None
            for _attempt in range(3):
                t0 = _time.time()
                run_mod.main()
                elapsed = _time.time() - t0
                if elapsed < 3.2:
                    break
                _time.sleep(0.5)
            ev_check("TTS+配图真并行（耗时接近较慢一步，而非两步相加）",
                  elapsed < 3.2, f"实际耗时 {elapsed:.2f}s（顺序应约 3.5s，并行应约 2.5s，已重试 3 次）")

        # (b) 失败传播：TTS 假脚本以退出码 7 失败，run.py 应该原样传播退出码
        with tempfile.TemporaryDirectory() as td2:
            src_path2 = os.path.join(td2, "segments_source.json")
            with open(src_path2, "w", encoding="utf-8") as f:
                json.dump(src, f, ensure_ascii=False)
            run_mod._script = lambda name: {
                "pipeline.py": fake_pipeline_fail,
                "search_images.py": fake_search,
            }.get(name, os.path.join(SCRIPTS_DIR, name))
            out_dir2 = os.path.join(td2, "audio_output")
            sys.argv = ["run.py", "--source", src_path2, "-o", out_dir2, "--until", "images"]
            try:
                run_mod.main()
                ev_check("TTS 失败时 run.py 以非零退出码终止", False, "main() 正常返回，没有退出")
            except SystemExit as e:
                ev_check("TTS 失败时 run.py 正确传播退出码 7", e.code == 7, f"实际退出码 {e.code}")
    finally:
        os.environ.pop("STEPFUN_API_KEY", None)
        fixture_tmp.cleanup()


FAKE_PIPELINE_SRC = '''import sys, time, os, json
out = sys.argv[sys.argv.index("-o") + 1]
os.makedirs(out, exist_ok=True)
time.sleep(2.5)
# fixture 必须满足 timing_manifest 契约（顶层 sentences 非空、每段
# sentences 非空、句子四字段齐全）——run.py 的 _image_coverage 走
# load_timing_manifest 校验，空列表会被当成坏产物拒收。
_sent = {"index": 0, "text": "内容。", "start_time": 0.0, "duration": 1.0}
with open(os.path.join(out, "timing_manifest.json"), "w") as f:
    json.dump({"sentences": [_sent], "total_duration": 1.0,
               "segments": [{"id": "news1", "title": "t1", "sentences": [_sent]}]}, f)
'''

FAKE_SEARCH_IMAGES_SRC = '''import sys, time, os, json
out = sys.argv[sys.argv.index("-o") + 1]
os.makedirs(out, exist_ok=True)
time.sleep(1.0)
with open(os.path.join(out, "..", "images.json"), "w") as f:
    json.dump({"news1": {"src": "images/news1.png"}}, f)
'''

FAKE_PIPELINE_FAIL_SRC = '''import sys, time
time.sleep(0.2)
print("[fake_pipeline] 模拟失败", file=sys.stderr)
sys.exit(7)
'''

FAKE_PIPELINE_SRC_2NEWS = '''import sys, time, os, json
out = sys.argv[sys.argv.index("-o") + 1]
os.makedirs(out, exist_ok=True)
time.sleep(0.1)
# 同上：契约要求顶层与每段的 sentences 都非空。
_s1 = {"index": 0, "text": "内容一。", "start_time": 0.0, "duration": 1.0}
_s2 = {"index": 1, "text": "内容二。", "start_time": 1.0, "duration": 1.0}
with open(os.path.join(out, "timing_manifest.json"), "w") as f:
    json.dump({"sentences": [_s1, _s2], "total_duration": 2.0,
               "segments": [{"id": "news1", "title": "t1", "sentences": [_s1]},
                            {"id": "news2", "title": "t2", "sentences": [_s2]}]}, f)
'''

FAKE_SEARCH_IMAGES_MISSING_SRC = '''import sys, time, os, json
out = sys.argv[sys.argv.index("-o") + 1]
os.makedirs(out, exist_ok=True)
time.sleep(0.1)
# news1 有定稿配图，news2 只有候选、没定稿——复现 run.py "缺图" 分支。
with open(os.path.join(out, "..", "images.json"), "w") as f:
    json.dump({"news1": {"src": "images/news1.png"}}, f)
# 1x1 蓝色 PNG 的最小字节序列（硬编码，零依赖）：候选图落盘不依赖 Pillow，
# 保证离线 gate 在没有 PIL 的机器上也能验证"缺图提示列出候选路径"这条链路。
# 注意双反斜杠是刻意的：这段源码会被原样写进假脚本文件再执行，外层字符串
# 不能把十六进制转义提前解释成真实字节（0x89 单独出现会让假脚本的
# UTF-8 源非法）
_MIN_PNG = (b"\\x89PNG\\r\\n\\x1a\\n\\x00\\x00\\x00\\rIHDR\\x00\\x00\\x00\\x01"
            b"\\x00\\x00\\x00\\x01\\x08\\x02\\x00\\x00\\x00\\x90wS\\xde"
            b"\\x00\\x00\\x00\\x0cIDATx\\x9cc``\\xf8\\x0f\\x00\\x01"
            b"\\x03\\x01\\x00\\x08\\x89\\xc2\\xec\\x00\\x00\\x00\\x00IEND\\xaeB`\\x82")
for i in range(1, 3):
    with open(os.path.join(out, f"news2_cand{i}.png"), "wb") as f:
        f.write(_MIN_PNG)
'''


# ── 纯独立审阅：run.py 缺图提示不再生成拼图，而是列出每个缺图段落的候选
# 原图全路径，让 agent 逐张、全分辨率 Read 判断图↔题贴合度。验证：
# 缺图时退出码 2；提示里包含"全分辨率"独立审阅指引且不再引用 review_grid；
# 并把候选原图文件路径列出来（假 search 只落候选图文件、不写 candidates.json，
# 新提示从 images 目录枚举 *_cand* 兜底）。
def check_missing_image_independent_review_hint():
    import importlib

    fixture_tmp = tempfile.TemporaryDirectory(prefix="run_eval_fixtures_")
    fixture_dir = fixture_tmp.name
    fake_pipeline2 = os.path.join(fixture_dir, "_fixtures_fake_pipeline_2news.py")
    fake_search_missing = os.path.join(fixture_dir, "_fixtures_fake_search_missing.py")
    for path, body in [
        (fake_pipeline2, FAKE_PIPELINE_SRC_2NEWS),
        (fake_search_missing, FAKE_SEARCH_IMAGES_MISSING_SRC),
    ]:
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)

    try:
        os.environ["STEPFUN_API_KEY"] = "fake-key-for-run-eval"
        sys.path.insert(0, SCRIPTS_DIR)
        run_mod = importlib.import_module("run")
        # 只替身 pipeline.py/search_images.py（假 API 调用）；其它脚本走真实路径。
        run_mod._script = lambda name: {
            "pipeline.py": fake_pipeline2,
            "search_images.py": fake_search_missing,
        }.get(name, os.path.join(SCRIPTS_DIR, name))

        src = {"opening": "开场。", "closing": "结尾。",
               "segments": [{"title": "t1", "text": "内容一。"},
                            {"title": "t2", "text": "内容二。"}]}

        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "segments_source.json")
            with open(src_path, "w", encoding="utf-8") as f:
                json.dump(src, f, ensure_ascii=False)
            out_dir = os.path.join(td, "audio_output")
            project_dir = os.path.join(td, "hf-project")
            # --until html（不是 images）：--until images 是"看完覆盖率
            # 即止"的调试语义，在缺图拦截之前正常退出 0；要验证缺图
            # exit 2 提示，得让流程走到拦截分支（html/render 路径都经过）。
            sys.argv = ["run.py", "--source", src_path, "-o", out_dir,
                        "--project", project_dir, "--until", "html"]

            buf = io.StringIO()
            exit_code = "未触发 SystemExit"
            with contextlib.redirect_stderr(buf):
                try:
                    run_mod.main()
                except SystemExit as e:
                    exit_code = e.code

            stderr_text = buf.getvalue()
            ev_check("缺图时退出码为 2（等待 agent 审阅）", exit_code == 2,
                  f"实际 {exit_code}；stderr: {stderr_text[-300:]}")
            ev_check("提示不再生成/引用 review_grid 拼图",
                  "review_grid" not in stderr_text, stderr_text[-300:])
            ev_check("提示要求逐张 Read 候选原图（全分辨率独立审阅）",
                  "全分辨率" in stderr_text and "Read" in stderr_text,
                  stderr_text[-500:])
            # 假 search 只落候选图文件（news2_cand*.png）、不写 candidates.json；
            # 新提示从 images 目录枚举候选原图路径，便于 agent 直接 Read。
            ev_check("提示列出了候选原图文件路径（可逐张 Read）",
                  "news2_cand1.png" in stderr_text
                  and "news2_cand2.png" in stderr_text,
                  stderr_text[-500:])
    finally:
        os.environ.pop("STEPFUN_API_KEY", None)
        fixture_tmp.cleanup()




# ═══════════════════ 第三段：文档漂移/打包门禁（原 dev/check.py，1.5.74 并入）═══════════════════
# 原则：文档声明了具体值 → 必须与实际一致，否则 FAIL；文档改成"引用权威来源、
# 不写死数值"（推荐的防漂移写法）→ 放行。历史教训见各检查函数内的注释。
ROOT = _SKILL_ROOT  # 门禁段沿用原 check.py 的路径根命名

def _check_json(rel):
    try:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            json.load(f)
        return True, ""
    except Exception as e:
        return False, str(e)


def _check_frontmatter():
    try:
        with open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return False, str(e)
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return False, "SKILL.md 缺少 YAML frontmatter"
    fm = m.group(1)
    for key in ("name:", "description:"):
        if key not in fm:
            return False, f"SKILL.md frontmatter 缺少 {key}"
    return True, ""


# SKILL.md"不适用场景"里的范围声明（纯英文稿件不适用）是"agent 读到越界
# 请求要正确拒绝"的语义前提——没有脚本能替 agent 做这个判断（见
# 本文件头部说明），但声明文本本身可以离线机械检查：它没有被误删/
# 改弱，是 agent 大概率能正确拒绝的必要（非充分）条件。这不是给语义判断
# "接了断言逻辑"，只是把"声明文本还在不在"这一半机械部分接入离线 gate，
# 减少"改 SKILL.md 时手滑删掉这段导致 agent 从此不再正确拒绝，但没人发现"
# 这种退化——发现得越早，成本越低。
_SCOPE_DECLARATION_KEYWORDS = ("纯英文", "此技能不适用")


def _check_scope_declaration():
    skill_path = os.path.join(ROOT, "SKILL.md")
    try:
        with open(skill_path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return False, str(e)
    missing = [kw for kw in _SCOPE_DECLARATION_KEYWORDS if kw not in text]
    if missing:
        return False, (
            f"SKILL.md '不适用场景' 一节似乎不再包含纯英文稿件的范围声明"
            f"（缺关键词：{missing}）——这是 agent 正确拒绝纯英文稿件请求的"
            f"前提之一，改动前请确认是有意为之，"
            f"不是手滑删掉了"
        )
    return True, ""


# ── 声明-实际交叉校验（防文档漂移）──────────────────────────────────
# 历史上反复出现"文档/注释里写死的具体值与代码/配置实际值漂移"：
# --gap 默认值（budget 0.3 vs pipeline 0.4）、tagline 兜底文案
# （"补充阅读" vs "AI 资讯"）、模板图片尺寸（460 vs 680）、踩坑/评估
# 条数。这类漂移没有任何机制兜底，等真实用户按文档操作时才暴露。
# 这里把可以机械比对的部分接进离线 gate，原则是：
#   - 文档声明了具体值 → 必须与实际一致，否则 FAIL；
#   - 文档改成"引用权威来源、不写死数值"（推荐的防漂移写法，见
#     run_eval.py 头部同款教训）→ 放行。
# 这样既抓住漂移，又不强迫文档必须保留某个数字。

def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _check_gap_default():
    """_contracts.DEFAULT_GAP 必须等于 pipeline.py --gap 的默认值
    （不一致会让 estimate 相对实测系统性偏移 (n-1)×Δ）。
    两处都是稳定字面量，解析不出来按失败处理，提醒同步维护本检查。"""
    m1 = re.search(r'add_argument\("--gap",\s*type=float,\s*default=([0-9.]+)',
                   _read("scripts/pipeline.py"))
    m2 = re.search(r"DEFAULT_GAP\s*=\s*([0-9.]+)", _read("scripts/_contracts.py"))
    if not m1 or not m2:
        return False, ("无法从 pipeline.py / _contracts.py 解析出 --gap 默认值"
                       "（代码结构变了？请同步更新本检查）")
    if abs(float(m1.group(1)) - float(m2.group(1))) > 1e-9:
        return False, (f"_contracts.DEFAULT_GAP={m2.group(1)} 与 pipeline --gap "
                       f"默认值={m1.group(1)} 不一致（两处必须一起改，"
                       "否则 estimate 系统性偏差）")
    return True, ""


def _declared_count_check(text, pattern, actual, what):
    """text 里声明了"N 条"（pattern 第 1 组捕获数字）时，必须等于 actual；
    没声明（文档改成不含数字的写法）则放行。"""
    m = re.search(pattern, text)
    if not m:
        return True, ""
    declared = int(m.group(1))
    if declared != actual:
        return False, f"{what}：文档声明 {declared} 条，实际 {actual} 条"
    return True, ""


def _measure_python_floor():
    """测出 scripts/ 下全部脚本真正需要的最低 Python 版本。

    用 ast.parse(feature_version=N) 从 3.8 起逐级探测：feature_version 只
    拒绝"比该版本新"的语法，所以能全部通过的最小版本就是实际下限。
    拿不到（例如代码用了探测范围之外的语法）返回 None。
    """
    import ast
    import glob as _glob
    versions = [(3, 8), (3, 9), (3, 10), (3, 11), (3, 12), (3, 13)]
    sources = []
    for path in sorted(_glob.glob(os.path.join(ROOT, "scripts", "*.py"))):
        try:
            with open(path, encoding="utf-8") as f:
                sources.append((path, f.read()))
        except OSError:
            return None
    for mv in versions:
        try:
            for _, src in sources:
                ast.parse(src, feature_version=mv)
        except SyntaxError:
            continue
        return mv
    return None


# 声明的 Python 下限允许比实测下限高 1 个小版本（保守声明是安全的：留一点
# 余量防 stdlib API 意外）；高得更多就成了虚构的硬门槛——本次 review 就撞到
# 声明 3.12、实测 3.8 语法即可跑（Python 3.11 全绿），把 3.11 的用户整条
# 挡在门外。这类"文档凭空抬高环境要求"没有任何别的机制能发现。
_PY_CLAIM_TOLERANCE_MINORS = 1


def _check_python_version_claim():
    """SKILL.md 声明的 Python 最低版本不得远高于代码实际所需版本。"""
    import re as _re
    skill = _read("SKILL.md")
    m = _re.search(r"Python\s+(\d+)\.(\d+)\+", skill)
    if not m:
        return True, ""  # 文档不写死版本（推荐写法）→ 放行
    claimed = (int(m.group(1)), int(m.group(2)))
    actual = _measure_python_floor()
    if actual is None:
        return False, ("测不出 scripts/ 的最低 Python 版本（用了探测范围之外的"
                       "语法？）——无法校验 SKILL.md 的版本声明，请同步更新本检查")
    allowed = (actual[0], actual[1] + _PY_CLAIM_TOLERANCE_MINORS)
    if claimed > allowed:
        return False, (
            f"SKILL.md 声明 Python {claimed[0]}.{claimed[1]}+，但 scripts/ 实际只需 "
            f"{actual[0]}.{actual[1]}（最高允许声明 {allowed[0]}.{allowed[1]}）。"
            f"凭空抬高版本门槛会把低版本用户整条挡在门外——请改回实测值，"
            f"或说明确有必要的新语法依赖")
    return True, ""


def _check_workers_default():
    """渲染抓帧 workers 的文档声明必须与 run.py 的默认值一致。

    注意作用域：本技能里有三个同名的 --workers，默认值各不相同——
      · run.py / hyperframes render：渲染抓帧 worker（默认 6）
      · search_images.py：并行搜图/下载线程（默认 4）
      · pipeline.py：并行 TTS 调用数（默认 4）
    所以这条检查只认"渲染"语境下的声明，另外两个工具的 --workers 4 是
    合法默认值，不能拿 run.py 的 6 去比对它们。
    """
    import re as _re
    m = _re.search(r'add_argument\("--workers",\s*type=int,\s*default=(\d+)',
                   _read("scripts/run.py"))
    if not m:
        return False, "无法从 run.py 解析出 --workers 默认值（代码结构变了？请同步更新本检查）"
    actual = int(m.group(1))
    # 出现"这些词"说明这条讲的是别的工具/或是在给调优建议，不是默认值声明
    _OTHER_TOOL = ("搜图", "下载线程", "TTS", "配音", "并行 TTS")
    _TUNING_ADVICE = ("降回", "降到", "建议降", "低配", "崩溃", "堆上限")
    for rel in ("SKILL.md", "references/rendering.md"):
        for lineno, line in enumerate(_read(rel).splitlines(), 1):
            if "--workers" not in line:
                continue
            if any(k in line for k in _OTHER_TOOL + _TUNING_ADVICE):
                continue
            # 两种写法都算声明："--workers（默认 6）" / 命令里的 "--workers 6"
            for dm in _re.finditer(r"--workers[）)]?\s*[（(]?\s*(?:默认\s*)?(\d+)",
                                   line):
                if int(dm.group(1)) != actual:
                    return False, (
                        f"{rel}:{lineno} 声明 --workers {dm.group(1)}，run.py 渲染"
                        f"抓帧 worker 实际默认 {actual}。调默认值时必须连同文档示例"
                        f"命令一起改，否则 agent 照抄示例就退回旧值")
    return True, ""


def _check_doc_drift():
    """返回 [(name, passed, err)] 列表，由 main 逐项计入结果。"""
    checks = []
    skill = _read("SKILL.md")

    r, err = _check_gap_default()
    checks.append(("doc_drift:gap_default", r, err))

    r, err = _check_python_version_claim()
    checks.append(("doc_drift:python_version_claim", r, err))

    r, err = _check_workers_default()
    checks.append(("doc_drift:workers_default", r, err))

    # tagline 兜底文案：pipeline.py 里写死的兜底字面量必须同步出现在 SKILL.md
    m = re.search(r'_tagline\s*=\s*"([^"]+)"', _read("scripts/pipeline.py"))
    if m:
        lit = m.group(1)
        passed = lit in skill
        checks.append(("doc_drift:tagline_fallback", passed,
                       "" if passed else
                       f"pipeline 兜底 tagline 为“{lit}”，但 SKILL.md 未提及该文案（两处必须同步）"))
    else:
        checks.append(("doc_drift:tagline_fallback", True,
                       "跳过：pipeline.py 未匹配到兜底字面量（变量名变了？）"))

    # 模板横屏图片尺寸：SKILL.md 与 references/rendering.md 各自声明的 W×H
    # 都必须与 template.json 一致（逐文件判，避免一份正确就放行另一份漂移）
    try:
        tpl = json.loads(_read("config/template.json"))
        img = tpl["layout"]["landscape"]["image"]
        declared = f'{img["width"]}×{img["height"]}'
        _docs = (("SKILL.md", skill),
                 ("references/rendering.md", _read("references/rendering.md")))
        _missing = [name for name, doc in _docs if declared not in doc]
        passed = not _missing
        checks.append(("doc_drift:image_size", passed,
                       "" if passed else
                       f"template.json 横屏图片为 {declared}，但 {', '.join(_missing)} "
                       f"未按此声明（改模板或改文档，两处必须同步）"))
    except Exception as e:
        checks.append(("doc_drift:image_size", False, f"无法解析 template.json：{e}"))

    # 踩坑条数：SKILL.md / pitfalls.md 标题里声明的条数 == 实际 ### N. 小节数
    pitfalls = _read("references/pitfalls.md")
    actual = len(re.findall(r"^### \d+\.", pitfalls, re.M))
    for src_text, pat, what in (
            (skill, r"(\d+) 条踩坑记录", "SKILL.md 踩坑条数"),
            (pitfalls, r"（(\d+) 条踩坑记录）", "pitfalls.md 标题条数")):
        r, err = _declared_count_check(src_text, pat, actual, what)
        checks.append((f"doc_drift:pitfalls_count:{what}", r, err))

    # CLI 选项漂移：SKILL.md 里出现的 `--aspect <val>` 必须都是 run.py argparse
    # 实际接受的 choices。这是之前 `--aspect vertical` 存活一整个版本的根因——
    # 没任何门禁交叉校验"文档声明的 CLI 选项 <-> 代码 argparse choices"。
    try:
        run_src = _read("scripts/run.py")
        m = re.search(r'add_argument\("--aspect"[^;]*?choices=\[([^\]]+)\]',
                       run_src, re.S)
        if m:
            choices = [c.strip().strip('"\'') for c in m.group(1).split(",")
                       if c.strip()]
            doc_aspects = set(re.findall(r"--aspect\s+([A-Za-z]+)", skill))
            bad = sorted(doc_aspects - set(choices))
            passed = not bad
            checks.append(("doc_drift:cli_aspect_choices", passed,
                           "" if passed else
                           f"SKILL.md 声明了 --aspect {bad}，但 argparse choices 只有 {choices}"
                           "（文档与 CLI 不同步，agent 照抄会 invalid choice）"))
        else:
            checks.append(("doc_drift:cli_aspect_choices", True,
                           "跳过：scripts/run.py 未匹配到 --aspect choices 定义"))
    except Exception as e:
        checks.append(("doc_drift:cli_aspect_choices", False, f"无法校验 CLI 选项：{e}"))

    return checks


def _check_version_consistency():
    """SKILL.md frontmatter version 必须等于 CHANGELOG.md frontmatter 的
    current_version（两处不同步没有任何机制兜底，只能靠这条门禁拦）。"""
    skill = _read("SKILL.md")
    m = re.match(r"^---\n(.*?)\n---\n", skill, re.S)
    if not m:
        return False, "SKILL.md 缺少 YAML frontmatter"
    vm = re.search(r'^version:\s*"?([0-9][0-9A-Za-z.\-]*)"?\s*$', m.group(1), re.M)
    if not vm:
        return False, "frontmatter 里解析不出 version 字段"
    try:
        log = _read("CHANGELOG.md")
        lm = re.match(r"^---\n(.*?)\n---\n", log, re.S)
        if not lm:
            return False, "CHANGELOG.md 缺少 YAML frontmatter"
        cv = re.search(r'^current_version:\s*"?([0-9][0-9A-Za-z.\-]*)"?\s*$',
                       lm.group(1), re.M)
        if not cv:
            return False, "CHANGELOG.md frontmatter 里解析不出 current_version"
    except Exception as e:
        return False, f"无法读取 CHANGELOG.md：{e}"
    if vm.group(1) != cv.group(1):
        return False, (f"SKILL.md version={vm.group(1)} 与 CHANGELOG.md "
                       f"current_version={cv.group(1)} 不一致（发版时两处必须一起改）")
    return True, ""


def _check_changelog_md():
    """CHANGELOG.md 结构自检：frontmatter current_version 必须与最新条目
    标题一致（条目按时间倒序、新条目插最上是本文件的发版约定）；每条
    `## 版本 · 日期` 标题必须带 YYYY-MM-DD 日期。"""
    log = _read("CHANGELOG.md")
    m = re.match(r"^---\n(.*?)\n---\n", log, re.S)
    if not m:
        return False, "CHANGELOG.md 缺少 YAML frontmatter"
    cv = re.search(r'^current_version:\s*"?([0-9][0-9A-Za-z.\-]*)"?\s*$',
                   m.group(1), re.M)
    if not cv:
        return False, "frontmatter 里解析不出 current_version"
    heads = re.findall(r"^## ([0-9][0-9A-Za-z.\-]*)\s*·\s*(\d{4}-\d{2}-\d{2})\s*$",
                       log, re.M)
    if not heads:
        return False, "找不到任何「## 版本 · 日期」条目标题"
    if heads[0][0] != cv.group(1):
        return False, (f"最新条目是 {heads[0][0]}，与 current_version="
                       f"{cv.group(1)} 不一致（新条目必须插在最上方）")
    bad = [v for v, _d in heads if not re.match(r"^[0-9][0-9A-Za-z.\-]*$", v)]
    if bad:
        return False, f"条目标题版本号格式异常：{bad}"
    return True, ""


def _heading_anchor(heading):
    """GitHub 风格 slug：小写、去标点（含中文标点）、空白转 '-'，保留字母
    数字与 CJK。与 SKILL.md 现有目录锚点写法（如 #核心工作流5-步）一致。"""
    out = []
    for ch in heading.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch.isspace():
            out.append("-")
        # 其余（（）／、：等标点）丢弃
    return "".join(out)


def _check_toc_anchors():
    """SKILL.md 目录/正文里的 `](#锚点)` 内链必须对得上某个标题。
    只查"链接有目标"，不强制"每个标题都被目录收录"（后者不是错误）。"""
    skill = _read("SKILL.md")
    anchors = {_heading_anchor(h) for h in re.findall(r"^#{1,6}\s+(.+?)\s*$",
                                                      skill, re.M)}
    broken = []
    for text, anchor in re.findall(r"\[([^\]]+)\]\(#([^)]+)\)", skill):
        if anchor not in anchors:
            broken.append(f"[{text}](#{anchor})")
    if broken:
        return False, ("SKILL.md 内链锚点对不上任何标题："
                       + "；".join(broken)
                       + "（改标题时目录同步改）")
    return True, ""



# ═══════════════════════════ main：三段编排 ═══════════════════════════

def main():
    setup_stdio()
    print("=== content-to-video 一键测试（断言 + 离线集成 + 文档门禁）===")

    # 预加载跨章节共享状态：[18]+ 用 template.json，[18] 崩了也不该阻断 [20+]
    _load_template()

    # ── 第一段：确定性断言（原 selftest）──
    _sections = [
        _test_1_split_sentences,
        _test_2_build_structured,
        _test_2b_dialogue,
        _test_2c_flow,
        _test_2d_subtitle_lines,
        _test_2e_subtitle_cues,
        _test_3_hyperframes,
        _test_3b_hyperframes_flow,
        _test_4_env,
        _test_5_theme_template,
        _test_6_verify_render,
        _test_7_audio_tts,
        _test_8_contracts,
        _test_9_render_watch,
        _test_10_search_images,
        _test_12_budget,
        _test_14_gen_charts,
        _test_15_series_split,
        _test_17f_review_fix,
        _test_18_anti_regression,
        _test_19_wcag,
        _test_20_series_check,
        _test_20c_manifest,
        _test_21_wcag_ext,
        _test_22_review_fix,
        _test_23_review_fix_guards,
        _test_25_gating,
    ]
    for _fn in _sections:
        try:
            _fn()
        except Exception as _e:
            global failed
            failed += 1
            RESULTS.append((f"{_fn.__name__} crashed", False))
            print(f"  [FAIL] {_fn.__name__} crashed: {_e}")
    unit = {"passed": passed, "failed": failed, "total": passed + failed}

    # ── 第二段：离线集成（原 run_eval：真子进程/真 ffmpeg/run.py 编排）──
    EV_RESULTS.clear()
    EV_SKIPPED.clear()
    try:
        ev_main()
    except Exception as _e:
        print(f"  [FAIL] integration crashed: {_e}")
    ev_p = sum(1 for _, ok in EV_RESULTS if ok)
    ev_t = len(EV_RESULTS)
    print(f"\n[integration] {ev_p}/{ev_t} passed"
          + (f"，另有 {len(EV_SKIPPED)} 项 SKIP（环境缺能力，非回归）"
             if EV_SKIPPED else ""))

    # ── 第三段：文档漂移/打包门禁（原 check.py 的非编排部分）──
    gates = []
    gates.append(("md:changelog",) + _check_changelog_md())
    gates.append(("frontmatter",) + _check_frontmatter())
    gates.append(("scope_declaration",) + _check_scope_declaration())
    gates.extend((n, ok, err) for n, ok, err in _check_doc_drift())
    gates.append(("packaging:version_consistency",) + _check_version_consistency())
    gates.append(("packaging:toc_anchors",) + _check_toc_anchors())
    gate_fail = 0
    for name, ok, err in gates:
        gate_fail += 0 if ok else 1
        print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f"：{err}" if (err and not ok) else ""))

    total_passed = unit["passed"] + ev_p + (len(gates) - gate_fail)
    total_failed = unit["failed"] + (ev_t - ev_p) + gate_fail
    print(f"\n=== 总计 {total_passed}/{total_passed + total_failed} passed"
          + (f"，{len(EV_SKIPPED)} skipped" if EV_SKIPPED else "") + " ===")
    summary = {
        "tool": "test",
        "passed": total_passed,
        "failed": total_failed,
        "total": total_passed + total_failed,
        "sections": {
            "unit": unit,
            "integration": {"passed": ev_p, "failed": ev_t - ev_p,
                            "total": ev_t, "skipped": len(EV_SKIPPED)},
            "gates": {"passed": len(gates) - gate_fail, "failed": gate_fail,
                      "total": len(gates)},
        },
        "results": ([{"name": n, "ok": ok} for n, ok in RESULTS]
                    + [{"name": n, "ok": ok} for n, ok in EV_RESULTS]
                    + [{"name": n, "ok": ok} for n, ok, _e in gates]),
    }
    print("__SUMMARY_JSON__ " + json.dumps(summary, ensure_ascii=False))
    sys.exit(1 if total_failed else 0)


if __name__ == "__main__":
    main()
