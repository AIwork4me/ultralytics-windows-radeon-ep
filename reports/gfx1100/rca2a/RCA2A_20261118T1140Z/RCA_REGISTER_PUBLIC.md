# RCA2A Register — AMDGPU/MIGraphX tuning & cache anomalies (live)

格式：`RCA2A-###` 现象 / 受影响面 / 复现 / 证据 / 假设与竞争解释 / 判别实验 /
root cause / 修复或规避 / 残余风险 / 状态。
状态限定：`CONFIRMED / SUPPORTED / NOT_ISOLATED / REFUTED / NOT_SUPPORTED_IN_SHIPPED_BUILD / BLOCKED`。

---

## RCA2A-001 — WindowsApps glob ACL 使 catalog 静默注册失败 → CPU 假跑 — CONFIRMED (fixed)

- **现象**：探针 v1/v2 子进程 `providers=["CPUExecutionProvider"]`，create 0.3s，
  仍打印 `canary ok`；stderr 仅 `Unknown Provider Type: AMDGPUExecutionProvider`。
- **受影响**：main 分支 `windows_ml_ep_catalog._rederive_real_paths()` 在普通进程
  权限下对 `C:\Program Files\WindowsApps` 的 glob 返回空 → 候选 DLL 列表为空 →
  注册被 best-effort 吞掉 → ORT Python 仅 WARN 并静默回退 CPU 会话。
- **复现**：`PROBES/probe_baseline`（v2 运行，12:25 时戳），stdout JSON providers 字段。
- **判别**：手工直接路径注册成功（ep_devices 出现 2×AMDGPUExecutionProvider），
  证明 DLL/EP 本身正常，故障在路径发现层。
- **root cause**：WindowsApps 目录 ACL 禁止普通进程列目录；catalog 的 glob 模式
  静默返回空且无失败信号。
- **修复（本任务）**：`rca2a_ep._appx_ep_dll_path()` 经 `Get-AppxPackage` 解析
  InstallLocation 作为回退；`_ensure_amdgpu_registered` 改为 fail-closed（注册后
  必须观察到 AMDGPU ep-device，否则 RuntimeError）；worker 在 session 建成后校验
  providers 含 AMDGPUExecutionProvider，否则 exit 45。
- **残余风险**：依赖 AppxPackage PowerShell（约 1-2s）；若包名升级需同步。
- **上游关联**：gfx1151 主线 harness 在其路径发现上同样存在该静默失败面。

## RCA2A-002 — main 版 device_identity 不产出 luid_key → 设备选择退化为启发式 — CONFIRMED (fixed)

- **现象**：探针 v3 `create` 成功但 `run` 报 `HIP failure 101: invalid device
  ordinal; GPU=0`。
- **受影响**：`rca2a_ep`（RCA1 移植）调用 `adapter.extra.get("luid_key")` 得 None
  → `target_luid=None` → 未走 `add_provider_for_devices` 钉选 → ORT 自选 EP 设备
  （枚举到镜像设备 LUID 118045，无输出、Discrete 缺失）→ hipSetDevice 失败。
- **证据**：诊断运行显示 main `resolve_target_adapter` 只填 `luid_str=
  '0x00000000:0x00011A9A'`；EP 设备 LUID 元数据为十进制字符串 '72346'/'118045'。
- **root cause**：main 与 RCA1 两个分支的 device_identity 字段契约不一致
  （RCA1 填 luid_key 整数；main 只有 luid_str 十六进制 High:Low）。
- **修复（本任务）**：`rca2a_ep.physical_luid_key()` 接受两种来源，解析 Low DWORD
  十六进制 → 十进制（0x11A9A == 72346，与 EP 元数据匹配），LUID 精确钉选恢复。
- **残余风险**：多 GPU 机器上 High:Low 解析需按 DXGI LUID 完整 64 位对齐；
  本机单卡+镜像枚举下 Low DWORD 判别已够（镜像 LUID 不同）。

## RCA2A-003 — MIGRAPHX_TRACE_BENCHMARKING=1/2 使 EP 会话初始化 EP_FAIL — CONFIRMED

- **现象**：canary 上 TRACE=1 与 TRACE=2 均先在 stdout 打印 benchmarking 细节
  （"Benchmarking gpu::mlir_op: 13 configs"，level 2 含 Problem 行与逐解耗时），
  随后 `InferenceSession` 构造以 `EPFail : 11 : Failed to call function` 失败。
