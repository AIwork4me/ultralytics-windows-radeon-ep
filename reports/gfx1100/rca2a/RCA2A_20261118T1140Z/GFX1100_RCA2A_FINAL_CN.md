# GFX1100 RCA2A 最终报告（A–J）— MIGraphX Auto-Tuning 归因与缓存有效性

任务：`UWR-GFX1100-RCA2A-RESUME-AND-PUBLISH` · 运行根：
`%RUN_ROOT%`（本地 `%RUN_ROOT%`）
· 报告时间：2026-11-18 · 全部结论由原始收据重算可得
（`runner/rca2a_g4_stats.py`、`runner/rca2a_main.py verify`）。

---

## A. Executive Verdict

| 门禁 | 判定 |
|---|---|
| `AUTOTUNE_ATTRIBUTION_GO` | **GO** — `MIGRAPHX_SKIP_BENCHMARKING` 被 DLL 识别且单变量物理生效：yolo26n create −59.4%（配对中位，bootstrap CI95 [58.6, 59.6]，n=3，low-power 标注）、rtdetr-l −23.1%（[22.8, 23.5]）。候选实测调优真实执行（trace 可见 25 configs/first conv op，canary 13 configs/GEMM op）。 |
| `CACHE_EFFECTIVENESS_GO`（.mxr 编译模型缓存） | **GO** — 落盘→跨进程命中（冷启动 **−98.0% / −96.7%**）→受控失效（FORCE_RECOMPILE 6/6 回基线 + 单次 attempt 内 .mxr 重写，SHA/size/mtime 三重变化）→进程内重建亦命中（0.64s vs 无缓存 42.8s）。 |
| `STEADY_NOT_REGRESSED`（首选缓存方案 E4） | **PASS** — 稳态 mean 门（≤5%）：yolo26n +2.63%、rtdetr-l +0.09%；15 分钟有效稳态 soak 双模型通过（916.8s / 943.5s），0 看门狗、0 静默回退、parity 3/3。 |
| `PROBLEM_CACHE` / `BINARY_CACHE` | `NOT_SUPPORTED_IN_SHIPPED_BUILD`（详见 G/E：重定向 fail-closed 崩溃 / DLL 无该环境变量字符串）——**与 .mxr 缓存生效是两层独立事实，不得互推**。 |
| `GPU_TRUTH_GO` | **GO** — 42 份收据中 38 份 `GPU_PASS_VALIDATED`（LUID 钉选物理 gfx1100 + EP profile 节点>0 + 监督器独立 parity + providers fail-closed）；4 份非 PASS 均为如实保留：2×E6 预注册 900s 天花板击杀、1×E1 诊断臂预期崩溃（RCA2A-003）、1×E8 修复前验证器缺陷输出（RCA2A-009，已修复并重跑通过）。 |
| 各 arm FAIL | 不掩盖：E2 使 rtdetr-l 稳态 **+20.0% 劣化**（`COLD_FAST_STEADY_SLOW`，不得作生产默认）；E6 穷举 ~12.4× create 成本且稳态无增益；rtdetr-l E6 = `BLOCKED_BY_PRESET_CEILING`。 |
| **总评** | 首选生产建议：**启用 `ORT_MIGRAPHX_CACHE_DIR` 指向用户可写持久目录**（唯一通过全部门禁的方案）；不建议默认 skip-benchmarking（rtdetr-l 稳态代价）或 exhaustive/compute-max。 |

## B. Exact Git State

- 目标远端 `origin/main`：`492975284f7c93684d6dbd67801a5634f450f7a0`（本日内多次经
  `api.github.com` 与 `git ls-remote` 双通道复核一致）。
- **github.com Git 端点 pack 传输全天中断**（ref 广告正常、对象传输
  reset/slow/EOF；`git fetch origin main` 与 shallow clone 均 40+ 次失败；
  调优 `http.lowSpeedLimit=0` 后一度接近完成仍断）。
