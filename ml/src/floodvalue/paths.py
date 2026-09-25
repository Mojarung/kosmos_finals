"""Filesystem layout of the pipeline; override roots with environment variables."""

import os
from pathlib import Path

PIPELINE_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("FLOODVALUE_DATA", PIPELINE_ROOT / "data"))
SEN1FLOODS11_DIR = DATA_DIR / "sen1floods11"
RUNS_DIR = Path(os.environ.get("FLOODVALUE_RUNS", PIPELINE_ROOT / "runs"))
CONFIGS_DIR = PIPELINE_ROOT / "configs"
ARTIFACTS_DIR = PIPELINE_ROOT / "artifacts"
