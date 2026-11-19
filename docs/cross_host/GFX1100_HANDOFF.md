# GFX1100（Radeon RX 7900 XTX / Navi 31）真机验收交接

本文件面向 **另一台物理主机** 的负责人。当前执行主机只有 gfx1151（Radeon 8060S），
因此 gfx1100 的一切物理结论必须由你在自己的机器上产出，本仓库不会代替你声明任何 PASS。

先读：`AGENTS.md`（失败即关闭、不可伪造、Receipt 不可改）、
`docs/GPU_EVIDENCE_CONTRACT.md`（证据契约）、`docs/HARDWARE_CLAIM_MATRIX.md`、
`docs/unified/CONTRACTS.md`。

> 设备 ID 的唯一权威来源是 `src/radeon_ort/device_identity.py` 的 `GFX_DEVICE_IDS`：
>
> | gfx | 架构 | 授权 DID |
> | --- | --- | --- |
> | gfx1100 | Navi 31 / RX 7900 XT(X) | `0x7448`, `0x744C` |
> | gfx1151 | Radeon 8060S | `0x1586` |
> | gfx1201 | Navi 48 / RX 9070 XT | `0x7550`, `0x7551` |
>
> gfx1100 主机的负向自检目标是 **gfx1151（0x1586）** 与 **gfx1201（0x7550/0x7551）**。
> 不要使用 `0x7480`（它不是 Navi 48，也从来不是 gfx1100）。

---

## 0. TL;DR：一条命令从 clone 到判决

在**真正的 gfx1100 主机**上，克隆并建好 venv（第 2 节）后：

```powershell
$env:PYTHONPATH="src"
.\.venv-winml\Scripts\python.exe -m runner.cross_host_validate --gfx gfx1100 --tier core --policy gpu-first --run-id GFX1100_CROSSHOST_<UTC>
```

- 退出码 `0` = `GFX1100_PHYSICAL_GO`；`1` = `GFX1100_NO_GO`；
  `2` = 非致命非 GO（`PHYSICAL_PENDING` / `CONDITIONAL_GO` / `BLOCKED_EP`）；
  `34` = `WRONG_PHYSICAL_DEVICE`（未做任何 GPU 工作）。
- 它按固定顺序执行 10 个 stage，每个 stage 输出
  `PASS / FAIL / BLOCKED / NOT_RUN / SKIPPED`，最后写入
  `reports\gfx1100\<run_id>\CROSS_HOST_VALIDATION.json`。
- 在 GPU stage 之前有 **硬门**：实时 DXGI 推导出的 gfx 必须等于 `--gfx`。
  本仓库当前主机是 gfx1151，所以在这里执行 `--gfx gfx1100` 只会得到
  `WRONG_PHYSICAL_DEVICE`（exit 34）和 `GFX1100_CROSS_HOST_HARNESS_READY`，
  绝不会产生 gfx1100 的物理 GO。

先做一次“只验计划、不碰 GPU”的自检：

```powershell
$env:PYTHONPATH="src"
.\.venv-winml\Scripts\python.exe -m runner.cross_host_validate --gfx gfx1100 --dry-run
```

在真正 gfx1100 主机上它应打印 `physical gate: PASS`；在 gfx1151 参考机上打印
`physical gate: WOULD REFUSE reason=WRONG_PHYSICAL_DEVICE`，两者都是有效计划（exit 0）。

---

## 1. 前置条件（先核对，不要猜）

| 项 | 要求 | 核对方式 |
| --- | --- | --- |
| Windows | Windows 11 24H2（build 26100）或更高；参考机为 25H2 26200.9974 | `winver` |
| GPU | gfx1100，PCI DID `0x7448` 或 `0x744C` | 第 3 节 preflight |
| 驱动 | 使用厂商/OEM 驱动；本次参考基线为 32.0.22018.5（2025-09-17，gfx1151 参考机）。**不要**为此安装非 OEM 驱动（AGENTS 规则 6） | `dxdiag` / preflight 记录 |
| ORT | `onnxruntime-windowsml 1.25.2`（`onnxruntime 1.25.2`）；providers 至少含 `DmlExecutionProvider` | 第 3 节 / 依赖探测 |
| Windows ML | python 包 `windowsml==2.5.96` | `pip show windowsml` |
| Python | 3.12.x（仓库要求 >= 3.11；参考 3.12.10） | `python -V` |
| 网络 | PyPI 可访问；GitHub 可访问（公司代理请自行配置，不要写进仓库） | `pip download --no-deps ...` |
| 权限 | 仅安装 Python 包/读取设备信息；不改驱动、固件、WindowsApps | — |

