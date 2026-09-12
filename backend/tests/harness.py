"""極簡測試 harness（stdlib only，不依賴 pytest）。

為什麼不用 pytest：Phase 0 的紅線之一是「測試路徑不新增外部依賴」，
`backend/tests/run_all.py` 必須能在一台只有 python3 的機器上直接跑起來。
pytest 風格的 `test_*` 函式命名保留，之後要接 pytest 也不用改寫。
"""
from __future__ import annotations

import traceback
from types import ModuleType
from typing import Callable


class TestFailure(AssertionError):
    pass


class TestSkipped(Exception):
    """這條測試**沒有執行**——不是通過。

    只在「執行它所需的本機資料不在 repo 裡」時用（例：含個資、不得進 git 的語料）。
    刻意不併進 passed：一條沒跑的測試被算成綠燈，就是我們一整天在抓的
    「形式具備、實質不具備」。跑完會單獨列出，並附上補齊資料的指令。
    """


def collect(module: ModuleType) -> list[tuple[str, Callable[[], None]]]:
    """收集模組內所有 test_ 開頭的無參數函式，依原始碼行號排序。"""
    items = []
    for name in dir(module):
        if not name.startswith("test_"):
            continue
        fn = getattr(module, name)
        if callable(fn):
            lineno = getattr(getattr(fn, "__code__", None), "co_firstlineno", 0)
            items.append((lineno, f"{module.__name__}.{name}", fn))
    items.sort()
    return [(n, f) for _, n, f in items]


SKIPPED: list[str] = []


def run(modules: list[ModuleType], verbose: bool = True) -> tuple[int, int, list[str]]:
    """跑完所有模組的測試，回傳 (通過數, 總數, 失敗訊息清單)。

    略過的測試**不計入 passed，也不計入 total**，另記在 `SKIPPED`。
    """
    passed = 0
    total = 0
    failures: list[str] = []
    for module in modules:
        for name, fn in collect(module):
            try:
                fn()
            except TestSkipped as e:
                SKIPPED.append(f"{name}：{e}")
                if verbose:
                    print(f"  略過  {name} —— {e}")
                continue
            except Exception:  # noqa: BLE001 - 測試 harness 要吞下所有例外
                total += 1
                failures.append(f"FAIL {name}\n{traceback.format_exc()}")
                if verbose:
                    print(f"  FAIL  {name}")
            else:
                total += 1
                passed += 1
                if verbose:
                    print(f"  ok    {name}")
    return passed, total, failures


def assert_eq(got, want, msg: str = "") -> None:
    if got != want:
        raise TestFailure(f"{msg}\n  got : {got!r}\n  want: {want!r}")


def assert_true(cond, msg: str = "") -> None:
    if not cond:
        raise TestFailure(msg or "expected truthy")


def assert_in(needle, haystack, msg: str = "") -> None:
    if needle not in haystack:
        raise TestFailure(f"{msg}\n  {needle!r} not in {haystack!r}")
