"""聊天層與六節點之間的橋：`CaseState` → plain dict。

## 為什麼要有這一個檔

`docs/spec/2026-09-12-chat-honesty-lamps.md` §4.0 禁止 `backend/llm/chat.py` 依賴
編排層與六節點（乙案 AgentCore 容器裡沒有 runstore）。契約 v2 §0.1 的解法是
**注入 callable**。但「注入」本身不保證層級守得住：

- 直接注入 `run_case` → 聊天層沒有 import 語句（AST 檢查會綠），卻會拿到一個
  `CaseState` 物件並讀它的屬性。**層級形式上守住、實質被穿。**
- 注入一個回傳 **plain dict** 的 adapter → 聊天層從頭到尾只碰得到 dict，
  換一個沒有編排層的容器時，只要有人提供同形狀的 dict 就接得上。

所以 adapter 是這條禁令真正的守法，不是形式上的包裝。

## 為什麼不放在 `backend/api/chat.py`

放那裡它就永遠沒有測試——那個檔頂層 import fastapi，而這個 repo 的測試路徑
**零外部依賴**（`backend/tests/run_all.py` 有靜態掃描擋住第三方 import）。
`CaseState` → dict 這段轉換正是 Epic C 要接的東西，一行都沒被執行過是不行的。
放在這裡，`backend/tests/test_e2e.py` 用 fixture 檔位就跑得到真的 `run_case`。
"""
from __future__ import annotations

import pathlib
from typing import Any, Callable

from backend.dossier import store
from backend.orchestrator.graph import build_payload, run_case
from backend.orchestrator.runstore import load_run

#: 餵給聊天 agent 的卷內分區（`read_case` 的值域）。
#: 與 `backend/llm/chat.py` 的 `CASE_SECTIONS` 必須一致——**刻意不 import 過來**：
#: 那會讓編排層依賴聊天層，方向反了。兩邊靠 `backend/tests/test_chat.py` 的
#: `test_payload_sections_match_the_chat_layer_case_sections` 釘住。
PAYLOAD_SECTIONS = ("intake", "facts_excerpt", "screen", "laws", "cases")

def load_case_manifest(case_id: str, cases_dir: pathlib.Path | None = None
                       ) -> dict[str, Any]:
    """讀本案的卷宗清單。讀不到就回空 dict。

    **走 `store.load()`，不自己拼路徑也不自己 parse**（2026-09-12 改）。原本是自己
    `(CASES_DIR / case_id / "manifest.json").read_text()`，那讓讀寫兩端各有一份
    「檔案在哪、鍵叫什麼、值是什麼型別」的假設——而這種漂掉**完全沒有症狀**：
    讀端只會回空 dict，看起來就像「使用者還沒挑法規」。走同一支之後，路徑組法
    （`manifest_path` 含 `CASE_ID_RE` 白名單）與 `_normalise`（保證四個群組一定是 list）
    兩邊共用，寫端改形狀讀端跟著動。

    空 dict 的意思是「這個案子的卷宗清單是空的」，而 `generate_decision_draft`
    會據此擋下前置條件 3（契約 v2 §3.5.1）。**這個方向是刻意的**：寧可擋下來要
    使用者先查法規與案例，也不要生一份通篇引用都被清空的草稿——那個失敗看起來
    很像成功。

    **用 `load` 不用 `ensure`**：`ensure` 讀不到會**建一份檔**，而這是讀路徑——
    一次聊天追問不該在磁碟上長出東西。壞掉的 JSON、不合法的 case_id、頂層不是物件，
    全部當成空：半份清單比沒有清單更難查。
    """
    try:
        return store.load(case_id, cases_dir)
    except Exception:  # noqa: BLE001 — 見 docstring：讀不到一律當成「清單是空的」
        return {}


def pipeline_adapter(case_id: str, cases_dir: pathlib.Path | None = None,
                     **run_kwargs: Any) -> Callable[..., dict[str, Any]]:
    """把 `run_case()` 包成聊天層看得懂的 callable。

    回傳的 callable 的簽章就是 `backend/llm/chat.py` 的 `ChatTools.run_pipeline` 契約：

        run_pipeline(*, to_node=None, from_node="n1", base_run_id=None,
                     overrides=None, on_event=None) -> dict

    回傳的 dict：`{run_id, state, node_timings, cite_count, artifact_id,
    has_draft, sections}`。**沒有 `CaseState`，也沒有任何編排層的型別。**

    `run_kwargs` 供測試指定 `mode`／`data_dir`／`persist`，正式路徑不傳。
    """

    def run_pipeline(*, to_node: str | None = None, from_node: str = "n1",
                     base_run_id: str | None = None,
                     overrides: dict[str, Any] | None = None,
                     on_event: Any = None) -> dict[str, Any]:
        base_state = load_run(base_run_id) if base_run_id else None
        state = run_case(
            case_id,
            base_state=base_state,
            from_node=from_node,
            to_node=to_node or "n6",
            overrides=overrides or None,
            on_event=on_event,
            **run_kwargs,
        )
        payload = build_payload(state)
        run_meta = payload.get("run_meta") or {}
        # 把這次 run 登記進卷宗（`latest_run_id` ＋ 有句子時一筆 artifact）。
        # **呼叫的是 `POST /runs` 用的同一支**，不是 chat 自己寫一份——
        # 2026-09-12 整合時發現登記只接在 HTTP 那條路上，而契約 §0.1 說前端只打 chat，
        # 於是唯一會登記的路徑正好是前端不會走的那一條：右欄「答辯書與產出」永遠空的、
        # 下一輪 chat 也沒有 `latest_run_id` 可帶。
        # 登記失敗不影響這次執行（`record_run` 自己吞掉並印警告），所以不包 try。
        artifact_id = store.record_run(case_id, payload, cases_dir=cases_dir)
        return {
            "run_id": payload.get("run_id"),
            "state": run_meta.get("final_state") or payload.get("state"),
            "node_timings": dict(run_meta.get("node_timings") or {}),
            "cite_count": len(payload.get("citations") or []),
            # 真值：登記到的那一筆。沒有草稿可登記就是 None——
            # 隨手編一個 `art-…` 會讓前端拿去打一支查不到的端點。
            "artifact_id": artifact_id,
            "has_draft": bool(payload.get("doc")),
            "sections": {k: payload.get(k) for k in PAYLOAD_SECTIONS},
        }

    return run_pipeline


__all__ = ["PAYLOAD_SECTIONS", "load_case_manifest", "pipeline_adapter"]