- 因此本轮 42 份收据的 `code_git_sha=UNKNOWN, worktree_dirty=true`
  （执行树 B' 无 .git，blob 级对齐 origin/main，见 `RUN_CODE_MANIFEST.json`
  v1/v2）——schema 按设计判 INVALID（fail-closed），**发布前必须在真实
  worktree B HEAD 上重跑 PUBCONF 确认套件**（计划已预冻结于
  `%RUN_ROOT%\PUBCONF*`）。
- 发布分支（拟）：`mission/gfx1100-rca2a-migraphx-autotune-cache`
  （自 `4929752`）。Draft PR URL 与 CI run：**取决于 G7 网络恢复，见文末状态更新**；
  若持续中断则如实 `PUBLICATION_BLOCKED`，本报告与全部精简证据仍本地交付。
- 本地主仓库 `mission/rca1-amdgpu-soak-stall` @`1aac7fb` 与 95GB 冻结证据未动。

## C. Hardware & EP Provenance

- 物理 GPU：AMD Radeon RX 7900 XTX，DEV_744C（gfx1100），DXGI LUID Low
  DWORD 72346（0x11A9A），驱动 32.0.21030.2001；EP 设备选择
  `LUID match` 钉选物理卡（镜像设备 LUID 118045 被显式排除，RCA2A-002 修复后）。
- EP 栈：MSIX `Microsoft.WinML.AMD.GPU.EP.Framework.2` 1.8.65.0；
  `amdgpu-ep.dll` SHA256 `cb7496b3…f8ab`（24 个 DLL SHA 见
  `ENVIRONMENT_LOCK_PUBLIC.json`，执行日复验一致）；MIGraphX 内部版本
  `version_2_17_0`；上游钉子：AMDMIGraphX@`2e2cb92`、
  onnxruntime-ep-amdgpu gpu_options.h@`3c988ec`。
- 运行：Python 3.12.16 + onnxruntime 1.25.2（ORT-WinML build）；
  模型 yolo26n/rtdetr-l 静态 FP32 ONNX（SHA 见 `EXPERIMENT_PLAN_LOCK`，
  权重绝不入库）；同一冻结输入（SHA `64d11ef4…`）。
- 已知无害异常：WMI `vram_gb=4` 为宿主上报假象（DXGI
  dedicated_video_mb 正确）；每会话 `auto_save_problem_cache` 写只读包目录
  WARN（RCA2A-005）。

## D. Auto-Tuning Attribution

- 执行证据：默认路径真实进行 kernel 候选 benchmark——E1（诊断臂）在
  yolo26n 上打印 `Benchmarking gpu::mlir_op: 25 configs` 与
  `Fastest solution: v3:64,64,64,16,2,4,1,1,2` 后 EP_FAIL（RCA2A-003，
  崩溃前部分计数=下界）；canary（GEMM op）13 configs。
- 归因（E0 vs E2，同模型/输入/EP/驱动/stdio=FILE，n=3 独立进程）：
  yolo26n create 43.64s→17.80s（−59.4%）；rtdetr-l 115.87s→89.09s（−23.1%）。
- 未分离部分：session create 内部的图优化/JIT/固定成本切分
  `GRAPH_OPT_JIT_AUTOTUNE_SPLIT_NOT_INSTRUMENTED`（EP 无内部阶段计时暴露，
  不杜撰百分比）。
- E6（compute_mode=maximum，AMENDMENT-1 2700s）：yolo26n 两次干净样本
  572.78/506.91s（≈12.4× 基线）而稳态 2.89ms ≈ 基线 2.91ms——穷举调优
  在该模型上无稳态收益；rtdetr-l 登记 `BLOCKED_BY_PRESET_CEILING`。
- E2 稳态代价：yolo26n +1.03%（无退化）；rtdetr-l **+20.04% 劣化**
  （12.63→15.16ms mean）→ `COLD_FAST_STEADY_SLOW`，生产不推荐。

## E. Problem / Binary / MXR Cache Results（四层独立结论）

