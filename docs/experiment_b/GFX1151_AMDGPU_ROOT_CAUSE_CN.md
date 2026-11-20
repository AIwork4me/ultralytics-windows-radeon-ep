# GFX1151（Radeon 8060S）AMDGPU EP 根因报告 — Experiment B

- **Run ID**: `B_GFX1151_AMDGPU_20261120T0336Z`
- **日期**: 2026-11-20（Asia/Shanghai）
- **主机**: HP ZBook Ultra G1a 14 / Ryzen AI MAX+ PRO 395 / Radeon 8060S（gfx1151，PCI DID `0x1586`）
- **Git**: `767efd71d6106be2fe63895743be8ff6bf652b2f`（`main`，harness 范围 clean）
- **结论标签**: `GFX1151_BLOCKED_OFFICIAL_EP_ARTIFACT`（不建议、也不允许升级为 `B_GFX1151_AMDGPU_GPU_EXECUTED`）
- **一句话**: 这台机器上 Windows ML 2.x 的 EP Catalog 在**所有 API 表面**上都返回 0 个 Provider（HRESULT `S_OK`），主机上**不存在** AMD GPU EP 的 MSIX 或 `amdgpu-ep.dll`，而官方渠道**没有发布**任何可直接获取的 AMDGPU EP 二进制产物。因此 AMDGPU EP 在这台 8060S 上既无法下载、也无法注册，更无法执行。

---

## 1. 结论先行：七个必答问题

| # | 问题 | 回答 |
| --- | --- | --- |
| 1 | 8060S 上 AMDGPU EP 是否真的能注册并执行？ | **不能。** 不是"注册失败"，而是**没有可注册的对象**：catalog 为空、EP 包与 DLL 全机缺失。 |
| 2 | 如果不能，卡在哪一个精确阶段？ | **S2（catalog 枚举）**。`WinMLEpCatalogCreate` = `0x00000000`、`WinMLEpCatalogEnumProviders` = `0x00000000`、`provider_count` = 0，所以 S3（`ensure_ready`）连调用目标都没有。 |
| 3 | 该阶段的直接证据是什么？ | `CATALOG_PROBE.json` / `STANDALONE_CATALOG.json`（两套独立实现各重复 3 次，掩码摘要完全一致，退出码 2/2/2 与 3/3/3）；`CATALOG_SURFACE_MATRIX.json`（7 个表面中 5 个可用，全部 0 个 Provider）；`MSIX_INVENTORY.json`（HKCU 140 个包 / HKLM 74 个包中均无 AMD GPU EP，2464 条部署事件中 `AMD.GPU` 命中 0 次）；`PROVISIONING_ATTEMPT.json`。 |
| 4 | 目前已排除哪些原因？ | Windows ML API/SDK 不可用（H1，**REFUTED**）；Python 投影/绑定版本造成的假空（6 个表面都不为空的话不成立）；OS 版本不足、WinML 共享运行时缺失（均为 **MET**）；Store 侧获取失败（扫描 4000 条 Store 事件，0 条获取错误）；当前用户权限导致枚举失败（注册表双 scope 交叉核对）。 |
| 5 | 哪些原因仍然没有证据？ | HP OEM 驱动是否满足 AMD 文档中的 Adrenalin 最低版本（**无法从只读遥测换算**）；gfx1151 的 kernel/operator 覆盖（**没有 EP 可测**）；Store/WU 为何不向 DID `0x1586` 定向投放该 MSIX。 |
| 6 | Microsoft/AMD 需要补充什么？ | (a) 让 catalog 向 DID `0x1586` 列出 `AMDGPUExecutionProvider`（`ReadyState=NotPresent` 即足够）；或 (b) 发布带签名、带 gfx 支持清单的 AMDGPU EP 可再分发产物；并说明 `32.0.22018.5` 对应的 Adrenalin 版本。 |
| 7 | 怎样才能从 BLOCKED 安全升级到 GPU GO？ | catalog 一旦列出该 Provider：`ensure_ready` → 校验 `amdgpu-ep.dll` 哈希 → 注册 → 运行 AMDGPU canary，要求 **AMDGPU profiler 节点 > 0、CPU 节点 = 0、按名数值对比 PASS、PT task oracle PASS_TASK、receipt verifier ALL_OK**，缺一不可。 |

