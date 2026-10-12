# -*- coding: utf-8 -*-
"""apply_sheet.py 的原子性与退出码语义。

核心断言：**预检阶段只要有一条不通过，磁盘上就一个字节都不能变。**
旧实现是边检边写，前面几行已经落盘后才发现后面出错，留下半张补丁 ——
模块自己的 docstring 却写着「绝不写部分补丁」。这里把那个承诺钉死。

退出码约定（写进 apply_sheet.py 的 docstring）：
    5  回读不一致（文件可能被改坏，最严重）
    6  预检失败，整批未写入
    7  回读/写入阶段失败
"""

from __future__ import annotations

import os

import pytest

from conftest import read_json, run_script, write_sheet


def _snapshot(game_root):
    """把 data/ 下所有文件的内容抓一份，用于比对是否被动过。"""
    data_dir = os.path.join(game_root, "data")
    out = {}
    for name in sorted(os.listdir(data_dir)):
        with open(os.path.join(data_dir, name), "rb") as f:
            out[name] = f.read()
    return out


DIALOGUE_PATH = "events[1].pages[0].list[0].parameters[0]"


# --------------------------------------------------------------------------
# 原子性
# --------------------------------------------------------------------------

def test_bad_row_leaves_disk_untouched(tmp_path, rmmz_game):
    """一条 src 对不上 → 整批中止，同批次的好行也不许写。"""
    before = _snapshot(rmmz_game)
    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [
        # 好行：本来会被写进去
        {"carrier": "dialogue", "file": "Map001.json", "path": DIALOGUE_PATH,
         "src": "こんにちは\\N[1]", "text": "你好\\N[1]", "status": "done"},
        # 坏行：src 和游戏里的现值对不上
        {"carrier": "sys_term", "file": "System.json", "path": "currencyUnit",
         "src": "WRONG-SRC", "text": "金币", "status": "done"},
    ])

    r = run_script("apply_sheet.py", "--root", rmmz_game,
                   "--sheet", str(sheet))
    assert r.returncode == 6, f"应返回 6，实际 {r.returncode}\n{r.stdout}"
    assert _snapshot(rmmz_game) == before, "预检失败却改动了磁盘"
    assert "一个字节都没改" in r.stdout


def test_bad_row_in_later_file_still_atomic(tmp_path, rmmz_game):
    """System.json 排在 Map001.json 之后 —— 确保不是「按文件顺序侥幸」。"""
    before = _snapshot(rmmz_game)
    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [
        {"carrier": "sys_term", "file": "System.json", "path": "currencyUnit",
         "src": "G", "text": "金币", "status": "done"},
        {"carrier": "dialogue", "file": "Map001.json", "path": DIALOGUE_PATH,
         "src": "对不上的原文", "text": "你好", "status": "done"},
    ])

    r = run_script("apply_sheet.py", "--root", rmmz_game,
                   "--sheet", str(sheet))
    assert r.returncode == 6
    assert _snapshot(rmmz_game) == before, "预检失败却改动了磁盘"


def test_missing_path_aborts_whole_batch(tmp_path, rmmz_game):
    before = _snapshot(rmmz_game)
    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [
        {"carrier": "dialogue", "file": "Map001.json", "path": DIALOGUE_PATH,
         "src": "こんにちは\\N[1]", "text": "你好\\N[1]", "status": "done"},
        {"carrier": "sys_term", "file": "System.json",
         "path": "terms.does.not.exist", "src": "x", "text": "y",
         "status": "done"},
    ])

    r = run_script("apply_sheet.py", "--root", rmmz_game,
                   "--sheet", str(sheet))
    assert r.returncode == 6
    assert _snapshot(rmmz_game) == before


def test_dry_run_never_writes(tmp_path, rmmz_game):
    before = _snapshot(rmmz_game)
    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [
        {"carrier": "dialogue", "file": "Map001.json", "path": DIALOGUE_PATH,
         "src": "こんにちは\\N[1]", "text": "你好\\N[1]", "status": "done"},
    ])

    r = run_script("apply_sheet.py", "--root", rmmz_game,
                   "--sheet", str(sheet), "--dry")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _snapshot(rmmz_game) == before


# --------------------------------------------------------------------------
# 正常路径
# --------------------------------------------------------------------------

