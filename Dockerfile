# ─────────────────────────────────────────────────────────────
# 1) 프론트(React) 빌드
# ─────────────────────────────────────────────────────────────
FROM node:24-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/.npmrc ./
COPY web/package-lock.json* ./
RUN npm install
COPY web/ ./
RUN npm run build

# ─────────────────────────────────────────────────────────────
# 2) Python 런타임 (FastAPI + 빌드된 SPA 단일 포트 서빙)
# ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS app
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# 의존성 → requirements 내보내 시스템 파이썬에 설치(venv 경로 문제 회피)
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /tmp/req.txt \
 && uv pip install --system -r /tmp/req.txt
# 앱 코드 + 빌드된 프론트
COPY *.py ./
COPY --from=web /web/dist ./web/dist
# 빌드 단계에서 /app 내용 확인 + api import 검증 (문제 있으면 여기서 명확히 실패)
RUN ls -la /app && python -c "import api" && echo "BUILD OK: api importable"
# Cloud Run 은 $PORT(기본 8080) 주입. 캐시는 GCS 볼륨 마운트 경로로.
ENV APP_ENV=prod PORT=8080 WAREHOUSE_CACHE=/mnt/cache
EXPOSE 8080
# 런처 스크립트로 기동(자기 위치를 sys.path에 넣어 api import 보장 — cwd/PYTHONPATH 무관)
CMD ["python", "/app/serve.py"]