---

## 2. 阶段停点矩阵（fail-closed）

| 阶段 | 内容 | 期望 | 观测 | 状态 |
| --- | --- | --- | --- | --- |
| S0 | 设备身份（DID + DXGI LUID） | DID `0x1586`、LUID 有效 | `0x1586`、`0x00000000:0x000154E4`、`did_match=true` | **PASS** |
| S1 | OS / Windows ML 运行时 | build ≥ 26100、WinML ≥ 2.5.96 | `26200.9974`、`windowsml 2.5.96`、`WinMLShared.7.0 1.2610.1050.0` | **PASS** |
| S2 | Catalog 枚举 | ≥ 1 Provider（含 `NotPresent`） | **0**，HRESULT `0x00000000` | **BLOCKED** |
| S3 | `ensure_ready_async()` | 可寻址 | 无 Provider 对象可调用 | **BLOCKED** |
| S4 | 官方产物获取 | 官方签名 MSIX 或 BYO | GitHub Release 0 个、NuGet 404、BYO 文档要求自行编译 | **BLOCKED** |
| S5 | EP DLL 存在 | `amdgpu-ep.dll` | 全机 0 个 | **BLOCKED** |
| S6 | ORT 注册库 | 注册成功 | 无可注册对象（负测试证明缺 DLL 会 `DLL_LOAD_ERROR`） | **NOT_RUN** |
| S7 | 创建 AMDGPU Session | Session 绑定 AMDGPU EP | 未创建 | **NOT_RUN** |
| S8 | GPU 执行 + 数值/任务对比 | 节点>0、CPU=0、parity PASS | 未执行 | **NOT_RUN** |

> 首个失败阶段 = **S2**。这不是"gfx1151 不支持"的证明，而是"链路在第 2 步就断了"的证明。

---

## 3. 主机与环境（本次重新实测，非假设）

| 项 | 观测值 | 证据 |
| --- | --- | --- |
| OS | Windows 11 专业版 25H2，`10.0.26200.9974` | `PRE_EXPERIMENT_SNAPSHOT.json:os` |
| GPU | AMD Radeon(TM) 8060S Graphics，`PCI\VEN_1002&DEV_1586&SUBSYS_8D01103C&REV_D1` | 同上 `:gpu` |
| DXGI | 同一物理适配器枚举 2 次（`…:0x000154E4` / `…:0x08D5FD5C`）+ Microsoft Basic Render | 同上 |
| 驱动 | `32.0.22018.5`，日期 `2025-09-17`，INF `oem21.inf`（原 `u0419920.inf`），签名 Microsoft Windows Hardware Compatibility Publisher | `DRIVER_COMPATIBILITY.json` |
| 驱动包内部版本 | INF 横幅 `;----------2510081301-25.20.18-250917a-419920C-HP` | 同上 |
| Windows ML（Python） | `windowsml 2.5.96`、`onnxruntime-windowsml 1.25.2.202605110140`、`wasdk-…MachineLearning 2.3.0` | `PRE_EXPERIMENT_SNAPSHOT.json:runtime` |
| WinML 共享运行时 | `WindowsWorkload.WinMLShared.7.0 1.2610.1050.0` | `MSIX_INVENTORY.json` |
| Windows App Runtime | 1.7 / 1.8 / 2.6.5.0 / 2.7.0.0 | 同上 |
| ORT Provider | `DmlExecutionProvider`、`CPUExecutionProvider` | 同上 |
| AMD EP 包 | 仅 `WindowsWorkload.EP.AMD.VitisAI.Framework.1.8 1.8.80.0`（**NPU**，非 GPU EP） | 同上 |

