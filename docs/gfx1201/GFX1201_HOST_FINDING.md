# gfx1201 hardware-validation issue — host finding and device-identity fixes

> Links #1. Baseline: main `7cef0c06` (PR #4 merged). Evidence run: `GFX1201_DIAG_20261118T2359Z`.

## Host finding / 主机发现

The mission's template assumes the executing host has an **RX 9070 XT (gfx1201)**.
Physical enumeration on the executing host shows the AMD dGPU is an **RX 7900 XTX
(PCI\VEN_1002&DEV_744C, gfx1100)** — see `reports/gfx1201/GFX1201_DIAG_20261118T2359Z/HOST_FINGERPRINT.json`.
Per `AGENTS.md` rule 2 and the issue's own gates, every gfx1201 GPU row in
`MODEL_TASK_MATRIX.csv` is honestly `NOT_RUN` and the 15-minute soak is `NOT_RUN`
(`SOAK.json`). gfx1100 canaries included in this PR are **code-path validation only**
and are labeled as such in every receipt.

## Device-identity fixes (red-first) / 设备身份修复

1. `0x7480` was mislabeled gfx1201 — removed. Navi 48 IDs `0x7550/0x7551` added per
   the pci.ids database (`1002:7550` = Navi 48 [Radeon RX 9070/9070 XT/9070 GRE]).
   `0x744C` (live-verified on this host) and `0x7448` map gfx1100; `0x1586` gfx1151.
2. `resolve_target_adapter` previously treated **every** non-software AMD adapter as
   a candidate for any non-gfx1151 target — a `gfx=gfx1201` request silently selected
   gfx1100 hardware. Now selection is DID-pinned per gfx arch (`GFX_DEVICE_IDS`);
   a wrong-arch request fails `WRONG_OR_AMBIGUOUS_DEVICE` naming what was found.
3. Live-discovered residual hole: the gfx1151 name fallback matched the generic
   needle "Radeon", letting gfx1151 requests select a 7900 XTX. Fallback needles are
   now arch-specific (`GFX_FALLBACK_NEEDLES = {"gfx1151": ("8060s",)}`).
4. New tests: `tests/unit/test_device_identity_gfx1201.py` (red 12 → green 20+1skip)
   covering 7550/7551, old-mislabel red, gfx1100/gfx1151, dual-AMD pinning, software
   adapters, duplicate-silicon dedup, zero-LUID flag, wrong-ordinal non-transfer,
   unknown-target listing.

## Windows ML 2.x Catalog discovery (read-only)

First catalog evidence on this host: `AMDGPUExecutionProvider` ReadyState=SUCCESS(1),
Certification=UNCERTIFIED(2); `MIGraphXExecutionProvider` SUCCESS/CERTIFIED;
OpenVINO/WebGpu FAILURE(2). All `library_path` entries empty → BYO/MSIX registration
(`Microsoft.WinML.AMD.GPU.EP.Framework.2` 1.8.65.0) remains the working DLL path.
No install actions attempted (`BLOCKED_INSTALL_AUTHORIZATION` posture preserved).

## What unblocks gfx1201 GO

A host with a real RX 9070 XT runs `reports/gfx1201/GFX1201_DIAG_20261118T2359Z/MIN_REPRODUCE.txt`.
If the vendor EP package lacks gfx1201 kernel images, expect `BLOCKED_UNSUPPORTED_ARCH`
(record precisely; never change drivers). Seven tasks + 900 s soak gates then apply unchanged.
