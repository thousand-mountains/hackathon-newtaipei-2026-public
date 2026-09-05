"""五步動線 live 模式端到端驗證（headless Chromium / Playwright）。

跑法：
    uv run --with playwright -- python verify_ui.py <case_id> <outdir>
"""
import json
import sys

from playwright.sync_api import sync_playwright

CASE = sys.argv[1] if len(sys.argv) > 1 else "synthetic-ordinary-01"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp"
URL = f"http://127.0.0.1:8080/?case={CASE}"

errors = []
console = []


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        pg.on("console", lambda m: (console.append(f"{m.type}: {m.text}"),
                                    errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None))
        pg.goto(URL, wait_until="networkidle")

        r = {}
        r["url"] = URL
        badge = pg.locator("#modebadge")
        r["badge_mode"] = badge.get_attribute("data-m")
        r["badge_text"] = badge.inner_text().strip()
        r["badge_tip"] = (badge.get_attribute("data-tip") or "")[:200]
        r["prov_text"] = pg.locator("#prov").inner_text().strip()
        r["sourcenote"] = pg.locator("#sourcenote").inner_text().strip()
        r["case_options"] = pg.eval_on_selector_all("#caseselect option", "os=>os.map(o=>o.value)")
        r["case_selected"] = pg.locator("#caseselect").input_value() if r["case_options"] else None

        # 步驟 0：載入案件
        pg.click("#demoload")
        pg.wait_for_function("document.querySelector('#f_no').value!==''", timeout=10000)
        r["step0"] = {
            "files": pg.eval_on_selector_all("#filelist li .meta b", "es=>es.map(e=>e.textContent)"),
            "no": pg.input_value("#f_no"),
            "type": pg.input_value("#f_type"),
            "person": pg.input_value("#f_person"),
            "d2": pg.input_value("#f_d2"),
            "d3": pg.input_value("#f_d3"),
            "auto_badges": pg.eval_on_selector_all(
                ".field .auto", "es=>es.filter(e=>e.textContent.trim()).map(e=>e.id)"),
            "go1_disabled": pg.is_disabled("#go1"),
        }
        pg.screenshot(path=f"{OUT}/{CASE}-step0.png", full_page=True)

        # 步驟 1：幕僚團
        pg.check("#f_confirm")   # 判斷卡 7：live 模式要先明示確認
        pg.click("#go1")
        pg.wait_for_function("!document.querySelector('#go2').disabled", timeout=60000)
        r["step1"] = {
            "progress": pg.inner_text("#agprog"),
            "agents": pg.eval_on_selector_all(
                "#agents .agent", "es=>es.map(e=>({name:e.querySelector('h3').textContent,"
                                  "out:e.querySelector('.out').textContent,state:e.dataset.s}))"),
            "console_head": pg.eval_on_selector_all("#console div", "es=>es.slice(0,3).map(e=>e.textContent)"),
        }
        pg.screenshot(path=f"{OUT}/{CASE}-step1.png", full_page=True)

        # 步驟 2：草稿編輯
        pg.click("#go2")
        pg.wait_for_selector(".panel[data-p='2'].on")
        r["step2"] = {
            "tab_badges": pg.eval_on_selector_all(".tab", "es=>es.map(e=>e.textContent.trim())"),
            "law_cards": pg.eval_on_selector_all("#tp-law .ref .nm", "es=>es.map(e=>e.textContent)"),
            "law_tags": pg.eval_on_selector_all("#tp-law .ref .foot .tag", "es=>es.map(e=>e.textContent)"),
            "issue_cards": pg.eval_on_selector_all("#tp-issue .ref .nm", "es=>es.map(e=>e.textContent)"),
            "case_pane": pg.inner_text("#tp-case")[:160],
            "law_section_header": pg.inner_text("#tp-law .refsec-h") if pg.locator("#tp-law .refsec-h").count() else None,
            "draft_citations": pg.eval_on_selector_all(
                "#tp-law .cite-row", "es=>es.map(e=>e.textContent.replace(/\\s+/g,' ').trim())"),
            "divergence": pg.inner_text("#tp-law .diverge") if pg.locator("#tp-law .diverge").count() else None,
            "sent_count": pg.inner_text("#senttotal"),
            "wc": pg.inner_text("#wc"),
            "doc_title": pg.inner_text("#sheet .doc-title"),
        }
        # 點一句有算式的看算式卡
        eng = pg.locator("#sheet .sent").nth(6)
        if eng.count():
            eng.click()
            r["step2"]["inspect"] = pg.inner_text("#inspect")[:400]
            calc = pg.locator("#inspect .calc summary")
            if calc.count():
                calc.click()
                r["step2"]["calc_steps"] = pg.eval_on_selector_all(
                    "#inspect .calc li b", "es=>es.map(e=>e.textContent)")
                r["step2"]["calc_caveats"] = pg.eval_on_selector_all(
                    "#inspect .calc .cav p", "es=>es.map(e=>e.textContent)")
        pg.screenshot(path=f"{OUT}/{CASE}-step2.png", full_page=True)

        # 步驟 3：燈號審核
        pg.click("#go3")
        pg.wait_for_selector(".panel[data-p='3'].on")
        r["step3"] = {
            "lamps": {k: pg.inner_text("#n_" + k) for k in ("r", "y", "g", "t")},
            "go4_disabled": pg.is_disabled("#go4"),
            "gatetitle": pg.inner_text("#gatetitle"),
            "gatemsg": pg.inner_text("#gatemsg"),
            "blockers_visible": pg.is_visible("#blockerlist"),
            "blockers": pg.eval_on_selector_all("#blockerlist .bk", "es=>es.map(e=>e.textContent.trim())"),
            "handoff_visible": pg.is_visible("#handoffcard"),
            "handoff_questions": pg.eval_on_selector_all("#handoffbody ol li", "es=>es.map(e=>e.textContent)"),
            "handoff_signals": pg.eval_on_selector_all("#handoffbody .handoff-s span", "es=>es.map(e=>e.textContent)"),
            "handoff_criterion": pg.inner_text("#handoffbody .crit") if pg.locator("#handoffbody .crit").count() else None,
            "handoff_observation_label": pg.eval_on_selector_all("#handoffbody .obs-h", "es=>es.map(e=>e.textContent)"),
            "advmarks": pg.eval_on_selector_all("#rvinner .advmark",
                                                "es=>es.map(e=>({t:e.textContent,tip:e.dataset.tip}))"),
            "redlist": pg.eval_on_selector_all("#redlist button", "es=>es.map(e=>e.textContent.trim())"),
            "placeholder_sent": pg.eval_on_selector_all(
                "#rvinner .rs", "es=>es.filter(e=>e.textContent.includes('承辦人判斷')).map(e=>e.textContent)"),
        }
        # 點第一句紅燈看細節
        red = pg.locator("#redlist button").first
        if red.count():
            red.click()
            r["step3"]["detail"] = pg.inner_text("#detbody")[:500]
        pg.screenshot(path=f"{OUT}/{CASE}-step3.png", full_page=True)

        # 步驟 4：送出（只有閘門允許時）
        if not pg.is_disabled("#go4"):
            pg.click("#go4")
            pg.wait_for_selector(".panel[data-p='4'].on")
            r["step4"] = {
                "no": pg.inner_text("#r_no"), "type": pg.inner_text("#r_type"),
                "result": pg.inner_text("#r_result"), "lamp": pg.inner_text("#r_lamp"),
                "s_min": pg.inner_text("#s_min"),
                "s_min_label": pg.inner_text("#s_min_lb"),
                "s_sent": pg.inner_text("#s_sent"),
                "s_cite": pg.inner_text("#s_cite"),
                "s_cite_label": pg.inner_text("#s_cite_lb"),
                "s_lamp": pg.inner_text("#s_lamp"),
                "s_note": pg.inner_text("#s_note"),
            }
            pg.screenshot(path=f"{OUT}/{CASE}-step4.png", full_page=True)
        else:
            r["step4"] = "送出審議按鈕為 disabled，未進入步驟 5（預期行為）"

        r["js_errors"] = errors
        r["console_all"] = console
        b.close()
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 1 if errors else 0


sys.exit(main())
