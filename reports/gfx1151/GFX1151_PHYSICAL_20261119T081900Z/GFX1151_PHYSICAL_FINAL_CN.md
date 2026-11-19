# GFX1151 物理 GPU 全量验证 — Windows Radeon 8060S × Windows ML × AMDGPU/DML

- **Run ID**: `GFX1151_PHYSICAL_20261119T081900Z`
- **Preflight Run ID**: `GFX1151_PREFLIGHT_20261118T234053Z` (separate run, same host/session)
- **执行日期 (UTC)**: 2026-11-18T23:32Z – 2026-11-19T00:05Z
- **执行者代号**: `AIwork4me / clean-host-8060s-02`
- **仓库**: https://github.com/AIwork4me/ultralytics-windows-radeon-ep
- **Base SHA**: `d24f7377813cddcab744c14c82733e890b881234` (origin/main at clone)
- **Branch**: `mission/gfx1151-physical-full-validation`

> 本报告只汇总本机、本次 run 的真实证据。历史 `reports/gfx1151/G2_TIER_A_RUN2` 与
> `R1_*` 仅作对照，未复制任何 receipt、未修改任何历史证据。

---

## A. Executive Verdict

| Verdict | 结果 | 依据 |
|---|---|---|
| **GFX1151_PHYSICAL_VALIDATION_GO / CONDITIONAL_GO / NO_GO** | **CONDITIONAL_GO（仅 DML；双 EP 不成立）** | DML 9/9 Tier A 全 PASS；AMDGPU EP 在本机不存在 → 9/9 `BLOCKED_EP` |
| **GFX1151_FULL_CATALOG_COVERAGE** | **NO** | 135 eligible 中仅 9 个 checkpoint 实际执行；126 个 `NOT_RUN` |
| **AMDGPU_EP_VERDICT** | **BLOCKED_EP** | 无 `amdgpu-ep.dll`；WinML `find_all_providers()==[]`；未做任何 GPU 声明 |
| **DML_EP_VERDICT** | **GO** | 9/9 `PASS_GPU_EXECUTED_FULL`，DML nodes=1、CPU nodes=0、CPU parity、任务级 oracle 全通过 |
| **TASK_ORACLE_VERDICT** | **GO（DML，7 任务真实指标）** | Detect P/R、Seg mask IoU、Semantic mIoU、Depth rel-MAE、Cls Top-1、Pose OKS、OBB rotated IoU |
| **PERFORMANCE_STABILITY_VERDICT** | **GO（DML，受限条件）** | 3×独立 session 基准；15 min soak 146,842 runs、0 error |
| **EVIDENCE_AUDIT_VERDICT** | **PASS（见 §J 与 manifest）** | 18 receipts 全 strict-validate；manifest/hash/redaction；negative injection 5/5 |

**一句话结论**：在真实 Radeon 8060S / gfx1151 上，**DirectML** 已被严格证明能正确执行
7 种 YOLO 任务和 9 个 Tier A checkpoint；**AMDGPU EP 因本机缺少 Windows ML EP
包而完全未被验证**。因此本次不是双 EP 的 `GO`，更不是全 catalog 的 `GO`。

---

## B. Exact Git State

| 项 | 值 |
|---|---|
| Repository | `AIwork4me/ultralytics-windows-radeon-ep` |
| Base SHA | `d24f7377813cddcab744c14c82733e890b881234` |
| Harness code commit (receipts) | `82d2e4fba8c25debd336188b42d136cd43cc9a60` (`worktree_dirty=false`) |
| Branch | `mission/gfx1151-physical-full-validation` |
| Evidence commit | 本文件所在 commit（其 SHA 记录在 PR 与 Issue #7 评论） |
| Draft PR | 见 Issue #7 最终评论；目标 `AIwork4me/ultralytics-windows-radeon-ep:main` |
| Issue #7 | https://github.com/AIwork4me/ultralytics-windows-radeon-ep/issues/7 |
| CI | `windows-cpu-ci` + `secret-scan` 在 PR 精确 HEAD 上检查（CPU CI 不认证 GPU） |

