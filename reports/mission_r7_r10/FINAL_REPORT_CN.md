# Mission R7–R10 最终工程报告

日期：2026-11-19（Asia/Shanghai）
执行主机：Windows 11 25H2 build 26200.9974 / AMD Ryzen AI MAX+ PRO 395 / Radeon 8060S
（gfx1151，DID `0x1586`，LUID `0x00000000:0x000154E4`，驱动 32.0.22018.5）
基线 main：`0c844da6727f5f7c43d27232b6e3eef9855a69bb`（= PR #11 merge commit）
分支：`mission/r7-r10-gfx1151-product-hardening`

---

## A. Executive Verdict

| Mission | 判决 |
| --- | --- |
| R7 | **R7_GFX1151_PRODUCT_HARDENING_GO** |
| R8 | **GFX1100_CROSS_HOST_HARNESS_READY** |
| R9 | **GFX1201_CROSS_HOST_HARNESS_READY** |
| R10 | **AMDGPU_EP_ESCALATION_READY** |

分层状态：

    GFX1151_DML_PHYSICAL_GO
    GFX1151_AMDGPU_BLOCKED_EP
    GFX1100_PHYSICAL_PENDING
    GFX1201_PHYSICAL_PENDING
    THREE_PLATFORM_RC_CONDITIONAL_GO

**没有任何 gfx1100 / gfx1201 物理 PASS。** 两个 Cross-Host Harness Ready 只表示
脚本、门禁、合成测试与 handoff 就绪，不构成物理结论。

机器可读版本：`reports/mission_r7_r10/RELEASE_READINESS.json`
执行台账：`reports/mission_r7_r10/EXECUTION_LEDGER.json`
基线快照：`reports/mission_r7_r10/BASELINE_AUDIT.json`

---

## B. Exact Git State

| 项 | 值 |
| --- | --- |
| 基线 main | `0c844da6727f5f7c43d27232b6e3eef9855a69bb` |
| 分支 | `mission/r7-r10-gfx1151-product-hardening` |
| PR #6 | OPEN（Draft，保留为设计参考，未合并） |
| PR #8 / #9 / #10 / #11 | 全部 MERGED（`d5ea312` / `bb4dc5a` / `444f248` / `0c844da`） |
| 起始工作树 | 无已跟踪文件修改 |
| 交付方式 | 两个独立 PR（R7 产品加固 / R8–R10 就绪），Draft → 自审 → Ready → 合并 |

主机与运行时（`BASELINE_AUDIT.json` 实采）：

    OS        Windows 11 Pro 25H2, 10.0.26200, build 26200.9974
    GPU       AMD Radeon(TM) 8060S Graphics, PCI\VEN_1002&DEV_1586
    DXGI LUID 0x00000000:0x000154E4（boot-local，仅作佐证）
    Driver    32.0.22018.5 (2025-09-17)
    ORT       1.25.2 (onnxruntime-windowsml), DmlExecutionProvider + CPUExecutionProvider
    WinML     windowsml 2.5.96
    AMDGPU EP CATALOG_EMPTY -> BLOCKED_EXTERNAL（本轮未改变）

---

## C. R7 代码改进

### C.1 新增模块

| 文件 | 作用 |
| --- | --- |
| `src/radeon_ort/win_job.py` | Windows Job Object 进程树生命周期（纯 ctypes） |
| `src/radeon_ort/task_oracle_runner.py` | 独立 PT Task Oracle 编排与证据绑定 |
| `schemas/unified_run.schema.json` | RunResult JSON Schema（draft-07） |
| `runner/unified_task_matrix.py` | 7 任务真机验证矩阵 |
| `runner/bench_e2e.py` | 真实图像分阶段 E2E benchmark |
| `runner/stability.py` | 有界稳定性 / 超时 / 恢复证据 |
| `runner/job_fault_evidence.py` | 8 场景进程树故障注入证据 |

### C.2 关键修改

* `unified_runtime.py`：新增 `verify_task / pt_reference / export_python / task_params /
  cpu_reference / cancel_event / allow_reuse_run_id / worker_module`；verifier 改为
  三态判定并复算 receipt 字节、模型/输入哈希、attempt 归属；新增分层 `readiness`。
