"""Bedrock Managed Knowledge Base 檢索（N4 通道 B、N5 retrieve_refs 工具共用）。

這是**檢索**不是 LLM：呼叫 boto3 `bedrock-agent-runtime.retrieve`，不生成任何文字，
也不 import `backend.llm`——所以 N4 用它不違反「規則引擎零 LLM 依賴」（CONSTITUTION §4）。
`run_all.py` 對本檔具名豁免第三方依賴（`DEPENDENCY_EXEMPT_FILES`）。

boto3 的 import 放在**模組頂層**並用 try/except 守衛（spec D8）：本機系統 python3 沒有
boto3，測試一律注入假 client，所以 `import backend.retrieval.kb` 必須成功；真的要打
Bedrock 卻沒裝套件時，在 `_c()` 明確 raise 說明缺什麼，不靜默失敗。

Managed KB 的 filter 不支援路徑比對（AppealAssist 實測），所以多抓三倍再在這裡後過濾：
分數門檻、URI 前綴、PDF 一律丟（Managed KB 對這批 PDF 解析全是亂碼）、demo 案來源決定書排除、
同一份文件的多個 chunk 只留最高分那筆。
"""
from __future__ import annotations

import dataclasses
import difflib
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
# 收哪些前綴、各幾席，**唯一的事實來源是 settings.similar_case_quota()**。
# 這裡刻意不留模組層常數：目錄名跟著 corpus 走（`新北訴願決定書_全量/` vs
# `新北訴願決定書_環保局全量/`），留一份常數在這裡，遲早會有人拿它當真。

# `retrieve_refs` 工具只准查這兩個前綴：函釋與判解是「可以引用的來源」，
# 決定書、卷證等不在此列（那些走 N4 的檢索通道，並且要另外做引用驗證）。
#
# **唯一的事實來源是 `settings.ref_prefixes()`，這裡不留模組層常數。**
#
# 這份清單原本定義在 `backend/nodes/n5_draft.py`，後來搬到這裡，為的是讓聊天層
# 也拿得到同一份——`backend/llm/chat.py` 不得 import `backend.nodes.*`
# （n5_draft 自己 import backend.llm.client，聊天層再 import 它就是層級倒置，
# 而且會把 backend.orchestrator.* 整包拉進純函式測試的 import 圖，
# 見 spec 2026-09-12-chat-honesty-lamps §4.0）。那個分層理由**仍然成立**。
#
# 但答案不是「把字面值搬到檢索層」，是**搬到比兩者都底層的 settings**：
# 目錄名跟著 corpus 走（第三方 corpus 的函釋在 `行政函釋_全量/`，4,520 筆），
# 寫死 `行政函釋/` 的話 N5 只看得到自己整理的那 10 份、而且不報錯。
# `settings` 誰都可以依賴，白名單由 `.env` 的 `REF_PREFIXES` 帶，預設值只有一份。

# 兩批的席次配額。**分開查、各自取 top-k、再合併**，不是先撈一大包再硬塞席次——
# 後者會在官方那批其實不相關時，硬把爛結果塞進前五名。
# 為什麼要配額：public 的檔數是 official 的 23 倍，單次查詢的前 15 名會被 public 佔滿
# （2026-09-12 實測 15/15），賽方那 101 筆精選案永遠出不來。做得起來的前提是兩批的
# 分數落在同一條線（實測 0.77–0.80）——**若哪天 official 的最佳命中掉到跟 public 差一截，
# 這個配額就該回頭重議**，因為那時保席次等於犧牲相關性。
# 某一批不足時由另一批補滿（不留空位），但一律仍受 KB_MIN_SCORE 門檻約束：寧可少一筆。
#
# **開了重排之後，席次保證的是「進得了候選池」，不是「進得了前五名」**（2026-09-12）。
# 席次會按比例放大到候選池（5 席→30 筆候選，官方 2 席→12 筆），最終前五名純由
# 重排的相關性分數決定。理由與上一段的但書是同一個：重排把相關性量準了
# （負控制 0.00 vs 真實案件 0.81+），這時再保席次就是拿相關性換版面。
# 細節見 `_quota_search`。
# 兩次 retrieve 之間的間隔。賽方規範要求 Bedrock 壓在 1 RPS 以下；retrieve 不是
# InvokeModel，但保守做——評審面前吃 throttle 的代價遠大於多等一秒。
RETRIEVE_INTERVAL_S = 1.1
# 配額查詢一次要抓多深。Managed KB 的 filter 不支援路徑比對（S3 物件的 provenance
# metadata 不是 KB 的可篩欄位），所以「只要 official 那批」只能靠抓深再後過濾。
# 2026-09-12 實測同一個查詢：numberOfResults=15 撈到 official 0 筆、=50 撈到 2 筆——
# 抓不夠深，配額席次就會永遠空著。這個值是實測出來的下限，不是猜的。
QUOTA_FETCH_DEPTH = 50
# 明確指定 prefix 的通道（N5 的 retrieve_refs 查判解／函釋）要抓多深。
# 原本是 `top_k * 3`＝15，跟上面同一個問題：判解 19 筆＋函釋 10 筆共 29 份，
# 只佔 KB 2477 筆的 1.2%，抓太淺就會被 2448 筆決定書擠掉席次。
#
# **但要講清楚這一改的效益，免得被讀成比實際更大**（2026-09-12 兩個 session 各自實測、
# 交叉對照後的結論）：15 在實質法題目上**本來就撈得到**判解
# （「行政罰法第7條第1項 故意過失」15 就有 2 筆、分數 0.787–0.789）。
# 改深的實際效益是「同一題從 3 筆變 6 筆」，**不是「從 0 變有」**。
# 真正撈不到的那類（例：寄存送達）是**判解語料本身沒有覆蓋**，
# 深度再深也變不出來——那要補語料，不是調這個常數。
REF_FETCH_DEPTH = 50
OUTCOME_RE = re.compile(r"(駁回|撤銷|不受理)")

