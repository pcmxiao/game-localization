#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit —— 最终验收。判据是「与原始备份同路径是否一致」，不是「看起来像外语」。

用法:
    python audit.py --root <游戏目录> --backup <备份目录> [--sheet sheet.csv]
    python audit.py --root ... --backup ... --report audit.json

为什么不用字符集检测
--------------------
用「还含假名吗」判断翻没翻完，会同时犯两个方向的错：

  · **漏** —— 全汉字的外语词一个都检测不到。「勉強」「料理」「元気」「statistics」
    通篇汉字，假名为零，看起来是「翻好了」，其实一个字没动。
  · **误** —— 目标语言里合法的内容会报警。中文的间隔号「李·爱铃」、
    日文长音符「ーーー」、有意保留的角色原名，都会被当成「没翻」。

实测：某次扫描残留 73 条，逐条核实后只有 35 条是真漏翻，其余 38 条是保留的原名、
名字前缀、中点和长音符。**「残留 N 条」不等于「漏翻 N 条」，必须逐条看。**

审计范围 = 抽取范围
--------------------
这个脚本**调用和 extract_sheet.py 完全相同的抽取器**来枚举条目。
这不是偷懒，是硬要求：

审计和抽取如果用两套「哪些算玩家可见」的定义，就会出现这种荒唐结果 ——
把 `note` 注释、事件名、插件参数、`.meta` 的 GUID 全算进来，
报告说「481,257 条没翻」，其实里面 99% 玩家根本看不见。

同一套定义，报告才有意义。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gameloc_core import (  # noqa: E402
    SheetError, eprint, open_sheet, read_json, resolve_engine, script_score,
)
import extract_sheet  # noqa: E402

# 意图保留标记：note 里出现这些算「主动保留」，不算漏翻
KEEP_MARKERS = ("keep_original", "保留原名", "保留", "keep", "原名", "follow")

# ------------------------------------------------------------------
# 「文本槽里装着代码」的启发式
# ------------------------------------------------------------------
#
# 有些游戏把插件脚本写在 101/401 的文本槽里，靠插件在运行时执行。
# 这些内容玩家**看不见**，但**改错会改行为** —— 比如把立绘状态名
# 'あBODY 汗03' 改成 'まHEAD 汗 02'，画面直接变了。
#
# 这类条目既不是「没翻」，也不该当成普通译文。它们要单独标出来给人看。

_CODE_HINTS = [
    (re.compile(r"^[A-Za-z_$][\w$.]*\s*\("), "函数调用"),
    (re.compile(r"\b(function|var|let|const|return|if|else|for|while)\b"), "JS 关键字"),
    (re.compile(r"^\s*[\$@]"), "变量/实例引用"),
    (re.compile(r"\b\w+\.[a-zA-Z_]\w*\s*\([^)]*\)\s*;?\s*$"), "方法调用"),
    (re.compile(r"\{\{.*\}\}|\$\{.*\}"), "模板/插值"),
    (re.compile(r"^\s*(local|function|end|if then|elseif)\b"), "Lua"),
    (re.compile(r"&\w+;|^\s*#"), "实体引用/指令"),
]


def looks_like_code(text):
    if not text:
        return None
    for rx, label in _CODE_HINTS:
        if rx.search(text):
            return label
    return None


# ------------------------------------------------------------------
# 「与原文完全一致」的自动分诊
# ------------------------------------------------------------------
#
# 报告里「与原文完全一致」动辄上万条，但其中绝大部分**根本不用管**：
#   · 裸控制码（只有一个 `\^`）
#   · 资源标识符（`s_TVkun`、`actor1`）
#   · 中日同字的人名地名（`夏乃`、`静`）—— 两种语言写法本来就一样
# 不做分诊，人就得手动翻两万个条目。

_BARE_CTRL = re.compile(r"^[\s\\\{\}\[\]^$._|><\-–—…、。，！？「」『』（）]*$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ .\-]*$")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")
_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")


def triage_identical(text):
    """给「与原文完全一致」的条目分类。返回 'ignore' 或 'review'。"""
    t = (text or "").strip()
    if not t:
        return "ignore", "空串"
    if _BARE_CTRL.match(t) and "\\" in t:
        return "ignore", "只有控制码，没有正文"
    if _IDENTIFIER.match(t):
        return "ignore", "纯 ASCII 标识符"
    has_cjk = bool(_CJK.search(t))
    has_kana = bool(_KANA.search(t))
    if has_cjk and not has_kana:
        # 全汉字，外语源语言下极可能就是中日同字词或已汉化的专有名词
        return "review", "纯汉字（可能是中日同字，需确认）"
    if has_kana:
        return "review", "含假名（大概率真漏翻）"
    return "review", "其它"


