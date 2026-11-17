# PERFORMANCE — measurement discipline

- Phases measured separately: EP registration, session construction/compile,
  first Run (cold), cold-start total, warmed `Session.run` p50/p95/p99,
  mean/std (sample count declared), preprocessing, postprocessing, full-image
  end-to-end, batch-1 throughput.
- Timer: `time.perf_counter_ns` (monotonic). Warmup runs excluded and declared.
  ≥3 independent repetitions for representative models; dispersion reported.
- Never: wall-clock of the whole script as "kernel latency"; never compare
  latency across different output contracts (raw vs embedded-NMS) as if equal.
- Power/background pinned and recorded qualitatively; VRAM/temp telemetry may
  be `NOT_MEASURED` when no validated meter exists — shared-memory 8060S
  allocations never get reported as dedicated VRAM usage.
- Soak (representative detect + one larger seg/pose, both EPs): 15–30 min,
  30 s heartbeat, no-progress watchdog, stage markers around `sess.run`,
  teardown + interpreter exit tracked separately from run hangs.
