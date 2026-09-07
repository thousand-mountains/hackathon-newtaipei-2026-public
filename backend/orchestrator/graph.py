"""Deterministic 狀態機：N1 → N2 → N3 → N4 → N5 → N6（architecture §4.1、§4.2）。

**編排是程式，不是 agent。** 節點順序、狀態轉換、降級路由全部寫死在這裡，
沒有任何一步由模型決定要不要跑、跑哪個。這是「規則引擎零 LLM 依賴」在編排層的體現。

每次轉換前 assert 不變式（`state.py` 的 `assert_*_invariant`），失敗即中止並記錄。
"""
from __future__ import annotations

import copy
import json
import pathlib
import time
import uuid
from typing import Any, Callable

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
    DEADLINE_INPUT_FIELDS,
    NODE_TO_AGENTS,
    PROVENANCE,
    SUBSTANTIVE_TYPES,
    SYNTHETIC_DIR,
    TOKEN_NOTE,
    UNCONFIRMED_DEADLINE_INPUT_CAVEAT,
    load_snapshot,
    retriever_kind,
    run_mode,
)
from backend.intake.uploads import list_upload_cases, load_upload_case
# 編排層知道 model id 是誰（要寫進 run_meta），節點層不知道——
# `backend.llm` 的禁令是給 N2/N3/N4/N6 的（CONSTITUTION §4），graph 不在禁單內。
# client.py 對 strands 有模組頂層 try/except 守衛，沒裝也 import 得動。
from backend.llm.client import model_ids
from backend.nodes import n1_extract, n2_classify, n3_procedure, n4_retrieval, n5_draft, n6_gate
from backend.orchestrator.narrative import (
    conclusion_block_criterion,
    merge_agent_narrative,
    summary_line,
)
from backend.orchestrator.runstore import save_run
from backend.orchestrator.state import CaseState, NodeCtx, utc_now_iso
from backend.retrieval.kb import build_retriever

NODE_ORDER = ("n1", "n2", "n3", "n4", "n5", "n6")
STATE_AFTER = {
    "n1": "EXTRACTED",
    "n2": "CLASSIFIED",
    "n3": "SCREENED",
    "n4": "RETRIEVED",
    "n5": "DRAFTED",
    "n6": "VERIFIED",
}

# 續跑時要從 base_state 搬過來的欄位，**按「即將要跑的節點」切**：
# key 是節點名，value 是「進這個節點之前必須已經在 state 裡」的欄位
# （也就是它的上一個節點寫出來的東西）。從 `from_node` 續跑時，
# 把 NODE_ORDER[:index(from_node)+1] 的欄位全部深拷貝過來，就等於
# 「上游原樣沿用、從 from_node 起重算」。
# 深拷貝不是保險起見：base_state 可能還被呼叫端（或另一次續跑）拿著，
# 共用同一份 dict 會讓這次執行的結果反寫回上一次的紀錄。
UPSTREAM_FIELDS: dict[str, tuple[str, ...]] = {
    "n1": (),
    "n2": (
        "intake",
        "intake_conf",
        "intake_origin",
        "intake_confirmed",
        "low_conf_fields",
        "facts_excerpt",
    ),
    "n3": ("classification",),
    "n4": ("screen",),
    "n5": ("retrieval",),
    "n6": ("draft",),
}
# 續跑可覆寫的參數白名單。**白名單制**：外面（HTTP body）能改的只有這一個，
# 不讓呼叫端用 overrides 這條路徑把任意值塞進節點。
OVERRIDE_WHITELIST = ("n4_query",)


class CaseNotFound(FileNotFoundError):
    pass


def list_synthetic_cases(data_dir: pathlib.Path | None = None) -> list[str]:
    d = data_dir or SYNTHETIC_DIR
    return sorted(p.stem for p in d.glob("synthetic-*.json"))


