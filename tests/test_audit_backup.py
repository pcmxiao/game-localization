# -*- coding: utf-8 -*-
"""audit.py 的备份定位与「假全清」守卫。

这里每一条都对应一个真实踩过的坑，不是凑覆盖率。

最重要的一条：传 `gameloc_backups/` 父目录时，旧实现只往下探一层而且要求
候选目录**直接**含 .json，于是探测不触发 → 备份抽出 0 条 → 所有文本被归到
「新增条目」→ 报告显示「已改动 0」→ 退出码 0。
**一份看起来通过的、完全无意义的报告。** 比报错危险得多。
"""

from __future__ import annotations

import os
import shutil

import pytest

from conftest import (make_rmmz_game, read_json, run_script, write_json,
                      write_sheet)

from gameloc_core import find_backup_root


def _build_backup_tree(tmp_path, game_root, tag="rpgmaker-mvmz_20260101_000000"):
    """复刻 backup_dir() 的真实产物布局：<dest>/<tag>/data/…"""
    dest = tmp_path / "gameloc_backups"
    inner = dest / tag / "data"
    os.makedirs(inner, exist_ok=True)
    for name in ("System.json", "Map001.json", "MapInfos.json"):
        shutil.copy2(os.path.join(game_root, "data", name),
                     os.path.join(inner, name))
    return str(dest), str(dest / tag), str(inner)


# --------------------------------------------------------------------------
# find_backup_root：三种传法都要能找到数据目录
# --------------------------------------------------------------------------

def test_find_backup_root_accepts_data_dir_directly(tmp_path, rmmz_game):
    _, _ts, data = _build_backup_tree(tmp_path, rmmz_game)
    assert os.path.normcase(find_backup_root(data)) == os.path.normcase(data)


def test_find_backup_root_descends_from_timestamped_dir(tmp_path, rmmz_game):
    _, ts, data = _build_backup_tree(tmp_path, rmmz_game)
    assert os.path.normcase(find_backup_root(ts)) == os.path.normcase(data)


def test_find_backup_root_descends_from_parent_dir(tmp_path, rmmz_game):
    """旧实现就是在这里静默失败的。"""
    parent, _ts, data = _build_backup_tree(tmp_path, rmmz_game)
    assert os.path.normcase(find_backup_root(parent)) == os.path.normcase(data)


def test_find_backup_root_picks_newest_of_several_backups(tmp_path, rmmz_game):
    parent, _ts, _data = _build_backup_tree(tmp_path, rmmz_game)
    newer = os.path.join(parent, "rpgmaker-mvmz_20260909_120000", "data")
    os.makedirs(newer, exist_ok=True)
    for name in ("System.json", "Map001.json", "MapInfos.json"):
        shutil.copy2(os.path.join(rmmz_game, "data", name),
                     os.path.join(newer, name))
    got = find_backup_root(parent)
    assert "20260909" in got, f"应取最新一份，实际 {got}"


def test_find_backup_root_returns_input_when_nothing_found(tmp_path):
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert os.path.normcase(find_backup_root(str(empty))) == \
        os.path.normcase(str(empty))


def test_find_backup_root_handles_missing_path(tmp_path):
    missing = str(tmp_path / "does_not_exist")
    assert find_backup_root(missing) == os.path.abspath(missing)


# --------------------------------------------------------------------------
# audit CLI：父目录传法必须给出**真实**报告，不能再是假全清
# --------------------------------------------------------------------------

def test_audit_via_parent_dir_reports_real_changes(tmp_path, rmmz_game):
    """这是那个静默 bug 的端到端回归。"""
    parent, _ts, _data = _build_backup_tree(tmp_path, rmmz_game)

    # 改掉一句台词
    data = read_json(os.path.join(rmmz_game, "data", "Map001.json"))
    data["events"][1]["pages"][0]["list"][0]["parameters"][0] = "你好\\N[1]"
    write_json(os.path.join(rmmz_game, "data", "Map001.json"), data)

    r = run_script("audit.py", "--root", rmmz_game, "--backup", parent)
    assert r.returncode == 0, r.stderr
    assert "备份可见条目: 0" not in r.stdout, "备份没被正确定位"
    # Map001 的那句应被算作「已改动」，而不是「新增条目」
    assert "新增条目                 0" in r.stdout or \
        "备份中没有的新增条目                 0" in r.stdout, r.stdout


def test_audit_all_paths_produce_same_totals(tmp_path, rmmz_game):
    """三种传法（data / 时间戳 / 父目录）必须给出同样的数字。"""
    parent, ts, data = _build_backup_tree(tmp_path, rmmz_game)

    outs = []
    for arg in (data, ts, parent):
        r = run_script("audit.py", "--root", rmmz_game, "--backup", arg)
        assert r.returncode == 0, r.stderr
        # 抓「条目总数」那一行
        line = [l for l in r.stdout.splitlines() if "条目总数" in l]
        assert line, r.stdout
        outs.append(line[0].strip())
    assert len(set(outs)) == 1, f"三种传法结果不一致: {outs}"


def test_audit_errors_on_empty_backup(tmp_path, rmmz_game):
    """备份抽出 0 条时必须硬报错，不能给一份假报告。"""
    empty = tmp_path / "empty_backup"
    (empty / "data").mkdir(parents=True)

    r = run_script("audit.py", "--root", rmmz_game, "--backup", str(empty))
    assert r.returncode == 7, f"应返回 7，实际 {r.returncode}\n{r.stdout}"
    assert "假报告" in r.stderr or "一条可见文本都没抽到" in r.stderr


def test_audit_errors_on_missing_backup_dir(tmp_path, rmmz_game):
    r = run_script("audit.py", "--root", rmmz_game,
                   "--backup", str(tmp_path / "nope"))
    assert r.returncode == 4


def test_audit_detects_untranslated_string(tmp_path, rmmz_game):
    """没改动的条目应出现在「与原文一致且需要判断」里。"""
    _parent, _ts, data = _build_backup_tree(tmp_path, rmmz_game)

    r = run_script("audit.py", "--root", rmmz_game, "--backup", data)
    assert r.returncode == 0, r.stderr
    # 台词含假名 → 应判定为需要人工判断，而不是被自动排除
    assert "与原文一致且需要判断" in r.stdout
    assert "こんにちは" in r.stdout


def test_audit_keep_original_marker_excludes_entry(tmp_path, rmmz_game):
    """note 里标了保留 → 计入「有意保留」，不算漏翻。"""
    _parent, _ts, data = _build_backup_tree(tmp_path, rmmz_game)

    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [{
        "carrier": "dialogue", "file": "Map001.json",
        "path": "events[1].pages[0].list[0].parameters[0]",
        "src": "こんにちは\\N[1]", "text": "", "status": "keep_original",
        "note": "保留原名",
    }])

    r = run_script("audit.py", "--root", rmmz_game, "--backup", data,
                   "--sheet", str(sheet))
    assert r.returncode == 0, r.stderr
    assert "已标记为有意保留" in r.stdout
    assert "已标记为有意保留                     1" in r.stdout


def test_audit_writes_json_report(tmp_path, rmmz_game):
    import json
    _parent, _ts, data = _build_backup_tree(tmp_path, rmmz_game)
    report = tmp_path / "audit.json"

    r = run_script("audit.py", "--root", rmmz_game, "--backup", data,
                   "--report", str(report))
    assert r.returncode == 0, r.stderr
    assert report.exists()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["engine"] == "rpgmaker-mvmz"
    assert "totals" in payload and payload["totals"]["total"] >= 1
