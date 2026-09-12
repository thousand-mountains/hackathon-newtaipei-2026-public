# 訴願案件審理 AI 輔助 — Phase 0 backend
#
# 目標部署平台：AWS ECS Fargate（region us-west-2 奧勒岡）
# ⚠️ 賽方規範限 us-east-1／us-west-2，舊註解寫的東京是錯的（2026-09-12 更正）
# 建置 context 是**專案根目錄**，不是 backend/：
#
#   docker build -f backend/Dockerfile -t hack-appeal-backend:phase0 .
#
# 這樣才抓得到 backend/ 與 frontend/dist/。

FROM python:3.12-slim

# ── 執行期環境 ────────────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080
# 刻意不在映像檔裡設 RUN_MODE（2026-09-12）：
#   寫死 fixture 會讓「忘記給 env」靜默退化成離線重播，而畫面照樣演完——
#   評審點下去看到的是重播，簡報上的 Bedrock 數字卻對不上。
#   settings.py 的 DEFAULT_RUN_MODE 仍是 fixture（fail-safe，不會偷打模型），
#   但「要跑哪個檔位」由 task definition 單一決定，映像檔不插手。

WORKDIR /app

# ── poppler-utils（提供 pdftotext）────────────────────────────────
# **不是 optional。** `backend/intake/documents.py` 用 `pdftotext -layout` 抽 PDF 文字層；
# 找不到它時每一份 PDF 都退到 `pdf_visual`（整份餵模型看圖說話）——慢、貴、且準確度低。
# 2026-09-12 線上實測缺這個套件，症狀是每份上傳 PDF 都出現
# 「文字抽取不可用：pdftotext 不在 PATH」，而合成測資的卷證其實**都有文字層**。
# 失敗方式是靜默降級不是報錯，所以裝上去之後 `/api/health` 也加了一項檢查。
RUN apt-get update \
    && apt-get install --no-install-recommends -y poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# 先裝相依再複製程式碼，讓 layer cache 在改程式時仍然有效
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# 這一行**同時**帶進 `backend/assets/fonts/NotoSansTC-Regular.otf`（5.4 MiB），
# PDF 匯出靠它。不要為了縮 context 把 `backend/assets` 加進 `.dockerignore`：
# 沒有 CJK 字型時 PDF 是整片豆腐字（□□□）**而且不報錯**，本機有系統字型撐著測不出來。
# 2026-09-12 在一個零系統字型的 python:3.12-slim 容器裡實測過這條路徑。
COPY backend/ /app/backend/

# 前端建置產物（2026-09-12 起帶進映像檔）：
#   不帶的話容器的 GET / 回 503，評審打開部署網址只看得到 API 文件。
#   只複製 Vite 的 dist/，不帶 frontend/src、node_modules、docs/、knowledge/。
COPY frontend/dist/ /app/frontend/dist/

# 入庫清單（2026-09-12 起帶進映像檔）：
#   settings.py 的 kb_corpus_counts() 讀它來具名列出每一批語料的筆數。
#   不帶的話雲上的 /api/health 只會說「本機讀不到 manifest，故不報各批筆數」——
#   等於評審看到的版本少了那段誠實揭露。manifest 只記路徑、來源與 sha256，
#   不含任何內容或人名（見 scripts/build_manifest.py 的說明），可以進映像檔。
COPY data/manifest.json /app/data/manifest.json

# 非 root 執行（ECS task 的最小權限原則）
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/backend/output \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# 健康檢查：ECS target group 也打同一支
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status == 200 else 1)"

CMD ["sh", "-c", "uvicorn backend.api.app:app --host 0.0.0.0 --port ${PORT}"]