实测与历史基线一致（DID、LUID、驱动版本、WinML 版本、catalog 为空、无 `amdgpu-ep.dll` 均未变化）。唯一需要注意的历史差异见 §9。

---

## 4. B1 — 官方 Catalog 只读复现

### 4.1 两套独立实现 × 3 次重复

| 工具 | 次数 | Provider 数 | create HRESULT | enum HRESULT | 退出码 | 掩码摘要一致 |
| --- | --- | --- | --- | --- | --- | --- |
| `runner.probe_amdgpu_ep` | 3 | 0 / 0 / 0 | `0x00000000` | `0x00000000` | 2 / 2 / 2 | ✅ |
| `repro/windowsml_amdgpu_catalog_probe.py` | 3 | 0 / 0 / 0 | `0x00000000` | `0x00000000` | 3 / 3 / 3 | ✅ |

- Provider Name / ReadyState / Package Identity / Library Path：**无对象可记录**（0 个 Provider）。
- 摘要稳定性：三次重复的"掩码摘要"（把时间戳屏蔽后哈希）完全一致；`stable_digest` 在**同一批次内**一致。
- 诚实提示：跨批次比较时 `stable_digest` 发生过变化（`c9f7ed5e…` → `344fb4d8…`），而所有语义字段未变，说明该摘要把至少一个易变输入（例如 `PendingFileRenameOperations` 计数）算了进去。因此"稳定"只按同批次内成立来解释，不跨批次引用。

判定：**`B_CATALOG_EMPTY_REPRODUCED`**。`S_OK` 不代表 EP Ready —— 本仓库从不用 `S_OK` 或 `get_providers()` 作为 PASS 依据。

### 4.2 Catalog 表面矩阵（排除"只是某个绑定坏了"）

| 表面 | 结果 |
| --- | --- |
| `wasdk` 投影，**无** bootstrap | 抛出 `WinError 0x80073CF4`（该进程没有程序包标识符） |
| `wasdk` 投影，bootstrap 1.7 | `AttributeError`：该 Windows 版本不提供无参重载 |
| `wasdk` 投影，bootstrap 1.8 | 可用，**0** 个 Provider |
| `wasdk` 投影，bootstrap 2.3 | 可用，**0** 个 Provider |
| `wasdk` 投影，bootstrap 2.6 | 可用，**0** 个 Provider |
| `wasdk` 投影，bootstrap 2.7 | 可用，**0** 个 Provider |
| 官方 `windowsml` 2.5.96 扁平 C API | 可用，**0** 个 Provider |

结论：`NO_CATALOG_SURFACE_ON_THIS_HOST_LISTS_ANY_EXECUTION_PROVIDER`。连文档里"任何 DX12 GPU 都支持"的 `WebGpuExecutionProvider`、以及本机**已安装**的 VitisAI NPU EP 都没有出现 —— 说明这个列表不是按"本机装了什么"或"驱动是否兼容"逐条过滤出来的，而是这台机器整体没有被投放任何 EP 条目。这一点是**观察**，不是对内部机制的证明（见 §11 未决项）。

---

## 5. B4 — MSIX / 驱动 / Store 依赖分析

### 5.1 包清单（双 scope 交叉核对）

| Scope | 来源 | 总数 | AMD GPU EP |
| --- | --- | --- | --- |
| 当前用户 | `Get-AppxPackage`（agent shell） + HKCU AppModel Repository | 129 / 140 | **0** |
| 全机 | HKLM `Appx\AppxAllUserStore\Applications` | 74 | **0** |

- `C:\Program Files\WindowsApps` 对本账户不可列（`Access denied`），因此"缺失"结论建立在 **AppX 自己的注册表存储**（双 scope）之上，而不是一次失败的目录枚举。
- 命名候选均未命中：`Microsoft.WinML.AMD.GPU.EP.Framework.*`、`Microsoft.WinML.AMD.GPU.EP.*`、`MicrosoftCorporationII.WinML.AMD.GPU.EP.*`。
- `amdgpu-ep.dll`：在 site-packages、用户 WindowsApps、全机 WindowsApps 的 4 类 glob 模式中均为 **0** 命中。

