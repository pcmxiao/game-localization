# -*- coding: utf-8 -*-
"""
核心自检。跑法: python scripts/selftest.py

这些不是「锦上添花的测试」—— 每一条都对应一个真的踩过的坑。
特别是 test_extract_returns_nonzero：忘传 events 导致 139 张地图
全部报告「零残留」的假象，只有这种非空断言才抓得住。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gameloc_core import (  # noqa: E402
    SheetError, get_path, parse_path, resolve_engine, script_score,
    set_path, verify_escape,
)

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name}  {detail}")
        FAILS.append(name)


# ---------------------------------------------------------------- 路径解析
print("\n[路径解析]")


def t_parse():
    check("带方括号",
          parse_path("events[6].pages[1].list[7].parameters[0]") ==
          [("key", "events"), ("idx", 6), ("key", "pages"), ("idx", 1),
           ("key", "list"), ("idx", 7), ("key", "parameters"), ("idx", 0)])
    check("纯下标", parse_path("[3].name") == [("idx", 3), ("key", "name")])
    check("纯键名", parse_path("terms.messages.autosave") ==
          [("key", "terms"), ("key", "messages"), ("key", "autosave")])
    check("方括号内下标",
          parse_path("p0[0][2]") == [("key", "p0"), ("idx", 0), ("idx", 2)])

    # 曾经的 bug：re.findall(r'([^\[\]]+)|\[(\d+)\]', 'terms.messages.autosave')
    # 会把整串当成一个键，然后静默在根字典建键 —— JSON 依然合法。
    obj = {}
    raised = False
    try:
        set_path(obj, "a.b.c", "x")
    except SheetError:
        raised = True
    check("路径不存在会抛异常（而不是静默建键）", raised,
          f"obj 被改成了 {obj}")
    check("抛异常后对象未被改动", obj == {}, f"obj = {obj}")

    # 真正能写进去的情况
    obj2 = {"a": {"b": {"c": 1}}}
    set_path(obj2, "a.b.c", 2)
    check("正常路径写入", obj2 == {"a": {"b": {"c": 2}}})

    # expect 核对
    raised = False
    try:
        set_path({"a": 1}, "a", 2, expect=999)
    except SheetError:
        raised = True
    check("expect 不匹配会中止", raised)


t_parse()

# ---------------------------------------------------------------- 控制码
print("\n[控制码等值校验]")
eng = resolve_engine("rpgmaker-mvmz")

ok, why = verify_eng = verify_escape(r"\m[1]\z[2]\N[3]台词", r"\m[1]\z[2]\N[3]中文", eng.escape)
check("控制码一致 → 通过", ok, why)

ok, why = verify_escape(r"\m[1]\z[2]台词", "中文", eng.escape)
check("少了控制码 → 拦截", not ok, why)

ok, why = verify_escape(r"台词", r"\m[1]中文", eng.escape)
check("多了控制码 → 拦截", not ok, why)

ok, why = verify_escape(r"%1は倒れた！", "%1倒下了！", eng.escape)
check("数据库 message 占位符不受影响", ok, why)

# 方括号里的参数必须一并校验。
# 实测中 idx 182 的形态是 `\z[0010]` 而不是 `\z[10]` —— 两种都要原样保留。
ok, why = verify_escape(r"\z[0010] あ", r"\z[0010] 中文", eng.escape)
check("零填充形态 \\z[0010] 一致 → 通过", ok, why)
ok, why = verify_escape(r"\z[0010] あ", r"\z[10] 中文", eng.escape)
check("零填充形态被改成 \\z[10] → 拦截", not ok, why)
ok, why = verify_escape(r"\z[10] あ", r"\z[1] 中文", eng.escape)
check("下标数字改变 → 拦截", not ok, why)

# ---------------------------------------------------------------- 文本粗筛
print("\n[语言粗筛 —— 只用于分诊，不是判据]")
check("假名能检出", "kana" in script_score("これはテスト"))
# 全汉字的日语词 —— 这正是不能用字符集检测当判据的原因
check("全汉字日语检不出假名",
      "kana" not in script_score("勉強"),
      f"← 这正是不能用它当判据的原因，实际返回 {script_score('勉強')}")
check("假名时态会掩盖问题（した里有假名）",
      "kana" in script_score("勉強した"))
check("中文间隔号不会误报假名", "kana" not in script_score("李·爱铃"))
check("韩文能检出", "hangul" in script_score("안녕하세요"))
check("泰文能检出", "thai" in script_score("สวัสดี"))

# ---------------------------------------------------------------- 引擎档案
print("\n[引擎档案]")


def t_engines():
    from gameloc_core import ENGINES
    check("每个内置引擎都有 layer",
          all(p.layer in ("L0", "L1", "L2", "L3", "L4") for p in ENGINES.values()))
    check("有 escape 正则的引擎，L0/L1 的都有",
          all(p.escape is not None for k, p in ENGINES.items()
              if p.layer in ("L0", "L1") and k != "generic-plaintext"))
    # 控制码正则至少要能匹配一个已知的通用形态
    import re
    pat = ENGINES["rpgmaker-mvmz"].escape
    check("rpgmaker escape 能匹配 \\V[10]",
          bool(re.search(pat, r"变量 \V[10] 的值")))
    check("rpgmaker escape 能匹配 \\m[3]（表情差分）",
          bool(re.search(pat, r"\m[3]差分")))
    check("rpgmaker escape 能匹配 \\ac\\（插件命令块）",
          bool(re.search(pat, r"\ac\色\xxx")))
    check("rpgmaker escape 不误伤普通中文",
          not re.search(pat, "这是一句普通的台词"))


t_engines()

# ---------------------------------------------------------------- 抽取非空
print("\n[抽取器非空断言]")


def t_extract():
    """用最小构造的 RPG Maker 结构验证抽取器返回非空。

    这条对应「忘传 data['events'] 导致全地图零残留」那个坑 ——
    一个只测「不报错」的测试永远抓不到它。
    """
    import extract_sheet
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "data"), exist_ok=True)
        import io, json
        mapjson = {
            "events": [
                None,
                {"pages": [{"list": [
                    {"code": 101, "parameters": ["第一行台词\\N[1]"]},
                    {"code": 401, "parameters": ["续行"]},
                    {"code": 102, "parameters": [["选项A", "选项B"]]},
                    {"code": 108, "parameters": ["这行是注释，不该出现"]},
                ]}]},
            ]
        }
        with io.open(os.path.join(d, "data", "Map001.json"), "w",
                     encoding="utf-8") as f:
            json.dump(mapjson, f, ensure_ascii=False)

        rows = []
        extract_sheet.rgmmz_scan_map(rows, "Map001.json",
                                     json.load(io.open(
                                         os.path.join(d, "data", "Map001.json"),
                                         encoding="utf-8")))
        texts = [r["src"] for r in rows]
        check("事件文本被抽到", "第一行台词\\N[1]" in texts, str(texts))
        check("续行被抽到", "续行" in texts)
        check("选项被抽到", "选项A" in texts and "选项B" in texts)
        check("注释没被抽到", not any("注释" in t for t in texts))
        check("path 是可写回的精确路径",
              any(r["path"] == "events[1].pages[0].list[0].parameters[0]"
                  for r in rows),
              str([r["path"] for r in rows]))
        check("选项 path 带下标",
              any(r["path"].endswith("[0]") or r["path"].endswith("[1]")
                  for r in rows))

        # 抽出来的 path 必须真的能被 set_path 写回去
        data = json.load(io.open(os.path.join(d, "data", "Map001.json"),
                                 encoding="utf-8"))
        ok_all = True
        for r in rows:
            try:
                set_path(data, r["path"], "译文")
            except SheetError as e:
                ok_all = False
                print(f"      写回失败 {r['path']}: {e}")
        check("所有抽出的 path 都能被写回", ok_all)


t_extract()

# ---------------------------------------------------------------- 通用兜底
print("\n[通用兜底字段白名单]")


def t_generic():
    import extract_sheet
    sink = []

    class Sink(list):
        def append(self, item):
            sink.append(item)

    extract_sheet._generic_walk(
        {"name": "武器名", "id": "w001", "note": "编辑器备注",
         "nested": {"title": "标题", "guid": "abc-123"},
         "items": [{"description": "说明"}]},
        "", Sink(), "generic")
    got = {p: t for p, _c, t in sink}
    check("name 被收录", got.get("name") == "武器名", str(got))
    check("title 被收录", got.get("nested.title") == "标题")
    check("list 内的 description 被收录",
          got.get("items[0].description") == "说明")
    check("id 被排除（标识符）", "id" not in got)
    check("note 被排除（编辑器内部字段）", "note" not in got)
    check("guid 被排除", "nested.guid" not in got)


t_generic()

# ---------------------------------------------------------------- 结论
print()
print("=" * 60)
if FAILS:
    print(f"✗ {len(FAILS)} 项未通过:")
    for f in FAILS:
        print(f"    - {f}")
    sys.exit(1)
print("✓ 全部通过")
