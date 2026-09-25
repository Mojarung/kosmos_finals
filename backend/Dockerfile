FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN uv sync --project backend --frozen --no-dev --extra demo
RUN uv pip install --python /app/backend/.venv/bin/python \
    --index https://download.pytorch.org/whl/cpu \
    'torch==2.14.0+cpu' 'torchvision==0.29.0+cpu' \
    'segmentation-models-pytorch==0.5.0' 'safetensors>=0.6,<1' 'scipy>=1.16,<2'
COPY backend/app ./backend/app
COPY data/demo ./data/demo
COPY data/catalog ./data/catalog
COPY ml/src/floodvalue ./ml/src/floodvalue
COPY ml/artifacts/model ./ml/artifacts/model
COPY ml/artifacts/evaluation/calibration.json ./ml/artifacts/evaluation/calibration.json
COPY ml/artifacts/baseline/baseline.json ./ml/artifacts/baseline/baseline.json
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
ENV PATH="/app/backend/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/ml/src \
    LIVE_DATA_DIR=/app/data/live
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