---

## C. Host Fingerprint

| 项 | 实测值 |
|---|---|
| 主机 | HP ZBook Ultra G1a 14 inch Mobile Workstation PC |
| OS | Windows 11 专业版 25H2, build **26200.9974** |
| WDDM | **3.2** (dxdiag) |
| CPU | AMD Ryzen AI MAX+ PRO 395 w/ Radeon 8060S, 16C/32T |
| RAM | Win32 95.8 GB；dxdiag 131072 MB（共享内存分配差异，不冒充专用显存） |
| GPU | AMD Radeon(TM) 8060S Graphics，集成 GPU |
| PCI | Vendor **0x1002**, Device **0x1586**，`PCI\VEN_1002&DEV_1586&SUBSYS_8D01103C&REV_D1` |
| 驱动 | **32.0.22018.5** (2025-09-17) |
| DXGI LUID | `0x00000000:0x000154E4`（boot-local） |
| 专用/共享显存 | 0 MB dedicated / 78,459 MB shared（DXGI） |
| Python | 3.12.10 x64（python.org，非 Store） |
| ORT | onnxruntime-windowsml **1.25.2.202605110140** |
| Windows ML | wasdk Python projection 2.3.0；WinMLShared **1.2610.1050.0**；WindowsAppRuntime 2.6.5 / 2.7.0 |
| AMDGPU EP | **NOT PRESENT**（site-packages/WindowsApps 全盘无 `amdgpu-ep.dll`；EP catalog 0 providers） |
| DirectML | ORT 内置 `DmlExecutionProvider`；`System32/DirectML.dll` SHA256 `9FE59703…5313E9` |
| Ultralytics | **8.4.215** |
| PyTorch | 2.15.0 |
| Catalog SHA256 | `40aa1fa66dd7878d49bb79e48be3298a6ed02333e8bec847c25b2004c6a378d2` |
| Thresholds SHA256 | 见 `thresholds_snapshot.json` / `ENVIRONMENT_LOCK.json` |

**设备识别链**：WMI PnP（VEN_1002&DEV_1586）→ DXGI EnumAdapters1（vendor 0x1002,
deviceId 0x1586, description 含 8060S）→ DID-pinned 唯一候选 → 每进程 fresh DXGI
复核 → DML canary profile。未使用 `Discrete=True` 或 dedicated VRAM>0 作为门槛。

---

## D. Full Validation Matrix

### D.1 Catalog 分母（当前冻结 catalog，非 Issue 文本）

| 分类 | 数量 |
|---|---:|
| Catalog total | **142** |
| eligible | **135** |
| asset_blocked | 4 |
| license_restricted | 1 |
| not_applicable | 2 |
| Tier A | 9 |
| Tier B BASELINE_REQUIRED | 33 |
| RESOURCE_GATED | 81 |
| OPTIONAL_STRESS | 12 |
| **目标单位 (135 eligible × 2 EP)** | **270** |

> 注：Issue #7 文本写 “9+33+81+12”，与当前 catalog 实际计数一致（33/81/12），
> 但 Issue 正文另一处 “43 BASELINE_REQUIRED” 并不存在；本报告以 catalog 实测为准。

### D.2 本次实际执行矩阵（9 checkpoints × 2 EP = 18 cases）

| Checkpoint | Task | AMDGPU | DML | DML task |
|---|---|---|---|---|
| yolo26n.pt | detect | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK |
| yolo26n-seg.pt | segment | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK |
| yolo26n-sem.pt | semantic | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK |
| yolo26n-depth.pt | depth | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK |
| yolo26n-cls.pt | classify | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK |
| yolo26n-pose.pt | pose | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK |
| yolo26n-obb.pt | obb | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK |
| yolo11n.pt | detect | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK |
| yolov8n.pt | detect | BLOCKED_EP | PASS_GPU_EXECUTED_FULL | PASS_TASK |

### D.3 Per-EP 汇总（分母 = 135 eligible）

