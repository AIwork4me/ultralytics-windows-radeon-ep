# Mission R3–R6 终报（Windows ML 2.x × AMDGPU EP × Radeon 三平台统一推理）

日期：2026-11-19（Asia/Shanghai）
执行主机：HP ZBook Ultra G1a 14 / Ryzen AI MAX+ PRO 395 / Radeon 8060S（gfx1151）
仓库：AIwork4me/ultralytics-windows-radeon-ep

---

## A. Executive Verdict

| Mission | 判决 | 一句话结论 |
| --- | --- | --- |
| R3 | **R3_FINAL_HARDENING_GO** | 退出码折叠、哈希范围缺口、未跟踪文件绕过、探测器语义反转全部修复；PR #8 与 PR #9 已按顺序合并 |
| R4 | **R4_BLOCKED_EXTERNAL** | 官方 2.x API 正常（S_OK），但本机 Catalog 不提供任何 EP，且无官方签名 2.x AMD GPU EP 产物 |
| R5 | **GFX1151_CONDITIONAL_GO** | DirectML 9/9 Tier A 物理 PASS（证据 ALL_OK，15 分钟 soak 通过）；AMDGPU 9/9 BLOCKED_EP |
| R6 | **UNIFIED_RUNTIME_DESIGN_GO** + **THREE_PLATFORM_RC_CONDITIONAL_GO** | 统一 Runtime 在真机跑出 GPU_FULL_DML；gfx1100/gfx1201 仍 PENDING_PHYSICAL_HOST |

没有把任何 BLOCKED 写成 PASS，也没有用 CPU CI、provider 枚举或 Session 创建成功替代 GPU 执行证明。

---

## B. Exact Git State

| 项 | 值 |
| --- | --- |
| 起始 main | `d24f7377813cddcab744c14c82733e890b881234` |
| PR #8 head（合并前） | `13b30f5f86ed2c2e41717658a12fe52a3afa77d2` |
| PR #8 merge commit | `d5ea31227bc7802f5270bce7055cfd9896768545` |
| PR #9 head（= R3 修复后） | `ed60ed47cbfb51e5232abc47ceded5f932e479db` |
| PR #9 merge commit | `bb4dc5a0501923ea1647d0012d3fe33049976f93` |
| R6 head | `7944fc818b1121490c5fb13a5570f1c8e7fb6df0`（PR #10，4 commits） |
| PR #10 merge commit | `444f248339b6d08b266e6b678314679a8ebc94b0`（最终 main） |
| PR #6 | **未合并**，保留分支 `mission/unified-winml-amdgpu-first-runtime` 作设计参考 |
| CI（精确 head） | PR #8 / #9 / #10 均 windows-cpu-ci success + secret-scan success |
| CPU 测试 | 起始 243 passed / 1 skipped → 最终 **292 passed / 1 skipped** |
| 网络约束 | `github.com:443` 不可达，`api.github.com` 可用；远端提交对象按 API 元数据逐字节重建，SHA 与远端一致 |

合并顺序严格按要求：先 PR #8，再把 PR #9 的 base 改到 main、复核对实际 diff（只含 R3 文件）后合并，最后以新分支 PR #10 承载 R6。未使用 force-push，未删除失败历史。

---

## C. Windows ML / AMDGPU EP