# 重排要看幾筆候選。embedding 只負責把對的文件撈進這個池子（recall），
# 排序交給 rerank（precision）。30 是實測時用的值：ordinary-01 的候選池剛好 30 筆，
# 而 rerank 選出來的前五名 embedding 分數只有 0.26–0.36——**排在 embedding 第一的
# 那筆（0.616）根本沒進前五**。池子太小就等於讓 embedding 的爛排序決定結果。
RERANK_CANDIDATES = 30
# 送進 rerank 的截斷長度。查詢是卷證摘要（實測 101–107 字），文件是 chunk 內文。
# 截斷是為了控制延遲與費用，不是品質考量——實測 1500 字已涵蓋決定書的主文與事實段。
RERANK_QUERY_CHARS = 1000
RERANK_DOC_CHARS = 1500

# `retrieve` 的搜尋設定鍵**依 KB 型態而異，兩者互斥**（2026-09-12 對兩個真 KB 各實測）：
#   MANAGED KB           → managedSearchConfiguration
#   VECTOR（S3 Vectors） → vectorSearchConfiguration
# 用錯的那個會直接 ValidationException（「is not supported for managed knowledge bases」）。
# 團隊現在兩種 KB 都有，**所以這裡不寫死**：寫死哪一個都會讓另一半的人整條通道掛掉，
# 而且這個坑已經在 main 上來回改過一次了。改成第一次呼叫時試錯決定，成功的那個按
# kb_id 記著，之後直接用——每個 process 對每個 KB 最多只會浪費一次呼叫。
#
# 刻意**不解析錯誤訊息的文字**：措辭是 AWS 的，哪天改了我們就跟著壞。只認
# 「這個鍵被 ValidationException 拒絕」這件事，換另一個鍵再試一次；兩個都不行就讓
# 例外照原樣冒出去（N4 會把它降級成通道 B 失敗，不會吞掉）。
MANAGED_SEARCH_KEY = "managedSearchConfiguration"
VECTOR_SEARCH_KEY = "vectorSearchConfiguration"
SEARCH_KEYS = (MANAGED_SEARCH_KEY, VECTOR_SEARCH_KEY)
# 「這個鍵不被接受」有**兩種**講法，兩種都要當成「換另一個鍵再試」：
#
#   ValidationException    AWS 伺服器端拒絕（KB 型態不對）
#   ParamValidationError   **botocore 在客戶端就擋下來**——這個版本的 botocore
#                          根本不認識那個鍵，請求連送都沒送出去
#
# **第二種是 2026-09-12 線上實際踩到的**，而且本機測不出來：容器裝的是
# `boto3~=1.35.0`（不認識 `managedSearchConfiguration`），開發機用
# `uv run --with boto3` 抓最新版（認識）。只認第一種的話，試錯的第一把就
# 拋出去、永遠輪不到第二個鍵，整條相似案通道在雲上回 0 筆——
# 而 AC7 紅字看起來像「檢索品質差」，不像「SDK 版本差」。
KEY_REJECTED_EXCEPTIONS = ("ValidationException", "ParamValidationError")
_SEARCH_KEY_CACHE: dict[str, str] = {}



def search_key_for(kb_id: str) -> str | None:
    """這個 KB 已經試出來的搜尋設定鍵；還沒試過回 None。測試與診斷用。"""
    return _SEARCH_KEY_CACHE.get(kb_id)


def reset_search_key_cache() -> None:
    """清掉試錯結果。正式路徑不用，給測試與換 KB 的情境。"""
    _SEARCH_KEY_CACHE.clear()


def doc_kind_filter(kinds: list[str]) -> dict[str, Any] | None:
    """`doc_kind` 的 metadata filter。空 list 回 None（不篩）。

    單值用 `equals`、多值用 `in`——兩者在 MANAGED 與 S3 Vectors 上都實測可用
    （2026-09-12，`orAll` 也可以，但 `in` 最短）。
    """
    if not kinds:
        return None
    if len(kinds) == 1:
        return {"equals": {"key": "doc_kind", "value": kinds[0]}}
    return {"in": {"key": "doc_kind", "value": list(kinds)}}


