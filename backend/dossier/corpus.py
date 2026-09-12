"""母庫（Bedrock KB ＋ S3）唯讀查詢：搜尋清單走 KB，全文走 S3 `get_object`。

**為什麼全文不從 KB 片段拼**（契約 v2 §4.2、CONSTITUTION §1）：KB 回的是 chunk，
拼起來會得到一份**殘缺卻看起來完整**的法規——承辦人讀到第 12 條接第 31 條不會發現
中間少了十九條，因為它看起來就是一份連續的法條文件。那正是「形式具備、實質不具備」。
S3 上那個 `.txt` 才是唯一的全文來源。

**法規有兩條通道，保證等級不同，不可以合成一個 `verified`**（契約 §4.2）：

- **查表**（`backend/retrieval/lawtable.py` + `laws-snapshot.json`）＝引用可驗、二元、
  燈號依據。**本檔一行都不碰它。**
- **KB 法規全文（本檔）**＝給人看與搜尋用，**恆 `verified:false`、`relevance:"unknown"`**，
  不影響 `lamp`。兩者混成一個旗標，燈號語意當場就穿。

**本檔不 import boto3。** client 一律由呼叫端注入（`backend/api/dossier.py` 建）。
理由不是潔癖，是 `run_all.py` 的紅線掃描：「核心與測試路徑零外部依賴」，
具名豁免只有 `backend/api/`、`backend/llm/`、`backend/retrieval/kb.py`。
與其把本檔加進豁免名單把洞開大，不如讓它變成一個**對注入 client 的純函式**
——順帶讓測試不必 monkeypatch 任何東西，直接餵假 client 就好。
"""
from __future__ import annotations

import json
import re
from typing import Any

from backend.config import settings
from backend.retrieval.kb import search_corpus

#: 母庫物件 key 的白名單。`doc_id` 從 HTTP path 進來直接餵 `s3.get_object`，
#: 不擋就等於讓呼叫端讀這個 bucket 裡的任意物件（含別人的執行輸出、備份、憑證檔）。
#: 四道條件缺一不可：
#:   1. 只准 `kb/official/` 與 `kb/public/` 兩個前綴（母庫就在這兩處）
#:   2. 不得含 `..`（S3 沒有目錄概念，但 key 裡的 `..` 會讓人誤判範圍，直接擋）
#:   3. 必須 `.txt` 結尾（母庫的全文一律是 .txt；PDF 那批 Managed KB 解出來是亂碼）
#:   4. 不得是側檔 `.txt.metadata.json`（那是 metadata，不是文件內容）
_KEY_RE = re.compile(r"^kb/(official|public)/(?!.*\.\.)[^\x00]+\.txt$")

#: KB 的 `doc_kind` 值域（2026-09-12 實測側檔）。
DOC_KIND_STATUTE = "statute"
DOC_KIND_DECISION = "decision"
#: **法院裁判書本期不納入相似案例**（2026-09-12 Ci 拍板）：那是法院判的，
#: 訴願決定是訴願會決定的。混進「相似案例」等於把法院裁判說成訴願前例。
#: 它在 KB 裡很會搶席次（實測不篩 20 筆裡佔 15 筆），所以要明確擋，不能靠運氣。
DOC_KIND_COURT_RULING = "court_ruling"


class CorpusUnavailable(RuntimeError):
    """母庫打不開（缺套件／缺設定）。**照實說缺什麼**，不回空清單冒充「查無」。"""


class DocumentNotFound(FileNotFoundError):
    pass


class OutOfScope(ValueError):
    """要的東西存在，但本期刻意不供應（例：法院裁判書）。與「找不到」不是同一件事。"""


def safe_key(doc_id: str) -> str:
    """驗 `doc_id` 是合法的母庫 key，回傳它本身。不合法直接 `ValueError`。

    **不做清洗**（去掉 `..`、補前綴之類）：清洗是猜對方想讀什麼，
    而猜錯的代價是讀出一個不該讀的物件。拒絕才是誠實。
    """
    k = (doc_id or "").strip()
    if not _KEY_RE.match(k) or k.endswith(".metadata.json"):
        raise ValueError(
            f"母庫文件 id 不合法：{doc_id!r}。"
            f"必須是 kb/official/… 或 kb/public/… 底下的 .txt（不得含 .. 或指向側檔）。"
        )
    return k


def _bucket() -> str:
    b = settings.kb_bucket()
    if not b:
        raise CorpusUnavailable(
            "母庫全文需要環境變數 S3_KB_BUCKET（見 .env.example），目前沒有設定。"
            "沒有它就讀不到 S3 上的法規／決定書全文——這裡不回空清單冒充查無。"
        )
    return b