def test_good_sheet_writes_and_backs_up(tmp_path, rmmz_game):
    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [
        {"carrier": "dialogue", "file": "Map001.json", "path": DIALOGUE_PATH,
         "src": "こんにちは\\N[1]", "text": "你好\\N[1]", "status": "done"},
        {"carrier": "sys_term", "file": "System.json", "path": "currencyUnit",
         "src": "G", "text": "金币", "status": "done"},
    ])

    r = run_script("apply_sheet.py", "--root", rmmz_game,
                   "--sheet", str(sheet))
    assert r.returncode == 0, r.stdout + r.stderr

    data = read_json(os.path.join(rmmz_game, "data", "Map001.json"))
    assert data["events"][1]["pages"][0]["list"][0]["parameters"][0] == "你好\\N[1]"
    sysj = read_json(os.path.join(rmmz_game, "data", "System.json"))
    assert sysj["currencyUnit"] == "金币"

    # 备份必须在游戏目录之外
    backup_root = os.path.join(os.path.dirname(rmmz_game), "gameloc_backups")
    assert os.path.isdir(backup_root), "没有生成备份"
    assert not os.path.isdir(os.path.join(rmmz_game, "gameloc_backups")), \
        "备份不该放在游戏目录里"


def test_backup_is_usable_by_audit(tmp_path, rmmz_game):
    """写完立刻审计：应报「已改动」，且备份能被自动定位。"""
    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [
        {"carrier": "dialogue", "file": "Map001.json", "path": DIALOGUE_PATH,
         "src": "こんにちは\\N[1]", "text": "你好\\N[1]", "status": "done"},
    ])
    r = run_script("apply_sheet.py", "--root", rmmz_game, "--sheet", str(sheet))
    assert r.returncode == 0, r.stdout

    # 从备份提示行里取目录
    line = [l for l in r.stdout.splitlines() if "已备份" in l][0]
    backup = line.split("→", 1)[1].strip()

    a = run_script("audit.py", "--root", rmmz_game, "--backup", backup)
    assert a.returncode == 0, a.stdout + a.stderr
    assert "备份可见条目: 0" not in a.stdout
    assert "✓ 已改动                               1" in a.stdout, a.stdout


def test_control_code_violation_is_excluded_not_written(tmp_path, rmmz_game):
    """控制码不匹配的行被排除；剩下的行照常写。"""
    before_text = read_json(
        os.path.join(rmmz_game, "data", "Map001.json")
    )["events"][1]["pages"][0]["list"][0]["parameters"][0]

    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [
        # \N[1] 被丢掉了 → 该校验不通过
        {"carrier": "dialogue", "file": "Map001.json", "path": DIALOGUE_PATH,
         "src": "こんにちは\\N[1]", "text": "你好", "status": "done"},
        {"carrier": "sys_term", "file": "System.json", "path": "currencyUnit",
         "src": "G", "text": "金币", "status": "done"},
    ])

    r = run_script("apply_sheet.py", "--root", rmmz_game,
                   "--sheet", str(sheet))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "控制码不匹配 1 条" in r.stdout

    # 被排除的那条必须原样保留
    after_text = read_json(
        os.path.join(rmmz_game, "data", "Map001.json")
    )["events"][1]["pages"][0]["list"][0]["parameters"][0]
    assert after_text == before_text
    # 另一条正常写入
    assert read_json(os.path.join(
        rmmz_game, "data", "System.json"))["currencyUnit"] == "金币"


def test_skipped_and_keep_original_statuses_are_not_written(tmp_path, rmmz_game):
    before = _snapshot(rmmz_game)
    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [
        {"carrier": "dialogue", "file": "Map001.json", "path": DIALOGUE_PATH,
         "src": "こんにちは\\N[1]", "text": "你好", "status": "skipped"},
        {"carrier": "sys_term", "file": "System.json", "path": "currencyUnit",
         "src": "G", "text": "金币", "status": "keep_original"},
    ])

    r = run_script("apply_sheet.py", "--root", rmmz_game,
                   "--sheet", str(sheet))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _snapshot(rmmz_game) == before
    assert "没有可写入的行" in r.stdout


def test_empty_sheet_rows_are_ignored(tmp_path, rmmz_game):
    before = _snapshot(rmmz_game)
    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [
        {"carrier": "dialogue", "file": "Map001.json", "path": DIALOGUE_PATH,
         "src": "こんにちは\\N[1]", "text": "", "status": "pending"},
    ])

    r = run_script("apply_sheet.py", "--root", rmmz_game,
                   "--sheet", str(sheet))
    assert r.returncode == 0
    assert _snapshot(rmmz_game) == before


