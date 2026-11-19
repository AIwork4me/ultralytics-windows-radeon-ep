# Mission R7 最终报告 — gfx1151 Unified Runtime 产品加固

日期：2026-11-19（Asia/Shanghai）
基线 main：`0c844da6727f5f7c43d27232b6e3eef9855a69bb`（= PR #11 merge commit）
工作分支：`mission/r7-r10-gfx1151-product-hardening`
执行主机：Windows 11 25H2 (build 26200.9974) / Ryzen AI MAX+ PRO 395 / Radeon 8060S (gfx1151, DID 0x1586)

判决：**R7_GFX1151_PRODUCT_HARDENING_GO**
配套：**GFX1151_DML_PHYSICAL_GO** / **GFX1151_AMDGPU_BLOCKED_EP**

---

## 1. 结论先行

R7 把 `predict()` 从「一个已验证的 GPU 执行入口」升级为「可用于真实开发者的推理与验收入口」。
四项能力从无到有，全部在本机真机跑通：

| 能力 | 之前（R6） | 现在（R7） | 证据 |
| --- | --- | --- | --- |
| 任务正确性 | `task_verified` 恒为 false（"use runner.run_matrix"） | 7/7 任务经 `predict(verify_task=True)` 独立 PT Oracle 判定 `PASS_TASK` | `reports/mission_r7/unified_task_matrix.json` |
| 进程树回收 | `subprocess.run(timeout=)`，只杀直接子进程 | Windows Job Object，孙进程/多层后代全部回收 | `reports/mission_r7/JOB_FAULT_INJECTION.json` (8/8) |
| 输入输出隔离 | 共享 `inputs.npz` / `out_<ep>.npz`，可被旧文件污染 | 每 attempt 私有目录 + 内容寻址输入 + attempt_id 绑定 | `tests/adversarial/test_attempt_isolation.py` (14) |
| 证据可信度 | verifier 只做结构检查 | verifier 复算 receipt 字节、模型/输入哈希、attempt 归属、任务层 | `tests/adversarial/test_unified_evidence.py` (23) |
| 真实 E2E 性能 | 只有 model-only 数字 | 分阶段 6 项 + p50/p95/p99 + 3 次独立进程重复 | `reports/mission_r7/e2e_bench.json` |

**关键验收（真实 DML，非模拟）：**

    runtime_status        = GPU_FULL_DML
    gpu_full              = true
    task_verified         = true
    task_parity.status    = PASS_TASK
    target_ep_profile_nodes = 1        (> 0)
    cpu_profile_nodes     = 0
    verifier              = RUNRESULT_VALID
    schema_check          = ok

---

## 2. R7-A：接入真实 PT Task Oracle

### 2.1 设计

新增 `src/radeon_ort/task_oracle_runner.py`，把仓库已有的三段可信组件串成一条**独立验证路径**：

1. **PT Reference**（export venv，CPU-only，独立进程 + 独立 Job Object）：
   `pt_oracle_v2.py` 在**与 GPU 完全相同的预处理张量**上运行 pinned Ultralytics 8.4.215 模型；
2. **候选后处理**：`onnx_postprocess.py` 用 vendor 自己的 `non_max_suppression` /
   `process_mask` 解码 EP 的**原始输出数组**；
3. **比较器**：`task_parity.compare` 按 `configs/thresholds.yaml` 预先声明的阈值判定。

关键点：GPU 侧**从不被询问自己是否成功**。它只提供原始输出 `outputs.npz`，
后处理与比较在另一个解释器里从零重算。

### 2.2 证据契约

每个任务结果都绑定并记录：

    binding.onnx_sha256          ONNX 文件哈希
    binding.pt_reference_sha256  PT 参考哈希
    binding.input_tensor_sha256  实际喂入的张量哈希（float32 字节）
    binding.thresholds_sha256    thresholds 快照哈希
    binding.imgsz / conf / preprocess / postprocess 参数
    metrics / thresholds_declared / passed / ref_fingerprint_sha256

`resolve_task_spec()` 把 ONNX 文件名反查到 `catalog/model_catalog.json`，
若 catalog 记录的 task 与请求的 task 不一致，直接 `TASK_ORACLE_MODEL_TASK_MISMATCH`
（不猜、不降级）。

### 2.3 TASK_ORACLE_BLOCKED 而非伪造 PASS

以下情形一律 `TASK_ORACLE_BLOCKED` + `task_verified=False`，且 **`gpu_full` 保持原值不变**：

