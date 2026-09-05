"""Deterministic 狀態機：N1 → N2 → N3 → N4 → N5 → N6（architecture §4.1、§4.2）。

**編排是程式，不是 agent。** 節點順序、狀態轉換、降級路由全部寫死在這裡，
沒有任何一步由模型決定要不要跑、跑哪個。這是「規則引擎零 LLM 依賴」在編排層的體現。

每次轉換前 assert 不變式（`state.py` 的 `assert_*_invariant`），失敗即中止並記錄。
"""
from __future__ import annotations

import json
import pathlib
import time
import uuid
from typing import Any

from backend.config.origin_registry import (
    TIER_HUMAN,
    TIER_SOURCED,
    TIER_VERIFIABLE,
    check_payload,
)
from backend.config.settings import (
    AUTO_FIELD_CONF_THRESHOLD,
    AUTO_TOAST_TEMPLATE,
    ISSUE_LAMP,
    ISSUE_TAG_BY_SEVERITY,
    CONFIRMABLE_INTAKE_FIELDS,
    NODE_TO_AGENTS,
    PROVENANCE,
    SUBSTANTIVE_TYPES,
    SYNTHETIC_DIR,
    TOKEN_NOTE,
    load_snapshot,
    run_mode,
)
from backend.nodes import n1_extract, n2_classify, n3_procedure, n4_retrieval, n5_draft, n6_gate
from backend.orchestrator.narrative import (
    conclusion_block_criterion,
    merge_agent_narrative,
    summary_line,
)
from backend.orchestrator.state import CaseState, NodeCtx, utc_now_iso

NODE_ORDER = ("n1", "n2", "n3", "n4", "n5", "n6")
STATE_AFTER = {
    "n1": "EXTRACTED",
    "n2": "CLASSIFIED",
    "n3": "SCREENED",
    "n4": "RETRIEVED",
    "n5": "DRAFTED",
    "n6": "VERIFIED",
}


class CaseNotFound(FileNotFoundError):
    pass


def list_synthetic_cases(data_dir: pathlib.Path | None = None) -> list[str]:
    d = data_dir or SYNTHETIC_DIR
    return sorted(p.stem for p in d.glob("synthetic-*.json"))


def load_case(case_id: str, data_dir: pathlib.Path | None = None) -> dict[str, Any]:
    """只讀 `synthetic-` 前綴的檔案。

    CONSTITUTION §3／§6：本 Phase 不存在、也不得讀取任何真實競賽資料。
    路徑名不帶 synthetic 前綴的一律拒絕，避免有人把真實資料丟進來就跑得起來。
    """
    if not case_id.startswith("synthetic-"):
        raise ValueError(
            f"案例 id {case_id!r} 不是 synthetic- 前綴。本系統只處理合成測資，"
            f"真實競賽資料不進 git、不由本流程讀取（CONSTITUTION §3、§6）。"
        )
    d = data_dir or SYNTHETIC_DIR
    p = d / f"{case_id}.json"
    if not p.exists():
        raise CaseNotFound(f"找不到合成案例 {p}。現有案例：{', '.join(list_synthetic_cases(d)) or '（無）'}")
    return json.loads(p.read_text(encoding="utf-8"))


