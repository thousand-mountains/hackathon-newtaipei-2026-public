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

import dataclasses
import re
import time
import urllib.parse
from typing import Any

from backend.config import settings
from backend.retrieval.base import Hit

try:  # 第三方相依只在真的要打 Bedrock 時才需要；缺席要看得見，不是隱藏開關
    import boto3
except ImportError:  # pragma: no cover - 有裝 boto3 的環境走不到
    boto3 = None

# 相似案通道收哪些前綴——**唯一的事實來源**。N4 不自己再寫一份（兩處各寫一份，
# 改一邊就會漏）。兩批都是新北市政府訴願決定書，性質相同，只是來源不同：
#   歷史訴願決定書/      official（賽方資料集，101 筆）
#   新北訴願決定書_全量/  public_crawl（市府公開全量爬蟲，2347 筆）
# 來源差異由 `_provenance()` 標出來，UI 看得到，不靠「只收一批」來維持誠實。
# 刻意**不收** 行政函釋/ 與 司法院釋字及行政判解/：那兩類是法條通道（通道 A）與
# N5 `REF_PREFIXES` 的材料，不是「相似案」。放寬不等於全收。
DEFAULT_PREFIXES = ["歷史訴願決定書/", "新北訴願決定書_全量/"]

# 兩批的席次配額。**分開查、各自取 top-k、再合併**，不是先撈一大包再硬塞席次——
# 後者會在官方那批其實不相關時，硬把爛結果塞進前五名。
# 為什麼要配額：public 的檔數是 official 的 23 倍，單次查詢的前 15 名會被 public 佔滿
# （2026-09-12 實測 15/15），賽方那 101 筆精選案永遠出不來。做得起來的前提是兩批的
# 分數落在同一條線（實測 0.77–0.80）——**若哪天 official 的最佳命中掉到跟 public 差一截，
# 這個配額就該回頭重議**，因為那時保席次等於犧牲相關性。
# 某一批不足時由另一批補滿（不留空位），但一律仍受 KB_MIN_SCORE 門檻約束：寧可少一筆。
SIMILAR_CASE_QUOTA = {"歷史訴願決定書/": 2, "新北訴願決定書_全量/": 3}
# 兩次 retrieve 之間的間隔。賽方規範要求 Bedrock 壓在 1 RPS 以下；retrieve 不是
# InvokeModel，但保守做——評審面前吃 throttle 的代價遠大於多等一秒。
RETRIEVE_INTERVAL_S = 1.1
# 配額查詢一次要抓多深。Managed KB 的 filter 不支援路徑比對（S3 物件的 provenance
# metadata 不是 KB 的可篩欄位），所以「只要 official 那批」只能靠抓深再後過濾。
# 2026-09-12 實測同一個查詢：numberOfResults=15 撈到 official 0 筆、=50 撈到 2 筆——
# 抓不夠深，配額席次就會永遠空著。這個值是實測出來的下限，不是猜的。
QUOTA_FETCH_DEPTH = 50
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
        """明確指定 `filters["prefix"]`（N5 的 retrieve_refs）→ 單次查詢，行為不變。

        沒指定 prefix ＝ 相似案通道 → 依 `SIMILAR_CASE_QUOTA` 兩批分開查再合併。
        """
        filters = filters or {}
        exclude = filters.get("exclude_case") or self.exclude_case
        explicit = list(filters.get("prefix") or [])
        if explicit:
            want = max(1, min(top_k * 3, 50))  # 後過濾會刷掉大半，先多抓三倍
            hits = self._retrieve(query, explicit, exclude, want=want, limit=top_k)
        else:
            hits = self._quota_search(query, exclude, top_k)
        # 編號在排序之後才給，`kb-1` 永遠是分數最高的那筆
        return [dataclasses.replace(h, id=f"kb-{i}") for i, h in enumerate(hits, start=1)]

    def _quota_search(self, query: str, exclude: str | None, top_k: int) -> list[Hit]:
        """兩批各自查、各自取配額席次，不足由另一批補滿，最後按分數排序。"""
        batches: list[tuple[int, list[Hit]]] = []
        for i, (prefix, quota) in enumerate(SIMILAR_CASE_QUOTA.items()):
            if i:
                time.sleep(RETRIEVE_INTERVAL_S)
            batches.append(
                (quota, self._retrieve(query, [prefix], exclude, want=QUOTA_FETCH_DEPTH, limit=top_k))
            )
        picked: list[Hit] = []
        for quota, rows in batches:
            picked.extend(rows[:quota])
        # 補位：某批沒撈滿配額，就讓另一批把剩下的席次補起來（不留空位）。
        # 這裡取的仍是 `_retrieve` 濾過門檻的結果，配額不會讓低分的東西進來。
        if len(picked) < top_k:
            for quota, rows in batches:
                for h in rows[quota:]:
                    if len(picked) >= top_k:
                        break
                    picked.append(h)
        picked.sort(key=lambda h: h.score, reverse=True)
        return picked[:top_k]

    def _retrieve(self, query: str, prefixes: list[str], exclude: str | None, *,
                  want: int, limit: int) -> list[Hit]:
        """打一次 KB 並做後過濾，回傳最多 limit 筆（`id` 是佔位值，由呼叫端重編）。"""
        resp = self._c().retrieve(
            knowledgeBaseId=self.kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": want}},
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
            if len(hits) >= limit:
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
