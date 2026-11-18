"""RCA2A metrics — percentiles, bootstrap CIs, arm comparisons.

Small samples get honest descriptions only: bootstrap CI with n<5 is reported
but flagged low_power=True and must not be quoted as strong evidence.
"""
from __future__ import annotations

import math
import random
from typing import Any


def percentile(xs: list[float], q: float) -> float:
    if not xs:
        raise ValueError("empty")
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    pos = (len(s) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(s[int(pos)])
    return float(s[lo] + (s[hi] - s[lo]) * (pos - lo))


def summarize(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    n = len(xs)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n
    return {"n": n, "mean": round(mean, 4),
            "p50": round(percentile(xs, 0.50), 4),
            "p95": round(percentile(xs, 0.95), 4),
            "p99": round(percentile(xs, 0.99), 4),
            "std": round(math.sqrt(var), 4),
            "min": round(min(xs), 4), "max": round(max(xs), 4)}


def bootstrap_ci(xs: list[float], *, stat="median", n_resample: int = 10000,
                 seed: int = 20261118, alpha: float = 0.05) -> dict[str, Any]:
    """Percentile bootstrap CI for median (default) or mean. Deterministic seed."""
    n = len(xs)
    if n < 2:
        return {"n": n, "low_power": True, "ci": None, "note": "n<2 no CI"}
    rng = random.Random(seed)
    stats = []
    for _ in range(n_resample):
        sample = [xs[rng.randrange(n)] for _ in range(n)]
        if stat == "median":
            stats.append(percentile(sample, 0.5))
        elif stat == "mean":
            stats.append(sum(sample) / n)
        else:
            raise ValueError(stat)
    stats.sort()
    lo = stats[int((alpha / 2) * n_resample)]
    hi = stats[min(n_resample - 1, int((1 - alpha / 2) * n_resample))]
    return {"n": n, "stat": stat, "ci95": [round(lo, 4), round(hi, 4)],
            "low_power": n < 5,
            "note": "n<5: descriptive only" if n < 5 else ""}


def paired_improvement(baseline: list[float], candidate: list[float]) -> dict[str, Any]:
    """Median paired improvement % (positive = candidate faster/lower).
    Pairs by index; unequal lengths use the shorter prefix (recorded)."""
    k = min(len(baseline), len(candidate))
    if k == 0:
        return {"pairs": 0, "note": "no pairs"}
    deltas = []
    for i in range(k):
        if baseline[i] != 0:
            deltas.append((baseline[i] - candidate[i]) / baseline[i] * 100.0)
    ci = bootstrap_ci(deltas)
    return {"pairs": k, "median_improvement_pct": round(percentile(deltas, 0.5), 3),
            "ci95": ci.get("ci95"), "low_power": ci.get("low_power")}


def steady_regression_verdict(base_ms: dict, cand_ms: dict,
                              *, threshold_pct: float = 5.0) -> dict[str, Any]:
    """STEADY_NOT_REGRESSED gate: candidate P50 and P95 each must not be worse
    than baseline by more than threshold_pct."""
    out: dict[str, Any] = {"threshold_pct": threshold_pct, "checks": []}
    ok = True
    for q in ("p50", "p95"):
        b, c = base_ms.get(q), cand_ms.get(q)
        if b is None or c is None or b <= 0:
            out["checks"].append({"metric": q, "status": "NOT_MEASURED"})
            ok = False
            continue
        regress = (c - b) / b * 100.0
        passed = regress <= threshold_pct
        ok = ok and passed
        out["checks"].append({"metric": q, "baseline_ms": b, "candidate_ms": c,
                              "regression_pct": round(regress, 2), "status":
                              "PASS" if passed else "FAIL"})
    out["verdict"] = "STEADY_NOT_REGRESSED" if ok else (
        "STEADY_REGRESSED" if any(c["status"] == "FAIL" for c in out["checks"])
        else "CONDITIONAL_NOT_MEASURED")
    return out
