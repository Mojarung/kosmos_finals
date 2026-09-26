FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN uv sync --project backend --frozen --no-dev --extra demo
# The PyTorch index also carries an old requests (2.28.1); without best-match uv takes only that one
# and pystac-client 0.9.0 (requests>=2.28.2) cannot be installed.
RUN uv pip install --python /app/backend/.venv/bin/python \
    --index https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match \
    'torch==2.14.0+cpu' 'torchvision==0.29.0+cpu' \
    'segmentation-models-pytorch==0.5.0' 'safetensors>=0.6,<1' 'scipy>=1.16,<2' 'scikit-learn==1.9.1' 'lightgbm==4.7.0' 'catboost==1.2.10' \
    'pystac-client==0.9.0' 'planetary-computer==1.0.0'
COPY backend/app ./backend/app
COPY data/demo ./data/demo
COPY data/catalog ./data/catalog
COPY data/backtest ./data/backtest
COPY ml/src/floodvalue ./ml/src/floodvalue
COPY ml/artifacts/model ./ml/artifacts/model
COPY ml/artifacts/next_optical ./ml/artifacts/next_optical
COPY ml/artifacts/context_model ./ml/artifacts/context_model
COPY ml/artifacts/evaluation/calibration.json ./ml/artifacts/evaluation/calibration.json
COPY ml/artifacts/evaluation/doubt_threshold.json ./ml/artifacts/evaluation/doubt_threshold.json
COPY ml/artifacts/evaluation/reliability_test_core.json ./ml/artifacts/evaluation/reliability_test_core.json
COPY ml/artifacts/baseline/baseline.json ./ml/artifacts/baseline/baseline.json
COPY ml/artifacts/rtc ./ml/artifacts/rtc
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
# The image has no .git: pass the commit at build time so run_metadata.json can name it.
ARG FLOODVALUE_COMMIT=
RUN test -n "$FLOODVALUE_COMMIT"
ENV FLOODVALUE_COMMIT=${FLOODVALUE_COMMIT} \
    PATH="/app/backend/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/ml/src \
    LIVE_DATA_DIR=/app/data/live
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
