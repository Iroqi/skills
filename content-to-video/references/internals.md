# 内部机制速览（数据流 / 路径解析 / 缓存）

排查诡异的路径/缓存问题时先看这里（原 `architecture.md`，曾并入 `pitfalls.md`，现独立成此文件）；日常制作不需要——第 3 步记得加 `--resume` 就够。完整踩坑记录仍见 `references/pitfalls.md`。

### 数据流全景

```text
segments_source.json
   │
   ├─► pipeline.py --source（第 3 步：逐段独立分句，
   │        │                 分句/分组逻辑来自 build_from_structured.py）
   │        ▼
   │   timing_manifest.json（实测时长 + segments + combined_audio 绝对路径）
   │        │
   │        ├───────────────────────────────────────────┐
   │        │                                           │
   └─► search_images.py / gen_charts.py（第 4 步，与 TTS 并行）
            │                                           │
            ▼                                           ▼
     images/* + images.json                 gen_hyperframes.py（第 5 步，读 manifest + images.json）
                                                        │
                                                        ▼
                                             HTML ──► hyperframes render ──► MP4
                                                                                  │
                                                                                  ▼
                                                                      verify_render.py（时长/编码校验）
```

补充产出物（可选，不参与上面这条主链路）：`export_extras.py` 读 `timing_manifest.json` → `chapters.txt` + `captions.srt`。

### 路径解析速记

- `search_images.py` 默认把 `images.json` 写到 `-o` 目录的上一级（`-o hf-project/images` → `hf-project/images.json`，可用 `--json-output` 覆盖）；`gen_charts.py` 只产出图片文件（`-o` 目录下的 `<id>.png/.svg`），需手动填进 `images.json`。
- `images.json` 的值支持字符串路径或对象（视频格式）两种，完整格式以 `references/image_options.md` 开头「images.json 统一格式（四种方式共用）」一节为家；结构不合法（如既不是字符串也不是对象、对象缺 `src`）会让 `gen_hyperframes.py` 直接报错；文件不存在则打警告退回纯文字版。
- 其余路径规则见 `references/pitfalls.md` 条目 #9（images.json 路径相对 HTML 目录）、#2（total_duration 用 ffmpeg 实测）与 SKILL.md 第 5 步"音频引用"（音频自动拷进项目根），不在此重复。

### 缓存与断点续跑

- 已生成且时长有效的句子 WAV 不会被重新调用 API（`--resume`）。
- 变速是可逆的：首次变速时保留 `.orig.wav` 原声备份 + `.spd` 速度标记，换语速重跑时从原声重新 atempo，不会在已变速文件上叠加变速；切回 `1.0` 时自动恢复原声。
