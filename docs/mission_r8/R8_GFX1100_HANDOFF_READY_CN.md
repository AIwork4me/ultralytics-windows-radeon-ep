# Mission R8 最终报告：gfx1100 跨主机交接（Handoff Ready）

日期：2026-11-19（Asia/Shanghai）
基线：`mission/r7-r10-gfx1151-product-hardening`（HEAD `0c844da6727f5f7c43d27232b6e3eef9855a69bb`）
本机：Windows 11 Pro 25H2 build 26200.9974 / Ryzen AI MAX+ PRO 395 / Radeon 8060S /
DID `0x1586` / gfx1151 / ORT 1.25.2 / windowsml 2.5.96

判决：

- **GFX1100_CROSS_HOST_HARNESS_READY**（本机可交付的 harness/计划就绪标签）
- **GFX1100_PHYSICAL_PENDING**（没有任何 gfx1100 物理运行）
- 本报告**不**声明任何 gfx1100 物理 PASS；AMDGPU EP 在本机仍为
  `NOT_INSTALLED / Catalog empty`，属 `BLOCKED_EXTERNAL`（与 R7 前一致）。

---

## 1. 交付了什么

| 交付物 | 作用 |
| --- | --- |
| `docs/cross_host/GFX1100_HANDOFF.md`（重写） | 同事从 clone 到分层判决的完整步骤 |
| `runner/cross_host_validate.py`（新增） | 一条命令验收入口；10 个 stage + 硬门 |
| `src/radeon_ort/cross_host.py`（新增） | 物理门 + 跨架构 receipt/contract/run_id 守卫 |
| `tests/unit/test_cross_host_validate.py`（新增） | 入口/判决/命令构造的单元测试 |
| `tests/adversarial/test_cross_host_arch_boundaries.py`（新增） | 跨架构 fail-closed 合成边界测试 |
| `docs/mission_r8/R8_GFX1100_HANDOFF_READY_CN.md`（本文件） | 结果与证据 |

没有修改任何 owner 清单之外的文件；没有运行任何 git 命令；没有提交。

---

## 2. 修复与新增内容

### 2.1 GFX1100 handoff 文档（R8-A）

旧文档的核心事实（DID `0x7448/0x744C`、`VERIFIED_GFX1100`、`reports\gfx1100`）
是正确的，但存在以下会误导同事的问题，已逐项重写：

| 问题 | 旧内容 | 新内容 |
| --- | --- | --- |
| run_id 前缀残留 | `GFX1100_R5_<UTC>` | `GFX1100_R8_<UTC>` / `GFX1100_CROSSHOST_<UTC>` |
| 最终判决不是合法词汇 | `GFX1100_AMDGPU_AND_DML_CORE_GO` | `GFX1100_PHYSICAL_GO`（词汇见第 6 节） |
| 缺少本机硬门说明 | 无 | `--dry-run` 在 gfx1151 上 `WOULD REFUSE`，执行必 exit 34 |
| 未说明 dirty worktree | 无 | `run_matrix` 的 dirty contract 不可 attest |
| 未说明跨架构隔离 | 只有零散提示 | 独立第 7 节：新 run_id / 环境绑定 / 拒绝理由 |
| 未说明 build_compatibility_matrix 固定列 | 直接让同事使用 | 明确它不是 gfx1100 的权威证据，改用 `CROSS_HOST_MATRIX.*` |

文档中的每条 CLI flag 都已用
`python -m runner.<mod> --help` 实际核对（preflight、probe_amdgpu_ep、
unified_canary、unified_predict、run_matrix、resume_matrix、multi_input_oracle、
bench_repeats、soak、negative_injection、redact_evidence、finalize_run、
verify_receipts、build_compatibility_matrix）。核对后修正了：
`run_matrix` 的 `--tier` 只接受 `core/full`（smoke 需 `--tier core --limit 1`）、
`multi_input_oracle` 没有 `--winml-python/--export-python`（它固定使用仓库根的
`.venv-winml` / `.venv-export`）、`probe_amdgpu_ep` 没有 `--run-id`。