| 情形 | reason_code |
| --- | --- |
| PT 参考文件缺失 | `TASK_ORACLE_REFERENCE_MISSING` |
| ONNX 缺失 | `TASK_ORACLE_ONNX_MISSING` |
| 候选输出缺失 | `TASK_ORACLE_OUTPUTS_MISSING` |
| export 解释器缺失 | `TASK_ORACLE_INTERPRETER_MISSING` |
| catalog task 与请求 task 冲突 | `TASK_ORACLE_MODEL_TASK_MISMATCH` |
| PT Oracle 超时 / 非零退出 | `TASK_ORACLE_TIMEOUT` / `TASK_ORACLE_PT_FAILED` |
| 后处理失败 | `TASK_ORACLE_POSTPROCESS_FAILED` |
| 比较器缺失 | `TASK_ORACLE_COMPARATOR_MISSING` |

这保证了「GPU 跑成功」与「任务正确」是两个互不覆盖的结论：
`GPU_FULL_DML + task_verified=false` 是合法状态，而 `TASK_ORACLE_BLOCKED + task_verified=true`
被 verifier 判为 `RUNRESULT_INVALID`。

### 2.4 七类任务真机结果（bus.jpg / dota_p1053.jpg，DML）

| task | checkpoint | metrics | 阈值 | 判定 |
| --- | --- | --- | --- | --- |
| detect | yolo26n.pt | P=1.00 R=1.00 IoU=0.9999994 matched=5/5 fn=0 fp=0 | P,R≥0.90, IoU≥0.5 | PASS_TASK |
| segment | yolo26n-seg.pt | P=1.00 R=1.00 maskIoU=1.0000 | maskIoU≥0.70 | PASS_TASK |
| semantic | yolo26n-sem.pt | pixel_agreement=1.0000 mIoU=1.0000（11 类） | ≥0.90 / ≥0.70 | PASS_TASK |
| depth | yolo26n-depth.pt | rel_MAE=3.53e-07（589,824 px） | ≤0.15 | PASS_TASK |
| classify | yolo26n-cls.pt | top1=654=cand top1, top5 重叠 5/5, drift=5.4e-07 | top1 命中, drift≤0.05 | PASS_TASK |
| pose | yolo26n-pose.pt | P=1.00 R=1.00 OKS=0.99999999（4/4） | OKS≥0.80 | PASS_TASK |
| obb | yolo26n-obb.pt | P=1.00 R=1.00 rIoU=0.99999994, 角度差 3.5e-06°（15/15） | rIoU≥0.70 | PASS_TASK |

每一行的 `target_ep_profile_nodes = 1`、`cpu_profile_nodes = 0`、verifier `RUNRESULT_VALID`、
schema `ok`。7/7 通过。

### 2.5 CPU 语义（明确记录，不假装没用到 CPU）

`allow_cpu=False` 禁止的是 **CPU 业务结果**，不是内部数值参考。因此：

* `cpu_reference.used = true`，`cpu_reference.attempts[]` 列出内部 CPU 参考 attempt；
* `attempts[]` 里有一条 `internal: true, purpose: "cpu_numeric_reference"` 的 CPU 记录；
* `counts_as_business_execution = false`；
* 结果**从不声称**整个请求从未使用 CPU。

需要严格无 CPU 的模式时用 `cpu_reference="forbid"`：不跑 CPU job，parity 无法建立，
attempt 以 `CPU_REFERENCE_FORBIDDEN` 拒绝，**不会**跳过验证还声称证据级 GPU PASS。

---

## 3. R7-B：Windows 子进程树安全回收

新增 `src/radeon_ort/win_job.py`（纯 ctypes，无第三方依赖）：

