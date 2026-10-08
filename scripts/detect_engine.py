#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
detect_engine —— 认引擎、判分层。

用法:
    python detect_engine.py <游戏目录>
    python detect_engine.py <游戏目录> --json

它只回答两个问题：
  1. 这是什么引擎？
  2. 落在哪一层（L0 可直改 / L1 需解包 / L2 二进制 / L3 只能外挂 / L4 不可能）？

第 2 个问题比第 1 个重要。翻两小时才发现是 L4，比一开始就说是 L4 强得多。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gameloc_core import ENGINES, eprint  # noqa: E402

LAYER_INFO = {
    "L0": ("明文文本", "直接改文件即可。"),
    "L1": ("需要解包", "先用现成工具展开成明文，读改后按需重新打包。"),
    "L2": ("二进制/加密", "需要专用解包器。优先找「导出成松散文件覆盖」的路线，别改原包。"),
    "L3": ("编译进产物", "改不了文件。改用外挂式翻译：Textractor / LunaTranslator / XUnity AutoTranslator。"),
    "L4": ("物理上不可能", "图片里烤死的字、烧录字幕视频、主机 ROM。只能 OCR 重绘或模拟器 Agent。"),
}


def detect_all(root):
    """返回 [(profile, confidence)]，按置信度降序。"""
    out = []
    for p in ENGINES.values():
        c = p.detect(root)
        if c > 0:
            out.append((p, c))
    out.sort(key=lambda t: -t[1])
    return out


def find_data_roots(root, profile):
    """在游戏目录里找出 profile 声明的数据根，返回存在的那些。"""
    found = []
    for g in profile.root_globs:
        p = os.path.normpath(os.path.join(root, g))
        if os.path.isdir(p):
            found.append(p)
    return found


def sniff_plaintext_files(root, limit=200):
    """兜底：数一下明文文本类文件的数量，给「通用明文」一个粗略信号。

    这不能证明任何一个具体引擎，只是告诉你「这里值得手工看一眼」。
    """
    pat = re.compile(r"\.(json|ya?ml|ini|csv|lua|po|tscn|tres|rpy|ks|translation|txt)$",
                     re.I)
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d.lower() not in
                       ("node_modules", "__pycache__", "save", "saves")]
        for fn in filenames:
            if pat.search(fn):
                n += 1
                if n >= limit:
                    return n
    return n


def main():
    ap = argparse.ArgumentParser(description="识别游戏引擎并判断可汉化程度")
    ap.add_argument("game_root", help="游戏目录（不是 exe）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--min-confidence", type=float, default=0.3,
                    help="低于此置信度的结果不显示（默认 0.3）")
    args = ap.parse_args()

    root = os.path.abspath(args.game_root)
    if not os.path.isdir(root):
        eprint(f"不是目录: {root}")
        return 2

    hits = [(p, c) for p, c in detect_all(root) if c >= args.min_confidence]
    plain = sniff_plaintext_files(root)

    if args.json:
        print(json.dumps({
            "game_root": root,
            "detected": [
                {"key": p.key, "title": p.title, "layer": p.layer,
                 "confidence": c, "data_roots": find_data_roots(root, p),
                 "notes": p.notes}
                for p, c in hits
            ],
            "plaintext_file_count": plain,
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"游戏目录: {root}")
    print(f"明文文本类文件（粗数）: {plain}\n")

    if not hits:
        print("没有匹配到已知引擎。")
        print("\n下一步：")
        print("  1. 看一眼目录里有什么（引擎通常有特征文件）")
        print("     - RPG Maker MZ  : data/System.json + js/rmmz_managers.js")
        print("     - RPG Maker MV  : data/System.json + www/ 或 js/rpg_managers.js")
        print("     - Ren'Py        : game/options.rpy 或 game/scripts.rpy")
        print("     - TyranoScript  : data/tyrano/ 或 tyrano/")
        print("     - Unity         : GameAssembly.dll 或 data/Managed/")
        print("     - Unreal        : Engine/Binaries/ 或 *.uproject")
        print("     - KiriKiri      : data.xp3")
        print("     - Wolf RPG      : Data/CommonEvent.dat")
        print("  2. 找语言目录：i18n/ locale/ locales/ lang/ translations/ *.po")
        print("     找到语言目录基本就锁定了引擎。")
        print("\n如果确认是明文容器（JSON/CSV/INI/YAML/Lua/.po），")
        print("可以直接用 extract_sheet.py --engine generic-plaintext 走通用兜底。")
        return 0

    for p, c in hits:
        name, how = LAYER_INFO[p.layer]
        print(f"✔ {p.title}  ({p.key}, 置信度 {c:.2f})")
        print(f"  分层 {p.layer} — {name}")
        print(f"  → {how}")
        roots = find_data_roots(root, p)
        if roots:
            print(f"  数据根候选: {', '.join(roots)}")
        if p.notes:
            print(f"  注意: {p.notes}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
