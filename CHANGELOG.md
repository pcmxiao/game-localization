# Changelog

本文件记录**对使用者有影响的变化**。格式参考
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.1] — 2026-10-09

复检之后的一轮修正。主要是把「没测过的东西」标出来，而不是假装它可靠。

### 新增

- **英文 README**（`README.md`），中文版移到 `README.zh-CN.md`，两份互相链接
- **`SKILL.md` 顶部英文规则速览** —— 国际用户和非中文 Agent 能先看懂规则，
  再看下面的中文详细版

### 修复

- **`wolf-rpg` 的控制码正则过松**：原来用 `#` 和 `|` 当控制码，
  而这两个字符在任意文本里都常见，会把大量正常译文误拦。
  改为**不自动校验**并标注语法未验证 —— 宁可承认没把握，
  也不要一个看起来在工作、其实满屏噪声的正则
- **真正的问题：11 个引擎的控制码正则从未被验证过。** 只有 RPG Maker MV/MZ
  在真实游戏上跑过。现已补上 12 个引擎 × 3 项测试（正例全命中 / 普通文本不误报 /
  等值校验拦得住缺失）
- `EngineProfile` 增加 `escape_verified` 字段；`apply_sheet.py` 用到未验证的正则时
  会主动打印警告，而不是让使用者把「没报警」当成「没问题」
- `README` 里的作者本地路径改为占位符 `<repo-path>`

### 数字

- `selftest.py` 从 36 项扩到 **74 项断言**
- 控制码正则：**4 个已验证**（rpgmaker-mvmz / renpy / tyranoscript / gettext），
  **7 个未验证**（会在使用时提示），**1 个不校验**（wolf-rpg）

## [0.1.0] — 2026-10-08

首个版本。提炼自一个真实的 RPG Maker MZ 汉化项目
（77,257 条可见文本，44,158 处控制码，最终达到假名层面 100% 覆盖）。

### 新增

**流程（`SKILL.md`）**
- 七步流水线：备份 → 识别引擎与分层 → 抽取 → 翻译 → 写回 → 审计 → 清理
- 七条铁律，全部来自实战踩坑：
  - 审计判据是「与原始备份同路径一致」，不是字符集检测
  - 写回 fail-closed，路径不存在就抛异常
  - 控制码逐条冻结-还原并做等值校验
  - 先 `--dry` 再写盘（应对 Excel 粘贴串行）
  - 按路径写而非按文本查找替换
  - 写完回读 + diff
  - 不翻译标识符
- 引擎分层模型（L0 明文 / L1 需解包 / L2 二进制 / L3 只能外挂 / L4 不可能），
  强调**先判断层级再动手**

**脚本（`scripts/`）**
- `gameloc_core.py` — 引擎无关核心：统一翻译表格式、引擎档案注册表、
  严格路径解析、fail-closed 写入、控制码等值校验、带编码处理的读写
- `detect_engine.py` — 认引擎 + 判分层，置信度分级输出
- `extract_sheet.py` — 抽取到统一翻译表；内置 `rpgmaker-mvmz` /
  `generic-plaintext` / `gettext` 三个抽取器
- `apply_sheet.py` — fail-closed 写回，`--dry` / `--only-verify` 两种校验模式，
  自动备份到游戏目录之外
- `audit.py` — 对照备份验收。**复用抽取器**以保证审计范围等于抽取范围，
  并对「与原文一致」的条目做自动分诊

**文档（`references/`）**
- `carriers.md` — 各引擎文本路径 + 明文型引擎的通用兜底写法
- `freeze-restore.md` — 各引擎控制码的冻结与还原语法
- `pitfalls.md` — 实战踩坑清单，含踩坑前后的数字对比
- `release.md` — 发布前清理，含「绝不删」清单
- `engines/index.md` — 引擎分层、索引、新增引擎的步骤

### 已知限制

- 内置抽取器只覆盖 RPG Maker MV/MZ、明文 JSON 兜底、gettext 三种
- 其余引擎需按 `references/engines/index.md` 现场写抽取器
- 不处理图片里的文字（需 OCR + 重绘），不处理烧录视频
- 加密存档（`.rgss3a`、`.pck`）内的文本不扫描

[0.1.1]: https://github.com/pcmxiao/game-localization/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/pcmxiao/game-localization/releases/tag/v0.1.0
