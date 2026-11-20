# A/B 最终差分报告（gfx1100 vs gfx1151）

**Run ID:** `C_GFX1151_AMDGPU_20261120T2153Z`
**数据来源:** [`reports/mission_c/C0_AB_COMPARISON_V2.json`](../../reports/mission_c/C0_AB_COMPARISON_V2.json)
（由 `tools/experiment_b_ab_compare.py` 生成，**本 Mission 首次真正导入 Experiment A 的证据**）

---

## 0. 一句话

两台机器跑的是**同一份 Windows ML API 二进制**，得到**同一个 `S_OK`**，却拿到 **4 条 vs 0 条**目录条目 —— 所以差异不在 SDK、不在 Python、不在绑定，而在**同一段代码读到的系统输入**。

---

## 1. 实测差分表

| 字段 | gfx1100（RX 7900 XTX，PR #15） | gfx1151（8060S，本机） | 是否受控 |
| --- | --- | --- | --- |
| OS build | `26300.9974`（26H2） | `26200.9974`（25H2） | ❌ 未受控 |
| GPU DID | `0x744C` | `0x1586` | — |
| Driver | `32.0.21030.2001` | `32.0.22018.5` | ❌ 未受控 |
| Windows ML | `2.5.96` | `2.5.96` | ✅ |
| ORT | `1.25.2` | `1.25.2` | ✅ |
| Interop DLL SHA256 | `8205a572…d7be0` | `8205a572…d7be0` | ✅ **逐字节相同** |
| Catalog create HRESULT | `0x00000000` | `0x00000000` | ✅ |
| Catalog enum HRESULT | `0x00000000` | `0x00000000` | ✅ |
| **Provider 数量** | **4** | **0** | ← 观测量 |
| AMDGPU 状态 | `NotReady(1)` | 不在列表中 | — |
| EP MSIX | `Microsoft.WinML.AMD.GPU.EP.Framework.2` 1.8.65.0 | 机器级不存在 | — |
| `amdgpu-ep.dll` | `cb7496b3…f8ab` | 不存在 | — |
| 直接注册 | `PASS` | `BLOCKED_NO_DLL` | — |
| Profiler 节点 | AMDGPU **2** / CPU **0** | 无会话 | — |
| 数值 / 任务 parity | `PASS` / `PASS_TASK` | `NOT_RUN` | — |

`tools/experiment_b_ab_compare.py` 判定：`primary_case = B2`，`verdict = HOST_SPECIFIC_CATALOG_FILTER_OR_DEPENDENCY`，`a_evidence_state = PRESENT`。

---

## 2. A 的 4 条到底是什么（PR #15 原始 JSON 复读）

| Provider | ReadyState | 含义 |
| --- | --- | --- |
| `AMDGPUExecutionProvider` | `NotReady(1)` | 包已安装，但未加入该应用运行时依赖图 |
| `MIGraphXExecutionProvider` | `NotReady(1)` | 同一个 MSIX 内含 MIGraphX |
| `OpenVINOExecutionProvider` | `NotPresent(2)` | Intel 厂 EP，未安装 |
| `WebGpuExecutionProvider` | `NotPresent(2)` | 未签名/未认证（`Uncertified`） |

**这张表推翻了一个很自然但错误的假设。** 如果 Catalog 只返回"与本机 GPU 匹配"的 EP，那么 Intel 的 OpenVINO 不会出现在一台纯 AMD 机器上。所以目录**不是**按设备过滤后的结果；本机返回 0 条，说明条目**在到达设备过滤之前就不存在**。

---

## 3. 为什么 8060S 是 0：三种可能的机制，以及各自的可证伪点

| 机制 | 说明 | 本 Mission 的检验 | 结论 |
| --- | --- | --- | --- |
| M1 目录表按设备过滤，而 8060S 被滤掉 | 需要 `0x1586` 出现在黑名单或缺失于支持厂商 | 从签名二进制提取 28 条目录表：`0x1586` **不在** `UnsupportedDeviceIDs`，厂商 `0x1002` **受支持** | **证伪** |
| M2 目录条目在生成阶段就没有（Store/UUP 查询无结果） | 需要"整表为 0、连 WebGPU 都没有" | 实测：3 种 catalog 模式 × 2 套 API 表面 × 独立进程 = 全 0；`FindProvider` = `0x80070490` | **支持** |
| M3 两台机器其实加载了不同的运行时 | 需要 interop DLL 哈希不同 | 哈希**逐字节相同** (`8205a572…`) | **证伪** |

