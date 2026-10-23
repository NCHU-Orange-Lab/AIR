"""讀 JUnit XML（pytest --junit-xml、unittest 的 xmlrunner 都寫這種格式）：每個測試的結果。

這是 verify 唯一認得的結果格式：有它才分得出「本來就失敗的」與「新增的失敗」（既有失敗基準）。
測試的 id 是 classname::name（沒有 classname 就只有 name；pytest 的收集錯誤就是這樣）。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

PASSED, FAILED, SKIPPED = "passed", "failed", "skipped"


def parse(path: Path) -> dict[str, str]:
    """測試 id → passed｜failed｜skipped。failure 與 error 都算 failed。不是 XML、或裡面沒有 testcase 就 raise ValueError。"""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise ValueError(f"不是合法的 XML（{e}）") from None
    out: dict[str, str] = {}
    for case in root.iter("testcase"):
        name = case.get("name", "").strip()
        classname = case.get("classname", "").strip()
        test_id = f"{classname}::{name}" if classname else name
        if not test_id:
            continue
        tags = {child.tag for child in case}
        if tags & {"failure", "error"}:
            out[test_id] = FAILED
        elif "skipped" in tags:
            out[test_id] = SKIPPED
        else:
            out[test_id] = PASSED
    if not out:
        raise ValueError("裡面沒有任何 testcase")
    return out
