#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_sheet —— 把翻译表写回游戏。fail-closed。

用法:
    python apply_sheet.py --root <游戏目录> --sheet sheet.csv --dry
    python apply_sheet.py --root <游戏目录> --sheet sheet.csv

先跑 --dry。永远先跑 --dry。

它做四件事，顺序不能换:
    1. 备份（--dry 时跳过）
    2. 控制码等值校验 —— 不通过的跳过并列出，不硬写
    3. 按路径精确写入，写前核对「游戏里的现值 == 表里的 src」
    4. 写完回读校验 + 逐条 diff

任何一步失败都**中止整批**，绝不写部分补丁。半张补丁比没有补丁更难查。
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gameloc_core import (  # noqa: E402
    ENGINES, STATUS_DONE, STATUS_KEEP, STATUS_SKIPPED, SheetError,
    backup_dir, eprint, open_sheet, read_json, resolve_engine,
    set_path, verify_escape, write_json,
)


def load_rows(path, include_statuses):
    rows = open_sheet(path)
    out, skipped = [], 0
    for r in rows:
        status = (r.get("status") or "pending").strip().lower()
        if status == STATUS_SKIPPED:
            skipped += 1
            continue
        if status == STATUS_KEEP:
            continue
        if status and status != STATUS_DONE and status != "pending":
            continue
        text = (r.get("text") or "").strip()
        if not text:
            continue
        if include_statuses and status not in include_statuses:
            continue
        out.append(r)
    return out, skipped


