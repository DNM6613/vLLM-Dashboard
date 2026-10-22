FROM node:22-slim AS frontend-builder

# Audit 2026-10-22 INF-5: opt-in npm registry mirror. Empty (the default)
# keeps the official registry — identical to the previous build; intranet
# hosts can pass --build-arg NPM_REGISTRY=https://... to speed up npm ci.
ARG NPM_REGISTRY=""

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -n "$NPM_REGISTRY" ]; then npm ci --registry "$NPM_REGISTRY"; else npm ci; fi

COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

# Audit 2026-10-22 INF-5: opt-in pip index mirror (same semantics as the
# NPM_REGISTRY build-arg above).
ARG PIP_INDEX_URL=""

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ipmitool \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash appuser

COPY requirements-lock.txt .
RUN if [ -n "$PIP_INDEX_URL" ]; then \
        pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements-lock.txt; \
    else \
        pip install --no-cache-dir -r requirements-lock.txt; \
    fi

COPY backend/ ./backend/

COPY --from=frontend-builder /build/dist ./static

RUN mkdir -p /app/data && chown appuser:appuser /app/data

ENV API_HOST=0.0.0.0
ENV API_PORT=5174
ENV STATIC_DIR=/app/static
EXPOSE 5174

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import os,httpx;k=os.environ.get('VLLM_DASHBOARD_API_KEY') or os.environ.get('API_KEY') or '';p=os.environ.get('API_PORT') or '5174';httpx.get(f'http://localhost:{p}/health',headers={'X-API-Key':k} if k else {}).raise_for_status()"

CMD ["python", "-m", "backend.main"]
