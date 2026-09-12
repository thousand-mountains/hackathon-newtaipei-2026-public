#!/usr/bin/env python3
"""賽方測資批次評估：上傳 → 跑 → 對黃金答案比分，輸出 markdown 到 stdout。

    # 服務先照 backend/DEPLOY.md §0.0 的 bedrock 指令起在 8123
    python3 scripts/run_eval.py --init-golden --dir ~/Downloads/aws_hackthon_eval
    # ↑ 產生 data/local/eval/golden.tsv 模板，**人工**逐檔抄正解後再往下跑
    python3 scripts/run_eval.py --dir ~/Downloads/aws_hackthon_eval --base http://127.0.0.1:8123

只用 stdlib（與 scripts/live_acceptance.py 同一條紀律：評估腳本不得長出第三方相依）。

## 這支腳本做什麼、不做什麼

**做**：把每份卷證上傳建案、跑完六節點、把系統輸出跟 `golden.tsv` 逐欄比對、
把行為面紅線（結論封鎖、越界引用、分層誠實）逐案判定。

**不做**：
- **不自己算期間**。要區分「N1 抽錯」與「引擎算錯」時，它把 golden 的日期打給
  `POST /api/deadline`（系統自己的引擎）再比，不在腳本裡寫第二套日期規則——
  那樣只是拿一套沒人驗過的實作去審一套驗過的（CONSTITUTION §4）。
- **不產生黃金答案**。`--init-golden` 只給空模板。腳本沒看過卷證，代抄就是編測資
  （CONSTITUTION §3）。golden 沒填的欄位一律標「未驗」，不會當成通過。
- **不在報告裡印個資**。`no`（處分字號）與 `person`（訴願人姓名）預設遮罩，
  完整值只落在 `--out` 目錄（`data/local/` 已 gitignored）。要看原值加 `--show-values`，
  那份輸出就**不要**貼進 docs/evidence（CONSTITUTION §6）。

## 退出碼

**退出碼只涵蓋「客觀判得出來」的那幾條**，其餘一律要人讀報告——這裡寫得比實作寬
會比沒有這段更糟：趕時間的人只看 exit code，會把「沒驗到」讀成「驗過了沒問題」。

- `1`：以下任一
  - **查無字號**（`citation_counts.missing > 0`）——編出來的字號，CONSTITUTION §2
  - **分層誠實違規**（`origin_violations`）——欄位的 origin 與註冊表不符
  - 有案子**根本沒跑起來**（建案／執行失敗）
  - **沒有任何一件跑到 `VERIFIED`**——那代表行為面紅線這一段一條都沒驗到，
    報告裡那幾欄全是 ⏸。這種情況回 0 等於拿「沒有證據」當「沒有問題」。
- `0`：其餘

**刻意不列入退出碼**（印在報告裡，要人判）：

- **該封鎖沒封鎖**：要知道「這件是不是實體爭議案」才判得出來，腳本沒有那個知識。
  報告第三段會把它標出來，出現「否」一律逐案查 `backend/gate/lamps.py`。
- **越界引用**（`out_of_scope`）：引到資料集涵蓋範圍外的字號不必然是錯（見第三段說明），
  數字變大才值得追。
- **抽取正確率**：那是要調 prompt 的量測結果，不是紅線。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_OUT = pathlib.Path("data/local/eval")
DEFAULT_TIMEOUT = 600.0

# golden.tsv 可以填的欄位＝收文欄位。`file` 是對應檔名，不是欄位。
GOLDEN_FIELDS = (
    "no", "type", "person", "org", "d1", "d2", "d3", "agent",
    "service_method", "transit_days", "interested_party",
)
# 報告預設遮罩的欄位：字號可回溯到真實案件，姓名是個資
MASKED_FIELDS = ("no", "person")
# 期間引擎的輸入。golden 有這幾欄時才做「輸入錯 vs 引擎錯」的分離驗證。
DEADLINE_FIELDS = ("d2", "d3", "service_method", "transit_days", "interested_party")

OUT: list[str] = []


def say(s: str = "") -> None:
    OUT.append(s)


def cell(s: object) -> str:
    """表格欄位：吃掉會把 markdown 表格弄壞的字元。"""
    return str(s).replace("|", "\\|").replace("\n", " ").strip()


# ── HTTP（stdlib）────────────────────────────────────────────────
def http(base: str, method: str, path: str, body: dict | None = None,
         timeout: float = 180) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        base + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except urllib.error.URLError as e:
        return 0, f"連不上服務：{e.reason}"


def upload(base: str, path: pathlib.Path) -> tuple[int, dict | str]:
    """單檔 multipart 上傳建案。case_id 由檔名＋內容雜湊決定，重跑會落在同一個案件。"""
    boundary = "----eval" + uuid.uuid4().hex
    data = path.read_bytes()
    ctype = "application/pdf" if path.suffix.lower() == ".pdf" else "text/plain"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{path.name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        base + "/api/cases", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except urllib.error.URLError as e:
        return 0, f"連不上服務：{e.reason}"


def run_case(base: str, case_id: str, timeout: float) -> tuple[int, dict | str]:
    """跑一次分析到終態。fixture 檔位同步回 200；bedrock 檔位回 202 後輪詢。"""
    code, ticket = http(base, "POST", f"/api/cases/{case_id}/runs", {})
    if code == 200:
        return code, ticket
    if code != 202 or not isinstance(ticket, dict):
        return code, ticket
    rid = ticket.get("run_id")
    if not rid:
        return 502, {"error": "202 回應缺 run_id", "body": ticket}
    end = time.monotonic() + timeout
    while True:
        code, payload = http(base, "GET", f"/api/runs/{rid}")
        if code != 409:
            return code, payload
        if time.monotonic() >= end:
            # 逾時要講清楚是**腳本等不及**，不是系統算錯
            return 408, {"error": f"等待逾時 {timeout}s，可稍後 GET /api/runs/{rid}"}
        time.sleep(2)


def resume_confirmed(base: str, case_id: str, base_run_id: str, confirmed: dict,
                     timeout: float) -> tuple[int, dict | str]:
    """用 golden 的值當「承辦人已確認」，從 n2 續跑到 N6（spec §5.5 的續跑路徑）。

    **值取自 golden 而不是模型自己抽的那一份**：拿模型的輸出回頭確認模型的輸出，
    等於沒有人看過卷證，`intake_origin` 卻會被翻成 `human`——那正是判斷卡 7 要防的事。
    golden 是人讀原文抄的，用它確認才對得上真實動線。

    `null` 的欄位**不送**：`_apply_confirmed` 會跳過 None，送了也不會生效，
    但送出去會讓人以為那一欄確認過了。少送一欄比虛報一欄誠實。
    """
    payload = {k: v for k, v in confirmed.items() if v is not None}
    code, ticket = http(base, "POST", f"/api/cases/{case_id}/runs",
                        {"base_run_id": base_run_id, "from_node": "n2",
                         "confirmed_intake": payload})
    if code == 200:
        return code, ticket
    if code != 202 or not isinstance(ticket, dict):
        return code, ticket
    rid = ticket.get("run_id")
    if not rid:
        return 502, {"error": "202 回應缺 run_id", "body": ticket}
    end = time.monotonic() + timeout
    while True:
        code, out = http(base, "GET", f"/api/runs/{rid}")
        if code != 409:
            return code, out
        if time.monotonic() >= end:
            return 408, {"error": f"續跑等待逾時 {timeout}s，可稍後 GET /api/runs/{rid}"}
        time.sleep(2)


def detail_of(body: dict | str) -> str:
    if isinstance(body, dict):
        return json.dumps(body.get("detail", body), ensure_ascii=False)[:300]
    return str(body)[:300]


# ── golden.tsv ─────────────────────────────────────────────────
_ROC = re.compile(r"^(\d{2,3})[年/\-.](\d{1,2})[月/\-.](\d{1,2})日?$")


def norm_date(raw: str) -> str:
    """民國年寫法一律轉 ISO。轉不動就原樣回傳——讓它在比對時大聲不合，不要安靜地猜。"""
    s = raw.strip()
    if not s:
        return s
    m = _ROC.match(s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return dt.date(y + 1911, mo, d).isoformat()
        except ValueError:
            return s
    return s


def norm_golden(field: str, raw: str) -> object | None:
    """空白／`null`／`-` 一律代表「卷證沒寫」，那是一個**有意義的正解**，不是缺值。"""
    s = raw.strip()
    if s in ("", "null", "NULL", "-", "—"):
        return None
    if field in ("d1", "d2", "d3"):
        return norm_date(s)
    if field == "transit_days":
        try:
            return int(s)
        except ValueError:
            return s
    if field == "interested_party":
        return s.lower() in ("1", "true", "yes", "y", "是")
    return s


def load_golden(path: pathlib.Path) -> tuple[dict[str, dict], list[str]]:
    """回傳 {檔名: {欄位: 正解}} 與「這份 golden 實際填了哪些欄位」。

    **有欄名但整欄空白** ≠ 有正解。逐格判斷：只有真的填了東西（含明寫 null）的格子才算。
    """
    rows: dict[str, dict] = {}
    used: set[str] = set()
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            name = (row.get("file") or "").strip()
            if not name:
                continue
            g: dict[str, object | None] = {}
            for fld in GOLDEN_FIELDS:
                if fld not in row or row[fld] is None:
                    continue
                if row[fld].strip() == "":
                    continue  # 沒填＝這一欄不驗；要表示「卷證沒寫」請明寫 null
                g[fld] = norm_golden(fld, row[fld])
                used.add(fld)
            rows[name] = g
    return rows, [f for f in GOLDEN_FIELDS if f in used]


def init_golden(files: list[pathlib.Path], path: pathlib.Path) -> None:
    """產生空模板。**只填檔名**，其餘留白等人抄。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["file", *GOLDEN_FIELDS])
        for p in files:
            w.writerow([p.name, *([""] * len(GOLDEN_FIELDS))])