> 版本说明：上表是**已验证基线**，不是“唯一允许版本”。如果你记录到不同版本，
> 只要 `RUN_CONTRACT.json` 的 `environment` 块能把它们绑定到该 run_id，就仍然是
> 可复核的证据；换版本必须换新 `run_id`（见第 7 节）。

---

## 2. clone、venv 与安装

```powershell
git clone <repo-url> ultralytics-windows-radeon-ep
cd ultralytics-windows-radeon-ep
# 使用已评审的分支（不要 force-push，不要改写历史）
git checkout main

python -m venv .venv-winml
.\.venv-winml\Scripts\python.exe -m pip install -U pip
.\.venv-winml\Scripts\pip.exe install -e ".[winml]" pytest
.\.venv-winml\Scripts\pip.exe install "windowsml==2.5.96"

python -m venv .venv-export
.\.venv-export\Scripts\pip.exe install -U pip
.\.venv-export\Scripts\pip.exe install ultralytics torch onnx onnxruntime

# 可选：独立 CPU 交叉检查
python -m venv .venv-cpu
.\.venv-cpu\Scripts\pip.exe install onnxruntime numpy
```

**venv 目录名必须保持 `.venv-winml` 与 `.venv-export`。**
`runner.multi_input_oracle` 在仓库根目录按这两个固定名字查找解释器；
改名会让该 stage 明确失败（不会静默改用 CPU）。

每次开新终端：

```powershell
$env:PYTHONPATH="src"
```

安装完成后先确认：

```powershell
.\.venv-winml\Scripts\python.exe -c "import onnxruntime as ort, numpy, onnx; print(ort.__version__, ort.get_available_providers())"
```

期望 `1.25.2` 且 providers 至少包含 `DmlExecutionProvider`；AMDGPU EP 是否出现取决于
第 5.1 节的只读 catalog 探测，**不允许**用 `get_providers()` 冒充 GPU 证据。

### 2.1 模型资产（新 clone 里没有，必须显式准备）

`models/`、`*.pt`、`*.onnx` 都被 `.gitignore` 排除，**不会随 git 克隆下来**。
在跑验收前，最少需要 Tier A 的 9 个 checkpoint：

```
yolo26n.pt  yolo26n-seg.pt  yolo26n-sem.pt  yolo26n-depth.pt
yolo26n-cls.pt  yolo26n-pose.pt  yolo26n-obb.pt  yolo11n.pt  yolov8n.pt
```

1. 从 Ultralytics 官方公开发布获取对应 `.pt`（AGPL-3.0；catalog 记录
   `ultralytics_release=8.4.215`），放到 `models\pt\<checkpoint>`；
   **不要提交权重**。
2. 在 `.venv-export` 中导出静态 ONNX：

