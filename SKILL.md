---
name: game-localization
description: Localize any PC game: detect engine, extract text, translate with control codes frozen, write back fail-closed, audit. Use for 汉化/翻译/本地化.
---

# Game Localization (multi-engine)

> **Language note.** This overview is in English. The detailed rules below are in
> Chinese (the pipeline was validated on Japanese → Chinese); scripts, CLI output
> and engine profiles are language-neutral. Read this section for the rules, then
> follow the Chinese detail — it says the same things with reasoning and examples.

Translate a game into a target language. The process is **engine-agnostic**: after
step 1 everything is identical across engines. An engine only decides *where the
text lives* and *what its markup looks like*.

Use this when the user says 汉化 / 翻译 / 本地化 / "localize this game" / "can this
be translated", or points at a game directory and asks where its text is.

## Hard rules

Each of these was paid for with a broken game file. Violating one means either the
game no longer launches, or control codes leak into your translations.

1. **Completion is judged path-by-path against the pristine backup — never by
   script detection.** Character-set checks miss Japanese written entirely in
   kanji (勉強, 料理, statistics) and false-positive on legal target-language
   characters (the middle dot in「李·爱铃」). One scan reported 73 leftovers;
   only 35 were real. "N leftovers" ≠ "N untranslated".
2. **Writes must fail closed.** A path that does not exist raises. Any
   "create it if missing" logic is the hole that silently writes junk keys into
   perfectly valid JSON.
3. **Freeze every control code, restore it, and verify the multiset is identical.**
   The generic `\V \C \N \I` set is not enough: in one measured title, 83% of
   all codes were `\m[n]` (face variants) and `\z[n]` (sprite poses) — the generic
   set misses both and breaks the game.
4. **Always `--dry` before writing.** Pasting from a spreadsheet shifts whole
   columns, invisibly. The dry run pinpoints the row.
5. **Write by path, never by search-and-replace on text.** Deduplicate, map by
   dedup id, then fill every occurrence.
6. **Read back and diff after writing.** Do not trust a "success" return value.
7. **Never translate identifiers.** Plugin command names, plugin parameter
   *keys*, note tag names, resource filenames, lookup keys, branch labels.
   Translating them disables features silently.

## Pipeline

1. **Back up** outside the game directory. Nothing gets modified before the backup exists.
2. **Detect the engine and its tier** (`python scripts/detect_engine.py <game>`).
3. **Extract** player-visible text into one engine-neutral CSV
   (`extract_sheet.py`).
4. **Translate**, carrying one glossary as the single source of truth.
5. **Write back fail-closed** (`apply_sheet.py --dry`, then without `--dry`).
6. **Audit** against the backup (`audit.py`) — candidates, not verdicts.
7. **Clean up** before release (`references/release.md`).

## Engine tiers

| Tier | What | How |
|---|---|---|
| **L0** | Plaintext | Edit directly ✅ |
| **L1** | Packed | Unpack → edit → repack |
| **L2** | Binary / encrypted | Dedicated unpacker; prefer a loose-file override |
| **L3** | Compiled into the binary | Not editable → Textractor / LunaTranslator / XUnity AutoTranslator |
| **L4** | Physically impossible | Text in images, burned-in subtitles, console ROMs → OCR + redraw, or an emulator agent |

Tell the user the tier **first**. Learning it is L4 after two hours of work is worse
than learning it at minute one.

Adding an engine = adding one profile answering five questions
(`detect` / `open` / `carriers` / `freeze` / `seal`). See
`references/engines/index.md`. A missing profile is not a blocker — plaintext
containers have a generic fallback in `references/carriers.md`.

---

# 游戏汉化（多引擎）

把一个外语游戏改成目标语言。核心是**引擎无关**：抽取之后的所有环节都一样，
引擎只影响「文本在哪、语法长什么样」。

## 什么时候用

用户说「汉化」「翻译这个游戏」「localize this game」「这游戏能不能汉化」，
或者给了一个游戏目录想知道文本在哪、改哪。

## 铁律

