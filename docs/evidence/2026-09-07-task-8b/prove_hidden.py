"""證明 .regenbar[hidden]{display:none} 是必要的：把那條規則拿掉，hidden 的那一列就現形。"""
import json
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page()
    pg.goto("http://127.0.0.1:8123/?case=synthetic-ordinary-01", wait_until="networkidle")
    pg.wait_for_function("document.querySelector('#sourcenote').textContent!==''", timeout=30000)
    out=pg.evaluate("""()=>{
      const el=document.getElementById('regenbar');
      el.hidden=true;
      const before=getComputedStyle(el).display;
      let removed=null;
      for(const ss of document.styleSheets){
        let rs; try{rs=[...ss.cssRules]}catch(_){continue}
        const i=rs.findIndex(r=>r.selectorText==='.regenbar[hidden]');
        if(i>=0){removed=rs[i].cssText; ss.deleteRule(i); break;}
      }
      return {hidden:el.hidden, display_with_rule:before, removed_rule:removed,
              display_without_rule:getComputedStyle(el).display};
    }""")
    print(json.dumps(out, ensure_ascii=False))
    b.close()