* `unified_executors.py`：每 attempt 独立目录 + 内容寻址输入 + Job Object +
  attempt_id 绑定 + 原子写 + 部分产物标注 + 相对路径（receipt 不含主机路径）。
* `runner/_ep_worker.py`：校验 `expected_feed_sha256`、echo `attempt_id`、原子写 fragment。
* `runner/unified_predict.py`：`--verify-task / --pt-reference / --cpu-reference` 等，
  新增退出码 4（结果采纳但任务未通过）。

### C.3 根因与为什么这样设计

1. **任务正确性无法由数值近似推出。** GPU 与 CPU 可以数值一致却同时给出错误任务结果，
   因此任务正确性必须由**另一个模型**（PT）在**同一输入字节**上独立判定。
2. **`subprocess.run(timeout=)` 只回收直接子进程。** PT Oracle 会再起进程，超时后
   孙进程成为孤儿；Job Object 的 `KILL_ON_JOB_CLOSE` 才能保证整棵树回收。
3. **共享工作目录是证据污染的根源。** 复用 `inputs.npz` / `out_<ep>.npz` 会让上一次
   的产物冒充这一次的结果；内容寻址 + 每 attempt 私有目录 + attempt_id 是唯一可靠解法。
4. **生产者自报的 label 不是证据。** verifier 必须能独立重算，否则 `task_verified`
   只是一句口号。

---

## D. Oracle 正确性

真实结果（`reports/mission_r7/unified_task_matrix.json`，DML，gfx1151）：

    runtime_status          = GPU_FULL_DML
    gpu_full                = true
    task_verified           = true
    task_parity.status      = PASS_TASK
    target_ep_profile_nodes = 1
    cpu_profile_nodes       = 0
    verifier                = RUNRESULT_VALID
    schema_check            = ok
    7/7 tasks PASS

逐任务指标：

| task | 关键指标 | 阈值 | 判定 |
| --- | --- | --- | --- |
| detect | P=1.00 R=1.00 IoU=0.9999994（5/5） | P,R≥0.90 | PASS_TASK |
| segment | P=1.00 R=1.00 maskIoU=1.0000 | ≥0.70 | PASS_TASK |
| semantic | pixel_agreement=1.0000 mIoU=1.0000 | ≥0.90/≥0.70 | PASS_TASK |
| depth | rel_MAE=3.5e-07（589,824 px） | ≤0.15 | PASS_TASK |
| classify | top1 命中 654，top5 5/5，drift=5.4e-07 | drift≤0.05 | PASS_TASK |
| pose | P=1.00 R=1.00 OKS=1.0000（4/4） | ≥0.80 | PASS_TASK |
| obb | P=1.00 R=1.00 rIoU=0.9999999，角差 3.5e-06°（15/15） | ≥0.70 | PASS_TASK |

**诚实边界**：`task_verified` 只说明「与 pinned PT 参考在预先声明的阈值内一致」，
不是数据集级精度，也不是「模型对所有输入都正确」。缺参考时返回
`TASK_ORACLE_BLOCKED`，`gpu_full` 保持原值，两者互不覆盖。

---

## E. Job Object 超时与进程树回收

`reports/mission_r7/JOB_FAULT_INJECTION.json`：**8/8 PASS**

| 场景 | 观察 |
| --- | --- |
| 正常退出 | rc=0，job_assigned=true，survivors=[] |
| 持续阻塞 | timed_out=true，terminated_by_job=true，无残留 |
| 存活孙进程 | 3 个 PID 记录，全部已退出 |
| 多层孙进程 | 5 个 PID 记录，全部已退出 |
| 部分输出后超时 | 部分文件保留并标注为失败产物 |
| Job 分配被拒 | 照常运行，job_assigned=false + job_error 如实记录 |
| 用户取消 | cancelled=true，rc=130，整棵树回收 |
| AMDGPU 超时后继续 DML | 记 TIMEOUT_2s，策略继续独立 DML，最终 GPU_FULL_DML |