```powershell
.\.venv-export\Scripts\python.exe -m runner.export_models --export-python .\.venv-export\Scripts\python.exe --catalog catalog\model_catalog.json
```

   只导一个模型可用 `--model yolo26n --limit 1`。产物名形如
   `models\onnx\yolo26n.raw_b1_fp32_static.onnx`。
   注意：`--allow-asset-download` 目前对缺失的 PT 只会打印 `SKIP`（没有隐式下载）；
   必须先把 PT 放进 `models\pt\`。

3. 确认 fixture 存在：`data\fixtures\bus.jpg`、`data\fixtures\dota_p1053.jpg`。
   如需替换，用 `--image` / `--obb-image` 传入你自己的图片；跨机器比较时必须保持同一
   SHA，否则 benchmark/任务指标的输入绑定不同。

4. 先跑 dry-run：缺 `bench_onnx` 或 `tier_a_assets` 时它会 **FAIL**，
   不会带着缺资产去跑 GPU。这是有意的 fail-closed。

---

## 3. 硬件身份预检（第一道门）

```powershell
$env:PYTHONPATH="src"
.\.venv-winml\Scripts\python.exe -m runner.preflight --gfx gfx1100 --run-id GFX1100_PREFLIGHT_<UTC>
```

通过标准（全部满足才继续）：

- 日志/产物 label 为 `VERIFIED_GFX1100`；
- DID 为 `0x7448` 或 `0x744C`；LUID 非零（LUID 只是本次启动的佐证，不是永久身份）；
- 产物写到 `reports\gfx1100\GFX1100_PREFLIGHT_<UTC>\`
  （`HOST_FINGERPRINT.json`、`EP_INSTALL_AND_PROBE.json`、`DEVICE_IDENTITY.json`、`GPU_CANARY.json`）。

负向自检（必须在**同一台机器**上失败）：

```powershell
.\.venv-winml\Scripts\python.exe -m runner.preflight --gfx gfx1151 --run-id GFX1100_NEG_1151_<UTC>
.\.venv-winml\Scripts\python.exe -m runner.preflight --gfx gfx1201 --run-id GFX1100_NEG_1201_<UTC>
```

两者都必须以 `WRONG_OR_AMBIGUOUS_DEVICE` 失败，并且**不创建**任何 session。
若出现 `AMBIGUOUS_GFX_TARGET`（机器上有多张受支持 GPU），不要强行继续：
先记录全部 adapter，再向维护者确认目标卡。

> `runner.preflight` 会运行 canary session，因此只能在物理主机上跑；
> 只想验证计划而不碰 GPU 时用第 0 节的 `--dry-run`。

---

## 4. 一条命令验收（推荐路径）

### 4.1 参数（全部已用 `--help` 核对）

| 参数 | 取值 / 默认 | 说明 |
| --- | --- | --- |
| `--gfx` | `gfx1100`（必填） | 必须是本机架构 |
| `--tier` | `smoke` / `core`（默认）/ `full` | smoke=1 个 checkpoint；core=Tier A；full=全 catalog |
| `--policy` | `gpu-first`（默认）/ `amdgpu-only` / `dml-only` | 永不隐式 CPU |
| `--run-id` | 必填（`--dry-run` 除外） | 必须新建，且不能含其它 gfx 标签 |
| `--dry-run` | 开关 | 只验计划、打印命令，不建 session |
| `--report-root` | `reports` | 证据根目录 |
| `--winml-python` | 当前解释器 | 带 ORT+EP 的解释器 |
| `--export-python` | `.venv-export\Scripts\python.exe` | 导出/PT oracle 解释器 |
| `--timeout-s` | 900 | 单个子进程上限 |
| `--skip-soak` | 开关 | 跳过 soak（会标 SKIPPED） |
| `--json-out` | 默认写入 run 目录 | 结构化报告路径 |
| `--models` / `--image` / `--obb-image` | 可选 | multi-input oracle 范围/输入 |
| `--bench-onnx` / `--bench-input-npy` / `--bench-reps` / `--bench-samples` | 可选 | 基准输入与重复数 |
| `--soak-minutes` | core=5 / full=15 | soak 时长 |

### 4.2 三条推荐命令

```powershell
# 1) 冒烟：1 个 checkpoint + DML，最快跑通链路
.\.venv-winml\Scripts\python.exe -m runner.cross_host_validate --gfx gfx1100 --tier smoke --policy dml-only --run-id GFX1100_SMOKE_<UTC>

# 2) 核心验收：Tier A × 双 EP（推荐；AMDGPU 不可用时自动落到 CONDITIONAL_GO/BLOCKED_EP）
.\.venv-winml\Scripts\python.exe -m runner.cross_host_validate --gfx gfx1100 --tier core --policy gpu-first --run-id GFX1100_CROSSHOST_<UTC>

