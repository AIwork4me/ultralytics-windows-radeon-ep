# GFX1151-R1 最终报告（中文 A–J）

任务：AMDGPU-First True Task Parity / 确定性输入 / 证据加固。
真机：Windows 11 26200 + AMD Radeon 8060S（gfx1151, VID 1002 DID 1586）。
执行窗口：2026-11-18 05:45–09:45（本地）。总评：**GFX1151_R1_GO_FOR_OWNER_REVIEW**。

---

## A. 执行判定（逐 Gate）

| Gate | 判定 | 依据 |
|---|---|---|
| R1-0 预检 | **GO** | 仓库干净、冻结收据未动、Gate 0 无阻断；初始审计 10 项契约缺口（4×P0） |
| R1-A 校验器 | **GO** | schema v2 + 正交判定维度；61 项对抗测试全绿；v1 legacy 模式下 G2/G3 18 张收据复验 OK |
| R1-B 确定性输入 | **GO** | input_id 规范哈希（排除 EP/PID/hashseed/时间戳）；跨进程 SHA 一致（16 案 48 片段 ALL_OK） |
| R1-C 设备绑定 | **GO**（分层） | EP_DEVICE_SELECTED 有探测+重建后复验证据；candidate→LUID 映射诚实标注 IDENTITY_BINDING_UNPROVEN（RCA-004） |
| R1-D 真任务对齐 | **GO** | YOLO26n 七任务真图像×PT 参照全过（预声明阈值） |
| R1-E RCA | **GO** | 6 条事件全部登记（2 CLOSED_VERIFIED / 1 ISOLATED / 3 MITIGATED-REPRODuced 观察项），无证据 suppressed |
| R1-F 性能/浸泡 | **GO** | 阶段分解真实测量；双 EP 15 分钟浸泡 0 错误 |
| R1-G 代表矩阵 | **GO** | 实跑 32/32 GPU 案例全过（精确分母，见 H） |
| R1-H 冻结/审计 | **GO** | 收据 32、manifest 749 项、脱敏扫描干净、CI 绿、Draft PR #3 未合并 |

**AMDGPU_FIRST_GO：是。** AMDGPU 的全部 16 案例凭自身证据（探测、最终剖析、数值对齐、任务对齐）通过；DML 16 案例独立对照通过，从未替代 AMDGPU 结论。
**Owner 合并建议：** 先审 PR #3（本任务），再视 RCA-001/002 的观察项决定是否连带 PR #2 进入正式化；二者均保持 Draft，不自动合并。

## B. 精确 Git 状态

- 起点：PR #2（Draft, OPEN, 未改动）head `563c24e5e27a6abd61328720efd97ee618f10021`，base main `ee47a4496eb7f23fc29aaa8e9a22e08ab7f700bd`。
- R1 分支：`mission/gfx1151-r1-amdgpu-first-task-parity`，本地 HEAD = 远端 HEAD = PR #3 head = **`8ce34c5162de78bf3ea373e5aba384fe9d7f0d36`**（三者一致，已核）。
- **Draft stacked PR #3**：https://github.com/AIwork4me/ultralytics-windows-radeon-ep/pull/3（base=`mission/gfx1151-windowsml-foundation`，即 PR #2 head 分支；Draft；未标记 Ready）。
- CI：`windows-cpu-ci` **pass** 于 PR 精确 HEAD —— Actions run **44219621167**（job 134517955520）；push 事件 run 44219520979 同过。`secret-scan` 长期 pending（自 G 轮即如此，非本轮引入，已在 I 节注明）。
- 未 force-push、未重写历史；所有被替换的缺陷收据保存在对应历史提交中并由 `ERRATA.md`（5 条）索引。

## C. 环境与真机身份

- OS Windows 11 26200；Ryzen AI MAX / 128GB；GPU `AMD Radeon(TM) 8060S Graphics`，PNP `PCI\VEN_1002&DEV_1586&SUBSYS_17141043&REV_C1`；驱动 32.0.22032.6002（仅记录，未改动）。第二适配器 GameViewer 虚拟显示（仅观察）。
- EP：机器级预装 MSIX `MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8_1.8.65.0`，`amdgpu-ep.dll` SHA256 `cb7496b3…f8ab`，按进程注册为 `AMDGPU`（非 `AMDGPUExecutionProvider`）。ORT：onnxruntime-windowsml `1.25.2.202605110140`；PT 参照：Ultralytics **8.4.215（pinned）** + torch 2.15.0。
- 实际选定设备（全部 16 张 AMDGPU 收据一致）：ep-device 候选[0]（全局索引 3）HIP 101 失败 → **候选[1]（全局索引 4）执行**，probe 与重建后最终剖析均 AMDGPU 节点>0，`attribution=fused_single_node`（EP 融合归因，非逐源节点证明——如实标注）。DXGI 佐证（LUID `0x0:0x11746` 所在物理 GPU，VID/DID/描述一致）单独存放于 `dxgi_corroboration`，不冒充绑定证明。