这七条是拿坏掉的游戏文件换来的。违反任何一条的代价是「游戏打不开了」
或「译文里混着控制码」。

1. **审计判据是「与原始备份同路径完全一致」，不是「看起来像外语」。**
   假名/字符集检测会同时犯两个方向的错：漏掉**全汉字的外语词**（勉強、料理、
   statistics —— 通篇汉字，假名为零，看起来翻好了其实一字未动），以及误报目标语言里
   合法的东西（间隔号「李·爱铃」、长音符「ーーー」、有意保留的原名）。
   实测：扫出 73 条残留，逐条核实后只有 35 条是真漏翻。

2. **写回必须 fail-closed：路径不存在就抛异常。**
   用 `re.findall(r'([^\[\]]+)|\[(\d+)\]', path)` 解析 `terms.messages.autosave`，
   会把整串当成一个键，然后在根字典静默新建它。**JSON 依然合法，不报错**，
   直到 diff 才发现。任何「找不到就新建」的逻辑都是这个洞。

3. **控制码必须逐条冻结-还原，并做等值校验。**
   通用的 `\V[n]` `\C[n]` `\N[n]` `\I[n]` 只是基础。带表情差分/立绘插件的 RPG
   还会出现 `\m[n]`（表情差分）和 `\z[n]`（立绘姿态）—— 实测某游戏里这两类占全部
   控制码的 **83%**，而通用保护方案完全漏掉它们，改错直接崩。

4. **先 `--dry`，再写盘。**
   从 Excel/WPS 粘贴译文会**整列错位**（实测两批都中招，且肉眼看不出来）。
   dry-run 按控制码逐行比对能精确定位到行号。

5. **按路径写，不按文本查找替换。**
   同一句「うん」在游戏里出现几十次。去重后按**去重编号**建映射再回填，
   比按文本字符串映射安全得多。

6. **写完立刻回读校验 + diff 验证。**
   不信任「写成功了」的返回值。

7. **不要翻译标识符。**
   插件指令名、插件参数的**键**、`note` 标签名、资源文件名、查找键值、分支标签 ——
   翻了功能会静默失效。插件参数中「会拿去和事件数据比对」的值必须保持原值。

## 通用流水线

七步。每步做完再进下一步，别攒着。

### 1. 备份

把要动的文件复制到**游戏目录之外**的带时间戳目录。后面所有「对比原始」都靠它。
备份不存在之前，不要改任何东西。

### 2. 识别引擎 + 判分层

```bash
python scripts/detect_engine.py <游戏目录>
```

引擎档案只回答五个问题：`detect` / `open` / `carriers` / `freeze` / `seal`。

**分层比引擎名更重要**：

| 层 | 类型 | 处理 |
|---|---|---|
| **L0** | 明文文本 | 直接改 ✅ |
| **L1** | 需解包 | 专用工具展开 → 改明文 → 重打包 |
| **L2** | 二进制 / 加密 | 专用解包器；优先找「导出松散文件覆盖」路线 |
| **L3** | 编译进 exe | 改不了 → Textractor / LunaTranslator / XUnity AutoTranslator |
| **L4** | 物理不可能 | 图片烤死的字、烧录视频、主机 ROM → OCR 重绘 / 模拟器 Agent |

**提前告诉用户落在哪一层**，比翻完两小时发现是 L4 强。

**档案缺失不等于做不了** —— 见 `references/carriers.md` 的「通用兜底」，
对明文型引擎（JSON / CSV / INI / Lua / YAML / `.po`）可以现场写一个。

### 3. 抽取 → 翻译表

```bash
python scripts/extract_sheet.py --root <游戏目录> --engine auto --out sheet.csv
```

产出统一中间格式：

```
id,carrier,file,path,kind,src,text,status,note
```

- `carrier`：`dialogue` / `choice` / `db_name` / `db_desc` / `db_message` /
  `sys_term` / `ui_label` / `generic`
- `path`：写回时的精确定位路径，不要自己拼
- `status`：`pending` / `done` / `skipped` / `keep_original`

