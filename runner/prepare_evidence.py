"""python -m runner.prepare_evidence — relocate raw tensors to LOCAL_ONLY.

Policy: .pt/.onnx weights, .npy inputs and .npz candidate/reference tensors are
NEVER published. They are moved outside the evidence tree, hashed, and listed in
LOCAL_ONLY_INDEX.json so a third party knows what existed and can regenerate the
inputs from the persisted manifest + image.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from runner import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "src"))

from radeon_ort.manifest import sha256_file  # noqa: E402

BINARY_EXT = {".npy", ".npz", ".pt", ".onnx", ".pth"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--dest", required=True, help="LOCAL_ONLY destination root")
    a = ap.parse_args()
    run_dir = Path(a.run_dir)
    dest = Path(a.dest) / run_dir.name
    dest.mkdir(parents=True, exist_ok=True)
    moved = []
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in BINARY_EXT:
            continue
        rel = p.relative_to(run_dir)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = sha256_file(p)
        size = p.stat().st_size
        shutil.move(str(p), str(target))
        moved.append({"path": rel.as_posix(), "sha256": digest, "bytes": size,
                      "reason": "raw tensor/weight: LOCAL_ONLY, not published"})
        print(f"[prepare] LOCAL_ONLY {rel} ({size} B)")
    idx = {
        "schema_version": 1,
        "run_dir": run_dir.name,
        "local_only_root": "%USERPROFILE%/../local_evidence/" + run_dir.name,
        "policy": "weights/tensors are not published; regenerate inputs from the "
                  "persisted manifest + image; receipts keep hashes",
        "files": moved,
    }
    out = run_dir / "LOCAL_ONLY_INDEX.json"
    out.write_text(json.dumps(idx, indent=2), encoding="utf-8")
    print(f"[prepare] moved {len(moved)} binary artifact(s) -> {idx['local_only_root']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