def _apply_confirmed_intake(state: CaseState, confirmed: dict[str, Any] | None) -> None:
    """把承辦人在收文頁確認過的欄位寫回 state，並把 origin 翻成 `human`（判斷卡 7）。

    「確認」的定義是**人看過那個值**（可能改過，也可能原樣採用），不是「值變了」。
    所以即使送來的值與 N1 抽的一模一樣，origin 也要翻成 human——
    差別在於現在有人為它背書了。

    白名單制：不在 `CONFIRMABLE_INTAKE_FIELDS` 內的鍵一律拒絕（丟 ValueError），
    不讓呼叫端用這條路徑塞進任意狀態。
    """
    if not confirmed:
        return
    unknown = [k for k in confirmed if k not in CONFIRMABLE_INTAKE_FIELDS]
    if unknown:
        raise ValueError(
            f"confirmed_intake 含不可確認的欄位 {unknown}；"
            f"允許的欄位：{', '.join(CONFIRMABLE_INTAKE_FIELDS)}"
        )
    applied: list[str] = []
    for k, v in confirmed.items():
        # **null 一律不採用**。舊版把型別脅迫寫在 None 檢查之前，於是
        # `{"transit_days": null}` 被 `int(v or 0)` 變成 0、`{"interested_party": null}`
        # 被 `bool(v)` 變成 False，而且照樣把 origin 翻成 human——
        # 那等於「送一個空值就能宣稱有人確認過」，正好是判斷卡 7 要防的那件事
        # （實測會把期滿日從 2024-07-18 改成 2024-07-15）。
        # 沒給值就是沒有人確認這一欄：值不動、origin 不翻、不列進 intake_confirmed。
        if v is None:
            continue
        if k == "transit_days":
            try:
                v = int(v)
            except (TypeError, ValueError) as e:
                raise ValueError(f"confirmed_intake.transit_days 必須是整數，實得 {v!r}") from e
        elif k == "interested_party":
            v = bool(v)
        state.intake[k] = v
        state.intake_origin[k] = "human"
        applied.append(k)
    state.intake_confirmed = sorted(applied)


def run_case(
    case_id: str,
    mode: str | None = None,
    data_dir: pathlib.Path | None = None,
    confirmed_intake: dict[str, Any] | None = None,
) -> CaseState:
    """把一個合成案例跑完六個節點，回傳終態 CaseState。

    `confirmed_intake`：承辦人在收文頁確認過的 intake 欄位。**不給就是沒人確認過**，
    此時期間結果不得用來解除結論封鎖（判斷卡 7，見 `gate/lamps.requires_human_conclusion`）。
    """
    fixture = load_case(case_id, data_dir)
    mode = mode or run_mode()
    snapshot = load_snapshot()
    digest = fixture.get("case_digest", "")

    state = CaseState(case_id=case_id, run_mode=mode)
    # architecture §6.1 2a：POST /runs 的回傳值是 run_id。Phase 0 是同步執行、
    # 沒有「進行中的 run」可以復用，所以每次執行給一個新的 id，並在
    # `run_meta.run_id_note` 誠實寫出「冪等復用尚未實作」，不假裝有做。
    state.run_id = f"run-{case_id}-{uuid.uuid4().hex[:12]}"
    state.files = list(fixture.get("files") or [])
    ctx = NodeCtx(run_mode=mode, snapshot=snapshot)

    run_started = time.perf_counter()
    node_timings: dict[str, int] = {}
    degraded: list[dict[str, Any]] = []
    agents: dict[str, Any] = {}

    # N4 的查詢句由 N1／N2／N3 的結果決定（`n4_retrieval.build_query()`）。
    # 這裡**刻意不再蒐集草稿引用**：舊版把 fixture 草稿即將引用的法條餵給 N4，
    # 那讓「引用一定查得到」變成必然——查什麼是照著答案要引用什麼倒著填的，
    # 檢索佐證的是自己。2026-09-05 Ci 拍板改獨立檢索。
    state.transition("EXTRACTING")
    for node in NODE_ORDER:
        result = _dispatch(node, state, ctx, fixture, digest)
        if node == "n1":
            # 人工確認緊接在抽取之後套用：N3 的程序判斷要看得到 origin 已翻成 human
            _apply_confirmed_intake(state, confirmed_intake)
        node_timings[node] = result.elapsed_ms
        merge_agent_narrative(agents, node, result.narrative, result.degraded, result.degrade_reason)
        if result.degraded:
            degraded.append(
                {"node": node, "reason": result.degrade_reason, "agents": NODE_TO_AGENTS.get(node, [])}
            )
        # N1 信心不足 → NEEDS_INPUT，狀態機停在這裡等人工補齊（architecture §4.2）
        if node == "n1" and result.degraded:
            state.transition("NEEDS_INPUT")
            break
        state.transition(STATE_AFTER[node])

    state.agents_narrative = agents
    state.run_meta = {
        "run_id": state.run_id,
        "run_id_note": "同步執行，每次 POST /runs 產生新 id；architecture §6.1 2a 的冪等復用尚未實作。",
        "run_mode": mode,
        "started_at": utc_now_iso(),
        "elapsed_ms": int((time.perf_counter() - run_started) * 1000),
        "node_timings": node_timings,
        "degraded": degraded,
        # 沒有實際呼叫任何模型，就不給 model id（不腦補）
        "model_ids": None,
        "model_ids_note": "fixture 檔位未呼叫任何基礎模型，故無 model id。",
        "kb_snapshot_date": snapshot.get("generated"),
        "final_state": state.state,
    }
    state.run_meta["summary"] = summary_line(state.run_meta)
    return state


