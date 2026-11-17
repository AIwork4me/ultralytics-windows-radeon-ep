# DEVICE_IDENTITY — selecting the real 8060S (gfx1151)

The Radeon 8060S is an **integrated** GPU in Ryzen AI MAX. Consequences we honor:

1. `Discrete` may be `False` — never required.
2. `DedicatedVideoMemory` can be ~0 with huge `SharedSystemMemory` — never
   required > 0; shared memory is NEVER reported as "dedicated VRAM".
3. DXGI index is not any other ordinal; LUID is **boot-local** (recorded per
   session as corroboration, never as permanent identity).
4. Multiple enumerations (e.g. virtual display adapters like GameViewer) exist —
   software/other-vendor adapters are excluded, not "adapter 0".

## Resolution chain (src/radeon_ort/device_identity.py)

```
Win32_PnPSignedDriver/PnP (name+VEN_1002&DEV_1586)   <- host_probe
        + DXGI EnumAdapters1 (vendor 0x1002, DeviceId, description needle)
        -> unique candidate? else WRONG_OR_AMBIGUOUS_DEVICE
        -> per-session live canary ONNX on the selected device
        -> receipt: verified_device_identity=true only with all links
```

## Verified facts on the reference host (2026-11-17)

- `PCI\VEN_1002&DEV_1586&SUBSYS_17141043` — AMD Radeon(TM) 8060S Graphics
- Driver 32.0.22032.6002 (2025-12-15), WDDM 3.2, SharedMemory ≈ 99 GB
- A GameViewer Virtual Display Adapter coexists (excluded by vendor/software filters)

## DML cross-check

DML sessions select `device_id` = the DXGI-resolved adapter ordinal of the same
physical Radeon; the profiled DML compute + LUID recorded in the receipt are the
cross-check that DML did not run on another adapter.
