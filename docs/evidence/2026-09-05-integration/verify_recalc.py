"""live 模式「改日期即時重算」驗證（headless Chromium / Playwright）。

驗的是 PR #2 的賣點在整合後還活著，而且**燈號是後端給的**：
收文頁把提起訴願日改到期滿日之後 → 前端打 `POST /api/deadline`
→ 期間判定句轉紅、紅燈數上升、送出閘門跟著收緊。

跑法：
    uv run --with playwright -- python verify_recalc.py <case_id> <new_filing_date> <outdir>
"""
import json
import sys

from playwright.sync_api import sync_playwright

CASE = sys.argv[1] if len(sys.argv) > 1 else "synthetic-blocked-01"
NEW_FILING = sys.argv[2] if len(sys.argv) > 2 else "2025-06-30"
OUT = sys.argv[3] if len(sys.argv) > 3 else "/tmp"
URL = f"http://127.0.0.1:8080/?case={CASE}"

errors = []


def lamps(pg):
    return {k: pg.inner_text("#n_" + k) for k in ("r", "y", "g", "t")}


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        pg.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
        pg.goto(URL, wait_until="networkidle")

        r = {"url": URL, "badge": pg.locator("#modebadge").get_attribute("data-m")}

        # 走完到燈號審核，先記下「改日期之前」的狀態
        pg.click("#demoload")
        pg.wait_for_function("document.querySelector('#f_no').value!==''", timeout=10000)
        r["filing_before"] = pg.input_value("#f_d3")
        r["service"] = pg.input_value("#f_d2")
        pg.check("#f_confirm")   # 判斷卡 7：live 模式要先明示確認
        pg.click("#go1")
        pg.wait_for_function("!document.querySelector('#go2').disabled", timeout=60000)
        pg.click("#go2")
        pg.wait_for_selector(".panel[data-p='2'].on")
        pg.click("#go3")
        pg.wait_for_selector(".panel[data-p='3'].on")
        r["before"] = {
            "lamps": lamps(pg),
            "go4_disabled": pg.is_disabled("#go4"),
            "calc_sentences": pg.eval_on_selector_all(
                "#rvinner .rs", "es=>es.filter(e=>/期滿日|生效|起算/.test(e.textContent)).map(e=>e.textContent)"),
            "red_sentences": pg.eval_on_selector_all("#redlist button", "es=>es.map(e=>e.textContent.trim())"),
        }
        pg.screenshot(path=f"{OUT}/{CASE}-recalc-before.png", full_page=True)

        # 回步驟 0，把提起訴願日改到期滿之後
        pg.click('.step[data-i="0"]')
        pg.wait_for_selector(".panel[data-p='0'].on")
        pg.fill("#f_d3", NEW_FILING)
        pg.dispatch_event("#f_d3", "change")
        pg.wait_for_function(
            "document.querySelector('#recalcnote').textContent.includes('期間已由後端重算')", timeout=15000)
        r["recalc_note"] = pg.inner_text("#recalcnote")
        r["proc_card"] = pg.eval_on_selector("#ag-proc .out", "e=>e.textContent")

        # 回燈號審核看結果
        pg.click('.step[data-i="3"]')
        pg.wait_for_selector(".panel[data-p='3'].on")
        r["after"] = {
            "filing": NEW_FILING,
            "lamps": lamps(pg),
            "go4_disabled": pg.is_disabled("#go4"),
            "gatetitle": pg.inner_text("#gatetitle"),
            "red_sentences": pg.eval_on_selector_all("#redlist button", "es=>es.map(e=>e.textContent.trim())"),
            "verdict_sentence": pg.eval_on_selector_all(
                "#rvinner .rs[data-l='r']", "es=>es.map(e=>e.textContent)"),
        }
        # 點那句紅燈看它的理由（必須是後端 verdict.why）
        red = pg.locator("#redlist button").last
        if red.count():
            red.click()
            r["after"]["detail"] = pg.inner_text("#detbody")[:600]
        pg.screenshot(path=f"{OUT}/{CASE}-recalc-after.png", full_page=True)

        r["js_errors"] = errors
        b.close()

    print(json.dumps(r, ensure_ascii=False, indent=1))
    ok = (
        not errors
        and int(r["after"]["lamps"]["r"]) > int(r["before"]["lamps"]["r"])
        and r["after"]["go4_disabled"]
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


sys.exit(main())