def main():
    ap = argparse.ArgumentParser(description="把翻译表写回游戏（fail-closed）")
    ap.add_argument("--root", required=True, help="游戏目录")
    ap.add_argument("--sheet", required=True, help="翻译表 CSV")
    ap.add_argument("--engine", default="auto", help="引擎 key 或 auto")
    ap.add_argument("--data-root", default=None, help="数据目录（默认自动推断）")
    ap.add_argument("--dry", action="store_true", help="只检查不落盘")
    ap.add_argument("--backup-to", default=None,
                    help="备份目录的父目录（默认 <游戏目录>/../<name>_backups）")
    ap.add_argument("--only-verify", action="store_true",
                    help="只做控制码与路径校验，一个字节都不写")
    args = ap.parse_args()

    root = os.path.abspath(args.root)

    if args.engine == "auto":
        from detect_engine import detect_all
        hits = detect_all(root)
        if not hits:
            eprint("没认出引擎，无法确定控制码语法。"
                   "用 --engine 指定，或用 generic-plaintext（该引擎无控制码校验）。")
            return 2
        engine = hits[0][0]
    else:
        engine = resolve_engine(args.engine)

    if args.data_root:
        data_root = os.path.abspath(args.data_root)
    else:
        from detect_engine import find_data_roots
        roots = find_data_roots(root, engine)
        data_root = roots[0] if roots else root

    rows, n_skipped = load_rows(args.sheet, None)
    print(f"引擎      : {engine.title} ({engine.key}, {engine.layer})")
    print(f"数据目录  : {data_root}")
    print(f"待写入    : {len(rows)} 行（另有 {n_skipped} 行 status=skipped 已跳过）")
    print(f"模式      : {'dry-run，未落盘' if (args.dry or args.only_verify) else '实际写入'}")
    if engine.escape is None:
        print("⚠️  该引擎没有配置控制码语法，**跳过控制码校验**。")
        print("   如果你在文本里见过 \\ 或 {} 之类的东西，请手动核对。")
    elif not engine.escape_verified:
        print("⚠️  该引擎的控制码正则**未在真实游戏上验证过**。")
        print("   它可能过松（拦下合法译文）或过紧（放过被改坏的控制码）。")
        print("   首次跑完请人工抽查控制码是否原样保留，确认后用 ")
        print("   EngineProfile(..., escape_verified=True) 标记，并补一个测试用例。")
    print()

    # ---- 2. 控制码等值校验 ----
    bad_esc = []
    for r in rows:
        ok, why = verify_escape(r.get("src", ""), r.get("text", ""),
                                engine.escape)
        if not ok:
            bad_esc.append((r, why))
    if bad_esc:
        print(f"!! 控制码不匹配 {len(bad_esc)} 条（已从本次写入中排除）：")
        for r, why in bad_esc[:20]:
            print(f"  id={r['id']}  {r['file']} {r['path']}")
            print(f"    {why}")
            print(f"    原文: {r['src'][:70]}")
            print(f"    译文: {r['text'][:70]}")
        if len(bad_esc) > 20:
            print(f"  ... 其余 {len(bad_esc) - 20} 条")
        print()
    else:
        print(f"控制码校验: 全部通过 ✓" if engine.escape else "控制码校验: 跳过")

    bad_ids = {r["id"] for r, _ in bad_esc}
    good = [r for r in rows if r["id"] not in bad_ids]

    # ---- 分文件 ----
    by_file = defaultdict(list)
    for r in good:
        by_file[r["file"]].append(r)

    missing_files = [f for f in by_file
                     if not os.path.exists(os.path.join(data_root, f))]
    if missing_files:
        print(f"!! 翻译表引用了 {len(missing_files)} 个不存在的文件，中止：")
        for f in sorted(missing_files)[:20]:
            print(f"  {f}")
        print("  → 常见原因：data_root 猜错了，或表是另一个游戏抽取的。")
        return 4

    if not by_file:
        print("没有可写入的行。")
        return 0

    # ---- 1. 备份（dry 不做）----
    if not (args.dry or args.only_verify):
        if args.backup_to:
            dest_root = os.path.abspath(args.backup_to)
        else:
            dest_root = os.path.join(os.path.dirname(root), "gameloc_backups")
        os.makedirs(dest_root, exist_ok=True)
        bak = backup_dir([data_root], dest_root, tag=engine.key)
        print(f"已备份 → {bak}")
        print("  （在游戏目录之外，发布前记得按 references/release.md 处理）")
        print()

    # ---- 3 + 4. 写入与回读 ----
    written = 0
    unchanged = 0
    failures = []
    diffs = []

    for fname in sorted(by_file):
        items = by_file[fname]
        fpath = os.path.join(data_root, fname)
        try:
            data = read_json(fpath)
        except Exception as e:
            failures.append((fname, "-", f"读取失败: {e}"))
            continue

        file_written = 0
        for r in items:
            try:
                # expect=r["src"] 是这里最重要的一行：翻译表是照着某个版本的
                # 原文填的，游戏要是后来变过，宁可中止也不要覆盖掉。
                if set_path(data, r["path"], r["text"], expect=r.get("src") or None):
                    written += 1
                    file_written += 1
                else:
                    unchanged += 1
            except SheetError as e:
                failures.append((fname, r["path"], str(e)))

        if file_written == 0 and not failures:
            unchanged += 0

        if file_written and not (args.dry or args.only_verify):
            try:
                write_json(fpath, data)
            except Exception as e:
                failures.append((fname, "-", f"写回失败: {e}"))
                print(f"  !! {fname}: 写回失败 {e} —— 该文件可能已损坏，立即检查！")
                continue
            # 回读：不信返回值，重新解析
            try:
                after = read_json(fpath)
            except Exception as e:
                failures.append((fname, "-", f"回读失败，文件可能已损坏: {e}"))
                print(f"  !! {fname}: 回读失败 {e} —— 从备份恢复！")
                continue
            # 逐条 diff
            for r in items:
                try:
                    cur = _get(after, r["path"])
                    if cur != r["text"]:
                        diffs.append((fname, r["path"], r["text"][:40], cur[:40]
                                      if isinstance(cur, str) else cur))
                except SheetError:
                    pass

        print(f"  {fname:<26}{len(items):>7} 行{'' if (args.dry or args.only_verify) else f'  (改动 {file_written})'}")

    print()
    print(f"合计：改动 {written}，本就是译文 {unchanged}，失败 {len(failures)}")

    if diffs:
        print(f"\n!! 回读 diff 不一致 {len(diffs)} 处 —— 写进去的和预期不同：")
        for f, p, want, got in diffs[:20]:
            print(f"  {f} {p}\n    期望: {want}\n    实际: {got}")
        return 5

    if failures:
        print("\n!! 有失败项，处理可能不完整：")
        for f, p, msg in failures[:20]:
            print(f"  {f} {p}: {msg}")
        if len(failures) > 20:
            print(f"  ... 其余 {len(failures) - 20} 条")
        print("\n可能的成因：")
        print("  · 游戏在抽取之后被改过 → 重新抽取这一批")
        print("  · data_root 指错了目录")
        print("  · 表里的 path 写错了")
        print("\n已成功的写入没有回滚 —— 从备份恢复后再来。")
        return 6

    if args.dry or args.only_verify:
        print("\n✓ 校验通过，未写入任何文件。可以去掉 --dry 再跑一次。")
        print("  建议：进游戏实际点一遍（主菜单 / 一段剧情 / 打一场战斗 / 存读档）。")
    else:
        print("\n✓ 全部文件回读校验通过，diff 一致。进游戏实际点一遍确认。")
        print("  然后跑 audit.py 做最终验收，最后按 references/release.md 清理。")
    return 0


def _get(obj, path):
    from gameloc_core import get_path
    return get_path(obj, path)


if __name__ == "__main__":
    sys.exit(main())