def _need(client: Any, what: str) -> Any:
    """client 是必填的。`None` 代表呼叫端沒建起來——**照實說，不靜默回空**。"""
    if client is None:
        raise CorpusUnavailable(f"母庫{what}需要注入 boto3 client（由 backend/api/dossier.py 建）")
    return client


def _kb_id() -> str:
    kb = settings.kb_id()
    if not kb:
        raise CorpusUnavailable("母庫搜尋需要環境變數 BEDROCK_KB_ID（見 .env.example）")
    return kb


def _is_missing_key(e: Exception) -> bool:
    """這個例外是不是「S3 上沒有這個 key」。

    botocore 的例外型別是動態生成的，`isinstance` 派不上用場；而 `ClientError`
    什麼錯都用它包（403、404、429 都是），所以要看 `response.Error.Code`。
    """
    if type(e).__name__ == "NoSuchKey":
        return True
    err = (getattr(e, "response", None) or {}).get("Error") or {}
    return str(err.get("Code")) in ("NoSuchKey", "404", "NotFound")


#: 從 AccessDenied 的訊息裡挖出「被拒的是哪個動作」。
#: botocore 不把它放進結構化欄位，只在 `Error.Message` 的自然語言裡。
_DENIED_ACTION_RE = re.compile(r"is not authorized to perform:\s*([A-Za-z0-9]+:[A-Za-z0-9*]+)")


def _denied_action(e: Exception) -> str | None:
    err = (getattr(e, "response", None) or {}).get("Error") or {}
    m = _DENIED_ACTION_RE.search(str(err.get("Message") or e))
    return m.group(1) if m else None


def _is_masked_missing_key(e: Exception) -> bool:
    """這個 AccessDenied 其實是「key 不存在」被 S3 偽裝成 403 嗎。

    **S3 對沒有 `s3:ListBucket` 的呼叫者，把不存在的物件回 403 而不是 404**
    ——否則呼叫者可以用 404／403 的差別去列舉 bucket 內容。所以我們這個 task role
    （只給了 `GetObject`）一碰到打錯的 id，拿到的是一句講權限的錯。

    2026-09-13 雲上實際遇到的訊息長這樣（帳號與 bucket 已遮）：

        AccessDenied … User: arn:aws:sts::…:assumed-role/… is not authorized to
        perform: s3:ListBucket on resource: "arn:aws:s3:::…"

    **判準是被拒的動作是不是 `ListBucket`**，不是「凡 AccessDenied 都當找不到」：

    - `s3:ListBucket` 被拒 → 這是「存在與否」的查詢被擋，也就是 S3 在說
      「我不告訴你它在不在」。在我們這個 role 上，這**必然**代表 key 不存在
      （存在的 key 用 `GetObject` 讀得到，根本不會走到問 ListBucket 這一步）。
    - `s3:GetObject` 被拒 → 這是**真的權限壞了**。往上丟，不得吞成 404，
      否則整個母庫掛掉會長得像「每一份法規都查無」，我們自己會瞎掉。
    - 挖不出動作 → 也往上丟。猜錯的代價不對稱：把權限故障說成「查無」
      會讓人去找一份其實存在的檔案，而且找一整晚。

    **這個判準只在「本服務的 role 沒有 ListBucket」這個前提下成立。**
    哪天 role 被加上 `s3:ListBucket`，不存在的 key 會改回 404（`_is_missing_key`
    那條就接得住），這條分支自然不再被觸發——不會因此誤判，只是變成冗餘。
    """
    err = (getattr(e, "response", None) or {}).get("Error") or {}
    if str(err.get("Code")) not in ("AccessDenied", "403"):
        return False
    return _denied_action(e) == "s3:ListBucket"


def fetch_text(key: str, s3: Any = None) -> str:
    """讀一份母庫文件的**全文**。`key` 必須已經過 `safe_key`。"""
    try:
        obj = _need(s3, "全文").get_object(Bucket=_bucket(), Key=key)
    except Exception as e:  # noqa: BLE001 - botocore 例外型別動態生成，不能用 isinstance
        # 只有「這個 key 不存在」翻成 404。權限不足、網路不通、bucket 名寫錯一律往上丟
        # ——把它們也說成「找不到這份文件」會讓人去找一份其實存在的檔案。
        # 「不存在」有兩種長相：直球的 NoSuchKey／404，以及被 S3 偽裝成 403 的那種
        # （見 `_is_masked_missing_key`）。
        if _is_missing_key(e) or _is_masked_missing_key(e):
            raise DocumentNotFound(f"母庫沒有這份文件：{key}") from e
        raise
    return obj["Body"].read().decode("utf-8")