def retrieve_raw(client: Any, kb_id: str, query: str, want: int,
                 metadata_filter: dict[str, Any] | None = None) -> dict:
    """打一次 KB，自動選對搜尋設定鍵（見上方 SEARCH_KEYS 的說明）。

    刻意做成模組層函式而不是 `KBRetriever` 的私有方法：`scripts/` 底下的量測腳本
    也要打同一個 KB，而它們**不能**各自再寫一份鍵名——那正是這個坑的成因。
    """
    cached = _SEARCH_KEY_CACHE.get(kb_id)
    # 已知的排前面，其餘仍留作退路：快取錯一次不會永遠錯。
    order = [cached] + [k for k in SEARCH_KEYS if k != cached] if cached else list(SEARCH_KEYS)
    last_exc: Exception | None = None
    for i, key in enumerate(order):
        if i:
            # 試錯的重試也算一次呼叫，照樣壓在 1 RPS 以下（賽方規範）
            time.sleep(RETRIEVE_INTERVAL_S)
        try:
            resp = client.retrieve(
                knowledgeBaseId=kb_id,
                retrievalQuery={"text": query},
                retrievalConfiguration={key: {"numberOfResults": want,
                                              **({"filter": metadata_filter} if metadata_filter else {})}},
            )
        except Exception as e:  # noqa: BLE001 — 例外型別由 botocore 動態生成
            if type(e).__name__ not in KEY_REJECTED_EXCEPTIONS:
                raise      # throttle、權限、網路問題不在這裡處理，直接往上丟
            last_exc = e
            continue
        _SEARCH_KEY_CACHE[kb_id] = key
        return resp
    raise last_exc  # type: ignore[misc]  兩個鍵都被拒，讓原始例外照原樣冒出去


# 通道自檢的探針查詢。**刻意用五句涵蓋不同類別的查詢**，而不是「每個前綴配一句」：
# 後者要求寫探針的人事先知道哪句話一定命中哪個目錄，猜錯就會把「查詢不相關」
# 誤報成「前綴設錯」。這裡反過來——不預設任何一句該命中誰，只把五次查詢回來的
# 目錄全部收集起來，再跟設定對照。誰都沒撈到的那句話不會害到任何人。
CHECK_PROBES = (
    "訴願駁回 原處分撤銷 訴願人主張",
    "行政函釋 法規適用疑義 釋示",
    "司法院釋字 行政法院判決 法律見解",
    "訴願期間 送達 不受理 程序",
    "空氣污染 廢棄物清理 裁處罰鍰",
)


def _top_dir(rel: str) -> str:
    """相對路徑的第一層目錄，帶尾斜線（設定裡的前綴就是這個形狀）。"""
    head = rel.split("/", 1)[0]
    return f"{head}/" if head else ""


