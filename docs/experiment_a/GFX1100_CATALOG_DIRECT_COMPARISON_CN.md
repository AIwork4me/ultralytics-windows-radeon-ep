# GFX1100 Catalog vs Direct Registration — Windows ML 2.x EP 提供链路对比实验（终版报告）

> Mission ID: `A_GFX1100_CATALOG_VS_DIRECT_REGISTRATION`
> 执行日期: 2026-11-20 · 报告语言: 中文（判定标签保持英文）
> 证据目录: `reports/experiment_a/`（7 个 JSON + MANIFEST.sha256 + REDACTION_SCAN.json）
> 复现脚本: `repro/windowsml_amdgpu_catalog_probe.py`、`repro/experiment_a_{inventory,canary_worker,cpu_worker,direct_canary,catalog_canary,negative_worker,negative,classify,assemble}.py`

---

## A. Executive Verdict（最终判定）

**`A_DIRECT_GPU_GO_CATALOG_NOT_READY`**

八级状态阶梯（互不替代，逐级实测）：

| # | 状态 | 结果 | 证据 |
|---|---|---|---|
| 1 | `EP_PACKAGE_INSTALLED` | ✅ TRUE | A2：`Microsoft.WinML.AMD.GPU.EP.Framework.2` 1.8.65.0（Store 签名框架包） |
| 2 | `EP_DLL_PRESENT` | ✅ TRUE | A2：`amdgpu-ep.dll` sha256 `cb7496b3…`（AMD 签名 Valid） |
| 3 | `DIRECT_EP_REGISTERED` | ✅ TRUE | A3：`register_execution_provider_library("AMDGPUExecutionProvider", dll)` 后 `get_ep_devices()` 出现该 EP |
| 4 | `DIRECT_AMDGPU_GPU_EXECUTED` | ✅ TRUE | A3：六项硬门禁全过（见 F 节） |
| 5 | `CATALOG_AMDGPU_LISTED` | ✅ TRUE | A1：官方 flat-C API 枚举 4 个 provider，含 `AMDGPUExecutionProvider` |
| 6 | `CATALOG_AMDGPU_READY` | ❌ FALSE | A1：ready_state=1（NotReady）；library_path 读取报 `ERROR_NOT_READY (0x80070015)` |
| 7 | `CATALOG_AMDGPU_REGISTERED` | ❌ FALSE | A4：未执行（NotReady + 未授权安装，无路径可注册） |
| 8 | `CATALOG_AMDGPU_GPU_EXECUTED` | ❌ FALSE | A4：`CATALOG_NOT_READY`，未借用 A3 的 PASS |

两维矩阵（BLOCKED ≠ FAIL）：

| Direct GPU | Catalog GPU | 意义 | 本机 |
|---|---|---|---|
| PASS | BLOCKED | DLL 能执行，但 Catalog 部署链路不完整 | **← 本机** |
| PASS | PASS | 两条路径都可用 | — |
| FAIL | PASS | Direct 注册环境存在独立问题 | — |
| FAIL | FAIL | 还需区分 DLL、驱动、设备及模型原因 | — |

**核心结论：本机 7900 XTX 能运行 AMDGPU EP，是因为已安装的 MSIX 框架包里的 `amdgpu-ep.dll` 可以被直接注册进 ORT 进程；Windows ML 2.x Catalog 虽然列出了 AMDGPUExecutionProvider，但它是 NotReady 状态、不向未打包（unpackaged）Python 进程提供 library path —— Catalog 链路在本机并未构成可用的提供方。**（详见文末最终问答。）

---

## B. Exact Git State（精确 Git 状态）

- 基线: `origin/main` = `d24f737`（Merge PR #5）
- 分支: `mission/experiment-a-catalog-vs-direct`（本 PR）
- 本 PR 内容: `repro/` 9 个实验脚本 + `reports/experiment_a/` 证据 + 本报告 + `src/radeon_ort/windows_ml_ep_catalog.py` 的 `_rederive_real_paths()` ACL 回退修复
- 无 force push、无历史 Receipt 改写；新 Run ID 全新建档：
  - Preflight: `A_EXP_A_PREFLIGHT_20261120T1140Z` / `…T1250Z` / `…T1300Z`（3 次，末次含 DLL 解析修复验证）
  - A1/A2/A3/A4/负向: 见 `reports/experiment_a/EXPERIMENT_A_RESULT.json` 的文件清单与哈希

