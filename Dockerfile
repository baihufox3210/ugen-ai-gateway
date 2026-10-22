# syntax=docker/dockerfile:1

# Build the dashboard.
# Built on the build machine's platform: the output is the same for every image.
FROM --platform=$BUILDPLATFORM node:22-slim AS dashboard
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# The gateway serves the API and the built dashboard from one port.
FROM python:3.13-slim
RUN pip install --no-cache-dir uv==0.11.32
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
COPY --from=dashboard /frontend/dist ./frontend/dist

RUN useradd --system --uid 1000 --home-dir /app ugen \
    && mkdir -p /app/data && chown ugen /app/data
USER ugen

ENV PATH=/app/.venv/bin:$PATH \
    HOST=0.0.0.0 \
    PORT=7632 \
    USAGE_DB_PATH=/app/data/ugen.db \
    STATIC_DIR=/app/frontend/dist
VOLUME /app/data
EXPOSE 7632
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"
CMD ["sh", "-c", "exec uvicorn ugen_ai_gateway.main:app --host \"$HOST\" --port \"$PORT\""]
