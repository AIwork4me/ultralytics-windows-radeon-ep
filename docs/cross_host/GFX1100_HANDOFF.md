# GFX1100（Radeon RX 7900 XTX）真机验收交接

本文件面向 **另一台物理主机** 的负责人。当前执行主机只有 gfx1151（Radeon 8060S），
因此 gfx1100 的一切结论必须由你在自己的机器上产出，本仓库不会代替你声明任何 PASS。

先读：`AGENTS.md`（失败即关闭、不可伪造、Receipt 不可改）、
`docs/GPU_EVIDENCE_CONTRACT.md`（证据契约）、`docs/HARDWARE_CLAIM_MATRIX.md`。

---

## 0. 前置条件

- Windows 11 24H2（build 26100）或更高；
- Radeon RX 7900 XTX（gfx1100，DID 0x7448 / 0x744C）；
- 管理员权限（仅安装 Python 包与读取设备信息；不改驱动，除非文档给出该 EP 自身的最低版本且你确实低于它）；
- 网络可访问 PyPI 与 GitHub（若 `github.com:443` 被阻断，见第 9 节）。

建立三个虚拟环境（与主仓库一致）：

    python -m venv .venv-winml
    .venv-winml\Scripts\pip install -e .[winml] pytest
    .venv-winml\Scripts\pip install "windowsml==2.5.96"
    python -m venv .venv-export
    .venv-export\Scripts\pip install ultralytics torch onnx onnxruntime
    python -m venv .venv-cpu
    .venv-cpu\Scripts\pip install onnxruntime numpy

---

## 1. 硬件身份预检（必须先做）

    .venv-winml\Scripts\python -m runner.preflight --gfx gfx1100

通过标准：`VERIFIED_GFX1100`；日志里 DID = 0x7448/0x744C；LUID 有值；
请求 gfx1151 或 gfx1201 必须返回 `WRONG_OR_AMBIGUOUS_DEVICE`（fail-closed 自检）。

若出现 `AMBIGUOUS_GFX_TARGET` 或多张受支持 GPU，不要强行继续，先记录。

---

## 2. 官方 Windows ML EP 接入（只读探测）

    .venv-winml\Scripts\python -m runner.probe_amdgpu_ep --out reports\mission_r4\PROBE_GFX1100.json --gfx gfx1100

判读 `state`：

| state | 含义 | 下一步 |
| --- | --- | --- |
| `EP_REGISTERED` / `GPU_EXECUTED` | 可用 | 继续第 3 节 |
| `EP_NOT_READY` | Catalog 提供但未就绪 | 对目标 provider 调 `ensure_ready_async()` 后重跑 |
| `CATALOG_EMPTY` | 系统没有给该设备提供 EP | 记录 `BLOCKED_EXTERNAL`，不要从非官方渠道拿 DLL |
| `NOT_INSTALLED` | 缺 `windowsml` 绑定 | 安装 2.5.96 后重跑 |

注意：Microsoft 文档明确 **不要** 用 `EnsureAndRegisterCertifiedAsync()` 注册 Python ORT；
正确路径是 `provider.ensure_ready_async()` 然后
`ort.register_execution_provider_library(provider.name, provider.library_path)`。

---

## 3. 统一 Runtime Canary（单模型，最快验证链路）

    .venv-winml\Scripts\python -c "import numpy as np; np.savez('canary_input.npz', images=np.zeros((1,3,640,640), dtype=np.float32))"
    .venv-winml\Scripts\python -m runner.unified_predict ^
      --model models\onnx\yolo26n.raw_b1_fp32_static.onnx ^
      --inputs-npz canary_input.npz ^
      --policy prefer_amdgpu_allow_dml --gfx gfx1100 ^
      --out reports\unified\gfx1100_canary.json --run-id GFX1100_CANARY_<UTC>

退出码 0 才表示 GPU_FULL / GPU_FULL_DML；30/31/34 见
`docs/unified/CONTRACTS.md`。结果里必须有
`target_ep_profile_nodes > 0`、`cpu_profile_nodes == 0`、`numeric_parity` 全 allclose。

---

## 4. Tier A × 双 EP（9 checkpoints × 2 EP = 18 例）

    .venv-winml\Scripts\python -m runner.run_matrix --gfx gfx1100 --tier core --ep both --benchmark --with-negative --run-id GFX1100_R5_<UTC> --winml-python .venv-winml\Scripts\python.exe --export-python .venv-export\Scripts\python.exe

