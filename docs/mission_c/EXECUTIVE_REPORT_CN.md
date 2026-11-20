# Mission C 最终工程报告 — gfx1151 / Radeon 8060S AMDGPU EP 根因闭环

**Mission ID:** `C_GFX1151_AMDGPU_EP_ROOT_CAUSE_CLOSURE`
**Run ID:** `C_GFX1151_AMDGPU_20261120T2153Z`
**Host:** HP ZBook Ultra G1a 14 · Ryzen AI MAX+ PRO 395 · Radeon 8060S (gfx1151) · DID `0x1586` · `host_id=host_53f62d2ac2c6`
**设备证据:** `PCI\VEN_1002&DEV_1586&SUBSYS_8D01103C&REV_D1` · Driver `32.0.22018.5` · Windows 11 25H2 `26200.9974`
**证据包:** [`reports/mission_c/`](../../reports/mission_c/)（`MANIFEST.sha256` + 245 项 verifier 检查 `ALL_OK`）

---

## A. Executive Verdict（核心判决）

### 主判决

```
C_CATALOG_BLOCKER_CONFIRMED_ROOT_CAUSE_UNVERIFIED
```

**同时成立的工程事实（必须一起读，不能只读其中一句）：**

| 项 | 状态 |
| --- | --- |
| AMDGPU EP 在**本机**现在能工作吗 | **不能** |
| 阻塞层是否已确认到具体 API/调用阶段 | **是**（`ExecutionProviderCatalog` 返回 `S_OK` 且 0 条目；`FindProvider` 返回 `0x80070490`） |
| 阻塞的内部根因（微软侧分发决策）是否确认 | **否**（`UNVERIFIED`，需要 ETW/管理员权限或微软答复） |
| 是否向微软提交了可复现问题 | 以 `C7_ESCALATION_STATUS.json` 的 `status`/`posted` 为准（见 §K；本文件不预先声明已提交） |
| DirectML 是否受损 | **否**（本机 DML canary 物理 PASS） |
| gfx1151 kernel 是否支持 | 本机无法证明；**另一台 gfx1151 主机**已物理执行同一 `amdgpu-ep.dll`（跨主机上下文，不作为本机结论） |

> **一句话结论：** 本机的 AMDGPU EP 不是在加载/驱动/内核阶段失败，而是**在"微软官方 EP 目录（Catalog）根本没有向这台设备提供任何条目"这一步就已经没有对象可供安装**。这一层已被 3 种模式 × 3 条 API 路径 × 2 套 API 表面独立复现并确认为**直接阻塞层**；但"微软为何这样判定"属于未公开的设备分发逻辑，本地无权限观测，已按规则判定为 `UNDERLYING_ROOT_CAUSE = UNVERIFIED`，并据此发起上游求助。

### 为什么不是更"漂亮"的结论

`C_GFX1151_AMDGPU_PHYSICAL_GO` 需要真机 AMDGPU 推理通过全部门禁。本机**不存在** `amdgpu-ep.dll`，Catalog 也**不提供**安装对象，因此任何 `PASS` 都只能来自伪造或 CPU 冒充 —— 本 Mission 明确拒绝这两者。`GPU_CANARY.json` 中 AMDGPU worker 的返回码是 `34 (BLOCKED_EP)`，而不是 `0`。

---

## B. Exact Git State