**过程中修掉一个真实缺陷**：`CREATE_SUSPENDED` 后立刻做 Toolhelp 线程快照，
偶尔看不到刚创建的线程 → 子进程永远挂起，直到 900 s 超时。修复为
「有界重试快照 + `NtResumeProcess` 兜底」，并加入 15 次连续 spawn 的回归测试
（`test_spawn_resume_is_robust_under_repetition`）。这个 bug 是在生成本报告
证据时真实触发的，不是假想。

不误杀无关进程：Job 外的旁观进程在 Job 超时终止后仍存活（单测断言）。

---

## F. 真实 E2E Performance

20 张真实照片（Unsplash License，逐张哈希写入
`data/fixtures/bench_images/SOURCES.json`），3 个模型 × 3 个独立进程 ×
（warmup 10 + 30 请求），DML：

| model | MODEL_ONLY p50 | PIPELINE_E2E p50 / p95 / p99 | SESSION_CREATE | FPS |
| --- | --- | --- | --- | --- |
| yolo26n detect | 5.07 ms | **18.42 / 18.65 / 18.67 ms** | 222–225 ms | 52.4–53.2 |
| yolo26n-seg | 6.16 ms | **21.01 / 21.11 / 21.12 ms** | 241–247 ms | 46.0–46.5 |
| yolo26n-pose | 5.15 ms | **17.24 / 17.47 / 17.49 ms** | 236–244 ms | 56.1–56.6 |

detect 分阶段 p50：
`load 0.34 | decode 7.06 | preprocess 4.14 | infer 5.07 | postprocess 1.60 ms`

**结论：这台 8060S 上瓶颈不在 GPU。** 输入通路（decode 7.1 + preprocess 4.1 ≈ 11.2 ms）
是 GPU 推理（5.1 ms）的两倍以上。要提升吞吐，先优化解码/预处理，而不是 EP。

**抖动如实记录**：更早一轮（warmup 5 + 20 请求）同一代码测到 detect e2e 32.8 ms /
model-only 9.58 ms。APU 的共享内存与功耗调度造成真实 run-to-run 差异；
因此只发布多进程 30 样本的数字，并把抖动写进报告。

温度 / 功耗 / GPU Activity：**NOT_MEASURED**（无受支持传感器路径），
绝不用固定时钟替代。

---

## G. 8060S Physical Regression

| 实验 | 结果 |
| --- | --- |
| 7 任务 Task Oracle（`unified_task_matrix`） | 7/7 PASS_TASK，每例 GPU_FULL_DML |
| 进程树故障注入（`job_fault_evidence`） | 8/8 PASS |
| E2E benchmark（`bench_e2e`） | 3/3 模型 × 3 进程 PASS |
| 稳定性（`stability`，8 次完整 predict） | 8/8 GPU_FULL_DML，输出哈希完全一致，句柄 +4（无泄漏） |
| 超时与恢复 | 1 s 超时 → BLOCKED、Job 终止、无残留；下一次调用立即恢复 GPU_FULL_DML |
| 有界 Soak（`runner.soak`，4 分钟 DML） | 80,038 次运行，p50 2.99 ms，DML nodes=1，无崩溃 |
| 对抗测试 | attempt 隔离 14 项、证据防伪 23 项、Schema 6 项、Job 11 项全部通过 |

---

## H. gfx1100 / gfx1201 Handoff Readiness

### H.1 修掉的 gfx1201 文档错误（R9-A）

| 错误 | 旧值 | 正确值 |
| --- | --- | --- |
| did 写错 | `0x7448 / 0x744C`（这是 gfx1100） | `0x7550 / 0x7551`（Navi 48 / gfx1201） |
| 负向自检自相矛盾 | 「请求 gfx1151 或 **gfx1201** 必须失败」 | 正向 gfx1201 必须通过；负向目标为 gfx1100 与 gfx1151 |
| 残留命名 | `GFX1201_R5_*` run_id | 改为新 run_id |
| 非法判决词 | `GFX1201_AMDGPU_AND_DML_CORE_GO` | `GFX1201_PHYSICAL_GO` 等合法词表 |

同时逐条用 `python -m runner.<mod> --help` 实核了两份 handoff 中引用的每个 CLI 参数
（例如 `run_matrix --tier` 只有 core/full；`multi_input_oracle` 没有
`--winml-python/--export-python`）。