def check_channels(client: Any, kb_id: str, *, probes: tuple[str, ...] = CHECK_PROBES,
                   want: int = 50) -> dict[str, Any]:
    """打幾次 KB，回報「設定要的前綴／doc_kind」與「KB 裡實際有的」對不對得上。

    **為什麼需要這個**：前綴比對是後過濾，設錯一個字就整條通道回 0 筆而且不報錯
    （`_relative_path` 的說明記了同一類問題咬過我們一次）。`DEFAULT_SIMILAR_CASE_QUOTA`
    寫的是 `新北訴願決定書_全量/`，而第三方 corpus 的目錄叫 `新北訴願決定書_環保局全量/`
    ——忘了設 `.env` 的人拿到的不是錯誤訊息，是安靜的「相似案：無」。

    **不做斷言，只做對照。** 回傳裡不判「對／錯」，而是把兩邊的名字擺在一起、
    並對撈不到的前綴給出最接近的實際目錄名。取樣命中不到不代表設定錯（也可能是
    這五句查詢剛好不碰那類文件），把判斷留給看的人比替他決定有用。

    `doc_kind` 分三態，因為 `_retrieve` 的防呆（篩空就退回不篩）會把兩種不同的
    失敗壓成同一個表現——值打錯與這個 KB 根本沒有側檔，都是安靜退回舊行為：

    - `no_sidecar`：取樣裡沒有任何一筆帶 `doc_kind` → 這個 KB 不能開 `REF_DOC_KINDS`
    - `missing`：別的值有、要的值沒有 → 值打錯，或該類文件不在這個 KB
    - `ok`：取樣裡出現過

    **不掛在 N4 的每案路徑上**：每個探針一次 retrieve，1 RPS 下五次就是五秒多，
    而相似案通道端到端已經 5.8 秒、硬上限 90 秒。這是換 corpus／換 KB 時跑一次的
    檢查，不是每件案子都要付的成本。
    """
    seen_dirs: dict[str, int] = {}
    seen_kinds: dict[str, int] = {}
    sampled = 0
    for i, q in enumerate(probes):
        if i:
            time.sleep(RETRIEVE_INTERVAL_S)
        for r in retrieve_raw(client, kb_id, q, want).get("retrievalResults", []):
            md = r.get("metadata") or {}
            uri = md.get("_source_uri") or ((r.get("location") or {}).get("s3Location") or {}).get("uri", "")
            _, rel = _relative_path(uri)
            d = _top_dir(rel)
            if d:
                seen_dirs[d] = seen_dirs.get(d, 0) + 1
            kind = md.get("doc_kind")
            if kind:
                seen_kinds[str(kind)] = seen_kinds.get(str(kind), 0) + 1
            sampled += 1

    quota = settings.similar_case_quota()
    wanted: dict[str, list[str]] = {}
    for pfx in quota:
        wanted.setdefault(pfx, []).append("SIMILAR_CASE_QUOTA")
    for pfx in settings.ref_prefixes():
        wanted.setdefault(pfx, []).append("REF_PREFIXES")

    prefixes = []
    for pfx, sources in wanted.items():
        n = sum(c for d, c in seen_dirs.items() if d == pfx or pfx.startswith(d) or d.startswith(pfx))
        # 撈不到時給最接近的實際目錄名——打錯字的人要的是「那該填什麼」，
        # 不是又一句「找不到」。difflib 的門檻放寬到 0.4：`新北訴願決定書_全量/`
        # 與 `新北訴願決定書_環保局全量/` 這種只差三個字的才是我們要抓的。
        #
        # **但候選池要排除其他也在設定裡的前綴**（2026-09-12 實測誤報）：
        # `REF_PREFIXES` 同時有 `行政函釋/` 與 `行政函釋_全量/`，前者在 KB A 撈不到，
        # 若不排除就會指著後者說「疑似打錯字」——而後者明明也在設定裡、也撈得到。
        # 那不是拼錯，是兩個不同 corpus 的不同目錄，其中一個在這個 KB 不存在。
        # 誤報會讓人不信任這支工具，比漏報更傷。
        pool = [d for d in seen_dirs if d not in (set(wanted) - {pfx})]
        prefixes.append({
            "prefix": pfx, "sources": sources, "sampled_hits": n,
            "closest": (difflib.get_close_matches(pfx, pool, n=1, cutoff=0.4) or [None])[0]
            if n == 0 else None,
        })

    kinds = []
    for k in settings.ref_doc_kinds():
        if not seen_kinds:
            state = "no_sidecar"
        elif k in seen_kinds:
            state = "ok"
        else:
            state = "missing"
        kinds.append({"doc_kind": k, "state": state, "sampled_hits": seen_kinds.get(k, 0),
                      "closest": (difflib.get_close_matches(k, list(seen_kinds), n=1, cutoff=0.4)
                                  or [None])[0] if state == "missing" else None})

    return {
        "search_key": search_key_for(kb_id), "sampled": sampled, "probes": len(probes),
        "kb_dirs": dict(sorted(seen_dirs.items(), key=lambda x: -x[1])),
        "kb_doc_kinds": dict(sorted(seen_kinds.items(), key=lambda x: -x[1])),
        "prefixes": prefixes, "doc_kinds": kinds,
    }


def _relative_path(uri: str) -> tuple[str, str]:
    """把各種 S3 URI 形式化約成 ("official"|"public", 相對路徑)。

    三種都要認，因為**不同 KB 型態回不同形式**（2026-09-12 實測）：

    - `s3://bucket/kb/official/歷史訴願決定書/113年/x.txt`（自管 vector KB）
    - `https://bucket.s3.us-west-2.amazonaws.com/kb/official/%E6%AD%B7...`（Managed KB，virtual-host）
    - `https://s3.us-west-2.amazonaws.com/bucket/kb/official/…`（path-style）

    只認 `s3://` 的話，https 那兩種會整串留著當相對路徑，前綴一律比不中，
    命中被後過濾**靜默刷成 0 筆**——回空 list、不報錯，比報錯更難查。
    """
    path = urllib.parse.unquote(uri)
    if path.startswith("s3://"):
        path = path.split("/", 3)[-1]                      # 去掉 s3://bucket/
    elif path.startswith(("http://", "https://")):
        rest = path.split("://", 1)[1]
        host, _, tail = rest.partition("/")
        # virtual-host（bucket 在 host 裡）→ tail 就是 key；
        # path-style（host 以 s3 開頭）→ tail 的第一段是 bucket 名，要再剝一層。
        path = tail.split("/", 1)[-1] if host.split(".", 1)[0] == "s3" else tail
    path = path.replace(" 的副本", "")
    m = re.match(r"^kb/(official|public)/(.+)$", path)
    if m:
        return m.group(1), m.group(2)
    return "unknown", path


def _provenance(kind: str) -> str:
    return {"official": "official", "public": "public_crawl"}.get(kind, "unknown")


#: 母庫查一次抓多深。使用者自己搜法規／決定書時**沒有配額也沒有重排**，
#: 只靠 server-side `doc_kind` filter 把類別框住，所以不需要 `QUOTA_FETCH_DEPTH`
#: 那種深度；但同一份文件會被切多個 chunk（實測：filter 後 10 筆只有 5 份文件），
#: 去重會吃掉席次，所以抓 `limit` 的三倍再去重。
CORPUS_FETCH_MULTIPLIER = 3


