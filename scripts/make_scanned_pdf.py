#!/usr/bin/env python3
"""造一份「掃描件」樣本 PDF：合成測資的文字排成圖片、存成無文字層的 PDF（AC16 用）。

    uv run --with pillow -- python3 scripts/make_scanned_pdf.py
    uv run --with pillow -- python3 scripts/make_scanned_pdf.py --out /tmp/other.pdf

讀 `backend/data/synthetic/synthetic-ordinary-01.json` 的 `documents[]`，逐份用 Pillow
畫成整頁點陣圖再存成多頁 PDF；因為頁面是圖片、PDF 本身沒有文字層，`pdftotext` 抽不出
任何字——這正是要餵給 `backend.intake.documents.route_documents()` 判成 `pdf_visual`
的樣本（spec 2026-09-07 §5.8）。

字型固定用系統內建的 STHeiti Light，找不到就退回 PingFang；兩個都沒有就直接說明白，
不要生一份看起來像 PDF、實際上全白畫面的假樣本。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print(
        "缺少 Pillow：用 `uv run --with pillow -- python3 scripts/make_scanned_pdf.py` 執行本腳本",
        file=sys.stderr,
    )
    sys.exit(2)

FIXTURE = pathlib.Path("backend/data/synthetic/synthetic-ordinary-01.json")
DEFAULT_OUT = pathlib.Path("/tmp/synthetic-scan.pdf")
FONT_CANDIDATES = (
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)
PAGE_SIZE = (1240, 1754)  # A4 @ 150dpi 上下
FONT_SIZE = 28
LINE_HEIGHT = 44
MARGIN = 80


def _load_font() -> "ImageFont.FreeTypeFont":
    for candidate in FONT_CANDIDATES:
        if pathlib.Path(candidate).exists():
            return ImageFont.truetype(candidate, FONT_SIZE)
    tried = "、".join(FONT_CANDIDATES)
    print(f"找不到可用字型（試過：{tried}）；本機沒有這些系統字型，無法產生中文掃描件樣本", file=sys.stderr)
    sys.exit(2)


def render_pages(documents: list[dict], font: "ImageFont.FreeTypeFont") -> list["Image.Image"]:
    """把每份文件的 `n`（檔名）＋`text` 排成一頁點陣圖，不寫入任何文字層。"""
    pages = []
    for doc in documents:
        img = Image.new("RGB", PAGE_SIZE, "white")
        draw = ImageDraw.Draw(img)
        y = MARGIN
        header = f"《{doc['n']}》\n" + doc["text"]
        for line in header.splitlines():
            draw.text((MARGIN, y), line, fill="black", font=font)
            y += LINE_HEIGHT
        pages.append(img)
    return pages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT, help=f"輸出路徑（預設 {DEFAULT_OUT}）")
    parser.add_argument("--fixture", type=pathlib.Path, default=FIXTURE, help=f"來源 fixture（預設 {FIXTURE}）")
    args = parser.parse_args()

    fx = json.loads(args.fixture.read_text(encoding="utf-8"))
    documents = fx.get("documents") or []
    if not documents:
        print(f"{args.fixture} 裡沒有 documents[]，沒東西可畫", file=sys.stderr)
        sys.exit(2)

    font = _load_font()
    pages = render_pages(documents, font)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(args.out, save_all=True, append_images=pages[1:])
    print(args.out)


if __name__ == "__main__":
    main()