### 2.2 一条命令验收入口（R8-C）

`runner/cross_host_validate.py` 的关键设计：

- **参数**：`--gfx`（必填）、`--tier {smoke,core,full}`、
  `--policy {gpu-first,amdgpu-only,dml-only}`、`--run-id`（`--dry-run` 除外必填）、
  `--dry-run`、`--report-root`、`--winml-python`、`--export-python`、
  `--timeout-s`、`--skip-soak`、`--json-out`；
  另有 `--catalog/--thresholds/--image/--obb-image/--models/--bench-*/--soak-minutes`。
- **10 个 stage**，每个输出 `PASS/FAIL/BLOCKED/NOT_RUN/SKIPPED`：
  `detect_host -> check_dependencies -> ep_probe -> device_canary -> tier_a
  -> multi_input_oracle -> benchmarks -> soak -> evidence_freeze -> final_verdict`。
- **硬门**：在 GPU stage 之前，用实时 DXGI/DID 推导 gfx；与 `--gfx` 不符时
  `detect_host`/`device_canary`/`tier_a`/... 全部 `BLOCKED`，reason
  `WRONG_PHYSICAL_DEVICE`，exit 34，且**不创建** `reports\<gfx>\` 目录。
- **dry-run**：验证解释器依赖、13 个 runner 模块的 `--help`、模型/阈值/schema、
  DXGI 读取、run_id/目录可写性，打印将执行的完整命令；不建 ONNX session、不做 GPU 工作；
  计划有效时 exit 0（即使物理门会拒绝）。
- **复用而非重写**：所有 GPU stage 都以 `python -m runner.X` 子进程执行，
  因此行为与 CI/手动命令一致。
- **证据冻结**：`redact -> finalize -> compatibility -> CROSS_HOST_MATRIX
  -> manifest -> verify -> redaction --check`。
- **脱敏**：JSON 报告中的绝对路径全部改写为 `%USERPROFILE%`；
  写盘前对序列化文本做 `scan_for_leaks`，仍有泄漏则抛错、不写文件。
- **判决词汇**（精确，不接受其它字符串）：
  `<GFX>_CROSS_HOST_HARNESS_READY` / `<GFX>_PHYSICAL_PENDING` /
  `<GFX>_PHYSICAL_GO` / `<GFX>_CONDITIONAL_GO` / `<GFX>_BLOCKED_EP` /
  `<GFX>_NO_GO`。
- **退出码**：0 = `PHYSICAL_GO`（或 dry-run 计划有效）；1 = `NO_GO`/计划无效；
  2 = 非致命非 GO；34 = `WRONG_PHYSICAL_DEVICE`。

### 2.3 跨架构守卫（R8-B 的代码侧）

`src/radeon_ort/cross_host.py` 提供：

- `gate_physical_target(adapters, requested_gfx)`：DID 绑定的物理门；
  `WRONG_PHYSICAL_DEVICE` / `AMBIGUOUS_GFX_TARGET` / `UNSUPPORTED_GFX_TARGET`。
- `receipt_architecture_problems(receipt, run_contract)`：
  `CROSS_ARCH_RECEIPT_REFUSED` / `RECEIPT_HOST_LABEL_FORGED` /
  `RECEIPT_DEVICE_ID_MISMATCH`。
- `run_contract_architecture_problems(contract, live_gate)`：
  `CROSS_ARCH_CONTRACT_REFUSED` / `CONTRACT_DEVICE_ID_MISMATCH`。
- `run_id_architecture_problem(run_id, gfx)`：拒绝含其它 gfx 标签的 run_id。
- `ep_readiness_refusal(policy, ...)`：缺 EP 时 `BLOCKED_EP`，永不 CPU 回退。
- `canary_stage_status(canary)`：拒绝“标签 PASS 但 profiler 0 节点”的假 GPU 结果。
- `build_cross_host_matrix(receipts)`：按 receipt 自身 `host.gfx` 聚合的跨架构视图，
  弥补 `build_compatibility_matrix` 固定 gfx1151 列的缺陷（未改后者，避免越权）。

---

## 3. 为什么是 fail-closed

1. **先验身份，再碰 GPU。** 硬门在任何 GPU stage 之前完成；不匹配就不建 session、
   不写 `reports/<gfx>`。这防止“错误架构目录里出现看起来像证据的文件”。
2. **DID 唯一权威。** 只认 `GFX_DEVICE_IDS`；gfx1100 只接受 `0x7448/0x744C`，
   不接受名字、LUID、`get_providers()` 或“任意 AMD”。
3. **歧义不猜。** 两张受支持 GPU、或同 gfx 两张不同卡，都是
   `AMBIGUOUS_GFX_TARGET`，`selected_device_id` 保持 `None`。
4. **没有 CPU 隐式回退。** `ep_readiness_refusal` 在 EP 缺失时返回 `BLOCKED_EP`；
   `unified_runtime` 的 `allow_cpu=True` 也只在显式 CPU policy 下合法。
5. **证据绑定环境。** `RUN_CONTRACT.json` 绑定 gfx+DID+host_id+environment；
   换 ORT/WindowsML/Python/OS 或换架构都必须新 run_id。
6. **跨架构拒绝有名字。** receipt/contract/run_id 三条路径分别给出
   `CROSS_ARCH_*` / `RECEIPT_DEVICE_ID_MISMATCH` / `RUN_ID_CROSS_ARCH_TAG`，
   不是笼统的“验证失败”。
7. **不可变 + 脱敏。** 失败也冻结；报告写盘前二次扫描泄漏。

---

## 4. 实际运行的实验与真实输出

### 4.1 新增合成测试（CPU-only）

```
$ .venv-winml\Scripts\python.exe -m pytest tests/unit/test_cross_host_validate.py tests/adversarial/test_cross_host_arch_boundaries.py -q
44 passed in 0.39s
```

覆盖：DID 表/正反向选择、gfx1100 与 gfx1201 的 4 个 DID、双 gfx 歧义、同 gfx 双卡歧义、
无 AMD/软件渲染、缺 EP/错误 provider/未授权 CPU、跨架构 receipt/contract/run_id 拒绝、
伪造 host label/DID/status、canary 假 PASS、报告脱敏。

### 4.2 全量测试

稳定复跑结果：

```
$ .venv-winml\Scripts\python.exe -m pytest -q
failed: tests/hardware/test_gpu_canary.py::test_amdgpu_canary_executes_on_gpu
failed: tests/hardware/test_gpu_canary.py::test_amdgpu_parity_against_cpu
2 failed, 412 passed（本次 86.40s；通过数随并行任务新增测试变化）
```

这两个失败是任务书已说明的**预期硬件失败**：本机没有 AMDGPU EP，
`ensure_amdgpu_registered()` 抛 `BLOCKED_EP: amdgpu-ep.dll not found`。
它们不是本轮改动引入的。

> 说明：第一次全量运行还出现过 2 个 `tests/adversarial/test_attempt_isolation.py`
> 失败（worker timeout 相关），单独运行该文件 14 passed，第二次全量运行也不再复现；
> 判断为并发/时序抖动，且该文件不在本轮 owner 范围，未做修改。

### 4.3 真实 CLI 输出（本机 gfx1151）

**gfx1100 dry-run：**

```
$ $env:PYTHONPATH="src"; .venv-winml\Scripts\python.exe -m runner.cross_host_validate --gfx gfx1100 --dry-run
[cross-host] DRY-RUN gfx=gfx1100 tier=core policy=gpu-first run_id=<RUN_ID>
[cross-host] mode: plan validation only - no ONNX session, no GPU work
[cross-host] plan checks: 38 OK, 0 FAIL
[cross-host] physical gate: WOULD REFUSE reason=WRONG_PHYSICAL_DEVICE (requested gfx1100 (DID ['0x7448', '0x744C']) but live DXGI-derived gfx is gfx1151 (observed DID ['0x1586']); refusing before any GPU work)
[cross-host] an executed run would stop before any GPU stage with exit code 34 and would write no evidence under %USERPROFILE%/Desktop/YOLO/ultralytics-windows-radeon-ep/reports/gfx1100/
...（逐条打印 ep_probe / unified_canary / run_matrix / multi_input_oracle / bench_repeats / soak / evidence_freeze 的完整命令）...
[cross-host] final verdict: GFX1100_CROSS_HOST_HARNESS_READY
exit code: 0
```

**gfx1100 执行（应拒绝）：**

```
$ $env:PYTHONPATH="src"; .venv-winml\Scripts\python.exe -m runner.cross_host_validate --gfx gfx1100 --tier smoke --policy dml-only --run-id X
[cross-host] HARD GATE REFUSED: WRONG_PHYSICAL_DEVICE - requested gfx1100 (DID ['0x7448', '0x744C']) but live DXGI-derived gfx is gfx1151 (observed DID ['0x1586']); refusing before any GPU work
[cross-host] no ONNX session, EP probe or GPU stage was started
[cross-host] no evidence directory was created under %USERPROFILE%/Desktop/YOLO/ultralytics-windows-radeon-ep/reports/gfx1100/
[cross-host] report: %USERPROFILE%/Desktop/YOLO/ultralytics-windows-radeon-ep/reports/_blocked/X.json
[cross-host] final verdict: GFX1100_CROSS_HOST_HARNESS_READY
exit code: 34
```

**gfx1151 dry-run（同机正向对照）：**

```
[cross-host] physical gate: PASS (gfx1151 verified by DID 0x1586; LUID is session corroboration only)
[cross-host] final verdict: GFX1151_CROSS_HOST_HARNESS_READY
exit code: 0
```

### 4.4 合成 DID 结果（来自 4.1 的测试）

| 模拟主机 DID | 请求 gfx | gate |
| --- | --- | --- |
| `0x7448` | gfx1100 | PASS |
| `0x744C` | gfx1100 | PASS |
| `0x1586` | gfx1100 | BLOCKED / `WRONG_PHYSICAL_DEVICE` |
| `0x7550` | gfx1100 | BLOCKED / `WRONG_PHYSICAL_DEVICE` |
| `0x1586 + 0x7550` | gfx1100 | BLOCKED / `AMBIGUOUS_GFX_TARGET` |

这些是**代码路径**证据，不是 gfx1100 物理证据。

### 4.5 Lint

```
$ .venv-winml\Scripts\python.exe -m ruff check src/radeon_ort/cross_host.py runner/cross_host_validate.py tests/unit/test_cross_host_validate.py tests/adversarial/test_cross_host_arch_boundaries.py
All checks passed!
```

`ruff check src runner tests` 在最终一次运行时报告 209 条仓库既有告警（数量随并行任务变化），全部位于本轮
未修改的文件（例如其他并行任务新增的 `tests/unit/test_win_job.py`）；本轮新增/修改的
4 个文件为 0 告警。

---

## 5. 仍然物理待定（PENDING）

以下内容在本机**没有**、也不可能有 gfx1100 证据：

- `device_canary` / `tier_a` / `multi_input_oracle` / `benchmarks` / `soak`
  的 gfx1100 物理执行；
- gfx1100 的 `PASS_GPU_EXECUTED_FULL` receipt；
- gfx1100 的 AMDGPU EP 可用性（本机 `NOT_INSTALLED`）；
- `CROSS_HOST_VALIDATION.json` 的 `GFX1100_PHYSICAL_GO` / `CONDITIONAL_GO`。

> 模型资产提醒：`models/`、`*.pt`、`*.onnx` 均被 `.gitignore` 排除，
> 新 clone 不会带权重。物理主机必须先放入 Tier A 的 9 个 `.pt`，再用
> `runner.export_models --export-python .venv-export\Scripts\python.exe` 导出 ONNX；
> dry-run 的 `bench_onnx` / `tier_a_assets` 检查会在缺资产时直接 FAIL。

关闭条件（必须全部满足）：

1. 在 gfx1100 主机上 `preflight` 得到 `VERIFIED_GFX1100`，DID `0x7448/0x744C`；
2. `cross_host_validate --gfx gfx1100 --tier core --policy gpu-first --run-id GFX1100_*`
   产出 `GFX1100_PHYSICAL_GO` 或（AMDGPU 外部阻塞时）`GFX1100_CONDITIONAL_GO`；
3. `verify_receipts` 返回 `ALL_OK`；
4. 新分支 Draft PR，含主机身份/run_id/9×2/benchmark SHA。

---

## 6. 分层判决词汇（与入口一致）

| 判决 | 含义 |
| --- | --- |
| `GFX1100_CROSS_HOST_HARNESS_READY` | 计划/harness 就绪；无物理声明（本机只能到此） |
| `GFX1100_PHYSICAL_PENDING` | 还没有完整物理运行 |
| `GFX1100_PHYSICAL_GO` | 所选范围全部 stage 通过（双 EP 全通过也用这个） |
| `GFX1100_CONDITIONAL_GO` | 一个 EP 完整，另一个有明确外部阻塞 |
| `GFX1100_BLOCKED_EP` | 没有可执行的目标 EP |
| `GFX1100_NO_GO` | 证据/任务失败 |

旧标签 `GFX1100_AMDGPU_AND_DML_CORE_GO` 已废弃。

---

## 7. 开发价值

- **一条命令**把 10 个 stage 串起来，减少“手动命令抄错/漏跑”的风险；
- **同一套命令**既给 CI 也给同事，子进程复用 runner 模块，行为可复现；
- **硬门 + 命名拒绝**让“在错误机器上跑错架构”变成显式、可审计的失败；
- **跨架构矩阵**让 gfx1100 receipt 出现在 gfx1100 列，而不是被固定列吞掉；
- **dry-run** 让同事在真正跑 GPU 之前验证解释器、模块、模型、阈值、schema 与身份；
- 文档中的 flag 经过 `--help` 实核，减少“文档命令跑不起来”的交接摩擦。

---

## 8. 下一步

1. gfx1100 负责人按 handoff 第 2–3 节建 venv、跑 preflight 与 dry-run；
2. 若 AMDGPU `CATALOG_EMPTY`：保留 `EP_PROBE.json`，用 `--policy dml-only` 完成
   DML 回归，判决应为 `GFX1100_CONDITIONAL_GO` / `GFX1100_BLOCKED_EP`；
3. 若 AMDGPU 可用：跑 `--tier core --policy gpu-first`，目标 `GFX1100_PHYSICAL_GO`；
4. 回填 `docs/cross_host/GFX1100_RESULT_CN.md` 与 Issue #1；
5. 后续工程项（非本轮）：把 `build_compatibility_matrix` 的固定列改为动态列，
   或在 CI 中直接消费 `CROSS_HOST_MATRIX.json`；
6. 根 agent 统一提交/推送；本轮不运行 git、不提交。

---

## 9. 文件清单（本轮）

新增：

- `runner/cross_host_validate.py`
- `src/radeon_ort/cross_host.py`
- `tests/unit/test_cross_host_validate.py`
- `tests/adversarial/test_cross_host_arch_boundaries.py`
- `docs/mission_r8/R8_GFX1100_HANDOFF_READY_CN.md`（本文件）

修改：

- `docs/cross_host/GFX1100_HANDOFF.md`（整篇重写）
