# -*- coding: utf-8 -*-
"""路径解析、转义，以及「抽出来的 path 一定能写回去」。

为什么值得单独一组测试：路径是整条流水线的接缝。抽取器拼错一个字符，
写回就会落到错误的键上 —— 而 JSON 依然合法、不报错，只有 diff 才发现。
这个仓库存在的理由之一就是消掉这类静默错误。
"""

from __future__ import annotations

import os

import pytest

from conftest import read_json, run_script, write_sheet

from gameloc_core import (SheetError, escape_path_token, get_path, parse_path,
                          set_path)


# --------------------------------------------------------------------------
# 基本解析
# --------------------------------------------------------------------------

def test_parse_bracket_path():
    assert parse_path("events[6].pages[1].list[7].parameters[0]") == [
        ("key", "events"), ("idx", 6), ("key", "pages"), ("idx", 1),
        ("key", "list"), ("idx", 7), ("key", "parameters"), ("idx", 0)]


def test_parse_leading_index():
    assert parse_path("[3].name") == [("idx", 3), ("key", "name")]


def test_parse_plain_keys():
    assert parse_path("terms.messages.autosave") == [
        ("key", "terms"), ("key", "messages"), ("key", "autosave")]


def test_parse_consecutive_indices():
    assert parse_path("p0[0][2]") == [("key", "p0"), ("idx", 0), ("idx", 2)]


def test_parse_empty_raises():
    with pytest.raises(SheetError):
        parse_path("")


def test_parse_malformed_raises():
    with pytest.raises(SheetError):
        parse_path("a[]")


# --------------------------------------------------------------------------
# 转义：键名里含 '.' / '[' / ']'
# --------------------------------------------------------------------------

def test_escaped_dot_is_one_key():
    assert parse_path(r"ui\.title") == [("key", "ui.title")]


def test_escaped_dot_mixed_with_separator():
    assert parse_path(r"a\.b.c") == [("key", "a.b"), ("key", "c")]


def test_escaped_brackets_are_literal():
    assert parse_path(r"x\[0\]") == [("key", "x[0]")]


@pytest.mark.parametrize("name", [
    "ui.title", "a.b.c", "weird[key]", "brack[0]", "normal", "dots.and[more]",
])
def test_escape_round_trip(name):
    """escape_path_token 的输出必须能被 parse_path 还原成同一个键。"""
    assert parse_path(escape_path_token(name)) == [("key", name)]


def test_ambiguous_dotted_key_is_now_unambiguous():
    """旧实现下这个对象无法安全写入：两种解释会撞在一起。

    现在扁平键走 'a\\.name'，嵌套键走 'a.name'，互不干扰。
    """
    obj = {"a.name": "flat", "a": {"name": "nested"}}
    assert get_path(obj, r"a\.name") == "flat"
    assert get_path(obj, "a.name") == "nested"

    set_path(obj, r"a\.name", "FLAT-NEW", expect="flat")
    assert obj["a.name"] == "FLAT-NEW"
    assert obj["a"]["name"] == "nested", "写扁平键时误改了嵌套键"

    set_path(obj, "a.name", "NESTED-NEW", expect="nested")
    assert obj["a"]["name"] == "NESTED-NEW"
    assert obj["a.name"] == "FLAT-NEW", "写嵌套键时误改了扁平键"


# --------------------------------------------------------------------------
# fail-closed 语义
# --------------------------------------------------------------------------

def test_missing_key_raises_and_does_not_create():
    obj = {}
    with pytest.raises(SheetError):
        set_path(obj, "a.b.c", "x")
    assert obj == {}, "路径不存在时不该静默建键"


def test_index_out_of_range_raises():
    with pytest.raises(SheetError):
        set_path({"a": [1]}, "a[5]", "x")


def test_expect_mismatch_raises():
    with pytest.raises(SheetError):
        set_path({"a": 1}, "a", 2, expect=999)


def test_set_path_reports_no_change_when_identical():
    obj = {"a": "same"}
    assert set_path(obj, "a", "same") is False
    assert set_path(obj, "a", "different") is True


# --------------------------------------------------------------------------
# 抽取 → 写回 的端到端接缝
# --------------------------------------------------------------------------

def test_every_extracted_path_is_writable(tmp_path, generic_game):
    """generic 兜底抽出的每条 path 都必须能被 set_path 写回去。"""
    sheet = tmp_path / "sheet.csv"
    r = run_script("extract_sheet.py", "--root", generic_game,
                   "--engine", "generic-plaintext", "--out", str(sheet))
    assert r.returncode == 0, r.stderr

    import csv
    import io
    with io.open(sheet, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows, "没有抽到任何条目"

    data = read_json(os.path.join(generic_game, "strings.json"))
    for row in rows:
        # 不抛异常即算通过
        get_path(data, row["path"])
        set_path(data, row["path"], "译文")


def test_generic_roundtrip_preserves_flat_and_nested_keys(tmp_path, generic_game):
    """含点号的扁平键与嵌套键都要各归各位。"""
    sheet = tmp_path / "sheet.csv"
    run_script("extract_sheet.py", "--root", generic_game,
               "--engine", "generic-plaintext", "--out", str(sheet))

    import csv
    import io
    with io.open(sheet, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    paths = {r["path"]: r["src"] for r in rows}
    # 'menu.title' 是扁平键（带命名空间），'menu.body' 是嵌套键
    assert r"menu\.title" in paths, f"扁平键没被转义: {list(paths)}"
    assert "menu.body" in paths, f"嵌套键缺失: {list(paths)}"
    assert paths[r"menu\.title"] == "菜单"
    assert paths["menu.body"] == "正文"

    for row in rows:
        row["text"] = "T:" + row["src"]
        row["status"] = "done"
    write_sheet(sheet, rows)

    r = run_script("apply_sheet.py", "--root", generic_game,
                   "--sheet", str(sheet), "--engine", "generic-plaintext")
    assert r.returncode == 0, r.stdout + r.stderr

    out = read_json(os.path.join(generic_game, "strings.json"))
    assert out["menu.title"] == "T:菜单", "扁平键写错了位置"
    assert out["menu"]["body"] == "T:正文", "嵌套键写错了位置"
    assert out["id"] == "w001", "标识符被改坏了"
    assert out["note"] == "编辑器备注", "跳过名单字段被改坏了"


def test_rpgmaker_roundtrip(tmp_path, rmmz_game):
    """RPG Maker 全流程：抽取 → 填 → dry → 写 → 审计。"""
    sheet = tmp_path / "sheet.csv"
    r = run_script("extract_sheet.py", "--root", rmmz_game, "--out", str(sheet))
    assert r.returncode == 0, r.stderr

    import csv
    import io
    with io.open(sheet, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows

    # 只填台词那一行，其余留空
    for row in rows:
        if row["carrier"] == "dialogue":
            row["text"] = "你好\\N[1]"
            row["status"] = "done"
    write_sheet(sheet, rows)

    d = run_script("apply_sheet.py", "--root", rmmz_game, "--sheet", str(sheet),
                   "--dry")
    assert d.returncode == 0, d.stdout + d.stderr

    w = run_script("apply_sheet.py", "--root", rmmz_game, "--sheet", str(sheet))
    assert w.returncode == 0, w.stdout + w.stderr

    out = read_json(os.path.join(rmmz_game, "data", "Map001.json"))
    assert out["events"][1]["pages"][0]["list"][0]["parameters"][0] == "你好\\N[1]"
