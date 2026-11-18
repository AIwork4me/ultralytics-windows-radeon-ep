"""Unified-runtime GPU canary (mission M2 code-path validation).

Builds a deterministic tiny conv ONNX, runs:
  1. CPU ORT oracle (same bytes),
  2. target-EP session (amdgpu by default; LUID-pinned; profiled),
and prints a JSON verdict: target EP profiler nodes, CPU nodes, per-output
worst |delta|, identity chain confidence. Fail-closed; no CPU fallback.

Usage: python -I runner/unified_canary.py --ep amdgpu --gfx gfx1100 --out canary.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def build_tiny_onnx(path: str) -> None:
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    rng = np.random.default_rng(20261118)
    w1 = (rng.standard_normal((8, 3, 3, 3)) * 0.125).astype(np.float32)
    b1 = np.zeros(8, np.float32)
    w2 = (rng.standard_normal((4, 8, 3, 3)) * 0.125).astype(np.float32)
    b2 = np.zeros(4, np.float32)
    nodes = [
        helper.make_node("Conv", ["x", "w1", "b1"], ["c1"],
                         kernel_shape=[3, 3], pads=[1, 1, 1, 1]),
        helper.make_node("Relu", ["c1"], ["r1"]),
        helper.make_node("Conv", ["r1", "w2", "b2"], ["c2"],
                         kernel_shape=[3, 3], pads=[1, 1, 1, 1]),
        helper.make_node("ReduceMean", ["c2"], ["y"], axes=[1, 2, 3]),
    ]
    graph = helper.make_graph(
        nodes, "canary",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 3, 32, 32])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, [1])],
        [numpy_helper.from_array(w1, "w1"), numpy_helper.from_array(b1, "b1"),
         numpy_helper.from_array(w2, "w2"), numpy_helper.from_array(b2, "b2")])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    onnx.checker.check_model(model)
    onnx.save(model, path)


def compare_named_outputs(reference: dict, actual: dict, *, atol=5e-3,
                          rtol=5e-3) -> dict:
    """Fail-closed by-name and by-element numeric agreement with CPU ORT.

    For each float element: abs(gpu - cpu) <= atol + rtol * abs(cpu).
    Integer/boolean outputs require exact equality; invalid/missing/non-finite
    tensors never produce a GPU PASS. This function needs no GPU runtime.
    """
    import numpy as np

    errors = []
    worst = 0.0
    if not isinstance(reference, dict) or not reference:
        return {"allclose": False, "max_abs": None, "reason": "empty_cpu_reference"}
    if not isinstance(actual, dict) or set(reference) != set(actual):
        return {"allclose": False, "max_abs": None, "reason": "output_names_mismatch"}
    if (not np.isfinite(atol) or not np.isfinite(rtol)
            or atol < 0 or rtol < 0):
        return {"allclose": False, "max_abs": None, "reason": "invalid_tolerances"}
    for name in sorted(reference):
        a = np.asarray(actual[name])
        b = np.asarray(reference[name])
        if a.shape != b.shape or a.size == 0:
            errors.append(f"{name}:shape_or_empty")
            continue
        if a.dtype.kind not in "biuf" or b.dtype.kind not in "biuf":
            errors.append(f"{name}:non_numeric")
            continue
        if not bool(np.isfinite(a).all() and np.isfinite(b).all()):
            errors.append(f"{name}:non_finite")
            continue
        a64, b64 = a.astype(np.float64), b.astype(np.float64)
        delta = np.abs(a64 - b64)
        worst = max(worst, float(delta.max()))
        if a.dtype.kind in "biu" or b.dtype.kind in "biu":
            agreement = a == b
        else:
            agreement = delta <= atol + rtol * np.abs(b64)
        if not bool(agreement.all()):
            errors.append(f"{name}:parity_failed")
    return {"allclose": not errors, "max_abs": worst,
            "reason": ";".join(errors) if errors else None}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", choices=["amdgpu", "dml"], default="amdgpu")
    ap.add_argument("--gfx", default="gfx1100")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = {"ep": a.ep, "gfx": a.gfx, "schema": "unified_canary_v1"}

    import numpy as np
    import onnxruntime as ort

    model_path = Path(a.out).parent / f"canary_{a.ep}.onnx"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    build_tiny_onnx(str(model_path))
    blob = model_path.read_bytes()
    out["onnx_sha256"] = hashlib.sha256(blob).hexdigest()
    rng = np.random.default_rng(4242)
    feeds = {"x": (rng.standard_normal((1, 3, 32, 32)) * 0.5).astype(np.float32)}
    out["input_sha256"] = hashlib.sha256(
        b"".join(v.tobytes() for _, v in sorted(feeds.items()))).hexdigest()

    # 1) CPU oracle
    cpu = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    ref = dict(zip([o.name for o in cpu.get_outputs()], cpu.run(None, feeds)))
    out["versions"] = {"python": sys.version.split()[0], "ort": ort.__version__,
                       "numpy": np.__version__}

    # 2) PR #5 must run independently: do NOT import unified_identity,
    # which belongs to stacked PR #6. Resolve only the requested architecture.
    from radeon_ort.device_identity import enum_dxgi_adapters, resolve_target_adapter
    res = resolve_target_adapter(enum_dxgi_adapters(), gfx=a.gfx)
    if (not res.get("ok") or not res.get("did_match")
            or not res.get("luid_valid")):
        out["status"] = "BLOCKED"
        out["reason"] = (res.get("reason") or "DID or LUID identity not proven")
        Path(a.out).write_text(json.dumps(out, indent=1))
        return 34
    adapter = res["adapter"]
    from radeon_ort.rca2a_ep import physical_luid_key
    luid = physical_luid_key(adapter)
    if luid is None:
        out.update(status="BLOCKED", reason="physical LUID key unavailable")
        Path(a.out).write_text(json.dumps(out, indent=1))
        return 34
    out["physical"] = {"description": adapter.description,
                       "vendor_id": adapter.vendor_id,
                       "device_id": adapter.device_id,
                       "luid": adapter.luid_str,
                       "dxgi_index": adapter.index,
                       "did_match": True}
    if a.ep == "amdgpu":
        # select_amdgpu_ep_device may fall back to an unrelated candidate;
        # ONLY a byte-exact LUID equality is strong binding evidence.
        try:
            from radeon_ort.rca2a_ep import (
                select_amdgpu_ep_device, _ep_device_metadata)
            ep_dev, selected = select_amdgpu_ep_device(luid)
            md = _ep_device_metadata(ep_dev) if ep_dev is not None else {}
            if str(md.get("LUID", "")) != str(luid):
                out.update(status="BLOCKED",
                           reason="AMDGPU EP LUID != requested physical DXGI LUID")
                Path(a.out).write_text(json.dumps(out, indent=1))
                return 34
            out["confidence"] = "PHYSICAL_BINDING_CONFIRMED"
        except Exception as exc:  # noqa: BLE001 — fail closed when EP probe raises
            out.update(status="BLOCKED",
                       reason=f"AMDGPU EP LUID attestation failed: {type(exc).__name__}")
            Path(a.out).write_text(json.dumps(out, indent=1))
            return 34
    else:
        out["confidence"] = "DML_DXGI_ORDINAL_CORROBORATED;EP_LUID_UNPROVEN"

    # 3) session (fail-closed)
    so = ort.SessionOptions()
    so.enable_profiling = True
    so.profile_file_prefix = str(Path(a.out).parent / f"ort_profile_{a.ep}")
    so.log_severity_level = 3
    t0 = time.monotonic()
    try:
        if a.ep == "amdgpu":
            from radeon_ort.rca2a_ep import create_amdgpu_session
            sess = create_amdgpu_session(str(model_path), sess_options_override=so,
                                         target_luid=luid)
            if "AMDGPUExecutionProvider" not in sess.get_providers():
                raise RuntimeError("silent CPU fallback blocked (providers="
                                   + str(sess.get_providers()) + ")")
        else:
            from radeon_ort.dml_session import create_dml_session
            sess = create_dml_session(str(model_path),
                                      device_id=adapter.index,
                                      sess_options_override=so)
            if "DmlExecutionProvider" not in sess.get_providers():
                raise RuntimeError("DML provider absent: " + str(sess.get_providers()))
    except Exception as e:  # noqa: BLE001
        out["status"] = "FAIL_RUNTIME"
        out["reason"] = f"{type(e).__name__}: {e}"[:500]
        Path(a.out).write_text(json.dumps(out, indent=1))
        return 31 if a.ep == "amdgpu" else 32
    finally:
        try:
            out["session_create_s"] = round(time.monotonic() - t0, 3)
        except NameError:
            pass

    outs = dict(zip([o.name for o in sess.get_outputs()], sess.run(None, feeds)))
    out["parity"] = compare_named_outputs(ref, outs)
    parity_ok = out["parity"]["allclose"]

    prof = sess.end_profiling()
    data = json.loads(Path(prof).read_text(encoding="utf-8"))
    needle = "amdgpu" if a.ep == "amdgpu" else "dml"
    ep_nodes = sum(1 for e in data if e.get("cat") == "Node"
                   and needle in str(e.get("args", {}).get("provider", "")).lower())
    cpu_nodes = sum(1 for e in data if e.get("cat") == "Node"
                    and "cpu" in str(e.get("args", {}).get("provider", "")).lower()
                    and needle not in str(e.get("args", {}).get("provider", "")).lower())
    out["profile"] = {"target_ep_nodes": ep_nodes, "cpu_nodes": cpu_nodes}

    ok = parity_ok and ep_nodes > 0 and cpu_nodes == 0
    out["status"] = "GPU_PASS_VALIDATED" if ok else "GPU_PASS_INVALIDATED"
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out[k] for k in ("ep", "status", "confidence",
                                          "parity", "profile", "session_create_s")}))
    return 0 if ok else 41


if __name__ == "__main__":
    sys.exit(main())
