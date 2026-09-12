"""Bedrock Managed Knowledge Base 檢索（N4 通道 B、N5 retrieve_refs 工具共用）。

這是**檢索**不是 LLM：呼叫 boto3 `bedrock-agent-runtime.retrieve`，不生成任何文字，
也不 import `backend.llm`——所以 N4 用它不違反「規則引擎零 LLM 依賴」（CONSTITUTION §4）。
`run_all.py` 對本檔具名豁免第三方依賴（`DEPENDENCY_EXEMPT_FILES`）。

boto3 的 import 放在**模組頂層**並用 try/except 守衛（spec D8）：本機系統 python3 沒有
boto3，測試一律注入假 client，所以 `import backend.retrieval.kb` 必須成功；真的要打
Bedrock 卻沒裝套件時，在 `_c()` 明確 raise 說明缺什麼，不靜默失敗。

Managed KB 的 filter 不支援路徑比對（AppealAssist 實測），所以多抓三倍再在這裡後過濾：
分數門檻、URI 前綴、PDF 一律丟（Managed KB 對這批 PDF 解析全是亂碼）、demo 案來源決定書排除。
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from backend.config import settings
from backend.retrieval.base import Hit

try:  # 第三方相依只在真的要打 Bedrock 時才需要；缺席要看得見，不是隱藏開關
    import boto3
except ImportError:  # pragma: no cover - 有裝 boto3 的環境走不到
    boto3 = None

DEFAULT_PREFIXES = ["歷史訴願決定書/"]
OUTCOME_RE = re.compile(r"(駁回|撤銷|不受理)")


def _relative_path(uri: str) -> tuple[str, str]:
    """s3://bucket/kb/official/歷史訴願決定書/113年/x.txt → ("official", "歷史訴願決定書/113年/x.txt")。"""
    path = urllib.parse.unquote(uri)
    path = path.split("/", 3)[-1] if path.startswith("s3://") else path  # 去掉 s3://bucket/
    path = path.replace(" 的副本", "")
    m = re.match(r"^kb/(official|public)/(.+)$", path)
    if m:
        return m.group(1), m.group(2)
    return "unknown", path


def _provenance(kind: str) -> str:
    return {"official": "official", "public": "public_crawl"}.get(kind, "unknown")


class KBRetriever:
    """Managed KB 相似案／函釋檢索。命中一律 `verified=False`——對回資料集實檔是 N6 的事。"""

    name = "bedrock_kb"

    def __init__(self, kb_id: str, region: str, min_score: float | None = None,
                 exclude_case: str | None = None, client: Any = None) -> None:
        if not kb_id or not region:
            raise ValueError("KBRetriever 需要 kb_id 與 region（BEDROCK_KB_ID / AWS_REGION）")
        self.kb_id = kb_id
        self.region = region
        self.min_score = settings.kb_min_score() if min_score is None else min_score
        self.exclude_case = exclude_case
        self._client = client

    def _c(self) -> Any:
        if self._client is None:
            if boto3 is None:
                raise ValueError(
                    "KBRetriever 需要 boto3 才能呼叫 bedrock-agent-runtime，但本環境沒有安裝。"
                    "請安裝 boto3，或改用 RETRIEVER=lawtable_only（相似案通道回空並標「庫外，未驗證」）。"
                )
            self._client = boto3.client("bedrock-agent-runtime", region_name=self.region)
        return self._client

    def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[Hit]:
        filters = filters or {}
        prefixes = list(filters.get("prefix") or DEFAULT_PREFIXES)
        exclude = filters.get("exclude_case") or self.exclude_case
        want = max(1, min(top_k * 3, 50))  # 後過濾會刷掉大半，先多抓三倍
        resp = self._c().retrieve(
            knowledgeBaseId=self.kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"managedSearchConfiguration": {"numberOfResults": want}},
        )
        hits: list[Hit] = []
        for r in resp.get("retrievalResults", []):
            score = float(r.get("score", 0.0))
            if score < self.min_score:
                continue
            if (r.get("metadata") or {}).get("_file_type") == "PDF":
                continue
            uri = (r.get("metadata") or {}).get("_source_uri") or (
                (r.get("location") or {}).get("s3Location") or {}
            ).get("uri", "")
            kind, rel = _relative_path(uri)
            if not any(rel.startswith(p) for p in prefixes):
                continue
            if exclude and exclude in rel:
                continue
            fname = rel.rsplit("/", 1)[-1]
            m = OUTCOME_RE.search(fname)
            text = re.sub(r"\s+", " ", ((r.get("content") or {}).get("text") or "")).strip()
            hits.append(
                Hit(
                    id=f"kb-{len(hits) + 1}",
                    title=fname.rsplit(".", 1)[0],
                    score=round(score, 3),
                    source=rel,
                    origin="retrieval",
                    verified=False,
                    note="",
                    # outcome 照檔名／主文，不由模型推測（CONSTITUTION §2）
                    payload={"outcome": m.group(1) if m else None,
                             "provenance": _provenance(kind),
                             "text": text},
                )
            )
            if len(hits) >= top_k:
                break
        return hits

    def meta(self) -> dict[str, Any]:
        return {"backend": self.name, "available": True, "min_score": self.min_score,
                "kb_id_set": bool(self.kb_id)}


def build_retriever(kind: str, *, exclude_case: str | None = None) -> KBRetriever | None:
    """編排層用：依 RETRIEVER 建相似案檢索器。lawtable_only → None（維持 Phase 0 行為）。"""
    if kind != "kb":
        return None
    missing = [n for n, v in (("BEDROCK_KB_ID", settings.kb_id()),
                              ("AWS_REGION", settings.aws_region())) if not v]
    if missing:
        raise ValueError(f"RETRIEVER=kb 需要環境變數 {missing}（見 .env.example）")
    return KBRetriever(kb_id=settings.kb_id(), region=settings.aws_region(), exclude_case=exclude_case)
