#!/usr/bin/env python3
"""組裝 dist/index.html：模板 + engine.js + app.js + 示範案件 + 法規快照全內嵌（單檔）。

外部資源僅 Google Fonts（Noto Sans/Serif TC、IBM Plex Mono、Material Symbols）；
載不到時字型走系統 fallback、圖示由 app.js 的保底機制整批隱藏，功能不受影響。
"""
import json
import pathlib

root = pathlib.Path(__file__).parent


def blob(name: str) -> str:
    """壓成單行 JSON 並跳脫 </，避免提前關掉 <script>。"""
    data = json.loads((root / "data" / name).read_text())
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


out = ((root / "static/index.tmpl.html").read_text()
       .replace("__CASE__", blob("case-demo.json"))
       .replace("__SNAPSHOT__", blob("laws-snapshot.json"))
       .replace("/*__ENGINE__*/", (root / "static/engine.js").read_text())
       .replace("/*__APP__*/", (root / "static/app.js").read_text()))

for placeholder in ("__CASE__", "__SNAPSHOT__", "/*__ENGINE__*/", "/*__APP__*/"):
    assert placeholder not in out, f"未替換的注入點: {placeholder}"

dist = root / "dist"
dist.mkdir(exist_ok=True)
(dist / "index.html").write_text(out)
print(f"dist/index.html {len(out.encode()) / 1024:.0f} KB")