> 结论：M2 是唯一同时满足全部实测的机制。但 **M2 的内部原因**（Store 查询为何无结果：投放规则 / OS build / OEM SUBSYS / 驱动分支 / flighting）无法在本机观测 → `UNVERIFIED`。

---

## 4. 两个 gfx1151 主机的差异（重要，且容易被误读）

仓库里 `reports/gfx1151/` 下的 AMDGPU 物理 PASS 证据来自**另一台** gfx1151 机器：

| 项 | 本机 | 另一台 gfx1151 |
| --- | --- | --- |
| PnP SUBSYS / REV | `8D01103C` / `D1` | `17141043` / `C1` |
| Driver | `32.0.22018.5` | `32.0.22032.6002` |
| Python | 3.12.10 | 3.13.13 |
| AMD GPU EP MSIX | 不存在 | **OEM 预装** `MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8` 1.8.65.0 |
| Catalog | 0 条 | 3 条（MIGraphX / VitisAI / WebGPU） |
| AMDGPU 执行 | 无 | 有（profiler 节点 > 0、任务 parity PASS） |

**这两台机器不能互相背书**（AGENTS.md 规则 2）。它们各自证明的是不同的事：

- 本机证明：**分发/投放**这一层在此设备上没有发生；
- 另一台证明：**1.8.65.0 内的 gfx1151 kernel 是可执行的**（该机靠直接注册本地预装 DLL，而非 Catalog 安装）。

合起来才是完整结论：**gfx1151 不是 kernel 层面的阻塞点，Catalog 投放才是。**

---

## 5. 证据完整性问题（如实记录）

| ID | 问题 | 处理 |
| --- | --- | --- |
| C-INT-1 | Experiment A 的 `MANIFEST.sha256` 8/8 条与其已提交 blob 不匹配（`git cat-file blob` 逐字节复核，非换行差异） | 冻结证据不改写；本 Mission 不使用 A 清单背书，所有 A 数值从 JSON 正文复读 |
| C-INT-2 | A 的 `CATALOG_REGISTRATION_CANARY.json` 有 UTF-8/GBK 乱码 | 记录，不影响数值 |
| C-INT-3 | `tools/experiment_b_ab_compare.py` 首次吃真实 A 证据即崩溃，且无 A schema 抽取器 → PR #14 实际是 `A_EVIDENCE_PENDING` | **本 Mission 修复**（新增抽取器 + 数值强转 + CPU-only 测试） |

---

## 6. 复现命令

```powershell
# A 的证据在 PR #15 分支上，用 detached worktree 取原始字节（不 force-push、不改历史）
git worktree add $env:TEMP\a_wt origin/mission/experiment-a-catalog-vs-direct --detach

.venv-winml\Scripts\python.exe tools\experiment_b_ab_compare.py `
  --run-id <NEW_RUN_ID> --a-gfx gfx1100 `
  --a-json $env:TEMP\a_wt\reports\experiment_a\EXPERIMENT_A_RESULT.json `
           $env:TEMP\a_wt\reports\experiment_a\HOST_ENVIRONMENT.json `
           $env:TEMP\a_wt\reports\experiment_a\CATALOG_PROBE.json `
           $env:TEMP\a_wt\reports\experiment_a\EP_PACKAGE_INVENTORY.json `
           $env:TEMP\a_wt\reports\experiment_a\DIRECT_REGISTRATION_CANARY.json `
           $env:TEMP\a_wt\reports\experiment_a\CATALOG_REGISTRATION_CANARY.json `
           $env:TEMP\a_wt\reports\experiment_a\NEGATIVE_TESTS.json `
  --out reports\mission_c\C0_AB_COMPARISON_V2.json
```