def _dispatch(node, state, ctx, fixture, digest):
    if node == "n1":
        return n1_extract.run(state, ctx, case_fixture=fixture)
    if node == "n2":
        return n2_classify.run(state, ctx, digest=digest)
    if node == "n3":
        return n3_procedure.run(state, ctx, digest=digest)
    if node == "n4":
        # cited_laws 刻意不傳：N4 自己從 state（N1/N2/N3 的結果）組查詢句。
        return n4_retrieval.run(state, ctx)
    if node == "n5":
        return n5_draft.run(state, ctx, case_fixture=fixture)
    if node == "n6":
        return n6_gate.run(state, ctx)
    raise ValueError(f"未知節點 {node}")


# ── §6.2 CASE payload 的頂層視圖欄位 ──────────────────────────────
def _auto_fields(state: CaseState) -> list[str]:
    """信心值 ≥ 門檻的欄位名清單（architecture §6.2，origin=rule）。

    **§6.2 未明確的地方，採保守解讀**：§6.2 寫「conf ≥ 門檻 的欄位 id 清單」，
    沒有定義「欄位 id」是後端欄位名（`type`）還是前端表單元素 id（`a_type`）。
    這裡回**後端欄位名**——後端不該知道前端的 DOM id，映射留給前端做。
    """
    return sorted(f for f, c in (state.intake_conf or {}).items() if c >= AUTO_FIELD_CONF_THRESHOLD)


def _issues_view(state: CaseState) -> list[dict[str, Any]]:
    """`issues[]`：N3 偵測到的事實認定爭點 + 燈號（architecture §6.2）。

    **§6.2 未明確的地方，採保守解讀**：§6.2 把 `issues[].{lamp,tag}` 歸給「N6 燈號規則」，
    但 N6 目前不產爭點卡的燈號（它只把 `I*` ref 掛到句子上）。事實認定爭點的定義本身
    就是「AI 不得代為認定」，所以這裡固定給紅燈——**不是猜一個燈號，是照定義給唯一可能的那個**，
    並依 severity 給 tag 文字。規則寫在 `config/settings.py`，零 LLM。
    """
    out: list[dict[str, Any]] = []
    for issue in (state.screen.get("fact_issues") or []):
        out.append(
            {
                "id": issue.get("id"),
                "t": issue.get("t"),
                "q": issue.get("q"),
                "src": issue.get("src"),
                "lamp": ISSUE_LAMP,
                "tag": ISSUE_TAG_BY_SEVERITY.get(issue.get("severity", ""), ISSUE_TAG_BY_SEVERITY[""]),
                "severity": issue.get("severity"),
                "origin": "rule",
            }
        )
    return out


