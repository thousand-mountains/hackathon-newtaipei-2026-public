"""N1 抽取節點（LLM 位置：fixture 重播 / bedrock 即時抽取 兩條分支）。

紅線（CONSTITUTION §1）：這個節點永遠不准靜默回一份看起來像模型抽出來、
其實是寫死的資料。三向分流各自說清楚自己是什麼：

- `fixture`：重播合成案例的 `extraction` 區塊。`conf` 是 fixture 裡寫好的數字，
  不是本次執行實際量到的信心，`narrative` 會把這件事寫出來。
- `bedrock`：把 `case_fixture["documents"]` 的卷證全文餵給
  `backend.llm.client.extract_intake`。失敗直接往上拋 `LLMError`，
  **不吞掉改吐 fixture**——降級成重播而不講，就是最典型的分層誠實違規。
- 其餘（如 `local`）：仍未實作，`ctx.require_fixture()` 明確 raise NotImplementedError。

`backend.llm.client` 在模組頂層 import（spec 2026-09-07 D8）：它自己用 try/except
守住 strands，沒裝也 import 得動，所以本檔不必把 import 藏進 bedrock 分支。
兩條分支的輸出形狀相同（含 `data["generation"]` 說明這批欄位是怎麼來的）。

註：N1 依賴 backend.llm 是設計如此；**零 LLM 依賴的紅線管的是 N2/N3/N4/N6**
（CONSTITUTION §4，由 run_all 的 scan_llm_import_graph 把關），那四個節點都不 import N1。
"""
from __future__ import annotations

import time
from typing import Any

from backend.llm import client as llm_client
from backend.orchestrator.state import CaseState, NodeCtx, NodeResult

# 必填欄位與信心門檻（architecture §3.1）。門檻 0.80 是假設值，待真實資料實測校準。
REQUIRED_FIELDS = ("no", "type", "d2", "service_method")
CONF_THRESHOLD = 0.80


def run(state: CaseState, ctx: NodeCtx, case_fixture: dict[str, Any] | None = None) -> NodeResult:
    started = time.perf_counter()
    if case_fixture is None:
        raise ValueError("N1 需要 case_fixture（合成案例檔內容）")

    if ctx.run_mode == "fixture":
        extraction = case_fixture.get("extraction")
        if not extraction:
            raise ValueError(f"合成案例 {case_fixture.get('id')!r} 缺 extraction 區塊")
        payload = {
            "intake": dict(extraction.get("intake", {})),
            "conf": dict(extraction.get("conf", {})),
            "quotes": dict(extraction.get("quotes", {})),
            "facts_excerpt": list(extraction.get("facts_excerpt", [])),
        }
        generation = {"mode": "fixture_replay", "model_id": None, "usage": None}
        mode_log = ["離線重播（fixture）：信心值為合成案例既有標註，非本次實測", "y"]
        extra_logs = None
    elif ctx.run_mode == "bedrock":
        docs = case_fixture.get("documents") or []
        if not docs:
            raise ValueError(
                f"案例 {case_fixture.get('id')!r} 缺 documents 區塊（bedrock 模式需要卷證）"
            )
        text_parts: list[str] = []
        pdfs: list[tuple[str, bytes]] = []
        route: dict[str, str] = {}
        notes: list[str] = []
        for i, d in enumerate(docs, 1):
            name = d.get("n")
            if not name:
                raise ValueError(
                    f"案例 {case_fixture.get('id')!r} 的第 {i} 份卷證缺檔名（documents[].n）；"
                    f"沒有檔名就無法在敘述裡交代這一份是怎麼讀的"
                )
            # 合成案例的 documents[] 沒有 kind 欄位——它們都是純文字，缺省視為 txt
            kind = d.get("kind") or "txt"
            route[name] = kind
            notes.extend(f"{name}：{n}" for n in (d.get("notes") or []))
            if kind == "pdf_visual":
                blob = d.get("bytes")
                if not blob:
                    # 送 b"" 過去，模型「讀」的是一份不存在的文件，而 input_route 仍會記成
                    # pdf_visual——等於宣稱視覺讀過了。寧可中止（CONSTITUTION §1）。
                    raise ValueError(
                        f"卷證 {name!r} 標為 pdf_visual 卻沒有檔案內容（documents[].bytes 為空）；"
                        f"不送空檔案給模型，也不假裝讀過"
                    )
                # 序號前綴：Bedrock 的 document name 只收 ASCII，多份中文檔名會被清成同一個字串，
                # 模型分不出哪份是哪份。原檔名保留在 input_route，前端要顯示的是那一份。
                pdfs.append((f"{i}-{name}", blob))
            else:
                text_parts.append(f"《{name}》\n{d.get('text') or ''}")
        out = llm_client.extract_intake("\n\n".join(text_parts), pdf_documents=pdfs or None)  # LLMError 直接往上拋
        payload = {k: out[k] for k in ("intake", "conf", "quotes", "facts_excerpt")}
        generation = {
            "mode": "bedrock_live",
            "model_id": out["model_id"],
            "usage": out["usage"],
            # 每一份卷證走哪一層、為什麼——**不能只回抽出來的欄位而不交代來源**
            "input_route": route,
            "route_notes": notes,
        }
        mode_log = [f"模型即時抽取：{out['model_id']}，信心值為模型自報", ""]
        extra_logs = [[f"卷證輸入：{'、'.join(f'{n}（{k}）' for n, k in route.items())}", ""]]
        if pdfs:
            extra_logs.append([f"{len(pdfs)} 份 PDF 文字抽取不足，改由模型視覺讀取整份頁面", "y"])
        extra_logs.extend([[n, "y"] for n in notes])
    else:
        ctx.require_fixture("N1 抽取節點")  # 維持既有 raise 訊息
        raise AssertionError("unreachable")

    return _finish(state, started, payload, generation, mode_log, extra_logs)