def load_case(case_id: str, data_dir: pathlib.Path | None = None) -> dict[str, Any]:
    """依前綴分流案例來源。

    - `synthetic-`：`backend/data/synthetic/` 的合成案例檔（進 git）。
    - `upload-`：承辦人上傳的卷證（`backend/output/uploads/`，**不進 git**）。
    - 其他一律拒絕：CONSTITUTION §3／§6，真實競賽資料不由本流程讀取，
      連 id 都不接受，避免有人把真實資料丟進來就跑得起來。
    """
    if case_id.startswith("upload-"):
        return load_upload_case(case_id)
    if not case_id.startswith("synthetic-"):
        raise ValueError(
            f"案例 id {case_id!r} 不是 synthetic- 或 upload- 前綴。本系統只處理合成測資與承辦人上傳的卷證，"
            f"真實競賽資料不進 git、不由本流程讀取（CONSTITUTION §3、§6）。"
        )
    d = data_dir or SYNTHETIC_DIR
    p = d / f"{case_id}.json"
    if not p.exists():
        raise CaseNotFound(f"找不到合成案例 {p}。現有案例：{', '.join(list_synthetic_cases(d)) or '（無）'}")
    return json.loads(p.read_text(encoding="utf-8"))


def list_cases(data_dir: pathlib.Path | None = None) -> dict[str, list[str]]:
    """兩種來源分開列，不混成一份看不出差別的清單。"""
    return {"synthetic": list_synthetic_cases(data_dir), "uploaded": list_upload_cases()}


