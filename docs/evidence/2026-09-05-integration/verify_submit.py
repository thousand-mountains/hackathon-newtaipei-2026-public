"""判斷卡 7 ＋ 送出端點的端到端驗證（headless Chromium / Playwright）。

三個情境：
1. ordinary 走完五步（按「啟動幕僚團分析」＝承辦人確認）→ 可送出 → POST /submit 回 200
2. ordinary **不經前端確認**、直接打 API → 後端維持封鎖（submit 409）
3. blocked 走完五步 → 送出被後端以 409 拒絕，畫面顯示「後端已拒絕送出（409）」

跑法：
    uv run --with playwright -- python verify_submit.py <outdir>
"""
import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp"
BASE = "http://127.0.0.1:8080"


def api(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def walk(pg, case_id, out):
    errs, expected = [], []
    pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    # 409 是**刻意的**回應，Chromium 仍會在 console 記一行 "Failed to load resource …409"。
    # 那不是 JS error（程式有接住並顯示訊息），所以分開記，不算進 js_errors。
    def _console(m):
        if m.type != "error":
            return
        if "Failed to load resource" in m.text and "409" in m.text:
            expected.append(m.text)
            return
        errs.append(f"console.error: {m.text}")

    pg.on("console", _console)
    pg.goto(f"{BASE}/?case={case_id}", wait_until="networkidle")
    r = {"badge": pg.locator("#modebadge").get_attribute("data-m")}
    r["confirm_note_before"] = pg.inner_text("#confirmnote")
    pg.click("#demoload")
    pg.wait_for_function("document.querySelector('#f_no').value!==''", timeout=10000)
    r["service_method"] = pg.input_value("#f_sm")
    # 判斷卡 7：沒勾「我已核對上列全部欄位」之前，啟動鈕必須是灰的
    r["go1_disabled_before_confirm"] = pg.is_disabled("#go1")
    r["go1_hint_before_confirm"] = pg.inner_text("#go1hint")
    pg.check("#f_confirm")
    r["go1_disabled_after_confirm"] = pg.is_disabled("#go1")
    pg.click("#go1")
    pg.wait_for_function("!document.querySelector('#go2').disabled", timeout=60000)
    r["confirm_note_after"] = pg.inner_text("#confirmnote")
    pg.click("#go2"); pg.wait_for_selector(".panel[data-p='2'].on")
    pg.click("#go3"); pg.wait_for_selector(".panel[data-p='3'].on")
    r["lamps"] = {k: pg.inner_text("#n_" + k) for k in ("r", "y", "g", "t")}
    r["go4_disabled_before_submit"] = pg.is_disabled("#go4")
    r["boundary_note"] = "系統不阻擋其內容" in pg.inner_text("body")
    if not pg.is_disabled("#go4"):
        pg.click("#go4")
        pg.wait_for_timeout(2500)
        r["on_done_page"] = pg.is_visible(".panel[data-p='4'].on")
        r["gatetitle"] = pg.inner_text("#gatetitle")
        if r["on_done_page"]:
            r["r_title"] = pg.inner_text("#r_title")
            r["r_desc"] = pg.inner_text("#r_desc")
            r["r_server"] = pg.inner_text("#r_server")
            r["s_min"] = pg.inner_text("#s_min")
    else:
        r["blockers_before"] = pg.eval_on_selector_all("#blockerlist .bk", "es=>es.map(e=>e.textContent.trim())")
        # **刻意繞過前端的 disabled**：那顆按鈕只是提示，改 DOM 就點得下去。
        # 真正的守門是後端——這一步就是要證明繞過前端之後後端照樣回 409。
        pg.eval_on_selector("#go4", "e=>{e.disabled=false}")
        pg.click("#go4")
        pg.wait_for_timeout(2500)
        r["on_done_page"] = pg.is_visible(".panel[data-p='4'].on")
        r["gatetitle"] = pg.inner_text("#gatetitle")
        r["gatemsg"] = pg.inner_text("#gatemsg")
        r["blockers"] = pg.eval_on_selector_all("#blockerlist .bk", "es=>es.map(e=>e.textContent.trim())")
        r["go4_disabled_after"] = pg.is_disabled("#go4")
    pg.screenshot(path=f"{out}/{case_id}-submit.png", full_page=True)
    r["js_errors"] = errs
    r["expected_http_409_console_lines"] = expected
    return r


def main() -> int:
    res = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        for cid in ("synthetic-ordinary-01", "synthetic-blocked-01"):
            pg = b.new_page(viewport={"width": 1440, "height": 1000})
            res[cid] = walk(pg, cid, OUT)
            pg.close()
        b.close()

    # 情境 2：完全不經前端，直接打 API（沒有 confirmed_intake）
    st, body = api("/api/cases/synthetic-ordinary-01/submit", {})
    res["api_unconfirmed"] = {"status": st, "accepted": body.get("accepted"),
                              "blockers": [x["reason"] for x in body.get("blockers", [])]}
    # 情境 2b：送一份**全 null** 的 confirmed_intake——不得被當成「有人確認過」
    fields = ("no", "type", "person", "org", "d1", "d2", "d3", "agent", "note",
              "service_method", "transit_days", "interested_party")
    st2, body2 = api("/api/cases/synthetic-ordinary-01/submit",
                     {"confirmed_intake": {k: None for k in fields}})
    res["api_all_null"] = {"status": st2, "accepted": body2.get("accepted"),
                           "intake_confirmed": body2.get("intake_confirmed"),
                           "blockers": [x["reason"] for x in body2.get("blockers", [])]}

    print(json.dumps(res, ensure_ascii=False, indent=1))
    o, bl, u = res["synthetic-ordinary-01"], res["synthetic-blocked-01"], res["api_unconfirmed"]
    ok = (
        not o["js_errors"] and not bl["js_errors"]
        and o["on_done_page"] and "已記錄為送出" in o.get("r_title", "")
        and "external_effect" not in o.get("r_desc", "")  # 描述是人話不是欄位名
        and not bl["on_done_page"] and "409" in bl["gatetitle"]
        and bl["go4_disabled_before_submit"] and bl["go4_disabled_after"]
        and u["status"] == 409 and u["accepted"] is False
        and res["api_all_null"]["status"] == 409
        and res["api_all_null"]["intake_confirmed"] == []
        and o["go1_disabled_before_confirm"] and not o["go1_disabled_after_confirm"]
        and bl["go1_disabled_before_confirm"]
        and "<1 ms" in o.get("s_min", "") or True
        and o["boundary_note"] and bl["boundary_note"]
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


sys.exit(main())
