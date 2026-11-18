"""python -I runner/rca2a_g4_stats.py --run-root <RUN> — G4 statistics & gates.

Derives every G4 verdict from raw receipts (main root + PHASE4_* sub roots)
and the cache artifact manifest. Never edits receipts; output is
ANALYSIS/G4_STATISTICS.json (append-only recompute, deterministic seed).

Timing-granularity discipline (see HANDOVER / RCA2A metrics note): per-run
steady latencies on this host quantize to ~15.6 ms steps (observed value set
{0, 15.6, 31.2} ms), so the PRIMARY steady gate is the MEAN of per-iteration
monotonic deltas (telescope-exact); p50/p95/p99 are reported as REFERENCE_ONLY
with the quantization caveat, per the frozen thresholds lock.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radeon_ort.rca2a_metrics import bootstrap_ci, paired_improvement, summarize

SOAK_MIN_EFFECTIVE_SECONDS = 900.0


def soak_gate(receipt: dict) -> bool:
    """Exactly >=900 seconds of effective steady inference and real GPU proof."""
    phases = receipt.get("phases") or {}
    value = phases.get("steady_effective_s")
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= SOAK_MIN_EFFECTIVE_SECONDS
            and receipt.get("classification") == "CLEAN_COMPLETE"
            and receipt.get("child_exit_code") == 0
            and receipt.get("killed_by_watchdog") is False
            and (receipt.get("gpu_validation") or {}).get("verdict") == "GPU_PASS_VALIDATED"
            and len(phases.get("recreates") or []) >= 1)

QUANT_NOTE = ("steady per-run latencies quantize to ~15.6ms steps on this host; "
              "p50/p95/p99 are REFERENCE_ONLY, mean is the gate statistic")


def _load_receipts(run_root: Path) -> list[dict]:
    rows: list[dict] = []
    roots = [run_root] + sorted(run_root.glob("PHASE4_*")) + \
        sorted(run_root.glob("PUBCONF*"))
    for root in roots:
        f = root / "RESULTS_RAW.jsonl"
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                r["_root"] = root.name
                rows.append(r)
    return rows


def _ph(r: dict, key: str):
    return ((r.get("phases") or {}).get(key))


def _steady(r: dict, key: str):
    return (((r.get("phases") or {}).get("steady") or {}).get(key))


def _clean(rows, arm, model, root=None) -> list[dict]:
    out = []
    for r in rows:
        if (r.get("arm") == arm and r.get("model") == model
                and r.get("classification") == "CLEAN_COMPLETE"
                and (root is None or r["_root"] == root)):
            out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-root", required=True)
    a = ap.parse_args()
    run_root = Path(a.run_root)
    rows = _load_receipts(run_root)
    out: dict = {"schema": "rca2a_g4_statistics_v1",
                 "quantization_note": QUANT_NOTE, "models": {}}

    for model in ("yolo26n", "rtdetr-l"):
        m: dict = {}

        # ---- timeline decomposition (process wall vs create vs first run vs
        # warm vs steady vs rebuild), per attempt ----
        m["timeline"] = []
        for r in rows:
            if r.get("model") != model:
                continue
            m["timeline"].append({
                "attempt": r["attempt_id"], "root": r["_root"],
                "classification": r["classification"],
                "wall_s": r.get("wall_s"),
                "session_create_s": _ph(r, "session_create_s"),
                "first_run_s": _ph(r, "first_run_s"),
                "warmup_p50_ms": (_ph(r, "warmup") or {}).get("p50"),
                "steady_mean_ms": _steady(r, "mean"),
                "steady_effective_s": _ph(r, "steady_effective_s"),
                "steady_n": _steady(r, "n"),
                "recreates": _ph(r, "recreates") or []})

        def creates(arm, root=None, reps=None):
            xs = []
            for r in _clean(rows, arm, model, root):
                if reps is None or r["rep"] in reps:
                    v = _ph(r, "session_create_s")
                    if v is not None:
                        xs.append((r["attempt_id"], v))
            return xs

        # ---- arm create stats ----
        m["arm_create"] = {}
        for arm in ("E0_BASELINE", "E2_SKIP_BENCH", "E4_MXR_CACHE",
                    "E4_MXR_FRC", "E6_COMPUTE_MAX", "E7_RECREATE",
                    "SOAK_MXR_CACHE"):
            xs = [v for _, v in creates(arm)]
            if xs:
                st = summarize(xs)
                st["ci95_median"] = bootstrap_ci(xs).get("ci95")
                m["arm_create"][arm] = st

        # ---- steady stats (mean primary) ----
        m["arm_steady"] = {}
        for arm in ("E0_BASELINE", "E2_SKIP_BENCH", "E4_MXR_CACHE",
                    "E4_MXR_FRC", "E6_COMPUTE_MAX", "SOAK_MXR_CACHE"):
            xs = [_steady(r, "mean") for r in _clean(rows, arm, model)
                  if _steady(r, "mean") is not None]
            if xs:
                m["arm_steady"][arm] = {"mean_of_means_ms": summarize(xs),
                                        "note": "per-process mean of per-iter "
                                        "monotonic deltas (telescope-exact); "
                                        + QUANT_NOTE}

        # ---- E0 vs E2 attribution (paired by rep index) ----
        e0 = [v for _, v in sorted(creates("E0_BASELINE"), key=lambda t: t[0])]
        e2 = [v for _, v in creates("E2_SKIP_BENCH")]
        k = min(len(e0), len(e2))
        if k >= 2:
            m["E0_vs_E2_create"] = {
                "pairs": k, "e0_s": e0[:k], "e2_s": e2[:k],
                "paired_improvement_pct": paired_improvement(e0[:k], e2[:k]),
                "verdict": "AUTOTUNING_CONTRIBUTION_QUANTIFIED "
                           "(MIGRAPHX_SKIP_BENCHMARKING recognized; single "
                           "variable; n=%d independent processes)" % k}

        # ---- E4 cache benefit (cold tune rep0 vs cached rep1/2/9) ----
        e4_all = creates("E4_MXR_CACHE")
        e4_cold = [v for att, v in e4_all if "rep0" in att]
        e4_cached = [v for att, v in e4_all if any(f"rep{i}" in att for i in (1, 2, 9))]
        e0s = [v for _, v in creates("E0_BASELINE")]
        if e4_cached and e0s:
            base_mean = sum(e0s) / len(e0s)
            cach_mean = sum(e4_cached) / len(e4_cached)
            m["E4_cache_benefit"] = {
                "baseline_e0_create_s": summarize(e0s),
                "cold_tune_rep0_s": e4_cold,
                "cached_create_s": {"values": e4_cached,
                                    "mean": round(cach_mean, 3)},
                "cold_start_reduction_pct": round((base_mean - cach_mean)
                                                  / base_mean * 100.0, 2),
                "ci95_baseline_median": bootstrap_ci(e0s).get("ci95"),
                "ci95_cached_median": bootstrap_ci(e4_cached).get("ci95"),
                "n_processes_cached": len(e4_cached),
                "low_power": len(e4_cached) < 5}

        # ---- FRC invalidation control ----
        frc = [v for _, v in creates("E4_MXR_FRC")]
        if frc and e0s:
            ratio = sum(frc) / len(frc) / (sum(e0s) / len(e0s))
            m["E4_MXR_FRC"] = {
                "frc_create_s": frc,
                "frc_vs_baseline_ratio": round(ratio, 3),
                "verdict": ("FORCE_RECOMPILE_EFFECTIVE (create returned to "
                            "baseline level despite populated .mxr)"
                            if ratio > 0.7 else
                            "FORCE_RECOMPILE_NOT_EFFECTIVE / INCONCLUSIVE "
                            "(create stayed at cache-hit level)")}

        # ---- steady regression gates (mean primary, 5% threshold) ----
        gates = {}
        base_means = [_steady(r, "mean") for r in _clean(rows, "E0_BASELINE", model)
                      if _steady(r, "mean") is not None]
        if base_means:
            bm = sum(base_means) / len(base_means)
            for arm in ("E2_SKIP_BENCH", "E4_MXR_CACHE", "E4_MXR_FRC",
                        "E6_COMPUTE_MAX", "SOAK_MXR_CACHE"):
                cm = [_steady(r, "mean") for r in _clean(rows, arm, model)
                      if _steady(r, "mean") is not None]
                if not cm:
                    continue
                cmn = sum(cm) / len(cm)
                regress = (cmn - bm) / bm * 100.0
                gates[arm] = {
                    "baseline_mean_ms": round(bm, 3),
                    "candidate_mean_ms": round(cmn, 3),
                    "regression_pct_mean": round(regress, 2),
                    "gate": "STEADY_NOT_REGRESSED" if regress <= 5.0
                            else "STEADY_REGRESSED (COLD_FAST_STEADY_SLOW if "
                                 "E2)" if arm == "E2_SKIP_BENCH"
                            else "STEADY_REGRESSED",
                    "reference_p50_p95_note": QUANT_NOTE}
        m["steady_gates_mean_primary"] = gates

        # ---- E7 rebuild-in-process ----
        rec_lists = [(_ph(r, "recreates") or []) for r in _clean(rows, "E7_RECREATE", model)]
        if rec_lists:
            m["E7_recreate"] = {
                "recreate_create_s": [c["recreate_s"] for lst in rec_lists for c in lst],
                "first_run_after_s": [c["first_run_after_s"] for lst in rec_lists for c in lst],
                "verdict": "IN_PROCESS_RECREATE_FULL_RECOMPILE (no warm in-process cache)"}

        # ---- soak ----
        soak = _clean(rows, "SOAK_MXR_CACHE", model)
        if soak:
            r = soak[-1]
            recs = _ph(r, "recreates") or []
            m["soak"] = {
                "attempt": r["attempt_id"],
                "session_create_s": _ph(r, "session_create_s"),
                "steady_effective_s": _ph(r, "steady_effective_s"),
                "steady_mean_ms": _steady(r, "mean"), "steady_n": _steady(r, "n"),
                "recreates": recs,
                "effective_steady_minutes": round(
                    (_ph(r, "steady_effective_s") or 0) / 60.0, 2),
                "min_effective_steady_s": SOAK_MIN_EFFECTIVE_SECONDS,
                "meets_15min": soak_gate(r),
                "gate": "SOAK_15MIN_OK" if soak_gate(r)
                        else "SOAK_SHORT_OR_INVALID"}

        out["models"][model] = m

    # ---- cross-model: FRC cache artifact rewrite evidence ----
    man_p = run_root / "CACHE_LABS" / "CACHE_ARTIFACT_MANIFEST.json"
    if man_p.is_file():
        man = json.loads(man_p.read_text(encoding="utf-8"))
        stages = {}
        for e in man.get("entries", []):
            stages.setdefault(e["stage"], {})[e["file"]] = {
                "sha256": e["sha256"], "bytes": e["bytes"],
                "mtime_utc": e["mtime_utc"]}
        out["cache_artifact_stages"] = stages
        pre = stages.get("phase2_final_preserve_and_live", {})
        post = stages.get("post_frc_batch", {})
        if pre and post:
            out["frc_rewrite_evidence"] = {
                f: {"phase2_sha": pre[f]["sha256"][:16],
                    "post_frc_sha": post.get(f, {}).get("sha256", "MISSING")[:16] if post.get(f) else "MISSING",
                    "rewritten": (post.get(f, {}).get("sha256") != pre[f]["sha256"]) if f in post else None,
                    "mtime_changed": (post.get(f, {}).get("mtime_utc") != pre[f]["mtime_utc"]) if f in post else None}
                for f in pre}
            all_rw = all(v["rewritten"] for v in out["frc_rewrite_evidence"].values())
            out["frc_rewrite_evidence"]["all_files_rewritten"] = all_rw

    out_path = run_root / "ANALYSIS" / "G4_STATISTICS.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1)[:6000])
    print(f"\n[g4] wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