- **受影响**：amdgpu-ep 1.8.65.0 + MIGraphX v2_17_0（shipped），任何开启
  MIGRAPHX_TRACE_BENCHMARKING 的进程。
- **对照**：同一 canary、同一代码、无 TRACE → 正常完成（create 0.625s）；
  TRACE + SKIP_BENCHMARKING=1 → 正常完成（"1 configs"）。
- **假设**：trace 打印路径在 EP 初始化的编译线程内回调（stdout 重入/同步问题）
  触发失败；竞争解释：仅 level-2 的逐解打印触发 —— 已被 level=1 同样失败否定。
- **root cause（工作定性）**：trace 输出与 EP 初始化路径不兼容（确切 native 帧
  无法在用户态定位，标注 SUPPORTED 级机理）；影响面=诊断能力受损，不影响正常执行。
- **规避**：TRACE 仅用于诊断且预期崩溃；诊断值=崩溃前的 partial 输出（候选计数
  仍可采集）。绝不把 TRACE arm 与计时 arm 混跑。
- **残余风险**：真实大模型上崩溃时点可能更早（更早的算子即触发），候选计数
  采集可能不完整 —— E1 证据按“部分计数下界”解释。

## RCA2A-004 — MIGRAPHX_GPU_OPTIONS={"problem_cache_files":[...]} 使会话初始化 EP_FAIL — CONFIRMED

- **现象**：设置该 env（指向用户可写 json）后 session 构造直接
  `EPFail: Failed to call function`，无任何 cache 相关日志。
- **受影响**：shipped migraphx_gpu.dll 含 `MIGRAPHX_GPU_OPTIONS` 与
  `problem_cache_files` 字符串，但该组合在本机构建上崩溃。
- **竞争解释**：v2_17_0 的 relaxed-JSON 解析器对该值形态敏感（反斜杠转义/数组
  形态）；或该键在 EP 交付路径上未初始化即被消费。
- **判别**：baseline 同进程无该 env 正常 → 单变量归因成立。
- **结论**：`PROBLEM_CACHE_WRITABLE_REDIRECT` 在当前 MSIX 构建 =
  `NOT_SUPPORTED_IN_SHIPPED_BUILD`（且 fail-closed 崩溃，非静默忽略）。
  不改 ACL、不改 DLL；上游能力缺口按 docs 记录。

## RCA2A-005 — auto_save_problem_cache 每会话尝试写只读包目录并失败（无害但恒定） — CONFIRMED

- **现象**：每次 AMDGPU 会话创建（无论开关），stderr 必现
  `[WARN] auto_save_problem_cache: save failed: ... write_buffer: Failure opening
  file: C:\Program Files\WindowsApps\...\ExecutionProvider\problem_cache.json`。
- **机理**：EP 构建（1.8.65.0）将 shipped problem_cache.json 同时当作读库与
  auto-save 目标（旧代实现；上游 main 已改为 read_only_problem_cache_files +
  可写 problem_cache_files 分离）。写失败仅 WARN，执行不受影响。
- **含义**：tuning solutions 无法在进程间持久化（除非未来构建支持
  ORT_MIGRAPHX_PROBLEM_CACHE 写侧或修包目录可写——均超出本任务授权）。
  注意：这不否定 `.mxr` 编译缓存（RCA2A-006 证明其独立生效）。

## RCA2A-006 — ORT_MIGRAPHX_CACHE_DIR 指向用户目录 → .mxr 编译产物真实落盘 — CONFIRMED

- **现象**：设置该 env 后，隔离缓存目录出现
  `21100-e703a7fc41b4615e-9880cf1608079e0d-32-510799189cd4b485.mxr`（270,891 B）。
  文件名前缀 21100 = MIGraphX 版本 hex；中段 = graph id / mlss 哈希；尾段 =
  input shapes 哈希（与上游 mgx_ep.cc 命名一致）。
- **判别**：无该 env 的 baseline 目录恒空（多探针多轮）→ 单变量成立。
- **待矩阵补强**（E4）：跨进程第二次启动同模型同 shape 是否加载命中
  （loaded_from_cache、create_s 显著下降、文件未被重写），以及
  FORCE_RECOMPILE=1 的失效行为（E4_MXR_FRC）。

## RCA2A-007 — shipped problem_cache.json 为四 gfx 目标只读调优库（gfx1100 672 解） — CONFIRMED (informational)