def fetch_sidecar(key: str, s3: Any = None) -> dict[str, Any]:
    """讀 `{key}.metadata.json` 的 `metadataAttributes`。

    **側檔缺席就回空 dict，不從內文猜**（契約 §4.3）：公開爬蟲那批的檔名只有
    「案號_結果」，案型不在裡面。猜錯就是把不同案型的案子當相似案推給承辦人。
    """
    # **缺 client／缺 bucket 設定要往上丟，不能被下面那個寬 except 吞掉**：
    # 那兩種是「母庫根本打不開」，回空 dict 會讓它看起來只是「這份沒有側檔」，
    # 於是 verdict／category 靜默變成 null，而畫面上分不出「沒有側檔」與「沒連上 S3」。
    client, bucket = _need(s3, "側檔"), _bucket()
    try:
        obj = client.get_object(Bucket=bucket, Key=key + ".metadata.json")
    except Exception:  # noqa: BLE001 - 側檔本來就可能不存在，這不是錯誤
        return {}
    try:
        return dict(json.loads(obj["Body"].read().decode("utf-8")).get("metadataAttributes") or {})
    except (ValueError, KeyError, AttributeError):
        return {}


def search_statutes(query: str, *, limit: int = 10, kb: Any = None) -> list[dict[str, Any]]:
    """`GET /api/laws?q=` 的內容。回 `{id,t,src,score,doc_kind}`（契約 §4.2）。"""
    hits = search_corpus(_need(kb, "搜尋"), _kb_id(), query, [DOC_KIND_STATUTE], limit=limit)
    return [{"id": h["id"], "t": h["t"], "src": h["src"], "score": h["score"],
             "doc_kind": h["doc_kind"]} for h in hits]


def search_decisions(query: str, *, limit: int = 10, kb: Any = None) -> list[dict[str, Any]]:
    """`GET /api/decisions?q=` 的內容。回 `{id,t,src,score,verdict,category,provenance,doc_kind}`。

    **法院裁判書不納入**：server-side filter 已經把 `doc_kind` 框成 `decision`，
    這裡再過濾一次是**刻意的雙保險**——filter 若哪天因為側檔缺失而失效
    （`_retrieve` 就有一個「篩了全空退回不篩」的分支），我們不會靜默地開始端裁判書。
    """
    hits = search_corpus(_need(kb, "搜尋"), _kb_id(), query, [DOC_KIND_DECISION], limit=limit)
    return [{"id": h["id"], "t": h["t"], "src": h["src"], "score": h["score"],
             "verdict": h["verdict"], "category": h["category"],
             "provenance": h["provenance"], "doc_kind": h["doc_kind"]}
            for h in hits if h["doc_kind"] != DOC_KIND_COURT_RULING]


def get_statute(law_id: str, *, s3: Any = None) -> dict[str, Any]:
    """`GET /api/laws/{lawId}`。**`verified` 恆 False、`relevance` 恆 unknown**。

    這兩個值不是佔位符，是這條通道的真實保證等級：KB 法規全文沒有經過
    `laws-snapshot.json` 的查表驗證，畫成「字號已驗」就是說謊（契約 §4.2）。
    """
    key = safe_key(law_id)
    md = fetch_sidecar(key, s3)
    body = fetch_text(key, s3)
    return {
        "id": key,
        "t": key.rsplit("/", 1)[-1].rsplit(".", 1)[0],
        "src": key.split("/", 2)[-1],
        "body": body,
        "verified": False,
        "relevance": "unknown",
        "provenance": md.get("provenance"),
        "category": md.get("category"),
    }


def get_decision(decision_id: str, *, s3: Any = None) -> dict[str, Any]:
    """`GET /api/decisions/{decisionId}`。回 `{id,t,src,verdict,category,full}`（契約 §4.3）。

    側檔說它是 `court_ruling` 時丟 `OutOfScope`（→ 400 並說明理由），
    **不靜默回一份法院裁判**：搜尋端不會回它，但 id 是可以被手打進來的。
    """
    key = safe_key(decision_id)
    md = fetch_sidecar(key, s3)
    if md.get("doc_kind") == DOC_KIND_COURT_RULING:
        raise OutOfScope(
            f"{key} 是法院裁判書（doc_kind=court_ruling），本期不納入相似案例："
            f"那是法院判的，訴願決定是訴願會決定的，混在一起等於把法院裁判說成訴願前例。"
        )
    full = fetch_text(key, s3)
    return {
        "id": key,
        "t": key.rsplit("/", 1)[-1].rsplit(".", 1)[0],
        "src": key.split("/", 2)[-1],
        "verdict": md.get("outcome"),
        "category": md.get("category"),
        "provenance": md.get("provenance"),
        "doc_kind": md.get("doc_kind"),
        "full": full,
    }
