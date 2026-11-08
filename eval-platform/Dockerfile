FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY eval-platform/frontend/package.json eval-platform/frontend/package-lock.json ./
RUN npm ci
COPY eval-platform/frontend/ ./
RUN npm run build

FROM python:3.12-slim AS test
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.0
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen
COPY backend ./backend
COPY eval-platform ./eval-platform
WORKDIR /app/eval-platform
CMD ["/app/.venv/bin/python", "-m", "pytest", "-q", "tests"]

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir uv==0.11.0
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY backend ./backend
COPY eval-platform ./eval-platform
COPY --from=frontend /app/frontend/dist ./eval-platform/frontend/dist

WORKDIR /app/eval-platform
EXPOSE 8770
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "platform_backend.main:app", "--host", "0.0.0.0", "--port", "8770"]