- 内容分析：4 设备块（gfx1200/gfx1151/gfx1201/gfx1100），共 2601 解；
  gfx1100 块 672 条（637 条 gpu::mlir_op GEMM problem 字符串 + 35 条 pooling 等
  dict 形态），cu_count=48 与 RX 7900 XTX 一致；含 f32 形如
  `-m 256 -n 1024 -k 512`、`-m 256 -n 1536 -k 512`（transformer 形状族）。
- 含义：读侧 tuning 库随包分发；`ORT_MIGRAPHX_PROBLEM_CACHE` 理论上可前置
  用户副本，但 canary 级未见任何可观察差异（probe `problem_cache_ort_env_read`
  无 load 日志、create 时间不变）→ 读侧重定向
  `RECOGNIZED_BUT_NO_OBSERVABLE_EFFECT`（canary 级）。

## RCA2A-008 — rca2a_worker --ep dml 调用契约不匹配（dml_session 无 sess_options_override） — CONFIRMED (fixed pre-execution)

- **现象**：Phase-4 准备期代码审查发现 `rca2a_worker.make_session()` 以
  `create_dml_session(..., sess_options_override=so)` 调用，而 main 分支
  `dml_session.create_dml_session` 无该关键字 → 一旦执行 E8 DML 臂必
  TypeError（rc=42 runtime_error）。
- **受影响**：仅 E8 DML 对照臂；Phase 1-3 全部 amdgpu 臂不受影响（无既有收据
  受污染——bug 在任何 E8 GPU 运行之前被发现并修复）。
- **root cause**：worker 复用 main 的 dml_session 时假设了与 amdgpu_session
  相同的 override 契约，未做签名核对；单测此前只覆盖 amdgpu 路径。
- **修复**：`dml_session.create_dml_session` 增加可选 `sess_options_override`
  关键字（向后兼容，既有调用点 soak.py/_ep_worker.py/test_gpu_canary.py 不变）；
  新增 `tests/unit/test_rca2a_dml_override.py`（2 例，含 override 对象同一性与
  DML 强制旗标断言）；38/38 通过。STAGING 记录于 `main_overrides/`。
- **残余风险**：无 GPU 侧影响；E8 首次真机运行仍按 fail-closed 门禁验收。

## RCA2A-006 补强（2026-11-18 phase-3 执行完毕） — E4 复用/失效闭环 — CONFIRMED

- **复用（同日独立进程）**：`E4_MXR_CACHE_*_rep9_p2` 在 FRC 之前执行：
  yolo26n create 1.016s、rtdetr-l create 4.05s；四个快照点 mxr 文件
  SHA/bytes/mtime 与 Phase-2 完全一致（0ac9bb17…/183121308B，05:56/05:58Z）→
  命中且未重写。
- **失效对照（FRC）**：`ORT_MIGRAPHX_FORCE_RECOMPILE=1`（migraphx-backend.dll
  字符串确认，上游 mgx_ep.cc load_compiled_program gate）6/6 attempt
  CLEAN+GPU_PASS：
  yolo26n create 44.6–46.1s（≈E0 43.3–44.0s），rtdetr-l 121.5–130.2s
  （≈E0 115.6–116.4s）；单次 attempt 内 pre→post 快照显示 mxr 被重写
  （yolo 0ac9bb→10f03f，size +2427B，mtime=attempt 时刻；rtdetr
  183121308→183129112B，mtime 10:28:11Z）。
- **结论**：`.mxr` 编译模型缓存的加载受 FORCE_RECOMPILE 门控 = 开关真实
  生效；缓存命中与失效均为可观察、可复现的物理行为。
  `CACHE_CROSS_PROCESS_HIT` + `CACHE_INVALIDATION_CONTROLLED` 双向成立。

---

## 四层缓存/调优机制定级总结（G5，不得混淆的四层）

