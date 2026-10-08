# 文本地图：各引擎文本在哪

动手前读这一份。列的是**玩家可见**的文本位置；
编辑器内部字段（事件名、`note` 注释、地图内部名）默认不进表 ——
玩家看不见，翻了只增加风险。

---

## 通用兜底：明文型引擎

不认识引擎时先试这条路。适用于任何「文本以明文存在文件里」的引擎，命中率很高。

```python
def walk(node, prefix, sink):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in SKIP_FIELDS:      # 见下
                continue
            child = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, str):
                if k in TEXT_FIELDS:
                    sink.append((child, v))
            else:
                walk(v, child, sink)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            child = f"{prefix}[{i}]"
            if isinstance(v, str):
                sink.append((child, v))
            else:
                walk(v, child, sink)
```

### 字段名启发式

**收录**（命中率高）：

```
name displayName title text label caption tooltip description message
message1 message2 message3 message4 dialog dialogue line choice option
prompt hint placeholder body content string
```

**排除**（翻了会坏）：

```
id key guid uuid hash version path file filename url type class tag tags
icon sprite anim meta __collapsed__ note comment comments m_name
```

白名单不命中不代表没有文本，只代表**需要人看一眼这个文件长什么样再手工补**。

### 三个通用启发式

1. **按扩展名扫**：`*.json` `*.csv` `*.ini` `*.txt` `*.yml` `*.yaml`
   `*.lua` `*.po` `*.tscn` `*.tres` `*.rpy` `*.ks`
2. **按语言包找**：`i18n/` `locale/` `locales/` `lang/` `translations/`
   `*.po` `*.mo` —— 找到语言目录基本就锁定了引擎
3. **识别编码**：UTF-8 失败就试 `cp932`（日文）`cp1252` `gb18030`。
   **读和写必须是两个独立决定**，用同一套假设会静默毁掉译文。

### 其余排除规则

- 资源路径、文件名、扩展名
- 枚举值、状态码、类型标签
- 查找表 / 字典的**键**（值可以翻，键不行）
- 版本号、哈希、UUID
- 注释（`//` `#` `--` `/* */` 之后的部分）

---

## RPG Maker MV / MZ

数据在 `data/*.json`，明文 UTF-8 无 BOM。目录在 `www/data/`（已部署）
或项目根 `data/`。**内置抽取器**：`extract_sheet.py` 的 `rgmmz_extract`。

### 事件指令码

| 码 | 含义 | 文本位置 | 翻？ |
|---|---|---|---|
| `101` | 显示文本（首行） | `parameters[0]` | ✅ |
| `401` | 续行 | `parameters[0]` | ✅ |
| `405` | 滚动文本 | `parameters[0]` | ✅ |
| `102` | 显示选项 | `parameters[0]` 是**数组** | ✅ 逐项 |
| `402` | 条件分支 | `parameters[0]` 是分支标签 | ❌ 必须与 102 原文一致 |
| `108` / `408` | 注释 | — | ❌ |
| `356` / `357` | MZ/MV 插件指令 | 参数里可能有文本 | ⚠ 先读懂插件 |

### 具体路径

```
Mapxxx.json      events[i].pages[j].list[k].parameters[0]
                 （102 时是 parameters[0][m]）
CommonEvents.json [i].list[k].parameters[0]
System.json      terms.<类>.<键>              ← UI 词条全在这
                 terms.<类>.<键>.<子键>
                 currencyUnit / title / gameOver / victory
                 skillTypes[i] / elements[i] / equipTypes[i] ...
MapInfos.json    [i].name / displayName
Actors.json      [i].name / nickname / profile / battlerName
Items.json  Skills.json  States.json  Weapons.json  Armors.json
Classes.json Enemies.json  Troops.json
                 [i].name / description / message1..message4
```

### 最容易漏的一层：状态/技能的 `message`

`States.json` / `Skills.json` 的 `message1..4` 是**战斗提示**，含 `%1` 占位符：

```
%1は倒れた！         → %1倒下了！
%1の攻撃！           → %1的攻击！
%1は身を守っている。 → %1正在防御。
```

很多工具只做事件文本，数据库的 `name` / `description` / `message` 全漏。
实测某游戏扫出 73 条残留，核实后**35 条真漏翻全在这一层**。