| EP | PASS_FULL | PASS_PARTIAL | FAIL | BLOCKED | NOT_RUN |
|---|---:|---:|---:|---:|---:|
| **AMDGPU** | 0 | 0 | 0 | **9** | **126** |
| **DML** | **9** | 0 | 0 | 0 | **126** |

**Catalog disposition**：142/142 均有 disposition（9 个有 DML 证据 + 9 个 AMDGPU
blocked 证据；4 asset-blocked、1 license、2 NA、126 eligible NOT_RUN）。
**Catalog Disposition Complete = YES；Physical GPU Full Coverage = NO（9/135 = 6.7%）**。

---

## E. Correctness（DML，Tier A）

所有 DML case：`provider_node_counts={"DmlExecutionProvider":1}`，CPU nodes=0，
parity 按输出名比较，阈值来自预先冻结的 `configs/thresholds.yaml`（atol/rtol 5e-3）。

| Task / Checkpoint | CPU parity max_abs | 任务级指标 | 门槛 | 结果 |
|---|---:|---|---|---|
| detect / yolo26n | 1.91e-3 | P=1.00, R=1.00, matched=5/5, IoU=0.999999 | P/R≥0.90, ≥1 | PASS |
| detect / yolo11n | 2.91e-3 | P=1.00, R=1.00, matched=5/5 | 同上 | PASS |
| detect / yolov8n | 9.16e-4 | P=1.00, R=1.00, matched=5/5 | 同上 | PASS |
| segment / yolo26n-seg | 1.31e-3 | P=1.00, R=1.00, **mask IoU=1.000000** | ≥0.70 | PASS |
| semantic / yolo26n-sem | **0** | **mIoU=1.000000**, pixel agreement=1.000000 | mIoU≥0.70, agree≥0.90 | PASS |
| depth / yolo26n-depth | 7.39e-5 | **rel MAE=3.53e-7**, RMSE=4.79e-6, valid=589,824 px | rel MAE≤0.15 | PASS |
| classify / yolo26n-cls | 4.77e-7 | Top-1 654 == 654, Top-5 overlap 5/5, drift 5.36e-7 | Top-1 match, drift≤0.05 | PASS |
| pose / yolo26n-pose | 4.53e-3 | P=1.00, R=1.00, **OKS=1.000000**, matched 4/4 | OKS≥0.80 | PASS |
| OBB / yolo26n-obb | 1.72e-3 | P=1.00, R=1.00, **rotated IoU=0.99999994**, angle Δ=3.5e-6° | rotated IoU≥0.70 | PASS |

**输入可复现性**：所有 case 使用同一张 `data/fixtures/bus.jpg`（OBB 用
`dota_p1053.jpg`），persisted tensor 的 SHA256 记录在 receipt 与 manifest；
CPU 参考与 DML 使用同一份 ONNX、同一份 tensor bytes、同一个输出名集合。

**任务 oracle 的真实性**（修复后的关键点）：

- 旧 `run_matrix --pt-oracle` 只运行 PT 并把 box 数写成 `PASS_TASK`（PT_APP smoke）。
- 本次使用 `r1_pipeline` + `pt_oracle_v2` + `onnx_postprocess` + `task_parity`：
  GPU 输出经 Ultralytics 8.4.215 原厂 NMS/process_mask 解码后，与 PT 在同一
  tensor 上的结果逐任务比较；PASS 由 metrics 与预声明阈值重算，不是标签。
- Semantic 的 `max_abs=0` 是 uint8 argmax 输出；CPU/DML 完全相同。

---

## F. Performance（DML，3 次独立 fresh session，30 samples/rep，warmup=5）

| Model | Session create (ms, 3 reps) | First run (ms) | Warm p50 (ms) | p95 | p99 | Throughput (img/s) |
|---|---|---|---:|---:|---:|---:|
| yolo26n detect | 695.9 / 569.2 / 559.5 | 8.88 / 8.69 / 9.55 | 11.763 / 11.799 / 11.887 | 13.95 / 15.09 / 12.79 | 14.64 / 18.00 / 13.18 | 81.7–85.1 |
| yolo26n-seg | 739.8 / 629.8 / 631.6 | 12.76 / 11.94 / 12.63 | 18.135 / 17.990 / 18.195 | 20.49 / 22.71 / 19.05 | 21.68 / 29.28 / 19.60 | 53.8–55.2 |
| yolo26n-pose | 727.2 / 222.4 / 216.6 | 9.12 / 3.94 / 3.82 | 2.560 / 2.704 / 2.662 | 10.52 / 2.79 / 2.80 | 15.94 / 2.91 / 2.83 | 254.2–373.2 |