### 5.2 部署事件（证明"投放机制是活的，只是没投这个包"）

| 关键词 | 命中数（共 2464 条相关事件） |
| --- | --- |
| `VitisAI` | 137 |
| `WinMLShared` | 112 |
| `AMD.GPU` / `AMDGPU` | **0** |
| `amdgpu`（小写） | **0** |

- AMD **NPU** EP 框架于 `2026-11-18T16:25:38` 至 `2026-11-18T16:43:31` 之间被注册（`OnDemandRegisterPackage` + 完整性校验 + Register 完成），说明 Windows Workload 的 EP 投放通道在本机是**正常工作**的。
- 同期**没有**任何 AMD GPU EP 包的 staging/registration 记录。
- 原始事件转储含账户 SID 与用户路径，**未提交**；提交的是脱敏后的 80 条引用事件 + 全量统计（`_raw/b4_appx_events_redacted.json`）。

### 5.3 Store / Windows Update

- `Microsoft.WindowsStore 22610.1401.8.0` 已安装；`Microsoft-Windows-Store/Operational` 扫描 4000 条，**0** 条获取错误/重试/拒绝。
- Windows Update 在线搜索（只读）：仅 2 项 —— Defender 定义更新 + `Microsoft Corporation AudioProcessingObject Driver Update`。**没有** AMD 显示驱动，也**没有**任何 EP 包。
- 无 servicing reboot 挂起；Store/WU 相关服务均为 Running/Manual（无禁用）。
- 结论：**没有可诊断的 Store 获取失败**。这是"没被投放"，不是"投放失败"。

### 5.4 驱动兼容性

- 已安装：`32.0.22018.5`（2025-09-17），HP 打包的 OEM 驱动（INF 横幅以 `-HP` 结尾，签名 WHCP）。
- 官方文档对该 EP 的驱动要求只有一句：**"Adrenalin Edition 24.12.1 or later"**。
- **Adrenalin 版本 = UNKNOWN**。WDDM `32.0.22018.5` 与 INF 内部串 `25.20.18` 都不是 Adrenalin 市场版本号，仓库里不存在可用的换算规则；已安装的 `AdvancedMicroDevicesInc-2.AMDRadeonSoftware 10.25.20179.0` 同样不可换算。
- 只能作为**旁证**：驱动日期 2025-09-17 晚于 24.12.1 的发布窗口；并且同一驱动上 DirectML 全量回归通过（§9），说明驱动栈本身能跑 GPU 推理。
- 明确写成 Compatibility Finding，而不是"版本号比大小"：

| ID | 陈述 | 状态 |
| --- | --- | --- |
| DRV-1 | OS build 26200.9974 满足 24H2（26100+） | **MET** |
| DRV-2 | 绑定 2.5.96 满足 2.5.96 最低要求 | **MET** |
| DRV-3 | Adrenalin ≥ 24.12.1 | **UNVERIFIED**（无换算规则） |
| DRV-4 | gfx1151 / DID 0x1586 被官方明确支持 | **UNVERIFIED**（官方无 gfx/DID 清单） |
| DRV-5 | 该驱动能跑 GPU 推理 | **MET_FOR_DML** |
| DRV-6 | HP OEM 打包阻止 AMDGPU EP | **UNVERIFIED**（官方无此声明） |

### 5.5 假设矩阵

| ID | 假设 | 状态 |
| --- | --- | --- |
| H1 | Windows ML API/SDK 坏了或太旧 | **REFUTED_BY_EVIDENCE** |
| H2 | Catalog/Store 没有向本机投放 EP 条目 | **SUPPORTED_BY_EVIDENCE** |
| H3 | AMD GPU EP MSIX/DLL 未安装且全机缺失 | **SUPPORTED_BY_EVIDENCE** |
| H4 | HP OEM 驱动不兼容 | **UNVERIFIED** |
| H5 | gfx1151 kernel/operator 不支持 | **UNVERIFIED** |
| H6 | "注册成功"其实是静默 CPU 回退 | **SUPPORTED_BY_EVIDENCE**（见 §8） |