### H.2 Cross-Host Validator

`runner/cross_host_validate.py`（+ `src/radeon_ort/cross_host.py`）统一
detect_host → check_dependencies → ep_probe → device_canary → tier_a →
multi_input_oracle → benchmarks → soak → evidence_freeze → final_verdict，
支持 `--dry-run`，并在任何 GPU 阶段前做硬门禁：

    本机（gfx1151）实测：
    --gfx gfx1100 --dry-run              -> exit 0，GFX1100_CROSS_HOST_HARNESS_READY
    --gfx gfx1201 --dry-run              -> exit 0，GFX1201_CROSS_HOST_HARNESS_READY
    --gfx gfx1100 --tier smoke           -> exit 34，WRONG_PHYSICAL_DEVICE，未创建任何 session
    --gfx gfx1151 --dry-run（正向对照）  -> exit 0，physical gate PASS

### H.3 合成测试（CPU-only，44 项）

模拟 gfx1100 DID `0x7448/0x744C` 与 gfx1201 DID `0x7550/0x7551` 正向解析；
错误架构请求被拒；多支持 GPU 场景 fail-closed；EP 缺失 / 错误 provider /
未授权 CPU fallback 被拒；跨架构缓存与 receipt 污染被拒；receipt 身份篡改被拒。

### H.4 边界

    GFX1100_PHYSICAL_PENDING
    GFX1201_PHYSICAL_PENDING

没有任何 gfx1100/gfx1201 的 `PASS_GPU_EXECUTED_FULL` receipt。
`0x7551` 从未硬件验证，表项本身不授予 PASS。

---

## I. AMDGPU EP Escalation

### I.1 官方文档真相（R10-A）

仓库此前（R3-D）声称「supported-execution-providers 页面里根本没有
`AMDGPUExecutionProvider`（0 次匹配）」。本轮独立抓取发现：那个结论读的是
**docs 分支的旧 markdown 源文件**（`ms.date: 04/28/2026`）。

| 旧判断 | 核实后的官方事实 | 来源 / 访问时间 / 内容哈希 |
| --- | --- | --- |
| 页面无 AMDGPU 行 | 2.x 表格有 `AMD GPU / AMDGPUExecutionProvider / AMD` | learn…/supported-execution-providers · 2026-11-19T13:52:29Z · `67b29c82…fdb013` |
| 无 2.5.96、无 24.12 | 最低 WinML **2.5.96**；Adrenalin **24.12.1+** | 同上 |
| MIGraphX 是唯一 AMD 行、驱动「恰好」25.10.13.09 | MIGraphX 标记 **Deprecated**，驱动 25.10.13.09 **或更高** | 同上 |
| 页面 `ms.date` 2026-04-28 | 已发布页最后更新 **2026-11-18**；只有 docs 分支是 04/28 | 同上 + main 分支 md |
| EP 通过 Windows Update D-week 分发 | ≥2.5.96 的 EP 通过 **Microsoft Store**；WU 只到 ≤2.4.89 | execution-provider-errors · 2026-11-07 |

17 个 URL 被抓取，每个记录 UTC + Asia/Shanghai 访问时间、body sha256、text sha256、
跨次抓取稳定性 → `reports/mission_r10/DOC_CITATIONS.json` + `_raw/` 原始内容。
历史判断没有被删除，而是标注为 `SUPERSEDED` 并保留原因。

### I.2 只读复测（R10-B）

    state            CATALOG_EMPTY
    verdict          BLOCKED_EP
    providers        0
    amd_devices      0
    stable_digest    c9f7ed5e5cd394aa49c3cb38a8e21e107938263166396a8e4bc2cb2b2f06b492
    exit code        2

两次运行 digest 完全一致；与 R4 的 `PROBE_AFTER_A1.json` 对比 `identical: true`。
`S_OK` 不等于可用：只有 `EP_REGISTERED` + 真实 GPU canary 才叫可用。
**R4 原始证据未修改，历史 BLOCKED 判决未改变。**

### I.3 最小复现与官方反馈（R10-C/D）

