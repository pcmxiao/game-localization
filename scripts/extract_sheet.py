#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_sheet —— 把游戏文本抽成一张翻译表。

用法:
    python extract_sheet.py --root <游戏目录> --engine <key> --out sheet.csv
    python extract_sheet.py --root <游戏目录> --engine auto --out sheet.csv

引擎可填 gameloc_core.ENGINES 里的 key，或 auto（自动识别）。
fallback 可以在 auto 认不出来时兜底。

设计要点
--------
1. 抽的是**玩家可见**文本。事件名、note 注释这些编辑器内部字段不进表 ——
   玩家看不见，翻了只增加风险。
2. 「哪些是玩家可见」由引擎 handler 判断，不由脚本猜。脚本只做遍历。
3. 默认**全量抽取**（不限语言）。用 --only-source-script 才按文字系统粗筛，
   而且筛完必须在说明里写清楚：粗筛会漏全汉字的外语词。
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gameloc_core import (  # noqa: E402
    ENGINES, SHEET_FIELDS, SheetError, eprint, open_sheet_for_write,
    read_json, resolve_engine, script_score,
)

# --------------------------------------------------------------------------
# RPG Maker MV / MZ
# --------------------------------------------------------------------------

# 事件指令码 → 是否携带玩家可见文本
RGMMZ_TEXT_CODES = {101, 401, 405}
RGMMZ_CHOICE_CODES = {102}

# 数据库字段 → 载体类别
RGMMZ_DB_FIELDS = [
    ("name", "db_name"),
    ("description", "db_desc"),
    ("nickname", "db_name"),
    ("profile", "db_desc"),
    ("battlerName", "db_name"),
    ("message1", "db_message"),
    ("message2", "db_message"),
    ("message3", "db_message"),
    ("message4", "db_message"),
]

RGMMZ_LIST_DB = ["Items.json", "Skills.json", "States.json", "Weapons.json",
                "Armors.json", "Actors.json", "Classes.json", "Enemies.json",
                "Troops.json"]

RGMMZ_SCALAR_SYS = ["currencyUnit", "title", "gameOver", "victory"]
RGMMZ_SCALAR_SYS_ARRAYS = ["skillTypes", "elements", "equipTypes", "weaponTypes",
                           "armorTypes", "params", "expParams"]


def _add(rows, carrier, file, path, text):
    """加一行。空串和纯空白不进表。"""
    if not isinstance(text, str) or not text.strip():
        return
    rows.append({
        "id": len(rows),
        "carrier": carrier,
        "file": file,
        "path": path,
        "src": text,
        "text": "",
        "status": "pending",
        "note": "",
    })


def rgmmz_scan_map(rows, fname, data):
    """Mapxxx.json：events[i].pages[j].list[k].parameters[0]。

    注意传入的必须是 data["events"]，不是整个 data —— 传错的话每个文件都会
    返回「0 条可见文本」，看起来像「全翻完了」，其实是「一个都没扫」。
    """
    for ei, ev in enumerate(data.get("events") or []):
        if not isinstance(ev, dict):
            continue
        for pi, pg in enumerate(ev.get("pages") or []):
            if not isinstance(pg, dict):
                continue
            for li, cmd in enumerate(pg.get("list") or []):
                if not isinstance(cmd, dict):
                    continue
                code = cmd.get("code")
                params = cmd.get("parameters") or []
                if not params:
                    continue
                base = f"events[{ei}].pages[{pi}].list[{li}].parameters[0]"
                if code in RGMMZ_TEXT_CODES and isinstance(params[0], str):
                    _add(rows, "dialogue", fname, base, params[0])
                elif code in RGMMZ_CHOICE_CODES and isinstance(params[0], list):
                    for ci, choice in enumerate(params[0]):
                        _add(rows, "choice", fname, f"{base}[{ci}]", choice)
                # 402 分支标签必须与 102 的选项原文一致 —— 不进表，不能翻。


def rgmmz_scan_common(rows, fname, data):
    for ei, ev in enumerate(data or []):
        if not isinstance(ev, dict):
            continue
        for li, cmd in enumerate(ev.get("list") or []):
            if not isinstance(cmd, dict):
                continue
            code = cmd.get("code")
            params = cmd.get("parameters") or []
            if not params:
                continue
            base = f"[{ei}].list[{li}].parameters[0]"
            if code in RGMMZ_TEXT_CODES and isinstance(params[0], str):
                _add(rows, "dialogue", fname, base, params[0])
            elif code in RGMMZ_CHOICE_CODES and isinstance(params[0], list):
                for ci, choice in enumerate(params[0]):
                    _add(rows, "choice", fname, f"{base}[{ci}]", choice)