def digest_from_state(state: CaseState) -> str:
    """N2／N3 的案情摘要來源：N1 抽出的事實段原文 ＋ intake.note。

    合成案例有預先寫好的 `case_digest`（fixture 重播用）；上傳案沒有，
    分類與爭點偵測只能吃 N1 這一次抽出來的東西——**同一份抽取結果，不另外生一份**。
    純字串拼接，零 LLM（CONSTITUTION §4）。
    """
    parts = [str(x.get("text") or "") for x in (state.facts_excerpt or [])]
    parts.append(str((state.intake or {}).get("note") or ""))
    return " ".join(p for p in parts if p).strip()


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
    *,
    base_state: CaseState | None = None,
    from_node: str = "n1",
    overrides: dict[str, Any] | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    persist: bool = True,
    run_id: str | None = None,
) -> CaseState:
    """把一個合成案例跑完六個節點，回傳終態 CaseState。

    `confirmed_intake`：承辦人在收文頁確認過的 intake 欄位。**不給就是沒人確認過**，
    此時期間結果不得用來解除結論封鎖（判斷卡 7，見 `gate/lamps.requires_human_conclusion`）。

    續跑（spec 2026-09-07 §5.5）：`base_state` + `from_node` → 上游節點結果原樣複製，
    從 `from_node` 依序跑到 N6。**不能停在中間**：N6 永遠最後重跑，所以續跑產生的
    終態一定經過守門，不會出現「草稿改過但沒重驗引用」的狀態。
    `from_node != "n1"` 而沒給 `base_state` 是錯誤（沒有上游可沿用）。
    `overrides` 白名單只有 `n4_query`；`on_event(kind, data)` 供 SSE；`persist=False` 供測試。
    """
    if from_node not in NODE_ORDER:
        raise ValueError(f"from_node 必須是 {NODE_ORDER} 之一，實得 {from_node!r}")
    if from_node != "n1" and base_state is None:
        raise ValueError("from_node 不是 n1 時必須提供 base_state（base_run_id）")
    overrides = dict(overrides or {})
    bad = [k for k in overrides if k not in OVERRIDE_WHITELIST]
    if bad:
        raise ValueError(f"overrides 只接受 {OVERRIDE_WHITELIST}，實得 {bad}")
    start_idx = NODE_ORDER.index(from_node)
    if confirmed_intake and start_idx > 1:
        # 從 N3 以後續跑代表 N3 已經拿舊的 intake 算過了，這時候才送確認欄位，
        # 值不會進到任何計算裡——**寧可拒絕，也不要收下一個不生效的確認**。
        raise ValueError("confirmed_intake 只能搭配 from_node 為 n1 或 n2")

    fixture = load_case(case_id, data_dir)
    mode = mode or run_mode()
    if case_id.startswith("upload-") and mode == "fixture":
        raise ValueError(
            "上傳案件沒有可重播的 fixture，只能在 RUN_MODE=bedrock 執行；fixture 模式請選 synthetic- 案例。"
        )
    snapshot = load_snapshot()
    digest = fixture.get("case_digest", "")

    state = CaseState(case_id=case_id, run_mode=mode)
    # architecture §6.1 2a：POST /runs 的回傳值是 run_id。續跑會產生**新的** run_id
    # （續跑是一次新的執行，不是覆寫舊紀錄），舊的那次靠 `run_meta.base_run_id` 指回去。
    # `run_id` 參數是給 API 端用的：202 要先把 id 回出去，才有東西可以查進度。
    state.run_id = run_id or f"run-{case_id}-{uuid.uuid4().hex[:12]}"
    state.files = list(fixture.get("files") or [])
    # 案件層的來源聲明（合成／上傳）。build_payload 會疊在服務層的 PROVENANCE 上，
    # 讓前端橫幅講的是**這一件**卷證從哪裡來，不是服務預設的那一句。
    state.provenance = dict(fixture.get("provenance") or {})
    if base_state is not None:
        if base_state.case_id != case_id:
            raise ValueError(f"base_run 是 {base_state.case_id} 的執行結果，不能接到 {case_id}")
        # 跨模式續跑會產生混血結果：N1–N4 是 fixture 重播、N5 是真模型，
        # 而 run_meta.run_mode 只寫得下一個值 → 那份 run_meta 對自己的來源說謊
        # （CONSTITUTION §1）。伺服器改設定重啟後接舊 run 就會踩到，一律拒絕。
        if base_state.run_mode != mode:
            raise ValueError(
                f"base_run 是 {base_state.run_mode} 模式的結果，不能接到 {mode} 模式續跑"
                f"（run_meta 會變成半重播半即時，說不清楚哪一段是模型寫的）"
            )
        for node in NODE_ORDER[: start_idx + 1]:
            for f in UPSTREAM_FIELDS[node]:
                setattr(state, f, copy.deepcopy(getattr(base_state, f)))
        # history 接續而不是重來：讀的人要看得出這一次是從哪一次的哪個節點接上去的
        state.history = list(base_state.history) + [f"(resume from {from_node}, base {base_state.run_id})"]
    # 相似案檢索器由編排層注入（architecture §10：retrieval 是共用元件，不是 N4 的內部實作）。
    # `RETRIEVER=lawtable_only`（預設）回 None，N4 通道 B 維持誠實回空。
    # kb.py 是檢索元件、不 import backend.llm，所以 N4 拿到它仍符合「規則引擎零 LLM 依賴」。
    retriever = build_retriever(retriever_kind(), exclude_case=fixture.get("exclude_case"))
    ctx = NodeCtx(run_mode=mode, snapshot=snapshot, retriever=retriever)

    run_started = time.perf_counter()
    node_timings: dict[str, int] = {}
    degraded: list[dict[str, Any]] = []
    agents: dict[str, Any] = {}
    rerun_nodes = set(NODE_ORDER[start_idx:])
    if base_state is not None:
        # 續跑的 agents[] 與 degraded[] 要含**沒有重跑的那幾個節點**的內容。
        # 不帶過來的話，前端的 agent 卡片會只剩重跑的那兩張，看起來像
        # 「N1～N4 沒跑過」——那是對已經發生過的事說謊（CONSTITUTION §1）。
        # 屬於重跑節點的 agent key 先刪掉再讓迴圈填回：留著會是上一次的舊敘事。
        rerun_agent_keys = {a for n in rerun_nodes for a in NODE_TO_AGENTS.get(n, [])}
        agents = {
            k: v
            for k, v in copy.deepcopy(base_state.agents_narrative or {}).items()
            if k not in rerun_agent_keys
        }
        degraded = [
            copy.deepcopy(d)
            for d in (base_state.run_meta or {}).get("degraded", [])
            if d.get("node") not in rerun_nodes
        ]

    def emit(kind: str, data: dict[str, Any]) -> None:
        """事件是旁路，**不准影響流水線**：callback 自己炸掉就吞掉。

        SSE 端（`api/`）自己記錯；讓它的例外往上冒會有兩個後果——
        一個壞掉的訂閱者能讓整次分析失敗，而且 `except` 分支會再呼叫同一個壞
        callback，第二次的例外還會把真正的錯誤蓋掉。
        """
        if on_event is None:
            return
        try:
            on_event(kind, {"run_id": state.run_id, **data})
        except Exception:  # noqa: BLE001 — 見上：訂閱者的錯不該變成分析的錯
            pass

    # N4 的查詢句由 N1／N2／N3 的結果決定（`n4_retrieval.build_query()`）。
    # 這裡**刻意不再蒐集草稿引用**：舊版把 fixture 草稿即將引用的法條餵給 N4，
    # 那讓「引用一定查得到」變成必然——查什麼是照著答案要引用什麼倒著填的，
    # 檢索佐證的是自己。2026-09-05 Ci 拍板改獨立檢索。
    # 續跑時起始狀態＝上一個節點跑完該有的狀態，不從 EXTRACTING 重來
    state.transition("EXTRACTING" if start_idx == 0 else STATE_AFTER[NODE_ORDER[start_idx - 1]])
    node = from_node  # 例外處理要報是哪一個節點炸的
    try:
        for node in NODE_ORDER[start_idx:]:
            emit("node_start", {"node": node, "agents": NODE_TO_AGENTS.get(node, [])})
            if node == "n2":
                # 確認欄位在進 N2 之前一定套用過（不論這一次有沒有跑 N1）：
                # N3 的程序判斷要看得到 origin 已翻成 human。從 n1 起跑時這是第二次呼叫，
                # 冪等（同樣的值、同樣的 origin），沒有副作用。
                _apply_confirmed_intake(state, confirmed_intake)
            result = _dispatch(node, state, ctx, fixture, digest, overrides)
            if node == "n1":
                # 也在 N1 之後立刻套一次：N1 降級會 break 在下面，
                # NEEDS_INPUT 的終態同樣要帶著承辦人確認過的欄位（維持既有行為）。
                _apply_confirmed_intake(state, confirmed_intake)
            node_timings[node] = result.elapsed_ms
            merge_agent_narrative(agents, node, result.narrative, result.degraded, result.degrade_reason)
            if result.degraded:
                degraded.append(
                    {"node": node, "reason": result.degrade_reason, "agents": NODE_TO_AGENTS.get(node, [])}
                )
            emit("node_done", {"node": node, "elapsed_ms": result.elapsed_ms, "degraded": result.degraded})
            # N1 信心不足 → NEEDS_INPUT，狀態機停在這裡等人工補齊（architecture §4.2）
            if node == "n1" and result.degraded:
                state.transition("NEEDS_INPUT")
                break
            state.transition(STATE_AFTER[node])
    except Exception as e:  # noqa: BLE001 — 事件要發得出去，例外照原樣往上拋
        emit("run_failed", {"node": node, "error": f"{type(e).__name__}: {e}"})
        raise

    state.agents_narrative = agents
    state.run_meta = {
        "run_id": state.run_id,
        "base_run_id": base_state.run_id if base_state is not None else None,
        "from_node": from_node,
        "overrides": overrides,
        "run_id_note": (
            "支援 base_run_id + from_node 續跑；同一份 body 重送仍會產生新的 run_id"
            "（不做冪等去重）。"
        ),
        "run_mode": mode,
        "started_at": utc_now_iso(),
        "elapsed_ms": int((time.perf_counter() - run_started) * 1000),
        "node_timings": node_timings,
        "degraded": degraded,
        # 沒有實際呼叫任何模型，就不給 model id（不腦補）。續跑時只報**這一次真的重跑過**
        # 的 LLM 節點：`from_node=n5` 時 N1 根本沒跑，填了就是虛報（覆核 I-4）。
        "model_ids": _model_ids_if_live(mode, rerun_nodes),
        "model_ids_note": _model_ids_note(mode, rerun_nodes),
        "kb_snapshot_date": snapshot.get("generated"),
        "final_state": state.state,
    }
    state.run_meta["summary"] = summary_line(state.run_meta)
    if persist:
        # 一次執行**一定**以 run_done 或 run_failed 結束。存檔失敗（磁碟滿、
        # 或呼叫端給的 run_id 不合 RUN_ID_RE）也是失敗，訂閱端不能只是等不到下一個事件。
        try:
            save_run(state)
        except Exception as e:  # noqa: BLE001 — 事件要發得出去，例外照原樣往上拋
            emit("run_failed", {"node": None, "error": f"{type(e).__name__}: {e}"})
            raise
    emit("run_done", {"final_state": state.state})
    return state