## D. P0 修复（文件/机制/测试）

| 缺陷（R1-0 审计） | 修复 | 负向验证 |
|---|---|---|
| 空奇偶校验块可导出 GPU PASS | `receipts.derive_gpu_status`/`_validate_v2`：PASS 必须有非空、双向精确覆盖 expected_outputs 的奇偶校验 | `test_v2_empty_parity_vacuous_pass` 等 61 项对抗 |
| `hash(case_id)` 随机输入（PYTHONHASHSEED 依赖） | `input_contract.py`：规范 JSON → SHA256 input_id；`.npy` 持久化；PT/CPU/AMDGPU/DML 同字节 | `test_cross_process_sha_match_hashseed_0_vs_random` + `runner/verify_r1_inputs`（ALL_OK） |
| PT_APP 冒充任务对齐 | v2：`PASS_TASK` 仅可由 `TASK_PARITY` 证据块（真指标 vs PT 参照 + 预声明阈值 + 指标-阈值门）导出；旧 PT_APP 解释为 PT_APP_SMOKE_ONLY | `test_v2_pt_smoke_as_task_pass` 等 |
| LUID 证据与实际绑定矛盾 | `amdgpu_session.create_amdgpu_session` 返回会话+选择证明（逐候选日志/探测/最终复验/计时）；worker 写入 `device_binding`；`IDENTITY_BINDING_UNPROVEN` 分级 | `test_v2_binding_unproven`、`test_v2_probe_zero_nodes` |
| 子进程 rc≠0 可凭残片 PASS | worker.exit_code 契约 + 校验器硬门 | `test_v2_worker_rc_nonzero_fragment_pass` |
| 1 融合节点冒充逐节点证明 | `attribution` 字段强制记录 | 收据 + PER_AND_SOAK 注记 |

执行中发现并同类修复（均先保留失败证据再修复，见 ERRATA）：阈值键映射、DML 身份证据维度、OBB 厂商 NMS 列序、classify 图内 softmax、seg 3D protos、pose 空检测 reshape、整数输出奇偶契约（设计说明 `docs/r1/R1_INT_OUTPUT_PARITY.md`）。

## E. 七任务真任务对齐（YOLO26n，真图 bus.jpg / DOTA P1053，输入字节 PT=ONNX）

| 任务 | PT 实体 | AMDGPU | DML | 关键指标（AMDGPU） |
|---|---|---|---|---|
| detect | 5 框 | FULL+PASS_TASK | FULL+PASS_TASK | 5/5 匹配，IoU 0.999999，P=R=1.0 |
| segment | 5 实例+掩码 | FULL+PASS_TASK | FULL+PASS_TASK | 框 5/5 IoU 0.999999；mask IoU 1.0 |
| semantic | 11 类像素图 | FULL+PASS_TASK | FULL+PASS_TASK | pixel agreement 1.0，mIoU 1.0（逐位一致） |
| depth | 稠密深度图 | FULL+PASS_TASK | FULL+PASS_TASK | MAE 1.3e-6，rel 3.0e-7 |
| classify | top1=654 | FULL+PASS_TASK | FULL+PASS_TASK | top1/top5 一致，分数漂移 1.1e-6 |
| pose | 4 人×17 点 | FULL+PASS_TASK | FULL+PASS_TASK | 4/4，OKS 1.0 |
| obb | 15 旋转框 | FULL+PASS_TASK | FULL+PASS_TASK | 15/15，rotated IoU 0.99999998，角度差 4.5e-6° |

负向对照（detect/segment/pose/obb，确定性合成灰噪声图）：PT 空且 GPU 空，4/4 discriminating。数值对齐（GPU vs CPU-ORT 同图同字节）：32/32 allclose（fp32 阈值 5e-3；整数类图按新契约 1-px/409600 记录）。四路差分（PT / CPU-ORT / AMDGPU / DML 同一后处理）：见收据 `ref/cand_fingerprint_sha256`——四路在同输入同解码下一致到度量下限。

## F. AMDGPU RCA 台账（`rca/AMDGPU_RCA_REGISTER.md`）

| ID | 级 | 状态 | 结论（证据） |
|---|---|---|---|
| RCA-001 Init provider bridge failed | P2 | REPRODUCED | env 创建即现、与注册无关；30+ 次 GPU 执行全部成功；下一判别实验已定义（verbose 日志） |
| RCA-002 problem_cache 写失败 | P2 | MITIGATED | WindowsApps ACL 致每进程冷编译 71–201 s/模型；仅延迟无正确性影响；不改系统（禁止） |
| RCA-003 候选[0] HIP 101 | P1 | ISOLATED | 3/3 进程确定性复现（GPU=0 无效序号）；根因在闭源 DLL（外部边界）；失败关闭候选循环+证明已缓解 |
| RCA-004 双枚举无 LUID | P1 | MITIGATED | EP 元数据无 LUID/序号 → candidate→LUID 映射 UNPROVEN；收据分层记录 EP_DEVICE_SELECTED + DXGI 佐证 |
| RCA-005 precompile_op 告警（obb） | P2 | CLOSED_VERIFIED | 修复解码后 obb IoU 0.99999998 —— 告警无影响（no-change closure，含前后证据） |
| RCA-006 sem-s 1px 差异 | P2 | CLOSED_VERIFIED | 1/409600 px argmax 平局；两 GPU 逐位一致 → 契约缺陷已修（整数精确一致率契约） |