def _finish(
    state: CaseState,
    started: float,
    payload: dict[str, Any],
    generation: dict[str, Any],
    mode_log: list[str],
    extra_logs: list[list[str]] | None = None,
) -> NodeResult:
    """兩條分支共用的收尾：寫 state、算降級、組 NodeResult。

    `extra_logs` 讓呼叫端補幾行只有該分支說得出來的敘述（例如卷證來源路由），
    接在既有 log 之後，不影響共用的三行。
    """
    intake = payload["intake"]
    conf = payload["conf"]
    quotes = payload["quotes"]
    facts_excerpt = payload["facts_excerpt"]

    low_conf = [f for f in REQUIRED_FIELDS if conf.get(f, 0.0) < CONF_THRESHOLD]
    missing = [f for f in REQUIRED_FIELDS if f not in intake or intake.get(f) in (None, "")]

    state.intake = intake
    state.intake_conf = conf
    # 每欄同時寫 origin。人工改過的欄位由 /api/cases/{id}/intake 覆寫為 "human"。
    state.intake_origin = {k: "llm" for k in intake}
    state.low_conf_fields = low_conf
    state.facts_excerpt = facts_excerpt

    degraded = bool(low_conf or missing)
    reason = None
    if degraded:
        reason = (
            f"必填欄位信心不足或缺漏（低信心：{low_conf or '無'}；缺漏：{missing or '無'}），"
            f"需人工表單補齊後才能續跑"
        )

    elapsed = int((time.perf_counter() - started) * 1000)
    return NodeResult(
        ok=not degraded,
        data={
            "intake": intake,
            "conf": conf,
            "quotes": quotes,
            "low_conf_fields": low_conf,
            "facts_excerpt": facts_excerpt,
            # 事實段的唯一生產者是卷證原文摘錄；抓不到就留空交人工，不由 N5 生成
            "facts_excerpt_available": bool(facts_excerpt),
            # 這批欄位是重播還是模型即時抽的，連同 model_id／用量一起說清楚
            "generation": generation,
        },
        degraded=degraded,
        degrade_reason=reason,
        elapsed_ms=elapsed,
        narrative={
            "clerk": {
                "out": f"自卷證擷取 {len(intake)} 個欄位，事實段原文 {len(facts_excerpt)} 段。",
                "logs": [
                    [f"必填欄位 {len(REQUIRED_FIELDS)} 項，信心門檻 {CONF_THRESHOLD:.2f}", ""],
                    [
                        f"低信心欄位：{'、'.join(low_conf) if low_conf else '無'}",
                        "y" if low_conf else "",
                    ],
                    mode_log,
                    *(extra_logs or []),
                ],
            }
        },
    )