| 层 | 机制 | shipped 1.8.65.0 (MIGraphX 2_17_0) 状态 | 独立证据 |
|---|---|---|---|
| 1 | **Kernel Benchmarking**（rocMLIR 候选实测调优） | `AUTOTUNING_EXECUTION_CONFIRMED`：默认路径真实执行（trace 可见 13 configs/op）；单变量 SKIP 开关 DLL 识别且物理生效 | CAPABILITY_MATRIX + E2（yolo -59%/rtdetr -23% create）+ E1 崩溃前 trace |
| 2 | **Problem Cache**（tuning solutions 持久化） | 读侧=随包只读库（gfx1100 672 解，RCA2A-007）；用户重定向 fail-closed 崩溃（RCA2A-004）→ `NOT_SUPPORTED_IN_SHIPPED_BUILD`；auto_save 恒失败（RCA2A-005）⇒ **tuning 解不能跨进程持久** | probe + E3 判定（无 PROBLEM_CACHE_EFFECTIVE 证据，亦非机理否定） |
| 3 | **Kernel Binary Cache**（`MIGRAPHX_BINARY_CACHE`） | DLL 字符串不存在 → `NOT_SUPPORTED_IN_SHIPPED_BUILD`（上游源码存在 ≠ 本机可用） | CAPABILITY_MATRIX 三层证明 |
| 4 | **Compiled Model Cache（.mxr）** | `MODEL_CACHE_EFFECTIVE`：落盘→跨进程命中（-98.0%/-96.7%）→FORCE_RECOMPILE 受控失效→进程内重建亦命中（0.64s vs E7 无缓存 42.8s） | RCA2A-006 + E4/E4_FRC/SOAK 收据链 |

**关键结论**：第 4 层生效**不等于**第 2 层生效——.mxr 保存的是编译后的整图程序
（含已选 kernel 的编译产物），tuning solution 库仍为只读且不可重定向；两者机制、
键、失效行为、上游实现文件（mgx_ep.cc load_compiled_program vs compile_ops.cpp
problem_cache）均不同，报告与 PR 描述必须分层陈述。

## RCA2A-009 — 监督器 GPU_PASS 验证对 DML 臂误要求 amdgpu 专属探针事件 — CONFIRMED (fixed, rerun scheduled)

- **现象**：E8_DML_yolo26n_rep0_p2 物理事实全部成立（providers=[Dml,CPU]、
  DML profile 节点 231>0、独立 parity max_abs=2.47e-03 < 5e-3、rc=0），但
  收据 verdict=GPU_PASS_INVALIDATED，唯一失败检查为 `ep_device_probe_event`。
- **root cause**：`rca2a_main._validate_gpu_pass` 无条件要求
  `ep_device_selected` 事件；该事件仅在 amdgpu 注册路径发出（worker 的
  `if a.ep == "amdgpu"` 分支），DML 路径按设计只发 `gpu_identity`
  （DXGI 物理适配器解析，含 vendor_id/device_id）。
- **修复**：验证器分支化——dml 臂要求 `gpu_identity` 事件且
  `vendor_id==0x1002`（物理 Radeon 解析成功）；amdgpu 路径保持原检查不变。
  新增 `tests/unit/test_rca2a_dml_validation.py`（4 例：通过/缺身份
  fail-closed/非 AMD fail-closed/amdgpu 路径回归），全套 42/42。
- **纪律**：rep0_p2 收据原样保留（INVALIDATED 是当时验证器的真实输出）；
  修复后以新 attempt（rep1_p2b）重跑 E8 出具有效收据；两次收据同存。

## RCA2A-010 — STAGING 镜像滞后：worker/probe 停留在 v1 版本 → C1 分支 amdgpu 路径损坏 — CONFIRMED (caught by PUBCONF, fixed)

- **现象**：PUBCONF 确认套件（发布头 7392be11）16 个 amdgpu 臂 2.1s 内全部
  rc=42（`ImportError: cannot import name '_ensure_amdgpu_registered' from
  'radeon_ort.amdgpu_session'`），E8 DML 臂通过 → 单点定位到
  `runner/rca2a_worker.py` 内容错误。
- **root cause**：C1 的树取自 `$RUN/STAGING`，而 STAGING 的
  rca2a_worker.py/rca2a_probe.py 是 G2 期（v1，2c1f44/5f4837）旧版；
  物理实验全部在 $WT 终版（v2 起 2f6fbd/cfeafc，RUN_CODE_MANIFEST v2/v3
  一致）上执行，STAGING 未随后同步 worker/probe 两个文件。
- **这正是 PUBCONF 存在的意义**：PUBLICATION_CODE_NOT_PHYSICALLY_VALIDATED
  条款在 PR 创建之前拦截了不一致代码；16 份失败收据（code_git_sha=
  7392be11, worktree_dirty=false）如实保留。
- **修复**：STAGING←$WT 终版全量对齐（仅此 2 文件陈旧，其余 11 个代码文件
  均一致）；追加修复 commit C2（parent=C1，不改写历史）；C2 头重跑全套
  PUBCONF。残余风险：无——v2 worker 正是产出全部既有 GPU 收据的版本。