| 阶段 | 值 |
| --- | --- |
| OS | Windows 11 25H2 build 26200.9974（满足 24H2+ 要求） |
| GPU | Radeon 8060S，`PCI\VEN_1002&DEV_1586`，LUID `0x00000000:0x000154E4` |
| 驱动 | 32.0.22018.5（2025-09-17，`oem21.inf`，HP OEM）；Adrenalin 版本未暴露 |
| Windows ML 共享运行时 | `WindowsWorkload.WinMLShared.7.0 1.2610.1050.0` |
| Windows App Runtime | 1.7 / 1.8 / 2.7（+ CBS 1.6/2） |
| Python 绑定（A1 前） | 未安装 |
| Python 绑定（A1 后） | `windowsml 2.5.96`（`.venv-winml` 与隔离的 `.venv-r4`） |
| ORT | `onnxruntime-windowsml 1.25.2.202605110140`（未叠加不兼容 wheel） |
| Catalog（官方 2.5.96 flat C） | `WinMLEpCatalogCreate/EnumProviders = 0x00000000`，**0 providers** |
| Catalog（旧 2.3.0 projection） | `api_ok=true`，**0 providers** |
| ORT providers / EP devices | `DmlExecutionProvider`、`CPUExecutionProvider` |
| AMD GPU EP 包 / DLL | 无（只有 AMD **NPU** 的 VitisAI 1.8.80.0） |
| Catalog 状态 | `CATALOG_EMPTY` |

**R3-D 校正**：2026-11-19 抓取的 Microsoft Learn 页面中 `AMDGPU` 出现 0 次、
`2.5.96`/`24.12` 各 0 次；AMD GPU 行是 `MIGraphXExecutionProvider`
（要求 driver 25.10.13.09 **精确**），AMD NPU 行是 `VitisAIExecutionProvider`。
因此「AMDGPUExecutionProvider = WinML 2.5.96+ / Adrenalin 24.12.1+」无法用现行官方文档复现，
仓库已改为记录实际 EP 名称/要求/页面日期，并明确 WDDM 驱动号与 Adrenalin 版本号不可比较。

---

## D. Physical Validation

命令（CLI 已按 `--help` 审核）：

    python -m runner.run_matrix --gfx gfx1151 --tier core --ep both --benchmark --with-negative \
      --run-id R5_GFX1151_20261119T1240 --winml-python .venv-winml\Scripts\python.exe \
      --export-python .venv-export\Scripts\python.exe

| Checkpoint | AMDGPU | DML | Task | p50/p95 (ms) | CPU nodes |
| --- | --- | --- | --- | --- | --- |
| YOLO26n Detect | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 9.86 / 11.36 | 0 |
| YOLO26n Segment | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 16.51 / 17.53 | 0 |
| YOLO26n Semantic | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 7.40 / 8.26 | 0 |
| YOLO26n Depth | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 6.36 / 6.59 | 0 |
| YOLO26n Classify | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 1.41 / 1.53 | 0 |
| YOLO26n Pose | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 11.75 / 14.00 | 0 |
| YOLO26n OBB | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 20.74 / 21.02 | 0 |
| YOLO11n Detect | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 11.72 / 12.35 | 0 |
| YOLOv8n Detect | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 11.62 / 12.65 | 0 |

- 严格审计：`PASS_FULL=9, BLOCKED=9, FAIL=0` → 退出码 **2**（不是 0/1）。
- `verify_receipts`：18 receipts + manifest（255 published entries）→ **ALL_OK**。
- 设备绑定：DML 标为 `EP_DEVICE_SELECTED_ORDINAL_MAPPING`（EP 不暴露 LUID，映射假设显式记录）。
- 多输入：引用同主机 hardening 运行的 `MULTI_INPUT_TASK_ORACLE.json`（7 任务 × 4 变体 = 28/28）。
- 覆盖率：9/135 eligible，`full_pass_both=0/135`，不声明全目录或数据集级覆盖。

---

## E. Performance

| 指标 | 值 | 绑定 |
| --- | --- | --- |
| Tier A warm p50/p95 | 见上表（1.41–20.74 ms） | 每 receipt 绑定 onnx_sha256 + input tensor_sha256 |
| 15 分钟 DML soak | 181,112 runs，p50 **2.7882 ms**，DML nodes=1、CPU nodes=0、errors=[] | `GFX1151_R5_SOAK_DML_20261119T1340` |
| 3 次独立重复（Pose，hash 绑定） | `BENCH_DML_yolo26n-pose.json`，`gpu_attested_all_reps=true` | `GFX1151_HARDENING_20261119T095500Z` |
| Compile / First run | `NOT_MEASURED`（未单独拆分） | — |
| End-to-End（含前后处理） | `NOT_MEASURED` | — |
| 温度 / 功耗 | `NOT_MEASURED`（APU 上 ADL 只暴露时钟） | — |
| PR #8 Pose 2.56 ms | `UNBOUND_LEGACY_OUTLIER`，不作加速结论 | — |