# ── 比分 ────────────────────────────────────────────────────────
VERDICTS = {
    "ok": "✅ 正確",
    "wrong": "❌ 抽錯",
    "hallucinated": "🔴 卷證沒寫卻填了值",
    "missed": "⚠ 卷證有寫卻沒抽到",
}


# 卷證沒寫時 **prompt 明文要求給的預設值**（`backend/llm/prompts/n1_extract.md`）：
#
#   transit_days      「卷證沒寫就 0 且 conf 給 0.5」
#   interested_party  「不確定給 false 且 conf 給 0.5」
#
# 這兩欄因此**幾乎不可能是 null**。golden 照本腳本的規則把「卷證沒寫」抄成 null，
# 若不認這件事，每一列都會被判成 🔴「卷證沒寫卻填了值」——而那一格的指示是
# 「改 n1_extract.md 時優先壓這一格」，等於叫人去改一個 prompt 明文要求的正確行為。
#
# 真正的 hallucination 是「卷證沒寫，而系統給了**非預設**的值」（例如憑空生出
# transit_days=5），那仍然會被抓出來。
SPEC_DEFAULTS: dict[str, object] = {"transit_days": 0, "interested_party": False}


def judge_field(field: str, want: object | None, got: object | None) -> str:
    """比一個欄位。`field` 是為了認得 SPEC_DEFAULTS 與寬鬆型別的那兩欄。

    `transit_days`／`interested_party` 在 schema 裡是 `LooseFieldValue`（型別刻意寬鬆），
    模型可能回 `False` 也可能回 `"false"`。**兩邊都過 `norm_golden` 再比**，
    否則 `str(False) != str("false")` 會把對的判成「抽錯」。
    """
    if field in SPEC_DEFAULTS and got is not None:
        got = norm_golden(field, str(got))
    if want is None and got is None:
        return "ok"
    if want is None:
        # 規格預設值不算「編」——見 SPEC_DEFAULTS 的說明
        if field in SPEC_DEFAULTS and got == SPEC_DEFAULTS[field]:
            return "ok"
        return "hallucinated"
    if got is None:
        return "missed"
    return "ok" if str(want) == str(got) else "wrong"