- Timer: `time.perf_counter_ns`；session create 包含 ORT/DML 初始化，故 0.22–0.74 s。
- **同一 pose 模型在更早一次独立 benchmark 中为 10.07–10.97 ms，本次为 2.56–2.70 ms**：
  GPU 时钟/机器热状态导致约 4× 差异；两次都保留，不取“最好看”的一次。
- Seg rep1/2 的 p95 19–23 ms，p99 最高 29.28 ms；p50 稳定在 18.0–18.2 ms。
- 注：本表来自**最终 committed-code run** `081900Z`；更早 `074700Z` 的数字仅作离散度对照。
- **Pre/post/E2E 图像级延迟：NOT_MEASURED**（本机脚本未把 cv2 前处理/后处理纳入
  同一计时闭环）；只报告 session/run 级数据，不把脚本 wall-clock 当 kernel latency。
- 功耗、温度、专用显存：**NOT_MEASURED**（无经校准的计量器；共享内存不冒充 VRAM）。
- 后台负载：无受控电源策略/独占模式；数值仅代表本机当时状态。

---

## G. Stability / Soak（DML）

| Case | 时长 | Runs | Errors | p50 / p95 / p99 | nodes / CPU | 结果 |
|---|---:|---:|---:|---|---|---|
| yolo26n detect | 900.1 s | **146,842** | **0** | 2.97 / 12.41 / 13.09 ms | DML=1 / CPU=0 | COMPLETED |
| yolo26n-seg | 900.0 s | **85,059** | **0** | 14.28 / 18.69 / 19.79 ms (mean 10.56, std 6.23) | DML=1 / CPU=0 | COMPLETED |

- 30 s heartbeat、180 s no-progress watchdog 均生效；detect 全程 30 次 heartbeat 无中断。
- Seg 的 mean 10.56 ms 低于 p50 14.28 ms：分布长尾/双峰，报告保留 p50 与 mean 两者。
- 15 分钟内吞吐有波动，但无错误、无 hang、无驱动错误。
- **AMDGPU soak：BLOCKED_EP**（无 EP DLL），不是“稳定性失败”。
- 温度/功耗：NOT_MEASURED。

---

## H. Fail-Closed / Negative Injection

Run: `GFX1151_NEG_INJECT_20261119T080500Z` → `ALL_INJECTIONS_FAIL_CLOSED: true` (5/5)

| # | 注入 | 真实结果 | 判定 |
|---|---|---|---|
| 1 | 伪造 Intel 适配器请求 gfx1151 | `WRONG_OR_AMBIGUOUS_DEVICE: no non-software AMD adapter found` | 正确拒绝 |
| 2 | 不存在的 AMDGPU EP DLL | `Error loading ... amdgpu-ep.dll which is missing (126)` | 正确拒绝 |
| 3 | CPU-only 图伪造成 `PASS_GPU_EXECUTED_FULL` | validator 重算为 `FAIL_NO_GPU_COMPUTE`，报 forged/stale | 正确拒绝 |
| 4 | DML `device_id=9` | ORT wrapper 打印 “Falling back to CPUExecutionProvider”；`providers=['CPUExecutionProvider']`，`cpu_fallback_detected=true` | 正确识别 CPU fallback |
| 5 | 错误 provider 名 `AMDGPUExecutionProvider` | wrapper 回退 CPU；profile `AMDGPU=0, CPU=1` | 正确识别 CPU-only masquerade |

**新增 P0 防护**：`create_dml_session()` 现在在返回前检查
`"DmlExecutionProvider" in sess.get_providers()`；ORT python wrapper 的静默 CPU
retry 会直接抛 `DML_FAILED`，不再可能把 CPU session 当 DML。