| 层 | 判定 | 关键证据 |
|---|---|---|
| 1 Kernel Benchmarking（rocMLIR 候选实测） | `AUTOTUNING_EXECUTION_CONFIRMED` | trace 行 + skip 单变量效应（D 节） |
| 2 Problem Cache（tuning solutions） | 用户侧重定向 `NOT_SUPPORTED_IN_SHIPPED_BUILD`（fail-closed 崩溃，RCA2A-004）；读侧=随包只读库（gfx1100 672 解，RCA2A-007；canary 级无可观察效应）；auto_save 恒失败（RCA2A-005）⇒ tuning 解不能跨进程持久 | probe + 上游源码钉子 |
| 3 Kernel Binary Cache（`MIGRAPHX_BINARY_CACHE`） | `NOT_SUPPORTED_IN_SHIPPED_BUILD`（DLL 字符串不存在） | CAPABILITY_MATRIX 三层证明 |
| 4 Compiled Model Cache（.mxr） | `MODEL_CACHE_EFFECTIVE` + 受控失效 + 进程内重建命中 | E4/E4_FRC/SOAK 收据链（F 节） |

明确声明：**第 4 层生效不证明第 2 层生效**；.mxr 保存编译后整图程序
（mgx_ep.cc load_compiled_program），tuning-solution 库为另一实现
（compile_ops.cpp problem_cache），机制、键、失效行为均不同。

## F. Cold Start & Steady Performance

- 冷启动（create_s，独立进程均值）：yolo26n 43.64s → 缓存后 **0.86s**
  （−98.0%，n=3 缓存进程：0.78/0.77/1.02s）；rtdetr-l 115.87s → **3.88s**
  （−96.7%：3.86/3.72/4.05s）；同日复测 rep9（FRC 前夜同驱动态）复现。
- 失效对照：FRC create 43.90s（yolo，1.006×E0）/122.74s（rtdetr，1.059×E0），
  单次 attempt 内 .mxr 重写（yolo SHA `0ac9bb17→10f03f00`、+2427B、
  mtime=attempt 时刻；rtdetr 183121308→183129112B），批后 SHA 与 Phase-2
  保全副本不同（`CACHE_MANIFEST_PUBLIC.json` 两个 stage）。
- Session 重建：无缓存（E7）= 全量重编译 42.8–43.3s / 115.4–116.5s；
  **有缓存（SOAK）= 0.63–0.64s / 3.61–3.70s 命中**；重建后 first run
  0.015–0.032s（无 lazy 编译惩罚）。
- first_run（建会后首推理）：全臂 0.015–0.062s（无 lazy compile 大延迟）。
- 稳态（mean 门，15.6ms 量化说明见 THRESHOLDS_LOCK）：E0 2.91/12.63ms；
  E4 3.00/12.68ms（+2.63%/+0.09%，PASS）；soak 全程 2.87/12.82ms 一致。

## G. Correctness & Soak

- 每 CLEAN attempt 均过四重门：物理身份（LUID 钉选）+ providers 复查
  （静默 CPU 回退 exit 45 fail-closed）+ EP profile 节点>0 + 监督器从 npz
  独立重算 CPU by-name parity（atol/rtol 5e-3，阈值从未放宽）。
- Soak（首选缓存方案）：yolo26n 916.8s（15.28min，315,000 次推理）与
  rtdetr-l 943.5s（15.72min，72,500 次）有效稳态，各含 2 次会话重建
  （命中 0.64s/3.6s）作为周期 parity 检查点——首跑、两次重建、结尾全部
  parity PASS；0 超时、0 静默回退、干净退出。（另保留一次 877.7s 的
  yolo soak 如实标注为未达 900s 线。）
- 新 EP 问题：本轮无新的 AMDGPU 运行时异常；E1 的 trace 崩溃与 E8 的
  harness 缺陷均已入 RCA register（RCA2A-003/008/009）。
- DML 对照（yolo26n）：GPU_PASS_VALIDATED（231 DML 节点，parity 2.47e-03），
  仅作监督器/Oracle 正确性对照，不替代任何 AMDGPU 门禁；rtdetr-l DML
  parity 漂移仍属 RCA3 范围，未在本任务处理。