# 哪個 LLM 節點對應 model_ids 的哪一個 key（graph 是唯一知道這件事的地方）
LLM_NODE_MODEL_KEYS = (("n1", "extract", "抽取結果"), ("n5", "draft", "草稿"))


def _model_ids_if_live(mode: str, rerun_nodes: set[str]) -> dict[str, Any] | None:
    """live 檔位才報 model id，而且**只報這一次真的重跑過的節點**。

    fixture 沒呼叫任何模型，報了就是謊報（CONSTITUTION §1）；同理，續跑時沒有重跑的
    LLM 節點也沒有呼叫任何模型——舊版只看 `mode`，`from_node=n5` 續跑照樣把
    `BEDROCK_MODEL_ID_EXTRACT` 的值填進 `model_ids.extract`（覆核 I-4）。
    沒跑過就是 None，理由寫在 `model_ids_note`。
    """
    if mode != "bedrock":
        return None
    ids = model_ids()
    out: dict[str, Any] = {"provider": ids.get("provider")}
    for node, key, _label in LLM_NODE_MODEL_KEYS:
        out[key] = ids.get(key) if node in rerun_nodes else None
    return out


_NOT_AWS_NOTE = (
    "MODEL_PROVIDER={provider}：本次執行呼叫的**不是** AWS 服務提供之基礎模型，"
    "輸出僅供開發期調 prompt，**不得**作為驗收證據或 demo 內容（賽制限 AWS 基礎模型）。"
)


