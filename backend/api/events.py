"""進程內執行狀態與事件：run_id → 事件序列與狀態。

Task 7a 用 status()（輪詢）；Task 7b 用 stream()（SSE）。單 process、demo 量級、不持久化；
process 重啟事件就沒了，結果仍在 runstore。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any


class RunEvents:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._status: dict[str, dict[str, Any]] = {}

    def start(self, run_id: str) -> None:
        with self._lock:
            self._events.setdefault(run_id, [])
            self._status[run_id] = {"status": "running"}

    def push(self, run_id: str, kind: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._events.setdefault(run_id, []).append({"event": kind, "data": data})
            if kind == "run_done":
                self._status[run_id] = {"status": "done"}
            elif kind == "run_failed":
                self._status[run_id] = {
                    "status": "failed",
                    "node": data.get("node"),
                    "error": data.get("error"),
                }

    def status(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._status[run_id]) if run_id in self._status else None

    def stream(self, run_id: str, poll_s: float = 0.2, idle_timeout_s: float = 300.0):
        """yield SSE 字串；run_done／run_failed 之後結束（Task 7b 的端點用）。"""
        sent = 0
        idle = 0.0
        while True:
            with self._lock:
                events = list(self._events.get(run_id, []))
            for ev in events[sent:]:
                yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'], ensure_ascii=False)}\n\n"
            new = len(events) - sent
            sent = len(events)
            if events and events[-1]["event"] in ("run_done", "run_failed"):
                return
            if new == 0:
                idle += poll_s
                if idle >= idle_timeout_s:
                    yield "event: timeout\ndata: {}\n\n"
                    return
                time.sleep(poll_s)
            else:
                idle = 0.0


BUS = RunEvents()
