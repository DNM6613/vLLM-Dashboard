FROM node:22-slim AS frontend-builder

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ipmitool \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash appuser

COPY requirements-lock.txt .
RUN pip install --no-cache-dir -r requirements-lock.txt

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