---

## 6. B3 — 官方发布现状（2026-11-20 实时核查）

| 页面 | 页面标记 | 原始 body sha256 | 与 R10 是否逐字节相同 |
| --- | --- | --- | --- |
| supported-execution-providers（HTML） | `ms.date 2026-11-18` | `bcfda0c4…` | 否（Learn HTML 含动态 chrome，不可逐字节比较） |
| 同上（**main 分支 markdown**） | `ms.date 11/18/2026` | `306bb247…` | **是** |
| initialize-execution-providers | `2026-05-30` | `fc082bf4…` | 否（同上） |
| register-execution-providers | `2026-02-13` | `a1cc2153…` | 否（同上） |
| execution-provider-errors | `2026-11-02` | `bd1380dd…` | 否（同上） |
| bring-your-own-eps | `2026-11-10` | `10bbdf7b…` | 否（同上） |
| WindowsML wiki（HTML） | — | `a0bb2a2b…` | 否 |
| 同上（**wiki markdown**） | — | `153355aa…` | **是** |

> 权威内容 hash 取 markdown 源：**与 R10 完全一致**，即在 R10 之后官方没有任何内容变更。

页面明确写到的内容：

- `AMDGPUExecutionProvider` **在** Windows ML 2.x 列表中；最低 `Microsoft.Windows.AI.MachineLearning 2.5.96`；**唯一**驱动要求是 "Adrenalin Edition 24.12.1 or later"；**没有** gfx/DID 列表。
- Wiki 原文：**"The AMD GPU EP and MIGraphX EP are shipped in the same package, but the AMD GPU EP is only compatible with Windows ML 2.5.96 or greater, and was added in the 2026-10-28 release."** 当前发布版本 `1.8.65.0`（2026-10-28），兼容 "Windows ML 2.x and 1.8.x"；下一个版本 `2.2611.67.0` 仍是 Insiders（GA 计划 2026-12-01），**不是正式可获取条件**。
- 获取渠道：Windows ML ≥ 2.5.96 通过 **Microsoft Store**；BYO 页面写明 AMDGPU EP **"must be built from source"**。

由此得到一条对本机很关键的事实：**能带来 `AMDGPUExecutionProvider` 的那个 MSIX（`1.8.65.0`，与 MIGraphX 同包）在文档层面是"已正式发布"的，但它没有出现在本机的包库里，也没有被 WU/Store 投放。**

---

## 7. B5 — 条件式官方 Provisioning 结果

### Path 1（官方 Catalog 动态安装）

- 只做只读寻址：`EpCatalog` 暴露的方法只有 `find_all_providers` 与 `close`（`EpCatalog_methods`），`ensure_ready` 是 **Provider 实例**上的方法。
- 0 个 Provider ⇒ **没有对象可调用** ⇒ 没有触发任何下载/安装。
- 结果：`NOT_ADDRESSABLE_CATALOG_EMPTY`。

### Path 2（官方签名 BYO）

| 检查 | 结果 |
| --- | --- |
| 官方仓库 `onnxruntime/onnxruntime-ep-amdgpu` | 存在（MIT，2026-05-11 创建，2026-11-19 有推送） |
| GitHub Releases | **0 个**（`releases` 返回 `[]`） |
| Release 资产 | 无 |
| NuGet `onnxruntime-ep-amdgpu` | **404** |
| NuGet `Microsoft.ML.OnnxRuntime.EP.AmDGpu` | **404** |
| NuGet 搜索 | `totalHits = 0` |
| 官方 BYO 声明 | "must be built from source"（需 ROCm + ORT 源码 + wheel 构建） |

结果：**`B_BLOCKED_OFFICIAL_ARTIFACT_UNAVAILABLE`**。系统未做任何更改；`POST_EXPERIMENT_STATE_DIFF.json` 对 18 个关键字段的比较结果为 **`SYSTEM_STATE_UNCHANGED`**。

