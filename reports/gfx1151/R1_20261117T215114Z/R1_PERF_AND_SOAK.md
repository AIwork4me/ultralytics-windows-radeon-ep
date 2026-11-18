# R1 performance & soak — AMDGPU first, DML independent comparator

Same machine, sequential (AMDGPU runs before DML per case; never concurrent),
identical input bytes per case, `time.perf_counter_ns` timer. Session-creation
phases from per-receipt device_binding attestations (probe create + rebuild;
the model is compiled twice by design — probe session then final session —
documented in `amdgpu_session.py`); warm percentiles from `benchmark_data`
(5 warmup + 30 samples, `configs/thresholds.yaml benchmark`).

## Cold session creation vs warm inference (16 models)

| model | AMDGPU cold-create s | AMDGPU warm p50 ms | DML warm p50 ms |
|---|---|---|---|
| yolo26n (detect) | 130 | 4.06 | 2.52 |
| yolo26n-seg | 186 | 6.38 | 4.01 |
| yolo26n-sem | 84 | 3.43 | 2.06 |
| yolo26n-depth | 126 | 10.16 | 6.90 |
| yolo26n-cls | 71 | 1.17 | 0.41 |
| yolo26n-pose | 158 | 4.69 | 2.80 |
| yolo26n-obb | 171 | 7.23 | 4.99 |
| yolo26s (detect) | 166 | 8.61 | 5.48 |
| yolo26s-seg | 201 | 10.25 | 8.47 |
| yolo26s-sem | 89 | 5.69 | 4.76 |
| yolo26s-depth | 159 | 14.23 | 10.50 |
| yolo26s-cls | 76 | 1.89 | 0.92 |
| yolo26s-pose | 182 | 8.32 | 5.61 |
| yolo26s-obb | 186 | 16.31 | 15.64 |
| yolo11n | 118 | 4.16 | 2.67 |
| yolov8n | 97 | 4.19 | 2.50 |

Reading (AMDGPU): cold creation is dominated by MIGraphX compilation that
cannot persist its problem cache (AMDGPU-RCA-002: WindowsApps ACL); it costs
71–201 s per process per model and recurs on every process restart. Warm
inference is fast and stable. DML creates sessions in ~1–2 s (not separately
tabulated; worker logs) and is faster warm on every model — reported as an
independent comparator, NOT as a substitute for the AMDGPU path (mission §4).

## 15-minute inference-phase soaks (after session init; setup excluded)

| EP | status | duration | runs | errors | p50 ms | p95 ms | input |
|---|---|---|---|---|---|---|---|
| AMDGPU (yolo26n, bus.jpg tensor) | COMPLETED | 900.0 s | 155,007 | 0 | 4.41 | 5.20 | deterministic bundle `input_b99692b53d3e5a04.npy` |
| DML (same model+input) | COMPLETED | 900.0 s | 269,534 | 0 | 3.27 | 3.94 | same bytes |

30 s heartbeats, 180 s no-progress watchdog (never triggered), clean exit on
both. Artifacts: `SOAK_amdgpu.json`, `SOAK_dml.json`.

## Attribution note

Every GPU receipt in this run records `attribution: fused_single_node` — the
ORT profile attributes one fused EP node to AMDGPU/DML (graph fusion), which
is EP-attributed execution evidence, NOT per-source-node proof (labels kept
honest per mission R1-A; `profile_fused_event_present` recorded).
