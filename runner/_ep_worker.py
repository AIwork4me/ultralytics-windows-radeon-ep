"""One-EP-per-process worker (v2): load case JSON, run the requested EP on the
persisted input bundle, emit a fail-closed receipt fragment + candidate outputs
(.npz). Called via subprocess by runner.run_matrix.

v2 changes (R1-B/R1-C):
  - inputs arrive as a verified .npy bundle (tensor sha256 re-derived at load);
    no arrays embedded in the case JSON,
  - the expected-output manifest is derived from the session's own IO contract,
  - AMDGPU sessions carry a full selection attestation (device_binding),
  - reference outputs arrive as .npz (no giant JSON),
  - exit codes remain the fail-closed contract (rc!=0 can never become PASS).

Exit codes: 0 OK; 31 AMDGPU_FAILED; 32 DML_FAILED; 34 BLOCKED_EP;
35 BLOCKED_IDENTITY; 3 usage/invalid.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radeon_ort import device_identity, profiler_audit  # noqa: E402
from radeon_ort.cpu_reference import compare_outputs, create_cpu_session  # noqa: E402
from radeon_ort.input_contract import load_input_bundle, tensor_sha256  # noqa: E402

ORT_TYPE_MAP = {
    "tensor(float)": "float32", "tensor(float16)": "float16",
    "tensor(double)": "float64", "tensor(int64)": "int64",
    "tensor(int32)": "int32", "tensor(bool)": "bool",
    "tensor(uint8)": "uint8", "tensor(int8)": "int8",
}


def _expected_from_session(sess) -> list[dict]:
    out = []
    for o in sess.get_outputs():
        shape = [d if isinstance(d, int) else None for d in (o.shape or [])]
        out.append({"name": o.name, "dtype": ORT_TYPE_MAP.get(o.type, o.type),
                    "shape": shape})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    case = json.loads(Path(a.case).read_text(encoding="utf-8"))
    ep = case["ep"].lower()
    gfx = case.get("gfx")
    if not gfx:
        print("invalid case: missing explicit gfx target (no gfx1151 default)",
              file=sys.stderr)
        return 3
    onnx_path = case["onnx_path"]
    atol = case.get("atol", 5e-3)
    rtol = case.get("rtol", 5e-3)
    bench = case.get("benchmark", False)

    frag = {"session": {"ep": ep.upper()}, "parity": None, "benchmark_data": None,
            "worker_pid": os.getpid(), "parent_pid": os.getppid(),
            "input_tensor_sha256": None}
    try:
        if case.get("feeds_npz"):
            # Multi-input canary path: every input is persisted, hash-checked and
            # fed under its graph name. Used by runner.preflight (MatMul+Add).
            z = np.load(case["feeds_npz"])
            feed_dict = {k: np.asarray(z[k], dtype=np.float32) for k in z.files}
            import hashlib as _hl
            hh = _hl.sha256()
            for k in sorted(feed_dict):
                hh.update(k.encode() + b"|")
                hh.update(tensor_sha256(feed_dict[k]).encode())
            frag["input_tensor_sha256"] = hh.hexdigest()
            frag["input_id"] = "canary_feeds:" + frag["input_tensor_sha256"][:16]
            input_name = None
            manifest = {"input_id": frag["input_id"]}
        else:
            manifest, tensor = load_input_bundle(case["input_manifest"])
            input_name = case.get("input_name") or "images"
            feed_dict = {input_name: np.asarray(tensor, dtype=np.float32)}
            frag["input_tensor_sha256"] = tensor_sha256(feed_dict[input_name])
            frag["input_id"] = manifest.get("input_id")

        # device identity check in this process (fresh session corroboration)
        adapters = device_identity.enum_dxgi_adapters()
        res = device_identity.resolve_target_adapter(adapters, gfx=gfx)
        if not res.get("ok"):
            frag["session"]["routing_notes"] = res["reason"]
            _write(a.out, frag)
            print("BLOCKED_IDENTITY: " + res["reason"], file=sys.stderr)
            return 35
        target = res["adapter"]
        dxgi_corr = {
            "evidence_kind": "HOST_DXGI_ENUMERATION_INDIRECT",
            "is_ep_reported": False,
            "adapter_index": target.index,
            "luid": target.luid_str,
            "vendor_id": f"0x{target.vendor_id:04X}",
            "device_id": f"0x{target.device_id:04X}",
            "description": target.description,
            "did_match": bool(res.get("did_match")),
            "mapping_assumption": (
                "DXGI enumerates the host adapters; the EP exposes no LUID/PCI "
                "identity on this build, so EP-device -> DXGI-LUID is an ordinal "
                "assumption, not an EP-reported LUID (AMDGPU-RCA-004)"),
            "note": "host-side corroboration only; DO NOT read this as an "
                    "EP-reported LUID/ordinal",
        }

        if ep == "cpu":
            sess = create_cpu_session(onnx_path, enable_profiling=True,
                                      profile_prefix=case.get("profile_prefix", "cpu"))
        elif ep == "amdgpu":
            from radeon_ort import windows_ml_ep_catalog
            from radeon_ort.amdgpu_session import (create_amdgpu_session,
                                                   ensure_amdgpu_registered)
            try:
                dll = ensure_amdgpu_registered()
            except RuntimeError as e:
                frag["session"]["routing_notes"] = f"BLOCKED_EP: {e}"
                _write(a.out, frag)
                print(str(e), file=sys.stderr)
                return 34
            frag["session"]["ep_library_path_redacted"] = (
                windows_ml_ep_catalog._redact(Path(dll)) if dll != "already-registered" else None)
            result = create_amdgpu_session(
                onnx_path, profile_prefix=case.get("profile_prefix", "amdgpu"),
                feeds_for_validation=feed_dict)
            sess = result.session
            att = result.attestation
            frag["device_binding"] = dict(att)
            frag["device_binding"]["dxgi_corroboration"] = dxgi_corr
            frag["device_binding"]["ep_reported_identity"] = {
                "source": "OrtEpDevice.ep_metadata/ep_options",
                "exposes_luid": False,
                "exposes_pci_id": False,
                "reported": dict((att.get("selected_ep_device") or {}).get("ep_metadata") or {}),
                "note": "this AMDGPU EP build exposes identical metadata for the "
                        "dual-enumerated 8060S and no LUID; physical corroboration "
                        "is host-side only",
            }
            frag["device_binding"]["identity_basis"] = (
                "EP_DEVICE_SELECTED_BY_PROFILE + HOST_DXGI_INDIRECT_CORROBORATION")
            frag["session"]["provider_node_counts"] = \
                (att.get("final_profile") or {}).get("provider_node_counts") or {}
            frag["session"]["graph_node_total"] = sum(
                frag["session"]["provider_node_counts"].values())
            frag["session"]["profile_fused_event_present"] = \
                frag["session"]["provider_node_counts"].get("AMDGPU", 0) == 1
        elif ep == "dml":
            from radeon_ort.dml_session import (create_dml_session,
                                                resolve_dml_device_id_for_adapter)
            dml_idx = resolve_dml_device_id_for_adapter(target.index)
            sess = create_dml_session(onnx_path, device_id=dml_idx,
                                      profile_prefix=case.get("profile_prefix", "dml"))
            # DML revalidation: first profiled run must attribute to DML
            sess.run(None, feed_dict)
            dml_counts = profiler_audit.audit_profile(sess.end_profiling())["provider_node_counts"]
            dml_nodes = dml_counts.get("DmlExecutionProvider", 0)
            frag["device_binding"] = {
                "ep": "DML",
                "state": "EP_DEVICE_SELECTED",
                "selected_candidate_index": dml_idx,
                "candidates_tried": 1,
                "selected_ep_device": {"ep_vendor": "Microsoft",
                                       "ep_metadata": {"device_id": dml_idx},
                                       "ep_options": {}},
                "probe": {"amdgpu_nodes": 0, "cpu_nodes": dml_counts.get("CPU", 0),
                          "profile": None},
                "final_profile": {"provider_node_counts": dict(dml_counts)},
                "attribution": ("fused_single_node" if dml_nodes == 1 else
                                "multi_node" if dml_nodes > 0 else "none"),
                "dxgi_corroboration": dxgi_corr,
                "ep_reported_identity": {
                    "source": "OrtEpDevice.ep_metadata",
                    "exposes_luid": False,
                    "exposes_pci_id": False,
                    "reported": {"device_id": dml_idx},
                    "note": "DML exposes only an adapter ordinal; it does not "
                            "report a LUID or PCI identity",
                },
                "identity_basis": "EP_DEVICE_ORDINAL + PROFILE_NODE_ATTESTATION "
                                  "+ HOST_DXGI_INDIRECT_CORROBORATION",
                "notes": "DML device_id = DXGI adapter ordinal (host-side assumption, "
                         "not EP-reported LUID); profile revalidated on the final "
                         "session's first run",
            }
            frag["session"]["provider_node_counts"] = dml_counts
            frag["session"]["graph_node_total"] = sum(dml_counts.values())
            frag["session"]["profile_fused_event_present"] = dml_nodes == 1
        else:
            print(f"invalid ep {ep}", file=sys.stderr)
            return 3

        frag["session"]["requested_providers"] = [p for p in sess.get_providers()]
        expected = _expected_from_session(sess)
        frag["expected_outputs"] = expected
        if input_name is not None:
            real_input = sess.get_inputs()[0].name
            if real_input != input_name:
                # session contract wins: re-feed under the graph's input name
                feed_dict = {real_input: feed_dict[input_name]}
                frag["input_tensor_sha256"] = tensor_sha256(feed_dict[real_input])
            frag["input_name"] = real_input
        else:
            names = {i.name for i in sess.get_inputs()}
            if set(feed_dict) != names:
                raise ValueError(f"canary input mismatch: feeds={sorted(feed_dict)} graph={sorted(names)}")

        outs = sess.run(None, feed_dict)
        out_names = [o.name for o in sess.get_outputs()]
        cand = dict(zip(out_names, outs))

        # save candidate outputs for task postprocessing (npz, not JSON)
        npz_path = case.get("outputs_npz")
        if npz_path:
            np.savez_compressed(npz_path, **{k: np.asarray(v) for k, v in cand.items()})

        if ep != "cpu" and case.get("reference_npz"):
            ref_npz = np.load(case["reference_npz"])
            ref = {k: ref_npz[k] for k in ref_npz.files}
            exp_map = {e["name"]: e for e in expected}
            frag["parity"] = compare_outputs(ref, cand, atol=atol, rtol=rtol,
                                             expected=exp_map)

        if ep == "cpu":
            audit = profiler_audit.audit_profile(sess.end_profiling())
            frag["session"]["provider_node_counts"] = audit["provider_node_counts"]
            frag["session"]["graph_node_total"] = sum(audit["provider_node_counts"].values())

        frag["session"]["ort_version"] = _ort_version()
        frag["session"]["ort_distribution"] = _ort_distribution()
        frag["session"]["device_luid"] = target.luid_str
        frag["outputs_summary"] = [
            {"name": k, "shape": list(np.asarray(v).shape), "dtype": str(np.asarray(v).dtype),
             "sha256": tensor_sha256(v)} for k, v in cand.items()]

        if bench:
            from radeon_ort.benchmark import bench_session_run
            frag["benchmark_data"] = bench_session_run(sess, feed_dict)

        _write(a.out, frag)
        # ORT profiler uses REAL provider names: AMDGPU / DmlExecutionProvider
        ort_provider_key = "AMDGPU" if ep == "amdgpu" else "DmlExecutionProvider"
        ep_nodes = (frag["session"].get("provider_node_counts") or {}).get(ort_provider_key, 0)
        if ep == "cpu":
            return 0  # reference path; never a GPU claim
        if ep_nodes == 0:
            print(f"{ep.upper()}_FAILED: zero target-EP profile nodes (no GPU compute)",
                  file=sys.stderr)
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
    Path(path).write_text(json.dumps(_jsonable(frag), allow_nan=False), encoding="utf-8")


def _jsonable(o):
    import numpy as np
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return f"<ndarray {o.shape} {o.dtype}>"
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, float) and o != o:
        return "NaN"
    return o


if __name__ == "__main__":
    sys.exit(main())
