# GFX1151 Open Gaps

1. **Tier B breadth (largest)**: 33 BASELINE_REQUIRED + 81 RESOURCE_GATED + 12
   OPTIONAL_STRESS catalog entries are NOT_RUN with evidence pending. Deterministic
   resume: see `DISPOSITION_LEDGER.json` `resume_command`. `G3_TIERB_YOLO26S`
   covers the yolo26 s-size slice (7 tasks × 2 EPs).
2. **WinML 2.5.96+ surface**: `AMDGPUExecutionProvider` (umbrella per MS docs) is
   not yet reachable from the stable PyPI wasdk (2.3.0). Current evidence uses the
   preinstalled vendor EP MSIX (`MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8`,
   provider name `AMDGPU`). Re-validate when wasdk ≥ 2.5.96 ships.
3. **ensure_ready_async 0x80073B0C**: dynamic install of the (deprecated)
   MIGraphX EP failed on this host. Low priority while the vendor EP MSIX is
   preinstalled; root cause likely Store/network environment.
4. **"Init provider bridge failed" ORT warning**: appears at env creation in
   onnxruntime-windowsml; does not block registration/execution (all evidence
   obtained with it present). Backend routing beyond provider node attribution
   remains `BACKEND_UNVERIFIED` (umbrella EP: hip/directx backend selection not
   observable from Python today).
5. **dev[0] HIP 101**: first AMDGPU ep-device (display enumeration LUID) cannot
   `hipSetDevice(0)`. Documented; selection is fail-closed. Upstream issue
   candidate (onnxruntime-ep-amdgpu).
6. **VRAM/temperature telemetry**: NOT_MEASURED (no validated meter); 8060S
   shared-memory allocations never reported as dedicated VRAM.
7. **gfx1100 / gfx1201**: NOT_RUN — separate hosts; issue #1 carries the gfx1201
   runbook for LZ-QWQ (write access confirmed).