* `repro/windowsml_amdgpu_catalog_probe.py`：单文件、仅依赖官方 `windowsml` 包、
  不 import 本仓库、只读、幂等；本机连续三次 digest 均为
  `66baf7b6…c6150b`，exit 3。
* `docs/mission_r10/AMDGPU_EP_MINIMAL_REPRO.md`：问题定义、环境、步骤、期望 vs 观察、
  HRESULT、Catalog Count、为什么与开发者预期不一致、真实输出、脱敏、单主机复现范围、
  已排除因素 vs 未证实推断。
* `docs/mission_r10/UPSTREAM_ISSUE_DRAFT_EN.md`：英文 Issue 草稿，标题
  「Windows ML 2.x: Empty ExecutionProviderCatalog on Radeon 8060S (gfx1151)
  despite successful catalog initialization」，含 8 个章节与 6 个维护者问题，
  标记 **ESCALATION_DRAFT_READY**，**未发布**。

问题陈述严格限定为单主机事实，不声称所有 AMD GPU 受影响，也不预设
Windows ML / 驱动 / Windows Update 哪一方有 bug。

### I.4 可重复探测（R10-E）

Probe 幂等、只读、不安装、不改驱动、不产生假 PASS；记录时间戳 / SDK / OS / 驱动；
新增 `--diff` 与 `stable_digest`。文档给出可选 Task Scheduler 片段，
**本轮未创建任何计划任务**。

---

## J. Release Readiness & Next Actions

### J.1 发布门禁

| P0 | 状态 |
| --- | --- |
| 无跨架构假 PASS | PASS（无 gfx1100/gfx1201 物理声明） |
| 无 CPU 隐式业务 Fallback | PASS（`allow_cpu` 门禁 + `cpu_reference` 披露） |
| 无旧输入/输出污染 | PASS（14 项对抗测试） |
| 无伪造 Task Verified | PASS（23 项对抗测试） |
| 无超时残留进程 | PASS（8/8 故障注入，survivors=[]） |
| 无篡改/覆盖冻结 Receipt | PASS（字节哈希校验 + `RUN_ID_ALREADY_FROZEN`） |
| 无主机身份虚假证明 | PASS（DID 钉死 + `WRONG_PHYSICAL_DEVICE` 硬门禁） |

测试：全仓 **413 passed / 2 failed（仅 tests/hardware，AMDGPU EP 缺失，属预期）**；
CI 范围（unit+contracts+adversarial）**412 passed**（基线 293）。

### J.2 仍未关闭的缺口

1. **AMDGPU EP 仍是外部阻塞。** R7 的 GPU 证据全部来自 DirectML；`GPU_FULL`（AMDGPU
   路径）代码路径就绪但未被真机验证。
2. **gfx1100 / gfx1201 无物理证据。** 只能给 Harness Ready。
3. **E2E 语料是 20 张照片**，不是数据集级精度声明。
4. **温度/功耗/活动未测量**（无传感器路径），保持 `NOT_MEASURED`。
5. **后处理计时用 numpy 等价 NMS**，与 vendor decode 有绝对差异；正确性路径不受影响。
6. **PT Oracle 依赖 export venv 的 torch/ultralytics**；纯 WinML 部署机上会如实返回
   `TASK_ORACLE_BLOCKED`。
7. **run-to-run 抖动**在 APU 上真实存在（同一代码两次测量可差 2 倍），需以多进程
   多样本结果为准。

### J.3 下一步

1. RX 7900 XTX 主机执行
   `runner.cross_host_validate --gfx gfx1100 --tier core --policy gpu-first --run-id <NEW>`，
   用 `verify_receipts` 与 `CROSS_HOST_MATRIX.*` 复核后把 `GFX1100_PHYSICAL_PENDING`
   替换为真实判决。
2. RX 9070 XT 主机同流程执行 gfx1201。
3. 官方 Windows ML / 驱动发布后重跑只读 probe；只有 `EP_REGISTERED` + 真实 GPU
   canary 才能解除 `BLOCKED_EXTERNAL`。
4. 在确认目标仓库与发布权限后发布 R10 Issue 草稿。
5. 若目标是把 E2E 吞吐再提高，优先做输入通路优化（GPU 解码 / 复用解码结果），
   而不是继续调 EP。