## H. RCA Register（RCA2A-001 … 009）

| # | 主题 | 状态 |
|---|---|---|
| 001 | WindowsApps glob ACL → EP 注册静默失败 → CPU 假跑 | ROOT_CAUSE_CONFIRMED（已修：Appx 回退 + fail-closed + providers 复查） |
| 002 | main 版 device_identity 无 luid_key → 选镜像设备 HIP 101 | ROOT_CAUSE_CONFIRMED（已修：luid_str Low DWORD 解析钉选） |
| 003 | MIGRAPHX_TRACE_BENCHMARKING=1/2 → 会话初始化 EP_FAIL | EFFECT_CONFIRMED（诊断受限，候选计数取崩溃前下界；机理 SUPPORTED 级） |
| 004 | MIGRAPHX_GPU_OPTIONS problem_cache_files → EP_FAIL | ROOT_CAUSE_CONFIRMED（fail-closed 崩溃非静默）→ PROBLEM_CACHE 重定向 `NOT_SUPPORTED_IN_SHIPPED_BUILD` |
| 005 | auto_save_problem_cache 每会话写只读包目录失败 | EFFECT_CONFIRMED（无害但恒定；tuning 解无法持久） |
| 006 | ORT_MIGRAPHX_CACHE_DIR → .mxr 落盘/命中/失效 | ROOT_CAUSE_CONFIRMED + 全链条闭环（含 FRC 重写与进程内重建命中） |
| 007 | shipped problem_cache.json = 四 gfx 只读调优库 | EFFECT_CONFIRMED（informational；gfx1100 672 解、cu_count=48） |
| 008 | worker→dml_session 调用契约不匹配 | ROOT_CAUSE_CONFIRMED（执行前发现修复，38→42 测试；无 GPU 收据污染） |
| 009 | 监督器对 DML 臂误要求 amdgpu 探针事件 | ROOT_CAUSE_CONFIRMED（修复+4 测试；rep0 收据保留、rep1_p2b 重跑 VALIDATED） |

## I. Evidence Manifest & CI

- 本地全量：42 份 attempt 收据（含 events.jsonl、FILE stdout/stderr、npz、
  profile）、`RESULTS_RAW.jsonl`×7 根、`EXPERIMENT_PLAN_LOCK`×7 +
  AMENDMENT×2、`ENVIRONMENT_LOCK`、`CAPABILITY_MATRIX`、`PROBES/`、
  `RCA_REGISTER.md`、`ANALYSIS/{G4_STATISTICS,SOAK_RESULTS,
  RESULTS_SUMMARY,LOCAL_RAW_EVIDENCE_INDEX}`、`THRESHOLDS_LOCK.json`、
  `CACHE_ARTIFACT_MANIFEST.json`（phase2 保全 + post-FRC 两 stage）。
- 公开精简包：`PUBLICATION_CANDIDATE_RCA2A_REDACTED/`（脱敏收据 42、
  docs 四件套、harness/测试、`REDACTION_SCAN.json` 0 违规、
  `MANIFEST.sha256`）。禁止项核查：无 .pt/.onnx/.mxr、无用户名/主机名/
  绝对个人路径（AIwork4me 路径→占位符）、无凭据；95GB 冻结与 RCA1 原始树
  仅以 SHA 锚点索引（`LOCAL_RAW_EVIDENCE_INDEX.json`）。
- CPU 测试：unit+adversarial **42/42 通过**；CI（若发布）：PR 精确 HEAD
  触发 cpu-contract workflow；`CPU_CI_DOES_NOT_ATTEST_GPU` 语义不变。
- 未执行/未完成实验（明示）：rtdetr-l E6（BLOCKED_BY_PRESET_CEILING）、
  E1 仅单模型单次（诊断目的达成）、yolo26l 未纳入本轮（资源边界）、
  PUBCONF 发布头重跑（等待 G7 网络）。