**未采取的禁止动作**（逐条记录在 `PROVISIONING_ATTEMPT.json`）：从别的机器复制 `WindowsApps` 下的 DLL；从不可信来源安装 1.8.65.0 MSIX；升级 HP OEM 驱动；关闭 Defender/Secure Boot/签名强制。

---

## 8. B6 — AMDGPU Canary：未执行（且不应执行）

不存在可注册的 EP 对象，因此 canary **未执行**，也不会被写成 PASS。为了让"没执行"这件事本身可审计，做了 6 项负测试：

| 测试 | 期望 | 实测 | 结论 |
| --- | --- | --- | --- |
| 注册不存在的 `amdgpu-ep.dll` | 报错 | `DLL_LOAD_ERROR`（`Error loading … which is missing`） | FAIL-CLOSED ✅ |
| 把一个非 EP 的 DLL 当 AMDGPU 注册 | 报错 | `Failed to find symbol CreateEpFactories … error code: 127` | FAIL-CLOSED ✅ |
| 未注册就请求 AMDGPU Session | 不静默回退 | **静默回退到 CPU**（见下） | **危险行为，已记录** |
| 请求任意不存在的 Provider | 报错 | 同样静默回退到 CPU | **危险行为，已记录** |
| 在 gfx1151 主机上请求 gfx1100 | 报错 | `WRONG_OR_AMBIGUOUS_DEVICE` | FAIL-CLOSED ✅ |
| 显式 DML Session | 绑定 DML | providers 含 `DmlExecutionProvider` | 对照通过 ✅ |

> **本次最有价值的副发现**：在 `onnxruntime 1.25.2` 的 Python 绑定上，`InferenceSession(providers=["AMDGPUExecutionProvider"])` **不会抛异常**，而是打印
> `EP Error Unknown Provider Type … Falling back to ['CPUExecutionProvider'] and retrying` 后**正常创建 CPU 会话**。
> 也就是说：任何"AMDGPU 跑通了"的结论，若只凭"session 创建成功"或"推理有输出"，都是假的。必须断言 `sess.get_providers()` 含目标 EP，并且 profiler 里目标 EP 节点 > 0、CPU 节点 = 0。

---

## 9. B7 — DirectML 安全回归

| 项 | 结果 |
| --- | --- |
| Tier A 全量（9 个模型，`--ep dml`） | **9/9** `PASS_GPU_EXECUTED_FULL` + **9/9** `PASS_TASK`，strict audit `9/9` |
| 单例（YOLO26n detect） | `PASS_GPU_EXECUTED_FULL` + `PASS_TASK`，receipt verifier **ALL_OK** |
| 数值对比（按输出名） | `all_outputs_allclose=true`、`by_name_matching=true`（output0 max_abs 1.9e-3，阈值 5e-3） |
| 任务 oracle（PT Ultralytics） | precision/recall = 1.0，5/5 实体匹配，`passed=true` |
| 设备身份 | `VERIFIED_GFX1151`，DID `0x1586`，LUID `0x00000000:0x000154E4` |
| Job Object 回收 | `job_fault_evidence` **8/8 PASS**（含 wedged worker / 多层孙进程 / 取消 / amdgpu 超时后回 dml） |
| 是否复用旧 receipt | 否：三个 run id 全新，`rerun_of=null`，manifest 各自独立 |

**历史保留（失败 → 修复 → 重跑）**：第一次尝试 `B_GFX1151_DML_SAFETY_20261120T0345Z` 的执行是 PASS，但 PT task oracle 因为 `--export-python` 指向了没有 `torch` 的 `.venv-winml` 而 `TASK_ORACLE_BLOCKED`；保留该 run 作为失败证据，改用 `.venv-export` 后重跑得到 `…T0352Z`（`PASS_TASK`）。

判定：**`DML_PATH_HEALTHY`**，本实验没有破坏 8060S 的 DML 路径。

---

## 10. B2 — gfx1100 vs gfx1151 A/B

