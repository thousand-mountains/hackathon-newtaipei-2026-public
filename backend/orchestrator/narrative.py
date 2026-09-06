"""敘述層：決定書骨架模板 + 幕僚卡片文案（architecture §3.2）。

兩件事，共同的紅線是**沒有任何一句是模型寫的，也沒有任何一個數字是寫死的**：

1. `build_doc_skeleton()`：決定書的四型區塊（title／meta／h／p）是靜態骨架，
   句子內容分三種來源——`record`（卷證直錄）、`engine`（期間引擎算式）、
   `llm`（fixture 重播的模板句）。句子 id 由這裡統一編號，不由模型給。
2. `merge_agent_narrative()` / `render_degraded_log()`：把節點回傳的統計數字套模板，
   降級一律外顯（紅色 log）。降級可以，隱瞞不行。
"""
from __future__ import annotations

from typing import Any

from backend.config import settings
from backend.config.settings import AGENTS_NARRATIVE

PLACEHOLDER_CONCLUSION_TEXT = "（結論段由承辦人判斷後填寫）"

# fixture 草稿裡手寫的 `cite_ids`（"L1"、"L3"…）**預設不帶進 doc[]**。
#
# 為什麼：那些 id 是寫 fixture 的人在 N4 還沒跑之前手填的，指向的是一份當時並不存在的
# 檢索結果。2026-09-05 N4 改成獨立檢索之後，它們只會有兩種下場——
# (a) 序號剛好對上 N4 的 L1/L2/L3，於是「解析成功」但指到完全不相干的法條；
# (b) 序號對不上，N6 判 unresolved → 紅燈 → 假的 blocker。
# 兩種都是假訊號。**真正的引用查核本來就不靠 id**：N6 直接從句子本文與 basis 抽引用、
# 對快照查四態，那條路徑不受影響（假法條照樣被攔）。
#
# 接上真實 N5 之後要打開這個開關——那時 N5 是看著 N4 的候選清單寫的，cite_ids 才有意義
# （architecture §6.2：「N5 的 cite_ids 經 N6 解析比對」）。
CARRY_DRAFT_CITE_IDS_DEFAULT = False


def _sentence(
    sid: str,
    text: str,
    origin: str,
    slot: str,
    cite_ids: list[str] | None = None,
    basis: str | None = None,
    engine: str | None = None,
    placeholder: bool = False,
    adversarial: bool = False,
    adversarial_note: str | None = None,
) -> dict[str, Any]:
    """一句話的骨架。`l`／`why`／`refs` 留給 N6 填——模型不產燈號。

    `adversarial` 必須一路帶到 `doc[]`：對抗測資裡刻意注入的假引用如果在輸出 JSON 裡
    沒有標記，任何人只截 `doc[]`（或把它貼進簡報）就會看到一句沒有註記的假法條。
    """
    return {
        "id": sid,
        "t": text,
        "origin": origin,
        "slot": slot,
        "adversarial": adversarial,
        "adversarial_note": adversarial_note,
        "cite_ids": list(cite_ids or []),
        "basis": basis,
        "engine": engine,
        "placeholder": placeholder,
        "l": None,
        "why": None,
        "refs": [],
        # 分層誠實：這兩個欄位標明燈號與理由由誰產出，check_payload 會驗
        "l_origin": "rule",
        "why_origin": "rule",
    }


