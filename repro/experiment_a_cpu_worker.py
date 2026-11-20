"""CPU ORT reference worker — independent oracle session (fresh process).

Mission venv, CPU-only, same ONNX bytes + same input tensor bytes as the GPU
worker. Output npz feeds the by-name per-element parity check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--npy", required=True)
    ap.add_argument("--out-npz", required=True)
    ap.add_argument("--out-json", required=True)
    a = ap.parse_args()
    receipt: dict = {"worker": "cpu_reference"}
    try:
        import numpy as np
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.log_severity_level = 3
        sess = ort.InferenceSession(str(Path(a.onnx)), sess_options=so,
                                    providers=["CPUExecutionProvider"])
        x = np.load(a.npy)
        feeds = {sess.get_inputs()[0].name: np.ascontiguousarray(x, dtype=np.float32)}
        outs = sess.run(None, feeds)
        names = [o.name for o in sess.get_outputs()]
        np.savez(a.out_npz, **{n: v for n, v in zip(names, outs)})
        receipt.update({"status": "CPU_RUN_COMPLETE",
                        "providers": sess.get_providers(),
                        "onnx_sha256": sha256_file(Path(a.onnx))})
        Path(a.out_json).write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        return 0
    except Exception as e:  # noqa: BLE001
        receipt.update({"status": "CPU_RUN_FAILED",
                        "error": f"{type(e).__name__}: {e}",
                        "traceback_tail": traceback.format_exc()[-800:]})
        Path(a.out_json).write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        return 3


if __name__ == "__main__":
    sys.exit(main())
