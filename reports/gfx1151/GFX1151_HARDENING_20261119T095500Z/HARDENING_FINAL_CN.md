# GFX1151 独立加固报告 — P0/P1 审计与 AMDGPU EP 只读调查

- 日期：2026-11-19（Asia/Shanghai）
- 仓库：`AIwork4me/ultralytics-windows-radeon-ep`
- 基线：Draft PR #8 head `13b30f5`；Issue #7
- 分支：`mission/gfx1151-independent-hardening`（从 PR #8 head 建立）
- 新 Draft PR：[#9](https://github.com/AIwork4me/ultralytics-windows-radeon-ep/pull/9)（关联 Issue #7 与 PR #8）
- 证据目录：`reports/gfx1151/GFX1151_HARDENING_20261119T095500Z/`
- 原则：不修改 PR #8 历史 Receipt；不伪造新的 GPU PASS；未合并任何 PR；未安装系统组件或驱动。

## 0. 结论摘要

| 项目 | 结论 | 关键证据 |
| --- | --- | --- |
| **P0-A gfx 硬编码** | **已修复** | `r1_pipeline`/`run_matrix`/`preflight`/`soak`/`bench_repeats` 不再默认 gfx1151；gfx 由 DXGI DID 推断/校验；gfx1100/gfx1201 请求在本机 fail-closed |
| **P0-B contract-aware resume** | **已实现** | `RUN_CONTRACT.json` + 每个 Receipt 的 `contract` 块；代码 SHA、harness hash、模型 SHA、输入 SHA、EP、阈值、设备身份全部校验；不匹配即拒绝，不覆盖 Receipt |
| **P0-C 最终 PASS/退出码** | **已修复** | `run_matrix` 对每个 Receipt 执行 `validate_receipt_strict` + contract 校验，只用 derived 证据计数；exit 0/1/2 由验证结果决定 |
| **P0-D 阈值快照** | **已统一** | 首次运行写 `thresholds_snapshot.json`；resume 必须与快照及 live config 完全一致；旧 run_id 改阈值 → `THRESHOLDS_CONFIG_CHANGED` |
| **P1-A DML 物理绑定** | **证据口径已收紧** | DXGI 仅 `HOST_DXGI_ENUMERATION_INDIRECT` / `is_ep_reported=false`；没有 EP-reported LUID 时禁止 `PHYSICAL_GPU_IDENTITY_CORROBORATED` |
| **P1-B dedicated/shared memory** | **已核对口径** | DXGI `DedicatedVideoMemory` 32589 MiB、`SharedSystemMemory` 78459 MiB；WMI `AdapterRAM` 4293918720 B（4095 MiB，uint32 饱和），三者不可互换 |
| **P1-C Pose 4× 时延** | **ROOT_CAUSE=UNKNOWN** | 081900Z 为 2.56–2.70 ms；074700Z 为 10.07–10.97 ms；095000Z 单 session 为 11.07 ms；无 clock/power/temperature 数据，PR #8 的“热状态”结论不成立 |
| **AMDGPU EP** | **BLOCKED_EP（未变）** | 无 `amdgpu-ep.dll`、无 AMD GPU EP 包；legacy 2.3.0 与官方 2.5.96 catalog 均返回 0 providers；未做任何系统变更 |

## 1. 审阅发现（PR #8 / Issue #7）

按 `AGENTS.md` 的 Fail-Closed 标准复核 Draft PR #8，确认以下可复现缺陷：

1. **P0-A**：`runner/r1_pipeline.py` 在 worker case JSON、`_host_block` 默认参数、`status.host` 中硬编码 `gfx1151`/`VERIFIED_GFX1151`/`8060S`。在 gfx1100/gfx1201 主机上存在错误设备选择或错误认证风险。
2. **P0-B**：`--resume` 只按“Receipt 文件是否存在”跳过；没有校验代码 SHA、模型 SHA、输入 SHA、EP、阈值或设备身份。`resume_matrix` 甚至向无参数的 `run_matrix.main()` 传参，实际会 `TypeError`。
3. **P0-C**：`run_matrix` 最终审计直接读取 `receipt["status"][ep]`，没有重新执行 strict validator；Receipt 的状态字段可被信任。
4. **P0-D**：`thresholds_snapshot.json` 只写不读；resume 仍使用 live `configs/thresholds.yaml`，旧 run_id 上改阈值可改变验收标准。
5. **P1-A**：Receipt 已记录 `EP_DEVICE_SELECTED`，但验证器允许仅凭 `dxgi_corroboration` 推导 `PHYSICAL_GPU_IDENTITY_CORROBORATED`；这会把主机 DXGI 间接枚举冒充 EP 独立报告的 LUID。
6. **P1-B**：报告把 dedicated/shared 混在一起描述，且 WMI `AdapterRAM` 的 uint32 饱和值没有被标注。
7. **P1-C**：PR #8 报告把 Pose 约 4× 差异归因于“GPU 时钟/机器热状态”，但没有任何 clock/power/temperature 证据；这是猜测。

## 2. P0 修复

### 2.1 P0-A：gfx 选择与认证

- 新增 `device_identity.target_device_ids()`、`gfx_for_device_id()`、`infer_gfx_from_adapters()`；gfx 只由 DXGI DID 决定，拒绝 generic “Radeon” 名称选择。
- `r1_pipeline` 新增 `--gfx`/`--gpu-name-needle`；省略 `--gfx` 时从 live DID 推断，不再默认 gfx1151。worker case 必须携带明确 `gfx`，`_ep_worker` 缺失时返回 usage error。
- `status.host` 由 `_host_status()` 从 live `gfx` 派生为 `VERIFIED_GFX1100/1151/1201`；`verified_device_identity` 仅当 DID 命中且 `did_match=true`。
- `run_matrix`/`preflight`/`soak`/`bench_repeats` 同样不再默认 gfx1151；`finalize_run` 的 ledger 列按实际 host 动态生成。
- 本机实测（`HOST_IDENTITY_MATRIX.json`）：

| 请求 | 结果 | 说明 |
| --- | --- | --- |
| `--gfx` 省略 / `gfx1151` | `verified=true`, DID `0x1586`, host_id `host_53f62d2ac2c6` | 正确选择 8060S |
| `gfx1100` | `verified=false` | `WRONG_OR_AMBIGUOUS_DEVICE`，found `0x1586`，未创建 session |
| `gfx1201` | `verified=false` | 同上，未创建 session |

### 2.2 P0-B / P0-D：contract-aware resume

- `src/radeon_ort/contract.py` 新增：
  - `harness_sha256(repo_root)`：对 `runner/`、`src/radeon_ort/`、`configs/`、`schemas/`、`pyproject.toml` 做内容哈希；
  - `RUN_CONTRACT.json`：run_id/gfx/eps/models/cases/thresholds/thresholds_sha256/code/device_identity/images/options/contract_sha256；
  - 每个新 Receipt 的 `contract` 块：run contract SHA、code、thresholds SHA、device identity、EP、model `pt/onnx` SHA、input `input_id/image/tensor` SHA；
  - `verify_run_contract`、`validate_receipt_contract`、`_case_contract_problems`（当前文件 vs Receipt contract）。
- `r1_pipeline --resume` 的顺序：加载 contract → 校验 code/thresholds/device/models/eps/images/options → 对每个已有 Receipt 执行 strict validator + contract + 当前文件哈希 → 全部通过才跳过；任何不匹配立即非零退出，不编辑、不覆盖、不重跑。
- `run_matrix --resume` 现在真正转发 `--resume`；`resume_matrix` 通过 `main(argv)` 正确传参（修复原 `TypeError`）。
- `thresholds_snapshot.json` 首次写入后不可覆盖；resume 时快照必须存在且与 live config 完全一致；旧 run_id 修改 `configs/thresholds.yaml` → `THRESHOLDS_CONFIG_CHANGED`，要求新建 run_id。
- 实测：
  - 脏工作树上的 run `HARDENING_20261119T095000Z`（contract `68b8bd3e…`）执行 resume，输出 `CODE_STATE_DIRTY` 并 `rc=2`，未复用 Receipt；
  - 干净提交 `4971342` 上的 run `CONTRACT_FINAL_20261119T100200Z`（contract `671763fe…`）执行 `resume_matrix`，输出 `RESUME OK ... (contract-identical)`；`run_matrix` strict audit 为 `PASS_FULL=1/1`，`rc=0`；
  - `resume_matrix` 在 `--reports-root` 下正确找到 contract；旧 run_id 的阈值/代码/选项不匹配时按 contract 拒绝（不静默继续）。

### 2.3 P0-C：最终审计

`runner/run_matrix.py` 的 `_audit()`：

1. 重新加载 contract 与 `thresholds_snapshot.json`，拒绝 contract/threshold 篡改；
2. 对每个 expected `(case, EP)` 读取 Receipt，执行 `validate_receipt_strict`（schema + 证据重算）和 `validate_receipt_contract`；
3. PASS 只来自 `v["derived"][ep]`，不读取 `receipt["status"][ep]` 作为结论；
4. 缺失 Receipt / `BLOCKED_*` / `NOT_RUN` / PARTIAL → exit 2；伪造、stale、contract/threshold mismatch、额外 Receipt、任何 `FAIL_*` → exit 1；
5. exit 0 仅当全部 expected case strict-validate 为 `PASS_GPU_EXECUTED_FULL`。

回归测试覆盖：伪造 `status` 但 DML nodes=0 → audit rc=1；少一个 EP 的 Receipt → rc=2；合法 Receipt → rc=0；contract 记录 `worktree_dirty=true` → audit rc=1（dirty run 不能认证 GPU PASS）。

## 3. P1 证据复核

### 3.1 P1-A：DML 物理设备绑定

- DML `device_binding` 现在显式记录：
  - `state=EP_DEVICE_SELECTED`；
  - `dxgi_corroboration.evidence_kind=HOST_DXGI_ENUMERATION_INDIRECT`、`is_ep_reported=false`；
  - `ep_reported_identity.exposes_luid=false`、`exposes_pci_id=false`；
  - `mapping_assumption`：DML `device_id` 等于 DXGI ordinal 是主机侧假设，不是 EP-reported LUID。
- `derive_verdicts` 和 strict validator 现在禁止把上述间接证据升级为 `PHYSICAL_GPU_IDENTITY_CORROBORATED`；该状态要求 EP 自己报告的 LUID 且与 DXGI LUID 一致（本机 AMDGPU/DML 均不提供）。
- 结论：PR #8 的 DML 证据应标记为 **EP_DEVICE_SELECTED + host DXGI corroboration**，不能称为“EP 独立确认的物理 LUID”。本报告与代码均按此口径。

### 3.2 P1-B：dedicated / shared GPU memory

| 指标 | 值 | 来源 | 口径 |
| --- | --- | --- | --- |
| DedicatedVideoMemory | 32589 MiB | DXGI `DXGI_ADAPTER_DESC1` | 驱动/BIOS 报告的 dedicated segment；APU 上是 carve-out，不等于独立 VRAM |
| SharedSystemMemory | 78459 MiB | DXGI `DXGI_ADAPTER_DESC1` | GPU 可共享的系统 RAM，不是 VRAM |
| DedicatedSystemMemory | 0 MiB | DXGI `DXGI_ADAPTER_DESC1` | 系统内存 dedicated segment |
| WMI AdapterRAM | 4293918720 B（4095 MiB） | WMI `Win32_VideoController.AdapterRAM` | uint32 字段，接近 4 GiB 饱和，不能当容量 |

三者是不同计量口径；运行期 VRAM 占用、带宽、温度、功耗仍为 `NOT_MEASURED`。`MEMORY_PROVENANCE.json` 记录了完整来源与语义。

### 3.3 P1-C：Pose 4× 时延

证据（`BENCH_DML_yolo26n-pose.json`，同一 ONNX SHA `f5a5ce5f…`、同一 `input_id` `f7c35eeb…`、DML、`DmlExecutionProvider=1`、CPU nodes=0）：

| run | warm p50（3 reps） | 说明 |
| --- | --- | --- |
| `GFX1151_PHYSICAL_20261119T074700Z` | 10.073 / 10.787 / 10.969 ms | `bench_repeats`，fresh session/rep |
| `GFX1151_PHYSICAL_20261119T081900Z` | 2.560 / 2.704 / 2.662 ms | 同一方法，约 4× 更快；benchmark 文件无 SHA，无法绑定到受审 ONNX/输入 |
| `HARDENING_20261119T095500Z` | 11.068 ms（单 session，p50，30 samples） | `r1_pipeline --benchmark`，方法不同，仅作旁证 |
| `HARDENING_20261119T095500Z`（hash-bound） | 11.862 / 12.019 / 11.973 ms | 本报告新增；ONNX/input SHA 绑定；kernel 6.17–7.10 ms；ADL clocks 2900/1000 MHz 恒定 |

Profiled first-run durations（raw ORT profile）：

| run | kernel_time | model_run |
| --- | --- | --- |
| 074700Z | 3147 µs | 8144 µs |
| 081900Z | 3021 µs | 8079 µs |
| 095000Z | 5945 µs | 16788 µs |

结论：

- **根因（证据层）**：旧 `BENCH_DML_yolo26n-pose.json` 只记录文件名，不记录 ONNX/input SHA256；它无法独立证明自己测的是受审的那份模型/输入。因此 2.56 ms 与 10–11 ms 的“4× 对比”是**未绑定（unbound）比较**，不能作为性能结论。本报告新增 hash-bound benchmark，ONNX `f5a5ce5f…`、input `00324540…`、DML、`DmlExecutionProvider=1`、CPU nodes=0。
- **新实测**：hash-bound warm p50 `11.862 / 12.019 / 11.973 ms`；kernel_time `7.096 / 6.221 / 6.174 ms`；wall−kernel `4.77 / 5.80 / 5.80 ms`。说明当前 11–12 ms 中约 5 ms 是 host 侧提交/排队开销，而不是纯 GPU kernel。
- **Clock 数据**：ADL `ADL2_Adapter_ObservedClockInfo_Get` 在测量期间恒为 core `2900 MHz`、mem `1000 MHz`（7 个样本），没有 clock 下降；高优先级进程与 profiling on/off 也都没有复现 2.56 ms（normal ≈11.9 ms，high ≈11.9 ms，no-profile ≈9–10 ms，profile-active ≈10.9 ms）。
- **Pose 2.56 ms 的物理根因 = UNKNOWN**：没有当时的 clock/power/temperature/RDP 状态；不能声称热状态。**THERMAL_HYPOTHESIS = UNSUPPORTED_BY_DATA**。PR #8 报告第 166–167 行的热状态归因是猜测，本报告更正。
- **风险处置**：只有带 `evidence_contract.onnx_sha256/input_sha256` 的 benchmark 才能用于比较；旧 2.56 ms 标记为 `UNBOUND_LEGACY_OUTLIER`，不得引用。若需解释该历史值，必须找到当时的 telemetry（已不存在）或按 §7 方案重新采集；在没有数据前保持 UNKNOWN。

## 4. AMDGPU EP 只读调查（Part 2）

完整报告见 `docs/AMDGPU_EP_PROVISIONING.md`；核心事实：

- OS 25H2 build `26200.9974`；AMD 驱动 `32.0.22018.5`（2025-09-17）；`WindowsWorkload.WinMLShared.7.0` `1.2610.1050.0`；Windows App Runtime 2.6.5/2.7.0。
- Python：`wasdk-Microsoft.Windows.AI.MachineLearning 2.3.0`（低于 Microsoft 文档要求的 **2.5.96**）；官方 `windowsml==2.5.96` 仅下载到临时目录检查，**未安装**。
- EP 包：只有 `WindowsWorkload.EP.AMD.VitisAI.Framework.1.8`（AMD **NPU**，1.8 时代）；没有 AMD GPU EP 包，没有 `amdgpu-ep.dll`。
- Catalog：legacy 2.3.0 projection → 0 providers；官方 2.5.96 flat C bindings（临时目录 import）→ 0 providers。说明不是单一 Python projection 的显示问题，OS catalog 当前确实为空。
- ORT：`onnxruntime-windowsml 1.25.2`，仅 CPU/DML；`register_execution_provider_library` 因无 DLL 而无法注册。
- 判定：**AMDGPU: BLOCKED_EP**。没有 AMDGPU PASS，也没有 CPU fallback 冒充。

Provisioning Plan（详见文档，均需用户批准，未执行）：

1. **A1**（用户级）：`pip install windowsml==2.5.96` 到 `.venv-winml`，仅用于官方 catalog/ensure_ready API；
2. **A2**（管理员 + 重启）：AMD GPU 驱动升级到文档最低 `25.10.13.09` 或更新；
3. **A3**（系统级）：官方动态安装 `AMDGPUExecutionProvider` MSIX，校验包签名/版本/DLL hash 后注册；
4. **A4**（系统级 fallback）：BYO EP，仅使用官方签名 MSIX 提取，不得绕过驱动要求；
5. **A5**：任何 Windows optional update/servicing 变更均需批准。

## 5. 测试与证据

- CPU 测试：`tests/unit tests/contracts tests/adversarial` → **243 passed, 1 skipped**（hardware test 在本机跳过；CPU CI 不代表 GPU）。
- Live identity probe：`HOST_IDENTITY_MATRIX.json`（gfx1151 verified；gfx1100/gfx1201 fail-closed）。
- Contract 集成：clean 工作树 `4971342` 上的单 case run `CONTRACT_FINAL_20261119T100200Z`；resume `RESUME OK`；strict audit `PASS_FULL=1/1`，rc=0。该 run 不提交为新的 GPU PASS 证据，只作为 contract 行为测试。
- AMDGPU 只读探测：`AMDGPU_EP_READONLY_PROBE.json`（`BLOCKED_EP` / `BLOCKED_ADMIN_DEPENDENCY`）。
- 内存口径：`MEMORY_PROVENANCE.json`。
- Pose 时延：`POSE_LATENCY_ANALYSIS.json`；新增 hash-bound `BENCH_DML_yolo26n-pose.json` + `BENCH_DML_yolo26n-pose.clocks.jsonl`。
- DML 绑定负向控制：`DML_BINDING_PROBE.json`（无效 ordinal 7/99 失败；相邻 ordinal 1 被接受，记录为 dual enumeration；无 EP-reported LUID）。
- 多输入 task-oracle：`MULTI_INPUT_TASK_ORACLE.json`（detect/pose 各 4 个输入变体，8/8 PASS；明确不是 dataset-level）。
- 历史 Receipt 政策：`docs/LEGACY_RECEIPTS.md`；PR #8 的 18 个 Receipt 仍 `ALL_OK`。
- 硬件声明矩阵：`docs/HARDWARE_CLAIM_MATRIX.md`（gfx1100/gfx1201 无硬件证据，禁止 PASS）。
- 新证据目录没有 Receipt，也没有新的 GPU PASS 声明；PR #8 的历史 Receipt 未被修改。

## 6. 风险根因与处置状态

| # | 风险 | 根因分析 | 本轮处置 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | AMDGPU EP 缺失 | 无 `amdgpu-ep.dll`/无 AMD GPU EP 包；2.3.0 与官方 2.5.96 catalog 均 0 providers；驱动最低版本未证明 | 只读探测 + 审批门禁 provisioning plan；记录 `BLOCKED_ADMIN_DEPENDENCY`；**未安装任何组件** | **仍阻塞，需 A1–A3 批准** |
| 2 | Pose 4× 时延 | 旧 benchmark 无 ONNX/input SHA，属 unbound 比较；当前 hash-bound 实测 ~11.9 ms，kernel 6.2–7.1 ms，ADL clock 恒 2900/1000 MHz；高优先级/profiling 不改变 | 新增 SHA 绑定 + kernel/wall 分解 + ADL clock 采样；旧 2.56 ms 标记 `UNBOUND_LEGACY_OUTLIER`，不再引用 | **证据风险已解决；2.56 ms 物理根因 UNKNOWN** |
| 3 | 单图 task oracle | 原 pipeline 每个任务只跑 1 张图 | 新增 `multi_input_oracle`，detect/pose 各 4 变体 8/8 PASS；seg 等在 7-task 运行时 PT-oracle 子进程卡住，未完成 | **harness 已实现；dataset-level 仍需数据集** |
| 4 | DML 绑定间接 | DML 只暴露 ordinal，不暴露 LUID/PCI | 新增 `DML_BINDING_PROBE` 负向控制：ordinal 7/99 失败，证明 ordinal 被强制执行；仍不能声明 EP-reported LUID | **已加强，限制仍在** |
| 5 | gfx1100/gfx1201 未真机 | 本机只有 gfx1151 | 合成 DID-pin 测试 + 硬件声明矩阵；无硬件不产生 PASS | **硬件阻塞** |
| 6 | 历史 Receipt 兼容 | 旧 Receipt 无 `contract` 块，resume 是循环论证 | 明确 verify-only 政策；旧 Receipt 继续 strict-verify，不复用、不改写 | **按设计接受** |

### 6.1 P1-C 补充结论（hash-bound）

- 旧 `BENCH_DML_yolo26n-pose.json` 只有文件名，没有 ONNX/input SHA；即使文件 mtime 显示模型未变，也不能独立证明当时加载的字节。该比较是 unbound。
- 新增 benchmark 的 `evidence_contract` 记录：
  - ONNX `f5a5ce5f38c6d15ee9af8653f3db9b1775fafd7a910005e6d5b4cf0e515fa656`
  - input `00324540e17f133ce47c239b8ad8de28c80d90ecd36a3b4de3a592c9749befd9`
  - warm p50 `11.862 / 12.019 / 11.973 ms`
  - kernel_time `7.096 / 6.221 / 6.174 ms`
  - wall−kernel `4.77 / 5.80 / 5.80 ms`
  - ADL core/mem clocks `2900/1000 MHz`（7 个样本，恒定）
- 高优先级进程（HIGH_PRIORITY_CLASS）与 profiling on/off 均未复现 2.56 ms：normal ≈11.9 ms，high ≈11.9 ms，no-profile ≈9–10 ms，profile-active ≈10.9 ms。热状态假设没有支持数据。
- 结论：**2.56 ms 的物理根因仍是 UNKNOWN**；可证实的是旧数据 unbound、当前 hash-bound 稳定在 ~11–12 ms，且没有 clock 下降证据。任何后续性能声明必须带 `evidence_contract` 哈希。

### 6.2 多输入 task-oracle

`multi_input_oracle` 对 `bus.jpg` 生成 `orig / flip / bright / crop90` 四个确定性变体，对 detect 和 pose 各跑 4 个输入：

| task | inputs | passed | 说明 |
| --- | --- | --- | --- |
| detect | 4 | 4/4 | all_passed=true |
| pose | 4 | 4/4 | all_passed=true |

这解决了“每个任务只有一张图”的 harness 限制，但不是 dataset-level 精度；seg/sem/depth/cls/obb 的 7-task 批量运行在 segment PT-oracle 子进程处卡住，未完成，不能宣称全部任务多输入通过。

## 7. 下一轮 gfx1151 真机命令

```powershell
# 0) 确认工作树干净；contract 会记录精确 HEAD
git status --short

# 1) 预检（只读；catalog 仍应为 BLOCKED_EP，除非 A1–A3 已完成）
.venv-winml\Scripts\python -m runner.preflight --gfx gfx1151

# 2) 新的 contract run（不要复用 PR #8 的 run_id）
.venv-winml\Scripts\python -m runner.run_matrix --tier core --ep both `
  --gfx gfx1151 --benchmark --with-negative --run-id GFX1151_HARDENING_R1

# 3) 严格验证（contract + receipt + manifest）
.venv-winml\Scripts\python -m runner.verify_receipts `
  --run-dir reports/gfx1151/GFX1151_HARDENING_R1

# 4) 仅在 contract 完全一致时 resume
.venv-winml\Scripts\python -m runner.resume_matrix `
  --run-id GFX1151_HARDENING_R1 --host gfx1151 --tier core --ep both --benchmark

# 5) 只读 AMDGPU 依赖探测（不安装）
.venv-winml\Scripts\python -m runner.probe_amdgpu_ep --gfx gfx1151 `
  --out reports/gfx1151/AMDGPU_PROBE/AMDGPU_EP_READONLY_PROBE.json

# 6) DML ordinal 负向控制（只读 + 1 个 canary session）
.venv-winml\Scripts\python -m runner.dml_binding_probe --gfx gfx1151 `
  --out reports/gfx1151/DML_BINDING_PROBE.json

# 7) 多输入 task-oracle smoke（detect/pose 为例）
.venv-winml\Scripts\python -m runner.multi_input_oracle `
  --models yolo26n.pt,yolo26n-pose.pt --gfx gfx1151 --ep dml `
  --out reports/gfx1151/MULTI/MULTI_INPUT_TASK_ORACLE.json

# 8) hash-bound benchmark + ADL clock 采样（benchmark 必须带 SHA）
.venv-winml\Scripts\python -m runner.bench_repeats `
  --onnx models\onnx\yolo26n-pose.raw_b1_fp32_static.onnx `
  --input-npy <persisted_input.npy> --ep dml --gfx gfx1151 `
  --out reports/gfx1151/BENCH/BENCH_DML_yolo26n-pose.json `
  --telemetry-out reports/gfx1151/BENCH/BENCH_DML_yolo26n-pose.clocks.jsonl
```

## 8. 变更文件

- 核心：`src/radeon_ort/contract.py`（新）、`runner/r1_pipeline.py`、`runner/run_matrix.py`、`runner/resume_matrix.py`、`runner/verify_receipts.py`
- gfx/DID：`src/radeon_ort/device_identity.py`、`src/radeon_ort/host_probe.py`、`runner/preflight.py`、`runner/soak.py`、`runner/bench_repeats.py`、`runner/finalize_run.py`
- 绑定/内存/遥测：`runner/_ep_worker.py`、`src/radeon_ort/receipts.py`、`src/radeon_ort/gpu_telemetry.py`（新）、`schemas/receipt_v2.json`
- 多输入/负向控制：`runner/multi_input_oracle.py`（新）、`runner/dml_binding_probe.py`（新）
- Part 2：`runner/probe_amdgpu_ep.py`、`docs/AMDGPU_EP_PROVISIONING.md`
- 测试：`tests/unit/test_contract_resume.py`、`tests/unit/test_ep_binding_memory.py`、`tests/helpers_receipts.py`
- 文档/证据：`docs/GPU_EVIDENCE_CONTRACT.md`、`docs/LEGACY_RECEIPTS.md`、`docs/HARDWARE_CLAIM_MATRIX.md`、`reports/gfx1151/GFX1151_HARDENING_20261119T095500Z/*`