def rgmmz_extract(data_root, rows):
    for fname in sorted(os.listdir(data_root)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(data_root, fname)
        try:
            data = read_json(path)
        except Exception as e:
            eprint(f"[跳过] {fname}: {e}")
            continue

        if fname == "MapInfos.json":
            for i, rec in enumerate(data or []):
                if isinstance(rec, dict):
                    _add(rows, "ui_label", fname, f"[{i}].name", rec.get("name"))
                    _add(rows, "ui_label", fname, f"[{i}].displayName",
                         rec.get("displayName"))
        elif fname == "CommonEvents.json":
            rgmmz_scan_common(rows, fname, data)
        elif re.fullmatch(r"Map\d+\.json", fname):
            rgmmz_scan_map(rows, fname, data)
        elif fname in RGMMZ_LIST_DB:
            for i, rec in enumerate(data or []):
                if not isinstance(rec, dict):
                    continue
                for key, carrier in RGMMZ_DB_FIELDS:
                    _add(rows, carrier, fname, f"[{i}].{key}", rec.get(key))
        elif fname == "System.json":
            terms = data.get("terms") or {}
            for cat, d in terms.items():
                if not isinstance(d, dict):
                    continue
                for key, val in d.items():
                    if isinstance(val, str):
                        _add(rows, "sys_term", fname, f"terms.{cat}.{key}", val)
                    elif isinstance(val, dict):
                        for k2, v2 in val.items():
                            _add(rows, "sys_term", fname,
                                 f"terms.{cat}.{key}.{k2}", v2)
            for key in RGMMZ_SCALAR_SYS:
                _add(rows, "sys_term", fname, key, data.get(key))
            for key in RGMMZ_SCALAR_SYS_ARRAYS:
                arr = data.get(key)
                if isinstance(arr, list):
                    for i, v in enumerate(arr):
                        if isinstance(v, str):
                            _add(rows, "sys_term", fname, f"{key}[{i}]", v)


# --------------------------------------------------------------------------
# 通用明文兜底
# --------------------------------------------------------------------------

# 这些字段名出现率高，但也最可能撞上标识符 —— 见 SKILL.md「不要翻译标识符」。
GENERIC_TEXT_FIELDS = {
    "name", "displayname", "title", "text", "label", "caption", "tooltip",
    "description", "message", "message1", "message2", "message3", "message4",
    "dialog", "dialogue", "line", "choice", "option", "prompt", "hint",
    "placeholder", "body", "content", "string",
}

# 这些字段名即使含人样文字也不该翻
GENERIC_SKIP_FIELDS = {
    "id", "key", "guid", "uuid", "hash", "version", "path", "file", "filename",
    "url", "type", "class", "tag", "tags", "icon", "sprite", "anim",
    "meta", "__collapsed__", "note", "comment", "comments", "m_name",
}

GENERIC_TEXT_EXT = {".json", ".yaml", ".yml", ".ini", ".csv", ".json5"}

GENERIC_LOCALE_DIRS = ("locale", "locales", "lang", "i18n", "translations",
                       "po", "resources")


def _generic_walk(node, prefix, sink, carrier):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in GENERIC_SKIP_FIELDS:
                continue
            child = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, str):
                if k in GENERIC_TEXT_FIELDS:
                    sink.append((child, carrier, v))
                # 非白名单字段不翻 —— 宁可漏，不要把标识符翻坏
            else:
                _generic_walk(v, child, sink, carrier)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            child = f"{prefix}[{i}]"
            if isinstance(v, str):
                sink.append((child, carrier, v))
            else:
                _generic_walk(v, child, sink, carrier)


def _generic_extract_file(path, rel, rows, json_only):
    sink = []
    if path.lower().endswith(".json"):
        try:
            data = read_json(path)
        except Exception as e:
            eprint(f"[跳过] {rel}: {e}")
            return
        _generic_walk(data, "", sink, "generic")
    elif not json_only:
        # 纯文本型（YAML/INI/CSV）只能按行猜，风险高，默认不进表
        return
    for p, carrier, text in sink:
        _add(rows, carrier, rel, p, text)


