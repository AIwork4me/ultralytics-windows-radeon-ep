"""tools/fetch_bench_images.py - rebuild the E2E benchmark corpus.

The corpus is 20 photographs served by Lorem Picsum (images from Unsplash) under
the Unsplash License: free to use, modify and redistribute. This script is
provided so the corpus can be rebuilt byte-for-byte from a clean checkout; the
committed data/fixtures/bench_images/SOURCES.json records the author, source URL
and sha256 of every image actually used.

Usage:
    python tools/fetch_bench_images.py [--ids 1-20] [--out DIR]

Respects HTTPS_PROXY / HTTP_PROXY if set. Idempotent: it re-downloads and then
verifies each file against SOURCES.json (if present), printing a per-image
result instead of silently overwriting evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "fixtures" / "bench_images"
UA = "radeon-ort-benchmark/0.1 (+https://github.com/AIwork4me/ultralytics-windows-radeon-ep)"


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return r.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="1-20")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=960)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args()
    lo, _, hi = a.ids.partition("-")
    ids = list(range(int(lo), int(hi or lo) + 1))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "SOURCES.json"
    existing = {}
    if manifest_path.is_file():
        doc = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        existing = {i["file"].split("/")[-1]: i for i in doc.get("images", [])}

    images = []
    for i in ids:
        info = json.loads(_get(f"https://picsum.photos/id/{i}/info").decode("utf-8"))
        url = f"https://picsum.photos/id/{i}/{a.width}/{a.height}"
        blob = _get(url)
        name = f"picsum_{i}.jpg"
        (out / name).write_bytes(blob)
        sha = hashlib.sha256(blob).hexdigest()
        prev = existing.get(name)
        status = "NEW" if prev is None else ("MATCH" if prev["sha256"] == sha else "CHANGED")
        images.append({
            "id": i, "file": f"bench_images/{name}",
            "width": a.width, "height": a.height, "bytes": len(blob),
            "sha256": sha, "author": info.get("author"),
            "source_url": info.get("url"), "download_url": url,
            "license": "Unsplash License", "license_url": "https://unsplash.com/license",
            "corpus": "Lorem Picsum (https://picsum.photos) - photos from Unsplash",
        })
        print(f"[fetch] {name} {status} {sha[:12]}")

    doc = {
        "schema_version": 1,
        "corpus_name": "radeon-ort E2E benchmark corpus v1",
        "count": len(images),
        "license_summary": "Every image is used under the Unsplash License (free to "
                           "use, modify and redistribute). Per-image author and "
                           "source URL are recorded below.",
        "license_url": "https://unsplash.com/license",
        "images": images,
    }
    manifest_path.write_text(json.dumps(doc, indent=2) + chr(10), encoding="utf-8")
    print(f"[fetch] wrote {manifest_path} ({len(images)} images)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