## C. Physical Host（物理主机）

- GPU: **AMD Radeon RX 7900 XTX**（Navi 31，gfx1100），PCI DID `0x744C`（VEN `0x1002`）
- DXGI: LUID `0x00000000:0x00011BAF`（本次开机值 72623；LUID 每次开机会变 —— RCA1 时代为 72346，属正常现象，非身份漂移）
- 驱动: `32.0.21030.2001`（与 2026-11-17/18 历史证据一致）
- OS: Windows 11 专业版 Build `26300.9974`（26H2）· CPU i7-13700K · RAM 31.8GB
- 设备身份判定: `resolve_target_adapter(gfx=gfx1100)` DID 钉定通过；EP 设备 metadata LUID 与物理适配器 LUID 唯一匹配（72623），mirror 设备 LUID 123265 未被选中 → `device_identity = VERIFIED_GFX1100`

## D. Installed EP / DLL（已安装 EP 与 DLL 来源）

- MSIX 包: `Microsoft.WinML.AMD.GPU.EP.Framework.2`，版本 **1.8.65.0**（机器上唯一版本，无更高版本）
  - Publisher: `CN=Microsoft Corporation…`，PublisherId `8wekyb3d8bbwe`，**SignatureKind = Store**（Store 签名框架包；具体获取事件不可考 → 安装渠道记为 Store 签名来源，获取事件 UNKNOWN）
  - 依赖: 无（框架包自包含）
- `amdgpu-ep.dll`: sha256 `cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab`，548,304 字节
  - Authenticode: **Valid，签名者 CN=Advanced Micro Devices**；FileVersion `7.14.2610.2.80`
  - PE 静态导入仅 `KERNEL32.dll` → hip/MIGraphX 后端为运行时动态加载
  - **与 2026-11-18 RCA2A / gfx1201 诊断证据中的哈希完全一致（DLL 未变动）**
- MIGraphX 与 AMDGPU EP **同包共存**（`migraphx*.dll` 7 个文件在 `ExecutionProvider/` 下）
- 架构指向: 包内含 `custom_kernels_gfx1151.dll`（gfx1151 定制内核）；**未见 gfx1100 专属内核文件** —— gfx1100 支持由 hip/directx 后端通用路径承担（包清单层面可审查的 gfx1100 声明不存在，记为"由后端通用支持，包内无显式 gfx1100 内核文件"这一事实）
- ORT 兼容: `onnxruntime-windowsml 1.25.2.202605110140`（含 `register_execution_provider_library` + `get_ep_devices`）；注册别名 `AMDGPUExecutionProvider`（EP 规范名）

## E. Official Windows ML Catalog（官方 Catalog 只读探测）

- 探测包: **`windowsml==2.5.96`（PyPI 官方包）**，wheel sha256 `3ac86cee…`（与 PyPI 元数据一致）
  - 其内置 interop DLL `Microsoft.Windows.AI.MachineLearning.dll`（2.5.96.5615）**微软签名 Valid**（Windows App SDK）
  - 安装于独立用户级 venv（未动系统、未动驱动、未动 WindowsApps）
- 3 次独立探测（间隔 2s）结果**完全一致**（digest `40158b88…` 三次相同）：
  - `WinMLEpCatalogCreate` HRESULT = `0x0`（S_OK）
  - `WinMLEpCatalogEnumProviders` HRESULT = `0x0`（S_OK），Provider Count = **4**
  - `AMDGPUExecutionProvider`：ready_state = **1（NotReady）**，certification = 1（Certified），version/pfn 空，`WinMLEpGetLibraryPath` → **`ERROR_NOT_READY (0x80070015 设备未就绪)`**
  - `MIGraphXExecutionProvider`：NotReady / Certified；`OpenVINO`、`WebGpu`：NotPresent
