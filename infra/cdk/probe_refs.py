#!/usr/bin/env python3
"""從 index.html 反推「當下實際對外服務的靜態掛載前綴」。

為什麼要反推而不是寫死一份路徑清單：掛載點會變，而且已經變過一次——
  舊版  app.mount("/static", StaticFiles(dist))        → 整個 dist 對外
  新版  app.mount("/assets", StaticFiles(dist/assets)) → 只有 assets/，且 /static 不存在

寫死清單的後果是：換版後那些路徑全部 404，檢查全綠，
但綠的理由是「掛載點沒了」而不是「資料被保護」，真正的暴露面一條都沒測到。
一條因為錯誤理由而通過的檢查，比沒有檢查更糟。

用法：
    cat index.html | probe_refs.py refs      # 站內絕對路徑資源，一行一個
    cat index.html | probe_refs.py prefixes  # 上述資源的目錄前綴，去重
"""
import posixpath
import re
import sys

# src="/assets/app.js" / href='/assets/app.css'，只取站內絕對路徑（排除 //cdn 這種協定相對）
ATTR = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def refs(html: str) -> list[str]:
    found = {
        u
        for u in (m.group(1) for m in ATTR.finditer(html))
        if u.startswith("/") and not u.startswith("//")
    }
    return sorted(found)


def prefixes(html: str) -> list[str]:
    out = set()
    for u in refs(html):
        d = posixpath.dirname(u)
        if d and d != "/":
            out.add(d)
    return sorted(out)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "refs"
    html = sys.stdin.read()
    if mode == "refs":
        lines = refs(html)
    elif mode == "prefixes":
        lines = prefixes(html)
    else:
        print(f"未知模式：{mode}（可用：refs／prefixes）", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
