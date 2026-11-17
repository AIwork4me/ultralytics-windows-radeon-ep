# TROUBLESHOOTING

| symptom | meaning | next step |
|---|---|---|
| `available_providers` lacks `AMDGPUExecutionProvider` | WinML runtime/vendor EP not installed for this user | check `EP_INSTALL_AND_PROBE.json`; Windows ML 2.x + dynamic EP needs 24H2+ and MSIX vendor package; record `BLOCKED_EP` |
| `InferenceSession` raises on AMDGPU-only providers | EP registered but cannot init (driver/runtime mismatch) | capture exact exception into receipt `FAIL_RUNTIME`; check driver date ≥ 2024-12-01 |
| `GPU 0 node` / `FAIL_NO_GPU_COMPUTE` | run executed but profiler shows no AMDGPU/Dml nodes | wrapper silently used CPU — inspect stderr signals; never report PASS |
| `HIP 101` / device asserts | low-level device errors | reduce concurrency (one GPU process), reboot if zombie locks persist; never modify drivers |
| `EP_FAIL` on specific models | model has ops the EP can't fuse | expect PARTIAL or FAIL_RUNTIME per receipt; try DML path independently |
| `Wrong EP` / `BLOCKED_IDENTITY` | device identity chain broken | rerun `runner.preflight`; check DXGI list changed (display replug); verify vendor 0x1002 + device id |
| ORT API mismatch (`register_execution_provider_library` missing) | distribution/version lacks WinML registration API | keep wasdk-pinned 1.25.2 combo; probe before use (`windows_ml_ep_catalog.probe_windows_ml_api`) |
| Backend unobservable | EP name doesn't reveal HIP/MIGraphX internals | status stays `BACKEND_UNVERIFIED` note; never claim a specific backend |
| onnxruntime wheel conflicts | multiple ORT distributions in one venv | separate venvs (see INSTALL_WINDOWS) |
| Shared-memory OOM on 8060S | concurrent model jobs exhausted shared RAM | matrix runs are strictly one GPU process at a time; resume with `runner.resume_matrix` |
