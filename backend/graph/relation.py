"""案件關聯圖：把一次 run 的 payload 攤成「卷證 → 事實 → 爭點 → 法規依據 → 結論」五欄。

契約形狀見 `docs/handoff/2026-09-12-frontend-contract-v2.md` §3.7，
計畫見 `plans/2026-09-12-relation-graph.md`。

**零 LLM**（CONSTITUTION §4）：全部由 payload 既有欄位的字串比對推出。
本檔只 import stdlib，不 import `backend.llm`、不碰 runstore、不碰 orchestrator。

**不編造**（CONSTITUTION §2）：每條邊帶 `basis`，寫明它是從哪個欄位推出來的。
推不出來就不畫——而且「推不出來」本身要出現在 `flagged` / `unlinked`，不是靜默丟掉。
**沒有「矛盾」這種邊**：後端沒有任何偵測矛盾的機制，畫了就是編。設計稿的第三種
線型改用 `cite` 邊的 `state` 與 `lamp`，那是真的。

## 兩件實查推翻的事（2026-09-13，掃過 9421 份不重複的 VERIFIED run）

**① `cite` 邊必須自己重算，不能複用 `ss[].refs` 裡的 `L*`。**
`_attach_law_refs()`（`backend/orchestrator/graph.py:621`）確實會把 `L*` 插進
`ss[].refs`，但那是 `build_payload()` 內的就地變更，而 `backend/output/runs/*.json`
存的是 `CaseState.as_dict()`（`backend/orchestrator/runstore.py:35`）。
實測 31,739 句裡 **0 句**的 `refs` 含 `L*`，其中 9,383 句明明帶著對得上的
`citations[]`。所以這裡用同一把尺自己 join，尺不變、只是換個地方量。

**② join key 用錯會靜默得到空圖。** 同一份 run：

    citations[].resolved_id = "L-訴願法-14"   格式 L-{法名}-{條號}
    laws[].gate_ref_key     = "訴願法|77"     格式 {法名}|{條號}
    resolved_id ↔ gate_ref_key  →  0/4 命中   ← 看起來該用這個，實際全空
    raw         ↔ laws[].t      →  3/4 命中   ← 正確

用 `raw` ↔ `t`，**字串相等，不做模糊比對**（與 `_attach_law_refs` 的註解同一個理由：
序號或格式巧合會在文字一變時靜默失效而不報錯）。

## 輸入有兩種形狀，兩種都吃

`build_payload()` 的輸出是**扁平**的（頂層 `doc` / `citations` / `laws` / `cases`），
runstore 存的是**巢狀**的（`gate.doc` / `gate.citations` / `retrieval.laws`）。
取值一律「先扁平、後巢狀」。

`facts_excerpt` 與 `screen.fact_issues` 兩種形狀相同，所以照原路徑取。
**刻意不用扁平 payload 的頂層 `issues[]`**：`_issues_view()`（`graph.py:540`）
把 `matched_keywords` 丟掉了，改吃它的話 `trigger` 邊會整批無聲消失。
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys
from typing import Any

#: 五欄的欄名與欄序。`c` 就是這個 tuple 的 index，前端照它排欄。
COLS = ("卷證", "事實", "爭點", "法規依據", "結論")

COL_DOC, COL_FACT, COL_ISSUE, COL_LAW, COL_OUT = range(5)

#: 節點文字的長度上限（CONSTITUTION §6 資料隔離 / plan AC8）。
#: `facts_excerpt[].text` 是**卷證原文**，含當事人姓名與住居所；關聯圖是拿來看
#: 「線怎麼連」的，不是拿來讀卷的——要讀原文請開卷證。所以這裡一律截斷，
#: 節點只留「認得出是哪一段」的長度。
FACT_TITLE_MAX = 40
FACT_DETAIL_MAX = 120
OUT_TITLE_MAX = 60
ISSUE_DETAIL_MAX = 120

#: 截斷記號。用全形省略號，跟畫面上其他截斷一致。
ELLIPSIS = "…"

#: 高風險識別資訊的樣式。**這三種 regex 抓得準，所以在這裡抓**；
#: 完整地址的去識別化不是 regex 做得到的事（「本市土城區」與「土城區○○路 12 號」
#: 沒有一條可靠的界線），**主要防線是上面的截斷，不是這張表**——
#: 把這張表講成「已完成去識別化」就是把截斷的功勞算到 regex 頭上。
#:
#: 門牌那條**刻意要求前面有路／街／巷／弄／段**：裸的 `\d+號` 會把
#: 「釋字第469號」「北環稽字第1130012345號」一起抹掉，而那正是這張圖存在的理由
#: （引用必可驗，CONSTITUTION §2）。**寧可漏抹一個沒有路名的門牌，
#: 也不要把一個可查證的字號變成 `○號`。**
_REDACTIONS = (
    (re.compile(r"[A-Z][12]\d{8}"), "○○○○○○○○○○"),                      # 身分證字號
    (re.compile(r"[A-Z]{2,3}-\d{3,4}|\d{3,4}-[A-Z]{2,3}"), "○○-○○○○"),   # 車牌
    (re.compile(r"(?<=[路街巷弄段])[^，。；、\s]{0,8}?\d+號(?:之\d+)?(?:\d+樓)?(?:之\d+)?"),
     "○○號"),                                                            # 門牌
)


def _redact(text: str) -> str:
    """抹掉身分證字號／車牌／門牌。**不是完整的去識別化**，見 `_REDACTIONS` 的說明。"""
    for pattern, mask in _REDACTIONS:
        text = pattern.sub(mask, text)
    return text


def _clip(text: Any, limit: int) -> str:
    """截到上限並抹掉高風險樣式。**先抹再截**——先截的話被截掉的那半就沒抹到，
    而它會原封不動留在別的欄位裡（例：`d` 截得比 `t` 長）。"""
    s = _redact(str(text or "").strip())
    return s if len(s) <= limit else s[:limit] + ELLIPSIS


def _pick(payload: dict[str, Any], flat: str, nested: tuple[str, str]) -> Any:
    """扁平優先、巢狀回退。

    **不能寫成 `payload.get(flat) or payload[a][b]`**：扁平 payload 裡一個空的
    `doc` 是「這個 run 沒有草稿」，回退到巢狀會拿到另一份 run 的殘留嗎？不會，
    但它會讓「鍵存在但為空」與「鍵不存在」變成同一件事，而這兩件事在
    `status:"empty"` 的判斷上意義不同。所以用 `in` 判斷鍵在不在。
    """
    if flat in payload:
        return payload.get(flat)
    a, b = nested
    return (payload.get(a) or {}).get(b)


def _sections(doc: list[dict[str, Any]]) -> dict[str, tuple[str, int]]:
    """每個句子 id → (所屬段落名, 段落內第幾句)，給 `out` 節點的 `d` 用。

    段落名取自前一個 `ty == "h"` 的區塊文字（實測值：事實／理由／期間計算／決定主文）。
    沒有標題就寫「未標名」，**不猜一個**。
    """
    out: dict[str, tuple[str, int]] = {}
    heading = "未標名"
    for block in doc or []:
        if block.get("ty") == "h" and str(block.get("text") or "").strip():
            heading = str(block["text"]).strip()
            continue
        for n, s in enumerate(block.get("ss") or [], start=1):
            if s.get("id"):
                out[s["id"]] = (heading, n)
    return out


#: 期間計算句 `ss[].basis` 裡的法條寫法：`訴願法 14 I`、`民法 120 II`、
#: `行政程序法 72`、`訴願法 17 → 民法 122`。**跟草稿內文的引用不是同一種格式**
#: ——這是引擎寫的，沒有「第…條」那三個字，項次用羅馬數字且不進 key。
#:
#: `{1,9}法` 不能寫成 `{2,10}法`：後者要求「法」前面至少兩個字，
#: **「民法」就永遠匹配不到**（我第一版量測就是這樣把 22 條邊漏掉的）。
_BASIS_ARTICLE = re.compile(r"([\u4e00-\u9fff]{1,9}法)\s*第?\s*(\d+)\s*條?(?:\s*之\s*(\d+))?")


def _basis_law_keys(basis: Any) -> list[str]:
    """把一句期間計算的 `basis` 解成 `laws[].gate_ref_key` 同格式的鍵（`訴願法|14`）。

    **只解析，不查證**——解出來的鍵之後一律要在 `laws[]` 裡找得到才畫（見
    `build_relation_graph` 那段）。解不出來就回空清單，不猜。

    刻意解不出來的兩種，**它們不是漏掉的**（plan §2「推不出來就不畫」）：
    - `以上各步`：最後一步是前面幾步的結論，本來就沒有法條依據。
    - `最高行 108 判 531 意旨`：判例不是法條，`laws[]` 不收判例。
      同一句的 `行政程序法 74` 照樣解得出來，這是**部分解析**，不是失敗。
    """
    keys: list[str] = []
    for m in _BASIS_ARTICLE.finditer(str(basis or "")):
        article = m.group(2) + (f"之{m.group(3)}" if m.group(3) else "")
        key = f"{m.group(1)}|{article}"
        if key not in keys:
            keys.append(key)
    return keys


def _verify_of(item: dict[str, Any], origin: str) -> str:
    """節點的驗證三態。**布林不夠用**：「查證過是假的」與「沒查證」是兩件事。

    **`origin` 不是裝飾，兩種東西的 `verified=False` 意思相反**：

    - 法規（`retrieval`）：`verified` 是「條號在 `laws-snapshot.json` 查得到嗎」
      （`n4_retrieval.py:6`）。`False` ＝ 查了、查不到 ＝ `suspect`。
    - 相似案（`similar_case`）：KB 命中**一律** `verified=False`，因為「對回資料集
      實檔是 N6 的事」（`backend/retrieval/kb.py:392`、`:626`）。`False` ＝ **還沒查**
      ＝ `unverifiable`。照法規那把尺讀，畫面上每一件相似訴願決定都會被標成可疑——
      那是一句假話，而且是這支程式自己講的，不是資料講的。
    """
    verified = item.get("verified")
    if verified is True:
        return "ok"
    if verified is False and origin != "similar_case":
        return "suspect"
    return "unverifiable"


def build_relation_graph(payload: dict[str, Any]) -> dict[str, Any]:
    """把一份 run payload 組成關聯圖。純函式：同輸入必同輸出（除了 `generated`）。

    回傳契約 §3.7 的形狀，另加三個欄位：

    - `status`：`"ok"` ／ `"empty"`。**一律存在**，前端不必用「nodes 是不是空的」猜。
    - `note`：`status == "empty"` 時說明為什麼；`"ok"` 時為空字串。
    - `flagged`：草稿引用了、但檢索結果裡查無的法條。**這是紅線不是功能**——
      「AI 引了一條我們沒檢索到的法條」正是承辦人最需要知道的事。
    - `unlinked.laws` ／ `unlinked.cases`：查到但沒被引用的**法規**與**相似訴願決定**，
      **分兩鍵**。2026-09-13 之前兩者合在 `laws` 一鍵裡，`note` 於是講出
      「9 條檢索到的法規沒有被引用」——其中 5 條是相似案。**這是假話，不是措辭問題**：
      承辦人會照它去找五條不存在的法規。
    """
    facts = list(payload.get("facts_excerpt") or [])
    issues = list((payload.get("screen") or {}).get("fact_issues") or [])
    laws = list(_pick(payload, "laws", ("retrieval", "laws")) or [])
    cases = list(_pick(payload, "cases", ("retrieval", "cases")) or [])
    doc = list(_pick(payload, "doc", ("gate", "doc")) or [])
    citations = list(_pick(payload, "citations", ("gate", "citations")) or [])
    issue_refs = list(_pick(payload, "issue_refs", ("gate", "issue_refs")) or [])
    files = list(payload.get("files") or [])

    generated = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))
    ).isoformat(timespec="seconds")
    base: dict[str, Any] = {
        "run_id": payload.get("run_id"),
        "generated": generated,
        "status": "ok",
        "note": "",
        "cols": list(COLS),
        "nodes": [],
        "edges": [],
        "flagged": [],
        "unlinked": {"laws": [], "cases": [], "issues": [], "note": ""},
    }

    sentences = [s for block in doc for s in (block.get("ss") or []) if s.get("id")]

    # ── 退化：沒有草稿就畫不出完整關聯 ───────────────────────────────
    # **不拋例外、不回半張圖假裝完整**（plan AC7）。只有程序審查的 run
    # （`to_node="n3"`）走到這裡，`gate` 整個是空的。
    if not sentences:
        base["status"] = "empty"
        base["note"] = (
            "要先生成草稿才畫得出完整關聯：這次 run 只跑到程序審查，"
            "沒有結論句，也就沒有『爭點→結論→法規』那三段。"
        )
        base["stats"] = {
            "nodes": 0,
            "edges": 0,
            "edges_flagged": 0,
            "sentences_total": 0,
            "sentences_in_graph": 0,
        }
        return base

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    flagged: list[dict[str, Any]] = []

    # ── 卷證節點與 quote 邊 ──────────────────────────────────────────
    # 同一份卷證多段摘錄只出一個節點；`quote_ref` 的格式是 `檔名#頁`。
    file_notes = {str(f.get("n") or ""): str(f.get("x") or "") for f in files}
    doc_ids: dict[str, str] = {}
    for n, fact in enumerate(facts, start=1):
        quote_ref = str(fact.get("quote_ref") or "")
        fname = quote_ref.split("#", 1)[0]
        fid = f"F{n}"
        if fname and fname not in doc_ids:
            did = f"D{len(doc_ids) + 1}"
            doc_ids[fname] = did
            nodes.append({
                "id": did, "k": "doc", "c": COL_DOC, "t": fname,
                "s": file_notes.get(fname, ""), "src": "卷證檔案", "origin": "record",
            })
        nodes.append({
            "id": fid, "k": "fact", "c": COL_FACT,
            "t": _clip(fact.get("text"), FACT_TITLE_MAX),
            "d": _clip(fact.get("text"), FACT_DETAIL_MAX),
            "src": quote_ref, "origin": "record",
        })
        if fname:
            edges.append({
                "from": doc_ids[fname], "to": fid, "rel": "quote",
                "basis": "facts_excerpt[].quote_ref",
            })

    # ── 爭點節點與 trigger 邊 ────────────────────────────────────────
    # 尺與 N3 相同（純字串包含，`n3_procedure.py:619`），但 **haystack 比 N3 窄**：
    # N3 比對的是 `digest + intake.note`，而 `digest` 是所有 `facts_excerpt[].text`
    # 串接再加 `intake.note`（`graph.py:128-137`）。只命中 `intake.note` 的爭點
    # **沒有可歸屬的事實節點**——那種爭點進 `unlinked.issues`，不畫一條看起來
    # 合理的線。這是刻意收窄，不是漏掉。
    issue_ids: set[str] = set()
    triggered: set[str] = set()
    for issue in issues:
        iid = issue.get("id")
        if not iid:
            continue
        issue_ids.add(iid)
        nodes.append({
            "id": iid, "k": "issue", "c": COL_ISSUE,
            "t": issue.get("t") or iid,
            "d": _clip(issue.get("q"), ISSUE_DETAIL_MAX),
            "severity": issue.get("severity"),
            "src": issue.get("src") or "", "origin": issue.get("origin") or "rule",
        })
        keywords = [k for k in (issue.get("matched_keywords") or []) if k]
        for n, fact in enumerate(facts, start=1):
            text = str(fact.get("text") or "")
            hit = [k for k in keywords if k in text]
            if hit:
                triggered.add(iid)
                edges.append({
                    "from": f"F{n}", "to": iid, "rel": "trigger",
                    "basis": "fact_issues[].matched_keywords", "detail": hit,
                })

    # ── 法規／案例節點 ───────────────────────────────────────────────
    # 相似案與法規同一欄（契約 §3.7 的 `cols` 只有五欄）。`k` 都是 `law`，
    # 但 `origin` 分得開（`retrieval` vs `similar_case`），前端要分色分得出來。
    #
    # **兩種東西同一欄，但不進同一個集合。** 合起來數、再用其中一種的名字稱呼它，
    # 就會得出「9 條檢索到的法規沒有被引用」這種話——而其中 5 條是相似訴願決定，
    # 不是法規（2026-09-13 QA 在雲上實打抓到）。分得開的依據本來就在資料裡
    # （`origin`），不需要新的判斷邏輯，也不需要猜。
    #
    # ⚠️ **`k` 仍然是 `"law"`，相似案也是——這是刻意留著的，不要「順手修好」**
    # （2026-09-13 Ci 拍板）。它跟上面那句假話**性質不同**：`k` 不是端到承辦人
    # 面前的一句話，只是前端拿來決定節點形狀的鍵，而要分色分類的地方前端讀的是
    # `origin`（`RelationGraph.vue`）。改 `k` 要同時動契約 §3.7 的節點定義與前端，
    # 換來的只是一個好看的名字。
    #
    # **會想修它是很自然的反應，而那正是危險的地方**：今晚這個專案已經有過一次
    # 教訓——把某個名詞機械地全部替換，會把一句本來正確的話一起改錯，而**改完
    # grep 歸零、看起來很乾淨**，比漏改難發現得多。這裡要改的從來不是「law 這個
    # 字出現在哪」，而是「哪一句話把相似案講成了法規」。那三處（`unlinked` 的計數、
    # `_verify_of` 的驗證用語、`cite` 邊的 `basis`）已經修了，這一處不在其中。
    law_ids: set[str] = set()          # 只有 retrieval.laws
    case_ids: set[str] = set()         # 只有 retrieval.cases（相似訴願決定）
    by_raw_law: dict[str, str] = {}
    by_raw_case: dict[str, str] = {}
    for item, origin in [(x, "retrieval") for x in laws] + [(x, "similar_case") for x in cases]:
        lid = item.get("id")
        if not lid:
            continue
        is_case = origin == "similar_case"
        (case_ids if is_case else law_ids).add(lid)
        by_raw = by_raw_case if is_case else by_raw_law
        if item.get("t") and item["t"] not in by_raw:
            by_raw[item["t"]] = lid
        nodes.append({
            "id": lid, "k": "law", "c": COL_LAW, "t": item.get("t") or lid,
            "verify": _verify_of(item, origin), "origin": origin,
        })

    # ── 結論句節點 ───────────────────────────────────────────────────
    # 先全部建起來，最後只留「有連線」的（顯示決定，不是資料決定）：
    # 孤立句子仍然在 payload 裡，只是不進圖。`stats` 兩個數字要讓人看得出
    # 進圖的只有一部分，不要讓人以為草稿只有那幾句。
    where = _sections(doc)
    out_nodes: dict[str, dict[str, Any]] = {}
    for s in sentences:
        heading, n = where.get(s["id"], ("未標名", 0))
        out_nodes[s["id"]] = {
            "id": s["id"], "k": "out", "c": COL_OUT,
            "t": _clip(s.get("t"), OUT_TITLE_MAX),
            "d": f"（{heading}段第 {n} 句）",
            "l": s.get("l"), "origin": s.get("origin") or "llm",
        }

    # ── address 邊（爭點 → 結論句）───────────────────────────────────
    # 兩份來源講的是同一件事：`ss[].refs` 裡的 `I*`（N6 掛的）與
    # `gate.issue_refs[]`（N6 另外記的一份）。取聯集是因為兩份在不同的 run
    # 形狀裡各有缺漏；`(句, 爭點)` 去重之後不會重複畫。
    # **實測一份 run 13 句只有 1 句掛得到爭點——稀疏是資料的事實，不補線。**
    linked_out: set[str] = set()
    addressed: set[tuple[str, str]] = set()
    for s in sentences:
        for ref in s.get("refs") or []:
            if str(ref) in issue_ids:
                addressed.add((str(ref), s["id"]))
    for ref in issue_refs:
        iid, sid = str(ref.get("issue_id") or ""), str(ref.get("sentence_id") or "")
        if iid in issue_ids and sid in out_nodes:
            addressed.add((iid, sid))
    for iid, sid in sorted(addressed):
        edges.append({
            "from": iid, "to": sid, "rel": "address",
            "basis": "doc[].ss[].refs 含 " + iid,
        })
        linked_out.add(sid)

    # ── cite 邊（結論句 → 法規／案例）───────────────────────────────
    # 一筆 citation 一條邊，**不去重**：邊數要能跟 `citations[]` 對得起來
    # （plan AC4 把邊數釘成「`raw` 能在 `laws[].t` 找到的筆數」，寫 `> 0`
    # 的話用錯 join key 也可能因為別的原因湊出幾條）。
    # 查法規優先、相似案其次（順序不影響命中，兩張表的 key 不會撞：一個是條號文字、
    # 一個是決定書字號）。**`basis` 要寫出是跟哪一種對上的**——`basis` 是寫給人核對的，
    # 寫 `laws[].t` 卻其實對到 `cases[].t`，人就核不出來。
    cited: set[str] = set()
    drawn: set[tuple[str, str]] = set()      # 已畫過的 (句, 法規)，給下一段去重用
    for c in citations:
        raw, sid = c.get("raw"), str(c.get("sentence_id") or "")
        lid, src_key = by_raw_law.get(raw), "laws"
        if lid is None:
            lid, src_key = by_raw_case.get(raw), "cases"
        if lid and sid in out_nodes:
            edges.append({
                "from": sid, "to": lid, "rel": "cite",
                "basis": f"citations[].raw ↔ {src_key}[].t 字串相等",
                "state": c.get("state"), "lamp": c.get("lamp"),
            })
            linked_out.add(sid)
            cited.add(lid)
            drawn.add((sid, lid))
            continue
        # 沒命中不是 bug 是訊號。兩種沒命中要分得開：查無法條 vs 句子不在草稿裡。
        flagged.append({
            "sentence_id": sid or None,
            "raw": raw,
            "state": c.get("state"),
            "lamp": c.get("lamp"),
            "basis": ("citations[].raw 在 laws[] 查無（相似案 cases[] 也沒有）"
                      if lid is None else
                      "citations[].sentence_id 不在 doc[] 的句子裡"),
        })

    # ── cite 邊（期間計算句 → 它依據的法規）2026-09-13 新增 ─────────
    #
    # **稀疏的主因不是判準太嚴，是有一欄資料從來沒被讀過。** 16 份雲上真 run
    # 的 261 句裡有 160 句帶著 `ss[].basis`，其中 86 句是期間計算段——每一句都
    # 寫著自己依據哪條法規（`訴願法 14 I；民法 120 II`），而 `訴願法第14條`／
    # `民法第120條`／`行政程序法第72條` 正是當時躺在 `unlinked` 裡的那幾條。
    # **線的兩端本來就都在，只是沒人去連。**（實測：+92 條邊、句子進圖 29.5%→56.3%、
    # `unlinked.laws` 72→19。）
    #
    # 為什麼這一段安全（三條，缺一條就不該做）：
    #
    # ① **`basis` 不是模型寫的**。它來自 `screen.deadline.steps[].basis`，由期間
    #    引擎產生，在 `backend/orchestrator/narrative.py:183-188` 原封不動寫進句子。
    #    所以這裡限定 `engine == "deadline"`——**不吃 `slot`／`origin` 以外還加這一層**，
    #    是因為理由段（`origin:"llm"`）的 `basis` 是模型寫的，同一把尺套上去就不成立了。
    #    （實測理由段吃下去也是 **+0 條**：它們的 basis 早就被 `citations[]` 蓋掉了。）
    # ② **只從 `laws[]` 既有節點裡挑，不新增節點**。解出來的鍵要在
    #    `laws[].gate_ref_key` 找得到才畫，所以不可能畫出一條指向不存在法規的邊。
    # ③ **`basis` 原文帶在邊上**，承辦人一眼可核到底是依哪一步、哪一條。
    #
    # `state` 一律 `None`：草稿引用的四態是**守門對引用的查核結果**，這條邊不是
    # 從引用來的，填 `"ok"` 等於替守門發一張它沒發過的燈。前端只在
    # `state && state !== "ok"` 時畫警示線（`RelationGraph.vue:47`），所以 `None`
    # 會正常畫成實線。`lamp` 用句子自己的燈號（期間計算句是「可驗算」層，一律綠）。
    by_gate_key = {
        str(law["gate_ref_key"]): law["id"]
        for law in laws
        if law.get("gate_ref_key") and law.get("id")
    }
    for s in sentences:
        if s.get("engine") != "deadline":
            continue
        sid, basis = s["id"], str(s.get("basis") or "")
        keys = _basis_law_keys(basis)
        hit = False
        for key in keys:
            lid = by_gate_key.get(key)
            if lid is None:
                continue
            hit = True
            cited.add(lid)
            linked_out.add(sid)
            if (sid, lid) in drawn:
                continue                     # 同一句已經由 citations[] 連過同一條
            drawn.add((sid, lid))
            edges.append({
                "from": sid, "to": lid, "rel": "cite",
                "basis": f"doc[].ss[].basis（期間引擎逐步算式）：{basis}",
                "state": None, "lamp": s.get("l"),
            })
        # 解得出法條、卻在 laws[] 查無 → 跟草稿引用查無同一件事，不靜默丟棄。
        # **解不出任何法條的不進這裡**（`以上各步` 沒有法條依據、判例不是法條），
        # 那種句子就讓它不連——那是真的沒有東西可連，不是漏掉。
        if keys and not hit:
            flagged.append({
                "sentence_id": sid,
                "raw": basis,
                "state": None,
                "lamp": s.get("l"),
                "basis": f"doc[].ss[].basis 解出 {'、'.join(keys)}，在 laws[] 查無",
            })

    # **事實段的句子刻意不連回卷證**（2026-09-13 Ci 拍板不做，理由寫在這裡，
    # 因為它看起來像個現成的缺口）：`slot=="facts"` 的句子也帶 `basis`，值就是
    # `facts_excerpt[].quote_ref`，連起來能再多 21 條邊、句子進圖從 56.3% 升到
    # 59.8%。**但實測 21/21 句的文字與 `facts_excerpt[].text` 一字不差**——
    # 事實段句子就是事實節點再講一次，畫了等於同一段文字在第 1 欄和第 5 欄各一份、
    # 中間拉一條長線，不增加任何資訊，只是讓那 21 句看起來不孤單。
    # **「讓數字好看」不是畫一條線的理由。** 要做的話請連的是 `F* → s*` 而不是
    # `D* → s*`，並先想清楚兩欄同文要怎麼呈現。
    #
    # ── 只留有連線的結論句（plan §4 備註）──────────────────────────
    nodes.extend(out_nodes[sid] for sid in out_nodes if sid in linked_out)

    # ── 斷掉的地方 ───────────────────────────────────────────────────
    unlinked_laws = sorted(law_ids - cited)
    unlinked_cases = sorted(case_ids - cited)
    unlinked_issues = sorted(issue_ids - triggered)
    # 每一句只數自己那一種，數字一律 len() 算（`judgment-externalization.md` L1：
    # 衍生值用程式算不用眼睛核）。**量詞也是承諾**：法規論「條」、訴願決定論「件」。
    notes = []
    if unlinked_laws:
        notes.append(f"{len(unlinked_laws)} 條檢索到的法規沒有被任何結論句引用")
    if unlinked_cases:
        notes.append(f"{len(unlinked_cases)} 件檢索到的相似訴願決定沒有被任何結論句引用")
    if unlinked_issues:
        notes.append(
            f"{len(unlinked_issues)} 個爭點的關鍵詞不在任何一段卷證摘錄裡"
            "（N3 的比對範圍還含 intake.note，這裡只比對 facts_excerpt，"
            "所以只由收文備註觸發的爭點接不到事實節點）"
        )
    base["unlinked"] = {
        "laws": unlinked_laws,
        "cases": unlinked_cases,
        "issues": unlinked_issues,
        "note": "；".join(notes),
    }
    base["nodes"] = nodes
    base["edges"] = edges
    base["flagged"] = flagged
    # 加總一律用 len() 算，不手寫（`judgment-externalization.md` L1：衍生值用程式算）。
    #
    # ⚠️ **`edges_flagged` 是誤稱，但不改名**（2026-09-13 Ci 拍板）。它數的是
    # `flagged` 的長度，而 `flagged` 裡的東西**不是邊**——正好相反，是「沒能變成
    # 邊的引用」（`citations[]` 裡 `raw` 在 `laws[]`／`cases[]` 查無、或 `sentence_id`
    # 不在 `doc[]` 裡的那幾筆）。契約 §3.7 已經照這個語意載明，改名要動契約與前端，
    # demo 前不值得。**看到這個名字不要照字面理解成「被標記的邊數」。**
    #
    # 另外記一筆給前端：`frontend/src/api/mock.js:440` 的 mock 算的是
    # **`lamp !== "g"` 的邊數**，跟這裡的 `len(flagged)` 是兩件不同的事。
    # 開發時看到的數字與上線後不一樣，而兩邊各自都跑得起來——這條前端那邊排隊處理，
    # **不要為了對齊 mock 而改這裡**：這裡是契約定義的那一邊。
    base["stats"] = {
        "nodes": len(nodes),
        "edges": len(edges),
        "edges_flagged": len(flagged),
        "sentences_total": len(sentences),
        "sentences_in_graph": len(linked_out),
    }
    return base


def _main(argv: list[str] | None = None) -> int:
    """`python3 -m backend.graph.relation --run <run_id>` — 驗收用的手動入口。

    刻意**不 import runstore**（那會把編排層拉進這個模組的 import 圖，
    零 LLM 掃描是遞迴走 import 邊的）。直接讀 JSON 檔，讀不到就說讀不到。
    """
    ap = argparse.ArgumentParser(description="把一份 run payload 組成案件關聯圖")
    ap.add_argument("--run", help="run id（讀 backend/output/runs/<run>.json）")
    ap.add_argument("--file", help="直接指定 payload JSON 檔路徑")
    ap.add_argument("--runs-dir", default="backend/output/runs")
    args = ap.parse_args(argv)

    if args.file:
        path = pathlib.Path(args.file)
    elif args.run:
        path = pathlib.Path(args.runs_dir) / f"{args.run}.json"
    else:
        ap.error("要給 --run 或 --file")
    if not path.exists():
        print(f"找不到 {path}", file=sys.stderr)
        return 2
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(build_relation_graph(payload), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["COLS", "build_relation_graph"]