def build_doc_skeleton(
    intake: dict[str, Any],
    facts_excerpt: list[dict[str, Any]],
    deadline_result: dict[str, Any],
    draft_slots: dict[str, list[dict[str, Any]]],
    requires_human_conclusion: bool,
    carry_draft_cite_ids: bool = CARRY_DRAFT_CITE_IDS_DEFAULT,
) -> list[dict[str, Any]]:
    """組出 doc[]（四型區塊）。句子 id 由編排層統一編號 s1, s2, …

    `carry_draft_cite_ids=False`（預設）時，草稿槽位帶的 `cite_ids` 不會進 `doc[]`——
    理由見模組頂端 `CARRY_DRAFT_CITE_IDS_DEFAULT` 的說明。引用查核不受影響（N6 從本文抽）。
    """
    doc: list[dict[str, Any]] = []
    counter = {"n": 0}

    def cids(s: dict[str, Any]) -> list[str] | None:
        return s.get("cite_ids") if carry_draft_cite_ids else []

    def nid() -> str:
        counter["n"] += 1
        return f"s{counter['n']}"

    doc.append(
        {
            "ty": "title",
            "text": f"{intake.get('org') or '（機關）'} 訴願決定書（合成測資草稿）",
            "ind": 0,
            "ss": [],
        }
    )
    doc.append(
        {
            "ty": "meta",
            "text": (
                f"案號 {intake.get('no') or '—'}／案由 {intake.get('type') or '—'}／"
                f"訴願人 {intake.get('person') or '—'}"
            ),
            "ind": 0,
            "ss": [],
        }
    )

    # ── 事實段：卷證原文直錄，不生成 ────────────────────────────
    doc.append({"ty": "h", "text": "事實", "ind": 0, "ss": []})
    facts_block: dict[str, Any] = {"ty": "p", "text": "", "ind": 1, "ss": []}
    if facts_excerpt:
        for ex in facts_excerpt:
            facts_block["ss"].append(
                _sentence(
                    nid(),
                    ex.get("text", ""),
                    origin="record",
                    slot="facts",
                    basis=ex.get("quote_ref"),
                )
            )
    else:
        facts_block["ss"].append(
            _sentence(
                nid(),
                "（卷證中未擷取到可辨識的事實段，請承辦人自卷證補錄；系統不代為生成事實。）",
                origin="human_required",
                slot="facts",
                placeholder=True,
            )
        )
    doc.append(facts_block)

    # ── 理由段：fixture 模板句 + 期間引擎算式句 ──────────────────
    doc.append({"ty": "h", "text": "理由", "ind": 0, "ss": []})
    reasoning_block: dict[str, Any] = {"ty": "p", "text": "", "ind": 1, "ss": []}
    for s in draft_slots.get("reasoning", []):
        reasoning_block["ss"].append(
            _sentence(
                nid(),
                s.get("t", ""),
                origin="llm",
                slot="reasoning",
                cite_ids=cids(s),
                basis=s.get("basis"),
                adversarial=bool(s.get("adversarial")),
                adversarial_note=s.get("adversarial_note"),
            )
        )
    doc.append(reasoning_block)

    # ── 期間計算段：規則引擎逐步算式（可驗算層）──────────────────
    if deadline_result.get("steps"):
        doc.append({"ty": "h", "text": "期間計算（規則引擎逐步驗算）", "ind": 0, "ss": []})
        calc_block: dict[str, Any] = {"ty": "p", "text": "", "ind": 1, "ss": []}
        for step in deadline_result["steps"]:
            calc_block["ss"].append(
                _sentence(
                    nid(),
                    f"{step['rule']}：{step['value']}",
                    origin="engine",
                    slot="calculation",
                    basis=step["basis"],
                    engine="deadline",
                )
            )
        doc.append(calc_block)

    # ── 結論段：C 型時封鎖，改填佔位 ──────────────────────────
    doc.append({"ty": "h", "text": "決定主文", "ind": 0, "ss": []})
    conclusion_block: dict[str, Any] = {"ty": "p", "text": "", "ind": 1, "ss": []}
    if requires_human_conclusion:
        conclusion_block["ss"].append(
            _sentence(
                nid(),
                PLACEHOLDER_CONCLUSION_TEXT,
                origin="human_required",
                slot="conclusion",
                placeholder=True,
            )
        )
    else:
        for s in draft_slots.get("conclusion", []):
            conclusion_block["ss"].append(
                _sentence(
                    nid(),
                    s.get("t", ""),
                    origin="llm",
                    slot="conclusion",
                    cite_ids=cids(s),
                    basis=s.get("basis"),
                    adversarial=bool(s.get("adversarial")),
                    adversarial_note=s.get("adversarial_note"),
                )
            )
    doc.append(conclusion_block)
    return doc


