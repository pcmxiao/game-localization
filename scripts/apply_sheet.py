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

**批次是原子的**：第 3 步之前会先把整批预检一遍（路径存在性 + src 核对），
只要有一条不通过就整批中止、一个字节都不写。这样才配得上「绝不写部分补丁」
—— 旧实现是边检边写，前几行已经落盘后才发现后面出错，留下半张补丁。

退出码:
    0  成功（或 dry-run 通过）
    2  认不出引擎
    3  路径解析/表结构错误
    4  表里引用了不存在的文件
    5  回读不一致 —— 写进去的和预期不同（可能是**文件被改坏**，最严重）
    6  预检失败，整批未写入（游戏在抽取后被改过 / 表填错行）
    7  回读失败，文件可能已损坏
所以 5/7 是需要立刻从备份恢复的信号，6 只是「这批别写，先查表」。
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gameloc_core import (  # noqa: E402
    STATUS_DONE, STATUS_KEEP, STATUS_SKIPPED, SheetError,
    backup_dir, eprint, get_path, open_sheet, read_json, resolve_engine,
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
            rid = (r.get("id") or "").strip() or "?"
            print(f"  id={rid}  {r['file']} {r['path']}")
            print(f"    {why}")
            print(f"    原文: {r['src'][:70]}")
            print(f"    译文: {r['text'][:70]}")
            print(f"    → status 改成 skipped 可让这条不再报警；"
                  f"译文里补回控制码则会被正常写入。")
        if len(bad_esc) > 20:
            print(f"  ... 其余 {len(bad_esc) - 20} 条")
        print()
    else:
        print(f"控制码校验: 全部通过 ✓" if engine.escape else "控制码校验: 跳过")

    # 用对象身份排除，不要用 r["id"]。
    #
    # 旧实现是 bad_ids = {r["id"] for r, _ in bad_esc}，再按 id 过滤。
    # id 列一旦是空的（用户从 Excel 粘贴时把整列冲掉、或手工删掉了 id 列），
    # bad_ids 就成了 {""} —— 于是**所有** id 为空的行都被一起丢掉。
    # 一行控制码写坏，连累整张表的好行静默不写，还返回 0。
    # 身份判断没有这个洞。
    bad_objs = {id(r) for r, _ in bad_esc}
    good = [r for r in rows if id(r) not in bad_objs]

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

    # ---- 3a. 预检（不落盘）----
    #
    # 先把整批走一遍：路径是否存在、表里的 src 是否还等于游戏里的现值。
    # 这一步不改任何东西，只要有一条不通过就整批中止 —— 这才叫原子。
    # 旧实现是「边检边写」，前面几行已经落盘后才发现第 7 行对不上，
    # 留下半张补丁，比完全没写更难查。
    checked = {}          # fname -> 已读入并预检过的 data
    precheck_fail = []
    writable = []         # 通过预检、确实需要改动的行
    unchanged = 0

    for fname in sorted(by_file):
        fpath = os.path.join(data_root, fname)
        try:
            data = read_json(fpath)
        except Exception as e:
            precheck_fail.append((fname, "-", f"读取失败: {e}"))
            continue

        for r in by_file[fname]:
            try:
                cur = get_path(data, r["path"])
                text = r["text"]

                # 已经是译文了 → 这条是空操作，不是错误。
                # 必须放在 src 核对**之前**：重跑同一张表时，游戏里存的已经是
                # 译文，此时 src 天然对不上。若先核对 src，第二次跑就会误报
                # 「原文对不上」而整批中止 —— 把「幂等」错杀成「冲突」。
                if cur == text:
                    unchanged += 1
                    continue

                # 核对 src：翻译表是照着某个版本的原文填的，游戏要是后来变过，
                # 宁可整批中止也不要覆盖掉别人的改动。
                expect = r.get("src") or None
                if expect is not None and cur != expect:
                    raise SheetError(
                        f"原文对不上: {r['path']}\n"
                        f"  表里 src: {expect!r}\n"
                        f"  游戏里现值: {cur!r}\n"
                        f"  表里 text: {text!r}\n"
                        f"  → 游戏可能在抽取之后被改过；或这条已经被别的批次改成了"
                        f"第三种值。先重新抽取这一批，或把 src 更新成现值。"
                    )
                writable.append(r)
            except SheetError as e:
                precheck_fail.append((fname, r["path"], str(e)))
        checked[fname] = data

    if precheck_fail:
        print(f"!! 预检失败 {len(precheck_fail)} 条 —— 整批未写入（一个字节都没改）：")
        for f, p, msg in precheck_fail[:20]:
            print(f"  {f} {p}: {msg}")
        if len(precheck_fail) > 20:
            print(f"  ... 其余 {len(precheck_fail) - 20} 条")
        print("\n可能的成因：")
        print("  · 游戏在抽取之后被改过 → 重新抽取这一批")
        print("  · data_root 指错了目录")
        print("  · 表里的 path 写错了")
        print("\n这是好事：没有写进去任何东西，改完表重跑即可。")
        return 6

    if not writable:
        print(f"\n没有需要改动的行"
              f"{f'（{unchanged} 行已经是译文，属空操作）' if unchanged else ''}。")
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
        print(f"  （在游戏目录之外，发布前记得按 references/release.md 处理）")
        print(f"  （审计时 --backup 指向这里的时间戳目录：{os.path.basename(bak)}）")
        print()

    # ---- 3b + 4. 写入与回读 ----
    #
    # 只写 writable 里的行 —— 即在预检中确认「现值 == src 且现值 != 译文」的。
    # 预检已经判过一遍，这里必须沿用同一结论；再调一次 set_path(expect=src)
    # 会把幂等重跑误判成冲突。
    by_file_writable = defaultdict(list)
    for r in writable:
        by_file_writable[r["file"]].append(r)

    written = 0
    failures = []
    diffs = []

    for fname in sorted(by_file_writable):
        items = by_file_writable[fname]
        data = checked.get(fname)
        if data is None:
            continue

        file_written = 0
        for r in items:
            if set_path(data, r["path"], r["text"]):
                written += 1
                file_written += 1

        if not file_written:
            continue

        if not (args.dry or args.only_verify):
            fpath = os.path.join(data_root, fname)
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
                        diffs.append((fname, r["path"], r["text"][:40],
                                      cur[:40] if isinstance(cur, str) else cur))
                except SheetError:
                    pass

    # 每个文件都报一行，不管有没有改动
    for fname in sorted(by_file):
        n = len(by_file[fname])
        nw = len(by_file_writable.get(fname, []))
        print(f"  {fname:<26}{n:>7} 行"
              f"{'' if (args.dry or args.only_verify) else f'  (改动 {nw})'}")

    print()
    print(f"合计：改动 {written}，本就是译文 {unchanged}，失败 {len(failures)}")

    if diffs:
        print(f"\n!! 回读 diff 不一致 {len(diffs)} 处 —— 写进去的和预期不同：")
        for f, p, want, got in diffs[:20]:
            print(f"  {f} {p}\n    期望: {want}\n    实际: {got}")
        print("\n这是**文件内容层面的问题**，不是表填错了：")
        print("  · 立即从备份恢复这个文件（references/release.md 有说明）")
        print("  · 把 audit.json 的 report 附上再排查")
        return 5

    if failures:
        print("\n!! 有文件在写入/回读阶段失败，处理不完整：")
        for f, p, msg in failures[:20]:
            print(f"  {f} {p}: {msg}")
        print("\n从备份恢复这些文件后再来。")
        return 7

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