def test_missing_referenced_file_aborts(tmp_path, rmmz_game):
    before = _snapshot(rmmz_game)
    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [
        {"carrier": "dialogue", "file": "NoSuchFile.json", "path": "[0].name",
         "src": "x", "text": "y", "status": "done"},
    ])

    r = run_script("apply_sheet.py", "--root", rmmz_game,
                   "--sheet", str(sheet))
    assert r.returncode == 4
    assert _snapshot(rmmz_game) == before


def test_only_verify_never_writes(tmp_path, rmmz_game):
    before = _snapshot(rmmz_game)
    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [
        {"carrier": "dialogue", "file": "Map001.json", "path": DIALOGUE_PATH,
         "src": "こんにちは\\N[1]", "text": "你好\\N[1]", "status": "done"},
    ])

    r = run_script("apply_sheet.py", "--root", rmmz_game,
                   "--sheet", str(sheet), "--only-verify")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _snapshot(rmmz_game) == before


def test_idempotent_second_run(tmp_path, rmmz_game):
    """第二次跑同样的表：内容已是译文，应报「无改动」而不是报错或重写。

    这里曾经有两个坑：
      · 预检把「现值 == 译文」误判成 src 冲突 → 整批中止（幂等被错杀）
      · 写入循环又调一次 set_path(expect=src) → 抛未捕获异常，退出码 1
    """
    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [
        {"carrier": "dialogue", "file": "Map001.json", "path": DIALOGUE_PATH,
         "src": "こんにちは\\N[1]", "text": "你好\\N[1]", "status": "done"},
    ])

    r1 = run_script("apply_sheet.py", "--root", rmmz_game, "--sheet", str(sheet))
    assert r1.returncode == 0, r1.stdout
    assert "改动 1" in r1.stdout

    before = _snapshot(rmmz_game)
    r2 = run_script("apply_sheet.py", "--root", rmmz_game, "--sheet", str(sheet))
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "没有需要改动的行" in r2.stdout, r2.stdout
    assert _snapshot(rmmz_game) == before, "空操作的重跑却改动了文件"


# --------------------------------------------------------------------------
# id 列为空时的行排除（真实 bug 回归）
# --------------------------------------------------------------------------

def test_blank_id_does_not_exclude_other_rows(tmp_path, rmmz_game):
    """一行控制码写坏，不能连累 id 为空的好行一起被丢掉。

    旧实现用 `bad_ids = {r["id"] ...}` 再按 id 过滤。id 列一旦被清空
    （从 Excel 粘贴时很容易发生），bad_ids 就成了 {""}，于是**所有**空 id
    的行都被排除 —— 一行坏、整表静默不写，还返回 0。
    """
    import io
    sheet = tmp_path / "sheet.csv"
    with io.open(sheet, "w", encoding="utf-8-sig", newline="") as f:
        f.write("id,carrier,file,path,src,text,status,note\n")
        # 好行，id 为空
        f.write(",sys_term,System.json,currencyUnit,G,金币,done,\n")
        # 坏行：\N[1] 被丢掉了
        f.write(",dialogue,Map001.json," + DIALOGUE_PATH +
                ",こんにちは\\N[1],你好,done,\n")

    r = run_script("apply_sheet.py", "--root", rmmz_game, "--sheet", str(sheet))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "控制码不匹配 1 条" in r.stdout

    # 好行必须被写进去
    sysj = read_json(os.path.join(rmmz_game, "data", "System.json"))
    assert sysj["currencyUnit"] == "金币", \
        "空 id 的好行被坏行连累，静默没写"


def test_blank_id_report_shows_placeholder(tmp_path, rmmz_game):
    """id 为空时报告里显示 `?`，而不是一片空白让人无从下手。"""
    import io
    sheet = tmp_path / "sheet.csv"
    with io.open(sheet, "w", encoding="utf-8-sig", newline="") as f:
        f.write("id,carrier,file,path,src,text,status,note\n")
        f.write(",dialogue,Map001.json," + DIALOGUE_PATH +
                ",こんにちは\\N[1],你好,done,\n")

    r = run_script("apply_sheet.py", "--root", rmmz_game, "--sheet", str(sheet))
    assert "id=?  " in r.stdout or "id=?" in r.stdout, r.stdout
