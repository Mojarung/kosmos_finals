FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN uv sync --project backend --frozen --no-dev
COPY backend/app ./backend/app
COPY data/demo ./data/demo

WORKDIR /app/backend
ENV PATH="/app/backend/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
