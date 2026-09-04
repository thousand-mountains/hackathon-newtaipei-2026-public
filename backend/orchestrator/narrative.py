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

from backend.config.settings import AGENTS_NARRATIVE

PLACEHOLDER_CONCLUSION_TEXT = "（結論段由承辦人判斷後填寫）"


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
) -> list[dict[str, Any]]:
    """組出 doc[]（四型區塊）。句子 id 由編排層統一編號 s1, s2, …"""
    doc: list[dict[str, Any]] = []
    counter = {"n": 0}

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
                cite_ids=s.get("cite_ids"),
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
                    cite_ids=s.get("cite_ids"),
                    basis=s.get("basis"),
                    adversarial=bool(s.get("adversarial")),
                    adversarial_note=s.get("adversarial_note"),
                )
            )
    doc.append(conclusion_block)
    return doc


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
