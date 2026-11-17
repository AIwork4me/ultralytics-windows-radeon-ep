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
| gfx1151 — Radeon 8060S | 验证中（Tier A 进行中） | 验证中 | `reports/gfx1151/` |
| gfx1100 — RX 7900 XTX | NOT_RUN | NOT_RUN | 未来主机 |
| gfx1201 — RX 9070 XT | NOT_RUN | NOT_RUN | 未来主机（见 gfx1201 issue） |

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
