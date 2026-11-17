# GFX1151 最终验证报告（中文）— Windows ML 2.x × Ultralytics × Radeon 8060S

**Mission ID:** `UWR-GFX1151-FOUNDATION-TO-VALIDATION`
**Run:** `G2_TIER_A_RUN2`（+ `G1_PREFLIGHT_FINAL`, `NEG_INJECT_RUN1`, `G3_TIERB_YOLO26S`）
**日期:** 2026-11-17 (UTC)

## A. 执行判定（Executive Verdict）

| 阶段 | 判定 | 说明 |
|---|---|---|
| G0 Foundation | **GO** | 仓库/CI/邀请/分支保护全就位 |
| G1 EP Preflight | **GO** | AMDGPU + DML 双 EP canary PASS（profiler 证明 GPU 执行 + CPU 数值一致） |
| G2 YOLO26 七任务 | **GO** | 9 模型 × 2 EP = 18/18 `PASS_GPU_EXECUTED_FULL` |
| G3 全目录 Disposition | **CONDITIONAL_GO** | 142 条全分类 ledger；33 个 Tier B 基线 NOT_RUN（含确定性 resume 命令），诚实分母 |
| G4 Perf/Soak/对抗 | **GO** | 基准齐备；负例注入 5/5 fail-closed；Soak 见 SOAK_RUN1 |

- **`gfx1151_ONLY_GO`** ✅（本机证据齐备）
- `ALL_MODELS_CATALOG_DISPOSITION_GO` ❌（33 基线未跑——resume 命令在 ledger）
- `ALL_GPU_THREE_HOST_NOT_RUN`（gfx1100/gfx1201 均 NOT_RUN，未伪造）

## B. 精确 Git 状态

| 项 | 值 |
|---|---|
| main HEAD | `ee47a44`（CI run 44094780122 success @ 精确 HEAD） |
| feature 分支 | `mission/gfx1151-windowsml-foundation` |
| G1 提交 | `777082e`（PR #2 head；windows-cpu-ci + secret-scan 双 PASS） |
| G2 提交 | `8af17db` |
| Draft PR | https://github.com/AIwork4me/ultralytics-windows-radeon-ep/pull/2 |
| gfx1201 Issue | https://github.com/AIwork4me/ultralytics-windows-radeon-ep/issues/1（assignee: LZ-QWQ） |
| 分支保护 | main：PR-only + 必需检查 `windows-cpu-ci`,`secret-scan` + 禁 force-push |
| 上游 | 未触碰 ultralytics/microsoft/onnxruntime；无 force-push；无历史重写 |

## C. 8060S 主机指纹（实测）

- OS：Windows 11 25H2，Build **26200.9974**（≥24H2 26100 ✓）
- CPU：AMD RYZEN AI MAX+ 395（Ryzen AI MAX APU），RAM 123.6 GB
- GPU：AMD Radeon(TM) 8060S Graphics，PCI\VEN_1002&DEV_1586，**gfx1151/RDNA3.5**
- 驱动：32.0.22032.6002（2025-12-15，WDDM 3.2，≥ Adrenalin 24.12.1 要求 ✓）
- 显存模型：集成显卡，dedicated ~4GB 报告值 / SharedSystemMemory ≈ 99 GB（不称"专用 VRAM"）
- 共存适配器：GameViewer Virtual Display Adapter、Microsoft Basic Render Driver（均被身份解析排除）
- Python：CPython 3.13.13 x64（venv 化；未用 Microsoft Store Python）
- ORT：onnxruntime-windowsml **1.25.2.202605110140**（wasdk 2.3.0 钉住版本）
- WindowsAppRuntime：2.3.x–2.6.5 已装（系统组件，未改动）

## D. WinML 目录 + AMDGPU 设备映射 + 负例 canary

- Python 投影：`winui3.microsoft.windows.ai.machinelearning`；`ExecutionProviderCatalog.get_default()` 需要 bootstrap 上下文（否则 WinError 0x80073CF4）
- 目录列出（1.8-era）：MIGraphXExecutionProvider(NOT_PRESENT)、VitisAI(NOT_PRESENT)、WebGpu(NOT_PRESENT)
  - `ensure_ready_async()` → HRESULT **0x80073B0C** "Failed to ensure provider ready"（如实记录 BLOCKED_EP 环节）
  - `AMDGPUExecutionProvider`（WinML ≥2.5.96）在 PyPI wasdk 稳定版（2.3.0）面上不可用
- **实际 AMD GPU EP 已随机预装（机器级）**：`MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8_1.8.65.0`
  - `ExecutionProvider/amdgpu-ep.dll` sha256 `cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab`
  - 附带 `custom_kernels_gfx1151.dll`、`hipblaslt/library/gfx1151/*.hsaco`、hip-backend + directx-backend（伞形后端）
- 注册路径（文档合规）：`ort.register_execution_provider_library("AMDGPU", <dll>)` → provider 名 **"AMDGPU"**
- 设备映射：8060S 双重枚举（DXGI LUID 0x…11746 / 0x…1FA74；两个 OrtEpDevice）
  - dev[0] → HIP failure 101 invalid device ordinal（如实记录）
  - dev[1] → **GPU 执行成功**（profiler `{'AMDGPU': n}`）；会话设备选择为 fail-closed 探测式（拒绝盲选 0 号）
