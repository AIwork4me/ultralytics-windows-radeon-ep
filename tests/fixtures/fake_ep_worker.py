"""Deterministic stub EP worker for isolation / lifecycle tests.

It implements the same CLI contract as runner._ep_worker (--case, --out) but
needs no DXGI, ONNX Runtime or GPU, so the attempt-isolation rules can be
tested hermetically on any Windows CI runner.

Behaviour is selected by case["fake_mode"]:

  ok             valid fragment + outputs, exit 0
  fail           fragment with an error, exit 31
  no_outputs     valid fragment, no outputs npz, exit 0
  wrong_attempt  fragment echoing a DIFFERENT attempt_id
  hang           writes a partial outputs file then blocks (timeout path)
  bad_feed       refuses when expected_feed_sha256 does not match the feeds
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np


def _tensor_sha(a) -> str:
    """Same contract as radeon_ort.input_contract.tensor_sha256."""
    hh = hashlib.sha256()
    hh.update((str(a.dtype) + "|" + str(a.shape) + "|").encode())
    hh.update(np.ascontiguousarray(a).tobytes())
    return hh.hexdigest()


def _feed_sha(path: str) -> str:
    """Same contract as radeon_ort.unified_executors._feed_fingerprint."""
    z = np.load(path)
    h = hashlib.sha256()
    for k in sorted(z.files):
        a = np.ascontiguousarray(np.asarray(z[k], dtype=np.float32))
        h.update(k.encode() + b"|")
        h.update(_tensor_sha(a).encode())
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    case = json.loads(Path(a.case).read_text(encoding="utf-8"))
    ep = case["ep"]
    mode = case.get("fake_mode", "ok")
    if isinstance(mode, dict):
        mode = mode.get(ep, "ok")
    attempt_id = case.get("attempt_id")
    frag: dict = {
        "attempt_id": attempt_id,
        "session": {
            "ep": ep.upper(),
            "provider_node_counts": ({"DmlExecutionProvider": 4} if ep == "dml"
                                     else {"CPU": 4} if ep == "cpu"
                                     else {"AMDGPU": 4}),
            "requested_providers": (["DmlExecutionProvider"] if ep == "dml"
                                    else ["CPUExecutionProvider"] if ep == "cpu"
                                    else ["AMDGPUExecutionProvider"]),
        },
        "parity": {
            "by_name_matching": True, "all_outputs_allclose": True,
            "outputs": [{"name": "out0", "shape": [1, 3], "dtype": "float32",
                         "allclose": True, "finite": True}],
        },
        "device_binding": {
            "identity_basis": "EP_DEVICE_ORDINAL + HOST_DXGI_INDIRECT_CORROBORATION",
            "dxgi_corroboration": {"evidence_kind": "HOST_DXGI_ENUMERATION_INDIRECT"},
        },
        "input_binding": {"ok": True},
    }

    if case.get("feeds_npz") and case.get("expected_feed_sha256"):
        got = _feed_sha(case["feeds_npz"])
        frag["input_binding"] = {"expected_sha256": case["expected_feed_sha256"],
                                 "actual_sha256": got,
                                 "ok": got == case["expected_feed_sha256"]}
        if not frag["input_binding"]["ok"] or mode == "bad_feed":
            _write(a.out, frag)
            print("INPUT_FINGERPRINT_MISMATCH", file=sys.stderr)
            return 3

    if mode == "wrong_attempt":
        frag["attempt_id"] = "A999-stale-000000000000"

    out_npz = case.get("outputs_npz")
    if out_npz and mode not in ("no_outputs",):
        Path(out_npz).parent.mkdir(parents=True, exist_ok=True)
        np.savez(out_npz, out0=np.zeros((1, 3), dtype=np.float32))
    if mode == "hang" and out_npz:
        Path(out_npz).write_bytes(b"PARTIAL")
        print("stub: wedged after partial output")
        end = time.time() + 3600
        while time.time() < end:
            time.sleep(0.05)

    _write(a.out, frag)
    if mode == "fail":
        print("stub: failing on purpose", file=sys.stderr)
        return 31
    return 0


def _write(path: str, frag: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp-" + str(os.getpid()))
    tmp.write_text(json.dumps(frag), encoding="utf-8")
    os.replace(tmp, p)


if __name__ == "__main__":
    sys.exit(main())