- 对照面（wasdk 2.3.0 投影 + WindowsAppRuntime bootstrap，独立进程）：同样 4 个 provider、AMDGPU ready_state 原始值 1、library_path 空
- **枚举语义勘误（重要）**：2026-11-18 gfx1201 诊断记录将 ready_state=1 读作 "SUCCESS(1)"，系误用 `ExecutionProviderReadyResultState`（ensure_ready 的结果枚举）解读 `ready_state` 属性；正确枚举是 `ExecutionProviderReadyState`（READY=0 / NOT_READY=1 / NOT_PRESENT=2），两个 API 面一致。**本机 Catalog 的 AMDGPU 条目一直是 NOT_READY。**（两面在 certification 上有一处分歧：flat-C 报 1=Certified，投影报 2=Uncertified —— 原始值已各自存档，不合并解读。）

## F. Direct Registration Results（直接注册金丝雀）

`GFX1100_DIRECT_AMDGPU_GPU_EXECUTED` —— 六项硬门禁全部通过：

| 门禁 | 结果 | 数值 |
|---|---|---|
| actual_ep | ✅ | `AMDGPUExecutionProvider`（注册后 `get_ep_devices()` 确认） |
| target_ep_profile_nodes | ✅ | 2（profiler 归因 `AMDGPUExecutionProvider`） |
| cpu_profile_nodes | ✅ | 0 |
| numeric_parity | ✅ | allclose=True，max_abs = 2.44e-4（独立 CPU ORT 子进程，同名逐元素） |
| task_parity | ✅ | PT oracle（Ultralytics 8.4.215）：5/5 框匹配，precision=recall=1.0，IoU 均值 0.971 |
| device_identity | ✅ | DID `0x744C` + EP 设备 LUID 72623 唯一匹配物理适配器 |

- 模型: YOLO26n detect ONNX（本日新导出，opset 17，nms=False）sha256 `7c75d8b9…`
- 输入: bus.jpg letterbox 640（tensor sha256 `e2fedaea…`，manifest 含图像/张量/解码三重哈希）
- 注册方式: Get-AppxPackage（只读）解析 InstallLocation → 直接注册该 DLL（**BYO/MSIX 路径，非 Catalog**）

## G. Catalog Registration Results（Catalog 注册金丝雀）

`CATALOG_NOT_READY`（未安装、未执行 GPU、未借用 A3）：

- 前置条件检查: AMDGPU 已列出 ✓ → 检查 ReadyState → **1 = NotReady** → 按 Mission §7 与 AGENTS.md 规则 6（禁止系统变更），**不调用** `ensure_ready` / `ensure_ready_async` / `try_register`
- library path: 读取即报 `ERROR_NOT_READY`，无路径可注册
- 结论: Catalog 部署链路在本机止步于 "Listed but NotReady"；这一状态与 DLL 物理存在、Direct 可执行**互不推导**

## H. Negative Tests（负向与对照测试）

12/12 全部 fail-closed（`NEGATIVE_TESTS.json`）：

1. 错误 DID（gfx1201/gfx1151 请求）→ `WRONG_OR_AMBIGUOUS_DEVICE` 拒绝 ✓
2. 错误 DLL（kernel32 注册为 AMDGPU 库）→ 无 AMDGPU EP 设备/报错 ✓
3. 未注册 EP → 无 AMDGPU 节点（CPU 回退被 profiler 抓获，不发 PASS）✓
4. 注册但不能执行（钉死不存在 LUID 999999）→ **现场发现**：`rca2a_ep.create_amdgpu_session` 对未匹配 LUID **不抛异常**，回退 "Discrete=1 unique"（库级 fail-open 倾向）；证据门禁以 `ep_device_luid_matches=0` 拒发 GPU-PASS ✓（建议后续 PR 在库层加严）
5. Provider 列表含 AMDGPU 但 profiler 无 GPU 节点 → 门禁拒绝 ✓
6. CPU 冒充 GPU → 门禁拒绝（cpu_nodes>0）✓
7. 不同输入 SHA 的数值一致性 → allclose=False ✓
8. 不同 Run ID/SHA 交叉污染 → sha 不匹配被检出 ✓
9. 错误 Library Path → 注册报错、无 EP 设备 ✓
10. Catalog 空 + DLL 存在 → 分类保持 `CATALOG_EMPTY`，不与 DLL 存在性合并 ✓
11. Catalog 有条目 + DLL 不可用 → `BLOCKED_EP` 路径 ✓
12. Direct PASS + Catalog FAIL 组合 → `A_DIRECT_GPU_GO_CATALOG_NOT_READY` ✓（本机真实组合）