def extract_map(engine_key, data_root):
    """用指定引擎的抽取器跑一遍，返回 {(file, path): src_text}。"""
    from gameloc_core import ENGINES
    extractor = extract_sheet.EXTRACTORS.get(engine_key)
    if extractor is None:
        raise SheetError(f"引擎 {engine_key} 没有内置抽取器，无法确定审计范围")
    rows = []
    if engine_key == "generic-plaintext":
        extractor(data_root, rows, json_only=True)
    else:
        extractor(data_root, rows)
    return {(r["file"], r["path"]): r["src"] for r in rows}, rows


def sheet_status_map(sheet_path):
    """从翻译表拿 (file, path) -> (status, note)。"""
    out = {}
    if not sheet_path or not os.path.exists(sheet_path):
        return out
    try:
        for r in open_sheet(sheet_path):
            out[(r["file"], r["path"])] = (
                (r.get("status") or "").strip().lower(),
                (r.get("note") or "").strip(),
            )
    except SheetError as e:
        eprint(f"翻译表读取失败（继续，仅按备份对比）: {e}")
    return out


def main():
    ap = argparse.ArgumentParser(description="对比备份验收：找出真正没翻的条目")
    ap.add_argument("--root", required=True, help="游戏目录")
    ap.add_argument("--data-root", default=None, help="当前数据目录（默认自动推断）")
    ap.add_argument("--backup", required=True, help="备份目录（data/ 的副本）")
    ap.add_argument("--engine", default="auto")
    ap.add_argument("--sheet", default=None,
                    help="翻译表。给了就能把「主动保留」标出来")
    ap.add_argument("--report", default=None, help="把结果写成 JSON")
    ap.add_argument("--sample", type=int, default=25, help="终端显示多少条样例")
    ap.add_argument("--min-script-hits", type=int, default=1,
                    help="「已改动但仍疑似外语」的阈值")
    args = ap.parse_args()

    root = os.path.abspath(args.root)

    if args.engine == "auto":
        from detect_engine import detect_all
        hits = detect_all(root)
        engine = hits[0][0] if hits else resolve_engine("generic-plaintext")
    else:
        engine = resolve_engine(args.engine)

    if args.data_root:
        data_root = os.path.abspath(args.data_root)
    else:
        from detect_engine import find_data_roots
        roots = find_data_roots(root, engine)
        data_root = roots[0] if roots else root

    backup = os.path.abspath(args.backup)
    # 备份里通常还套了一层同名目录
    if not any(f.endswith(".json") for f in os.listdir(backup)):
        for cand in sorted(os.listdir(backup)):
            sub = os.path.join(backup, cand)
            if os.path.isdir(sub) and any(f.endswith(".json") for f in os.listdir(sub)):
                backup = sub
                break

    if engine.key not in extract_sheet.EXTRACTORS:
        eprint(f"引擎 {engine.key} 没有内置抽取器，audit 无法确定审计范围。\n"
               f"请照着 extract_sheet.py 的 EXTRACTORS 写一个，或直接手工核对。")
        return 3

    print(f"引擎    : {engine.title} ({engine.key})")
    print(f"当前    : {data_root}")
    print(f"备份    : {backup}")
    print(f"\n正在按与 extract_sheet.py 相同的规则枚举条目…")

    cur_map, cur_rows = extract_map(engine.key, data_root)
    bak_map, _ = extract_map(engine.key, backup)
    print(f"  当前可见条目: {len(cur_map)}")
    print(f"  备份可见条目: {len(bak_map)}")

    sheet = sheet_status_map(args.sheet)

    identical = []        # 与原文完全一致，且需要人工判断
    identical_ignored = []  # 与原文完全一致，但基本不用管
    suspect = []          # 改了但仍含外语字符
    changed_ok = 0
    keep_marked = 0
    code_in_text = []     # 文本槽里装着代码
    new_paths = []        # 备份里没有、当前新增的（一般是插件动态生成，不该进表）

    ignore_reasons = defaultdict(int)

    for key, cur in cur_map.items():
        orig = bak_map.get(key)
        fname, path = key

        if orig is None:
            new_paths.append(key)
            continue

        if cur == orig:
            status, note = sheet.get(key, ("", ""))
            if status == "keep_original" or any(m in note for m in KEEP_MARKERS):
                keep_marked += 1
                continue
            verdict, why = triage_identical(cur)
            if verdict == "ignore":
                identical_ignored.append((fname, path, cur, why))
                ignore_reasons[why] += 1
            else:
                identical.append((fname, path, orig, note, why))
            continue

        # 已经改动了
        code_kind = looks_like_code(cur) or looks_like_code(orig)
        if code_kind:
            code_in_text.append((fname, path, cur, orig, code_kind))
            continue

        hits = script_score(cur)
        if hits and sum(hits.values()) >= args.min_script_hits:
            suspect.append((fname, path, cur, orig, hits))
        else:
            changed_ok += 1

    total = (len(identical) + len(identical_ignored) + len(suspect) + changed_ok
             + keep_marked + len(code_in_text) + len(new_paths))

    print()
    print("=" * 68)
    print("验收报告")
    print("=" * 68)
    print(f"审计范围 = 抽取范围（玩家可见文本，{engine.key}）")
    print()
    print(f"条目总数                          {total:>8}")
    print(f"  ✓ 已改动                        {changed_ok:>8}")
    print(f"  — 已标记为有意保留              {keep_marked:>8}")
    print(f"  · 与原文一致但不用管            {len(identical_ignored):>8}  ← 自动分诊排除")
    print(f"  ⚠ 与原文一致且需要判断          {len(identical):>8}  ← 逐条看")
    print(f"  ⚠ 已改动但仍含外语字符          {len(suspect):>8}  ← 粗筛，需核实")
    print(f"  ⚠ 文本槽里装着代码              {len(code_in_text):>8}  ← 别乱翻")
    print(f"  · 备份中没有的新增条目          {len(new_paths):>8}  ← 一般无需处理")
    print()

    if identical_ignored:
        print(f"已自动排除 {len(identical_ignored)} 条「与原文一致但不用管」的条目：")
        for why, n in sorted(ignore_reasons.items(), key=lambda t: -t[1]):
            print(f"    {n:>7}  {why}")
        print("  （需要看具体内容的话，去 --report 的 JSON 里翻）")
        print()

    if identical:
        print(f"--- 与原文一致且需要判断 {len(identical)} 条（前 {args.sample}）---")
        print("这些才是真正要看的。逐条确认：是真的漏了，还是本来就该保持原样？")
        for f, p, v, note, why in identical[:args.sample]:
            print(f"  {f} {p}  [{why}]")
            print(f"    {v[:90]}")
            if note:
                print(f"    备注: {note}")
        if len(identical) > args.sample:
            print(f"  ... 其余 {len(identical) - args.sample} 条")
        print()

    if suspect:
        print(f"--- 已改动但仍含外语字符 {len(suspect)} 条（前 {args.sample}）---")
        print("这是**粗筛**。目标语言里合法的字符也会命中 ——")
        print("间隔号、长音符、有意保留的原名都会出现在这里。")
        for f, p, c, o, hits in suspect[:args.sample]:
            print(f"  {f} {p}  {hits}")
            print(f"    现: {c[:80]}")
            print(f"    原: {o[:80]}")
        if len(suspect) > args.sample:
            print(f"  ... 其余 {len(suspect) - args.sample} 条")
        print()

    if code_in_text:
        print(f"--- 文本槽里装着代码 {len(code_in_text)} 条（前 {args.sample}）---")
        print("⚠ 这类内容玩家看不见，但改错会改行为。")
        print("  典型：把立绘/皮肤的状态名改掉，画面就变了。")
        print("  除非你确定这些字符串是可以自由翻译的标签，否则应该改回去。")
        for f, p, c, o, kind in code_in_text[:args.sample]:
            print(f"  {f} {p}  [{kind}]")
            print(f"    现: {c[:80]}")
            print(f"    原: {o[:80]}")
        if len(code_in_text) > args.sample:
            print(f"  ... 其余 {len(code_in_text) - args.sample} 条")
        print()

    if new_paths:
        print(f"--- 备份中没有的新增条目 {len(new_paths)} 条（前 {args.sample}）---")
        print("通常是插件在运行时动态生成的文本，或游戏更新过。确认一下即可。")
        for f, p in new_paths[:args.sample]:
            print(f"  {f} {p}")
        if len(new_paths) > args.sample:
            print(f"  ... 其余 {len(new_paths) - args.sample} 条")
        print()

    print("结论怎么下：")
    print("  · 上面每个 ⚠ 清单都要**人工逐条过**。脚本给的是候选，不是判决。")
    print("  · 「残留 N 条」不等于「漏翻 N 条」。")
    print("  · 全部处理完后，进游戏实际点一遍：")
    print("      主菜单 → 一段剧情（看立绘/表情/变量）→ 一场战斗 → 存读档")

    if args.report:
        payload = {
            "engine": engine.key,
            "data_root": data_root,
            "backup": backup,
            "scope": "same extractor as extract_sheet.py (player-visible only)",
            "totals": {
                "total": total,
                "changed_ok": changed_ok,
                "keep_marked": keep_marked,
                "identical_ignored": len(identical_ignored),
                "identical": len(identical),
                "changed_suspect": len(suspect),
                "code_in_text_slot": len(code_in_text),
                "new_paths": len(new_paths),
            },
            "identical": [
                {"file": f, "path": p, "text": v, "note": n, "reason": w}
                for f, p, v, n, w in identical],
            "identical_ignored": [
                {"file": f, "path": p, "text": v, "reason": w}
                for f, p, v, w in identical_ignored],
            "changed_suspect": [
                {"file": f, "path": p, "current": c, "original": o, "hits": h}
                for f, p, c, o, h in suspect],
            "code_in_text_slot": [
                {"file": f, "path": p, "current": c, "original": o, "kind": k}
                for f, p, c, o, k in code_in_text],
            "new_paths": [{"file": f, "path": p} for f, p in new_paths],
        }
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n完整结果 → {args.report}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
