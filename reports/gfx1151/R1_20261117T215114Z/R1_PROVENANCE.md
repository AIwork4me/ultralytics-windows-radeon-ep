# R1 provenance — exact versions, hashes, and source citations

## Host (captured live 2026-11-17/18)

| Item | Value |
|---|---|
| OS | Windows 11 家庭版 26200 (10.0.26200) |
| CPU/RAM | Ryzen AI MAX / 128 GB (132766306304 B) |
| GPU | AMD Radeon(TM) 8060S Graphics — PNP `PCI\VEN_1002&DEV_1586&SUBSYS_17141043&REV_C1` (gfx1151) |
| Driver | 32.0.22032.6002 (WDDM; recorded, never modified) |
| Other adapter | GameViewer Virtual Display Adapter (ROOT\DISPLAY\0000) — observed only |

## Software

| Component | Version | Where |
|---|---|---|
| Python | 3.13.13 | all three venvs |
| onnxruntime-windowsml | 1.25.2.202605110140 | `.venv-winml` |
| onnx (winml) | 1.23.2 | `.venv-winml` |
| numpy | 2.5.5 | winml/export |
| torch | 2.15.0 | `.venv-export` |
| **ultralytics (pinned)** | **8.4.215** | `.venv-export` |
| opencv | 5.0.0.93 | `.venv-export` |
| AMD GPU EP MSIX | `MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8_1.8.65.0_x64__8wekyb3d8bbwe` | WindowsApps (machine, preinstalled) |
| amdgpu-ep.dll SHA256 | `cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab` (548304 B) | glob-resolved, hashed, registered per-process as "AMDGPU" |

## Pinned-source citations for the task pipeline (ultralytics 8.4.215)

- `ultralytics/utils/nms.py:13` `non_max_suppression`: conf 0.25 default, `iou_thres=PREDICT_IOU` (=0.5, `ultralytics/utils/__init__.py`), class-aware via `max_wh=7680` offset, `max_det=300`, extras after `4+nc` (mask coeffs / keypoints / angle); `rotated=True` keeps `xywh` + angle as last extra — **column order (x,y,w,h,conf,cls,angle)** verified live on yolo26n-obb (RCA-005 follow-up).
- `ultralytics/utils/ops.py:637` `process_mask` — 3D protos `(dim, mh, mw)`; ONNX emits `[1,32,160,160]` → reshape before use.
- `ultralytics/utils/ops.py:599` `crop_mask`; `ops.py:220` `scale_boxes`; `ops.py:358` `xywh2xyxy`.
- `ultralytics/cfg/default.yaml:55-56` predict defaults (conf 0.25; NMS IoU 0.5 predict/export, 0.7 val).
- Classify export applies softmax IN-GRAPH (verified live: output sums to 1.0, min>0 → postprocess must not double-softmax).
- Semantic export emits uint8 argmax map IN-GRAPH; depth export emits dense float map `[1,1,H,W]`.

## Task thresholds & parity contracts

- `configs/thresholds.yaml` — task_oracle.* predeclared before physical outputs;
  `numeric_parity.int_outputs.min_exact_agreement: 0.999` added 2026-11-18 via
  versioned design note `docs/r1/R1_INT_OUTPUT_PARITY.md` (after RCA-006;
  rationale and honesty constraints therein).
- Snapshot used by every comparator invocation: `thresholds_snapshot.json` in
  this run dir; receipts embed `thresholds_declared` + `thresholds_source`, and
  `runner.verify_receipts` re-derives PASS against the live yaml.

## Git

- Base: PR #2 head `563c24e5e27a6abd61328720efd97ee618f10021` (branch
  `mission/gfx1151-windowsml-foundation`, Draft, OPEN, base main `ee47a44`).
- R1 branch: `mission/gfx1151-r1-amdgpu-first-task-parity` (no force-push; one
  linear history; superseded defective receipts preserved at their commits and
  indexed in `ERRATA.md`).
- Frozen evidence from G1/G2/G3/SOAK_RUN1 untouched (byte-identical in git;
  verified during R1-0).

## Fixtures

- `data/fixtures/bus.jpg` — ultralytics asset (AGPL repo asset, previously committed).
- `data/fixtures/dota_p1053.jpg` + `dota8_LICENSE.txt` — from ultralytics dota8 sample dataset.
- `negative_gray.png` — deterministic synthetic (seed 0x511E6), generated into this run dir.
