# Mission R9 最终报告：gfx1201 跨主机交接（Handoff Ready）

日期：2026-11-19（Asia/Shanghai）
基线：`mission/r7-r10-gfx1151-product-hardening`（HEAD `0c844da6727f5f7c43d27232b6e3eef9855a69bb`）
本机：Windows 11 Pro 25H2 build 26200.9974 / Ryzen AI MAX+ PRO 395 / Radeon 8060S /
DID `0x1586` / gfx1151 / ORT 1.25.2 / windowsml 2.5.96

判决：

- **GFX1201_CROSS_HOST_HARNESS_READY**（本机可交付的 harness/计划就绪标签）
- **GFX1201_PHYSICAL_PENDING**（没有任何 gfx1201 物理运行）
- 本报告**不**声明任何 gfx1201 物理 PASS。历史上 2026-11-18 的“gfx1201 验证”
  实际发生在 gfx1100 主机上，PR #5 已如实记录为
  `NO_GFX1201_HARDWARE_ON_HOST -> NOT_RUN`；本轮不改变这一点。

---

## 1. 交付了什么

| 交付物 | 作用 |
| --- | --- |
| `docs/cross_host/GFX1201_HANDOFF.md`（整篇重写，含 R9-A 修正记录） | 同事从 clone 到分层判决的完整步骤 |
| `runner/cross_host_validate.py`（新增） | 一条命令验收入口；10 个 stage + 硬门 |
| `src/radeon_ort/cross_host.py`（新增） | 物理门 + 跨架构 receipt/contract/run_id 守卫 |
| `tests/unit/test_cross_host_validate.py`（新增） | 入口/判决/命令构造的单元测试 |
| `tests/adversarial/test_cross_host_arch_boundaries.py`（新增） | 跨架构 fail-closed 合成边界测试 |
| `docs/mission_r9/R9_GFX1201_HANDOFF_READY_CN.md`（本文件） | 结果与证据 |

没有修改 owner 清单之外的文件；没有运行任何 git 命令；没有提交。

---

## 2. R9-A：修掉的已知 bug（精确到行）

`docs/cross_host/GFX1201_HANDOFF.md` 的旧版本是从 gfx1100 文档复制的，
包含以下**事实错误**：

### 2.1 硬件身份预检 DID（最严重）

旧文本（错误）：

```
通过标准：`VERIFIED_GFX1201`；日志里 DID = 0x7448/0x744C；...
```

问题：`0x7448/0x744C` 是 **Navi 31 / gfx1100** 的 DID，不是 Navi 48 / gfx1201。
如果同事按旧文档核对，真实的 gfx1201 主机会被误判为“不对”，
而一台 gfx1100 机器的 DID 反而“通过”文本核对（虽然代码层 `resolve_target_adapter`
仍会拒绝，但文档会把人带向错误结论）。

修正后：

```
通过标准：`VERIFIED_GFX1201`；DID = 0x7550/0x7551；LUID 有值；
请求 gfx1100（0x7448/0x744C）或 gfx1151（0x1586）必须失败。
```

### 2.2 负向自检目标自相矛盾

旧文本（错误）：

```
请求 gfx1151 或 gfx1201 必须返回 WRONG_OR_AMBIGUOUS_DEVICE
```

问题：把 **gfx1201 自己**列进了负向目标。真实 gfx1201 主机当然应该通过正向 DID 检查，
不可能同时“必须失败”。这是 R9-A 的核心逻辑错误。

修正后：负向目标是 **gfx1100（0x7448/0x744C）** 与 **gfx1151（0x1586）**；
真实 gfx1201 必须 PASS 正向检查。

### 2.3 其它复制粘贴残留

