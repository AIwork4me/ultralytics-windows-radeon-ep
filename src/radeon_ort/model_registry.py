"""Local model registry: hashes, provenance, no double downloads, never committed."""

from __future__ import annotations

import json
from pathlib import Path

from .manifest import sha256_file


class ModelRegistry:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "pt").mkdir(parents=True, exist_ok=True)
        (self.root / "onnx").mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "registry_index.json"
        self.index = json.loads(self.index_path.read_text(encoding="utf-8")) if self.index_path.exists() else {"models": {}}

    def pt_path(self, checkpoint: str) -> Path:
        return self.root / "pt" / checkpoint

    def onnx_path(self, checkpoint: str, tag: str) -> Path:
        stem = Path(checkpoint).stem
        return self.root / "onnx" / f"{stem}.{tag}.onnx"

    def register_pt(self, checkpoint: str, source_url: str) -> dict:
        p = self.pt_path(checkpoint)
        entry = {
            "checkpoint": checkpoint,
            "exists": p.is_file(),
            "sha256": sha256_file(p) if p.is_file() else None,
            "bytes": p.stat().st_size if p.is_file() else 0,
            "source_url": source_url,
        }
        self.index["models"][f"pt::{checkpoint}"] = entry
        self._flush()
        return entry

    def register_onnx(self, checkpoint: str, tag: str, export_args: dict) -> dict:
        p = self.onnx_path(checkpoint, tag)
        entry = {
            "checkpoint": checkpoint, "tag": tag,
            "exists": p.is_file(),
            "sha256": sha256_file(p) if p.is_file() else None,
            "bytes": p.stat().st_size if p.is_file() else 0,
            "export_args": export_args,
        }
        self.index["models"][f"onnx::{checkpoint}::{tag}"] = entry
        self._flush()
        return entry

    def _flush(self):
        self.index_path.write_text(json.dumps(self.index, indent=2), encoding="utf-8")

    def provenance_csv_rows(self) -> list[list[str]]:
        rows = [["kind", "checkpoint", "tag", "sha256", "bytes", "source"]]
        for key, e in sorted(self.index["models"].items()):
            kind = key.split("::")[0]
            rows.append([kind, e["checkpoint"], e.get("tag", ""), e["sha256"] or "",
                         str(e["bytes"]), e.get("source_url", "")])
        return rows