def _handoff_view(state: CaseState) -> dict[str, Any]:
    """交接卡 + **如實描述的封鎖判準**（2026-09-05 Ci 拍板）。

    N6 產出的 `handoff.signals` 是一個字串陣列，把「本案的操作判準」與「另外偵測到的
    事實認定爭點」並排列出。並排列出＝讀起來像兩個都是原因，但覆核實測證明不是：
    對抗案例把事實爭點全部拿掉，**仍然封鎖**。

    這裡**不動 N6 的輸出**（`signals` 原樣保留，守門節點是別人的範圍），
    只additively 加三個欄位讓 UI 有辦法如實呈現：
    - `criterion`：本案真正的操作判準，一句話講完（來自 narrative.conclusion_block_criterion）
    - `observations`：事實爭點訊號，明確標成提醒而非原因
    - `observations_label`：那份清單該用什麼標題（提醒／原因，依它是不是操作判準而定）
    """
    handoff = dict(state.gate.get("handoff") or {})
    criterion = conclusion_block_criterion(state.screen or {}, state.classification or {}, SUBSTANTIVE_TYPES)

    # 把 signals 拆成「爭點類」與「其餘」——用 fact_issue 的 id 比對，不用字串樣式猜
    issue_ids = [i.get("id") for i in (state.screen.get("fact_issues") or []) if i.get("id")]
    signals = list(handoff.get("signals") or [])
    observations = [s for s in signals if any(iid and iid in s for iid in issue_ids)]

    handoff["criterion"] = criterion
    handoff["observations"] = observations
    handoff["observations_label"] = criterion["fact_issue_label"]
    return handoff


def _retrieval_divergence(laws: list[dict[str, Any]], citations: list[dict[str, Any]]) -> dict[str, Any]:
    """獨立檢索找到的 vs 草稿實際引用的，兩邊的差集。

    N4 改成獨立檢索（2026-09-05 Ci 拍板）之後，這兩份清單本來就不會一樣。
    **落差本身是資訊，不是故障**——但如果只是讓左欄少幾張卡片，看的人會以為
    「系統檢索過了、沒意見」。所以把兩邊的差集明講出來：
    - `cited_not_retrieved`：草稿引用了、但獨立檢索沒找到。可能是條號寫錯（假法條就落在這裡），
      也可能只是案情訊號不足以推出那個條號（Phase 0 沒有 PDF 視覺抽取，實體法條號抽不到）。
      **這個清單不代表引用是錯的**——引用對錯由守門的四態負責，不由這裡。
    - `retrieved_not_cited`：檢索找到、草稿沒引用。可能是漏引，也可能只是不相干。
    """
    law_titles = [l["t"] for l in laws if l.get("t")]
    cited = []
    for c in citations:
        raw = c.get("raw")
        if raw and raw not in cited:
            cited.append(raw)
    return {
        "cited_not_retrieved": [c for c in cited if c not in law_titles],
        "retrieved_not_cited": [t for t in law_titles if t not in cited],
        "note": (
            "N4 的檢索是獨立進行的（查詢句只由案情組成），所以它找到的法條與草稿實際引用的"
            "不會一一對應。落差不等於錯誤：引用對錯看 citations[] 的四態，這裡只說明兩份清單"
            "為什麼長得不一樣。實體法條號目前無法由案情自動判定（缺 PDF 視覺抽取），"
            "所以 cited_not_retrieved 會偏長，這是已知限制不是 bug。"
        ),
    }


def _attach_law_refs(doc: list[dict[str, Any]], laws: list[dict[str, Any]]) -> None:
    """把句子的引用解析成 `L*` ref（architecture §6.2 `doc[].ss[].refs[]`）。

    解析方式是**字串相等比對**：句子的 `citations[].raw` 與 `laws[].t` 都是引用原文
    （例「訴願法第14條」），相等才掛。不做模糊比對、不靠 `cite_ids` 的序號巧合——
    序號巧合會在文字一變時靜默失效而不報錯（HANDOFF 五點五節指出過這個風險）。
    解析不到就不掛；那一句的紅黃燈另由 N6 的引用四態決定，不受這裡影響。
    """
    by_raw = {law.get("t"): law.get("id") for law in laws if law.get("t") and law.get("id")}
    for block in doc:
        for s in block.get("ss", []):
            refs = s.setdefault("refs", [])
            for c in s.get("citations", []) or []:
                rid = by_raw.get(c.get("raw"))
                if rid and rid not in refs:
                    refs.insert(0, rid)  # L* 排在 N6 掛的 I* 前面


