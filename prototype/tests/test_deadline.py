import json
import pathlib
import sys
import datetime as dt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from engine.deadline import compute  # noqa: E402

VECTORS = json.loads((pathlib.Path(__file__).resolve().parents[1] / "data" / "test-vectors.json").read_text())["vectors"]


def _d(s):
    return dt.date.fromisoformat(s) if s else None


def test_vectors():
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
    assert not fails, "\n".join(fails)


def test_public_service_refuses():
    r = compute("public", dt.date(2024, 6, 13))
    assert r.deadline is None and r.caveats


def test_overdue_carries_article80_caveat():
    r = compute("personal", dt.date(2023, 11, 12), dt.date(2025, 10, 20))
    assert r.overdue is True
    assert any("80" in c for c in r.caveats)


def test_steps_have_basis():
    r = compute("deposit", dt.date(2024, 6, 13), dt.date(2024, 7, 20))
    assert all(s.basis for s in r.steps) and len(r.steps) >= 5