| 位置 | 旧内容 | 新内容 |
| --- | --- | --- |
| run_id 前缀 | `GFX1201_R5_<UTC>` | `GFX1201_R8_<UTC>` / `GFX1201_CROSSHOST_<UTC>` |
| 最终判决 | `GFX1201_AMDGPU_AND_DML_CORE_GO` | `GFX1201_PHYSICAL_GO` |
| 报告路径 | 混用 gfx1100 示例 | 统一 `reports\gfx1201\<run_id>\` |
| 预检输出 label | 未明确 | `VERIFIED_GFX1201` |
| 历史 DID | 未提 `0x7480` 的错误 | 明确 `0x7480` 不是 Navi 48 |

### 2.4 CLI flag 审计（硬要求）

旧文档里的命令全部重新用
`python -m runner.<mod> --help` 核对。核对后修正：

- `run_matrix --tier` 只接受 `core/full`；旧文档的“smoke”不是合法 tier，
  应写成 `--tier core --limit 1`；
- `multi_input_oracle` 没有 `--winml-python/--export-python`，它固定使用仓库根的
  `.venv-winml` / `.venv-export`，文档必须要求保持这两个 venv 名称；
- `probe_amdgpu_ep` 没有 `--run-id`；
- `redact_evidence` 的 `--run-dir` 可重复（`action="append"`），`--check` 存在；
- `resume_matrix` 接受 `--gfx`（`--host` 的别名）与 `--tier`/`--ep`；
- `unified_canary` 的 `--ep {amdgpu,dml}`、`--gfx`、`--out` 均存在。

---

## 3. 一条命令入口与 fail-closed 设计（R9-B / R8-C 共用）

`runner/cross_host_validate.py`：

- 10 个 stage：`detect_host -> check_dependencies -> ep_probe -> device_canary
  -> tier_a -> multi_input_oracle -> benchmarks -> soak -> evidence_freeze
  -> final_verdict`，每个 `PASS/FAIL/BLOCKED/NOT_RUN/SKIPPED`。
- **硬门**在任何 GPU stage 之前：实时 DXGI 推导 gfx 必须等于 `--gfx`；
  否则 `WRONG_PHYSICAL_DEVICE`、exit 34、**不创建** `reports\gfx1201\`。
- `--dry-run` 只验计划（winml/export 两个解释器依赖 + 13 个模块 `--help` + 模型/阈值/schema/身份），
  打印完整命令，不建 session；计划有效即 exit 0。
- 所有 GPU stage 复用 `python -m runner.X` 子进程，不复制逻辑。
- 证据冻结顺序：`redact -> finalize -> compatibility -> CROSS_HOST_MATRIX
  -> manifest -> verify -> redaction --check`。
- 判决词汇（精确）：`GFX1201_CROSS_HOST_HARNESS_READY` /
  `GFX1201_PHYSICAL_PENDING` / `GFX1201_PHYSICAL_GO` /
  `GFX1201_CONDITIONAL_GO` / `GFX1201_BLOCKED_EP` / `GFX1201_NO_GO`。
- 退出码：0 = `PHYSICAL_GO` 或有效 dry-run；1 = `NO_GO`/计划无效；
  2 = 非致命非 GO；34 = `WRONG_PHYSICAL_DEVICE`。

为什么 fail-closed：

1. 先验身份再碰 GPU，错误架构目录不会产生“像证据”的文件；
2. DID 是唯一权威（`0x7550/0x7551`），不认名字/LUID/`get_providers()`；
3. 多张受支持 GPU 或同 gfx 双卡 => `AMBIGUOUS_GFX_TARGET`，不猜；
4. EP 缺失 => `BLOCKED_EP`，永不隐式 CPU；
5. `RUN_CONTRACT.json` 绑定 gfx+DID+host_id+environment，换环境必须新 run_id；
6. receipt/contract/run_id 的跨架构污染分别有命名拒绝；
7. `0x7551` 虽在表中，但从未硬件验证；表项本身不授予 `PHYSICAL_BINDING_CONFIRMED`。

---

## 4. 实际运行的实验与真实输出

### 4.1 新增合成测试（CPU-only）

```
$ .venv-winml\Scripts\python.exe -m pytest tests/unit/test_cross_host_validate.py tests/adversarial/test_cross_host_arch_boundaries.py -q
44 passed in 0.39s
```

其中直接覆盖 R9-A 的用例：

- `test_authoritative_did_table_is_not_copied_across_gfx`：
  `gfx1201 == {0x7550, 0x7551}`，且与 `{0x7448, 0x744C}` 无交集；
- `test_simulated_gfx1201_did_resolves_positively`（`0x7550`、`0x7551` 两个参数）；
- `test_requesting_gfx1201_on_other_hosts_fails_closed`（gfx1151 / gfx1100 主机）；
- `test_two_supported_gfx_present_is_ambiguous_and_never_picked`；
- 跨架构 receipt/contract/run_id、伪造 gfx/DID/status、假 canary PASS、未授权 CPU 等。

### 4.2 全量测试

```
$ .venv-winml\Scripts\python.exe -m pytest -q
failed: tests/hardware/test_gpu_canary.py::test_amdgpu_canary_executes_on_gpu
failed: tests/hardware/test_gpu_canary.py::test_amdgpu_parity_against_cpu
2 failed, 412 passed（本次 86.40s；通过数随并行任务新增测试变化）
```

两个失败是预期的硬件失败（本机无 AMDGPU EP，
`ensure_amdgpu_registered()` 抛 `BLOCKED_EP: amdgpu-ep.dll not found`），
与任务书基线描述一致，不是本轮引入。

> 第一次全量运行中 `tests/adversarial/test_attempt_isolation.py` 的 2 个失败
> 单独运行 14 passed、第二次全量不再复现；判断为并发/时序抖动，
> 且不在本轮 owner 范围，未修改。

### 4.3 真实 CLI 输出（本机 gfx1151）

**gfx1201 dry-run：**

```
$ $env:PYTHONPATH="src"; .venv-winml\Scripts\python.exe -m runner.cross_host_validate --gfx gfx1201 --dry-run
[cross-host] DRY-RUN gfx=gfx1201 tier=core policy=gpu-first run_id=<RUN_ID>
[cross-host] mode: plan validation only - no ONNX session, no GPU work
[cross-host] plan checks: 38 OK, 0 FAIL
[cross-host] physical gate: WOULD REFUSE reason=WRONG_PHYSICAL_DEVICE (requested gfx1201 (DID ['0x7550', '0x7551']) but live DXGI-derived gfx is gfx1151 (observed DID ['0x1586']); refusing before any GPU work)
[cross-host] an executed run would stop before any GPU stage with exit code 34 and would write no evidence under %USERPROFILE%/Desktop/YOLO/ultralytics-windows-radeon-ep/reports/gfx1201/
...（逐条打印完整命令）...
[cross-host] final verdict: GFX1201_CROSS_HOST_HARNESS_READY
exit code: 0
```

**gfx1201 执行（应拒绝）：**

```
$ $env:PYTHONPATH="src"; .venv-winml\Scripts\python.exe -m runner.cross_host_validate --gfx gfx1201 --tier smoke --policy dml-only --run-id Y
[cross-host] HARD GATE REFUSED: WRONG_PHYSICAL_DEVICE - requested gfx1201 (DID ['0x7550', '0x7551']) but live DXGI-derived gfx is gfx1151 (observed DID ['0x1586']); refusing before any GPU work
[cross-host] no ONNX session, EP probe or GPU stage was started
[cross-host] no evidence directory was created under %USERPROFILE%/Desktop/YOLO/ultralytics-windows-radeon-ep/reports/gfx1201/
[cross-host] report: %USERPROFILE%/Desktop/YOLO/ultralytics-windows-radeon-ep/reports/_blocked/Y.json
[cross-host] final verdict: GFX1201_CROSS_HOST_HARNESS_READY
exit code: 34
```

### 4.4 合成 DID 结果（来自 4.1）

| 模拟主机 DID | 请求 gfx | gate |
| --- | --- | --- |
| `0x7550` | gfx1201 | PASS |
| `0x7551` | gfx1201 | PASS（代码路径；`0x7551` 仍硬件未验证） |
| `0x1586` | gfx1201 | BLOCKED / `WRONG_PHYSICAL_DEVICE` |
| `0x7448` | gfx1201 | BLOCKED / `WRONG_PHYSICAL_DEVICE` |
| `0x1586 + 0x7550` | gfx1201 | BLOCKED / `AMBIGUOUS_GFX_TARGET` |

这些是**代码路径**证据，不是 gfx1201 物理证据。

### 4.5 Lint

```
$ .venv-winml\Scripts\python.exe -m ruff check src/radeon_ort/cross_host.py runner/cross_host_validate.py tests/unit/test_cross_host_validate.py tests/adversarial/test_cross_host_arch_boundaries.py
All checks passed!
```

`ruff check src runner tests` 在最终一次运行时报告 209 条仓库既有告警（数量随并行任务变化），全部位于本轮
未修改的文件；本轮新增/修改的 4 个文件为 0 告警。

---

## 5. 仍然物理待定（PENDING）

以下内容在本机**没有**、也不可能有 gfx1201 证据：

- gfx1201 的 `device_canary` / `tier_a` / `multi_input_oracle` /
  `benchmarks` / `soak` 物理执行；
- gfx1201 的 `PASS_GPU_EXECUTED_FULL` receipt；
- gfx1201 的 AMDGPU EP 可用性（本机 `NOT_INSTALLED`）；
- `CROSS_HOST_VALIDATION.json` 的 `GFX1201_PHYSICAL_GO` / `CONDITIONAL_GO`；
- `0x7551` 变体的任何硬件验证。

> 模型资产提醒：`models/`、`*.pt`、`*.onnx` 均被 `.gitignore` 排除，
> 新 clone 不会带权重。物理主机必须先放入 Tier A 的 9 个 `.pt`，再用
> `runner.export_models --export-python .venv-export\Scripts\python.exe` 导出 ONNX；
> dry-run 的 `bench_onnx` / `tier_a_assets` 检查会在缺资产时直接 FAIL。

关闭条件（必须全部满足）：

1. 在 gfx1201 主机上 `preflight` 得到 `VERIFIED_GFX1201`，DID `0x7550/0x7551`；
2. 负向 gfx1100/gfx1151 均失败；
3. `cross_host_validate --gfx gfx1201 --tier core --policy gpu-first --run-id GFX1201_*`
   产出 `GFX1201_PHYSICAL_GO` 或（AMDGPU 外部阻塞时）`GFX1201_CONDITIONAL_GO`；
4. `verify_receipts` 返回 `ALL_OK`；
5. 新分支 Draft PR，含主机身份/run_id/9×2/benchmark SHA。

---

## 6. 分层判决词汇（与入口一致）

| 判决 | 含义 |
| --- | --- |
| `GFX1201_CROSS_HOST_HARNESS_READY` | 计划/harness 就绪；无物理声明（本机只能到此） |
| `GFX1201_PHYSICAL_PENDING` | 还没有完整物理运行 |
| `GFX1201_PHYSICAL_GO` | 所选范围全部 stage 通过（双 EP 全通过也用这个） |
| `GFX1201_CONDITIONAL_GO` | 一个 EP 完整，另一个有明确外部阻塞 |
| `GFX1201_BLOCKED_EP` | 没有可执行的目标 EP |
| `GFX1201_NO_GO` | 证据/任务失败 |

旧标签 `GFX1201_AMDGPU_AND_DML_CORE_GO` 已废弃；`0x7480` 不是 gfx1201。

---

## 7. 开发价值

- 修掉了一个会直接误导真机验收的 DID/负向自检 bug，并把它写成回归测试；
- 一条命令覆盖 10 个 stage，减少“抄错 flag / 漏跑 stage”的交接风险；
- 硬门让“在 gfx1151 上跑 gfx1201”变成 exit 34 的显式拒绝，而不是产生假证据；
- 跨架构 receipt/contract/run_id 守卫与 `RUN_CONTRACT` 环境绑定一起，
  阻断旧 gfx1100/gfx1151 证据的复用；
- `CROSS_HOST_MATRIX.*` 让 gfx1201 receipt 出现在 gfx1201 列；
- 文档 flag 全部 `--help` 实核，交接可执行性提高。

---

## 8. 下一步

1. gfx1201 负责人按 handoff 第 2–3 节建 venv、跑 preflight 与 dry-run；
2. 负向自检 gfx1100/gfx1151 必须失败，gfx1201 正向必须通过；
3. 若 AMDGPU `CATALOG_EMPTY`：保留 `EP_PROBE.json`，用 `--policy dml-only` 完成
   DML 回归，判决应为 `GFX1201_CONDITIONAL_GO` / `GFX1201_BLOCKED_EP`；
4. 若 AMDGPU 可用：跑 `--tier core --policy gpu-first`，目标 `GFX1201_PHYSICAL_GO`；
5. 回填 `docs/cross_host/GFX1201_RESULT_CN.md` 与 Issue #1；
6. 后续工程项（非本轮）：把 `build_compatibility_matrix` 固定列改为动态列，
   或 CI 直接消费 `CROSS_HOST_MATRIX.json`；
7. 根 agent 统一提交/推送；本轮不运行 git、不提交。

---

## 9. 文件清单（本轮）

新增：

- `runner/cross_host_validate.py`
- `src/radeon_ort/cross_host.py`
- `tests/unit/test_cross_host_validate.py`
- `tests/adversarial/test_cross_host_arch_boundaries.py`
- `docs/mission_r9/R9_GFX1201_HANDOFF_READY_CN.md`（本文件）

修改：

- `docs/cross_host/GFX1201_HANDOFF.md`（整篇重写，含 R9-A 修正记录）