---

## F. Engineering Hardening

| 修复 | Commit | 验证 |
| --- | --- | --- |
| 退出码分类（0/1/2）+ PIPELINE_EXIT/MATRIX_EXIT | `ff1499e`（PR #9） | 7 项集成测试（真实子进程 rc=2/7、main() 四路径） |
| Harness 范围含 configs+schemas；未跟踪即 dirty | 同上 | 17 项篡改矩阵（阈值/Schema/Python/ONNX/输入/EP/DID/伪造 PASS/多/少/HEAD/dirty） |
| 运行环境绑定（python/ORT/WinML/providers） | 同上 | `ENVIRONMENT_CHANGED` / `UNBOUND` / `UNVERIFIED` / `RECEIPT_ENVIRONMENT_MISMATCH` |
| 证据字节精确（`reports/** -text`） | 同上 | CRLF fingerprint 恒 dirty 问题消失 |
| 只读探测器五态 + HRESULT 分离 | 同上 | 9 项状态机测试 + 真机 CATALOG_EMPTY |
| 官方文档校正（R3-D） | `ed60ed4` | `docs/AMDGPU_EP_PROVISIONING.md` 第 2 节 + 探测器 `ep_reference` |
| Owner 授权例外（范围极窄） | `ed60ed4` | `AGENTS.md` 新章节 |
| 统一 Runtime 四策略 + 证据门禁 + 子进程隔离 | `8ffd7c6`（PR #10） | 16 项测试 + 真机 GPU_FULL_DML |

测试基线不降：243 → 292 passed，1 skipped 保持不变（该 skip 是既有的环境相关跳过）。

---

## G. Three-Platform Matrix

| Platform | AMDGPU | DML | Unified Runtime | 证据 / 阻塞 |
| --- | --- | --- | --- | --- |
| gfx1151 / 8060S | BLOCKED_EP | 9/9 Physical PASS | Physical GPU_FULL_DML | `reports/gfx1151/R5_GFX1151_20261119T1240`、`reports/mission_r6/GFX1151_R6_CANARY_20261119T1335` |
| gfx1100 / 7900 XTX | PENDING_PHYSICAL_HOST | PENDING_PHYSICAL_HOST | PENDING_PHYSICAL_HOST | 交接：`docs/cross_host/GFX1100_HANDOFF.md` |
| gfx1201 / 9070 XT | PENDING_PHYSICAL_HOST | PENDING_PHYSICAL_HOST | PENDING_PHYSICAL_HOST | 交接：`docs/cross_host/GFX1201_HANDOFF.md` |

跨架构合成测试只证明「不会错选设备」，不构成物理验证；本机没有、也不会产生 gfx1100/gfx1201 的 PASS。

---

## H. Remaining Gaps

| 优先级 | 问题 | Root cause | Owner | Next action | 关闭条件 |
| --- | --- | --- | --- | --- | --- |
| **P1** | AMDGPU EP 不可用 | 本机 Windows ML Catalog 不提供任何 EP；无官方签名 2.x AMD GPU EP 包；官方文档当前只列 MIGraphX/VitisAI | Microsoft/AMD + repo owner | 等 Windows Update D-week / 官方 EP 包；出现 `NotPresent` 条目后走 `ensure_ready` + `register_execution_provider_library` | 探测器 ≥ `EP_REGISTERED` 且 canary 0 CPU 节点 |
| **P1** | gfx1100/gfx1201 无真机证据 | 硬件不在本机 | 跨主机验证者 | 按交接文档执行并回填 Issue #1 | 两台主机各自 `verify_receipts ALL_OK` |
| **P2** | unified predict 未接 PT task oracle | 任务 oracle 仍在 `runner.run_matrix` | repo | 把 PT oracle 接入统一路径 | `task_verified` 可由独立 PT 指标为真 |
| **P2** | E2E / 温度 / 功耗未测 | 缺少前后处理计时与 APU 传感器 | repo | 增加端到端 harness 与可选遥测 | 有绑定输入 SHA 的 E2E 数据 |
| **P2** | unified 子进程超时后可能有孙进程 | 父进程 kill 只保证直接子进程 | repo | 进程组/Job Object 回收 | 超时后无残留进程 |

