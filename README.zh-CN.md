# ultralytics-windows-radeon-ep

在 **Windows ML 2.x** 上，以 **AMDGPU Execution Provider**（主）与
**DirectML EP**（独立对照）对 **Ultralytics YOLO ONNX 模型**进行独立的、
fail-closed（失败即关闭）的验证。首个硬件目标：**AMD Radeon 8060S
（gfx1151，Ryzen AI MAX APU）**。

> 本仓库中的 GPU `PASS` 必须有实证：已验证的物理设备身份、目标 EP 的
> profiler 节点计数 > 0、与同 ONNX 的 CPU ORT 参考数值一致、任务级 oracle
> 通过。仅凭 `sess.get_providers()` 不构成任何证明。

## 状态

| 主机 | AMDGPU EP | DirectML | 证据 |
|---|---|---|---|
| gfx1151 — Radeon 8060S | `BLOCKED_EP`：`AMDGPUExecutionProvider` 在 Windows ML 2.x 中确有文档（需 `Microsoft.Windows.AI.MachineLearning` 2.5.96+、Adrenalin 24.12.1+），但本机 EP 目录**一个 provider 都没有** | **9/9 Tier A 物理 PASS** | `reports/gfx1151/R5_GFX1151_20261119T1240/` + `reports/mission_r4/PROBE_AFTER_A1.json` + `reports/mission_r10/PROBE_*.json` |
| gfx1100 — RX 7900 XTX | PENDING_PHYSICAL_HOST | PENDING_PHYSICAL_HOST | 交接：`docs/cross_host/GFX1100_HANDOFF.md` |
| gfx1201 — RX 9070 XT | PENDING_PHYSICAL_HOST | PENDING_PHYSICAL_HOST | 交接：`docs/cross_host/GFX1201_HANDOFF.md` |

本机实测（2026-11-19）：Windows 11 25H2 build 26200.9974，Radeon 8060S
DID `0x1586` / LUID `0x00000000:0x000154E4`，驱动 32.0.22018.5，
`windowsml 2.5.96`、`onnxruntime-windowsml 1.25.2`。Microsoft 官方文档**确实**
为 Windows ML 2.x 列出了 `AMDGPUExecutionProvider`（最低
`Microsoft.Windows.AI.MachineLearning` 2.5.96、Adrenalin Edition 24.12.1 或更高；
MIGraphX 已弃用）。但本机官方 Catalog 仍返回 `S_OK` 且 **0 个 provider** —— EP 包
根本没有被提供 —— 因此 AMDGPU 属于外部阻塞：不是代码缺陷，也不是单靠升级驱动就能解决。
引用与哈希见 `docs/AMDGPU_EP_PROVISIONING.md` 第 2 节与
`reports/mission_r10/DOC_CITATIONS.json`。

`CPU_CI_DOES_NOT_ATTEST_GPU`：GitHub CPU CI 只验证契约与对抗性防伪造测试，
不证明任何 GPU 执行。

## 快速开始（Windows + Radeon GPU）

```powershell
git clone https://github.com/AIwork4me/ultralytics-windows-radeon-ep
cd ultralytics-windows-radeon-ep
python -m venv .venv-winml
.venv-winml\Scripts\pip install -e .[winml] pytest
# 主机指纹 + WinML EP 目录 + 设备身份 + CPU/AMDGPU/DML 金丝雀：
.venv-winml\Scripts\python -m runner.preflight --gfx gfx1151
# Tier A 七任务冒烟（需要 models/pt 下有模型，见 docs/INSTALL_WINDOWS.md）：
.venv-winml\Scripts\python -m runner.smoke --winml-python .venv-winml\Scripts\python --export-python <导出venv的python>
# 严格 receipt 校验：
.venv-winml\Scripts\python -m runner.verify_receipts --run-dir reports\gfx1151\<run_id>
```

## 统一 API（一个入口，四种策略）

```python
from radeon_ort.unified_runtime import predict

result = predict(
    model_path="models/onnx/yolo26n.raw_b1_fp32_static.onnx",
    inputs={"images": tensor},          # 名称 -> ndarray；按名称/dtype/shape/字节哈希
    policy="prefer_amdgpu_allow_dml",   # require_amdgpu | prefer_amdgpu_allow_dml | gpu_first_allow_cpu | cpu_opt_in
    target_gfx="gfx1151",               # 省略时按 DXGI DID 推断，绝不默认架构
    allow_cpu=False,                    # 只有显式同意才允许 CPU
    require_full_gpu=True,
)
print(result["runtime_status"])         # GPU_FULL | GPU_FULL_DML | CPU_ONLY_USER_AUTHORIZED | BLOCKED | FAIL
print(result["attempted_eps"], result["fallback_reason"])
```

GPU 结论要求：目标 EP profiler 节点 > 0、CPU 节点 = 0、与独立 CPU ORT 的按输出名逐元素
parity 通过、设备绑定标签在白名单内；`gpu_full` 与 `task_verified` 分开报告。
命令行：`python -m runner.unified_predict --help`；契约见 `docs/unified/CONTRACTS.md`。

三 venv 布局（winml / export / cpu）、8060S 集成显卡的设备身份规则
（绝不要求 `Discrete=True`）、严格禁止静默 CPU 回退的策略，分别见
[docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md)、
[docs/DEVICE_IDENTITY.md](docs/DEVICE_IDENTITY.md)、
[docs/PROVIDER_FALLBACK.md](docs/PROVIDER_FALLBACK.md)。

## 目录结构

- `src/radeon_ort/` — 测试框架模块（设备身份、EP 目录、会话、receipt、oracle、基准、清单、脱敏）
- `runner/` — CLI：`preflight`、`generate_catalog`、`export_models`、`smoke`、`run_matrix`、`resume_matrix`、`analyze_profiles`、`verify_receipts`、`build_compatibility_matrix`
- `schemas/` — 版本化 JSON 契约（receipt / manifest / host / catalog）
- `catalog/` — 冻结的上游快照 + 生成的模型目录
- `configs/` — gfx1151 配置、数值阈值（运行前固定）、provider 策略
- `tests/` — 单元 / 契约 / 对抗（防伪造）/ 硬件
- `reports/<gfx>/<run_id>/` — receipt、profile、manifest、兼容矩阵

## 许可

本仓库自研框架代码为 MIT。Ultralytics 权重/代码、ONNX Runtime、
Windows App SDK 保留各自许可；本仓库绝不提交模型权重。