- 实验 A（gfx1100）的机器可读 JSON **尚未到达本机**，因此 `AB_COMPARISON.json` 的 A 侧全部标记为 `A_EVIDENCE_PENDING`，**不做任何推断性 PASS**。
- 已交付可导入的比较工具：`tools/experiment_b_ab_compare.py --a-json <A 产物…>`，支持 4 种 A 输入形态（AB schema / probe 报告 / canary / smoke run_result），字段缺失即 `UNKNOWN`，绝不自行推导。
- 依赖 A 证据的判定（Case B1 / B2）**本次不主张**；本次只主张 `GFX1151_BLOCKED_OFFICIAL_EP_ARTIFACT`（Case B5）。
- 详见 `docs/experiment_b/AMDGPU_AB_COMPARISON_CN.md` 与 `reports/experiment_b/AB_COMPARISON.json`。

---

## 11. 根因、责任与未决项

### 11.1 分层判定

1. **Windows ML API/SDK**：健康。两套独立绑定 + 5 个可用表面 + `S_OK` + 版本达标。→ 不是原因。
2. **Catalog / Store 分发**：**这是断点**。本机没有被投放任何 EP 条目。
3. **EP MSIX/DLL**：**缺失**（全机双 scope 0 命中）。这是断点的**后果**，也是"无法注册"的直接原因。
4. **HP OEM 驱动**：**无证据**。不能因为它是 OEM 包就归因；也不能证明它满足 Adrenalin 要求。
5. **gfx1151 kernel/operator**：**无证据**，且**不可推导**为"架构不支持"。

### 11.2 责任划分

| 现象 | Owner | 需要给出的东西 |
| --- | --- | --- |
| catalog 对 DID `0x1586` 返回 0 个 Provider（连 WebGPU、已安装的 VitisAI 都没有） | **Microsoft（Windows ML / Store 定向）** | 说明 2.x catalog 的条目来源与设备定向规则；让该 DID 至少看到 `ReadyState=NotPresent` 的条目 |
| `AMDGPU EP` 无官方二进制 | **Microsoft + AMD** | 发布带签名、带 gfx 支持清单的可再分发产物，或明确"仅随 MSIX 分发" |
| Adrenalin 版本 ↔ OEM WDDM 版本映射 | **AMD + HP** | 明确 `32.0.22018.5` / `25.20.18` 对应的 Adrenalin 版本，以及 OEM 包是否被 GPU EP 认可 |
| gfx1151 覆盖声明 | **AMD** | 在 EP 文档中列出支持的 gfx 目标 |

### 11.3 仍然没有证据的问题（不得当作结论）

- Store/WU **为什么**不向本机定向投放 `Microsoft.WinML.AMD.GPU.EP.Framework.*`（DID 过滤？SKU？区域？OEM 通道？）——只读遥测无法区分。
- 若把 `1.8.65.0` 包放在本机，它是否会加载并执行（即 gfx1151 kernel 是否覆盖）——没有官方产物，不能测。
- HP OEM 驱动是否会在 EP 初始化时拒绝设备——同上。

---

## 12. 从 BLOCKED 到 GPU GO 的关门条件

必须先满足 **A（外部条件）**，再满足 **B（本地验收链）**：

**A. 外部条件（任一）**

1. catalog 在 DID `0x1586` 上列出 `AMDGPUExecutionProvider`（`ReadyState` 任意，包括 `NotPresent`）——此时 `ensure_ready_async()` 才有对象可寻址；或
2. 官方发布带签名、带 gfx1151 支持声明的 AMDGPU EP 产物（MSIX 或 wheel），并给出 ABI/驱动矩阵。

**B. 本地验收链（全部为真才算 GPU GO）**

```text
physical_device = VERIFIED_GFX1151
actual_ep       = AMDGPUExecutionProvider
target_ep_profile_nodes > 0
cpu_profile_nodes        = 0
numeric_parity           = PASS   (按输出名，vs CPU ORT)
task_parity              = PASS_TASK (独立 PT oracle)
receipt_verifier         = ALL_OK
```

