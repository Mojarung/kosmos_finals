"""Run event CV after the four full-split boosting screens finish."""
import time
from pathlib import Path
import subprocess
import sys

from floodvalue.next_boost import CONFIGS

while not all((Path("runs/next/boost")/name/"full/ranking.json").exists() for name in CONFIGS):
    time.sleep(10)
subprocess.run([sys.executable, "-u", "-m", "floodvalue.next_boost", "--stage", "cv"], check=True)