| 项 | 值 |
| --- | --- |
| 共同祖先（main） | `767efd71d6106be2fe63895743be8ff6bf652b2f` |
| **本 PR 的 base** | `mission/experiment-b-gfx1151-amdgpu-root-cause` @ `24e4d22`（**stacked PR**：A/B 比较器与 Experiment B 证据只存在于该分支；#14 合入后可 retarget 到 `main`） |
| Branch | `mission/c-gfx1151-amdgpu-root-cause-closure` |
| HEAD | `git log -1` 与 [PR #16](https://github.com/AIwork4me/ultralytics-windows-radeon-ep/pull/16) 的 head SHA（本文件随该提交一同发布） |
| PR | **Draft PR #16** — <https://github.com/AIwork4me/ultralytics-windows-radeon-ep/pull/16> |
| CPU CI | PR #16 的 `cpu-ci` checks（compile / ruff 非阻断 / 434 tests / Mission C 证据自检 `verify_mission_c_evidence.py`）；结论以 checks 页面为准。`CPU_CI_DOES_NOT_ATTEST_GPU=1` |
| 历史 PR | #14（Experiment B，本 PR 的 base）、#15（Experiment A，`CONFLICTING`，证据冻结未改写） |
| 禁止动作 | 未 force-push、未重写历史、未编辑任何既有 receipt |

**本 Mission 未做：** force-push、历史重写、编辑任何既有 receipt、跨主机搬运证据。

---

## C. Host and Software Environment

| 项 | 观测值 | 证据 |
| --- | --- | --- |
| OS | Windows 11 25H2 `26200.9974` | `C0_BASELINE_AUDIT.json` |
| GPU | AMD Radeon(TM) 8060S Graphics / `gfx1151` | DXGI + PnP 交叉 |
| DID / VID | `0x1586` / `0x1002` | 同上 |
| PnP | `PCI\VEN_1002&DEV_1586&SUBSYS_8D01103C&REV_D1` | 同上 |
| LUID | `0x00000000:0x000154E4` | DXGI |
| Driver | `32.0.22018.5`（2025-09-17，HP OEM） | WMI + DXGI UMD |
| Windows ML 绑定 | `windowsml==2.5.96`（官方文档最低版本） | pip |
| OS WinML 运行时 | `WindowsWorkload.WinMLShared.7.0 1.2610.1050.0` | Appx |
| ORT | `onnxruntime-windowsml 1.25.2.202605110140` | 运行时 |
| ORT providers | `DmlExecutionProvider`, `CPUExecutionProvider` | `get_available_providers()` |
| Python | 3.12.10 (CPython x64) | 运行时 |
| 权限 | **非管理员**（Medium integrity，Administrators 为 deny-only） | `whoami /groups` |
| 会话 | 远程会话（存在 `Microsoft Remote Display Adapter`） | PnP |

---

## D. Experiment A/B Evidence Reconciliation

### D.1 真实差分表（由 `tools/experiment_b_ab_compare.py` 生成，**首次真正导入 A 的证据**）

| 字段 | gfx1100（Experiment A / PR #15） | gfx1151（本机） |
| --- | --- | --- |
| OS build | **26300.9974（26H2）** | **26200.9974（25H2）** |
| Driver | `32.0.21030.2001` | `32.0.22018.5` |
| Windows ML | `2.5.96` | `2.5.96`（相同） |
| ORT | `1.25.2` | `1.25.2`（相同） |
| Interop DLL SHA256 | `8205a572…d7be0` | `8205a572…d7be0`（**逐字节相同**） |
| Catalog create / enum HRESULT | `0x00000000` / `0x00000000` | `0x00000000` / `0x00000000`（相同） |
| **Catalog 条目数** | **4** | **0** |
| Catalog 中的 AMDGPU | `NotReady(1)` | 不存在 |
| EP MSIX | `Microsoft.WinML.AMD.GPU.EP.Framework.2 1.8.65.0` | 机器级不存在 |
| `amdgpu-ep.dll` | `cb7496b3…f8ab` | 不存在 |
| 直接注册 | `PASS` | `BLOCKED_NO_DLL` |
| GPU profiler 节点 | **2**（CPU 节点 **0**） | 0（无会话） |
| 数值/任务 parity | `PASS` / `PASS_TASK` | `NOT_RUN` |

### D.2 判决

工具输出 `primary_case = B2`，`verdict = HOST_SPECIFIC_CATALOG_FILTER_OR_DEPENDENCY`。

**关键推论（本 Mission 新增，且可复核）：**

1. 两台机器的 **Catalog API 二进制逐字节相同**，OR/HResult 相同 → 差异不是 SDK 版本，而是**同一段代码读到的系统输入不同**。
2. A 主机 Catalog 有 4 条且**包含 OpenVINO（Intel 厂商）与 WebGPU** → Catalog **不是**"按本机 GPU 过滤后的结果"，否则 Intel 的 EP 不会出现在 AMD 主机上。因此"8060S 被设备兼容性过滤掉了"**不能**单独解释"整表为 0"。
3. 本机连 WebGPU（文档要求仅为"任意 DX12 GPU"）也没有 → 说明**条目在到达设备过滤之前就没有生成**（本机 DML 可在同一驱动上执行，DX12 能力成立）。

### D.3 对旧报告的修正（不重写冻结证据，只加独立 Correction）

| 旧表述 | 修正 | 状态 |
| --- | --- | --- |
| PR #14：「没有官方 AMDGPU EP 制品发布」 | 官方 **Store 产品确实存在**：`Microsoft.WinML.AMD.GPU.EP.Framework.2` → ProductID `9PNKGVD4CPCV`；`EP.2` → `9NMRQLX4HLSK`。缺的是**分发/投放**，不是发布 | `CORRECTED` |
| PR #14：「gfx1151 兼容性 UNVERIFIED」 | 保持 `UNVERIFIED`（本机无法测），但补充：**另一台** gfx1151 主机（`SUBSYS_17141043`，驱动 `32.0.22032.6002`）跑通了同一 DLL。属跨主机上下文，**不移植为本机结论** | `QUALIFIED_NOT_TRANSPLANTED` |
| PR #15：「Catalog listed 但 NotReady → 部署链未完成」 | A 的 `NotReady` 语义是"已安装但未加入该应用的运行时依赖图"，**不是**"可下载"。A 能列出 AMDGPU 是因为包**已机器级预装** | `CLARIFIED` |
| PR #14 的 A/B | `A_EVIDENCE_PENDING`（工具此前从未真正导入 A） | `FIXED`（见 §H） |

### D.4 证据完整性缺陷（如实记录，不修冻结文件）

| ID | 严重度 | 发现 |
| --- | --- | --- |
| C-INT-1 | P1 | Experiment A 的 `MANIFEST.sha256` **8/8 条全部与已提交 blob 不匹配**（已用 `git cat-file blob` 逐字节复核，排除换行差异）。A 的数值只能从 JSON 正文复读，不能从清单背书。 |
| C-INT-2 | P2 | A 的 `CATALOG_REGISTRATION_CANARY.json` 存在 UTF-8/GBK 乱码（仅文本，数值完好）。 |
| C-INT-3 | P0 | `tools/experiment_b_ab_compare.py` 在**第一次**吃进真实 A 证据时崩溃（`str > int`），且没有 A 的 schema 抽取器。 |

---

## E. Catalog Internal Diagnostics

### E.1 API 层面（本机实测）

| 探测 | 结果 |
| --- | --- |
| `WinMLEpCatalogCreate` | `0x00000000` (S_OK) |
| `WinMLEpCatalogEnumProviders` | `0x00000000`，**0 条** |
| `WinMLEpCatalogFindProvider("AMDGPUExecutionProvider")`（不带 PFN） | `0x80070490`（元素未找到） |
| 同上，带 `Microsoft.WinML.AMD.GPU.EP.Framework.2_…` | `0x80070490` |
| 同上，带 `MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8_…` | `0x80070490` |
| **`WinMLEpCatalogCreateWithMode(mode)`**，mode = 0/1/2 | 全部 `S_OK` + **0 条** |
| mode = 3/4/5 | `0x80070057`（E_INVALIDARG，说明枚举只有 0..2） |
| WinAppSDK 2.3 投影（`wasdk` bootstrap） | 0 条 |
| 官方 2.5.96 flat-C | 0 条 |

> `WinMLEpCatalogCreateWithMode` **未出现在公开发布的 `WinMLEpCatalog.h` 头文件**中，却存在于同一份微软签名二进制内 —— 这是一个可提给微软的诊断缺口。

> **残差缺口（如实记录）：** 任务要求的"官方 C/C++ 与 C# 最小样例"控制**未构建**：本机 `cl`/`cmake`/`dotnet`/`csc` 全部不存在，也没有 Visual Studio；安装 Windows SDK/MSVC 属于系统变更且未获授权。因此该项为 `NOT_RUN`（见 `C1_API_RUNTIME_DIFFERENTIAL.json:native_controls.compiled_sample_controls`）。flat-C 探针直接调用的是与 C 样例**同一组导出函数**（结构体布局取自官方头文件），WinRT 一侧另有 4 套 WinAppSDK 投影覆盖，但严格来说这一控制项仍未闭合。

### E.2 元数据来源（本 Mission 的核心新发现）

官方 2.5.96 运行时 DLL 内部**内嵌了一张 EP 目录表**（28 条），每条包含：

```json
{ "Name": "AMDGPUExecutionProvider",
  "SupportedDeviceKinds": ["GPU"],
  "Packages": [{
      "PackageFamilyName": "Microsoft.WinML.AMD.GPU.EP.Framework.2_8wekyb3d8bbwe",
      "MinimumPackageVersion": "1.8.64.0",
      "Product": { "ProductID": "9PNKGVD4CPCV" },
      "SupportedVendorIDs": ["0x1002"],
      "UnsupportedDeviceIDs": [ ... 约 80 个旧 DID ... ] }] }
```

机器可读版本见 `C1_CATALOG_INTERNALS.json`（含 28 条全表、`catalog_table_digest`）。

**由此得到三个可复核结论：**

1. **`0x1586` 不在 `UnsupportedDeviceIDs` 中**，且厂商 `0x1002` 受支持 → 公开目录元数据**没有**排除 Radeon 8060S。空表不能由"设备黑名单"解释。
2. AMD GPU EP 有 **2 个官方 Store 产品**（`EP.2` 与 `EP.Framework.2`），即"官方发布"为真、"向本机投放"为假。
3. 二进制内引用了 `Windows.ApplicationModel.Store.Preview.InstallControl` 与 `DXCoreCreateAdapterFactory`，并带有内部遥测原因串：`FindAllProvidersTest::reason::no_store`、`DownloadExecutionProvidersTest::reason::no_target_package / not_acquirable` 等 —— **这些正是把"为什么是 0"区分开的开关点**。

### E.3 ETW

`BLOCKED_ADMIN_DEPENDENCY`。本会话 **非管理员**，官方文档明确要求管理员权限才能启停 ETW。
`C3_ETW_CAPTURE_MANIFEST.json` 已给出完整、可直接执行的采集计划（wprp/rundown/wpaProfile 三者来源与 SHA 记录要求、20 秒有界采集、脱敏与私有 ETL 不入境规则）。若 ETW 仍不足以暴露该决策，则标记 `CATALOG_INTERNAL_DECISION_NOT_INSTRUMENTED`，**不得**由"日志中没有错误"反推"没有错误"。

---

## F. Microsoft Store and Official EP Package

四种状态**分开**记录（禁止合并）：

| 状态 | 值 | 依据 |
| --- | --- | --- |
| `OFFICIAL_MSIX_RELEASED` | **TRUE** | 1.8.65.0（2026-10-28，"AMD GPU EP added"）；运行时表内含 AMDGPU 条目 |
| `OFFICIAL_PACKAGE_TARGETED_TO_HOST` | **UNVERIFIED** | `0x1586` 未被排除，但目录未下发；投放规则未公开 |
| `OFFICIAL_PACKAGE_ACQUIRABLE_ON_HOST` | **FALSE（实测）** | 3 模式 0 条、`FindProvider` 未找到、各 scope 无包、0 条 AMD GPU EP 部署事件 |
| `OFFICIAL_INDEPENDENT_BYO_ARTIFACT_AVAILABLE` | **FALSE** | 官方 BYO 页：`onnxruntime-ep-amdgpu` 需自行从源码构建 |

**Store 表面本身是健康的**（证伪"Store 缺失/被策略禁用"作为唯一原因）：`Microsoft.WindowsStore 22610.1401.10.0` 已安装；`AppInstallManager` 可实例化；`StoreContext.GetDefault()` 成功；无 Store 策略键。

---

## G. Driver and gfx1151 Compatibility

| 问题 | 判定 |
| --- | --- |
| 当前驱动是否满足 AMD 文档的 Adrenalin ≥ 24.12.1 | `DRIVER_COMPATIBILITY_UNVERIFIED` —— WDDM `32.0.22018.5` 与 Adrenalin 市场版本不同量纲，无官方映射 |
| 是否 HP OEM 特有约束 | 未知（无公开文档） |
| 驱动是否为本次阻塞的**共同**原因 | 证据不支持：同一驱动上 **DML 正常执行**；且 WebGPU（仅需 DX12）也缺席 |
| 是否更换驱动 | **否** —— 无"可验证的不兼容证据"，也无组件级 Owner 授权 |

**gfx1151 kernel 兼容性**：本机为 `UNVERIFIED`（无 EP 可跑）。存在跨主机证据（另一台 gfx1151 机器同 DLL 物理执行成功），按 AGENTS.md 规则 2 **不作本机结论**。

---

## H. Attempted Fixes

| # | 假设 | 干预 | 结果 | 回滚 |
| --- | --- | --- | --- | --- |
| H-1 | Catalog 空是 A/B 工具未导入 A 证据导致的分析缺陷 | 修复 `experiment_b_ab_compare.py`（新增 A schema 抽取 + 数值强制转换） | ✅ 首次产出真实 A/B 差分（`B2`） | 无需（纯代码） |
| H-2 | 显式 `target_luid` 不匹配时会回退到**另一块**物理 GPU（实验 A 的 `luid_pin_fail` 已暴露） | `select_amdgpu_ep_device` 改为 **fail-closed**：显式 LUID 必须恰好匹配 1 个设备，否则返回 `None` | ✅ 12 条 CPU-only 对抗测试通过 | 无需 |
| H-3 | Worker 的 `input_manifest` 契约错误会被误判成 GPU 失败 | 新增 `validate_case_input_source()` + worker 退出码 **36 = HARNESS_CONTRACT_VIOLATION** | ✅ 单测通过；`runner.preflight` 端到端正常 | 无需 |
| H-4 | 隐藏的 Catalog 模式可能列出 EP | 直接调用未公开的 `WinMLEpCatalogCreateWithMode(0..5)` | ❌ 0/1/2 均 `S_OK` + 0 条；3..5 `E_INVALIDARG` | 无（只读） |
| H-5 | 目录"知道"AMDGPU 但过滤了枚举 | `WinMLEpCatalogFindProvider` 用 3 种 PFN 直接查 | ❌ 全部 `0x80070490` | 无（只读） |
| H-6 | 官方 Store 投放可用 | `ensure_ready_async()` | ⛔ 无 provider 对象可调用；且安装属系统变更 → `BLOCKED_AUTHORIZATION` | — |
| H-7 | BYO 官方二进制 | 查 BYO 文档 | ⛔ 官方要求自建源码；自建产物不得冒充签名 EP | — |
| H-8 | 驱动问题 | 换驱动 | ⛔ 无证据 + 无授权（AGENTS.md 规则 6） | — |

**系统组件安装/变更计数：0。** 唯一写入是本仓库的代码与证据文件。

---

## I. Real AMDGPU Physical Validation

```
status = NOT_RUN
reason = 本机不存在 amdgpu-ep.dll，Catalog 不提供安装对象；绝不以 CPU 会话替代
```

| 门禁 | 值 |
| --- | --- |
| DID `0x1586` / gfx1151 | ✅ 已核验 |
| EP DLL VERIFIED / 签名有效 | ❌ 不存在 |
| actual_provider = AMDGPUExecutionProvider | ❌ `null` |
| target_ep_profile_nodes > 0 | ❌ 0 |
| cpu_profile_nodes = 0 | `null`（无会话） |
| numeric_parity / task_parity | `NOT_RUN` |
| receipt_verifier | `NOT_RUN` |

**代价对照（证明 DML 未受损）**：同一 canary 模型上 `DmlExecutionProvider` 有 1 个节点、parity `allclose=True`、`max_abs=2.86e-06`；AMDGPU worker 返回 `34`。见 `C5_DML_REGRESSION.json`。

---

## J. Root Cause Verdict

```
CONFIRMED_PROXIMATE_BLOCKER = CATALOG_EMPTY
UNDERLYING_ROOT_CAUSE       = UNVERIFIED
```

| 假设 | 状态 | 置信度 | 关键反证 |
| --- | --- | --- | --- |
| H1 SDK / runtime 激活 | `REFUTED` | HIGH | API 二进制与工作主机逐字节相同、HRESULT `S_OK` |
| H2 Python 投影 | `REFUTED` | HIGH | flat-C 与 wasdk 2.3 两套表面同为 0 |
| H3 包身份 / bootstrap | `REFUTED` | HIGH | 7 种 bootstrap 表面皆 0；flat-C 无需包身份 |
| H4 Catalog 元数据来源 | `SUPPORTED_HYPOTHESIS` | MEDIUM | Store API 存活；缺权威过滤输入 |
| H5 Store 投放/flighting | `SUPPORTED_HYPOTHESIS` | MEDIUM | WebGPU 也缺席 |
| H6 EP MSIX 获取 | `REFUTED`（作为根因） | HIGH | 获取失败是空表的**结果**，非原因 |
| H7 OEM 驱动兼容性 | `UNVERIFIED` | LOW | DML 正常；WebGPU 也不在 |
| H8 gfx1151 设备兼容性 | `SUPPORTED_HYPOTHESIS` | MEDIUM | `0x1586` 未被黑名单；跨主机可执行 |
| H9 DLL ABI/依赖 | `REFUTED` | HIGH | 本机无 DLL；跨主机同 DLL 加载成功 |
| H10 GPU kernel/算子 | `REFUTED`（作为阻塞原因） | HIGH | 阻塞发生在任何 kernel 之前 |

**责任边界：** 直接阻塞层（本仓库可复现）= Catalog 不下发；内部决策（微软 Store/Catalog 权威输入）= 需微软答复。

---

## K. Microsoft Escalation

| 项 | 内容 |
| --- | --- |
| 渠道核查 | `microsoft/WindowsML` 的 issue 模板明确把"运行时/API 问题"指向 **WindowsAppSDK**；因此主渠道 = `microsoft/WindowsAppSDK` issues |
| 重复检查 | `NONE FOUND`（覆盖 4 个仓库、7 组关键词；最接近的 `WindowsAppSDK#6072` 是"已列出但 ensure_ready 失败"，症状不同） |
| Issue 文本 | [`UPSTREAM_MICROSOFT_ISSUE_EN.md`](UPSTREAM_MICROSOFT_ISSUE_EN.md)（可直接复制） |
| 状态 | **`ESCALATION_POSTED`** — <https://github.com/microsoft/WindowsAppSDK/issues/6994>（尚未获回复，`acknowledged=false`） |
| Feedback Hub | 无法在自动化会话中操作 GUI；已提供 [`FEEDBACK_HUB_SUBMISSION_GUIDE.md`](FEEDBACK_HUB_SUBMISSION_GUIDE.md)，**不声称已提交** |

---

## L. Release Readiness & Developer Guidance

### L.1 一个外部开发者现在可以做什么

```powershell
# 1) 复现本机结论（只读，无 GPU 会话）
.venv-winml\Scripts\python.exe tools\mission_c_catalog_internals.py

# 2) 复现证据包并自校验
.venv-winml\Scripts\python.exe tools\mission_c_build_evidence.py `
  --run-id <NEW_RUN_ID> --preflight-run <preflight dir>
.venv-winml\Scripts\python.exe tools\verify_mission_c_evidence.py --run-id <NEW_RUN_ID>
```

判读规则：**AMDGPU worker 返回 0 不等于 GPU 成功**；只有 profiler 上 `AMDGPU > 0` 且 `CPU == 0`、数值 parity 与任务 oracle 同时通过，才算 GPU 证据。

### L.2 什么时候本机会解封

任一条件成立即可立刻复测（`C5` 的 `NOT_RUN` 会变成可执行）：

1. Catalog 出现 `AMDGPUExecutionProvider`（`ReadyState=NotPresent` 即可调 `ensure_ready_async()`）；
2. 微软/AMD 提供适用于本机、当前 ORT ABI 的**官方签名** EP 制品或其正式投放说明；
3. Owner 明确授权"按 Store 产品 `9PNKGVD4CPCV` 安装 EP 包"这一组件级变更。

### L.3 限制（必须随结论一起传播）

- 本机结论**不能**外推为"gfx1151 不支持 AMDGPU EP"；反证已存在（另一台 gfx1151 主机可执行）。
- Experiment A 的 Catalog `NotReady` **不等于**该 EP 可被本机获取。
- CPU CI 绿 **不** 代表 GPU；`CPU_CI_DOES_NOT_ATTEST_GPU`。
