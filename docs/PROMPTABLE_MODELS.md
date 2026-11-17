# PROMPTABLE_MODELS — YOLOE / YOLO-World / SAM family handling

These are NOT single-image classifiers: they need prompt encoders, text
embeddings (CLIP), or multi-component pipelines.

- **YOLOE**: encoder / prompt-encoder / decoder are separate exportable graphs
  when officially supported; we test each legitimately exportable component and
  the pipeline semantics. If an official ONNX pipeline is unavailable for a
  component, status is `EXPORT_UNSUPPORTED` — never `FAIL_AMDGPU`.
- **YOLO-World**: export requires the **worldv2** variant for ONNX workflows;
  arbitrary prompt settings have no official ONNX path — same rule.
- **SAM3/3.1**: HF-gated weights (license/access) -> `LICENSE_RESTRICTED` unless
  access is legally obtained by the operator.
- **SAM2 / SAM / MobileSAM / FastSAM**: `PREDICT_ONLY`; multi-component
  (image encoder + prompt encoder + mask decoder). Export support is verified
  live per version; unexportable components are `EXPORT_UNSUPPORTED`, and
  unavailable official checkpoints are `ASSET_GATED`, not EP failures.
