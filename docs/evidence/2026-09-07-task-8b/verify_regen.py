"""Task 8b：每卡重新產生列驗證（fixture 檔位，POST /runs 同步回 200，跑不到 SSE 路徑）。

跑法：uv run --with playwright -- python verify_regen.py
"""
import json
import sys

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8123/?case=synthetic-ordinary-01"
DIST = "/Users/claireliang/Desktop/mytest/fish-in-water/hackathon-newtaipei-2026/prototype/dist/index.html"
OUT = "/tmp/t8b"
r = {}
errs = []


def meta(pg):
    return pg.evaluate("()=>({run_id:LIVE.run_id, rm:LIVE.run_meta, conf:(LIVE.intake_confirmed||[]).length})")


def regen(pg, node):
    pg.click(f"#regenbar button[data-from='{node}']")
    pg.wait_for_function(
        "document.querySelector('#go1hint').textContent.indexOf('已從')===0", timeout=60000)


def agents_done(pg):
    pg.wait_for_function("document.querySelector('#agprog').textContent==='7 / 7'", timeout=60000)


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 1000})
    pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    pg.on("console", lambda m: errs.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    pg.goto(URL, wait_until="networkidle")
    # bootApp() 綁 #demoload 之前按下去會靜默沒反應（boot() 的 postRun 還在飛）
    pg.wait_for_function("document.querySelector('#sourcenote').textContent!==''", timeout=30000)
    pg.click("#demoload")
    pg.wait_for_function("document.querySelector('#f_no').value!==''", timeout=15000)

    r["regenbar_hidden_live"] = pg.eval_on_selector("#regenbar", "e=>e.hidden")
    r["buttons"] = pg.eval_on_selector_all(
        "#regenbar button[data-from]", "es=>es.map(e=>[e.dataset.from, e.textContent.trim()])")
    pg.screenshot(path=f"{OUT}/regen-bar.png", clip={"x": 0, "y": 250, "width": 780, "height": 430})

    # 先走一次正常動線讓 agentsRan=true（重跑要重繪幕僚團那一段）
    pg.check("#f_confirm")
    pg.click("#go1")
    # go1 是 async（runConfirmed 先打後端才 goto(1)）：等進到步驟 2 再等進度，
    # 不然 agprog 還留著上一次的 7 / 7，等待條件會立刻通過。
    pg.wait_for_selector(".panel[data-p='1'].on", timeout=60000)
    agents_done(pg)
    base = meta(pg)
    r["after_go1"] = {k: base["rm"].get(k) for k in ("run_id", "from_node", "base_run_id")}
    pg.click(".step[data-i='0']")

    # (1) n5：重新產生草稿
    regen(pg, "n5")
    m5 = meta(pg)
    r["n5"] = {"hint": pg.inner_text("#go1hint"), "run_id": m5["run_id"],
               "from_node": m5["rm"].get("from_node"), "base_run_id": m5["rm"].get("base_run_id"),
               "node_timings": list((m5["rm"].get("node_timings") or {}).keys()),
               "f_confirm": pg.is_checked("#f_confirm"), "intake_confirmed_n": m5["conf"]}
    if m5["rm"].get("from_node") != "n5":
        errs.append("n5: run_meta.from_node 不是 n5")
    if m5["rm"].get("base_run_id") != base["run_id"]:
        errs.append("n5: base_run_id 不等於前一次的 run_id")
    if not pg.is_checked("#f_confirm"):
        errs.append("n5: 承辦人確認被無故清掉（n5 不動 intake）")
    pg.screenshot(path=f"{OUT}/regen-n5.png", clip={"x": 0, "y": 250, "width": 780, "height": 430})
    agents_done(pg)
    r["n5_agprog"] = pg.inner_text("#agprog")

    # (2) n4 ＋ 查詢詞
    pg.fill("#regen-query", "行政程序法 送達 期間")
    regen(pg, "n4")
    m4 = meta(pg)
    r["n4"] = {"hint": pg.inner_text("#go1hint"), "from_node": m4["rm"].get("from_node"),
               "base_run_id": m4["rm"].get("base_run_id"), "overrides": m4["rm"].get("overrides"),
               "node_timings": list((m4["rm"].get("node_timings") or {}).keys())}
    if (m4["rm"].get("overrides") or {}).get("n4_query") != "行政程序法 送達 期間":
        errs.append("n4: run_meta.overrides.n4_query 沒有帶到")
    if m4["rm"].get("base_run_id") != m5["run_id"]:
        errs.append("n4: base_run_id 不是上一次重跑的 run_id")
    pg.screenshot(path=f"{OUT}/regen-n4.png", clip={"x": 0, "y": 250, "width": 780, "height": 430})
    agents_done(pg)

    # (3) n1：不帶 base_run_id、查詢詞不外溢、欄位重填、承辦人確認清空
    regen(pg, "n1")
    pg.wait_for_function(
        "document.querySelector('#go1hint').textContent.indexOf('欄位已換成本次抽取的結果')>0", timeout=15000)
    m1 = meta(pg)
    r["n1"] = {"hint": pg.inner_text("#go1hint"), "from_node": m1["rm"].get("from_node"),
               "base_run_id": m1["rm"].get("base_run_id"), "overrides": m1["rm"].get("overrides"),
               "node_timings": list((m1["rm"].get("node_timings") or {}).keys()),
               "intake_confirmed_n": m1["conf"], "f_confirm": pg.is_checked("#f_confirm"),
               "confirmnote": pg.inner_text("#confirmnote")[:80], "f_no": pg.input_value("#f_no"),
               "sourcenote": pg.inner_text("#sourcenote")}
    if m1["rm"].get("from_node") != "n1":
        errs.append("n1: run_meta.from_node 不是 n1")
    if m1["rm"].get("base_run_id"):
        errs.append("n1: 不該帶 base_run_id")
    if m1["rm"].get("overrides"):
        errs.append("n1: 查詢詞外溢到 n1")
    if m1["conf"] == 0 and pg.is_checked("#f_confirm"):
        errs.append("n1: 後端確認已清空，畫面上的勾選框還打著勾")
    if m1["run_id"] not in r["n1"]["sourcenote"]:
        errs.append("n1: 資料來源那一行還留著上一次的 run_id：" + r["n1"]["sourcenote"])
    pg.screenshot(path=f"{OUT}/regen-n1.png", clip={"x": 0, "y": 250, "width": 780, "height": 430})
    agents_done(pg)

    # (4) 重新確認後再重跑一次 n5，走到步驟 5 看時間軸容忍缺格
    pg.check("#f_confirm")
    pg.click("#go1")
    # go1 是 async（runConfirmed 先打後端才 goto(1)）：等進到步驟 2 再等進度，
    # 不然 agprog 還留著上一次的 7 / 7，等待條件會立刻通過。
    pg.wait_for_selector(".panel[data-p='1'].on", timeout=60000)
    agents_done(pg)
    pg.click(".step[data-i='0']")
    regen(pg, "n5")
    agents_done(pg)
    pg.click(".step[data-i='1']")
    pg.click("#go2")
    pg.click("#go3")
    pg.wait_for_selector(".panel[data-p='3'].on")
    r["gate"] = {"go4_disabled": pg.is_disabled("#go4"), "title": pg.inner_text("#gatetitle"),
                 "reds": pg.eval_on_selector_all("#redlist button", "es=>es.length")}
    if pg.is_disabled("#go4"):
        errs.append("步驟 4 閘門在重新確認後仍關著：" + pg.inner_text("#gatemsg")[:120])
    else:
        pg.click("#go4")
        pg.wait_for_selector(".panel[data-p='4'].on", timeout=60000)
        r["s_min"] = pg.inner_text("#s_min")
        r["s_min_lb"] = pg.inner_text("#s_min_lb")
        if "本次重跑 2 節點" not in r["s_min_lb"]:
            errs.append("步驟 5 時間軸沒有照實說只重跑兩個節點：" + r["s_min_lb"])
        pg.screenshot(path=f"{OUT}/regen-step5.png", full_page=True)

    # (5) 離線 fixture（file:// 直開）：整列藏起來
    pg.goto("file://" + DIST)
    pg.wait_for_timeout(1000)
    r["offline_badge"] = pg.eval_on_selector("#modebadge", "e=>e.dataset.m")
    r["regenbar_hidden_offline"] = pg.eval_on_selector("#regenbar", "e=>e.hidden")
    # hidden 屬性有設不等於真的看不到：作者樣式的 display:flex 會壓過 UA 的 [hidden]
    r["regenbar_display_offline"] = pg.eval_on_selector("#regenbar", "e=>getComputedStyle(e).display")
    r["regenbar_visible_offline"] = pg.is_visible("#regenbar")
    if r["regenbar_display_offline"] != "none" or r["regenbar_visible_offline"]:
        errs.append("離線模式的重新產生列 hidden 了卻還看得見：display=" + r["regenbar_display_offline"])
    if r["offline_badge"] != "offline":
        errs.append("file:// 直開沒有進離線模式")
    if not r["regenbar_hidden_offline"]:
        errs.append("離線模式沒有把重新產生列藏起來")
    b.close()

r["errors"] = errs
print(json.dumps(r, ensure_ascii=False, indent=1))
sys.exit(1 if errs else 0)