# 3) 完整目录（耗时长，受 --timeout-s 约束）
.\.venv-winml\Scripts\python.exe -m runner.cross_host_validate --gfx gfx1100 --tier full --policy gpu-first --run-id GFX1100_FULL_<UTC>
```

### 4.3 Stage 语义

| stage | 做什么 | 是否可以产生物理 GO 依据 |
| --- | --- | --- |
| `detect_host` | 实时 DXGI/DID + host fingerprint；计算硬门 | 否（身份门） |
| `check_dependencies` | 解释器/模块/模型/阈值/schema/目录可写性 | 否 |
| `ep_probe` | 只读 catalog 探测（`runner.probe_amdgpu_ep`） | 否 |
| `device_canary` | `runner.unified_canary`，逐 EP、LUID/DID、profiler 节点、parity | 是 |
| `tier_a` | `runner.run_matrix`，Tier A × 所选 EP | 是（核心） |
| `multi_input_oracle` | `runner.multi_input_oracle`，多输入任务正确性 | 是 |
| `benchmarks` | `runner.bench_repeats`，ONNX/input 哈希绑定的延迟 | 性能证据 |
| `soak` | `runner.soak`，单 GPU 进程、心跳/watchdog | 稳定性证据 |
| `evidence_freeze` | 脱敏 -> finalize -> 兼容矩阵 -> manifest -> verify | 否（但它必须 PASS 才能 GO） |
| `final_verdict` | 按第 8 节词汇给出分层判决 | 汇总 |

### 4.4 必须知道的两条约束

1. **工作树必须干净。** `run_matrix` 会把 `worktree_dirty=true` 写进
   `RUN_CONTRACT.json`，而严格审计拒绝用它出具 GPU PASS
   （见 `docs/GPU_EVIDENCE_CONTRACT.md` §9.2）。验收前先提交/stash 自己的改动；
   不要为了“跑绿”而绕过这条规则。
2. **新 run_id。** 同一 `run_id` 已存在 `RUN_CONTRACT.json` 或 receipt 时，
   入口会直接拒绝（exit 2），不会覆盖、不会续写。续跑用 `runner.resume_matrix`
   （第 5.7 节），且合同必须完全一致。

---

## 5. 手动分步（等价命令，排障用）

> 所有命令都先 `$env:PYTHONPATH="src"`，在仓库根目录执行。
> 每条命令的 flag 都已用 `python -m runner.<mod> --help` 核对。

### 5.1 官方 Windows ML EP 接入（只读探测）

```powershell
.\.venv-winml\Scripts\python.exe -m runner.probe_amdgpu_ep --out reports\gfx1100\<run_id>\EP_PROBE.json --gfx gfx1100
```

判读 `state`：

| state | 含义 | 下一步 |
| --- | --- | --- |
| `EP_REGISTERED` / `GPU_EXECUTED` | 可用 | 进入 5.2 |
| `EP_NOT_READY` | catalog 有但未就绪 | 对目标 provider 调 `ensure_ready_async()` 后重跑 |
| `CATALOG_EMPTY` | 系统没有为该设备提供 EP | 记录 `BLOCKED_EXTERNAL`；不要从非官方渠道拿 DLL |
| `NOT_INSTALLED` | 缺 `windowsml` 绑定 | 安装 `windowsml==2.5.96` 后重跑 |

正确注册路径是 `provider.ensure_ready_async()` 后
`ort.register_execution_provider_library(provider.name, provider.library_path)`；
**不要**用 `EnsureAndRegisterCertifiedAsync()` 去注册 Python ORT。

### 5.2 单模型 canary

```powershell
# EP 精确 canary（tiny conv；DID/LUID + profiler 节点 + parity）
.\.venv-winml\Scripts\python.exe -m runner.unified_canary --ep dml --gfx gfx1100 --out reports\gfx1100\<run_id>\CANARY_DML.json
.\.venv-winml\Scripts\python.exe -m runner.unified_canary --ep amdgpu --gfx gfx1100 --out reports\gfx1100\<run_id>\CANARY_AMDGPU.json

