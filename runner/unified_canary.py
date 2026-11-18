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

    # 2) identity chain (amdgpu path)
    from radeon_ort.unified_identity import build_chain
    chain = build_chain(a.gfx if a.ep == "amdgpu" else "gfx1100")
    if a.ep == "amdgpu":
        if chain.physical is None:
            out["status"] = "BLOCKED"
            out["reason"] = ";".join(chain.failures)
            Path(a.out).write_text(json.dumps(out, indent=1))
            return 34
        out["physical"] = {k: v for k, v in chain.physical.items() if k != "adapter"}
        out["confidence"] = chain.confidence()
        luid = chain.physical.get("luid_key")
    else:
        from radeon_ort.device_identity import enum_dxgi_adapters, resolve_target_adapter
        res = resolve_target_adapter(enum_dxgi_adapters(), gfx=a.gfx)
        if not res.get("ok"):
            out["status"] = "BLOCKED"
            out["reason"] = res["reason"]
            Path(a.out).write_text(json.dumps(out, indent=1))
            return 34
        out["physical"] = {k: v for k, v in res["adapter"].__dict__.items()}
        out["confidence"] = "DML_ADAPTER_PINED_BY_DXGI_ORDINAL"
        luid = None

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
                                      device_id=res["adapter"].index,
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
    worst = 0.0
    parity_ok = True
    for k in sorted(ref):
        d = float(np.max(np.abs(np.asarray(outs[k], np.float64)
                                - np.asarray(ref[k], np.float64))))
        worst = max(worst, d)
        finite = bool(np.isfinite(np.asarray(outs[k])).all())
        parity_ok = parity_ok and finite and d <= 5e-3 + 5e-3 * float(
            np.max(np.abs(np.asarray(ref[k], np.float64))))
    out["parity"] = {"allclose": parity_ok, "max_abs": worst}

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