def _dedupe_key(s3_key: str, metadata: dict[str, Any]) -> str:
    """母庫查的去重鍵：**有案號就用案號，沒有才退回 S3 key**。

    案號是「這是哪一件」的識別，S3 key 是「這份檔案放在哪」。同一件決定書在庫裡有
    兩個 key（舊爬蟲批與第三方整理批並存），按 key 去重收不掉，清單上就會同一件出現兩次。

    退回 key 的情況是**法規**：它們沒有案號，而兩部不同法規本來就該各佔一列。
    判解有 `case_no`（裁判字號），同一份判決的重複收錄一樣該收成一筆。

    空字串與 None 都當成「沒有案號」——側檔欄位推不出來時留空是常態（見
    `scripts/build_kb_metadata_from_keys.attributes_for`），拿空字串當鍵會把所有
    沒案號的文件併成一筆。
    """
    case_no = (metadata.get("case_no") or "").strip()
    return f"case:{case_no}" if case_no else s3_key


def search_corpus(client: Any, kb_id: str, query: str, doc_kinds: list[str], *,
                  limit: int = 10, min_score: float = 0.0) -> list[dict[str, Any]]:
    """母庫查：使用者在「搜尋並加入」對話框裡查法規／訴願決定（契約 v2 §4.2、§4.3）。

    **這是一條獨立通道，不是 `KBRetriever._retrieve` 的變形**，兩者服務的對象不同：

    - `_retrieve` 服務 N4 相似案與 N5 判解——有前綴白名單、兩批配額、重排、
      `exclude_case`（案子不能是自己的相似案）。那套語意套到「使用者手動搜法規」
      上全都是錯的：使用者要的就是那一部法規，沒有「本案要排除誰」。
    - 這裡只做三件事：server-side `doc_kind` filter、來源去重、截 `limit`。

    **兩個刻意的差異，都不是漏看：**

    1. **不退回不篩。** `_retrieve` 有「篩了全空就退回不篩再試」的防呆，那在 N4 是對的
       （寧可回相關性低的，也不要靜默 0 筆）。母庫查**不能**退：退了就會把法院裁判書
       當成法規端給承辦人。2026-09-12 實測「廢棄物」不篩抓 20 筆，法規佔 **0 筆**
       （15 筆裁判書 + 5 筆決定書）——filter 是必要條件，不是優化。查無就回空。
    2. **去重一律開，而且是按「文件」去重不是按 key。** `dedupe_by_source` 在
       `_retrieve` 預設 False 是為了不動已驗證的相似案通道；這裡沒有那個包袱。
       2026-09-12 實測同一查詢 filter 後 10 筆只有 **5 份**不同法規——
       不去重的話「命中 10 筆」會把讀的人騙成 10 部法律。

       **2026-09-13：去重鍵從 S3 key 改成「案號優先」**（`_dedupe_key`）。原因是同一件
       決定書在 S3 上有兩份：舊爬蟲批 `新北訴願決定書_全量/`（2,347）與第三方整理的
       `新北訴願決定書_環保局全量/`（8,486），其中 **2,331 個案號是同一件**，兩批並存、
       舊批沒刪。key 不同、案號相同，按 key 去重收不掉——承辦人會在同一張清單上看到
       同一件決定書兩次，而那正是這段 docstring 上一句在防的事（「命中 10 筆」騙成
       10 份不同文件），只是換了一個維度發生。

       **只影響母庫查這一條通道。** N4 相似案走的是 `KBRetriever.search`，那裡刻意不
       去重（`test_similar_case_channel_is_deliberately_left_undeduped` 釘著，改它要
       重驗配額分布）。兩條通道的去重語意本來就不同，這裡改不會動到那邊。

    `id` 是**完整的 S3 key**（`kb/{official|public}/{rel}`），不是 `_relative_path` 的 `rel`：
    `rel` 分不出 official 與 public 兩批，而且沒辦法直接餵 `s3.get_object` 讀全文。
    """
    want = max(limit * CORPUS_FETCH_MULTIPLIER, limit)
    resp = retrieve_raw(client, kb_id, query, want, doc_kind_filter(doc_kinds))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in resp.get("retrievalResults", []):
        score = float(r.get("score", 0.0))
        if score < min_score:
            continue
        md = r.get("metadata") or {}
        uri = md.get("_source_uri") or ((r.get("location") or {}).get("s3Location") or {}).get("uri", "")
        kind, rel = _relative_path(uri)
        if not rel:
            continue
        key = f"kb/{kind}/{rel}" if kind in ("official", "public") else rel
        # 結果已按分數遞減，所以「留第一個看到的」＝留最高分那筆。
        dedupe_key = _dedupe_key(key, md)
        if dedupe_key in seen:   # 同一份文件的其他 chunk，或同一件決定書的另一批副本
            continue
        seen.add(dedupe_key)
        fname = rel.rsplit("/", 1)[-1]
        out.append({
            "id": key,
            "t": fname.rsplit(".", 1)[0],
            "src": rel,
            "score": round(score, 3),
            "doc_kind": md.get("doc_kind"),
            "provenance": md.get("provenance") or _provenance(kind),
            "verdict": md.get("outcome"),
            "category": md.get("category"),
        })
        if len(out) >= limit:
            break
    return out


