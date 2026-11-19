# Mission R5 最终报告（gfx1151 物理验收）

日期：2026-11-19（Asia/Shanghai）
主机：`host_53f62d2ac2c6` / Radeon 8060S（gfx1151, DID 0x1586, LUID 0x00000000:0x000154E4）
代码：`ed60ed4`（run contract 记录 `code.git_head_sha=ed60ed47cbfb...`）
run_id：`R5_GFX1151_20261119T1240`

判决：**GFX1151_CONDITIONAL_GO**

- DirectML：**9/9 Tier A 全量通过**（物理 GPU，严格回执验证 ALL_OK）
- AMDGPU：**BLOCKED_EP**（Catalog 为空，见 R4），不是本机可修复的代码问题

---

## 1. 运行前置（全部实测）

| 项 | 值 |
| --- | --- |
| 工作区 | clean（run contract 中 worktree_dirty=false） |
| Commit | `ed60ed4`（本地对象与远端 PR #9 分支 head 逐字节一致） |
| Harness hash | 记录于 `RUN_CONTRACT.json`（含 runner/、src/radeon_ort/、configs/、schemas/） |
| 环境绑定 | python 3.12.10 / ORT 1.25.2 (onnxruntime-windowsml) / windowsml 2.5.96 |
| 模型 | models/onnx/*.raw_b1_fp32_static.onnx（每个 receipt 记录 onnx_sha256） |
| 输入 | 真实图像 bus.jpg / dota_p1053.jpg，tensor_sha256 记录在 receipt |
| 设备 | DID 0x1586 + LUID 0x00000000:0x000154E4（同一物理 GPU 两个 LUID 别名，去重后选 index 0） |
| 阈值 | `thresholds_snapshot.json`（run 内冻结，事后不可改） |

## 2. Tier A × 双 EP（9 checkpoints × 2 EP = 18 例）

命令（CLI 已按 `--help` 审核）：

    .venv-winml\Scripts\python.exe -m runner.run_matrix --gfx gfx1151 --tier core --ep both --benchmark --with-negative --run-id R5_GFX1151_20261119T1240 --winml-python .venv-winml\Scripts\python.exe --export-python .venv-export\Scripts\python.exe

| Case | AMDGPU | DML | DML task | DML p50 / p95 (ms) | CPU nodes | max abs diff |
| --- | --- | --- | --- | --- | --- | --- |
| yolo26n Detect | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 9.86 / 11.36 | 0 | 1.9e-3 |
| yolo26n Segment | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 16.51 / 17.53 | 0 | 1.3e-3 |
| yolo26n Semantic | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 7.40 / 8.26 | 0 | 0.0 |
| yolo26n Depth | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 6.36 / 6.59 | 0 | 7.4e-5 |
| yolo26n Classify | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 1.41 / 1.53 | 0 | 0.0 |
| yolo26n Pose | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 11.75 / 14.00 | 0 | 4.5e-3 |
| yolo26n OBB | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 20.74 / 21.02 | 0 | 1.7e-3 |
| yolo11n Detect | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 11.72 / 12.35 | 0 | 2.9e-3 |
| yolov8n Detect | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK | 11.62 / 12.65 | 0 | 9.2e-4 |

每个 DML PASS 都满足：DmlExecutionProvider profiler 节点 = 1（>0）、CPU 节点 = 0、
by-name 数值 parity 全 allclose、PT-vs-ONNX 任务 oracle 通过、Contract/模型/输入哈希一致。

矩阵严格审计输出：

    PASS_FULL=9  PASS_PARTIAL=0  FAIL=0  BLOCKED=9  NOT_RUN=0
    [matrix] BLOCKED/NOT_RUN: incomplete evidence cannot PASS (exit 2)

即：**9/18 通过，9 个 AMDGPU 案例是已分类外部阻塞**；退出码 2 而不是 0/1，
符合 R3-A 的语义。

## 3. 独立复核与证据冻结

    .venv-winml\Scripts\python.exe -m runner.redact_evidence --run-dir reports\gfx1151\R5_GFX1151_20261119T1240 --check
    # scanned=258 rewritten=0 problems=0
    .venv-winml\Scripts\python.exe tools\r1_finalize.py reports\gfx1151\R5_GFX1151_20261119T1240
    .venv-winml\Scripts\python.exe -m runner.verify_receipts --run-dir reports\gfx1151\R5_GFX1151_20261119T1240
    # 18 receipts OK; manifest OK (353 entries); verdict: ALL_OK

覆盖率（`COVERAGE_SUMMARY.json`）：9/135 eligible，`full_pass_both=0/135`，
`full_coverage_claim_allowed=false` —— 不声明全目录或数据集级覆盖。

## 4. 多输入任务正确性（历史同代码族证据）

本 Mission 未重复执行 7×4 多输入矩阵（时间预算），引用同一 gfx1151 主机、
hardening 运行 `GFX1151_HARDENING_20261119T095500Z` 的
`MULTI_INPUT_TASK_ORACLE.json`：7 个任务 × 4 变体（orig/flip/bright/crop90）= **28/28 PASS**
（Detect P/R+IoU、Segment mask IoU、Semantic mIoU、Depth rel MAE、Classify Top-1/Score Drift、
Pose OKS、OBB Rotated IoU）。该文件同时声明：这不是数据集级精度，只是多输入 parity。

## 5. 性能与稳定性

- 上表的 p50/p95 来自 `run_matrix --benchmark`（30 个 warm 采样/例，timer=perf_counter_ns）；
  每个 receipt 都绑定 onnx_sha256 与 input tensor_sha256，可跨运行比较。
- 3 次独立重复的 Pose 基准（同代码族，hash 绑定）见
  `GFX1151_HARDENING_20261119T095500Z/BENCH_DML_yolo26n-pose.json`：
  `gpu_attested_all_reps=true`、onnx_sha256 `f5a5ce5f...`、input_sha256 `00324540...`。
- PR #8 的 Pose 2.56 ms 仍然只能作为 `UNBOUND_LEGACY_OUTLIER`，不作为加速结论。
- 15 分钟 DML Detect soak：**181,112 runs**，p50 **2.7882 ms**，DML profiler 节点 = 1、CPU 节点 = 0、
  `errors=[]`，心跳 30s、no-progress watchdog 180s、时长有界。证据：
  `reports/gfx1151/GFX1151_R5_SOAK_DML_20261119T1340/SOAK_dml__yolo26n_detect.json`。
- AMDGPU 性能：`NOT_MEASURED`（EP 未就绪，无法执行）。

## 6. Negative Injection

历史运行 `GFX1151_NEG_INJECT_20261119T080500Z` 与本 Mission 新增的对抗测试共同覆盖：
错误 DID、缺失 EP、错误 DML ordinal、CPU-only 冒充 GPU、静默 CPU 回退、错误 input/model SHA、
篡改 Receipt、不完整 task oracle、假设备绑定 —— 全部 fail-closed。
本轮新增 `tests/adversarial/test_contract_tamper_matrix.py`（17 项）把同样的原则固化到代码层。

## 7. 失败与限制登记

| 项 | 分类 | 说明 |
| --- | --- | --- |
| AMDGPU 9 例 | BLOCKED_EP | Catalog 不提供 EP；R4 已给出只读证据与升级路径 |
| 数据集级精度 | NOT_RUN | 超出本项目范围（多输入 parity 不等于数据集精度） |
| E2E 延迟 | NOT_MEASURED | 未做端到端（含前后处理）计时 |
| 温度/功耗 | NOT_MEASURED | APU 上 ADL 只暴露时钟，不暴露温度/功耗 |

## 8. 结论

- DirectML 在真实 Radeon 8060S 上完成 9/9 Tier A 验收，证据可独立复算（ALL_OK）。
- AMDGPU 仍是外部阻塞，**不声明任何 AMDGPU PASS**。
- 因此 gfx1151 的判决是 **GFX1151_CONDITIONAL_GO**：DML 侧可对外声明支持，
  AMDGPU 侧等待官方 EP 可用后按同一 run_id 规则重跑。
