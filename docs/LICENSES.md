# LICENSES

| component | license | in-repo? |
|---|---|---|
| harness code (`src/`, `runner/`, tests, docs) | MIT (this repo) | yes |
| Ultralytics code + YOLO weights | AGPL-3.0 / model-specific | NO — downloaded at runtime by the operator |
| onnxruntime-windowsml / wasdk projections | MIT (Microsoft) | via pip, not vendored |
| Windows App SDK / Windows ML | Microsoft license terms | system component, never redistributed |
| ONNX | Apache-2.0 | via pip |

Rules enforced: no weights or datasets committed; receipts never embed third-
party content beyond metadata; the repo MIT applies only to project-authored
code and does not relicense anything upstream.
