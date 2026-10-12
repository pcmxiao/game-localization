# -*- coding: utf-8 -*-
"""
gameloc_core —— 与引擎无关的核心。

这里只放「所有引擎都一样」的东西：
  · 翻译表的统一格式
  · 引擎档案注册表（只声明控制码语法这类声明式的东西）
  · 严格路径解析与 fail-closed 写入
  · 控制码等值校验
  · 带编码处理的读写

引擎特有的「文本在哪」逻辑放在各自脚本里，或现场写一个档案。
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import sys
from datetime import datetime

# --------------------------------------------------------------------------
# 翻译表统一格式
# --------------------------------------------------------------------------

# 一次抽取全部引擎共用这一张表。引擎只负责「怎么把文本变成这张表」。
SHEET_FIELDS = [
    "id",        # 表内唯一序号，0 起
    "carrier",  # 载体类别: dialogue / choice / db_name / db_desc /
                #            db_message / sys_term / ui_label / generic
    "file",     # 相对 game_root 的路径，用 / 分隔
    "path",     # 写回时的精确定位路径，如 events[6].pages[1].list[7].parameters[0]
    "src",      # 原文，写回前会拿它核对当前值
    "text",     # 译文（人填）
    "status",   # pending / done / skipped / keep_original
    "note",     # 备注，写给人看，程序忽略
]

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_KEEP = "keep_original"

# status 为空时的默认值
STATUS_DEFAULT = STATUS_PENDING


class SheetError(Exception):
    """翻译表相关的错误。路径不存在、空译文文件等。"""


# --------------------------------------------------------------------------
# 引擎档案注册表
# --------------------------------------------------------------------------
#
# 这里只声明「这个引擎的控制码长什么样」。文本位置不在这里 ——
# 那是 extract_sheet.py 里各引擎 handler 的事。
#
# 加新引擎：加一条 EngineProfile，声明 detect 特征和 escape 正则。

class EngineProfile:
    def __init__(
        self,
        key,
        title,
        layer,          # L0 明文 / L1 需解包 / L2 二进制 / L3 外挂 / L4 不可能
        detect,         # (game_root) -> 置信度 0..1
        escape,         # 匹配控制码/标记的正则（用于等值校验）
        root_globs,     # 文本根目录候选
        notes="",
        escape_verified=False,   # 这条正则是否在真实游戏上验证过
    ):
        self.key = key
        self.title = title
        self.layer = layer
        self._detect = detect
        self.escape = re.compile(escape) if escape else None
        self.root_globs = root_globs or []
        self.notes = notes
        # 有没有在真实游戏上跑过。没跑过的正则可能过松（拦下合法译文）
        # 或过紧（放过被改坏的控制码）。两种都不能当它不存在。
        self.escape_verified = escape_verified

    def detect(self, root):
        try:
            return self._detect(root)
        except Exception:
            return 0.0


def _has(root, *parts):
    return os.path.exists(os.path.join(root, *parts))


def _count(root, pattern):
    try:
        return len([n for n in os.listdir(root) if re.fullmatch(pattern, n)])
    except Exception:
        return 0


# RPG Maker MZ/MV：反斜杠 + 字母，后面可能跟一对方括号参数。
#
# 方括号里的内容必须一并纳入 token —— 否则 `\z[10]` 被写成 `\z[0010]`
# 或 `\z[1]` 都会得到同一个 token 集合，校验器就看不出差异。
# 实测里 idx 182 的形态是 `\z[0010]` 而不是 `\z[10]`，两种都要原样保留。
#
# 带表情差分/立绘插件的游戏还会出现 \m[n] \z[n] \ac\...\xxx\}，
# 同一个正则已经覆盖，不需要为插件单独加规则。
_RGMMZ_ESCAPE = r'\\(?:[A-Za-z]+(?:\[[^\]]*\])?|\{|\[|[^\w\s{])'

ENGINES = {}


def _reg(p):
    ENGINES[p.key] = p


_reg(EngineProfile(
    key="rpgmaker-mvmz",
    title="RPG Maker MV / MZ",
    layer="L0",
    detect=lambda r: (
        1.0 if (_has(r, "data", "System.json")
                and (_has(r, "js", "rmmz_managers.js")
                     or _has(r, "js", "rpg_managers.js")
                     or _has(r, "www", "data", "System.json")))
        else (0.5 if _has(r, "data", "System.json") and _has(r, "data", "MapInfos.json")
              else 0.0)
    ),
    escape=_RGMMZ_ESCAPE,
    root_globs=["data", "www/data"],
    notes="明文 UTF-8 JSON。events 指令码 101/401/405 文本、102 选项、402 分支标签对应不改。",
    escape_verified=True,
))

_reg(EngineProfile(
    key="rpgmaker-xp-vx-ace",
    title="RPG Maker XP / VX / VX Ace",
    layer="L1",
    detect=lambda r: 1.0 if _has(r, "Data", "System.rxdata") or _has(r, "Data", "System.rvdata2") else 0.0,
    escape=r'\\[A-Za-z]+|\$[A-Za-z]+(?:\[\d+\])?|\^|\{[^}]*\}',
    root_globs=["Data"],
    notes="Ruby Marshal 二进制，需先解包再处理；可能裹在 .rgss3a 里。",
))

_reg(EngineProfile(
    key="renpy",
    title="Ren'Py",
    layer="L0",
    detect=lambda r: 1.0 if (_has(r, "game") and (_has(r, "game", "options.rpy")
                                                or _has(r, "game", "scripts.rpy"))) else 0.0,
    # {tag} {tag=arg}　{/tag} 加上 [/tag] 闭合
    escape=r'\{/?[a-zA-Z_][^}]*\}|%%|\[\[[^\]]+\]\]',
    root_globs=["game"],
    notes=".rpy 是明文；只有 .rpyc/.rpa 时先跑 unrpyc / rpatool 展开。",
    escape_verified=True,
))

_reg(EngineProfile(
    key="tyranoscript",
    title="TyranoScript / KAG",
    layer="L0",
    detect=lambda r: 1.0 if _has(r, "data", "tyrano") or _has(r, "tyrano") else 0.0,
    escape=r'\[/?[a-z_][^\]\[]*(?:=[^\]]*)?\]',
    root_globs=["data", "tyrano"],
    notes="参数里也含文本（[cm name=\"..\"]），翻值不翻参数名和 target。",
    escape_verified=True,
))

_reg(EngineProfile(
    key="gettext",
    title="gettext (.po / .mo)",
    layer="L0",
    detect=lambda r: 1.0 if _count(r, r".*\.po") or _has(r, "locale") else 0.0,
    escape=r'%\d*\$?[sdxoufgecp]|%[0-9.#]*[a-zA-Z]|\{[a-zA-Z_][^}]*\}|<\d+>|%%|\bn\b',
    root_globs=["locale", "locales", "po", "lang"],
    notes="结构行（#: #. #|）不要动；msgid_plural / msgstr[n] 要成对处理。",
    escape_verified=True,
))

_reg(EngineProfile(
    key="nscripter",
    title="NScripter / ONScripter",
    layer="L0",
    detect=lambda r: 1.0 if (_has(r, "nscr_sec.exe") or _has(r, "script.txt")
                             or _has(r, "onscripter")) else 0.0,
    escape=r'\\[a-zA-Z]+|%[0-9]*\$?[a-zA-Z]',
    root_globs=["."],
    notes="# 开头是命令、* 开头是标签，都不能翻。乱码版要先还原编码。",
    # 未在真实游戏上验证过
    escape_verified=False,
))

_reg(EngineProfile(
    key="godot",
    title="Godot Engine",
    layer="L0",
    detect=lambda r: 1.0 if (_has(r, "project.godot") or _has(r, "data", "project.godot")) else 0.0,
    escape=r'\{[^{}]*\}|\[[^\]]*\]|%\d*\$?[sdf]',
    root_globs=["."],
    notes=".tscn/.tres 是明文；导出的 .pck 要用 GDRE Tools 解包再重打包。",
))

_reg(EngineProfile(
    key="electron-nwjs",
    title="Electron / NW.js",
    layer="L1",
    # package.json 单独存在太常见（几乎所有 node 项目都有），只给低置信度；
    # app.asar 才是强特征。
    detect=lambda r: (
        0.95 if (_has(r, "app.asar") or _has(r, "resources", "app.asar"))
        else (0.35 if _has(r, "package.json") and _has(r, "node_modules") else 0.0)
    ),
    escape=r'\{[^{}]*\}|%\d*\$?[sdf]|\$\{[^}]*\}',
    root_globs=["."],
    notes="asar 要先 asar extract；asar 优先于松散目录，只改松散文件不生效。",
))

_reg(EngineProfile(
    key="kirikiri",
    title="KiriKiri / KAG",
    layer="L2",
    detect=lambda r: 1.0 if _has(r, "data.xp3") else 0.0,
    escape=r'\[/?[a-z_][^\]\[]*(?:=[^\]]*)?\]|\$[a-zA-Z]+',
    root_globs=["."],
    notes="XP3 有多种加密。优先找能导出成松散文件覆盖的路线，别改原包。",
))

_reg(EngineProfile(
    key="wolf-rpg",
    title="Wolf RPG Editor",
    layer="L2",
    detect=lambda r: 1.0 if _has(r, "Data", "CommonEvent.dat") else 0.0,
    # 之前写过 `#|\|`，太松：`#` 和 `|` 是任意文本里都常见的字符，
    # 会把正常译文大量误拦。宁可标记成「语法未验证，跳过自动校验」，
    # 也不要一个看起来在工作、其实满屏噪声的正则。
    escape=None,
    root_globs=["."],
    notes="无 markup 可冻结。用 UberWolf 解包，再重打包成 .wolf。",
    escape_verified=False,
))

_reg(EngineProfile(
    key="unity",
    title="Unity",
    layer="L1",
    detect=lambda r: 1.0 if _has(r, "GameAssembly.dll") or _has(r, "data", "Managed") else 0.0,
    escape=r'<[^<>]+>|\\[nrt]|\\u[0-9A-Fa-f]{4}|\{[0-9]+\}',
    root_globs=["."],
    notes="YAML/JSON 文本资产可直改；bundle 里用 UnityPy + Il2CppDumper。IL2CPP 改文件常失败。",
))

_reg(EngineProfile(
    key="unreal",
    title="Unreal Engine 4 / 5",
    layer="L1",
    detect=lambda r: 1.0 if _has(r, "Engine", "Binaries") or _has(r, "*.uproject") else 0.0,
    escape=r'<[^<>]+>|\{[0-9]+\}|\\[nrt]',
    root_globs=["."],
    notes=".locres / .pak / IoStore。用 FModel 导出，改完作为覆盖包放进 Content/Paks。",
))

_reg(EngineProfile(
    key="generic-plaintext",
    title="通用明文兜底",
    layer="L0",
    detect=lambda r: 0.0,  # 兜底，不主动识别
    escape=None,
    root_globs=["."],
    notes="JSON / CSV / INI / YAML / Lua / .po 等明文容器。靠字段名启发式扫描，无控制码校验。",
))


# --------------------------------------------------------------------------
# 路径解析与 fail-closed 读写
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"([^\[\]]*)((?:\[\d+\])*)")

# 路径转义：`\.` 表示键名里的字面点号，`\[` `\]` 表示键名里的字面方括号。
# 见 parse_path —— 没有它，键名本身含 '.' 的 JSON 无法寻址。
_ESC_DOT = "\x00DOT\x00"
_ESC_LB = "\x00LB\x00"
_ESC_RB = "\x00RB\x00"


def escape_path_token(name):
    """把一个键名编码成路径片段，使其能被 parse_path 还原。

    抽取器在拼 path 时必须用它，否则键名里的 '.' / '[' / ']' 会被
    当成路径分隔符，导致写回落到错误的键上（或抛「路径不存在」）。
    """
    return (name.replace("\\", "\\\\")
                .replace(".", "\\.")
                .replace("[", "\\[")
                .replace("]", "\\]"))


def split_path(path):
    """按未转义的点号切分路径。`a\\.b.c` → ['a.b', 'c']。"""
    parts, buf, i = [], [], 0
    while i < len(path):
        ch = path[i]
        if ch == "\\" and i + 1 < len(path):
            buf.append(_ESC_DOT if path[i + 1] == "." else
                       _ESC_LB if path[i + 1] == "[" else
                       _ESC_RB if path[i + 1] == "]" else path[i + 1])
            i += 2
            continue
        if ch == ".":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def parse_path(path):
    """把 'events[6].pages[1].parameters[0][2]' 解析成 token 序列。

    严格版：任何看不懂的片段直接抛异常，不猜。

    为什么要严格：早先用 re.findall(r'([^\\[\\]]+)|\\[(\\d+)\\]', path) 解析
    'terms.messages.autosave' 时，会把整串当成一个键，然后在根字典里静默新建
    这个键 —— JSON 依然合法，不报错，diff 才知道出了事。

    转义：键名里的字面点号/方括号用 `\\.` `\\[` `\\]` 表示。抽取器负责用
    `escape_path_token()` 编码。没有转义时，键名含 '.' 的 JSON（如
    `{"ui.title": ...}` 这种扁平表）会被切成两个键 —— 若恰好同时存在嵌套
    的同名结构，就会静默写到错误的位置。
    """
    tokens = []
    for part in split_path(path):
        if part == "":
            continue
        # 先把转义占位符还原，再判断方括号下标（下标语法只对未转义括号生效）
        m = _TOKEN_RE.fullmatch(part)
        if not m:
            raise SheetError(f"路径片段无法解析: {part!r} (完整路径 {path!r})")
        name = (m.group(1).replace(_ESC_DOT, ".").replace(_ESC_LB, "[")
                .replace(_ESC_RB, "]"))
        idxs = re.findall(r"\[(\d+)\]", m.group(2))
        if name:
            tokens.append(("key", name))
        for i in idxs:
            tokens.append(("idx", int(i)))
    if not tokens:
        raise SheetError(f"空路径: {path!r}")
    return tokens


def _descend(obj, tokens, path):
    cur = obj
    for kind, k in tokens:
        if kind == "key":
            if not isinstance(cur, dict):
                raise SheetError(f"路径 {path!r} 中 {k!r} 处期望 dict，实际是 {type(cur).__name__}")
            if k not in cur:
                raise SheetError(f"路径不存在: 缺少键 {k!r} in {path!r}")
            cur = cur[k]
        else:
            if not isinstance(cur, list):
                raise SheetError(f"路径 {path!r} 中 [{k}] 处期望 list，实际是 {type(cur).__name__}")
            if k >= len(cur):
                raise SheetError(f"下标越界: [{k}] (长度 {len(cur)}) in {path!r}")
            cur = cur[k]
    return cur


def get_path(obj, path):
    """严格读取。路径不存在就抛 SheetError，绝不返回 None 了事。"""
    tokens = parse_path(path)
    parent = _descend(obj, tokens[:-1], path)
    kind, k = tokens[-1]
    if kind == "key":
        if k not in parent:
            raise SheetError(f"路径不存在: 缺少键 {k!r} in {path!r}")
        return parent[k]
    if k >= len(parent):
        raise SheetError(f"下标越界: [{k}] (长度 {len(parent)}) in {path!r}")
    return parent[k]


def set_path(obj, path, value, expect=None):
    """严格写入。返回 True 表示真的改了，False 表示新值与原值相同。

    expect 不为 None 时，先核对当前值；不匹配抛 SheetError —— 这是
    「翻译表是照着旧版原文填的」这类情况的唯一防线。
    """
    tokens = parse_path(path)
    parent = _descend(obj, tokens[:-1], path)
    kind, k = tokens[-1]
    if kind == "key":
        if k not in parent:
            raise SheetError(f"路径不存在: 缺少键 {k!r} in {path!r}")
        cur = parent[k]
    else:
        if k >= len(parent):
            raise SheetError(f"下标越界: [{k}] (长度 {len(parent)}) in {path!r}")
        cur = parent[k]

    if expect is not None and cur != expect:
        raise SheetError(
            f"原文对不上: {path}\n  表里 src: {expect!r}\n  游戏里现值: {cur!r}\n"
            f"  → 游戏可能在抽取之后被改过，或填错了行。先重新抽取这一批。"
        )
    if cur == value:
        return False
    if kind == "key":
        parent[k] = value
    else:
        parent[k] = value
    return True


# --------------------------------------------------------------------------
# 控制码等值校验
# --------------------------------------------------------------------------

def escape_multiset(text, pattern):
    """提取并排序控制码列表，用于比对。"""
    if not text:
        return []
    return sorted(pattern.findall(text))


def verify_escape(src, dst, pattern):
    """控制码多重集是否完全相等。相等返回 (True, None)，否则 (False, 差异说明)。"""
    if pattern is None:
        return True, None
    a = escape_multiset(src, pattern)
    b = escape_multiset(dst, pattern)
    if a == b:
        return True, None
    miss = [x for x in a if x not in b]
    extra = [x for x in b if x not in a]
    detail = []
    if miss:
        detail.append(f"缺少 {miss}")
    if extra:
        detail.append(f"多出 {extra}")
    if not detail:
        detail.append(f"数量不同: 原文 {len(a)} 个 → 译文 {len(b)} 个")
    return False, "；".join(detail)


# --------------------------------------------------------------------------
# 文件读写（带编码处理）
# --------------------------------------------------------------------------

READ_ENCODINGS = ["utf-8-sig", "utf-8", "cp932", "gb18030", "cp1252"]


def read_text(path, encoding=None):
    """读文本。encoding 为 None 时依次尝试常见编码。

    注意：读编码和写编码是两个独立决定。拿同一套假设读写会静默毁掉译文 ——
    日文 Shift-JIS 装不下中文/西里尔/阿拉伯文，硬塞会被悄悄替换掉。
    """
    if encoding:
        with io.open(path, encoding=encoding) as f:
            return f.read()
    last = None
    for enc in READ_ENCODINGS:
        try:
            with io.open(path, encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError as e:
            last = e
    raise SheetError(f"无法解码 {path}（试过 {READ_ENCODINGS}）: {last}")


def write_json(path, data, encoding="utf-8"):
    """写回 JSON。

    用紧凑分隔符，体积小很多。ensure_ascii=False 保证中日文正常显示。
    不要加 BOM —— RPG Maker 等引擎读 BOM 会出错。
    """
    with io.open(path, "w", encoding=encoding, newline="") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def read_json(path, encoding="utf-8"):
    with io.open(path, encoding=encoding) as f:
        return json.load(f)


def backup_dir(paths, dest_root, tag="backup"):
    """把若干路径复制到 dest_root/<tag>_<时间戳>/，返回备份目录。

    必须放在被备份目录之外 —— 否则清理脚本会把自己的备份一起删掉。
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(dest_root, f"{tag}_{stamp}")
    os.makedirs(dest, exist_ok=True)
    for p in paths:
        if not os.path.exists(p):
            continue
        base = os.path.basename(p.rstrip("\\/")) or "root"
        target = os.path.join(dest, base)
        if os.path.isdir(p):
            shutil.copytree(p, target, dirs_exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(p, target)
    return dest


def _looks_like_data_dir(path, probe_exts=(".json",)):
    """目录里是否直接躺着数据文件（而不是又一层子目录）。"""
    try:
        entries = os.listdir(path)
    except OSError:
        return False
    return any(e.lower().endswith(probe_exts) for e in entries)


def find_backup_root(backup, probe_exts=(".json",), max_depth=4):
    """在备份目录里找到真正的数据根。

    背景：`backup_dir()` 产出的是 `<dest>/<tag>_<时间戳>/<原名>/`，
    所以用户传进来的可能是：
      · 时间戳目录本身       → 它下面就是 data/，还要再下一层
      · `gameloc_backups/`   → 时间戳目录的父目录，要下两层
      · 直接就是 data/       → 已经对了

    旧实现只往下探**一层**，而且要求候选目录**直接**含 `.json`。传父目录时
    探测不触发，返回的目录里一条条目都抽不出来 —— 报告于是显示「备份可见
    条目: 0」，把所有文本都归到「新增条目」，看起来像是「全部通过」。
    一个静默的假全清，比报错危险得多。

    这里改成逐层广度优先：先看自己是不是数据根，再看下一层，最多 max_depth
    层。同一层有多个候选时取名字最大的（时间戳目录按字典序即时间序，
    即最新的一份）。找不到就返回原路径，由调用方决定怎么处理
    （audit.py 会对「抽出 0 条」硬报错）。
    """
    backup = os.path.abspath(backup)
    if not os.path.isdir(backup):
        return backup

    frontier = [backup]
    for _ in range(max_depth):
        for cand in frontier:
            if _looks_like_data_dir(cand, probe_exts):
                return cand
        nxt = []
        for cand in frontier:
            try:
                for name in sorted(os.listdir(cand)):
                    sub = os.path.join(cand, name)
                    if os.path.isdir(sub) and not name.startswith("."):
                        nxt.append(sub)
            except OSError:
                continue
        if not nxt:
            break
        # 同一层多个候选（多份时间戳备份）取最新的一份，不要混着看
        if len(nxt) > 1:
            nxt = [max(nxt, key=os.path.basename)]
        frontier = nxt

    for cand in frontier:
        if _looks_like_data_dir(cand, probe_exts):
            return cand
    return backup


# --------------------------------------------------------------------------
# 翻译表读写
# --------------------------------------------------------------------------

def open_sheet_for_write(path):
    f = io.open(path, "w", encoding="utf-8-sig", newline="")
    w = csv.DictWriter(f, fieldnames=SHEET_FIELDS, extrasaction="ignore")
    w.writeheader()
    return f, w


def open_sheet(path):
    f = io.open(path, encoding="utf-8-sig", newline="")
    r = csv.DictReader(f)
    rows = list(r)
    f.close()
    missing = [c for c in SHEET_FIELDS if c not in (r.fieldnames or [])]
    if missing:
        raise SheetError(f"翻译表缺少列: {missing}")
    return rows


def save_sheet(path, rows):
    f, w = open_sheet_for_write(path)
    with f:
        for row in rows:
            row = dict(row)
            row.setdefault("status", STATUS_DEFAULT)
            w.writerow({k: row.get(k, "") for k in SHEET_FIELDS})


def resolve_engine(key):
    if key in ENGINES:
        return ENGINES[key]
    raise SheetError(f"未知引擎 {key!r}，可用: {sorted(ENGINES)}")


# --------------------------------------------------------------------------
# 语言粗筛（只用于分诊，绝不能当判据）
# --------------------------------------------------------------------------

_SCRIPT_RANGES = {
    "kana": re.compile(r"[぀-ゟ゠-ヿ]"),          # 日语假名
    "hangul": re.compile(r"[가-힯ᄀ-ᇿ]"),
    "cyrillic": re.compile(r"[Ѐ-ӿ]"),
    "greek": re.compile(r"[Ͱ-Ͽ]"),
    "thai": re.compile(r"[฀-๿]"),
    "arabic": re.compile(r"[؀-ۿݐ-ݿ]"),
    "hebrew": re.compile(r"[֐-׿]"),
}


def script_score(text):
    """粗略判断文本里有哪些文字系统。

    ⚠️ 这个函数**不能用来判断「翻完了没」**。它看不见全汉字的日文词
    （勉強、料理、元気），也看不见用中点写的人名（李·爱铃）——
    两者都会给出错误结论。唯一可靠的判据是「与原始备份同路径下是否一致」，
    见 audit.py。

    它的正当用途：给译文文本做**分诊**，以及在抽取阶段粗筛「这行可能有外语」。
    """
    if not text:
        return {}
    return {name: len(rx.findall(text)) for name, rx in _SCRIPT_RANGES.items()
            if rx.search(text)}


def eprint(*a):
    print(*a, file=sys.stderr)


def setup_stdio():
    """把标准输出/错误切到 UTF-8。

    Windows 控制台默认 GBK，print 一个 ✔ 或 ✖ 就 UnicodeEncodeError。
    危险的不是报错本身 —— 是**报错之前已经打出来的那部分是乱码**，
    排查时很容易误判成数据坏了。

    光靠 stdout.reconfigure(encoding="utf-8") 不够：Python 会吐 UTF-8 字节，
    但控制台仍按系统代码页（GBK）解码，于是整篇变乱码。管道（`|`、`>`）更是
    直接被重编码一次。所以这里先把控制台代码页也切到 65001。

    输出需要长期保存时，用工具自带的 `--report out.json`（写 UTF-8 文件），
    不要靠重定向命令行输出。
    """
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


setup_stdio()
