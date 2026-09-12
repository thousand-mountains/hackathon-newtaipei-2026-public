"""run payload → 契約 §4.4 的草稿結構（`sections[]`）。純轉換，零外部依賴。

放在 `backend/dossier/` 而不是 `backend/api/dossier.py` 的理由很實際：
`backend/api/*` 需要 fastapi 才 import 得動，而 `run_all.py` 跑在一台只有
stdlib 的 python 上，放在那裡就**測不到**——只能靠 AST 讀原始碼，
而 AST 讀不出「這個對應關係算得對不對」。
"""
from __future__ import annotations

from typing import Any

#: `doc[].ty` 裡屬於「標題」的三型（`test_contract` 釘住值域是 title/meta/h/p）。
#: 只有 `p` 帶句子，其餘三型是段落標頭。
_HEADING_TYPES = ("title", "meta", "h")


def sections_from_payload(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """`build_payload()` 的結果 → 契約 §4.4 的 `(sections[], cite_count)`。

    三個對應關係，都不是自己定的，是照 payload 既有的形狀（`test_contract.py` 釘著）：

    - 句子文字是 `ss[].t`，**不是 `doc[].text`**（後者在 `ty="p"` 的塊上是空字串）。
    - 引註是 `ss[].refs`（`L*`／`C*`／`I*`），`label` 去左欄三個清單查標題。
      `test_contract.test_sentence_refs_resolve_to_existing_cards` 保證查得到；
      萬一查不到就**用 id 當 label**，不隱藏那一筆——點不開的空按鈕才是最糟的。
    - **`doc[].ind` 是縮排層級，不是引註。** 別拿它當 cites。
    - `cite_count` 從 payload **數出來**，不是設計稿寫死的「14 處」。
    """
    labels = {x["id"]: x.get("t") or x["id"]
              for group in ("laws", "cases", "issues")
              for x in (payload.get(group) or [])}
    sections: list[dict[str, Any]] = []
    count = 0

    def current() -> dict[str, Any]:
        if not sections:
            sections.append({"h": "", "blocks": []})
        return sections[-1]

    for block in payload.get("doc") or []:
        ty = block.get("ty")
        if ty in _HEADING_TYPES:
            sections.append({"h": block.get("text") or "", "blocks": []})
            continue
        for sentence in block.get("ss") or []:
            cites = [{"id": r, "label": labels.get(r, r)} for r in (sentence.get("refs") or [])]
            count += len(cites)
            current()["blocks"].append({"text": sentence.get("t") or "", "cites": cites})
    return sections, count
