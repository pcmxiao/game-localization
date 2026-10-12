# 引擎分层与索引

引擎档案只回答五件事：

| 字段 | 含义 |
|---|---|
| `detect` | 靠哪些目录 / 文件特征认出它 |
| `open` | 文本是明文，还是要先解包 |
| `carriers` | 文本在哪些文件的哪些字段 |
| `freeze` | 该引擎的控制码 / 标记语法 |
| `seal` | 写回什么产物（覆盖目录 / patch 文件 / 重打包） |

## 分层：先判断，再动手

| 层 | 类型 | 处理 |
|---|---|---|
| **L0** | 明文文本 | 直接改 ✅ |
| **L1** | 需要解包 | 专用工具展开 → 改明文 → 重打包 |
| **L2** | 二进制 / 加密 | 专用解包器；优先找「导出松散文件覆盖」路线，别改原包 |
| **L3** | 编译进产物 | 改不了 → 外挂（Textractor / LunaTranslator / XUnity AutoTranslator）|
| **L4** | 物理不可能 | 图片烤死的字、烧录视频、主机 ROM → OCR 重绘 / 模拟器 Agent |

**先跑 `detect_engine.py`。** 翻两小时才发现是 L4，比一开始就说是 L4 强。

### L4 时的替代路线

| 场景 | 工具 |
|---|---|
| 大多数日语游戏（含 3D）| [Textractor](https://github.com/lukesampson/Textractor) |
| 通用 / 自定义渲染器 | [LunaTranslator](https://github.com/xd233/LunaTranslator) |
| Unity 游戏 | [XUnity AutoTranslator](https://github.com/NateLehman/Unity-Auto-Translator) |
| 固定区域文字 | PCOT / Translumo / MORT（OCR 路线）|
| 主机游戏 | 模拟器 RPCS3 / PCSX2 / Ryujinx 的 Agent 脚本 |

---

## 已内置抽取器

在 `scripts/extract_sheet.py` 的 `EXTRACTORS` 里注册。

| key | 层 | 说明 |
|---|---|---|
| `rpgmaker-mvmz` | L0 | 事件码 101/401/405/102 + 全数据库层 + `System.terms` |
| `generic-plaintext` | L0 | JSON 深度遍历 + 字段名白名单 |
| `gettext` | L0 | `.po` 的 `msgid` / `msgstr` |

已注册引擎档案（`gameloc_core.ENGINES`，含 `detect` 与 `escape` 正则）：
`rpgmaker-mvmz` / `rpgmaker-xp-vx-ace` / `renpy` / `tyranoscript` / `gettext` /
`nscripter` / `godot` / `electron-nwjs` / `kirikiri` / `wolf-rpg` /
`unity` / `unreal` / `generic-plaintext`

**有档案 ≠ 有抽取器。** 档案解决「控制码怎么认、怎么打分」，
抽取器解决「文本在哪」。缺抽取器时按下面写一个。

---

## 未内置但可以直接写

文本位置见 [`../carriers.md`](../carriers.md)，
控制码语法见 [`../freeze-restore.md`](../freeze-restore.md)。

**L0 明文**
Ren'Py `.rpy` · TyranoScript / KAG `.ks` · NScripter `.txt` ·
Godot `.tscn` / `.tres` / `.translation` · Unity YAML 文本资产 ·
Electron / NW.js 内的 JSON · CSV / INI / YAML 型独立游戏

**L1 需解包**
Ren'Py `.rpyc` + `.rpa`（`unrpyc` + `rpatool`）·
RPG Maker XP/VX/Ace（Ruby Marshal，可能裹在 `.rgss3a`）·
Unity asset bundle / Addressables（UnityPy + Il2CppDumper）·
Unreal `.pak` / `.locres` / IoStore

**L2 二进制**
Wolf RPG（UberWolf）· KiriKiri XP3 · YU-RIS `.ypf` ·
AliceSoft `.ald` / `.afa` · LiveMaker `.lsb`（VFF）· SRPG Studio `data.dts`

---

## 加一个新引擎

### 1. 在 `gameloc_core.ENGINES` 注册档案

```python
_reg(EngineProfile(
    key="my-engine",
    title="我的引擎",
    layer="L0",
    detect=lambda r: 1.0 if _has(r, "data", "Script.json") else 0.0,
    escape=r'\\{[a-z]+\}|%d',           # 控制码正则，用于等值校验
    root_globs=["data"],
    notes="给未来的自己和用户看的提示。",
))
```

**置信度要分级。** 强特征（`app.asar`）给 1.0，
弱特征（光秃秃一个 `package.json`，几乎所有 node 项目都有）给 0.35 ——
否则一个 NW.js 游戏会同时被识别成 NW.js 和它真正用的 RPG Maker，排序混乱。

### 2. 在 `extract_sheet.EXTRACTORS` 注册抽取器

```python
def myengine_extract(data_root, rows):
    for fname in sorted(os.listdir(data_root)):
        ...
        _add(rows, "dialogue", fname, path, text)
```

签名就一个约束：`rows` 里每项要含 `carrier` / `file` / `path` / `src`。
`_add()` 会帮你跳过空串和纯空白、自动分配 `id`。

写完 `apply_sheet.py` 和 `audit.py` 都能直接用 ——
**统一中间格式是引擎无关的，这就是解耦点。**

### 3. 补三处文档

- [`../carriers.md`](../carriers.md) —— 文本路径
- [`../freeze-restore.md`](../freeze-restore.md) —— 控制码语法
- 本文件 —— 登记

### 4. 写测试

至少验证三件事：

1. 在**已知非空**的样本上返回了非零条数
   （忘传 `events` 导致全地图「零残留」那类 bug 只有这样才抓得住）
2. 抽出来的每条都能被 `set_path` 写回去 —— 用 `apply_sheet.py --dry` 验
3. 控制码正则能同时匹配正例与反例

写进 `tests/`，跑：

```bash
python -m pytest tests/        # 需要 pytest，仅测试用
python scripts/selftest.py     # 零依赖，核心断言都在这里
```

`tests/` 里的每个夹具都是**现场构造**的最小 JSON —— 这个仓库不含任何游戏文本。
新增引擎时照着 `tests/conftest.py` 的 `make_rmmz_game()` 加一个构造函数即可。