# 统一入口 canary（真实 YOLO26n 单模型）
.\.venv-winml\Scripts\python.exe -c "import numpy as np; np.savez('canary_input.npz', images=np.zeros((1,3,640,640), dtype=np.float32))"
.\.venv-winml\Scripts\python.exe -m runner.unified_predict --model models\onnx\yolo26n.raw_b1_fp32_static.onnx --inputs-npz canary_input.npz --policy prefer_amdgpu_allow_dml --gfx gfx1100 --out reports\gfx1100\<run_id>\UNIFIED_CANARY.json --run-id GFX1100_CANARY_<UTC>
```

`unified_predict` 退出码：0 = GPU_FULL/GPU_FULL_DML/CPU_ONLY_USER_AUTHORIZED；
2 = 参数契约错误；30 = 阻塞；31 = AMDGPU 失败；34 = 设备/身份阻塞。
结果里必须有 `target_ep_profile_nodes > 0`、`cpu_profile_nodes == 0`、
`numeric_parity` 全 allclose；`get_providers()` 不是证据。

### 5.3 Tier A × 双 EP（9 checkpoints × 2 EP = 18 例）

```powershell
.\.venv-winml\Scripts\python.exe -m runner.run_matrix --gfx gfx1100 --tier core --ep both --run-id GFX1100_R8_<UTC> --reports-root reports --winml-python .\.venv-winml\Scripts\python.exe --export-python .\.venv-export\Scripts\python.exe --image data\fixtures\bus.jpg --obb-image data\fixtures\dota_p1053.jpg --with-negative
```

- `runner.run_matrix` 的 `--tier` 只接受 `core` / `full`；smoke 等价于 `--tier core --limit 1`。
- 9 个 Tier A checkpoint：yolo26n Detect/Segment/Semantic/Depth/Classify/Pose/OBB、
  yolo11n Detect、yolov8n Detect。
- 每个 `(model, EP)` 必须被严格校验为 `PASS_GPU_EXECUTED_FULL` 才计入；
  退出码 0 = 全部 PASS，1 = 证据伪造/真实失败，2 = 有阻塞/未执行。
- AMDGPU 仍 `CATALOG_EMPTY` 时，用 `--ep dml` 单独完成 DML 回归，
  并在报告中把 AMDGPU 标成 `BLOCKED`（不要伪造成 PASS）。

### 5.4 多输入任务正确性（7 任务 × 4 变体）

```powershell
.\.venv-winml\Scripts\python.exe -m runner.multi_input_oracle --gfx gfx1100 --ep dml --out reports\gfx1100\<run_id>\MULTI_INPUT_DML.json --image data\fixtures\bus.jpg --obb-image data\fixtures\dota_p1053.jpg --timeout-s 900
```

AMDGPU 可用时再跑 `--ep amdgpu`。每个变体的 Detect P/R + IoU、Segment mask IoU、
Semantic mIoU、Depth rel MAE、Classify Top-1/Score Drift、Pose OKS、OBB Rotated IoU
都必须给出数值，不允许空指标。

### 5.5 Benchmark 与 Soak

```powershell
.\.venv-winml\Scripts\python.exe -m runner.bench_repeats --onnx models\onnx\yolo26n.raw_b1_fp32_static.onnx --input-npy reports\gfx1100\<run_id>\inputs\<...>.npy --ep dml --gfx gfx1100 --reps 3 --warmup 5 --samples 50 --out reports\gfx1100\<run_id>\BENCH_DML_yolo26n.json --telemetry-out reports\gfx1100\<run_id>\BENCH_DML_telemetry.jsonl