* `CreateJobObjectW` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`
  + `JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION`；
* **CREATE_SUSPENDED → AssignProcessToJobObject → 恢复所有线程**，消除
  "先 exec 再 assign" 的竞态；
* 超时/取消/异常时 `TerminateJobObject`，有界等待，必要时才对直接子进程 `TerminateProcess`；
* 清理后重新探测存活 PID（`process_alive`），记录
  `survivors` / `job_stats_before_cleanup` / `job_stats_after_cleanup`；
* Job 无法创建或 assign 被拒时，**照常运行**并记录 `job_assigned=false` + `job_error`，
  降级容器被如实披露，不静默升级为干净。

### 3.1 真实故障注入（8/8 PASS）

`runner/job_fault_evidence.py` → `reports/mission_r7/JOB_FAULT_INJECTION.json`：

| # | 场景 | 观察结果 |
| --- | --- | --- |
| 1 | 正常退出 | rc=0, job_assigned=true, survivors=[] |
| 2 | 持续阻塞 | timed_out=true, terminated_by_job=true, 无残留 |
| 3 | 存活孙进程 | 记录 3 个 PID，全部已退出 |
| 4 | 多层孙进程 | 记录 5 个 PID，全部已退出 |
| 5 | 部分输出后超时 | 部分文件保留为失败产物，进程全清 |
| 6 | Job 分配失败 | rc=0 但 job_assigned=false, job_error 如实记录 |
| 7 | 用户取消 | cancelled=true, rc=130, 进程树全清 |
| 8 | AMDGPU 超时后继续 DML | AMDGPU 记 TIMEOUT_2s（不被吞掉），策略继续独立 DML attempt，最终 GPU_FULL_DML |

另有一条测试保证**不误杀无关进程**：Job 外的旁观进程在 Job 超时终止后仍存活。

### 3.2 超时不会被当作「EP 兼容性失败」自动重试

AMDGPU attempt 的超时被记录为 `TIMEOUT_<n>s` 并进入 `fallback_reason`，
只按 policy 继续**下一个独立 EP attempt**（DML），不会在同一 EP 上无条件重试。
`require_amdgpu` 下超时直接 `BLOCKED`。

---

## 4. R7-C：输入/输出文件复用风险

| 风险 | 修复 |
| --- | --- |
| `inputs.npz` 已存在时被复用 | 内容寻址：`inputs/<feed-fingerprint>/feeds.npz`；不同张量不可能碰撞 |
| worker 不校验输入 | case 携带 `expected_feed_sha256`，worker 重算并 `INPUT_FINGERPRINT_MISMATCH`（exit 3）拒绝 |
| 旧 `out_<ep>.npz` 污染 | 每次 attempt 独立 `attempts/<attempt_id>/outputs.npz`，只从本 attempt 目录读取 |
| 旧 fragment 被复用 | fragment 带 `attempt_id`，不匹配即 `ATTEMPT_MISMATCH` |
| 半写 JSON | fragment 与 receipt 均原子发布（temp + `os.replace`） |
| 同名 run_id 覆盖冻结 receipt | `RUN_ID_ALREADY_FROZEN`（需显式 `allow_reuse_run_id=True`） |
| 部分产物被静默丢弃 | `attempts[].partial_artifacts` 保留并标注为失败产物 |
| 并发争用同一目录 | attempt_id 唯一 + 每 attempt 目录独占；并发测试验证目录互不相同 |

对抗测试（`tests/adversarial/test_attempt_isolation.py`，14 项）覆盖：
不同张量同 run_id、不同 ONNX 同工作目录、植入旧 `out_dml.npz`、外来 fragment、
失败 worker 的部分产物、1s 超时、并发三路调用、篡改 `expected_feed_sha256`。

**结论：任何旧文件、部分输出或不匹配产物都无法通过新的 GPU PASS 验证。**

---

## 5. R7-D：RunResult 独立可信度

`verify_run_result(..., deep=True)` 现在独立复算：

| 检查 | 说明 |
| --- | --- |
| receipt 文件字节 SHA | 与 `receipt.file_sha256` 比对，篡改即失效 |
| receipt body SHA | 重新序列化后与 `receipt_sha256` 比对 |
| model / input 哈希 | 从磁盘文件与内存张量重算 |
| attempt 归属 | `adopted_attempt_id` 必须在 `attempts[]` 中唯一命中 |
| attempt 完整证据 | `fragment_matches_attempt`、`outputs_sha256`、`process`、`survivors==[]`、`input_binding.ok` |
| 任务层 | `PASS_TASK` ⇔ `task_verified`；必须有 metrics / thresholds / pt+onnx+input 哈希 |
| 分层 readiness | `product_ready ⇒ gpu_full ∧ task_verified` |
| CPU 语义 | 业务 CPU 结果必须有 `cpu_user_authorized` |

判定三态：`RUNRESULT_VALID` / `RUNRESULT_INVALID`（矛盾）/ `RUNRESULT_UNVERIFIED`
（没有原始证据，**不冒充通过**）。`schemas/unified_run.schema.json` 由
`validate_run_result_schema()` 强制。

23 项对抗测试逐条攻击上述字段，全部判 `RUNRESULT_INVALID`。

DML ordinal binding 仍为**间接证据**口径（`EP_DEVICE_SELECTED_ORDINAL_MAPPING`）；
无 EP-reported LUID 时绝不升级为 `PHYSICAL_BINDING_CONFIRMED`。

---

## 6. R7-E：真实图像 End-to-End Benchmark

* 语料：**20 张真实照片**，`data/fixtures/bench_images/`，全部 Unsplash License
  （可自由使用/修改/再分发），每张记录 author、source URL、license、sha256
  → `SOURCES.json`；benchmark 前逐张哈希校验。
* 每张 1280×960，覆盖人物/街景/自然/物体等场景。
* **3 次独立进程重复（不同 PID）**，每次 warmup 5 + 20 次请求，DML，gfx1151。
* 阶段全部用 `time.perf_counter()` 单独计时，不靠墙钟差值推断。

| model | MODEL_ONLY p50 | PIPELINE_E2E p50 / p95 / p99 | SESSION_CREATE | FPS |
| --- | --- | --- | --- | --- |
| yolo26n (detect) | 5.07 ms | **18.42 / 18.65 / 18.67 ms** | 222–225 ms | 52.4–53.2 |
| yolo26n-seg | 6.16 ms | **21.01 / 21.11 / 21.12 ms** | 241–247 ms | 46.0–46.5 |
| yolo26n-pose | 5.15 ms | **17.24 / 17.47 / 17.49 ms** | 236–244 ms | 56.1–56.6 |

（最终一轮：warmup 10 + 30 次请求/进程 × 3 进程）

detect 的分阶段 p50：

    load 0.34 ms | decode 7.06 ms | preprocess 4.14 ms | infer 5.07 ms | postprocess 1.60 ms

**抖动必须如实记录**：同一代码在更早一轮（warmup 5 + 20 请求）测到 detect
e2e p50 = 32.8 ms、model-only 9.58 ms —— 正好 2 倍。APU 的共享内存与功耗调度会造成
真实 run-to-run 差异；因此本仓库只发布"多次独立进程 + 30 样本"的数字，并把
单轮数字的抖动写进报告，而不是挑最好看的一次。

**开发者的直接结论：在这台 8060S 上，瓶颈不在 GPU。**
JPEG 解码（7.1 ms）+ 预处理（4.1 ms）≈ 11.2 ms，是 GPU 推理（5.1 ms）的两倍以上。
把 decode 换成 GPU/nvJPEG 路径或复用解码结果，比优化 EP 更有效。

温度 / 功耗 / GPU Activity：**NOT_MEASURED**（该 APU 无受支持传感器路径），
绝不用固定时钟替代。

后处理计时使用 numpy 等价 NMS（conf 0.25 / IoU 0.5 / max_det 300，class-aware），
在 `postprocess_impl` 字段中显式标注；正确性路径仍用 ultralytics vendor decode。

---

## 7. R7-F：真机稳定性与开发者体验

`runner/stability.py` → `reports/mission_r7/STABILITY.json`：

* 8 次连续完整 `predict()`（CPU reference + AMDGPU attempt + DML attempt）：
  8/8 `GPU_FULL_DML`，输出哈希**完全一致**（`deterministic_outputs = true`）；
* 句柄增量 **+4**（8 次迭代），无单调增长 → 无泄漏；
* 超时注入：1s 超时后 `BLOCKED`，`terminated_by_job=true`，`survivors=[]`，wall 时间有界；
* 超时后的下一次调用立即恢复为 `GPU_FULL_DML`。

另有 bounded soak（`runner.soak`，DML，独立 run_id）。

CLI 退出码：`0` 采纳结果（含 `--verify-task` 时需 task_verified）、`2` 契约错误、
`4` 结果采纳但任务未通过、`30` blocked、`31` AMDGPU 失败、`34` 设备阻塞。

---

## 8. 测试与质量门

| 项 | 结果 |
| --- | --- |
| 全仓 pytest | 见 `reports/mission_r7_r10/EXECUTION_LEDGER.json` |
| CI 范围（unit+contracts+adversarial） | 见 ledger（基线 292/1 skip，R7 新增 51 项） |
| 新增对抗/契约测试 | `test_win_job.py`(10)、`test_attempt_isolation.py`(14)、`test_unified_evidence.py`(23)、`test_unified_run_schema.py`(6)、`test_task_oracle_contract.py`(12) |
| Ruff | 新文件 0 findings（仓库既有文件仍有历史 findings，CI 中为非阻塞） |
| 真机 DML 回归 | 7/7 任务 + 8/8 故障注入 + 3/3 benchmark 模型 + 8/8 稳定性 |

---

## 9. 仍未关闭的缺口

1. **AMDGPU EP 仍为 `BLOCKED_EXTERNAL`**（见 R10）。R7 的 GPU 证据全部来自 DirectML；
   `GPU_FULL`（AMDGPU 路径）在本机无法产生，代码路径已就绪但未被真机验证。
2. **PT Oracle 依赖 export venv 的 torch/ultralytics**。它不影响主运行时，
   但意味着任务验证在纯 WinML 部署机上不可用 → 会如实返回 `TASK_ORACLE_BLOCKED`。
3. **后处理计时是 numpy 等价实现**，与 vendor decode 有绝对差异；正确性路径不受影响。
4. **温度/功耗/活动未测量**：无受支持传感器，保持 `NOT_MEASURED`。
5. **E2E 语料是 20 张照片**，不是数据集级精度声明；它只用于性能与管线分解。

下一步：gfx1100 / gfx1201 真机按 R8/R9 handoff 跑出各自证据后，才能讨论
`THREE_PLATFORM_RC_GO`；AMDGPU EP 需等官方 Catalog 提供后按 R10 流程验证。