---

## I. Root Cause & Open Gaps（P0/P1/P2）

### P0

1. **AMDGPU EP 缺失（环境，BLOCKED_ADMIN_DEPENDENCY）**
   - 全盘无 `amdgpu-ep.dll`；`C:\Program Files\WindowsApps` 只有 VitisAI 1.8，
     没有 `MicrosoftCorporationII.WinML.AMD.GPU.EP`；WinMLShared 7.0 是 1.8 代。
   - WinML `ExecutionProviderCatalog.find_all_providers()` 返回 `[]`；
     `ensure_and_register_certified_async()` 也返回 `[]`。
   - **根因判断**：本机没有 Windows ML 2.x + AMD GPU EP 包；驱动 32.0.22018.5
     与参考机 32.0.22032.6002 不同，但仅凭版本不能断定因果。
   - 未安装/升级系统 EP 或驱动（mission 禁止）。AMDGPU 9/9 只能记
     `BLOCKED_EP`，不能算 CPU/GPU PASS。
2. **Full catalog 未完成**：135 eligible 中 126 个 NOT_RUN（33 baseline + 81
   resource-gated + 12 optional-stress）。未声明 full coverage。
3. **DML 物理绑定强度**：`device_binding.state=EP_DEVICE_SELECTED` +
   DXGI LUID 佐证，但 DML EP 不暴露 LUID/ordinal，`candidate→LUID` 仍属
   **indirect**（AMDGPU-RCA-004）。报告标注 binding confidence，不称
   `PHYSICAL_GPU_IDENTITY_CORROBORATED`。

### P1

4. **任务 oracle 的单图/近似限制**：pose OKS 使用 `s=2*imgsz²` 作为 object
   scale 代理，而非 bbox area；segment mask 在输入分辨率比较；所有任务只跑
   1 张图（OBB 1 张），不足以代表数据集级 mAP/精度。
5. **性能测量不含 pre/post E2E**，无温度/功耗；seg 三次重复离散度大。
6. **没有 shard orchestration 的独立 CLI**：本次仅 Tier A；full-catalog 需要
   4-checkpoint shard + resume 的编排器与资源预算，尚未实现/执行。
7. **Pose parity 接近阈值**：max_abs=4.53e-3 < 5e-3，余量仅 ~10%；需要在更多
   输入上确认不是边界脆弱。

### P2

8. `routing_notes` 出现重复前缀 `BLOCKED_EP: BLOCKED_EP:`（cosmetic）。
9. 本机 `Win32_ComputerSystem` RAM 95.8 GB 与 dxdiag 128 GB 不一致（共享显存
   分配），报告保留两者，不推断。
10. GPU 功耗/温度/驱动事件日志未采集。

---

## J. Deliverables & Evidence Index

Run 目录：`reports/gfx1151/GFX1151_PHYSICAL_20261119T081900Z/`

| 文件 | 说明 |
|---|---|
| `HOST_FINGERPRINT.json` | OS/CPU/GPU/驱动/PnP（实例后缀已脱敏） |
| `DEVICE_IDENTITY.json` | DXGI adapters、selected LUID、DID-pinned resolution |
| `EP_INSTALL_AND_PROBE.json` | WinML API、EP DLL 搜索、register 结果、providers |
| `GPU_CANARY.json` | CPU/AMDGPU/DML canary：nodes、CPU parity、DML LUID 佐证 |
| `receipts/` | 18 个 immutable v2 receipts |
| `raw/` | 脱敏后的 worker 日志、case JSON、ORT profile |
| `benchmark/` | 3 次独立 fresh-session 基准 |
| `CASE_LEDGER.json`, `R1_CASE_LEDGER.jsonl` | 逐 case 账本 |
| `COVERAGE_SUMMARY.json` | 135 eligible / 270 目标单位的 per-EP 覆盖 |
| `DISPOSITION_LEDGER.json/.csv` | 142 项逐项 disposition |
| `compatibility_matrix.csv` | 每 checkpoint 一行，gfx1100/gfx1201 = NOT_RUN |
| `PROFILE_SUMMARY.json` | ORT profile provider 节点统计 |
| `ENVIRONMENT_LOCK.json`, `ENV/` | 版本锁定、pip freeze、dxdiag 摘要 |
| `REPRODUCE.md` | 从零复现命令 |
| `PREFLIGHT_SNAPSHOT/` | 独立 preflight run 的快照 + SHA 索引 |
| `LOCAL_ONLY_INDEX.json` | 权重/张量未公开，记录 hash 与本地位置 |
| `manifest.sha256` | 所有公开文件 hash |
| `GFX1151_PHYSICAL_FINAL_CN.md` | 本报告 |
| `GFX1151_PHYSICAL_OPEN_GAPS.md` | 开放问题清单 |