### 插件

| 位置 | 内容 | 风险 |
|---|---|---|
| `js/plugins.js` | 插件参数，值是**嵌套 JSON 字符串**，要递归穿透 | 高 |
| `js/plugins/*.js` | 插件源码里硬编码的 UI 文字 | 中，插件升级会覆盖 |

`js/plugins.js` 的 `parameters` 里**每个值必须是字符串**，
写坏成对象/数字会让游戏起不来。

---

## Ren'Py

| 项 | 内容 |
|---|---|
| 明文 | `game/*.rpy` |
| 需解包 | `game/*.rpyc`（`unrpyc`）、`game/*.rpa`（`rpatool`）|
| 文本位置 | 对话行、`label` 后面的句子、`menu:` 的选项、`define`/`default` 里的字符串、`translate` 块 |
| 不能翻 | `label` 名、`init python:` 代码块、`$` 表达式、文件路径 |

`define` 块里的字符串常被其它地方引用（如物品名、角色名），
改一处等于改全局 —— 先全局搜索确认。

## TyranoScript / KAG

`.ks` 文件，纯文本。文本在标签之间。

危险的是**参数里也含文本**：

```
[cm name="三个字"]      ← name 的值可以翻
[link target=se_01]     ← target 绝对不能翻
```

翻 `target` 会让跳转失效，而且**不报错**。

## gettext (.po)

```
msgid "原文"
msgstr "译文"
```

- `#:` 注释行、`#.` 提取注释、`#|` 旧译文 —— 结构行不要动
- 复数形式 `msgid_plural` / `msgstr[0]` / `msgstr[1]`，条数要对齐
- `fuzzy` 标记的条目通常还没确认
- 换行转义 `\n` 是结构的一部分

内置抽取器：`extract_sheet.py` 的 `po_extract`。

## NScripter / ONScripter

```
*label
台词文本
#0000
```

`#` 开头是命令，`*` 开头是标签，**都不能翻**。
乱码版本（字符集被替换过）要先还原编码再处理。

## Godot

- `.tscn` / `.tres` 是明文，找 `text = "..."`
- `.translation` 是官方 CSV
- 导出的 `.pck` 用 GDRE Tools 解包再重打包

## Unity

- **YAML / JSON 文本资产**：普通字符串。注意别把 `.meta` 的 GUID、
  `m_Name` 当文本 —— 改了引用就断
- **TextMeshPro**：富文本 `<sprite=0> <color=#fff> <size=80%>`，
  以及 `\uXXXX` 转义
- **asset bundle / Addressables**：UnityPy + Il2CppDumper 解，改完重打包
- **IL2CPP**：常改不动，且很多游戏把文字编译进 `Assembly-CSharp.dll` 的字符串字面量

## Unreal

`.locres`（含 FString 表）、`.pak`、IoStore。
用 FModel 导出为 `.json`/`.csv`，改完作为**覆盖包**放进 `Content/Paks` ——
不要重打整个游戏。

## Wolf RPG

`Data/*.dat` 二进制。用 UberWolf 解包，得到松散文件后可直接改，
再重打包成 `.wolf`。

引擎命令（`\\`、`#`、`@`、`~` 等）不是控制码，不要动。
WOLF 的字符串有压缩编码，编辑工具有现成的。

## Kirikiri / KAG

XP3 归档，多种加密（Xe / TCR / ...)，
`krkrExtract` / `GARbro` 能处理大部分变种。

优先找「导出成松散文件覆盖」的路线：
kirikiri 会优先读松散目录，只改 XP3 里的内容不生效。

---

## 物理上做不到的

| 情况 | 为什么 | 替代方案 |
|---|---|---|
| 图片里烤死的字 | 已经变成像素 | OCR + 重绘 + 重打包加密图片 |
| 烧录进视频的字幕 | 变成音视频流 | OCR 提字轨，或外挂 |
| 3D 场景贴图上的文字 | 同上 | 同上 |
| 主机游戏 ROM | 加密 + 签名 | 模拟器 Agent（RPCS3 / PCSX2 等）|
| Web / 云串流 | 根本没有本地文件 | 只能 OCR 叠加层 |