def mask(field: str, value: object | None, show: bool) -> str:
    if value is None:
        return "null"
    if show or field not in MASKED_FIELDS:
        return str(value)
    s = str(value)
    return f"{s[:2]}…{s[-2:]}（{len(s)} 字，加 --show-values 看原值）" if len(s) > 4 else "…"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="測資目錄（.txt／.pdf）")
    ap.add_argument("--base", default="http://127.0.0.1:8123", help="服務位址")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help=f"產出目錄（預設 {DEFAULT_OUT}，已 gitignored）")
    ap.add_argument("--golden", default=None, help="黃金答案 tsv（預設 <out>/golden.tsv）")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="等一件跑完的上限（秒）")
    ap.add_argument("--only", default=None, help="只跑檔名含此字串的案子")
    ap.add_argument("--init-golden", action="store_true", help="產生空的 golden.tsv 模板後結束")
    ap.add_argument("--show-values", action="store_true", help="報告印出字號與姓名原值（**不要**貼進 evidence）")
    ap.add_argument("--reuse", action="store_true", help="沿用 <out>/runs 既有結果，不重打模型（只重算分數）")
    ap.add_argument("--confirm", action="store_true",
                    help="第一次執行停在 NEEDS_INPUT 時，用 golden 的值當作「承辦人已確認」從 n2 續跑，"
                         "讓 N2–N6 真的跑起來。**抽取分數仍算第一次的 intake**，不算確認後的")
    args = ap.parse_args(argv)

    src = pathlib.Path(args.dir).expanduser()
    if not src.is_dir():
        print(f"錯誤：{src} 不是目錄", file=sys.stderr)
        return 1
    files = sorted(p for p in src.iterdir()
                   if p.suffix.lower() in (".txt", ".pdf")
                   and (args.only is None or args.only in p.name))
    if not files:
        print(f"錯誤：{src} 裡沒有 .txt／.pdf", file=sys.stderr)
        return 1

    out_dir = pathlib.Path(args.out).expanduser()
    runs_dir = out_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    golden_path = pathlib.Path(args.golden) if args.golden else out_dir / "golden.tsv"

    if args.init_golden:
        init_golden(files, golden_path)
        print(f"已產生模板：{golden_path}（{len(files)} 列）")
        print("請逐檔讀原文抄正解。卷證沒寫的欄位請明寫 null——那是有意義的正解，留白代表「這欄不驗」。")
        return 0

    golden, golden_fields = ({}, [])
    if golden_path.exists():
        golden, golden_fields = load_golden(golden_path)

    # ── 服務狀態：報告要說得出這批數字是哪個模式跑出來的 ──
    code, health = http(args.base, "GET", "/api/health")
    if code == 0 or not isinstance(health, dict):
        print(f"錯誤：{detail_of(health)}（服務起了嗎？）", file=sys.stderr)
        return 1
    run_mode = str(health.get("run_mode", "?"))
    kb_backend = str(health.get("kb_backend", "?"))
    # provider 不從 /api/health 拿。**不是因為那裡沒有**——bedrock 檔位它有值
    # （`app.py` 的 `model_ids` 走 `llm/client.py:model_ids()`，一定帶 provider；
    # fixture 檔位回 None 是刻意的，那時根本沒呼叫過任何模型）。
    # 而是因為 health 報的是**設定值**，這份報告要講的是**這批數字是誰跑出來的**
    # ——那只有跑完一次才知道，所以往下從 payload 的 run_meta.model_ids.provider 讀。
    provider = "?"

    # ── 上傳 → 跑 ──
    ids_path = out_dir / "case-ids.tsv"
    cases: list[tuple[pathlib.Path, str]] = []
    failures: list[tuple[str, str]] = []
    payloads: dict[str, dict] = {}        # 第一次執行（抽取分數算這一份）
    resumed: dict[str, dict] = {}          # 確認後續跑（行為面與程序判定算這一份）

    for p in files:
        code, body = upload(args.base, p)
        if code != 201 or not isinstance(body, dict):
            failures.append((p.name, f"建案失敗 HTTP {code}：{detail_of(body)}"))
            continue
        cid = str(body["case_id"])
        cases.append((p, cid))
        cached = runs_dir / f"{cid}.json"
        cached2 = runs_dir / f"{cid}.confirmed.json"
        if args.reuse and cached.exists():
            payloads[cid] = json.loads(cached.read_text(encoding="utf-8"))
            if cached2.exists():
                resumed[cid] = json.loads(cached2.read_text(encoding="utf-8"))
                print(f"· {p.name} → {cid}（沿用既有結果）", file=sys.stderr)
                continue
            if not (args.confirm and payloads[cid].get("state") != "VERIFIED" and golden.get(p.name)):
                print(f"· {p.name} → {cid}（沿用既有結果）", file=sys.stderr)
                continue
            # 抽取跑過了、確認續跑還沒跑（上次中斷）：只補續跑那一半，不重打 N1。
            print(f"· {p.name} → {cid}（沿用抽取結果，補跑確認續跑）", file=sys.stderr, flush=True)
            g = golden[p.name]
            code, rp = resume_confirmed(args.base, cid, str(payloads[cid]["run_id"]), g, args.timeout)
            if code != 200 or not isinstance(rp, dict):
                failures.append((p.name, f"確認續跑失敗 HTTP {code}：{detail_of(rp)}"))
                continue
            cached2.write_text(json.dumps(rp, ensure_ascii=False, indent=2), encoding="utf-8")
            resumed[cid] = rp
            print(f"  續跑完成　state={rp.get('state')}", file=sys.stderr)
            continue
        print(f"· {p.name} → {cid} 執行中…", file=sys.stderr, flush=True)
        t0 = time.monotonic()
        code, payload = run_case(args.base, cid, args.timeout)
        if code != 200 or not isinstance(payload, dict):
            failures.append((p.name, f"執行失敗 HTTP {code}：{detail_of(payload)}"))
            continue
        cached.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payloads[cid] = payload
        print(f"  完成 {time.monotonic() - t0:.0f}s　state={payload.get('state')}", file=sys.stderr)

        if args.confirm and payload.get("state") != "VERIFIED":
            g = golden.get(p.name) or {}
            if not g:
                print("  ⏸ 沒有 golden，不做確認續跑", file=sys.stderr)
                continue
            print(f"  確認 {len([v for v in g.values() if v is not None])} 欄後從 n2 續跑…",
                  file=sys.stderr, flush=True)
            code, rp = resume_confirmed(args.base, cid, str(payload["run_id"]), g, args.timeout)
            if code != 200 or not isinstance(rp, dict):
                failures.append((p.name, f"確認續跑失敗 HTTP {code}：{detail_of(rp)}"))
                continue
            cached2.write_text(json.dumps(rp, ensure_ascii=False, indent=2), encoding="utf-8")
            resumed[cid] = rp
            print(f"  續跑完成　state={rp.get('state')}", file=sys.stderr)

    with ids_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["file", "case_id"])
        w.writerows([[p.name, cid] for p, cid in cases])

    # ── 報告 ──
    def final(cid: str) -> dict | None:
        """行為面與程序判定要看的那一份：有確認續跑就用它，否則就是第一次執行。

        抽取正確率**不准**用這個——確認後的 intake 已被 golden 覆寫，拿去比會是假的滿分。
        """
        return resumed.get(cid) or payloads.get(cid)

    providers = {str(((d.get("run_meta") or {}).get("model_ids") or {}).get("provider", "?"))
                 for d in payloads.values()}
    provider = "／".join(sorted(providers)) if providers else "?"

    say(f"# 測資評估　{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    say()
    say(f"- 服務：`{args.base}`　run_mode=**{run_mode}**　model provider=**{provider}**"
        f"　檢索器=`{kb_backend}`")
    say(f"- 測資：`{src}`　{len(files)} 件　產出：`{out_dir}`")
    if resumed:
        say(f"- **確認續跑**：{len(resumed)}／{len(payloads)} 件以 golden 的值當「承辦人已確認」"
            "從 n2 續跑到 N6。第一段（抽取）算的是**確認前**第一次執行的 intake；"
            "第二、三、四段算的是**確認後**那一次。")
    say(f"- 黃金答案：{'`' + str(golden_path) + '`（已填欄位：' + '、'.join(golden_fields) + '）' if golden_fields else '**未提供**'}")
    if run_mode != "bedrock" or providers not in ({"bedrock"}, set()):
        say()
        say("> ⚠ **這批結果不是真 AWS 基礎模型跑的**（run_mode／provider 不是 bedrock），"
            "不得作為賽制驗收證據。上傳案在 fixture 模式本來就跑不起來，下方失敗列即為此。")
    say()

    if failures:
        say("## 沒跑起來的案子")
        say()
        say("| 檔案 | 原因 |")
        say("|---|---|")
        for name, why in failures:
            say(f"| {cell(name)} | {cell(why)} |")
        say()

    # 抽取比對
    say("## 一、抽取正確率（N1）")
    say()
    tally: dict[str, dict[str, int]] = {f: dict.fromkeys(VERDICTS, 0) for f in golden_fields}
    overconfident: list[str] = []
    if not golden_fields:
        say("⏸ **未驗**：沒有黃金答案，無從判斷抽得對不對。"
            "先跑 `--init-golden` 產模板、人工抄完正解再跑一次。"
            "（這裡不會拿模型輸出當正解——那是自己考自己。）")
        say()
    else:
        say("| 檔案 | " + " | ".join(golden_fields) + " |")
        say("|---" * (len(golden_fields) + 1) + "|")
        for p, cid in cases:
            d = payloads.get(cid)
            if d is None:
                continue
            g = golden.get(p.name, {})
            conf = d.get("intake_conf") or {}
            cells = []
            for fld in golden_fields:
                if fld not in g:
                    cells.append("—")
                    continue
                got = (d.get("intake") or {}).get(fld)
                v = judge_field(fld, g[fld], got)
                tally[fld][v] += 1
                sym = {"ok": "✅", "wrong": "❌", "hallucinated": "🔴", "missed": "⚠"}[v]
                if v == "ok":
                    cells.append(sym)
                else:
                    cells.append(f"{sym} 期待 {mask(fld, g[fld], args.show_values)}／"
                                 f"實得 {mask(fld, got, args.show_values)}")
                c = conf.get(fld)
                if v in ("wrong", "hallucinated") and isinstance(c, (int, float)) and c >= 0.8:
                    overconfident.append(f"{p.name} `{fld}` conf={c}")
            say(f"| {cell(p.name)} | " + " | ".join(cell(x) for x in cells) + " |")
        say()
        say("| 欄位 | ✅ 正確 | ❌ 抽錯 | 🔴 沒寫卻填 | ⚠ 有寫沒抽到 |")
        say("|---|---|---|---|---|")
        for fld in golden_fields:
            t = tally[fld]
            say(f"| `{fld}` | {t['ok']} | {t['wrong']} | {t['hallucinated']} | {t['missed']} |")
        say()
        say("🔴「卷證沒寫卻填了值」是四種錯裡最嚴重的：它不是抽錯，是**編**。"
            "改 `backend/llm/prompts/n1_extract.md` 時優先壓這一格。")
        say()
        if overconfident:
            say("### 高信心的錯誤（比錯誤本身更危險）")
            say()
            say("抽錯卻給高 conf，代表畫面上會打「自動擷取」徽章、承辦人容易直接採用：")
            say()
            for s in overconfident:
                say(f"- {s}")
            say()

    # 程序判定：輸入錯 vs 引擎錯
    say("## 二、程序判定（N3 規則引擎）")
    say()
    dl_fields = [f for f in DEADLINE_FIELDS if f in golden_fields]
    if not {"d2", "d3"} <= set(dl_fields):
        say("⏸ **未驗**：golden 缺 `d2`／`d3`，無法分離「N1 抽錯」與「引擎算錯」。")
        say()
    else:
        say("把 golden 的日期打給 `POST /api/deadline`（**系統自己的引擎**，腳本不自算），"
            "跟這次執行的期滿日並排。兩者不同＝輸入錯導致程序判定翻轉，引擎本身沒有嫌疑。")
        say()
        say("| 檔案 | 系統期滿日／逾期 | 以 golden 輸入重算 | 判定 |")
        say("|---|---|---|---|")
        for p, cid in cases:
            d = final(cid)
            if d is None:
                continue
            g = golden.get(p.name, {})
            if g.get("d2") is None or g.get("d3") is None:
                say(f"| {cell(p.name)} | — | — | ⏸ golden 的 d2／d3 為 null，無從重算 |")
                continue
            sm = g.get("service_method")
            if sm is None:
                say(f"| {cell(p.name)} | — | — | ⏸ golden 的 service_method 為 null，引擎算不出期滿日 |")
                continue
            code, res = http(args.base, "POST", "/api/deadline", {
                "method": sm, "service": g["d2"], "filing": g["d3"],
                "transit": g.get("transit_days") or 0,
                "interested": bool(g.get("interested_party")),
            })
            sys_dl = (d.get("screen") or {}).get("deadline") or {}
            got = f"{sys_dl.get('deadline') or '未能計算'}／逾期={sys_dl.get('overdue')}"
            if code != 200 or not isinstance(res, dict):
                say(f"| {cell(p.name)} | {cell(got)} | HTTP {code} | ❌ 重算失敗：{cell(detail_of(res))} |")
                continue
            want = f"{res.get('deadline') or '未能計算'}／逾期={res.get('overdue')}"
            same = (str(sys_dl.get("deadline")) == str(res.get("deadline"))
                    and bool(sys_dl.get("overdue")) == bool(res.get("overdue")))
            verdict = "✅ 一致" if same else "❌ **不一致：抽取輸入已讓程序判定翻轉**"
            say(f"| {cell(p.name)} | {cell(got)} | {cell(want)} | {verdict} |")
        say()

    # 行為面紅線
    say("## 三、行為面紅線（不需要黃金答案，客觀判準）")
    say()
    say("| 檔案 | state | 結論封鎖 | 可送出 | 越界引用 | 查無字號 | 分層誠實 |")
    say("|---|---|---|---|---|---|---|")
    red = 0
    halted: list[str] = []
    for p, cid in cases:
        d = final(cid)
        if d is None:
            continue
        state = str(d.get("state"))
        cc = d.get("citation_counts") or {}
        oos, miss = int(cc.get("out_of_scope", 0)), int(cc.get("missing", 0))
        viol = len(d.get("origin_violations") or [])
        submit = bool(d.get("submit_allowed"))
        bad = (miss > 0) or (viol > 0)
        red += 1 if bad else 0
        if state != "VERIFIED":
            # N1 信心不足就停在 NEEDS_INPUT，下游節點根本沒跑。
            # 那些欄位標「否」等於宣稱「跑到了但沒封鎖」——那是不實的。
            halted.append(p.name)
            blocked_cell = f"⏸ 未跑到（停在 {state}）"
        else:
            blocked_cell = "🔒 是" if (d.get("screen") or {}).get("requires_human_conclusion") else "⚠ 否"
        say(f"| {cell(p.name)} | {cell(state)} | {cell(blocked_cell)} | "
            f"{'是' if submit else '否'} | {oos} | {'🔴 ' + str(miss) if miss else '0'} | "
            f"{'🔴 ' + str(viol) + ' 項' if viol else '✅ 無違規'} |")
    say()
    verified_n = sum(1 for _, cid in cases if str((final(cid) or {}).get("state")) == "VERIFIED")
    if halted:
        say(f"- **停在 NEEDS_INPUT 的有 {len(halted)}／{len(payloads)} 件**："
            + "、".join(f"`{h}`" for h in halted)
            + "。N1 抽取信心不足就停下來要承辦人補，**這是正確行為不是失敗**；"
            "但比例太高代表 prompt 對這種文體讀不動，值得調。停下來的案子下游沒跑，"
            "封鎖那一欄標 ⏸ 而不是「否」。加 `--confirm` 可用 golden 的值當"
            "「承辦人已確認」續跑，讓下游真的跑起來。")
    if cases and not verified_n:
        # **這一段沒有驗到任何東西**：沒有一件跑到終態，紅線那幾欄全是 ⏸。
        # 這種情況回 0 就是拿「沒有證據」當「沒有問題」——AC5 先前假通過的同一個坑。
        say()
        say("> 🔴 **沒有任何一件跑到 `VERIFIED`，本段一條紅線都沒有驗到。**"
            "上面的 0 不代表沒問題，代表沒量到。退出碼因此是 1。")
    say("- **結論封鎖**：實體爭議案（廢清法、建築法…）應為「是」。出現「否」要逐案查 "
        "`backend/gate/lamps.py`，那是最嚴重的破口，**不是 prompt 問題**。")
    say("- **越界引用**（庫外未驗證）不必然是錯，但數字變大代表主筆在引資料集涵蓋範圍外的字號；"
        "**查無字號**則是編出來的號碼，一律紅線。兩者都改 `n5_draft.md`。")
    say()

    # 逐案明細
    say("## 四、逐案明細")
    say()
    say("| 檔案 | case_id | 案型 | d2 | d3 | 送達方式 | 期滿日 | 逾期 | 77 條款 | 燈號 綠/黃/紅 |")
    say("|---|---|---|---|---|---|---|---|---|---|")
    rows = []
    for p, cid in cases:
        d = final(cid)
        if d is None:
            continue
        i = d.get("intake") or {}
        s = d.get("screen") or {}
        dl = s.get("deadline") or {}
        st = d.get("lamp_stats") or {}
        say(f"| {cell(p.name)} | `{cid}` | {cell(i.get('type'))} | {cell(i.get('d2'))} | "
            f"{cell(i.get('d3'))} | {cell(i.get('service_method'))} | {cell(dl.get('deadline'))} | "
            f"{cell(dl.get('overdue'))} | {cell((s.get('art77') or {}).get('clause'))} | "
            f"{st.get('g', 0)}/{st.get('y', 0)}/{st.get('r', 0)} |")
        conf = d.get("intake_conf") or {}
        rows.append({"file": p.name, "case_id": cid, "state": d.get("state"),
                     **{f: i.get(f) for f in GOLDEN_FIELDS},
                     **{f"{f}_conf": conf.get(f) for f in ("d2", "d3", "service_method", "type")},
                     "deadline": dl.get("deadline"), "overdue": dl.get("overdue"),
                     "art77": (s.get("art77") or {}).get("clause"),
                     "blocked": s.get("requires_human_conclusion"),
                     "submit_allowed": d.get("submit_allowed"),
                     "citation_counts": json.dumps(d.get("citation_counts") or {}, ensure_ascii=False),
                     "lamp_stats": json.dumps(d.get("lamp_stats") or {}, ensure_ascii=False)})
    say()
    if rows:
        actual = out_dir / "actual.tsv"
        with actual.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter="\t")
            w.writeheader()
            w.writerows(rows)
        say(f"完整逐欄輸出（含未遮罩原值）：`{actual}`、`{runs_dir}/<case_id>.json`。"
            "這兩處在 `data/local/`（gitignored），**不要複製進 docs/ 或 git**。")
    say()

    print("\n".join(OUT))
    # 退出碼的完整契約見檔頭。`red` 是查無字號／分層誠實違規的件數；
    # 「一件都沒跑到 VERIFIED」單獨成一條，因為那是「沒驗到」而不是「驗過了」。
    return 1 if (red or failures or (cases and not verified_n)) else 0


if __name__ == "__main__":
    sys.exit(main())