class KBRetriever:
    """Managed KB 相似案／函釋檢索。命中一律 `verified=False`——對回資料集實檔是 N6 的事。"""

    name = "bedrock_kb"

    def __init__(self, kb_id: str, region: str, min_score: float | None = None,
                 exclude_case: str | list[str] | None = None, client: Any = None) -> None:
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

        沒指定 prefix ＝ 相似案通道 → 依 `settings.similar_case_quota()` 分批查再合併。
        """
        filters = filters or {}
        # 排除鍵**可以是多個**（2026-09-12）：上傳案要同時擋掉收文案號與檔名，
        # 兩者不一定一樣。呼叫端沒給就退回建構時的值（合成案在 fixture 手寫那一個）。
        exclude = filters.get("exclude_case") or self.exclude_case
        explicit = list(filters.get("prefix") or [])
        # 有重排時先多留候選給它排；沒有重排就維持原本「撈幾筆回幾筆」的行為。
        want_hits = RERANK_CANDIDATES if settings.rerank_model_id() else top_k
        if explicit:
            # 伺服器端先篩文件種類，確保函釋／判解進得了候選池（see settings.ref_doc_kinds）
            hits = self._retrieve(query, explicit, exclude, want=REF_FETCH_DEPTH,
                                  limit=want_hits, dedupe_by_source=True,
                                  doc_kinds=settings.ref_doc_kinds())
        else:
            hits = self._quota_search(query, exclude, want_hits)
        hits = self._rerank(query, hits, top_k)
        # 編號在排序之後才給：有重排時 `kb-1` 是 rerank 最相關的那筆，
        # 沒有重排時仍是 embedding 分數最高的那筆（`payload.ranked_by` 說得出是哪一種）
        return [dataclasses.replace(h, id=f"kb-{i}") for i, h in enumerate(hits, start=1)]

    def _quota_search(self, query: str, exclude: str | list[str] | None, top_k: int) -> list[Hit]:
        """兩批各自查、各自取配額席次，不足由另一批補滿，最後按分數排序。

        `top_k` 是**這一層要交出幾筆**，不一定等於最後顯示幾筆：有重排時呼叫端會要
        `RERANK_CANDIDATES` 筆當候選池。席次因此要**按比例放大到候選池**——
        設定寫的是「5 席裡官方佔 2」，那 30 筆的候選池就該是「官方佔 12」。
        不放大的話，配額在候選池這一層等於沒作用（2、3 席之後的補位迴圈會把兩批
        剩下的全倒進來，再按 embedding 分數截斷），而 public 的檔數是 official 的
        23 倍、單次查詢前 15 名實測 15/15 都是 public——官方那批會整批擠不進候選池，
        **連被重排看一眼的機會都沒有**。配額當初就是為了防這件事。

        放大只影響「誰進得了候選池」。最終前五名由重排的相關性分數決定，
        **不保留席次**：重排把相關性量準了之後，保席次就等於犧牲相關性
        （這個取捨在模組頂端的配額註解裡本來就寫了「哪天該回頭重議」）。
        `top_k == 總席次` 時 `scale == 1`，行為與沒有重排時逐字相同。
        """
        quota_map = settings.similar_case_quota()
        total_seats = sum(quota_map.values()) or 1
        scale = max(1, -(-top_k // total_seats))     # ceil，讓席次總和撐滿候選池
        batches: list[tuple[int, list[Hit]]] = []
        for i, (prefix, quota) in enumerate(quota_map.items()):
            if i:
                time.sleep(RETRIEVE_INTERVAL_S)
            batches.append(
                (quota * scale,
                 self._retrieve(query, [prefix], exclude, want=QUOTA_FETCH_DEPTH, limit=top_k))
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

    def _rerank(self, query: str, hits: list[Hit], top_k: int) -> list[Hit]:
        """用 cross-encoder 重排並砍掉不相關的。沒設模型就原樣回傳。

        回傳的 `score` 換成 rerank 分數，原本的 embedding 分數移到
        `payload["embedding_score"]`，並以 `payload["ranked_by"]` 標明是誰排的。
        **畫面上顯示的數字必須是真的決定了排序的那個** ——留著 embedding 分數當
        顯示值會讓看板出現「第一名 35 分、第二名 62 分」這種看不懂的順序，
        那是對「為什麼這幾筆排在前面」說謊（CONSTITUTION §1）。

        **同一份文件的多個 chunk 只留最高分那筆**（2026-09-12 補）。這件事在
        embedding-only 的時候是「已知且刻意未改」的小問題：候選池就是 top_k 那幾筆，
        重複頂多吃掉一兩席。加了重排之後量級不一樣了——候選池變成 30 筆，而
        cross-encoder 會把同一份高度相關決定書的**每個 chunk 都打高分**，
        前五名很可能是同一件案子的五個片段。承辦人看到的是「五筆相似案」，
        實際上只有一件，那是對「我們找到幾件」說謊。
        去重按來源檔路徑（`h.source`），與 `_retrieve` 的 `dedupe_by_source` 同一把尺。

        因此 `numberOfResults` 要一次要回**全部候選**的排名，不能只要 top_k：
        只要 5 筆而那 5 筆都是同一份文件的話，去重後就只剩 1 筆，**而且沒有遞補**
        ——那是把重複問題換成了另一種靜默少筆。要回全部再自己截斷，
        成本相同（同一次 API 呼叫），但席次會被真正不同的文件補滿。
        """
        model = settings.rerank_model_id()
        if not model or not hits:
            return hits[:top_k]
        docs = [(h, (h.payload or {}).get("text") or "") for h in hits]
        docs = [(h, t) for h, t in docs if t.strip()]
        if not docs:
            # 沒有可讀的內文就不重排——硬送空字串進去只會得到無意義的分數
            return hits[:top_k]
        time.sleep(RETRIEVE_INTERVAL_S)      # rerank 也是一次 Bedrock 呼叫（賽方 1 RPS）
        resp = self._c().rerank(
            queries=[{"type": "TEXT", "textQuery": {"text": query[:RERANK_QUERY_CHARS]}}],
            sources=[{"type": "INLINE",
                      "inlineDocumentSource": {"type": "TEXT",
                                               "textDocument": {"text": t[:RERANK_DOC_CHARS]}}}
                     for _, t in docs],
            rerankingConfiguration={
                "type": "BEDROCK_RERANKING_MODEL",
                "bedrockRerankingConfiguration": {
                    # 要回全部候選的排名（見 docstring：去重要有東西可以遞補）。
                    # numberOfResults 不得超過來源數，否則 ValidationException——
                    # 這裡剛好就是來源數，上限自然滿足。
                    "numberOfResults": len(docs),
                    "modelConfiguration": {"modelArn": model},
                },
            },
        )
        floor = settings.rerank_min_score()
        out: list[Hit] = []
        seen_sources: set[str] = set()
        for r in resp.get("results", []):
            if len(out) >= top_k:
                break
            rs = float(r.get("relevanceScore", 0.0))
            if rs < floor:
                continue            # 撈到了但不相關——寧可少一筆，不要塞
            h, _ = docs[int(r["index"])]
            # 同一份文件的其他 chunk（結果已按相關性遞減，第一次遇到的就是最高分那筆）
            if h.source in seen_sources:
                continue
            seen_sources.add(h.source)
            payload = dict(h.payload or {})
            payload["embedding_score"] = h.score
            payload["rerank_score"] = rs
            payload["ranked_by"] = "rerank"
            out.append(dataclasses.replace(h, score=rs, payload=payload))
        return out

    def _retrieve_raw(self, query: str, want: int,
                      metadata_filter: dict[str, Any] | None = None) -> dict:
        return retrieve_raw(self._c(), self.kb_id, query, want, metadata_filter=metadata_filter)

    def _retrieve(self, query: str, prefixes: list[str], exclude: str | list[str] | None, *,
                  want: int, limit: int, dedupe_by_source: bool = False,
                  doc_kinds: list[str] | None = None) -> list[Hit]:
        """打一次 KB 並做後過濾，回傳最多 limit 筆（`id` 是佔位值，由呼叫端重編）。

        `dedupe_by_source` **預設 False，只有判解／函釋通道開它**（2026-09-12）。
        去重本身對兩條通道都有意義，但相似案通道的「official 2 ＋ public_crawl 3」
        是當天實跑驗證過的行為，決賽期間不為了一個一般性的改善去動已驗證的路徑。
        相似案通道的 chunk 重複**在有重排時由 `_rerank` 收掉**（那裡候選池 30 筆，
        重複的量級跟這裡不一樣，見該處說明）；沒有重排時仍維持已驗證的舊行為，
        是**已知且刻意未改**，不是漏看。
        """
        # 單一字串或字串清單都收：來源可能是 fixture 的一個值，也可能是 N4 依
        # 收文案號＋檔名算出來的兩三個。
        exclude_keys = [exclude] if isinstance(exclude, str) else list(exclude or [])
        flt = doc_kind_filter(doc_kinds or [])
        resp = self._retrieve_raw(query, want, flt)
        if flt and not resp.get("retrievalResults"):
            # 防呆：這個 KB 的文件可能根本沒有 `doc_kind` 側檔，篩了就全空。
            # 靜默回 0 筆比報錯難查（今天已經被同一類問題咬過一次），所以退回不篩再試。
            time.sleep(RETRIEVE_INTERVAL_S)
            resp = self._retrieve_raw(query, want)
        hits: list[Hit] = []
        # 同一份文件會被切成多個 chunk，各自以不同分數回來（2026-09-12 兩個 session
        # 各自實測：判解查詢命中 8 筆其實只有 6 份、命中 5 筆其實只有 2 份）。
        # 不去重的話「命中 N 筆」會把讀的人騙成 N 份不同的判解，
        # 而且重複的 chunk 會把 `limit` 的席次吃光，把真正不同的第二、三份擠掉。
        # **必須在 limit 截斷之前去重**，不能等回傳後再處理。
        # 去重按來源檔路徑（`rel`），不是按分數或 chunk id——同一份判決的不同 chunk
        # 分數本來就不同，按分數去重等於沒去重。KB 的結果已按分數遞減，
        # 所以第一次遇到的那個 chunk 就是該文件的最高分，保留它即可。
        seen_sources: set[str] = set()
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
            if any(x and x in rel for x in exclude_keys):
                continue
            if dedupe_by_source:  # 同一份文件的其他 chunk，丟掉（見上方 seen_sources 說明）
                if rel in seen_sources:
                    continue
                seen_sources.add(rel)
            fname = rel.rsplit("/", 1)[-1]
            md_kind = (r.get("metadata") or {}).get("doc_kind")
            # **只有決定書才從檔名推結果。** 決定書的檔名帶自己的主文（`1141050994_駁回`、
            # `…-訴願逾期-不受理`），抓得對；但函釋的檔名是**別人問了什麼**，不是本署答了什麼
            # ——「…至撤銷查封時，始歸消滅」會被抓出 `outcome=撤銷`，而那個函釋本身沒有結果，
            # 它只是在討論撤銷。法規名同理（「訴願決定撤銷原行政處分案件處理及管制作業要點」）。
            # 2026-09-12 清點語料：函釋 16/4530、法規 5/679 會誤抓，判解 0/3185。
            # 數量不多，但性質是**無中生有一個法律結果掛到不該有結果的文件上**（CONSTITUTION §1）。
            # `doc_kind` 讀不到時（沒有側檔的 KB）維持舊行為——寧可保留退路，不要靜默少一個欄位。
            m = OUTCOME_RE.search(fname) if md_kind in (None, "decision") else None
            text = re.sub(r"\s+", " ", ((r.get("content") or {}).get("text") or "")).strip()
            md = r.get("metadata") or {}
            # 側檔（`x.txt.metadata.json`，scripts/build_kb_metadata.py 產）優先，檔名是退路。
            # **案型沒有退路**：公開爬蟲那批的檔名是 `案號_結果`，案型不在裡面，
            # 側檔缺席就誠實留 None——不從內文猜，猜錯就是把不同案型的案子當相似案推給承辦人。
            hits.append(
                Hit(
                    id=f"kb-{len(hits) + 1}",
                    title=fname.rsplit(".", 1)[0],
                    score=round(score, 3),
                    source=rel,
                    origin="retrieval",
                    verified=False,
                    note="",
                    # outcome 照側檔／檔名，不由模型推測（CONSTITUTION §2）
                    payload={"outcome": md.get("outcome") or (m.group(1) if m else None),
                             "provenance": md.get("provenance") or _provenance(kind),
                             "category": md.get("category") or None,
                             "year": md.get("year") or None,
                             # `source` 是 `kb/<kind>/` **底下**的相對路徑，`kind` 到這裡
                             # 就被丟掉了——於是拿一筆 hit 回頭讀 S3 全文時，key 少一段。
                             # 猜（先試 official 再試 public）會在猜錯時讀到另一份文件，
                             # 所以把 kind 帶著走（2026-09-13 補，聊天歸檔要用）。
                             "kb_kind": kind,
                             "text": text},
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def meta(self) -> dict[str, Any]:
        return {"backend": self.name, "available": True, "min_score": self.min_score,
                "kb_id_set": bool(self.kb_id)}


def build_retriever(kind: str, *, exclude_case: str | list[str] | None = None) -> KBRetriever | None:
    """編排層用：依 RETRIEVER 建相似案檢索器。lawtable_only → None（維持 Phase 0 行為）。"""
    if kind != "kb":
        return None
    missing = [n for n, v in (("BEDROCK_KB_ID", settings.kb_id()),
                              ("AWS_REGION", settings.aws_region())) if not v]
    if missing:
        raise ValueError(f"RETRIEVER=kb 需要環境變數 {missing}（見 .env.example）")
    return KBRetriever(kb_id=settings.kb_id(), region=settings.aws_region(), exclude_case=exclude_case)


def describe_similar_case_backend(kind: str) -> str:
    """健康檢查用：相似案通道現在到底是什麼狀態。**不打任何 API。**

    三種答案各自代表不同的事，不可互相代替：

    - `unavailable`：設定就是不查 KB（`RETRIEVER=lawtable_only`），這是刻意的
    - `bedrock_kb`：通道開著，檢索器建得起來
    - `misconfigured：…`：**想開但開不成**——`RETRIEVER=kb` 卻缺環境變數。
      這種情況回 `unavailable` 等於把設定錯誤說成「本來就沒要開」，
      看板上分不出「沒設定」與「設錯了」，而後者是要有人去修的。

    `build_retriever()` 只驗環境變數並建物件，boto3 client 要到 `_c()` 才生出來，
    所以這裡不會產生任何 AWS 呼叫，健康檢查可以放心每次都問。
    """
    if kind != "kb":
        return "unavailable"
    try:
        r = build_retriever(kind)
    except ValueError as e:
        return f"misconfigured：{e}"
    return r.name if r else "unavailable"
