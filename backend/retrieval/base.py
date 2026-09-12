"""檢索介面：N2（分類 kNN）與 N4（檢索）共用的元件（architecture §10）。

N2 跑在 N4 之前，所以 retrieval 不是 N4 的內部實作，而是編排層注入的共用元件。
這個簽名是三邊（N2／N4／編排層）的凍結契約，Phase 0 先把形狀定下來。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Hit:
    """一筆檢索結果。

    `verified` 標明這筆命中是否可對回資料集實體：
    - True  ：在 laws-snapshot.json 或（未來的）決定書索引內，可驗
    - False ：庫外，未驗證——必須連同 `note` 一起外顯，不得當成已驗證來用
    """

    id: str
    title: str
    score: float
    source: str
    origin: str = "retrieval"
    verified: bool = False
    note: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "t": self.title,
            "score": self.score,
            "src": self.source,
            "origin": self.origin,
            "verified": self.verified,
            "note": self.note,
            **({"payload": self.payload} if self.payload else {}),
        }


class Retriever(Protocol):
    """凍結的檢索簽名。實作端：lawtable（法條查表）／kb（Bedrock KB）／local（BM25）。"""

    name: str

    def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[Hit]:
        ...


class UnavailableRetriever:
    """庫外通道：沒有資料集就誠實回空。

    CONSTITUTION §2 的直接落實——賽方資料集不在本機、也不進 git，
    所以「相似歷史案」這條通道在 Phase 0 **不能有任何內容**。
    回空 list 並在 `reason` 說明為什麼，不是偷懶，是紅線。
    """

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[Hit]:
        return []

    def meta(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "available": False,
            "reason": self.reason,
            "hits": 0,
            "verified": False,
            "label": "庫外，未驗證",
        }
