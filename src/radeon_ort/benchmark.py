"""Real latency benchmarking: cold path phases + warmed run percentiles.

Timer: time.perf_counter_ns (monotonic high resolution). Never uses wall-clock of
the whole script as 'kernel latency'. Warmup excluded; sample count declared.
"""

from __future__ import annotations

import statistics
import time


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * p
    f, c = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


class PhaseTimer:
    def __init__(self):
        self.marks: list[tuple[str, float]] = []

    def mark(self, name: str):
        self.marks.append((name, time.perf_counter_ns()))

    def phases_ms(self) -> dict:
        out = {}
        for (n0, t0), (n1, t1) in zip(self.marks, self.marks[1:]):
            out[f"{n0}->{n1}_ms"] = (t1 - t0) / 1e6
        return out


def bench_session_run(session, feeds: dict, *, warmup: int = 5, samples: int = 30,
                      run_fn=None) -> dict:
    run = run_fn or (lambda: session.run(None, feeds))
    for _ in range(warmup):
        run()
    times_ms: list[float] = []
    for _ in range(samples):
        t0 = time.perf_counter_ns()
        run()
        times_ms.append((time.perf_counter_ns() - t0) / 1e6)
    s = sorted(times_ms)
    return {
        "warm_run_ms_p50": _percentile(s, 0.50),
        "warm_run_ms_p95": _percentile(s, 0.95),
        "warm_run_ms_p99": _percentile(s, 0.99),
        "warm_run_ms_mean": statistics.fmean(times_ms),
        "warm_run_ms_std": statistics.pstdev(times_ms) if len(times_ms) > 1 else 0.0,
        "warm_run_samples": len(times_ms),
        "timer": "time.perf_counter_ns",
    }


def e2e_image_timings(pre_ms: list[float], run_ms: list[float], post_ms: list[float]) -> dict:
    pre, run, post = sorted(pre_ms), sorted(run_ms), sorted(post_ms)
    e2e = [p + r + q for p, r, q in zip(pre_ms, run_ms, post_ms)]
    e2e_sorted = sorted(e2e)
    return {
        "preprocess_ms_mean": statistics.fmean(pre_ms),
        "postprocess_ms_mean": statistics.fmean(post_ms),
        "e2e_image_ms_p50": _percentile(e2e_sorted, 0.50),
        "e2e_image_ms_p95": _percentile(e2e_sorted, 0.95),
    }
