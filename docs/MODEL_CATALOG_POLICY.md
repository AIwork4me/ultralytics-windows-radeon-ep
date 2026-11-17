# MODEL_CATALOG_POLICY

- Catalog is generated (`runner.generate_catalog`) from the pinned upstream
  snapshot (`catalog/upstream_snapshot.json`: release tag + commit SHA + docs
  access date) and reviewed in a separate diff. No unadvertised weights.
- YOLO26 seven tasks × official sizes; classic families (11/v8/v12/v10/v9/v5u/
  v3u); RT-DETR; YOLO-NAS (asset-blocked); YOLOE/YOLO-World (multi-component,
  prompt-dependent); SAM family (predict-only / HF-gated); YOLOv4 = NOT_APPLICABLE;
  tracking = application pipeline, not an ONNX task.
- Task default imgsz honored: classify 224, depth 768, OBB 1024, others 640
  (model metadata wins over assumptions).
- Export semantics: `nms=None|True|False`, `quantize=16|8` (never legacy
  half/int8 flags).
- Tier A = seven-task compact smoke + YOLO11n/v8n detect. Tier B = every eligible
  baseline (fp32/static/batch1/default imgsz/raw). Stress tiers (NMS modes,
  dynamic, batch 2/4, quant) are OPTIONAL_STRESS, separately budgeted.
- Eligibility: `eligible | asset_blocked | export_blocked | license_restricted |
  not_applicable` with reason; every entry keeps a final disposition — nothing
  silently dropped; "full coverage" counts NOT_RUN in the denominator.
