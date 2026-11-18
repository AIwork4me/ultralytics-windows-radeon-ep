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


def compare_outputs(ref: dict, cand: dict, *, atol: float, rtol: float,
                    expected: dict | None = None, thresholds_source: str = "configs/thresholds.yaml",
                    int_min_agreement: float = 0.999) -> dict:
    """By-name comparison with an optional expected-output manifest (v2).

    v2 semantics when ``expected`` is given: the compared name set must equal the
    expected set in BOTH directions (missing AND unexpected both fail); dtype must
    match the expected contract; per-output sha256 fingerprints recorded; the
    declared thresholds block is embedded so the strict validator can re-derive
    the PASS against predeclared values.

    Integer outputs (argmax class maps, uint8/int*): float allclose is the WRONG
    contract — a single boundary argmax tie flips a pixel by 1. For integer
    dtypes the parity metric is the exact-agreement fraction, PASS when
    agreement >= int_min_agreement (predeclared; see configs/thresholds.yaml
    numeric_parity.int_outputs). Rationale: AMDGPU-RCA-006 (1 px / 409600).
    """
    import hashlib

    import numpy as np

    results = []
    all_ok = True
    names = list(ref.keys())
    if expected is not None:
        exp_names = list(expected.keys())
        for n in exp_names:
            if n not in ref:
                results.append({"name": n, "missing_in_reference": True, "allclose": False,
                                "finite": False, "max_abs_diff": None,
                                "mean_abs_diff": None})
                all_ok = False
        for n in names:
            if n not in expected:
                results.append({"name": n, "unexpected_in_candidate": True, "allclose": False,
                                "finite": False, "max_abs_diff": None,
                                "mean_abs_diff": None})
                all_ok = False
    for name, ref_arr in ref.items():
        if name not in cand:
            results.append({"name": name, "missing_in_candidate": True, "allclose": False,
                            "finite": False, "max_abs_diff": None,
                            "mean_abs_diff": None})
            all_ok = False
            continue
        c = np.asarray(cand[name])
        r = np.asarray(ref_arr)
        if c.shape != r.shape:
            results.append({"name": name, "shape": list(c.shape), "shape_mismatch": list(r.shape),
                            "allclose": False, "finite": False,
                            "max_abs_diff": None, "mean_abs_diff": None})
            all_ok = False
            continue
        finite = bool(np.isfinite(c).all() and np.isfinite(r).all()) \
            if c.dtype.kind == "f" else bool(np.isfinite(c.astype(np.float64)).all())
        diff = np.abs(c.astype(np.float64) - r.astype(np.float64))
        with np.errstate(invalid="ignore"):
            rel = diff / np.maximum(np.abs(r.astype(np.float64)), 1e-12)
        if c.dtype.kind in "iu":
            agree = float((c == r).mean()) if c.size else 1.0
            close = agree >= int_min_agreement
            item = {
                "name": name, "shape": list(c.shape),
                "dtype": str(c.dtype), "finite": finite,
                "max_abs_diff": float(diff.max()) if diff.size else 0.0,
                "mean_abs_diff": float(diff.mean()) if diff.size else 0.0,
                "max_rel_diff": float(rel.max()) if rel.size else 0.0,
                "p99_abs_diff": float(np.percentile(diff, 99)) if diff.size else 0.0,
                "allclose": close, "threshold_used": int_min_agreement,
                "comparison_kind": "int_exact_agreement",
                "exact_agreement": agree,
                "disagree_px": int((c != r).sum()),
                "candidate_sha256": hashlib.sha256(np.ascontiguousarray(c).tobytes()).hexdigest(),
                "ref_sha256": hashlib.sha256(np.ascontiguousarray(r).tobytes()).hexdigest(),
            }
        else:
            close = bool(np.allclose(c, r, atol=atol, rtol=rtol)) if finite else False
            item = {
                "name": name, "shape": list(c.shape),
                "dtype": str(c.dtype), "finite": finite,
                "max_abs_diff": float(diff.max()) if diff.size else 0.0,
                "mean_abs_diff": float(diff.mean()) if diff.size else 0.0,
                "max_rel_diff": float(rel.max()) if rel.size else 0.0,
                "p99_abs_diff": float(np.percentile(diff, 99)) if diff.size else 0.0,
                "allclose": close, "threshold_used": atol,
                "candidate_sha256": hashlib.sha256(np.ascontiguousarray(c).tobytes()).hexdigest(),
                "ref_sha256": hashlib.sha256(np.ascontiguousarray(r).tobytes()).hexdigest(),
            }
        if expected is not None and name in expected:
            want_dtype = expected[name].get("dtype")
            if want_dtype and str(c.dtype) != want_dtype:
                item["dtype_mismatch"] = want_dtype
                all_ok = False
        results.append(item)
        all_ok = all_ok and close and finite
    out = {"outputs": results, "all_outputs_allclose": all_ok, "by_name_matching": True,
           "thresholds": {"atol": atol, "rtol": rtol, "source": thresholds_source,
                          "int_min_agreement": int_min_agreement}}
    return out