# ── 三層誠實的輸出視圖 ─────────────────────────────────────────────
def build_payload(state: CaseState) -> dict[str, Any]:
    """把終態組成對外 payload，並依三層誠實分層。"""
    doc = state.gate.get("doc", [])
    laws = list((state.retrieval or {}).get("laws") or [])
    cases = list((state.retrieval or {}).get("cases") or [])
    _attach_law_refs(doc, laws)
    tiers: dict[str, list[dict[str, Any]]] = {
        TIER_VERIFIABLE: [],
        TIER_SOURCED: [],
        TIER_HUMAN: [],
    }
    for block in doc:
        for s in block.get("ss", []):
            tier = s.get("tier") or TIER_HUMAN
            tiers.setdefault(tier, []).append(
                {
                    "id": s["id"],
                    "slot": s["slot"],
                    "t": s["t"],
                    "l": s["l"],
                    "why": s["why"],
                    "origin": s["origin"],
                    "basis": s.get("basis"),
                    "refs": s.get("refs", []),
                    "placeholder": s.get("placeholder", False),
                }
            )

    # 期間引擎的 caveats 與交接卡也屬「請人工判斷」層，一併掛上
    caveats = (state.screen.get("deadline") or {}).get("caveats", [])
    for c in caveats:
        tiers[TIER_HUMAN].append({"id": None, "slot": "caveat", "t": c, "l": "y", "why": "期間引擎明列之人工判斷項", "origin": "rule"})

    auto_fields = _auto_fields(state)
    # §6.2：`intake.auto_fields` / `intake.auto_toast` 是編排層算的（origin=rule），
    # 不覆寫 N1 寫進 state.intake 的抽取欄位——所以組一份新 dict，不動 state。
    intake_view = dict(state.intake)
    intake_view["auto_fields"] = auto_fields
    intake_view["auto_toast"] = AUTO_TOAST_TEMPLATE.format(n=len(auto_fields))

    payload = {
        "case_id": state.case_id,
        "run_id": state.run_id,
        "state": state.state,
        "provenance": PROVENANCE,
        "files": state.files,
        "intake": intake_view,
        "intake_conf": state.intake_conf,
        "intake_origin": state.intake_origin,
        # 判斷卡 7：哪些欄位是承辦人確認過的（空 list = 全部仍是模型抽取）
        "intake_confirmed": state.intake_confirmed,
        "facts_excerpt": state.facts_excerpt,
        "classification": state.classification,
        "screen": state.screen,
        "retrieval": state.retrieval,
        # §6.2 的頂層 `laws[]` / `cases[]` / `issues[]`：前端左欄三個分頁直接吃這三個。
        # `laws` / `cases` 是 `retrieval.*` 的同一份物件（不是複製一份改過的），
        # 避免兩處內容漂移；`issues` 由 fact_issues 加燈號組成，見 `_issues_view()`。
        "laws": laws,
        "cases": cases,
        "issues": _issues_view(state),
        "doc": doc,
        "citations": state.gate.get("citations", []),
        # 獨立檢索找到的 vs 草稿實際引用的，兩邊的差集（見 _retrieval_divergence 的說明）
        "retrieval_divergence": _retrieval_divergence(laws, state.gate.get("citations", [])),
        "citation_counts": state.gate.get("citation_counts", {}),
        "lamp_stats": state.gate.get("lamp_stats", {}),
        "blockers": state.gate.get("blockers", []),
        "issue_refs": state.gate.get("issue_refs", []),
        "handoff": _handoff_view(state),
        "submit_allowed": state.gate.get("submit_allowed", False),
        "agents": list(state.agents_narrative.values()),
        "token_note": TOKEN_NOTE,
        "run_meta": state.run_meta,
        "tiers": {
            "可驗算": tiers[TIER_VERIFIABLE],
            "有出處": tiers[TIER_SOURCED],
            "請人工判斷": tiers[TIER_HUMAN],
        },
        "history": state.history,
    }
    payload["origin_violations"] = check_payload(payload)
    return payload
