#!/usr/bin/env python3
"""組裝 dist/index.html：模板 + engine.js + app.js + snapshot + fixtures + vectors 全內嵌（單檔、零外部資源）。"""
import json
import pathlib

root = pathlib.Path(__file__).parent
tmpl = (root / "static/index.tmpl.html").read_text()
engine = (root / "static/engine.js").read_text()
app = (root / "static/app.js").read_text()
snapshot = json.dumps(json.loads((root / "data/laws-snapshot.json").read_text()), ensure_ascii=False, separators=(",", ":"))
fixtures = json.dumps(json.loads((root / "data/fixtures.json").read_text()), ensure_ascii=False, separators=(",", ":"))
vectors = json.dumps(json.loads((root / "data/test-vectors.json").read_text())["vectors"], ensure_ascii=False, separators=(",", ":"))

app = app.replace("__VECTORS__", vectors)
out = (tmpl.replace("__SNAPSHOT__", snapshot.replace("</", "<\\/"))
           .replace("__FIXTURES__", fixtures.replace("</", "<\\/"))
           .replace("/*__ENGINE__*/", engine)
           .replace("/*__APP__*/", app))
dist = root / "dist"
dist.mkdir(exist_ok=True)
(dist / "index.html").write_text(out)
print(f"dist/index.html {len(out.encode())/1024:.0f} KB")
