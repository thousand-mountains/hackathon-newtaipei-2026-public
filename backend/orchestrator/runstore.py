"""執行結果持久化：每次 run_case 的終態 CaseState 存成 backend/output/runs/{run_id}.json。

用途只有一個：續跑（POST /runs 帶 base_run_id + from_node）。檔案就是 CaseState.as_dict()，
沒有額外 schema；Phase 1 用檔案不上 DB。output/ 已 gitignored。

**run_id 走白名單 regex 才拼路徑**：run_id 之後會從 HTTP body 進來，
不擋就等於讓呼叫端指定任意檔案路徑（`../../etc/x`）。格式不合就直接 ValueError，
不做「清洗後照樣讀」——清洗是猜對方想讀什麼，拒絕才是誠實。
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import re

from backend.config.settings import RUNS_DIR
from backend.orchestrator.state import CaseState

RUN_ID_RE = re.compile(r"^run-[A-Za-z0-9_\-]+$")


class RunNotFound(FileNotFoundError):
    pass


def _path(run_id: str, runs_dir: pathlib.Path | None) -> pathlib.Path:
    if not RUN_ID_RE.match(run_id or ""):
        raise ValueError(f"run_id 格式不合法：{run_id!r}")
    return (runs_dir or RUNS_DIR) / f"{run_id}.json"


def save_run(state: CaseState, runs_dir: pathlib.Path | None = None) -> pathlib.Path:
    p = _path(state.run_id, runs_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load_run(run_id: str, runs_dir: pathlib.Path | None = None) -> CaseState:
    """讀回終態。**只挑 CaseState 認得的欄位**：舊版檔案多出來的鍵直接忽略，
    不讓一個格式漂移的檔案把續跑整條路徑炸掉。"""
    p = _path(run_id, runs_dir)
    if not p.exists():
        raise RunNotFound(f"找不到執行紀錄 {run_id}（{p}）")
    raw = json.loads(p.read_text(encoding="utf-8"))
    names = {f.name for f in dataclasses.fields(CaseState)}
    return CaseState(**{k: v for k, v in raw.items() if k in names})
