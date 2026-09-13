"""草稿的「產出視圖」：把 `build_payload()` 的 `doc[]` 轉成契約 §4.4 的 `sections[]`。

契約：`docs/handoff/2026-09-12-frontend-contract-v2.md` §4.4
（`GET /api/cases/{id}/artifacts/{artifactId}` 與同一支的 `…/export`）。

```jsonc
{ "artifact_id":"art-…", "title":"訴願決定書草稿 v1", "run_id":"run-…",
  "sections":[ { "h":"主文", "blocks":[
      { "text":"原處分撤銷……", "cites":[ {"id":"L8","label":"訴願法§81 I"} ] } ]} ],
  "cite_count": 14 }
```

**這個檔刻意零第三方依賴**，所以它受 `backend/tests/run_all.py` 的
`scan_core_path_dependencies()` 管轄（`api/` 與 `llm/` 是具名豁免，這裡不是）。
理由：「引註是怎麼算出來的」是這個產品的核心主張（CONSTITUTION §2 引用必可驗），
不該有任何外部套件混進這條路徑。實際組 `.docx`／`.pdf` 的位元組那一層另外放在
`backend/api/export_render.py`，那裡才 import `python-docx` 與 `fpdf2`。

兩件實測過、不要再推論的事：

1. **`doc[]` 的 `ty` 值域是 `title` / `meta` / `h` / `p`**（`orchestrator/narrative.py`）。
   `title` 與 `meta` 不是 section，是文件抬頭；把它們當 section 會在匯出檔裡
   長出兩個空標題。
2. **正文在 `block["ss"][*]["t"]`，不在 `block["text"]`**。`ty=="p"` 的 `text`
   實測一律是空字串，句子全在 `ss`。只讀 `text` 會匯出一份完全空白卻不報錯的檔案。

`cite_count` **一律現數**（REQ-EXPORT-003 AC4）：設計稿寫死的「引註 14 處」是假資料，
沿用它會讓畫面上的數字與檔案裡的引註數量對不上。
"""
from __future__ import annotations

from typing import Any

# `doc[]` 區塊型別（`orchestrator/narrative.py`）
TY_TITLE = "title"
TY_META = "meta"
TY_HEADING = "h"

# `refs[]` 的前綴 → payload 裡對應的清單。順序即查找順序。
_REF_SOURCES: tuple[tuple[str, str], ...] = (
    ("L", "laws"),      # N4 檢索到的法條
    ("C", "cases"),     # N4 檢索到的相似案例
    ("R", "refs"),      # N5 `retrieve_refs` 工具當次撈到的函釋／判解
    ("I", "issues"),    # N6 掛上的爭點
)

UNRESOLVED_SUFFIX = "（未解析）"


def _label_index(payload: dict[str, Any]) -> dict[str, str]:
    """id → 顯示字串。四份清單的標題欄位實測都叫 `t`（n4_retrieval.py:273/329、
    n5_draft.py:109、graph.py `_issues_view`），所以這裡不做欄位嗅探——
    嗅探會在某一份改了欄位名時靜默回到 id，而那正是「看得到編號、查不到出處」。"""
    index: dict[str, str] = {}
    for _prefix, key in _REF_SOURCES:
        for item in payload.get(key) or []:
            rid = item.get("id")
            label = item.get("t")
            if rid and label:
                index.setdefault(str(rid), str(label))
    return index


def _cites_of(
    sentence: dict[str, Any],
    index: dict[str, str],
    unresolved: list[str],
) -> list[dict[str, str]]:
    """把 `ss[].refs[]`（`["L3", "I1"]`）轉成 `[{"id","label"}]`。

    查不到 label 時**照實回 id 並標「（未解析）」**，不靜默省略（REQ-EXPORT-003 AC3）。
    省略掉的引用在匯出檔裡看起來就像「這句沒有引用」——那是把一個已知的缺陷
    偽裝成一個乾淨的事實。
    """
    cites: list[dict[str, str]] = []
    seen: set[str] = set()
    for rid in sentence.get("refs") or []:
        rid = str(rid)
        if rid in seen:
            continue
        seen.add(rid)
        label = index.get(rid)
        if label is None:
            unresolved.append(rid)
            label = f"{rid}{UNRESOLVED_SUFFIX}"
        cites.append({"id": rid, "label": label})
    return cites


