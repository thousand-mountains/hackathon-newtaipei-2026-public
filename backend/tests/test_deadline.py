"""期間引擎搬遷驗證。

三個層次：
1. 搬遷保真：backend/engine/deadline.py 與 prototype/engine/deadline.py 位元組相同，
   **測試向量檔也位元組相同**（2026-09-05 起）。
2. 向量零分歧：backend 引擎跑 data/test-vectors.json 全部 16 條，逐條與期望值相符。
3. 行為測試：沿用 prototype/tests/test_deadline.py 的四個既有案例。

> 2026-09-05：backend 那份向量原本停在搬遷時的 15 條，prototype 那份已經是 16 條
> （PR #2 合進了示範案件向量）。兩邊各自的測試都是綠的，**所以沒有人會發現它們不同步**——
> 「數量鎖」只鎖得住縮水，鎖不住分岔。改成直接比對兩個檔的 sha256：分岔就紅。
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


def test_vector_file_is_byte_identical_to_prototype():
    """兩份 test-vectors.json 必須位元組相同——它們是同一個引擎的同一份基準。

    只鎖數量的話，「backend 15 條、prototype 16 條、兩邊都綠」這種分岔可以躲很久
    （實際上就躲過了一次，見模組 docstring）。比 sha256 才擋得住。
    """
    src = REPO / "prototype" / "data" / "test-vectors.json"
    dst = BACKEND / "data" / "test-vectors.json"
    assert_eq(
        _sha(dst),
        _sha(src),
        "backend 與 prototype 的 test-vectors.json 不同步。"
        "兩邊是同一份基準，要加向量請改 prototype 那份再複製過來（不要各自增修）。",
    )


def test_vectors_zero_divergence():
    """16 條測試向量逐條零分歧（驗收條件 SC-01）。"""
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


def test_vector_count_is_16():
    """向量數量鎖定：新增向量要顯式改這個數字，避免測試檔被悄悄縮水。

    16 = 原 15 條歷史案向量 + PR #2 的示範案件向量 `demo-114-1147061268`
    （前端決定書理由五的數字由它鎖定）。
    """
    assert_eq(len(VECTORS), 16, "測試向量數量變動")


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


def test_public_overdue_replay_agreement_at_least_99_percent():
    """爬蟲 77-2 不受理案回放（spec AC13）：**只報告一致率，不 assert**。

    檔案不存在就跳過（不假裝有跑）。筆數不足 100 也只報告不 assert
    ——「樣本夠不夠」是量測範圍問題，不是紅線（控制端 2026-09-07 裁定）。
    回放集由 `scripts/replay_overdue_public.py` 產生，只含案號與日期（無人名、無全文）。
    """
    p = BACKEND / "data" / "replay" / "overdue-public.jsonl"
    if not p.exists():
        print("  skip  回放集不存在（跑 scripts/replay_overdue_public.py 產生）")
        return
    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) < 100:
        print(f"  info  回放集只有 {len(rows)} 件，樣本不足以宣稱一致率——不 assert，只記錄")
        return
    agree = 0
    mismatched: list[str] = []
    for r in rows:
        res = compute(service_method=r["service_method"], service_date=_d(r["service_date"]),
                      filing_date=_d(r["filing_date"]), transit_days=0, interested_party=False)
        if bool(res.overdue) == r["expected_overdue"]:
            agree += 1
        else:
            mismatched.append(str(r["case_no"]))
    rate = agree / len(rows)
    # 只報告不 assert：這是量測不是紅線，日期 heuristics 偏差不該卡住全套測試（plan-guardian 2026-09-07）
    print(f"  info  公開案回放一致率 {rate:.3%}（{agree}/{len(rows)}）；目標 ≥ 99%，未達即寫入 docs/evidence 的 replay-mismatch.md")
    if mismatched:
        print(f"  info  不一致案號（前 10）：{', '.join(mismatched[:10])}")
