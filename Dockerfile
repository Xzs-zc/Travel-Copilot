FROM node:22-slim AS frontend
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.11.0

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

RUN uv run playwright install --with-deps --only-shell chromium \
    && useradd --create-home --uid 10001 travel \
    && mkdir -p /app/data /app/media \
    && chown -R travel:travel /app /ms-playwright

COPY --chown=travel:travel backend ./backend
COPY --chown=travel:travel alembic ./alembic
COPY --chown=travel:travel evals ./evals
COPY --chown=travel:travel scripts ./scripts
COPY --chown=travel:travel alembic.ini ./
COPY --chown=travel:travel --from=frontend /app/frontend/dist ./frontend/dist

USER travel
EXPOSE 8765
ENTRYPOINT ["sh", "/app/scripts/docker-entrypoint.sh"]
CMD ["api"]