- 9 个 checkpoint：yolo26n Detect/Segment/Semantic/Depth/Classify/Pose/OBB、yolo11n Detect、yolov8n Detect；
- 每个 (model, EP) 必须严格校验为 `PASS_GPU_EXECUTED_FULL` 才计入；
- 退出码 0 = 全部 PASS；1 = 证据伪造/失败；2 = 有阻塞/未执行（AMDGPU 未就绪时就是 2）。

若 AMDGPU 仍 `CATALOG_EMPTY`，用 `--ep dml` 单独完成 DML 回归，并在报告中把 AMDGPU 标为 `BLOCKED`。

---

## 5. 多输入任务正确性（7 任务 × 4 变体）

    .venv-winml\Scripts\python -m runner.multi_input_oracle --gfx gfx1100 --ep dml --out reports\gfx1100\multi_input_dml.json

AMDGPU 可用时再跑 `--ep amdgpu`。每个变体的 Detect P/R + IoU、Segment mask IoU、
Semantic mIoU、Depth rel MAE、Classify Top-1/Score Drift、Pose OKS、OBB Rotated IoU 必须给出数值，
不允许空指标。

---

## 6. Benchmark 与 Soak

    .venv-winml\Scripts\python -m runner.bench_repeats --onnx models\onnx\yolo26n.raw_b1_fp32_static.onnx --input-npy <run_dir>\inputs\<...>.npy --ep dml --gfx gfx1100 --out reports\gfx1100\bench_detect.json --telemetry-out reports\gfx1100\bench_detect_telemetry.jsonl --reps 3 --samples 50

    .venv-winml\Scripts\python -m runner.soak --ep dml --gfx gfx1100 --minutes 15 --onnx models\onnx\yolo26n.raw_b1_fp32_static.onnx --input-npy <...>.npy --run-id GFX1100_SOAK_<UTC>

- benchmark 必须记录 ONNX/input SHA（新版本已强制），没有 SHA 的旧数据只能标 `UNBOUND_LEGACY_OUTLIER`；
- soak 单 GPU 进程、有心跳与 no-progress watchdog、有上限；失败记录不可删除。

---

## 7. Negative Injection

    .venv-winml\Scripts\python -m runner.negative_injection --gfx gfx1100 --run-id GFX1100_NEG_<UTC>

必须全部 fail-closed：错误 DID、缺失 EP、错误 DML ordinal、CPU 冒充 GPU、
静默 CPU 回退、错误 input/model SHA、篡改 Receipt、不完整 task oracle、假设备绑定。

---

## 8. 证据冻结与校验

    .venv-winml\Scripts\python -m runner.redact_evidence --run-dir reports\gfx1100\<run_id> --check
    .venv-winml\Scripts\python -m runner.finalize_run --run-dir reports\gfx1100\<run_id>
    .venv-winml\Scripts\python -m runner.verify_receipts --run-dir reports\gfx1100\<run_id>
    .venv-winml\Scripts\python -m runner.build_compatibility_matrix --run-dir reports\gfx1100\<run_id>

先脱敏，再冻结；冻结后不改内容。任何结论都必须能被 `verify_receipts` 独立重算。

---

## 9. GitHub 提交流程

- 新分支：`mission/gfx1100-physical-validation`；Draft PR 到 main；
- 不要 force-push，不要改历史 Receipt，不要删除失败记录；
- 若 `git push` 因网络不可用，可让维护者用 API 推送（本仓库的提交对象可逐字节复现，SHA 一致）；
- PR 描述里写清楚：主机身份（DID/LUID/驱动）、run_id、9×2 结果、benchmark 的 ONNX/input SHA。

---

## 10. 最终判决（必须分层）

- `GFX1100_AMDGPU_AND_DML_CORE_GO`：两 EP 都 9/9 且证据严格校验通过；
- `GFX1100_CONDITIONAL_GO`：一个 EP 完整、另一个有明确外部阻塞；
- `GFX1100_BLOCKED_EP`：Catalog 不提供 EP 且无官方产物；
- `GFX1100_NO_GO`：证据不可信或任务正确性失败。

把判决写进 `docs/cross_host/GFX1100_RESULT_CN.md` 并回填 Issue #1 或新 Issue。