Soak：`reports/gfx1151/GFX1151_SOAK_DETECT_20261119T075000Z/`、
`GFX1151_SOAK_SEG_20261119T080600Z/`、`GFX1151_SOAK_20261119T074900Z/` (AMDGPU blocked)。
Negative：`reports/gfx1151/GFX1151_NEG_INJECT_20261119T080500Z/`。

**独立复核命令**：

```powershell
.venv-winml\Scripts\python -m runner.verify_receipts --run-dir reports/gfx1151/GFX1151_PHYSICAL_20261119T081900Z
```

第三方应逐条核对：receipt 的 `session.provider_node_counts`、
`device_binding`、`parity.outputs`、`task_parity.metrics` 与
`worker.exit_code`，而不是只看 `ALL_OK`。

---

## K. Next Actions

1. **P0 — 在授权窗口安装 Windows ML 2.x AMD GPU EP**（或换到已带
   `MicrosoftCorporationII.WinML.AMD.GPU.EP` 的镜像），重跑 preflight 与 9×2
   Tier A，拿到真正的 AMDGPU 节点/parity/task 证据；不得用 DML 结果替代。
2. **P0 — 完成 33 个 BASELINE_REQUIRED + 81 RESOURCE_GATED 的有界 shard**：
   先实现 4 checkpoint/shard 的 `runner.shard_matrix`，带 resume、资源预算、
   OOM/超时分类，再执行；未跑项必须保留 NOT_RUN。
3. **P1 — 多图任务 oracle**：至少 20–50 张 COCO/DOTA fixture，报告数据集级
   P/R/mIoU/OKS，而不是单图；pose OKS 改用 bbox area。
4. **P1 — E2E + 资源计量**：把 cv2 preprocess/postprocess 纳入计时；用
   `psutil`/驱动计数器记录进程内存与 GPU 共享内存；温度/功耗继续 NOT_MEASURED
   除非有校准工具。
5. **P2 — 加固**：修 `routing_notes` 重复前缀；把 `negative_injection` 的
   DML fallback 检查固化进 CI 的 CPU-safe 子集。

---

## 核心问题回答

**在真实 Windows Radeon 8060S 上，哪些模型/任务已被严格证明可由 AMDGPU/DML 正确执行？**

- **DML**：9 个 Tier A checkpoint（YOLO26 detect/segment/semantic/depth/classify/
  pose/OBB + YOLO11n/YOLOv8n detect）全部 `PASS_GPU_EXECUTED_FULL`；
  DML profiler 节点 >0、CPU 节点 0、CPU ORT by-name parity、7 类任务 oracle 全过。
- **AMDGPU**：**0 个**。本机没有 EP，所有 9 个目标 case 均为 `BLOCKED_EP`；
  没有静默 CPU fallback，也没有伪造 GPU PASS。
- **性能**：DML 暖跑 p50 约 10–12 ms（detect/pose）、12–18 ms（seg），
  冷启动 0.4–0.9 s；15 min detect soak 146,842 runs、0 error。
- **第三方复现**：使用 `REPRODUCE.md` 的冻结版本/权重/输入，重跑
  `runner.verify_receipts` 并逐条核对 raw profile、parity、task metrics 与
  manifest；所有 NOT_RUN/BLOCKED 在 `DISPOSITION_LEDGER` 中可查。