## I. Evidence Integrity（证据完整性）

- 所有证据文件过仓库脱敏扫描：**CLEAN**（`REDACTION_SCAN.json`，7 文件 0 泄漏）
- `MANIFEST.sha256` 覆盖全部 7 个发布 JSON
- 关键哈希: ONNX `7c75d8b9…` / 输入 `e2fedaea…` / PT `9b09cc8b…` / EP DLL `cb7496b3…` / Catalog digest `40158b88…`
- 每个子步骤独立进程 + 文件重定向输出 + 有界 watchdog（RCA1 纪律）；失败回执原样保留
- 仓库测试套件: **218 passed, 4 skipped**（本 PR 分支上）

## J. Comparison with 8060S and Next Actions（与 8060S 对比与后续行动）

与 gfx1151（8060S）主机的历史证据对比：

| 维度 | gfx1151 主机（历史） | 本机 gfx1100（本次实测） |
|---|---|---|
| Catalog 列出 AMDGPU | 否（wasdk 2.3.0 面只见 1.8 时代三 EP） | **是**（flat-C 2.5.96 + 投影两面均列出） |
| Catalog ready | —（未列出） | **NotReady**（两面一致） |
| MSIX DLL | 存在（含 gfx1151 定制内核） | 存在（哈希同源 `cb7496b3…`，含 gfx1151 内核文件） |
| Direct 注册执行 | 已验证（历史） | **已验证**（本次六门禁） |

后续行动建议：

1. **补齐 Catalog 链路的唯一缺口**：在一台可抛弃的测试机上（或获得明确授权后）对 Catalog 条目执行 `ensure_ready`，验证其能否落地 ready + 可注册 library path —— 这是区分 "微软尚未向本机推送 ready 包" 与 "链路设计上就不向 unpackaged Python 提供" 的决定性实验（本机按 AGENTS.md 规则 6 未做）。
2. `rca2a_ep.select_amdgpu_ep_device` 的 LUID 未匹配回退建议改为可选 fail-hard（本次负向测试 #4 的现场发现）。
3. 修复 main 里 preflight canary 的预存契约断裂（`_ep_worker.py` 已改用 `input_manifest`，`runner/preflight.py::_run_canary` 仍传 `feeds` → `KeyError`；本 PR 仅修了 DLL 解析 ACL 回退，canary 修复留待专项）。
4. 跟踪 `windowsml` 包演进（2.6.74rc0 / 2.7.x alpha 已在 PyPI）；在 8060S 主机镜像执行本 mission 的 A1/A4 以完成双主机对照。

---

## 最终问答（Mission 强制问题）

> **"7900 XTX 能运行 AMDGPU EP，是因为 Catalog 正常提供了它，还是因为现有 MSIX DLL 可以被直接注册？"**

**是因为现有 MSIX DLL 可以被直接注册。** 本机证据链：Catalog 在两个官方 API 面上都列出 `AMDGPUExecutionProvider`，但 ready_state=NotReady 且 `WinMLEpGetLibraryPath` 返回 `ERROR_NOT_READY` —— Catalog 既没有把它置为可用，也没有向我们这种无包标识（unpackaged）的 Python 进程暴露任何可注册路径；而 `Microsoft.WinML.AMD.GPU.EP.Framework.2`（1.8.65.0，Store 签名）中的 `amdgpu-ep.dll`（AMD 签名，`cb7496b3…`）经 `register_execution_provider_library` 直接注册后，在 gfx1100（DID `0x744C`，LUID 物理匹配）上完成了真实 GPU 执行并通过全部六项硬门禁。

判定边界（诚实声明）：**"Catalog 永远无法在本机提供该 EP" 不在本次证据范围内** —— 本机未授权执行 `ensure_ready`，无法排除"补装/升级后 Catalog 变 Ready 并给出 library path"的可能。以当前主机状态而言，答案是确定的（Direct 注册路径）；Catalog 链路的生命力属于 UNKNOWN，留待授权环境实验（J-1）。