- DML：`DmlExecutionProvider` 独立验证（同物理 Radeon，device_id=DXGI 序号交叉核对）
- 负例：`NEG_INJECT_RUN1/NEGATIVE_INJECTION.json` 5/5 fail-closed

## E. 目录资格账本（142 条，快照 8.4.215 @ b1942e93）

| disposition | 数量 |
|---|---|
| EVIDENCED（Tier A 全过） | 9 |
| NOT_RUN_eligible(BASELINE_REQUIRED) | 33 |
| NOT_RUN_eligible(RESOURCE_GATED) | 81 |
| NOT_RUN_eligible(OPTIONAL_STRESS) | 12 |
| ASSET_BLOCKED（YOLOv7/v6/YOLO-NAS/seg-12） | 4 |
| LICENSE_RESTRICTED（SAM3） | 1 |
| NOT_APPLICABLE（YOLOv4、Tracking 管线） | 2 |

`full_coverage_claim_allowed = false`（诚实分母）；resume 命令在 `DISPOSITION_LEDGER.json`。

## F. 七任务 + 可达模型实测（18/18 FULL GPU PASS）

| 模型 | 任务 | AMDGPU | DML | max_abs | PT task oracle |
|---|---|---|---|---|---|
| yolo26n | detect | PASS_GPU_EXECUTED_FULL | PASS_GPU_EXECUTED_FULL | 1.0e-03 / 7.3e-04 | PASS |
| yolo26n-seg | segment | 同上 | 同上 | 1.6e-03 / 1.3e-03 | PASS |
| yolo26n-sem | semantic | 同上 | 同上 | 0.0 / 0.0 | PASS |
| yolo26n-depth | depth | 同上 | 同上 | ≤阈值 | PASS |
| yolo26n-cls | classify | 同上 | 同上 | ≤阈值 | PASS |
| yolo26n-pose | pose | 同上 | 同上 | ≤阈值 | PASS |
| yolo26n-obb | obb | 同上 | 同上 | ≤阈值 | PASS |
| yolo11n | detect | 同上 | 同上 | ≤阈值 | PASS |
| yolov8n | detect | 同上 | 同上 | ≤阈值 | PASS |

（每例：CPU-ORT 同 ONNX by-name parity + 目标 EP profiler 节点>0 + 设备身份链 + PT 应用级 oracle；receipts 不可变，`verify_receipts` ALL_OK）

## G. 基准（warm Session.run，30 样本，perf_counter_ns）

p50 中位（ms）：AMDGPU 1.5（cls）～14.2（depth）；DML 0.6（cls）～9.6（depth）。
完整 p50/p95/p99/mean/std 见各 receipt 的 `benchmark_data`。冷启动（session create）数百 ms 级。
Soak：`SOAK_RUN1`（15 分钟 × 双 EP，心跳 30s，看门狗 180s）——见 JSON 工件。

## H. 失败目录 / 回退与负例

| 现象 | 定性 | 处置 |
|---|---|---|
| provider 名写成 AMDGPUExecutionProvider | wrapper **静默 CPU 回退**（两次活捉） | profiler 节点计数抓出；固化到 amdgpu_session 文档 + 对抗测试 |
| AMDGPU dev[0] HIP 101 invalid ordinal | 双枚举中显示路无 HIP ordinal | fail-closed 设备选择（探测式） |
| ensure_ready 0x80073B0C | MIGraphX 动态安装失败 | 如实记录；改走已安装 amdgpu-ep.dll（文档 Bring-your-own-EP 路径） |
| "Init provider bridge failed"（ORT env 创建警告） | 不阻断 EP 注册/执行 | 记录在案（BACKEND_UNVERIFIED 注记） |
| 负例注入 5 项 | 全部按预期暴露（BLOCKED_IDENTITY / BLOCKED_EP / FAIL_NO_GPU_COMPUTE / DML wrong adapter / wrapper CPU retry） | CLI 显式 `*_FAILED` + 非零退出码 |

## I. 证据清单 / 许可 / CI

- 证据目录：`reports/gfx1151/{G1_PREFLIGHT_FINAL, G2_TIER_A_RUN2, NEG_INJECT_RUN1, G3_TIERB_YOLO26S, SOAK_RUN1}`
- `G2_TIER_A_RUN2/manifest.sha256`：180 条目，verify OK（含 receipts/raw/profiles）
- 权重不提交（.gitignore *.pt/*.onnx）；AGPL/模型许可保留（docs/LICENSES.md）；脱敏扫描 CLEAN
- CI：`windows-cpu-ci` + `secret-scan` 在 `ee47a44`（main）与 `777082e`（PR head）双绿；`CPU_CI_DOES_NOT_ATTEST_GPU`

## J. 剩余工作与授权请求

1. Tier B 33 基线（+81 RESOURCE_GATED +12 OPTIONAL_STRESS）——resume：
   `python -m runner.run_matrix --tier full --ep both --resume ...`（DISPOSITION_LEDGER.json）
2. gfx1100（7900 XTX）主机：待独立主机就绪（NOT_RUN 保持）
3. gfx1201（9070 XT）：LZ-QWQ 已接受 Write 邀请并认领 Issue #1；跑 runbook 即可
4. `ensure_ready_async` 动态安装 0x80073B0C 根因（可能需 Store/网络环境）——低优先级（本机已装 EP 1.8）
5. WinML ≥2.5.96 的 wasdk/AMDGPUExecutionProvider 面市后：升级 venv 验证新 EP 名义路径
