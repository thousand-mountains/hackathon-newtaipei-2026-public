"""期間引擎搬遷驗證。

三個層次：
1. 搬遷保真：backend/engine/deadline.py 與 prototype/engine/deadline.py 位元組相同。
2. 向量零分歧：backend 引擎跑 data/test-vectors.json 全部 15 條，逐條與期望值相符。
3. 行為測試：沿用 prototype/tests/test_deadline.py 的四個既有案例。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib

from backend.engine.deadline import compute
from backend.tests.harness import assert_eq, assert_true

REPO = pathlib.Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
VECTORS = json.loads((BACKEND / "data" / "test-vectors.json").read_text(encoding="utf-8"))["vectors"]


def _d(s):
    return dt.date.fromisoformat(s) if s else None


def _sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_engine_migration_is_byte_identical():
    """CONSTITUTION §4 / architecture §10：deadline.py 原封搬遷，一行不改。"""
    src = REPO / "prototype" / "engine" / "deadline.py"
    dst = BACKEND / "engine" / "deadline.py"
    assert_eq(_sha(dst), _sha(src), "backend/engine/deadline.py 與 prototype 版本不同（搬遷必須原封）")


def test_vectors_zero_divergence():
    """15 條測試向量逐條零分歧（驗收條件 SC-01）。"""
    fails = []
    for v in VECTORS:
        r = compute(
            service_method=v["in"]["method"],
            service_date=_d(v["in"]["service"]),
            filing_date=_d(v["in"].get("filing")),
            transit_days=v["in"].get("transit", 0),
        )
        got = {"deadline": r.deadline.isoformat() if r.deadline else None, "overdue": r.overdue}
        if got != v["out"]:
            fails.append(f'{v["id"]}: got {got}, want {v["out"]}  ({v["note"]})')
    assert_true(not fails, "測試向量分歧：\n" + "\n".join(fails))


def test_vector_count_is_15():
    """向量數量鎖定：新增向量要顯式改這個數字，避免測試檔被悄悄縮水。"""
    assert_eq(len(VECTORS), 15, "測試向量數量變動")


def test_public_service_refuses():
    r = compute("public", dt.date(2024, 6, 13))
    assert_true(r.deadline is None and r.caveats, "公示送達必須拒答並附 caveat")


def test_overdue_carries_article80_caveat():
    r = compute("personal", dt.date(2023, 11, 12), dt.date(2025, 10, 20))
    assert_eq(r.overdue, True)
    assert_true(any("80" in c for c in r.caveats), "逾期案必須帶訴願法 80 caveat")


def test_steps_have_basis():
    r = compute("deposit", dt.date(2024, 6, 13), dt.date(2024, 7, 20))
    assert_true(all(s.basis for s in r.steps) and len(r.steps) >= 5, "每個步驟都要有法源依據")


def test_unknown_method_raises():
    try:
        compute("unknown", dt.date(2024, 6, 13))
    except ValueError:
        return
    raise AssertionError("未知送達方式應該 raise ValueError")