---

## I. Release Readiness

**可以对外声明**：

- Windows 上 DirectML 路径在 Radeon 8060S 上完成了 9 个 Tier A checkpoint 的真实 GPU 验证，
  每例都有 profiler 节点、CPU-ORT parity、PT 任务 oracle 与可复算 manifest；
- 统一 Runtime API/CLI 在真机返回 `GPU_FULL_DML`，且 AMDGPU 的失败被显式披露；
- AMDGPU EP 的只读诊断可以区分五种状态并给出 HRESULT 与建议动作。

**不能对外声明**：

- 任何 AMDGPU GPU PASS；任何 gfx1100/gfx1201 物理 PASS；
- 数据集级精度、端到端延迟、温度/功耗；
- 「三平台 RC GO」（只能 CONDITIONAL_GO）。

Release 判决分层（`reports/mission_r3_r6/RELEASE_READINESS.json`）：
`UNIFIED_RUNTIME_DESIGN_GO` / `GFX1151_PHYSICAL_GO (DML)` /
`GFX1151_AMDGPU_BLOCKED_EP` / `GFX1100_PHYSICAL_PENDING` / `GFX1201_PHYSICAL_PENDING` /
`THREE_PLATFORM_RC_CONDITIONAL_GO`。

---

## J. Next Actions（按收益/依赖排序）

1. **保持 AMDGPU 探测自动化**：每次 Windows Update 后跑一次 `runner.probe_amdgpu_ep`；
   Catalog 一旦出现 AMD GPU EP，立刻按 R4 第 7 节链路注册并跑 canary，再执行 R5 的 9×2。
2. **两台跨主机真机验收**：按 `docs/cross_host/GFX1100_HANDOFF.md`、
   `docs/cross_host/GFX1201_HANDOFF.md` 执行；这是 THREE_PLATFORM_RC_GO 的唯一路径。
3. **把 task oracle 接进 unified predict**：让 `task_verified` 能独立为真（P2，但影响 API 完整性）。
4. **进程组超时回收**：统一执行器的超时要能清理孙进程（P2，长时间运行时重要）。
5. **E2E 与遥测**：补充含前后处理的计时与 APU 可用的功率/温度来源（P2）。

---

## 附：证据索引

| 文件 | 内容 |
| --- | --- |
| `reports/mission_r3_r6/BASELINE_AUDIT.json` | 起始基线（git/OS/GPU/驱动/Python/EP/MSIX） |
| `reports/mission_r4/PRE_PROVISION_SNAPSHOT.json` | A1 前后快照与回滚 |
| `reports/mission_r4/PROBE_AFTER_A1.json` | 五态探测、HRESULT、依赖矩阵 |
| `reports/gfx1151/R5_GFX1151_20261119T1240/` | 18 receipts、contract、coverage、manifest（ALL_OK） |
| `reports/gfx1151/GFX1151_R5_SOAK_DML_20261119T1340/` | 15 分钟 soak |
| `reports/mission_r6/GFX1151_R6_CANARY_20261119T1335/` | 统一 Runtime 真机 canary |
| `reports/mission_r3_r6/EXECUTION_LEDGER.json` | 分阶段执行台账 |
| `reports/mission_r3_r6/RELEASE_READINESS.json` | 分层判决与缺口 |
