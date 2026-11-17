"""SHA256 evidence manifest: build, verify, tamper detection."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_manifest(root: str | Path, run_id: str, generator: str, *,
                   relative_to: str | Path | None = None) -> dict:
    """Walk root (sorted, deterministic), hash every regular file."""
    root = Path(root)
    base = Path(relative_to) if relative_to else root
    entries = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(base).as_posix()
            entries.append({"path": rel, "sha256": sha256_file(p), "bytes": p.stat().st_size})
    return {
        "schema_version": 1,
        "run_id": run_id,
        "created_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": generator,
        "entries": entries,
    }


def write_manifest(root: str | Path, run_id: str, generator: str, *, out_name: str = "manifest.sha256") -> Path:
    m = build_manifest(root, run_id, generator)
    out = Path(root) / out_name
    # manifest must not hash itself
    entries = [e for e in m["entries"] if e["path"] != out_name]
    m["entries"] = entries
    out.write_text(json.dumps(m, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return out


def verify_manifest(root: str | Path, manifest_path: str | Path) -> dict:
    """Verify every entry; also detect files present on disk but missing from manifest.

    Returns {'ok': bool, 'mismatches': [...], 'unlisted': [...], 'missing': [...]}
    """
    root = Path(root)
    m = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    mismatches, missing, unlisted = [], [], []
    listed = set()
    for e in m["entries"]:
        listed.add(e["path"])
        p = root / e["path"]
        if not p.is_file():
            missing.append(e["path"])
            continue
        actual = sha256_file(p)
        if actual != e["sha256"] or p.stat().st_size != e["bytes"]:
            mismatches.append({"path": e["path"], "expected": e["sha256"], "actual": actual})
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            if rel not in listed and rel != Path(manifest_path).name:
                unlisted.append(rel)
    return {
        "ok": not (mismatches or missing or unlisted),
        "mismatches": mismatches,
        "missing": missing,
        "unlisted": unlisted,
    }
