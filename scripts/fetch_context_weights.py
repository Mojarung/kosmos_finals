# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Download the context model weights into ml/artifacts/context_model and check every sha256.

The GitVerse copy has no room for these 696 MB in Git LFS, so they live in one zip on
Google Drive. Every file is checked against ml/artifacts/context_model/recipe.json, so a
wrong or damaged download never reaches the service.

    uv run scripts/fetch_context_weights.py                    # from the published link
    uv run scripts/fetch_context_weights.py --zip weights.zip  # from a file you already have
    uv run scripts/fetch_context_weights.py --next-optical     # next_optical package, if Git LFS is unavailable

The next_optical archive carries its own SHA256SUMS.txt; every file is checked against it, and
its recipe must be byte for byte the recipe of this checkout before anything is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ml" / "artifacts" / "context_model"
# Google Drive file id of context_model_weights.zip; the share link is ".../file/d/<id>/view".
DRIVE_FILE_ID = "1XjEdtb7nVy25i0AnklaBGTjbTFesOQFr"
# The same next_optical package as ml/artifacts/next_optical (Git LFS), as one zip of 234 MB.
NEXT_OPTICAL_ID = "1vb37LBhdCjupBtDoaJFoI3Y7osDJ-UnL"
NEXT_ROOT = "FloodValue_next_optical/"


def drive_download(file_id: str, target: Path) -> None:
    url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as output:
        if "text/html" in response.headers.get("content-type", ""):
            raise SystemExit("Google Drive returned a page instead of the file: check that the link is open to anyone")
        total = int(response.headers.get("content-length") or 0)
        done = 0
        while chunk := response.read(1 << 20):
            output.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r{done / 1e6:7.0f} / {total / 1e6:.0f} MB", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)


def file_id(value: str) -> str:
    match = re.search(r"/d/([\w-]{20,})|id=([\w-]{20,})", value)
    return (match.group(1) or match.group(2)) if match else value


def install(archive: Path) -> list[str]:
    recipe = json.loads((TARGET / "recipe.json").read_text(encoding="utf-8"))
    expected = {w["path"]: w["sha256"] for member in recipe["members"].values() for w in member["weights"]}
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        missing = sorted(set(expected) - names)
        if missing:
            raise SystemExit(f"the archive lacks {len(missing)} weight files, e.g. {missing[0]}")
        for path, digest in expected.items():
            data = bundle.read(path)
            if hashlib.sha256(data).hexdigest() != digest:
                raise SystemExit(f"sha256 mismatch for {path}: the archive is damaged or from another recipe")
            destination = TARGET / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
    return sorted(expected)


def install_next_optical(archive: Path) -> list[str]:
    with zipfile.ZipFile(archive) as bundle:
        for line in bundle.read(NEXT_ROOT + "SHA256SUMS.txt").decode("utf-8").splitlines():
            if not line.strip():
                continue
            digest, name = line.split(maxsplit=1)
            name = name.lstrip("*").strip()
            if hashlib.sha256(bundle.read(NEXT_ROOT + name)).hexdigest() != digest:
                raise SystemExit(f"sha256 mismatch for {name}: the archive is damaged")
        recipe = ROOT / "ml" / "artifacts" / "next_optical" / "recipe.json"
        packed = bundle.read(NEXT_ROOT + "ml/artifacts/next_optical/recipe.json")
        if hashlib.sha256(packed).digest() != hashlib.sha256(recipe.read_bytes()).digest():
            raise SystemExit("the archive holds another next_optical recipe than this checkout")
        prefix = NEXT_ROOT + "ml/artifacts/next_optical/"
        written = []
        for name in bundle.namelist():
            if name.startswith(prefix) and not name.endswith("/"):
                destination = ROOT / name[len(NEXT_ROOT):]
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(bundle.read(name))
                written.append(name[len(NEXT_ROOT):])
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zip", type=Path, help="an already downloaded context_model_weights.zip")
    parser.add_argument("--drive", help="Google Drive link or file id; defaults to the published one")
    parser.add_argument("--next-optical", action="store_true", help="install the next_optical package instead")
    args = parser.parse_args()
    if args.next_optical:
        with tempfile.TemporaryDirectory() as folder:
            archive = args.zip or Path(folder) / "next_optical.zip"
            if not args.zip:
                drive_download(file_id(args.drive or NEXT_OPTICAL_ID), archive)
            written = install_next_optical(archive)
        print(f"{len(written)} next_optical files checked against SHA256SUMS.txt and placed in ml/artifacts/next_optical")
        return
    if args.zip:
        installed = install(args.zip)
    else:
        source = args.drive or DRIVE_FILE_ID
        if not source:
            raise SystemExit("no Google Drive link is published yet: pass --drive <link> or --zip <file>")
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "context_model_weights.zip"
            drive_download(file_id(source), archive)
            installed = install(archive)
    print(f"{len(installed)} weight files checked against recipe.json and placed in {TARGET}")


if __name__ == "__main__":
    main()
