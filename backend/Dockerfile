# syntax=docker/dockerfile:1
# Сборка из КОРНЯ репозитория:
#   docker build -f backend/Dockerfile -t xmldt-backend .

# ---- builder: зависимости из uv.lock ----
FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM python:3.12.12-slim AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app/backend

# 1) только зависимости (кэшируется, пока не меняются pyproject/uv.lock)
COPY backend/pyproject.toml backend/uv.lock backend/.python-version backend/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --no-editable

# 2) сам проект (не editable: venv самодостаточен)
COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---- runtime ----
FROM python:3.12.12-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    XMLDT_DATA=/app/data \
    XMLDT_VAR=/var/lib/xmldt

RUN groupadd --system --gid 10001 xmldt \
 && useradd --system --uid 10001 --gid xmldt --home-dir /app --shell /usr/sbin/nologin xmldt \
 && mkdir -p /var/lib/xmldt \
 && chown xmldt:xmldt /var/lib/xmldt

COPY --from=builder /opt/venv /opt/venv
# Справочные данные только для чтения (root:root, без права записи для xmldt)
COPY data/schemas /app/data/schemas
COPY data/norms /app/data/norms
COPY data/kb /app/data/kb
COPY data/derived /app/data/derived
COPY data/samples /app/data/samples

WORKDIR /app
USER xmldt
VOLUME ["/var/lib/xmldt"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/catalog', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "xmldt.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