def _blocks_of(
    block: dict[str, Any],
    index: dict[str, str],
    unresolved: list[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in block.get("ss") or []:
        text = (s.get("t") or "").strip()
        if not text:
            continue
        out.append(
            {
                "text": text,
                "cites": _cites_of(s, index, unresolved),
                # 契約沒要求，但匯出檔要能指回系統裡的那一句，多這一鍵不傷前端。
                "sentence_id": s.get("id"),
            }
        )
    if not out:
        # `ty=="p"` 實測 `text` 為空、句子在 `ss`；但別的 block 型別可能反過來。
        # 兩邊都讀，才不會因為某一種區塊改了寫法就靜默掉字。
        text = (block.get("text") or "").strip()
        if text:
            out.append({"text": text, "cites": [], "sentence_id": None})
    return out


def build_sections(payload: dict[str, Any], artifact_id: str | None = None) -> dict[str, Any]:
    """`build_payload()` 的輸出 → 契約 §4.4 的產出視圖。

    回傳額外帶兩個契約沒列的鍵，兩個都是為了「不要靜默」：

    - `meta`：`ty=="title"` / `ty=="meta"` 的抬頭行（案號／案由／訴願人）。
      匯出檔的抬頭要用，丟掉的話承辦人拿到一份不知道是哪一案的文件。
    - `unresolved`：label 查不到的 ref id。空 list 才代表「每個 cite 都可回溯」，
      契約 §4.4 那句「來源控管文案只有在每個 cite 真的可回溯時才顯示」要靠它判斷。
    - `notices`：出處與執行檔位的揭露（`payload["provenance"]`）。**匯出檔一定要帶著它**：
      一份寫著「訴願決定書」的 `.docx` 一旦離開系統在辦公室裡流傳，畫面上的那條
      「合成測資／fixture 重播」橫幅就不在了。分層誠實只在系統裡成立，等於沒有成立
      （CONSTITUTION §1、§3）。
    """
    doc = payload.get("doc") or []
    index = _label_index(payload)
    unresolved: list[str] = []

    title = ""
    meta: list[str] = []
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for block in doc:
        ty = block.get("ty")
        text = (block.get("text") or "").strip()
        if ty == TY_TITLE:
            title = title or text
            continue
        if ty == TY_META:
            if text:
                meta.append(text)
            continue
        if ty == TY_HEADING:
            # `role` 從 `doc[]` 一路帶到 `sections[]`，renderer 才分得出這一段是
            # 主文／事實／理由，還是附錄、落款、教示條款。**沒有 role 就不放這個鍵**：
            # 契約 §4.4 沒有它，多一個永遠是 None 的鍵等於改了回應形狀。
            #
            # `h` 是空字串的 section 是**刻意的**：公文的落款與教示條款各自成段、
            # 但沒有標題。它們一定帶 `role`，所以「空標題」與「title／meta 被誤當成
            # section」（那種既沒 role、blocks 也是空的）分得開。
            current = {"h": text, "blocks": []}
            role = block.get("role")
            if role:
                current["role"] = role
            sections.append(current)
            continue
        blocks = _blocks_of(block, index, unresolved)
        if not blocks:
            continue
        if current is None:
            # 沒有先出現標題就有內文：開一個無名 section，不丟句子。
            current = {"h": "", "blocks": []}
            sections.append(current)
        current["blocks"].extend(blocks)

    # 只有標題沒有內文的 section 留著（那是文件結構的一部分），
    # 但整份都沒有內文時由呼叫端判斷要不要匯出（REQ-EXPORT-004 AC4）。
    cite_count = sum(len(b["cites"]) for sec in sections for b in sec["blocks"])

    return {
        "artifact_id": artifact_id,
        "title": title or "訴願決定書草稿",
        "run_id": payload.get("run_id"),
        "case_id": payload.get("case_id"),
        "state": payload.get("state"),
        "meta": meta,
        "sections": sections,
        "cite_count": cite_count,
        "unresolved": sorted(set(unresolved)),
        # 契約 §4.4 的 `X-Unresolved-Cites` 是「對不回本案 laws／references 的引註**數**」。
        # **是出現次數不是相異 id 數**——跟 `cite_count`（實際帶出的引註數）同一個計法，
        # 兩個數字要能直接相比：「14 處引註，其中 3 處對不回來」。
        # 相異 id 數會讓同一個對不上的編號被引三次時只算 1，警示強度被稀釋。
        "unresolved_count": len(unresolved),
        "notices": _notices(payload),
        "dataset_scope": dataset_scope(payload),
    }


# 匯出檔抬頭要帶的揭露，依重要性排序。`provenance` 的鍵由 `settings.provenance()` 決定；
# 缺鍵就跳過，不補寫（不編造是紅線，缺了就是缺了）。
_NOTICE_KEYS: tuple[str, ...] = ("banner", "execution_note", "retrieval_note")


def _notices(payload: dict[str, Any]) -> list[str]:
    prov = payload.get("provenance") or {}
    out: list[str] = []
    for key in _NOTICE_KEYS:
        text = (prov.get(key) or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def dataset_scope(payload: dict[str, Any]) -> str:
    """「引用驗證涵蓋到哪裡」。放在匯出檔的引註對照底下——
    只列引註不說驗證範圍，讀的人會以為每一條都經過完整查核。"""
    return ((payload.get("provenance") or {}).get("dataset_scope") or "").strip()


def has_body(view: dict[str, Any]) -> bool:
    """有沒有任何一句正文。用來把「run 存在但還沒生成草稿」跟「草稿是空的」分開——
    前者該回 409 叫人先生成草稿，不是回一份空白檔說成功。"""
    return any(sec.get("blocks") for sec in view.get("sections") or [])


def citation_lines(view: dict[str, Any]) -> list[tuple[str, str]]:
    """文末「引註對照」用的 (id, label) 清單，依首次出現順序去重。

    抽成函式是因為 `.docx` 與 `.pdf` 兩條組檔路徑都要它，各寫一份必然漂移。
    """
    seen: dict[str, str] = {}
    for sec in view.get("sections") or []:
        for b in sec.get("blocks") or []:
            for c in b.get("cites") or []:
                seen.setdefault(c["id"], c["label"])
    return list(seen.items())


def inline_marks(block: dict[str, Any]) -> str:
    """行內引註標註：`[L3] [I1]`。沒有引用回空字串。"""
    return " ".join(f"[{c['id']}]" for c in block.get("cites") or [])
