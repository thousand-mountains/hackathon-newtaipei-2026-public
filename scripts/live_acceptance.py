#!/usr/bin/env python3
"""live 驗收：對跑起來的服務逐條打 AC4–AC15，輸出 markdown 到 stdout。

    RUN_MODE=bedrock RETRIEVER=kb ... uvicorn backend.api.app:app --port 8123 &
    python3 scripts/live_acceptance.py --base http://127.0.0.1:8123 --timeout 600 \
        > docs/evidence/2026-09-07-bedrock-live/acceptance.md

只用 stdlib。每條 AC 印 ✅／❌／⏸ 與證據；任何 ❌ 以 exit 1 結束。
`--timeout` 是「等一次執行跑完」的上限（秒，預設 600）；逾時的證據欄會寫明是腳本等不及。

**⏸ 的意思**：腳本每次拿到 payload 都讀 `run_meta.run_mode` **與 `run_meta.model_ids.provider`**。
需要真模型／真 KB 的 AC（AC4、AC5、AC7、AC15）只有「`run_mode=bedrock` **而且**
provider 是 bedrock」才真驗，其餘一律標「未驗」而**不是**失敗——fixture 模式下這些 AC
根本沒有可驗的對象，判 ❌ 是說謊，判 ✅ 更是。
`MODEL_PROVIDER=openai` 時 `run_mode` 仍然是 `bedrock`（程式路徑確實走 live 那條），
但呼叫的不是 AWS 服務提供之基礎模型，**它的輸出不得充當賽制驗收證據**，所以照樣 ⏸。
不需要模型的 AC（AC6 封鎖、AC8／AC8b／AC9 續跑、health）在任何模式都照常真驗。
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable

OUT: list[str] = []
FAILS = 0

FIXTURE_PATH = "backend/data/synthetic/synthetic-ordinary-01.json"
# 等一次執行跑完的上限（秒）。bedrock 檔位六個節點要跑多久沒有人保證，所以可調（--timeout）。
DEFAULT_TIMEOUT = 600.0

# AC 名稱集中放，⏸ 與 ✅／❌ 兩條路徑印出來的列名才不會漂掉
AC4 = "AC4 N1 live：欄位數對齊 schema、origin=llm、conf 0–1、model_id 非空"
AC5 = "AC5 N5 live：每句 cite_ids ⊆ N4 ∪ 工具命中"
AC6 = "AC6 C 型封鎖成立（requires_human_conclusion／無 llm 結論／submit_allowed=false）"
AC7 = "AC7 KB recall：cases ≥ 3 且同案型 ≥ 3"
AC8 = "AC8 續跑 n5：N1–N4 相同、只跑 n5/n6"
AC8B = "AC8b from_node 無 base → 400"
AC9 = "AC9 確認後從 n2 續跑不重抽"
AC10 = "AC10 SSE 6 對 start/done + run_done（加值層）"
AC15 = "AC15 上傳 txt → N1 抽取與 fixture 一致、N2 案型一致"


def say(s: str) -> None:
    OUT.append(s)


def _cell(s: str) -> str:
    """表格欄位：吃掉會把 markdown 表格弄壞的字元。"""
    return s.replace("|", "\\|").replace("\n", " ").strip()


def check(name: str, ok: bool, evidence: str) -> None:
    global FAILS
    FAILS += 0 if ok else 1
    say(f"| {name} | {'✅' if ok else '❌'} | {_cell(evidence)} |")


def pending(name: str, evidence: str) -> None:
    """未驗：不計入 FAILS，也絕不能寫成通過。"""
    say(f"| {name} | ⏸ | {_cell(evidence)} |")


def guarded(fn: Callable[[], tuple[bool, str]]) -> tuple[bool, str]:
    """檢查式自己炸掉時，照實記成失敗並帶原始例外，不讓腳本整支中斷。"""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — 驗收腳本要把任何例外原樣端出來
        return False, f"檢查時例外：{type(e).__name__}: {e}"


def why_not_live(live_mode: str) -> str:
    """把「為什麼這條沒真驗」講清楚：是模式不對，還是模型不是 AWS 的。"""
    if live_mode.startswith("provider="):
        pv = live_mode.removeprefix("provider=")
        return (
            f"服務 run_mode=bedrock，但 MODEL_PROVIDER={pv}——呼叫的不是 AWS 服務提供之"
            "基礎模型，其輸出不得作為驗收證據（賽制限 AWS 基礎模型）"
        )
    return f"服務為 {live_mode} 模式，需 Bedrock 開通"


def live_ac(name: str, live_mode: str, fn: Callable[[], tuple[bool, str]]) -> None:
    """需要真模型／真 KB 的 AC：只有 bedrock 模式＋AWS provider 才真驗，其餘一律 ⏸。"""
    if live_mode != "bedrock":
        pending(name, f"未驗（{why_not_live(live_mode)}）")
        return
    ok, evidence = guarded(fn)
    check(name, ok, evidence)


def local_ac(name: str, fn: Callable[[], tuple[bool, str]]) -> None:
    """不需要模型就能驗的 AC：任何模式都真判 ✅／❌。"""
    ok, evidence = guarded(fn)
    check(name, ok, evidence)


def http(base: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        base + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except urllib.error.URLError as e:
        # 服務根本沒起來時不要噴 traceback，回一個假狀態碼讓該列記成 ❌
        return 0, f"連不上服務：{e.reason}"


def wait_run(base: str, rid: str, timeout: float, result_url: str) -> tuple[int, dict | str]:
    """輪詢到終態或逾時。

    逾時回的訊息要讓人看得出是**腳本等不及**、不是系統壞了——bedrock 檔位六個節點
    要跑多久沒有人保證，寫死一個上限然後把逾時記成失敗，就是拿腳本的耐性當系統的品質。
    """
    deadline = time.monotonic() + timeout
    while True:
        code, body = http(base, "GET", f"/api/runs/{rid}")
        if code != 409:
            return code, body
        if time.monotonic() >= deadline:
            return 408, {"error": f"等待逾時 {timeout}s：run 仍在執行，可稍後 GET {result_url}"}
        time.sleep(2)


def run(
    base: str, case: str, body: dict | None = None, timeout: float = DEFAULT_TIMEOUT
) -> tuple[int, dict | str, list[str]]:
    """啟動一次分析並拿回終態 payload。

    兩個檔位的回應形狀不同（app.py:304-345）：bedrock 回 202 ＋ ticket，之後輪詢；
    fixture 同步跑完直接回 200 ＋ payload。其他狀態碼原樣回傳，由呼叫端判成 ❌。
    """
    code, ticket = http(base, "POST", f"/api/cases/{case}/runs", body or {})
    if code == 200:
        return code, ticket, []
    if code != 202 or not isinstance(ticket, dict):
        return code, ticket, []
    rid = ticket.get("run_id")
    if not rid:
        # 202 卻沒給 run_id：這是服務端的契約破了，該記成該條 AC 的 ❌，
        # 不是讓 KeyError 把整張表一起帶走。
        return 502, {"error": "202 回應缺 run_id", "body": ticket}, []
    events: list[str] = []
    if ticket.get("events_url"):  # bedrock 檔位的 202 才有；沒有就純輪詢
        with urllib.request.urlopen(base + ticket["events_url"], timeout=300) as r:
            for raw_line in r:
                line = raw_line.decode().strip()
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
                if events and events[-1] in ("run_done", "run_failed", "timeout"):
                    break
    code, payload = wait_run(base, rid, timeout, ticket.get("result_url") or f"/api/runs/{rid}")
    return code, payload, events


def safe_run(
    base: str, case: str, body: dict | None = None, timeout: float = DEFAULT_TIMEOUT
) -> tuple[int, dict | str, list[str]]:
    """`run()` 的例外版本：任何例外變成該條 AC 的 ❌，不中斷整張表。"""
    try:
        return run(base, case, body, timeout)
    except Exception as e:  # noqa: BLE001 — 驗收腳本要把任何例外原樣端出來
        return 0, f"執行時例外：{type(e).__name__}: {e}", []


def post_multipart(base: str, path: str, files: list[tuple[str, str]]) -> tuple[int, dict | str]:
    """把 (filename, text) 們組成 multipart/form-data 打上去（AC15 的上傳建案）。"""
    boundary = "----ac15" + uuid.uuid4().hex
    body = b""
    for name, text in files:
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
            f"Content-Type: text/plain\r\n\r\n"
        ).encode() + text.encode() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        base + path,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def detail_of(body: dict | str) -> str:
    """把錯誤回應擠成一行人看得懂的字串。"""
    if isinstance(body, dict):
        return json.dumps(body.get("detail", body), ensure_ascii=False)
    return str(body)[:300]


CONFIRMABLE = ("d2", "d3", "service_method", "transit_days", "interested_party", "note")


def run_full(base: str, case: str, timeout: float):
    """跑完整六節點，回 `(code, 第一次的 payload, 完整那次的 payload, 兩次的事件合起來)`。

    送空 body 時，流程跑完 N1 就停在 `NEEDS_INPUT` 等人確認收文欄位
    （commit 31b3486 引入的**正確行為**）。所以「一次 POST 驗六節點」這個假設
    在這個系統上已經不成立——照舊寫法量到的是「N2–N6 沒跑」，不是「跑錯」。

    2026-09-12 踩到的具體代價：AC7 報 `cases=0` 被讀成檢索壞掉（實際是 N4 沒執行），
    而 AC5 因此**假通過**——沒有任何句子，「越界引用：無」當然成立。
    一條永遠綠的驗收比紅的更危險，它讓人以為那裡被守著。

    確認的值直接取 N1 抽出來的，等同承辦人「看過沒問題就按確認」；
    要驗的是流程能不能跑完，不是承辦人會不會改值。
    """
    code, p0, ev0 = safe_run(base, case, timeout=timeout)
    if code != 200 or not isinstance(p0, dict) or p0.get("state") != "NEEDS_INPUT":
        return code, p0, p0, ev0          # fixture 檔位一次跑完，沒有停等這一步
    confirmed = {k: p0["intake"][k] for k in CONFIRMABLE if k in p0.get("intake", {})}
    code2, p2, ev2 = safe_run(
        base, case,
        {"base_run_id": p0["run_id"], "from_node": "n2", "confirmed_intake": confirmed},
        timeout)
    return code2, p0, p2, (ev0 or []) + (ev2 or [])


def expected_intake_fields() -> int:
    """N1 該回幾個欄位——**問 schema，不寫死數字**。

    寫死會過時而且過時得很安靜：`INTAKE_FIELDS` 因為 §77-3 當事人適格加了
    `interested_party`／`respondent_name` 之後，這裡還釘著 12，AC4 就變成紅的，
    而系統其實完全正確（2026-09-12 實測：產出的 13 個欄位與 schema 逐一相符）。
    **一條會因為別處正常演進而變紅的驗收，比沒有這條更糟**——它會讓人去改沒壞的東西。

    用 ast 解析而不是 import：本腳本刻意只靠 stdlib（`backend.llm.schemas` 要 pydantic，
    而驗收要能在任何一台機器上對著跑起來的服務執行，不該綁開發環境的套件）。
    """
    src = (pathlib.Path(__file__).resolve().parents[1] / "backend/llm/schemas.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "INTAKE_FIELDS" for t in node.targets):
            return len(node.value.elts)
    raise RuntimeError("在 backend/llm/schemas.py 找不到 INTAKE_FIELDS")


def check_ac4(p: dict) -> tuple[bool, str]:
    fields = [k for k in p["intake"] if k not in ("auto_fields", "auto_toast")]
    all_llm = all(p["intake_origin"].get(k) == "llm" for k in fields)
    conf_ok = bool(p["intake_conf"]) and all(0 <= v <= 1 for v in p["intake_conf"].values())
    model_ok = bool((p["run_meta"].get("model_ids") or {}).get("extract"))
    want = expected_intake_fields()
    ok = all_llm and conf_ok and model_ok and len(fields) == want
    return ok, f"欄位數={len(fields)}/{want}、全 llm={all_llm}、conf 皆 0–1={conf_ok}、extract model_id 非空={model_ok}"


def check_ac5(p: dict) -> tuple[bool, str]:
    allowed = (
        {law["id"] for law in p["laws"]}
        | {c["id"] for c in p["cases"]}
        # 工具命中的來源：payload 頂層的 `refs`（2026-09-12 才補上輸出；
        # 在那之前 build_payload 沒有輸出 draft，這條 AC 只要 N5 引用 R* 就必紅）。
        # 舊 payload 的 `draft.refs` 一併接受，讓這支腳本對得起兩種版本。
        | {r["id"] for r in (p.get("refs") or [])}
        | {r["id"] for r in ((p.get("draft") or {}).get("refs") or [])}
    )
    bad = [
        c
        for b in p["doc"]
        for s in b["ss"]
        if s.get("origin") == "llm"
        for c in (s.get("cite_ids") or [])
        if c not in allowed
    ]
    return not bad, f"越界引用：{bad or '無'}"


def check_ac7(p: dict) -> tuple[bool, str]:
    """top-5 相似案裡同案型的筆數。

    兩個訊號取聯集，因為**兩批來源的案型藏在不同地方**（2026-09-12 實測）：

    - `category`（KB 側檔）：公開爬蟲那批唯一的案型來源，檔名只有「案號_結果」
    - 標題：賽方那批的檔名帶案型（`04.114年-違反廢棄物清理法事件-77(2)-…`）

    只看標題的話，公開那批**永遠算 0**——2026-09-12 首次量到的「cases=5、同案型=0」
    就是這樣來的（5 筆事後查 manifest 全是空污案，與測試案同型）。

    `norm` 吃掉「汙／污」的寫法差異：資料集兩種都有。
    比對用雙向 `in`，因為兩邊的粒度不同：`category` 可能是法規名（`空氣污染防制法`），
    `intake.type` 是案由（`違反空氣污染防制法事件`），前者是後者的子字串。
    """
    def norm(s: str) -> str:
        return (s or "").replace("汙", "污")

    want = norm(p["intake"]["type"])

    def same(c: dict) -> bool:
        cat = norm(c.get("category") or "")
        if cat and (cat in want or want in cat):
            return True
        return want in norm(c.get("t") or "")

    same_type = sum(1 for c in p["cases"] if same(c))
    ok = len(p["cases"]) >= 3 and same_type >= 3
    detail = [(c.get("t"), c.get("category"), c.get("outcome")) for c in p["cases"]]
    return ok, f"cases={len(p['cases'])}、同案型={same_type}、明細={detail}"


def check_ac6(code: int, pb: dict | str) -> tuple[bool, str]:
    if code != 200 or not isinstance(pb, dict):
        return False, f"取不到 payload：HTTP {code} {detail_of(pb)}"
    requires_human = pb["screen"].get("requires_human_conclusion") is True
    llm_conclusion = [
        s["id"]
        for b in pb["doc"]
        for s in b["ss"]
        if s.get("slot") == "conclusion" and s.get("origin") == "llm" and not s.get("placeholder")
    ]
    submit_blocked = pb["submit_allowed"] is False
    ok = requires_human and not llm_conclusion and submit_blocked
    reasons = [b.get("reason") for b in pb.get("blockers", [])]
    return ok, (
        f"requires_human_conclusion={requires_human}、非佔位 llm 結論={llm_conclusion or '無'}、"
        f"submit_allowed={pb['submit_allowed']}、blockers={reasons}"
    )


def check_ac8(base: str, p: dict, timeout: float) -> tuple[bool, str]:
    base_rid = p["run_id"]
    code, p2, _ = safe_run(base, p["case_id"], {"base_run_id": base_rid, "from_node": "n5"}, timeout)
    if code != 200 or not isinstance(p2, dict):
        return False, f"續跑失敗：HTTP {code} {detail_of(p2)}"
    same_retrieval = p2["retrieval"] == p["retrieval"]
    timings = sorted(p2["run_meta"]["node_timings"])
    ok = same_retrieval and timings == ["n5", "n6"] and p2["run_meta"]["base_run_id"] == base_rid
    return ok, f"retrieval 相同={same_retrieval}、node_timings={timings}、base_run_id 正確={p2['run_meta']['base_run_id'] == base_rid}"


def check_ac9(base: str, p: dict, timeout: float) -> tuple[bool, str]:
    confirmed = {
        k: p["intake"][k]
        for k in ("d2", "d3", "service_method", "transit_days", "interested_party", "note")
    }
    code, p3, _ = safe_run(
        base,
        p["case_id"],
        {"base_run_id": p["run_id"], "from_node": "n2", "confirmed_intake": confirmed},
        timeout,
    )
    if code != 200 or not isinstance(p3, dict):
        return False, f"續跑失敗：HTTP {code} {detail_of(p3)}"
    timings = sorted(p3["run_meta"]["node_timings"])
    ok = "n1" not in timings and p3["intake_origin"]["d2"] == "human"
    return ok, f"node_timings={timings}、intake_origin.d2={p3['intake_origin']['d2']}"


def check_ac8b(base: str, case: str) -> tuple[bool, str]:
    code, body = http(base, "POST", f"/api/cases/{case}/runs", {"from_node": "n5"})
    return code == 400, f"HTTP {code}：{detail_of(body)}"


def check_ac15(pu: dict, want: dict) -> tuple[bool, str]:
    keys = ("no", "d2", "d3", "service_method")
    got = {k: pu["intake"].get(k) for k in keys}
    expect = {k: want[k] for k in keys}
    same = all(str(got[k]) == str(expect[k]) for k in keys)
    type_ok = pu["classification"]["class"]["case_type"] == want["type"]
    return same and type_ok, f"got={got}、want={expect}、案型一致={type_ok}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8123")
    ap.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"等一次執行跑完的上限（秒），預設 {DEFAULT_TIMEOUT:.0f}。bedrock 檔位跑得慢就調大。",
    )
    a = ap.parse_args()
    base = a.base.rstrip("/")

    say("✅ 通過　❌ 失敗　⏸ 未驗（需 Bedrock）\n")
    say("| AC | 結果 | 證據 |")
    say("|---|---|---|")

    code, health = http(base, "GET", "/api/health")
    local_ac(
        "health 200 且 live_settings ok",
        lambda: (
            code == 200 and isinstance(health, dict) and health.get("ok") is True,
            f"HTTP {code}、run_mode={health.get('run_mode') if isinstance(health, dict) else '?'}、"
            f"checks={[(c['name'], c['ok']) for c in health.get('checks', [])] if isinstance(health, dict) else health}",
        ),
    )

    code, p0, p, ev = run_full(base, "synthetic-ordinary-01", a.timeout)
    ok_main = code == 200 and isinstance(p, dict)
    mode = p["run_meta"]["run_mode"] if ok_main else "unknown"
    # 需要真模型的 AC 得同時看 provider：`run_mode=bedrock` 只說明走了 live 程式路徑，
    # 說不出模型是誰家的。`MODEL_PROVIDER=openai` 下把 AC4／AC5／AC7／AC15 蓋成 ✅，
    # 等於拿非 AWS 模型充當賽制證據（spec R1 明文禁止）。
    provider = (p["run_meta"].get("model_ids") or {}).get("provider") if ok_main else None
    live_mode = mode if provider in (None, "bedrock") else f"provider={provider}"
    local_ac(
        "synthetic-ordinary-01 跑完六節點",
        lambda: (ok_main, f"HTTP {code}、run_mode={mode}" if ok_main else f"HTTP {code}：{detail_of(p)}"),
    )

    if ev:
        local_ac(
            AC10,
            lambda: (
                # 兩次執行加起來要涵蓋六個節點：第一次 N1（1 對），確認後 N2–N6（5 對）。
                # 停等收文是正確行為，所以「一次 POST 六對」這個舊斷言已經不適用。
                ev.count("node_start") == 6 and ev.count("node_done") == 6 and ev[-1] == "run_done",
                " → ".join(ev),
            ),
        )
    else:
        pending(
            AC10,
            "fixture 檔位的 POST /runs 同步回 200，沒有 ticket 也就沒有 events_url，"
            "無 SSE 可驗（端點 GET /api/runs/{id}/events 本身已實作，見 Task 7b commit 1b0db05；"
            "要驗 SSE 需對 RUN_MODE=bedrock 的服務跑，那時 /runs 回 202 帶 events_url）",
        )

    if not ok_main:
        say("\n主案例跑不起來，其餘 AC 無法驗。")
        print("\n".join(OUT))
        return 1

    # AC4 驗的是 N1 的產出，要看**N1 真的跑過的那一次**；續跑那次 n1 沒執行，
    # 它的 intake_origin 是 human、run_meta 也沒有 extract 的 model_id。
    live_ac(AC4, live_mode, lambda: check_ac4(p0 if isinstance(p0, dict) else p))
    live_ac(AC5, live_mode, lambda: check_ac5(p))
    live_ac(AC7, live_mode, lambda: check_ac7(p))
    local_ac(AC8, lambda: check_ac8(base, p, a.timeout))
    # AC9 驗的是「確認後從 n2 續跑」，base 要用**第一次那個停在 NEEDS_INPUT 的 run**，
    # 不是已經續跑完的 p——拿 p 當 base 等於在驗「續跑的續跑」，不是這條 AC 的對象。
    local_ac(AC9, lambda: check_ac9(base, p0 if isinstance(p0, dict) else p, a.timeout))
    local_ac(AC8B, lambda: check_ac8b(base, "synthetic-ordinary-01"))

    code_b, _pb0, pb, _ = run_full(base, "synthetic-blocked-01", a.timeout)
    local_ac(AC6, lambda: check_ac6(code_b, pb))

    # AC15：上傳合成訴願書 txt → 抽取結果與該案 fixture 一致
    fx = json.loads(pathlib.Path(FIXTURE_PATH).read_text(encoding="utf-8"))
    files = [(d["n"].replace(".pdf", ".txt"), d["text"]) for d in fx["documents"]]
    code_c, created = post_multipart(base, "/api/cases", files)
    if code_c != 201 or not isinstance(created, dict):
        check(AC15, False, f"上傳建案失敗：HTTP {code_c} {detail_of(created)}")
    else:
        cid = created["case_id"]
        # 上傳案同樣會停在 NEEDS_INPUT——AC15 要比的是 N1 抽取與 N2 案型，
        # N2 沒跑的話 `classification` 是空 dict，檢查時就炸 KeyError: 'class'。
        code_u, _pu0, pu, _ = run_full(base, cid, a.timeout)
        if mode != "bedrock":  # fixture／local：上傳案根本跑不起來
            # 上傳案沒有可重播的 fixture，fixture 檔位會擋下來（graph.py:221）。
            # 這代表「機制正確、live 未驗」，不是失敗。
            pending(
                AC15,
                f"上傳建案 201（case_id 前綴 upload-）；POST runs → HTTP {code_u}：{detail_of(pu)}"
                "。機制正確、live 未驗（需 Bedrock 開通）",
            )
        elif code_u != 200 or not isinstance(pu, dict):
            check(AC15, False, f"上傳案分析失敗：HTTP {code_u} {detail_of(pu)}")
        elif live_mode != "bedrock":
            # 跟 fixture 那支不同：這裡上傳案**真的跑起來了**（HTTP 200），
            # 抽取結果一致與否也真的可比。不判的理由只有一個——模型不是 AWS 的。
            ok_shape, ev = guarded(lambda: check_ac15(pu, fx["extraction"]["intake"]))
            pending(AC15, f"{why_not_live(live_mode)}。僅供參考的比對結果：{ok_shape} — {ev}")
        else:
            local_ac(AC15, lambda: check_ac15(pu, fx["extraction"]["intake"]))

    say("")
    say(
        f"服務模式：`{mode}`"
        + (f"、model provider：`{provider}`" if provider else "")
        + "。⏸ 的項目要等 Bedrock 開通後，對 `RUN_MODE=bedrock`"
        + "（且 `MODEL_PROVIDER=bedrock`）的服務重跑同一支腳本。"
    )
    say("AC11（模型 id 設成不存在值 → 502 且 payload 無 fixture 內容）需另起一個服務實例驗證，證據見 `ac11.md`。")
    print("\n".join(OUT))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