def conclusion_block_criterion(
    screen: dict[str, Any],
    classification: dict[str, Any],
    substantive_types: tuple[str, ...],
) -> dict[str, Any]:
    """如實描述「這一案的結論段為什麼被封鎖（或沒有）」。

    **這個函式不做任何判斷，只是把 `gate/lamps.requires_human_conclusion()` 已經做完的
    判斷重新講一遍人話。** 之所以要重講，是因為原本 UI 只拿得到 lamps 產出的訊號字串陣列，
    而那個陣列把「操作判準」跟「附帶偵測到的事實爭點」並排列出，讀起來像兩個都是原因。
    覆核實測證實不是：對抗案例把事實爭點全拿掉仍然封鎖。

    判斷順序刻意對齊 `lamps.requires_human_conclusion()` 的 early-return 順序
    （fail-safe → substantive → 高風險爭點），這樣「哪一條才是操作判準」才會講對。
    漂移由契約測試釘住：`blocked` 必須等於 `screen.requires_human_conclusion`。
    """
    art77 = screen.get("art77") or {}
    fact_issues = screen.get("fact_issues") or []
    case_type = ((classification or {}).get("class") or {}).get("case_type") or ""

    substantive = bool(art77.get("requires_substantive_review")) and case_type in substantive_types
    high = [i for i in fact_issues if i.get("severity") == "high"]
    unknown_type = case_type not in substantive_types
    inputs_confirmed = bool(screen.get("procedural_inputs_confirmed"))
    unconfirmed_fields = list(screen.get("unconfirmed_procedural_fields") or [])
    procedurally_resolved = bool(art77.get("clause")) and inputs_confirmed
    # 判斷卡 7：靠未確認的抽取欄位算出來的程序結論，不准拿來解除封鎖
    unconfirmed_unlock = bool(art77.get("clause")) and not inputs_confirmed
    fail_safe = unknown_type and not procedurally_resolved

    if fail_safe:
        reason_id = "unknown_case_type_fail_safe"
        text = settings.BLOCK_CRITERION_FAIL_SAFE.format(case_type=case_type or "（空白）")
    elif unconfirmed_unlock and not substantive:
        reason_id = "unconfirmed_procedural_inputs"
        text = settings.BLOCK_CRITERION_UNCONFIRMED.format(
            fields="、".join(unconfirmed_fields) or "（未指明）",
            clause=art77.get("clause") or "—",
        )
    elif substantive:
        reason_id = "procedurally_valid_needs_substantive_review"
        text = settings.BLOCK_CRITERION_SUBSTANTIVE.format(case_type=case_type)
    elif high:
        reason_id = "high_severity_fact_issue"
        text = settings.BLOCK_CRITERION_FACT_ISSUE.format(issue_ids="、".join(i["id"] for i in high))
    else:
        reason_id = "not_blocked"
        text = settings.BLOCK_CRITERION_NONE

    blocked = bool(fail_safe or substantive or high or unconfirmed_unlock)
    # 高風險爭點只有在它「就是」操作判準時才算原因；其餘情況一律標成提醒
    fact_issue_is_operative = reason_id == "high_severity_fact_issue"
    return {
        "blocked": blocked,
        "reason_id": reason_id,
        "text": text,
        "fact_issue_role": "operative" if fact_issue_is_operative else "observation",
        "fact_issue_label": (
            settings.FACT_ISSUE_OPERATIVE_LABEL
            if fact_issue_is_operative
            else settings.FACT_ISSUE_OBSERVATION_LABEL
        ),
        "origin": "rule",
    }


def merge_agent_narrative(
    acc: dict[str, Any], node: str, result_narrative: dict[str, Any], degraded: bool, reason: str | None
) -> dict[str, Any]:
    """把節點 narrative 併進 agents[] 結構；降級時強制追加一條紅色 log。"""
    for key, payload in result_narrative.items():
        static = AGENTS_NARRATIVE.get(key, {})
        entry = {
            "k": key,
            "ico": static.get("ico", key),
            "name": static.get("name", key),
            "role": static.get("role", ""),
            "node": node,
            "out": payload.get("out", ""),
            "logs": [list(l) for l in payload.get("logs", [])],
        }
        if degraded:
            entry["logs"].append([f"⚠ 本節點已降級：{reason or '未說明原因'}", "r"])
        acc[key] = entry
    return acc


def summary_line(run_meta: dict[str, Any]) -> str:
    degraded = run_meta.get("degraded", [])
    return (
        f"本次執行 {len(run_meta.get('node_timings', {}))} 個節點，"
        f"共 {run_meta.get('elapsed_ms', 0)} ms，"
        f"其中 {len(degraded)} 個節點降級。"
    )