任意一条为假（尤其"注册成功但 GPU 节点为 0"），只能是 `BLOCKED_*` 或 `FAIL_*`，不能升级。

---

## 13. 证据索引

全部证据在 `reports/experiment_b/`，逐文件 sha256 见 `reports/experiment_b/MANIFEST.sha256`。

| 文件 | 作用 |
| --- | --- |
| `PRE_EXPERIMENT_SNAPSHOT.json` | 实验前环境冻结（git/os/gpu/driver/appx/runtime/catalog/Store/R10 文件哈希） |
| `CATALOG_PROBE.json` / `STANDALONE_CATALOG.json` | 两套实现的 catalog 复现（各 3 次 + 稳定性判定） |
| `CATALOG_SURFACE_MATRIX.json` | 7 个 catalog 表面的完整矩阵 |
| `OFFICIAL_DOC_CITATIONS.json` | 8 个官方页面的 URL/访问时间/哈希/标记/与 R10 对比 |
| `OFFICIAL_ARTIFACT_PROBE.json` | GitHub Releases / NuGet / BYO 产物核查 |
| `MSIX_INVENTORY.json` | 双 scope 包清单、EP 缺失、部署事件统计、Store 状态 |
| `DRIVER_COMPATIBILITY.json` | 驱动包元数据 + 官方要求 + 兼容性发现（含 UNKNOWN 的诚实标注） |
| `DEPENDENCY_HYPOTHESES.json` | H1–H6 假设矩阵（SUPPORTED/REFUTED/UNVERIFIED） |
| `NEGATIVE_TESTS.json` | 6 项 fail-closed 负测试（含静默 CPU 回退发现） |
| `PROVISIONING_ATTEMPT.json` | Path 1/Path 2 结果、例外清单、禁止动作 |
| `POST_EXPERIMENT_STATE_DIFF.json` | 前后状态对比（18 字段，`SYSTEM_STATE_UNCHANGED`） |
| `DML_SAFETY_REGRESSION.json` | 9/9 + 单例 + Job Object + 不复用 receipt |
| `AB_COMPARISON.json` | `AMD_GPU_EP_AB_COMPARISON_V1`（A 侧 `A_EVIDENCE_PENDING`） |
| `EXPERIMENT_B_RESULT.json` | 机器可读总结（阶段矩阵、7 问 7 答、关门条件） |
| `_raw/` | 字节级原始捕获（含官方文档正文；含 SID 的事件原始转储**未提交**） |

---

## 14. 方法与边界

- **只读**：未安装/卸载任何 MSIX，未改驱动、未改 Windows 组件、未改 Store 策略、未关闭安全特性。前后状态对比为 `SYSTEM_STATE_UNCHANGED`。
- **不可绕过**：没有把"catalog `S_OK`"当 Ready，没有把 `get_providers()` 当 PASS，没有把 NPU VitisAI 包当 GPU EP，没有复制他机 DLL。
- **重复性**：catalog 探测两套实现各 3 次；表面矩阵每个表面独立进程；DML 回归 9 例。
- **不可比项**：`stable_digest` 跨批次不稳定（已注明）；Learn HTML body hash 含动态 chrome（已改用 markdown 源）；Adrenalin 版本无法换算（标 `UNKNOWN`）。
- **未执行项**：AMDGPU canary 全链路（无对象）、实验 A 对照（A 证据未到）、驱动升级（无授权且无证据支持）。
- **Manifest 约定**：每个 run 目录的 `manifest.sha256` 只列**会被 checkout 出来的文件**（git 忽略的
  `inputs/*.npy`、`*.raw.npz` 等原始张量不列入，其 sha256 记录在 receipt/input manifest 里），
  因此严格校验在"fresh-checkout 布局"下进行（`tools/experiment_b_dml_summary.py:verify_clean`）；
  `reports/experiment_b/MANIFEST.sha256` 覆盖该目录下全部证据文件（51 项）。