def generic_extract(root, rows, json_only=True):
    """通用兜底：只扫明文 JSON。

    刻意保守：只收白名单字段名，别的字符串一律不动。白名单不命中不代表没有
    文本，只代表「需要人看一眼这个文件长什么样再手工补」。
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d.lower() not in
                       ("node_modules", "__pycache__", "save", "saves",
                        "systemvolumeinformation")]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in GENERIC_TEXT_EXT:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if any(part.lower() in GENERIC_LOCALE_DIRS
                   for part in rel.split("/")[:-1]):
                carrier = "ui_label"
            else:
                carrier = "generic"
            _generic_extract_file(full, rel, rows, json_only)


# --------------------------------------------------------------------------
# gettext
# --------------------------------------------------------------------------

_PO_ENTRY = re.compile(
    r'^(msgid|msgid_plural|msgstr(?:\[(?P<idx>\d+)\])?)\s+"(?P<body>.*)"\s*$')
_PO_COMMENT = re.compile(r"^\s*(#|msgctxt)")


def po_extract(root, rows):
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            if not fn.endswith(".po"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            with open(full, encoding="utf-8", errors="replace") as f:
                cur_key = None
                cur_text = None
                for ln, line in enumerate(f, 1):
                    line = line.rstrip("\n")
                    if _PO_COMMENT.match(line):
                        cur_key = cur_text = None
                        continue
                    m = _PO_ENTRY.match(line)
                    if m:
                        kind = m.group(1)
                        body = m.group("body")
                        if kind.startswith("msgid"):
                            cur_key = f"L{ln}"
                            cur_text = body
                        elif cur_key is not None:
                            # msgstr 也当可填槽位，path 用 msgid 行号定位
                            idx = m.group("idx")
                            p = f"{cur_key}:{kind}{'[' + idx + ']' if idx else ''}"
                            _add(rows, "generic", rel, p, body)
                        continue
                    cur_key = cur_text = None


EXTRACTORS = {
    "rpgmaker-mvmz": rgmmz_extract,
    "generic-plaintext": generic_extract,
    "gettext": po_extract,
}


def main():
    ap = argparse.ArgumentParser(description="抽取游戏文本到统一翻译表")
    ap.add_argument("--root", required=True, help="游戏目录")
    ap.add_argument("--engine", default="auto",
                    help=f"引擎 key 或 auto。可用: {sorted(ENGINES)}")
    ap.add_argument("--out", default="sheet.csv", help="输出 CSV（UTF-8 BOM）")
    ap.add_argument("--data-root", default=None,
                    help="直接指定数据目录，跳过自动推断")
    ap.add_argument("--only-source-script", default=None,
                    help="只保留含该文字系统的行（如 kana/hangul）。"
                         "⚠️ 粗筛会漏掉全汉字的外语词，仅供分诊，不是判据")
    ap.add_argument("--all-text", action="store_true",
                    help="连 CSV/YAML/INI 里的散文本也收（风险更高）")
    args = ap.parse_args()

    root = os.path.abspath(args.root)

    if args.engine == "auto":
        from detect_engine import detect_all
        hits = detect_all(root)
        if not hits:
            eprint("没认出引擎。用 --engine generic-plaintext 走兜底，"
                   "或先跑 detect_engine.py 看看目录结构。")
            return 2
        engine = hits[0][0]
        eprint(f"自动识别: {engine.title} ({engine.key})")
    else:
        engine = resolve_engine(args.engine)

    if engine.key in EXTRACTORS:
        extractor = EXTRACTORS[engine.key]
        data_root = args.data_root
        if data_root is None:
            from detect_engine import find_data_roots
            roots = find_data_roots(root, engine)
            data_root = roots[0] if roots else root
        data_root = os.path.abspath(data_root)
        rows = []
        if engine.key == "generic-plaintext":
            extractor(root, rows, json_only=not args.all_text)
        else:
            extractor(data_root, rows)
    else:
        eprint(f"引擎 {engine.key} 目前没有内置抽取器。\n"
               f"「文本在哪」请读 references/carriers.md 的通用兜底一节，\n"
               f"照着写一个抽出 (file, path, text) 的函数，格式照 EXTRACTORS 里现有的。\n"
               f"统一中间格式是引擎无关的，写完就能用 apply_sheet.py 和 audit.py。")
        return 3

    if args.only_source_script:
        key = args.only_source_script
        before = len(rows)
        rows = [r for r in rows if key in script_score(r["src"])]
        eprint(f"粗筛 {key}：{before} → {len(rows)} 行 "
               f"（粗筛会漏全汉字的外语词，不能当「翻完了」的判据）")

    f, w = open_sheet_for_write(args.out)
    with f:
        for r in rows:
            w.writerow({k: r.get(k, "") for k in SHEET_FIELDS})

    by_carrier = {}
    for r in rows:
        by_carrier[r["carrier"]] = by_carrier.get(r["carrier"], 0) + 1

    print(f"抽取 {len(rows)} 行 → {args.out}")
    for c, n in sorted(by_carrier.items(), key=lambda t: -t[1]):
        print(f"  {c:<14}{n:>7}")

    if engine.escape:
        from collections import Counter
        from gameloc_core import escape_multiset
        cnt = Counter()
        for r in rows:
            cnt.update(escape_multiset(r["src"], engine.escape))
        print(f"\n控制码统计（译文必须原样保留，共 {sum(cnt.values())} 处）:")
        for code, n in cnt.most_common(20):
            print(f"  {code:<24}{n:>7}")

    print("\n下一步：")
    print("  1. 用 Excel / WPS 打开，只填 text 列（其它列别动）")
    print("  2. 填完先跑 apply_sheet.py --dry 检查控制码和路径")
    print("     —— 从表格粘贴会导致整列错位，dry 能精确定位到行号")
    print("  3. 确认无误再跑 apply_sheet.py 真正写入（会自动备份）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
