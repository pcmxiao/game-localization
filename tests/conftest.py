# -*- coding: utf-8 -*-
"""共享夹具：造最小的 RPG Maker MZ 游戏目录，以及跑 CLI 的辅助函数。

刻意不依赖真实游戏 —— 这个仓库不含任何游戏文本。
所有样本都是现场构造的几十字节 JSON。
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO_ROOT, "scripts")

if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

SHEET_FIELDS = ["id", "carrier", "file", "path", "src", "text", "status", "note"]


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def read_json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def make_rmmz_game(root, map_text="こんにちは\\N[1]", currency="G"):
    """造一个能被 detect_engine 认成 RPG Maker MV/MZ 的最小目录。"""
    root = str(root)
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    os.makedirs(os.path.join(root, "js"), exist_ok=True)
    with io.open(os.path.join(root, "js", "rmmz_managers.js"), "w",
                 encoding="utf-8") as f:
        f.write("// minimal marker for engine detection\n")

    write_json(os.path.join(root, "data", "System.json"), {
        "currencyUnit": currency,
        "terms": {"messages": {"autosave": "オートセーブ"}},
    })
    write_json(os.path.join(root, "data", "Map001.json"), {
        "events": [None, {"pages": [{"list": [
            {"code": 101, "parameters": [map_text]},
        ]}]}],
    })
    write_json(os.path.join(root, "data", "MapInfos.json"), [])
    return root


def write_sheet(path, rows):
    """rows: dict 列表，缺的列自动补空串。id 默认按行号填。

    注意：不传 id 时，这里会填 0,1,2…，与 extract_sheet 的输出一致。
    想测「id 列被冲掉」的场景，显式传 id=""。
    """
    with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_FIELDS, extrasaction="ignore")
        w.writeheader()
        for i, r in enumerate(rows):
            row = {k: "" for k in SHEET_FIELDS}
            row.update(r)
            if not str(row.get("id", "")).strip():
                row["id"] = i
            w.writerow(row)
    return path


def read_sheet(path):
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def run_script(name, *args, **kw):
    """跑 scripts/<name>，返回 CompletedProcess（UTF-8 解码）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, name), *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, **kw)


@pytest.fixture
def rmmz_game(tmp_path):
    """一个已就绪的 RPG Maker MZ 游戏目录。"""
    return make_rmmz_game(tmp_path / "game")


@pytest.fixture
def generic_game(tmp_path):
    """一个 generic-plaintext 兜底可抽取的目录。"""
    root = tmp_path / "generic"
    os.makedirs(root, exist_ok=True)
    write_json(os.path.join(root, "strings.json"), {
        "menu.title": "菜单",
        "menu": {"body": "正文"},
        "name": "武器名",
        "id": "w001",
        "note": "编辑器备注",
    })
    return str(root)
