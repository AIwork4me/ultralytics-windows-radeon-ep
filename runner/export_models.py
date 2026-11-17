"""python -m runner.export_models — download (if allowed) + export catalog PTs to ONNX."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from radeon_ort.model_export import run_export
from radeon_ort.model_registry import ModelRegistry
from runner import REPO_ROOT


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default=str(REPO_ROOT / "catalog" / "model_catalog.json"))
    ap.add_argument("--models-root", default=str(REPO_ROOT / "models"))
    ap.add_argument("--export-python", required=True, help="python of .venv-export")
    ap.add_argument("--allow-asset-download", action="store_true")
    ap.add_argument("--model", default=None)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    cat = json.loads(Path(a.catalog).read_text(encoding="utf-8"))
    entries = [e for e in cat["entries"].values() if e.get("eligibility") == "eligible"]
    if a.model:
        entries = [e for e in entries if a.model.lower() in e["checkpoint"].lower()]
    if a.limit:
        entries = entries[: a.limit]
    registry = ModelRegistry(a.models_root)

    for entry in entries:
        pt = registry.pt_path(entry["checkpoint"])
        if not pt.is_file():
            if a.allow_asset_download:
                pass
            print(f"[export] SKIP {entry['checkpoint']} (pt missing; download gate)")
            continue
        tag = f"{entry.get('nms_mode', 'raw')}_b{entry.get('batch', 1)}_fp32_static"
        onnx_p = registry.onnx_path(entry["checkpoint"], tag)
        if onnx_p.is_file():
            print(f"[export] SKIP {entry['checkpoint']} (onnx exists)")
            continue
        r = run_export(a.export_python, str(pt), str(onnx_p), imgsz=entry.get("imgsz_default"))
        if r["returncode"] == 0 and r["payload"]:
            registry.register_onnx(entry["checkpoint"], tag, r["payload"]["export_args"])
            print(f"[export] OK {entry['checkpoint']} sha={r['payload']['sha256'][:12]}")
        else:
            print(f"[export] FAIL {entry['checkpoint']}: {r['stderr_tail'][-300:]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
