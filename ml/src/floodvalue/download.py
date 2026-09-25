"""Download the hand-labeled part of the official Sen1Floods11 bucket with md5 verification."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

BUCKET = "sen1floods11"
VERSION_PREFIX = "v1.1/"
LIST_URL = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o"
MEDIA_URL = f"https://storage.googleapis.com/{BUCKET}/"
DEFAULT_PREFIXES = (
    "v1.1/Sen1Floods11_Metadata.geojson",
    "v1.1/splits/flood_handlabeled/",
    "v1.1/data/flood_events/HandLabeled/S1Hand/",
    "v1.1/data/flood_events/HandLabeled/LabelHand/",
    "v1.1/data/flood_events/HandLabeled/JRCWaterHand/",
)
MANIFEST_NAME = "download_manifest.json"
RETRIES = 4
TIMEOUT_S = 60


@dataclass(frozen=True)
class RemoteObject:
    name: str
    size: int
    md5_b64: str
    generation: str
    updated: str

    @property
    def url(self) -> str:
        return MEDIA_URL + urllib.parse.quote(self.name)


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:
        return json.load(response)


def list_objects(prefix: str) -> list[RemoteObject]:
    """List bucket objects under a prefix through the public GCS JSON API."""
    objects: list[RemoteObject] = []
    token = None
    while True:
        params = {"prefix": prefix, "fields": "items(name,size,md5Hash,generation,updated),nextPageToken"}
        if token:
            params["pageToken"] = token
        page = _get_json(f"{LIST_URL}?{urllib.parse.urlencode(params)}")
        objects.extend(
            RemoteObject(item["name"], int(item["size"]), item["md5Hash"], item["generation"], item["updated"])
            for item in page.get("items", [])
            if not item["name"].endswith("/")
        )
        token = page.get("nextPageToken")
        if not token:
            return objects


def md5_b64(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return base64.b64encode(digest.digest()).decode("ascii")


def local_path(root: Path, name: str) -> Path:
    return root / name.removeprefix(VERSION_PREFIX)


def download_object(obj: RemoteObject, root: Path) -> Path:
    """Fetch one object unless a verified copy already exists; raise after RETRIES failures."""
    target = local_path(root, obj.name)
    if target.exists() and md5_b64(target) == obj.md5_b64:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(obj.url, timeout=TIMEOUT_S) as response:
                partial.write_bytes(response.read())
            if md5_b64(partial) != obj.md5_b64:
                raise OSError(f"md5 mismatch for {obj.name}")
            partial.replace(target)
            return target
        except OSError as error:
            last_error = error
            time.sleep(2**attempt)
    raise RuntimeError(f"failed to download {obj.name}") from last_error


def download(root: Path, prefixes: tuple[str, ...] = DEFAULT_PREFIXES, workers: int = 16) -> Path:
    """Download all objects under the prefixes and write a manifest with checksums."""
    objects = [obj for prefix in prefixes for obj in list_objects(prefix)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda obj: download_object(obj, root), objects))
    manifest = {
        "source": "https://github.com/cloudtostreet/Sen1Floods11",
        "bucket": f"gs://{BUCKET}",
        "version": VERSION_PREFIX.rstrip("/"),
        "prefixes": list(prefixes),
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "object_count": len(objects),
        "total_bytes": sum(obj.size for obj in objects),
        "objects": [{**asdict(obj), "url": obj.url} for obj in objects],
    }
    manifest_path = root / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path
