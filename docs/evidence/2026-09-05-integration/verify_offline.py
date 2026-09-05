import json, sys
from playwright.sync_api import sync_playwright
DIST = sys.argv[1]; OUT = sys.argv[2]
errors=[]; console=[]
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1440,"height":1000})
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.on("console", lambda m: (console.append(f"{m.type}: {m.text}"),
          errors.append(f"console.{m.type}: {m.text}") if m.type=="error" else None))
    pg.goto("file://"+DIST); pg.wait_for_timeout(1500)
    r={"badge_mode":pg.locator("#modebadge").get_attribute("data-m"),
       "badge_text":pg.locator("#modebadge").inner_text().strip(),
       "badge_tip":(pg.locator("#modebadge").get_attribute("data-tip") or "")[:180],
       "prov":pg.locator("#prov").inner_text().strip(),
       "sourcenote":pg.inner_text("#sourcenote"),
       "casepick_visible":pg.is_visible("#casepick"),
       "demoload":pg.inner_text("#demoload")}
    pg.click("#demoload"); pg.wait_for_function("document.querySelector('#f_no').value!==''",timeout=10000)
    r["step0"]={"no":pg.input_value("#f_no"),"type":pg.input_value("#f_type"),
                "files":pg.eval_on_selector_all("#filelist li .meta b","es=>es.map(e=>e.textContent)"),
                "auto":pg.eval_on_selector_all(".field .auto","es=>es.filter(e=>e.textContent.trim()).map(e=>e.id)")}
    pg.screenshot(path=OUT+"/offline-step0.png", full_page=True)
    pg.click("#go1"); pg.wait_for_function("!document.querySelector('#go2').disabled",timeout=60000)
    r["step1"]={"progress":pg.inner_text("#agprog"),
                "console_head":pg.eval_on_selector_all("#console div","es=>es.slice(0,2).map(e=>e.textContent)")}
    pg.click("#go2"); pg.wait_for_selector(".panel[data-p='2'].on")
    r["step2"]={"tabs":pg.eval_on_selector_all(".tab","es=>es.map(e=>e.textContent.trim())"),
                "sents":pg.inner_text("#senttotal"),"title":pg.inner_text("#sheet .doc-title"),
                "law_verify":pg.eval_on_selector_all("#tp-law .ref .foot .tag","es=>es.map(e=>e.textContent)")}
    pg.click("#go3"); pg.wait_for_selector(".panel[data-p='3'].on")
    r["step3"]={"lamps":{k:pg.inner_text("#n_"+k) for k in ("r","y","g","t")},
                "go4_disabled":pg.is_disabled("#go4"),"gatetitle":pg.inner_text("#gatetitle"),
                "blockers_visible":pg.is_visible("#blockerlist"),"handoff_visible":pg.is_visible("#handoffcard"),
                "redlist_n":len(pg.eval_on_selector_all("#redlist button","es=>es"))}
    pg.screenshot(path=OUT+"/offline-step3.png", full_page=True)
    r["js_errors"]=errors; r["console_all"]=console
    b.close()
print(json.dumps(r,ensure_ascii=False,indent=1))