**只抽玩家可见文本。** 事件名、`note` 注释、资源标识符不进表 ——
玩家看不见，翻了纯属浪费预算还引入风险。

一次抽全量，按批次（200~500 行）推进，每批带同一份术语表。

### 4. 翻译

- 术语表是**单一本源**，每批都带。不一致的专有名词比错译更刺眼。
- **重复句先去重**，译文回填到所有出现处。
- 目标语言不是中文时先确认字体：日文/中文引擎常常只有日文字体集，
  换成泰文/阿拉伯文要另配字体，否则全是豆腐块。

### 5. 写回

```bash
python scripts/apply_sheet.py --root <游戏目录> --sheet sheet.csv --dry   # 先验
python scripts/apply_sheet.py --root <游戏目录> --sheet sheet.csv       # 再写
```

写前核对「游戏里的现值 == 表里的 `src`」，不匹配就中止整批，绝不写部分补丁。
控制码等值校验不通过的条目跳过并列出，不硬写。

### 6. 审计

```bash
python scripts/audit.py --root <游戏目录> --backup <备份目录> --report audit.json
```

**审计范围必须和抽取范围用同一个定义。** `audit.py` 直接调用 `extract_sheet.py`
的抽取器 —— 不是偷懒，是硬要求：

如果审计和抽取用两套「哪些算玩家可见」的定义，就会把 `note` 注释、事件名、
插件参数、`.meta` 的 GUID 全算进来，报告说「481,257 条没翻」，其实里面 99%
玩家根本看不见。

报告会分四类输出，并把明显不用管的先自动排除（实测把 25,175 条「一致」
压到 545 条待看，24,627 条是纯 ASCII 资源标识符）：

```
✓ 已改动                    ← 好了
· 与原文一致但不用管         ← 自动分诊排除
⚠ 与原文一致且需要判断       ← 逐条看：真漏了还是该保留？
⚠ 已改动但仍含外语字符       ← 粗筛，目标语言合法字符也会命中
⚠ 文本槽里装着代码           ← 玩家看不见，但改错会改行为
```

**候选 ≠ 判决。** 每一类都要人工过。

### 7. 发布前清理

按 `references/release.md` 清掉工作产物、临时表、备份残留。有一条「绝不删」清单。

## 内容策略

这一层现成工具都没做好，也是这套流程真正的价值所在。

- **分级处理**：系统层（战斗提示、属性名、UI 词条、菜单）通常可以翻满；
  敏感向剧情按用户的意愿决定跳不跳。**跳过要留下记录**（哪些、为什么），
  否则最后没人知道哪些是没翻、哪些是漏了。
- **筛查用词组，不用词根**：`メイク`（化妆）含 `イク`、`メッセージ`（留言）含 `セー`
  —— 词根匹配会满屏假阳性。实测曾因此把 498 行全判成露骨，全靠词表重筛才归零。
- **正则是要清洗的**：筛查正则的 alternative 里混进空格或非目标语言字符时，
  `pattern.search()` 会**匹配到空串**，导致每一行都命中。一个静默的全假阳性。
- **保留原名的约定写进术语表**：某个角色名前一位译者已经保留了原样 ——
  那就跟着保留。译文和既有汉化风格一致比「翻译得更彻底」重要。

## 参考文件

| 文件 | 什么时候读 |
|---|---|
| `references/carriers.md` | **动手前必读** —— 各引擎文本路径 + 明文型引擎的通用兜底写法 |
| `references/freeze-restore.md` | 写译文时对照 —— 各引擎控制码的冻结与还原语法 |
| `references/pitfalls.md` | 每次写入前扫一眼 —— 实战踩坑清单 |
| `references/engines/index.md` | 识别出引擎后读对应那节 |
| `references/release.md` | 发布前必读 —— 清理清单 |

## 进度

维护批次表（批次 / 总行 / 已填 / 校验一致 / 残留）。**校验一致**和**已填**
必须分开统计 —— 「已填」只说明译文写进了 CSV，「一致」才说明游戏里真的生效了。
