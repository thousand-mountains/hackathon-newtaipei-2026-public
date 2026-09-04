# 訴願案件審理 AI 輔助 — Phase 0 backend
#
# 目標部署平台：AWS ECS Fargate（region ap-northeast-1 東京）
# 建置 context 是**專案根目錄**，不是 backend/：
#
#   docker build -f backend/Dockerfile -t hack-appeal-backend:phase0 .
#
# 這樣才抓得到 backend/ 整包。prototype/ 不進映像檔。

FROM python:3.12-slim

# ── 執行期環境 ────────────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    RUN_MODE=fixture

WORKDIR /app

# 先裝相依再複製程式碼，讓 layer cache 在改程式時仍然有效
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# 只複製 backend/，不帶 prototype/、docs/、knowledge/
COPY backend/ /app/backend/

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