## G. 性能与浸泡（AMDGPU 优先）

- 会话创建（冷，含模型两次编译）：AMDGPU 71–201 s/模型（RCA-002 主导，探测+重建计时见每收据 device_binding）；DML ~1–2 s。
- 暖推理 p50（同输入同机串行）：AMDGPU 1.17 ms（cls-224）… 16.31 ms（s-obb-1024）；DML 全线更快（对照，不替代）。
- 15 分钟推理相浸泡（初始化排除、心跳 30 s、看门狗未触发、干净退出）：AMDGPU 155,007 次 / 0 错 / p50 4.41 ms；DML 269,534 次 / 0 错 / p50 3.27 ms。明细 `R1_PERF_AND_SOAK.md`。

## H. 实际执行矩阵与目录

- **本轮实跑分母：32 GPU 案例 = 16 模型 × 2 EP，全数 PASS_GPU_EXECUTED_FULL + PASS_TASK**（yolo26n 七任务、yolo26s 七任务、yolo11n、yolov8n）+ 8 次 GPU 负向对照 + 32 次 CPU 参照 + PT 参照（正/负）。随机张量数值压力类本轮未重跑（由冻结的 G2/G3 证据与确定性压力单测单独覆盖，标签分离）。
- 目录快照 142 不变：16 EVIDENCED（证据等级显著加强）、26 BASELINE_REQUIRED NOT_RUN（独立工作流，续跑命令 `runner/r1_pipeline --models <list>`）、100 其他受限。未谎称 142/142。
- G2 期 9/33 快照在 `ERRATA.md` 语境下解释（历史冻结不改写；本轮 32 收据为新增直接证据）。

## I. 证据与 CI

- 根目录 `reports/gfx1151/R1_20261117T215114Z/`：收据 32（`receipts/`）、原始证据（`raw/`，含全部失败尝试）、输入束（`inputs/`，仅哈希进收据）、`manifest.sha256`（749 项，重算验证 OK）、`R1_INPUT_IDENTITY.json`、`R1_CASE_MATRIX.csv`、`R1_TASK_PARITY_MATRIX.csv`、`R1_DEVICE_BINDINGS.json`、`R1_LEDGER.json`、`AMDGPU_RCA_REGISTER.md`+`rca/`、`R1_PERF_AND_SOAK.md`、`R1_ADVERSARIAL_CI.md`、`R1_GAP_REGISTER.md`、`R1_PROVENANCE.md`、`R1_REPRODUCE.md`、`redaction_scan.json`（干净）、`ERRATA.md`。
- 校验命令与结果：`runner.verify_receipts` → **ALL_OK**（含阈值交叉核对）；`runner.verify_r1_inputs` → **ALL_OK**；`pytest tests` → **116 passed / 1 skipped**（3 硬件测试真机通过）。
- CI：`windows-cpu-ci` pass @ 精确 PR head（run 44219621167）。`CPU_CI_DOES_NOT_ATTEST_GPU` 语义不变；`secret-scan` pending 为仓库既有状态（G 轮起即如此，非本轮引入）。
- 冻结证据 G1/G2/G3/SOAK_RUN1 字节未动（本轮零接触；G2 manifest 的 8 项 unlisted 为历史遗留，已在报告中说明且不改写）。

## J. 后续与 Owner 决策点

1. **仅限 Owner 批准的动作**：合并 PR #3 → 再评估 PR #2 转正；是否授权 26 项 BASELINE_REQUIRED 目录跑批；是否向 AMD/微软反馈 RCA-002/003（MSIX 缓存路径 + 无效设备序号，最小复现已备）。
2. 阻断项：无（无 BLOCKED_*）。观察项：RCA-001（bridge 告警）、RCA-002（冷编译成本，若 EP 未来支持缓存重定向 env 可复测）。
3. 建议最终合并门：PR #3 review 通过 → 合并 → PR #2 rebase 后转正评审；合并前保持双 Draft。
4. 未做且明确不做：不改驱动/WindowsApps/系统配置；不上游 PR；不伪造/覆盖任何收据（缺陷证据全部在 git 历史 + ERRATA）。

---

*报告标签保留英文；数据文件以仓库内 `R1_LEDGER.json`/CSV 为准。*