.\.venv-winml\Scripts\python.exe -m runner.soak --ep dml --gfx gfx1100 --minutes 15 --onnx models\onnx\yolo26n.raw_b1_fp32_static.onnx --input-npy reports\gfx1100\<run_id>\inputs\<...>.npy --reports-root reports --run-id GFX1100_R8_<UTC> --tag yolo26n_dml
```

- benchmark 必须记录 ONNX/input SHA；没有 SHA 的旧数据只能标 `UNBOUND_LEGACY_OUTLIER`。
- soak 单 GPU 进程、有心跳与 no-progress watchdog、有上限；
  `SOAK_<ep>__<tag>.json` 已存在时会拒绝覆盖（不可变）。

### 5.6 Negative injection

```powershell
.\.venv-winml\Scripts\python.exe -m runner.negative_injection --gfx gfx1100 --reports-root reports --run-id GFX1100_NEG_<UTC>
```

必须全部 fail-closed：错误 DID、缺失 EP、错误 DML ordinal、CPU 冒充 GPU、
静默 CPU 回退、错误 input/model SHA、篡改 receipt、不完整 task oracle、假设备绑定。

### 5.7 中断后续跑

```powershell
.\.venv-winml\Scripts\python.exe -m runner.resume_matrix --run-id GFX1100_R8_<UTC> --gfx gfx1100 --tier core --ep both --winml-python .\.venv-winml\Scripts\python.exe --export-python .\.venv-export\Scripts\python.exe --image data\fixtures\bus.jpg --with-negative
```

只有合同完全一致的已完成 case 才会被跳过；环境/代码/阈值/设备身份任一漂移都会
以 `ENVIRONMENT_CHANGED` / `CODE_STATE_DIRTY` / `THRESHOLDS_CONFIG_CHANGED` /
`DEVICE_IDENTITY_CHANGED` 拒绝，并要求新 run_id。

---

## 6. 证据冻结与校验

```powershell
.\.venv-winml\Scripts\python.exe -m runner.redact_evidence --run-dir reports\gfx1100\<run_id>
.\.venv-winml\Scripts\python.exe -m runner.finalize_run --run-dir reports\gfx1100\<run_id> --host gfx1100 --catalog catalog\model_catalog.json
.\.venv-winml\Scripts\python.exe -m runner.build_compatibility_matrix --run-dir reports\gfx1100\<run_id> --catalog catalog\model_catalog.json
.\.venv-winml\Scripts\python.exe -m runner.verify_receipts --run-dir reports\gfx1100\<run_id>
.\.venv-winml\Scripts\python.exe -m runner.redact_evidence --run-dir reports\gfx1100\<run_id> --check
```

顺序固定：先脱敏，再冻结，最后校验；冻结后不改内容。
`runner.cross_host_validate` 会自动完成上述顺序，并额外生成：

- `CROSS_HOST_MATRIX.json` / `CROSS_HOST_MATRIX.csv`：按 receipt 自己的
  `host.gfx` 聚合的跨架构视图（gfx1100 receipt 会出现在 gfx1100 列）；
- `CROSS_HOST_VALIDATION.json`：协调器报告（含每个 stage 的状态/命令/退出码）。

注意：`runner.build_compatibility_matrix` 生成的 `compatibility_matrix.csv` 是
**固定列（以 gfx1151 为主机）的历史产物**，在 gfx1100 主机上那几列会显示 NOT_RUN；
不要拿它当 gfx1100 的证据。gfx1100 的权威证据是 `receipts/`、`manifest.sha256`、
`verify_receipts` 的 ALL_OK，以及 `CROSS_HOST_MATRIX.*`。
`CROSS_HOST_VALIDATION.json` 在 manifest 之后写入，因此它是协调器摘要，
不在冻结 manifest 内。

---

## 7. 跨架构隔离（硬规则）

1. **新架构 = 新 run_id。** 旧 gfx1151/gfx1100 的 receipt、RUN_CONTRACT、模型缓存、
   soak 输出都不能被复用或重新贴标签。`RUN_CONTRACT.json` 绑定
   `gfx + DXGI vendor/device ID + host_id + environment`；任一变化都必须新 run_id。
2. **run_id 不得含其它 gfx 标签。** 例如在 `--gfx gfx1100` 下使用
   `GFX1151_*` / `GFX1201_*` 会被入口直接拒绝（`RUN_ID_CROSS_ARCH_TAG`）。
3. **receipt/contract 跨架构检查。** `src/radeon_ort/cross_host.py` 会拒绝
   `CROSS_ARCH_RECEIPT_REFUSED`、`CROSS_ARCH_CONTRACT_REFUSED`、
   `RECEIPT_DEVICE_ID_MISMATCH`、`RECEIPT_HOST_LABEL_FORGED`。
4. **禁止 CPU 静默回退。** 目标 EP 不可用时是 `BLOCKED_EP`，不是悄悄跑 CPU。
5. **合成测试不是物理 PASS。** 0x7448/0x744C 的合成正向测试只证明代码不会错选；
   它不能替代本机 receipt。

---

## 8. 最终判决（精确字符串，分层）

| 判决 | 含义 | 需要什么证据 |
| --- | --- | --- |
| `GFX1100_CROSS_HOST_HARNESS_READY` | 计划/harness 就绪；**没有**物理声明 | dry-run 通过；或本机物理门拒绝 |
| `GFX1100_PHYSICAL_PENDING` | 还没有完整物理运行 | 无（或部分 stage 未跑） |
| `GFX1100_PHYSICAL_GO` | 所选范围内全部 stage 通过 | Tier A 双 EP 严格 PASS + canary + multi-input + bench + soak + freeze/verify |
| `GFX1100_CONDITIONAL_GO` | 一个 EP 完整，另一个有明确外部阻塞 | 例如 DML 全 PASS，AMDGPU `BLOCKED_EP` |
| `GFX1100_BLOCKED_EP` | 没有可执行的目标 EP | `ep_probe` 阻塞且无 CPU 回退 |
| `GFX1100_NO_GO` | 证据/任务失败 | 任一 stage FAIL |

> `GFX1100_AMDGPU_AND_DML_CORE_GO` **不是**合法判决字符串，旧文档中的这个标签已废弃；
> 双 EP 全通过时使用 `GFX1100_PHYSICAL_GO`。
> `GFX1100_CONDITIONAL_GO` 不等于 GO；是否放行由评审决定，并在报告中写明哪个 EP 阻塞。

把最终判决写进 `docs/cross_host/GFX1100_RESULT_CN.md`（新文件），
并回填 Issue #1 或新 Issue。

---

## 9. 常见阻塞与处理

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `WRONG_PHYSICAL_DEVICE` | `--gfx` 与本机 DID 不符 | 核对 DID；不要改 `--gfx` 硬凑 |
| `AMBIGUOUS_GFX_TARGET` | 多张受支持 GPU | 记录 adapter；确认目标卡；不要猜 |
| `BLOCKED_EP` / `NOT_INSTALLED` / `CATALOG_EMPTY` / `EP_NOT_READY` | EP 未安装/未就绪 | 只读探测 + 官方渠道；记录 `BLOCKED_EXTERNAL` |
| `CODE_STATE_DIRTY` | 工作树有未提交改动 | 提交/stash 后重跑；不要伪造 clean |
| `ENVIRONMENT_CHANGED` | ORT/WindowsML/Python/OS 变了 | 新 run_id |
| `THRESHOLDS_CONFIG_CHANGED` | 改了 `configs/thresholds.yaml` | 新 run_id；旧 receipt 不重新 bless |
| `RECEIPT_DEVICE_MISMATCH` / `CROSS_ARCH_*` | 复用了别的 gfx 的证据 | 新 run_id，重新生成 |
| `run_matrix` exit 2 | 有阻塞/未执行 | 看 `MATRIX_EXIT.json` 的 classification |
| `run_matrix` exit 1 | 证据伪造/真实失败 | 保留失败记录，修复后新 run_id 重跑 |

---

## 10. 交回什么

- 新分支 `mission/gfx1100-physical-validation`，Draft PR 到 `main`；
- PR 描述包含：主机身份（DID/LUID/驱动/OS）、新 `run_id`、Tier A 9×2 结果、
  multi-input 指标、benchmark 的 ONNX/input SHA、soak 结论、
  `CROSS_HOST_VALIDATION.json` 的 final verdict；
- 不要 force-push，不要改/删 receipt，不要删除失败记录；
- 如果 `git push` 因网络不可用，可让维护者用 API 推送；提交对象可逐字节复现。

### 验收清单

- [ ] `preflight --gfx gfx1100` => `VERIFIED_GFX1100`，DID `0x7448/0x744C`
- [ ] 负向 gfx1151 / gfx1201 => `WRONG_OR_AMBIGUOUS_DEVICE`
- [ ] `cross_host_validate --gfx gfx1100 --dry-run` => `physical gate: PASS`
- [ ] 工作树干净（无 dirty contract）
- [ ] 新 run_id，且不含其它 gfx 标签
- [ ] Tier A 双 EP 严格 PASS（或明确记录哪个 EP 阻塞）
- [ ] multi-input / benchmark / soak 全部有数值且哈希绑定
- [ ] `verify_receipts` => `ALL_OK`
- [ ] 判决字符串属于第 8 节词汇
