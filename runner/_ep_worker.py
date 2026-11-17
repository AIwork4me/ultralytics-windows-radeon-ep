"""One-EP-per-process worker: load case JSON, run session on requested EP,
emit receipt fragment JSON. Called by fallback_policy.run_ep_subprocess.

Exit codes (fail-closed, machine-readable):
  0 OK            31 AMDGPU_FAILED      32 DML_FAILED
  34 BLOCKED_EP   35 BLOCKED_IDENTITY   3 usage/invalid
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radeon_ort import device_identity, profiler_audit  # noqa: E402
from radeon_ort.cpu_reference import compare_outputs, create_cpu_session  # noqa: E402


def _np(v):
    return np.asarray(v, dtype=np.float32)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    case = json.loads(Path(a.case).read_text(encoding="utf-8"))
    ep = case["ep"].lower()
    onnx_path = case["onnx_path"]
    feeds = {k: _np(v) for k, v in case["feeds"].items()}
    atol = case.get("atol", 1e-3)
    rtol = case.get("rtol", 1e-3)
    bench = case.get("benchmark", False)

    frag = {"session": {"ep": ep.upper()}, "parity": None, "benchmark_data": None}
    try:
        # device identity check in this process (fresh session corroboration)
        adapters = device_identity.enum_dxgi_adapters()
        res = device_identity.resolve_target_adapter(adapters, gfx=case.get("gfx", "gfx1151"))
        if not res.get("ok"):
            frag["session"]["routing_notes"] = res["reason"]
            _write(a.out, frag)
            print("BLOCKED_IDENTITY: " + res["reason"], file=sys.stderr)
            return 35
        target = res["adapter"]

        if ep == "cpu":
            sess = create_cpu_session(onnx_path, enable_profiling=True)
        elif ep == "amdgpu":
            from radeon_ort.amdgpu_session import create_amdgpu_session
            sess = create_amdgpu_session(onnx_path, profile_prefix=case.get("profile_prefix", "amdgpu"))
        elif ep == "dml":
            from radeon_ort.dml_session import create_dml_session
            # select DML adapter by resolved DXGI index of the intended Radeon
            dml_idx = device_identity.resolve_dml_device_id_for_adapter(target.index)
            sess = create_dml_session(onnx_path, device_id=dml_idx,
                                      profile_prefix=case.get("profile_prefix", "dml"))
        else:
            print(f"invalid ep {ep}", file=sys.stderr)
            return 3

        frag["session"]["requested_providers"] = [p for p in sess.get_providers()]
        outs = sess.run(None, feeds)
        out_names = [o.name for o in sess.get_outputs()]
        cand = dict(zip(out_names, outs))

        if ep != "cpu" and case.get("reference_outputs"):
            ref = {k: _np(v) for k, v in case["reference_outputs"].items()}
            frag["parity"] = compare_outputs(ref, cand, atol=atol, rtol=rtol)

        audit = profiler_audit.audit_profile(sess.end_profiling())
        frag["session"]["provider_node_counts"] = audit["provider_node_counts"]
        frag["session"]["graph_node_total"] = sum(audit["provider_node_counts"].values())
        frag["session"]["profile_fused_event_present"] = audit["fused_event_count"] > 0
        frag["session"]["ort_version"] = _ort_version()
        frag["session"]["ort_distribution"] = _ort_distribution()
        frag["session"]["device_luid"] = target.luid_str
        frag["outputs"] = {k: v.tolist() if hasattr(v, "tolist") else v for k, v in cand.items()}

        if bench:
            from radeon_ort.benchmark import bench_session_run
            frag["benchmark_data"] = bench_session_run(sess, feeds)

        _write(a.out, frag)
        ep_name = ep.upper()
        ep_nodes = audit["provider_node_counts"].get(ep_name, 0)
        if ep == "cpu":
            return 0  # reference path; never a GPU claim
        if ep_nodes == 0:
            print(f"{ep_name}_FAILED: zero target-EP profile nodes (no GPU compute)", file=sys.stderr)
            return 31 if ep == "amdgpu" else 32
        return 0
    except Exception as e:  # noqa: BLE001
        frag["session"]["routing_notes"] = f"exception: {type(e).__name__}: {e}"
        _write(a.out, frag)
        traceback.print_exc()
        code = 31 if ep == "amdgpu" else 32 if ep == "dml" else 3
        print(f"{ep.upper()}_FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return code


def _ort_version():
    try:
        import onnxruntime as ort
        return ort.__version__
    except Exception:  # noqa: BLE001
        return "unknown"


def _ort_distribution():
    try:
        import onnxruntime
        p = Path(onnxruntime.__file__).parent.name
        return "onnxruntime-windowsml" if "windowsml" in str(onnxruntime.__file__) else p
    except Exception:  # noqa: BLE001
        return "unknown"


def _write(path, frag):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(_jsonable(frag)), encoding="utf-8")


def _jsonable(o):
    import numpy as np
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, float) and o != o:
        return "NaN"
    return o


if __name__ == "__main__":
    sys.exit(main())
