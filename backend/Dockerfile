# 訴願案件審理 AI 輔助 — Phase 0 backend
#
# 目標部署平台：AWS ECS Fargate（region us-west-2 奧勒岡）
# ⚠️ 賽方規範限 us-east-1／us-west-2，舊註解寫的東京是錯的（2026-09-12 更正）
# 建置 context 是**專案根目錄**，不是 backend/：
#
#   docker build -f backend/Dockerfile -t hack-appeal-backend:phase0 .
#
# 這樣才抓得到 backend/ 與 prototype/dist/。

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

# 先裝相依再複製程式碼，讓 layer cache 在改程式時仍然有效
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/ /app/backend/

# 前端建置產物（2026-09-12 起帶進映像檔）：
#   不帶的話容器的 GET / 回 503，評審打開部署網址只看得到 API 文件。
#   只複製 dist/（單檔全內嵌），不帶 static/ 原始碼、docs/、knowledge/。
COPY prototype/dist/ /app/prototype/dist/

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
