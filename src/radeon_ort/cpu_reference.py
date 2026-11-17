"""CPU ORT reference oracle — same ONNX, same inputs, same-output-name comparison."""

from __future__ import annotations

from pathlib import Path


def create_cpu_session(onnx_path: str | Path, *, enable_profiling: bool = False,
                       profile_prefix: str = "cpu_profile"):
    import onnxruntime as ort
    so = ort.SessionOptions()
    if enable_profiling:
        so.enable_profiling = True
        so.profile_file_prefix = profile_prefix
    # Match GPU-session optimization defaults as closely as possible.
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(onnx_path), sess_options=so,
                                providers=["CPUExecutionProvider"])


def run_reference(session, feeds: dict) -> dict:
    return {name: out for name, out in zip([i.name for i in session.get_outputs()],
                                           session.run(None, feeds))}


def compare_outputs(ref: dict, cand: dict, *, atol: float, rtol: float) -> dict:
    """By-name comparison; every output of the reference must be present & close."""
    import numpy as np

    results = []
    all_ok = True
    for name, ref_arr in ref.items():
        if name not in cand:
            results.append({"name": name, "missing_in_candidate": True, "allclose": False,
                            "finite": False, "max_abs_diff": float("inf"),
                            "mean_abs_diff": float("inf")})
            all_ok = False
            continue
        c = np.asarray(cand[name])
        r = np.asarray(ref_arr)
        if c.shape != r.shape:
            results.append({"name": name, "shape": list(c.shape), "shape_mismatch": list(r.shape),
                            "allclose": False, "finite": False,
                            "max_abs_diff": float("inf"), "mean_abs_diff": float("inf")})
            all_ok = False
            continue
        finite = bool(np.isfinite(c).all() and np.isfinite(r).all())
        diff = np.abs(c.astype(np.float64) - r.astype(np.float64))
        with np.errstate(invalid="ignore"):
            rel = diff / np.maximum(np.abs(r.astype(np.float64)), 1e-12)
        close = bool(np.allclose(c, r, atol=atol, rtol=rtol)) if finite else False
        results.append({
            "name": name, "shape": list(c.shape),
            "dtype": str(c.dtype), "finite": finite,
            "max_abs_diff": float(diff.max()) if diff.size else 0.0,
            "mean_abs_diff": float(diff.mean()) if diff.size else 0.0,
            "max_rel_diff": float(rel.max()) if rel.size else 0.0,
            "p99_abs_diff": float(np.percentile(diff, 99)) if diff.size else 0.0,
            "allclose": close, "threshold_used": atol,
        })
        all_ok = all_ok and close and finite
    return {"outputs": results, "all_outputs_allclose": all_ok, "by_name_matching": True}
