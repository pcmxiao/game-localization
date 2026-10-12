# game-localization

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-compatible-orange.svg)](https://agentskills.io/)

[English](README.md) · **简体中文**

> 把任何类型的单机游戏改成你的语言 —— 不只 RPG Maker。

一套给 AI Agent 用的**引擎无关游戏本地化流程**：识别引擎 → 抽出文本 →
保护控制码翻译 → fail-closed 写回 → 对照备份验收。附六个能直接跑的 Python 脚本，
无第三方依赖。

**核心主张**：引擎只影响「文本在哪、语法长什么样」，不影响流程。
引擎特有的部分被压缩成一张小档案，**加新引擎 = 加一个档案**，不用重写流水线。

---

## 为什么不用现成工具

现成的游戏汉化工具不少（[RPGMTL](https://github.com/MizaGBF/RPGMTL)、
[rvpacker-txt-rs](https://github.com/RPG-Maker-Translation-Tools/rvpacker-txt-rs)、
[RPGMLocalizer](https://github.com/Lord0fTurk/RPGMLocalizer)、RuneTranslate…），
它们解决的问题是真问题。这套流程补的是它们没覆盖或做得不够的地方：

| # | 差异点 | 别人怎么做 | 这里怎么做 |
|---|---|---|---|
| 1 | **控制码保护范围** | 只保护 `\V \C \N \I` 等通用码 | 正则覆盖引擎该有的**全部**控制码。实测某游戏 83% 的控制码是 `\m[n]`（表情差分）和 `\z[n]`（立绘姿态）——通用保护完全漏掉这两类，改错必崩 |
| 2 | **写入语义** | 路径找不到就新建 | **fail-closed**：路径不存在就抛异常。理由见下 |
| 3 | **完成度判据** | 「还含假名吗」 | **与原始备份同路径逐条对比**。理由见下 |
| 4 | **Excel 粘贴** | 多数不提 | 内建检测，dry-run 能精确定位到行号 |
| 5 | **内容分级** | 要么全翻要么不过滤 | 系统层 / 敏感内容分开处理，跳过项留记录 |
| 6 | **发布前清理** | 多数不提 | 独立清单，含「绝不删」列表 |
| 7 | **批次原子性** | 出错留下半张补丁 | 先对**整批**只读预检，全过了才写。一条坏行 → 磁盘一个字节没动（退出码 `6`） |

### 关于第 7 条

校验和写入原来是同一遍做的，所以第 3 行的 src 对不上时，第 1、2 行早就落盘了 ——
留下半张补丁，比完全没写更难查。现在退出码把两类失败分开，因为处理方式正好相反：

| 码 | 含义 | 该做什么 |
|---|---|---|
| `0` | 成功 / dry-run 通过 | 继续 |
| `5` | 回读不一致 —— 写进去的和预期不同 | **立刻从备份恢复** |
| `6` | 预检失败，一个字节都没写 | 改表，重跑 |
| `7` | 写入/回读阶段失败 | 从备份恢复相关文件 |

### 关于第 2 条

早先的写入函数用 `re.findall(r'([^\[\]]+)|\[(\d+)\]', path)` 解析路径。
对 `terms.messages.autosave` 这种没有方括号的键，它会把**整串**当成一个键，
然后在根字典静默新建这个键。**JSON 依然合法，不抛异常，不报错** —— 直到 diff 才发现。

`fail-closed` 的意思就是：宁可吵一点，也不要静默写坏。

### 关于第 3 条

用「还含假名吗」判断翻没翻完，会同时犯两个方向的错：

- **漏** —— 全汉字的外语词一个都检测不到。「勉強」「料理」「元気」「statistics」通篇汉字，
  假名为零，看起来是「翻好了」，其实一个字没动。
- **误** —— 目标语言里合法的内容会报警。中文的间隔号「李·爱铃」、日文长音符「ーーー」、
  有意保留的角色原名，全都会被当成「没翻」。

实测：某次扫描残留 73 条，逐条核实后**只有 35 条是真漏翻**，其余 38 条是保留的原名、
名字前缀、中点和长音符。**「残留 N 条」不等于「漏翻 N 条」。**

`audit.py` 报的是候选，不是判决，而且会把明显不用管的先自动排除掉
（实测把 25,175 条「一致」压到 545 条待看，24,627 条是纯 ASCII 资源标识符）。

---

## 安装

这个仓库同时是标准 Agent Skill 和一组独立 CLI 脚本，用法二选一。

### 作为 Agent Skill

```
your-skills-dir/
└── game-localization/
    ├── SKILL.md
    ├── references/
    ├── scripts/
    └── assets/
```

放进所用 Agent 的技能目录即可。支持 Claude Code / OpenClaw / WorkBuddy 等
遵循 [Agent Skills 开放标准](https://agentskills.io/) 的客户端。

> `SKILL.md` 与 `references/` 是中文写的（这套流程在日→中上验证）。
> 脚本、CLI 输出和引擎档案与语言无关。`SKILL.md` 顶部有英文的规则速览。

Windows 上如果仓库放在别的盘，用目录联接免去复制：

```cmd
mklink /J "%USERPROFILE%\.openclaw\skills\game-localization" "<仓库路径>"
```

### 作为独立脚本

无第三方依赖，Python 3.8+：

```bash
git clone https://github.com/pcmxiao/game-localization.git
cd game-localization/scripts
python detect_engine.py "D:\Games\某个游戏"
```

---

## 五步用法

### 1. 认引擎

```bash
python scripts/detect_engine.py <游戏目录>
```

输出引擎名 + **分层**（这一步比引擎名更重要）：

| 层 | 含义 | 处理 |
|---|---|---|
| **L0** | 明文文本 | 直接改 ✅ |
| **L1** | 需要解包 | 专用工具展开 → 改明文 → 重打包 |
| **L2** | 二进制 / 加密 | 专用解包器；优先找「导出松散文件覆盖」路线，别改原包 |
| **L3** | 编译进 exe | 改不了 → Textractor / LunaTranslator / XUnity AutoTranslator |
| **L4** | 物理不可能 | 图片里烤死的字、烧录字幕、主机 ROM → OCR 重绘 / 模拟器 Agent |

**提前知道是 L4，比翻两小时才发现强。**

### 2. 抽取

```bash
python scripts/extract_sheet.py --root <游戏目录> --engine auto --out sheet.csv
```

产出统一中间格式，Excel 可直接打开：

```
id,carrier,file,path,src,text,status,note
```

只抽**玩家可见**文本。事件名、`note` 注释、资源标识符不进表 ——
玩家看不见，翻了只增加风险。

### 3. 翻译 → 4. 写回

用 Excel / WPS 填 `text` 列，**其它列别动**。

```bash
# 先验，永远先验
python scripts/apply_sheet.py --root <游戏目录> --sheet sheet.csv --dry

# 确认无误再写（自动备份到游戏目录之外）
python scripts/apply_sheet.py --root <游戏目录> --sheet sheet.csv
```

`--dry` 会检查控制码多重集是否相等、路径是否存在、原文是否对得上。
从表格粘贴会导致**整列错位**（实测中招过两次，且肉眼看不出来），dry 能精确定位到行号。

### 5. 验收

```bash
python scripts/audit.py --root <游戏目录> --backup <备份目录> --report audit.json
```

`--backup` 给时间戳备份目录或 `gameloc_backups/` 父目录都行 ——
会自动向下探测，多份备份时取最新的。

**备份抽出 0 条可见文本时会直接报错退出，而不是给报告。**
静默的零看起来像「通过」，恰恰是最不能信的那种结果。
（这是修过的一个真 bug：传父目录会得到一份看起来很干净的报告，
里面所有条目都被归成「新增条目」。）

对照备份逐条比对，输出四类清单：

```
条目总数                          77257
  ✓ 已改动                        51842
  · 与原文一致但不用管            24630  ← 自动分诊排除
  ⚠ 与原文一致且需要判断            545  ← 逐条看
  ⚠ 已改动但仍含外语字符             38  ← 粗筛，需核实
  ⚠ 文本槽里装着代码                 0  ← 别乱翻
```

最后按 [`references/release.md`](references/release.md) 清理。

---

## 加一个新引擎

引擎档案只回答五个问题：

| 字段 | 含义 |
|---|---|
| `detect` | 靠哪些目录 / 文件特征认出它 |
| `open` | 明文还是要先解包 |
| `carriers` | 文本在哪些文件的哪些字段 |
| `freeze` | 该引擎的控制码 / 标记语法 |
| `seal` | 写回什么产物（覆盖目录 / patch 文件 / 重打包） |

**中间那张翻译表是引擎无关的** —— 这就是解耦点。写一个 handler，
产出 `(file, path, text)`，后面的 `apply_sheet.py` 和 `audit.py` 都能直接用。

没档案也不影响干活：对任何明文容器（JSON / CSV / INI / YAML / Lua / `.po`），
[`references/carriers.md`](references/carriers.md) 给了通用兜底写法 ——
深度遍历 + 字段名启发式，现场就能写一个。

### 已内置

RPG Maker MV/MZ · 通用明文兜底 · gettext

其余引擎的文本位置见 [`references/carriers.md`](references/carriers.md)，
控制码语法见 [`references/freeze-restore.md`](references/freeze-restore.md)。

---

## 目录

```
game-localization/
├── SKILL.md                      # 流程与铁律（Agent 读这个）
├── scripts/
│   ├── gameloc_core.py           # 与引擎无关的核心
│   ├── detect_engine.py          # 认引擎 + 判分层
│   ├── extract_sheet.py          # 抽取到翻译表
│   ├── apply_sheet.py            # fail-closed 写回
│   ├── audit.py                  # 对照备份验收
│   └── selftest.py               # 自检，每条对应一个真踩过的坑
├── tests/                        # 端到端测试（pytest）
│   ├── conftest.py               # 夹具：现场构造最小合成游戏
│   ├── test_audit_backup.py      # 备份定位 + 假全清守卫
│   ├── test_apply_atomic.py      # 批次原子性 + 退出码
│   └── test_paths.py             # 路径转义 + 抽取→写回往返
├── references/
│   ├── carriers.md               # 各引擎文本在哪
│   ├── freeze-restore.md         # 控制码冻结与还原
│   ├── pitfalls.md               # 实战踩坑清单
│   ├── release.md                # 发布前清理
│   └── engines/index.md          # 引擎分层与索引
├── assets/
│   └── glossary.template.csv     # 术语表模板
└── evals/
    └── evals.json
```

---

## 测试

```bash
python scripts/selftest.py     # 零依赖 —— 核心断言都在这儿
python -m pytest tests/        # 端到端（需要 pytest，只用于测试）
```

CI 每次推送都会在 Linux + Windows、Python 3.8 ~ 3.13 上跑这两套。
`tests/` 里的夹具都是**现场构造**的最小合成游戏 ——
**仓库不含任何游戏文本**，所以测试不牵扯任何作品的版权。

---

## 这个流程是怎么来的

从一个真实的 RPG Maker MZ 汉化项目提炼：77,257 条可见文本，44,158 处控制码
（其中 `\m` 和 `\z` 占 83%），前后发现并修掉了十几个**静默失败**的问题 ——
路径解析吞掉键名、扫描脚本忘了传 `events` 导致 139 张地图全部「零残留」的假象、
筛查正则匹配到空串导致 498 行全假阳性、词根匹配把「メイク」（化妆）判成露骨内容。

这些全都在 [`references/pitfalls.md`](references/pitfalls.md) 里。
仓库里不含任何游戏文本、译文或术语表 —— 每个游戏的译文都是那个游戏独有的，不属于工具。

---

## License

MIT