## J. Draft PR / Owner Decisions & 下一步

- 建议 Owner 审阅顺序：`RCA2A_REVIEW_INDEX.md` → ABLATION → receipts。
- 待 Owner 决策：① 是否接受 `.mxr` 缓存作为 gfx1100 生产建议
  （`ORT_MIGRAPHX_CACHE_DIR` 用户可写目录 + 首次调优 ~44/122s 一次性成本）；
  ② E2/E6 结论的措辞（不推荐默认）是否入 README；③ 上游功能缺口
  （problem-cache 重定向、binary-cache、trace 崩溃）仅本地保存的 Issue 草稿
  是否在未来单独任务提交；④ PUBCONF 发布头确认套件结果追认。
- 下一步（依网络）：fetch 成功 → worktree B → 分支提交 → PUBCONF 真头重跑
  → push → Draft PR（base=main）→ CI 观察；全程不合并、不 Ready、不上游 Issue。

---

## G7 发布状态（实时更新段）

- 2026-11-18 全天：github.com:443 git pack 传输中断（ls-remote/API 正常），
  40+ 次 fetch/clone 失败；发布流程在此门口**诚实停机**——不使用旧基线
  `cfc38e1`，不 force-push，不绕过安全核验。
- 状态：见本文件末尾追加的发布结果记录。

---

## G7 发布终态（2026-11-18 13:4x UTC 追加）

**结果：Draft PR 已创建（API 通道，用户授权）——发布成功。**

- **通道**：github.com git smart-http 端点全天中断（fetch/clone 40+ 次失败，
  含 HTTP/1.1、openssl、shallow 变体；曾两次传输至"仅剩数 KB"被 RST）。
  经用户明确授权改用 GitHub Git Data API（api.github.com 全天正常）：
  服务端从 main `4929752` 真实 tree 逐 blob 构建，本地以字节精确对象重建
  并以 **SHA 相等断言**四重验证（main tree `b4d84571`、T2/T3、签名 merge
  parent `4929752`、C1/C2/C3），全部命中；GPU 确认套件执行于与远端分支
  密码学同一的本地 worktree（sparse-checkout，逐文件 blob 校验 0 差异）。
- **分支**：`mission/gfx1100-rca2a-migraphx-autotune-cache`（base=main
  `4929752`，无 force、无历史改写，全部 fast-forward）。
- **提交链**：
  - `7392be11` C1：harness+tests+docs+脱敏证据（初版）。
  - `ef3faec9` C2：**RCA2A-010 修复**——PUBCONF 在 C1 上抓到 STAGING 镜像
    滞留的 v1 版 worker/probe（amdgpu import 崩溃，16 收据如实记录），
    对齐为 RUN_CODE_MANIFEST v2/v3 物理验证版本；**C2 即验证代码 HEAD**。
  - C3（证据）：PUBCONF 重跑收据 + 终版统计/文档。
- **PUBCONF 确认套件（C2 头，code_git_sha=ef3faec9, worktree_dirty=false）**：
  19 attempt = 18 CLEAN+GPU_PASS_VALIDATED + 1×E1 预期诊断崩溃；
  E0 43.1/124.2s、E2 20.2/98.2s、缓存命中 0.78s/3.9s（wall 4.1/10.1s）、
  FRC 46-48/126-130s、E6 518.7s、E8 DML PASS、soak 903.5s/924.6s
  （均 ≥900s，2 次命中重建，parity 3/3）——主实验全部数字在发布代码上复现。
- **Draft PR**：见仓库 Pull Requests（base=main，draft，不合并、不 Ready、不上游 Issue；本文件提交先于 PR 创建，PR 描述含完整链接与 commit 图）。
- **CI**：PR 精确 HEAD 触发 cpu-contract workflow；CPU CI 不证明 GPU。
- 遗留：本地主仓库 origin/main 引用仍为旧值（fetch 未成），不影响本次
  发布正确性（远端 HEAD 已由 API+ls-remote 双通道复核）。