def _model_ids_note(mode: str, rerun_nodes: set[str]) -> str | None:
    """把 `model_ids` 裡的 None 講清楚是「沒跑」還是「沒有模式」。

    另外：provider 不是 bedrock 時要在這裡大聲說出來。`run_mode` 仍然是 `bedrock`
    （程式路徑確實走 live 那條），光看 `run_mode` 分不出模型是誰家的。
    """
    if mode != "bedrock":
        return f"本次執行模式 {mode} 未呼叫任何基礎模型，故無 model id。"
    provider = model_ids().get("provider")
    prefix = "" if provider == "bedrock" else _NOT_AWS_NOTE.format(provider=provider) + " "
    skipped = [
        f"續跑未重跑 {node.upper()}，{label}沿用 base_run"
        for node, _key, label in LLM_NODE_MODEL_KEYS
        if node not in rerun_nodes
    ]
    tail = "；".join(skipped) + "，故該節點不填 model id。" if skipped else ""
    return (prefix + tail) or None


def _dispatch(node, state, ctx, fixture, digest, overrides=None):
    overrides = overrides or {}
    if node == "n1":
        return n1_extract.run(state, ctx, case_fixture=fixture)
    if node == "n2":
        return n2_classify.run(state, ctx, digest=digest or digest_from_state(state))
    if node == "n3":
        return n3_procedure.run(state, ctx, digest=digest or digest_from_state(state))
    if node == "n4":
        # cited_laws 刻意不傳：N4 自己從 state（N1/N2/N3 的結果）組查詢句。
        # 唯一的例外是承辦人在續跑時明講的查詢詞（`overrides.n4_query`）——
        # 那是人指定的，不是從草稿引用倒著填回去的。
        # **兩條通道都要收到**（spec §5.5）：`cited_laws` 進通道 A（法規查表）、
        # `extra_case_terms` 進通道 B（相似案）。只傳前者的話，畫面上緊鄰相似案的
        # 那個輸入框其實只會影響法條清單（2026-09-07 覆核 I-5）。
        q = overrides.get("n4_query")
        return n4_retrieval.run(
            state, ctx, cited_laws=[q] if q else None, extra_case_terms=[q] if q else None
        )
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

    # HACK-S-17：逐句的 why 之外，這一層也放一條，讓人掃一眼就看到。
    # **與引擎 caveats 分開**：引擎那些是法規／曆法的極限，這條是資料來源的極限，
    # `why` 不同，混在一起會讓「這是誰說的」變模糊。
    unconfirmed_inputs = [
        f for f in (state.screen.get("unconfirmed_procedural_fields") or [])
        if f in DEADLINE_INPUT_FIELDS
    ]
    if unconfirmed_inputs:
        tiers[TIER_HUMAN].append(
            {
                "id": None,
                "slot": "caveat",
                "t": UNCONFIRMED_DEADLINE_INPUT_CAVEAT.format(fields="、".join(unconfirmed_inputs)),
                "l": "y",
                "why": "輸入來源檢查：期間算式的輸入欄位 intake_origin 仍為 llm",
                "origin": "rule",
            }
        )

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
        # 服務層聲明打底，案件層（合成案例檔／上傳案的 case.json）覆蓋。
        # **不是整塊換掉**：合成案例的 provenance 區塊沒有 banner／dataset_scope，
        # 整塊換會讓前端橫幅從「合成測資：……」掉回泛稱的「示範案件」——
        # 那是誠實度的倒退，不是重構。
        "provenance": {**PROVENANCE, **(state.provenance or {})},
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
