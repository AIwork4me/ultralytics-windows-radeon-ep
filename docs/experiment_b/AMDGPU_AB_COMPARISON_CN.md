# AMD GPU EP A/B 对照 — gfx1100 vs gfx1151

- **Schema**: `AMD_GPU_EP_AB_COMPARISON_V1`
- **Run ID（gfx1151 侧）**: `B_GFX1151_AMDGPU_20261120T0336Z`
- **A 侧证据状态**: **`A_EVIDENCE_PENDING`**（实验 A 的机器可读 JSON 尚未到达本机）
- **判定**: `GFX1151_BLOCKED_OFFICIAL_EP_ARTIFACT`（**Case B5**）
- **机器可读记录**: `reports/experiment_b/AB_COMPARISON.json`

## 1. 为什么 A 侧是 PENDING

按照契约，缺失字段必须记为 `A_EVIDENCE_PENDING` / `UNKNOWN`，**不得自行推导 PASS**。因此：

- 依赖 A 事实的 **Case B1**（gfx1100 catalog 为空 + 直连成功）与 **Case B2**（gfx1100 catalog 列出 AMDGPU）
  **本次不主张**；
- 本次只主张仅凭本机证据即可判定的 **Case B5**。

## 2. 逐字段对照（25 字段，格式：值 [状态]）

| 字段 | gfx1100（实验 A） | gfx1151（本机实测） | 比较 |
| --- | --- | --- | --- |
| `schema_version` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | AMD_GPU_EP_AB_COMPARISON_V1 **[PASS]** | - |
| `git_sha` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | 767efd71d6106be2fe63895743be8ff6bf652b2f **[PASS]** | - |
| `run_id` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | B_GFX1151_AMDGPU_20261120T0336Z **[PASS]** | - |
| `host_gfx` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | gfx1151 **[PASS]** | - |
| `device_did` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | 0x1586 **[PASS]** | - |
| `driver_wddm` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | 32.0.22018.5 **[PASS]** | - |
| `driver_adrenalin` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | UNKNOWN **[UNKNOWN]** | - |
| `windows_build` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | 26200.9974 **[PASS]** | - |
| `windowsml_version` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | 2.5.96 **[PASS]** | - |
| `ort_version` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | 1.25.2 **[PASS]** | - |
| `catalog_api_hresult` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | create=0x00000000, enum=0x00000000 **[PASS]** | - |
| `catalog_provider_count` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | 0 **[PASS]** | - |
| `catalog_amdgpu_state` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | NOT_LISTED **[BLOCKED]** | - |
| `ep_package_identity` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | ABSENT_MACHINE_WIDE **[BLOCKED]** | - |
| `ep_package_version` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | ABSENT **[BLOCKED]** | - |
| `ep_dll_sha256` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | ABSENT **[BLOCKED]** | - |
| `direct_registration_status` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | BLOCKED_NO_DLL **[BLOCKED]** | - |
| `catalog_registration_status` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | BLOCKED_CATALOG_EMPTY **[BLOCKED]** | - |
| `gpu_executed` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | False **[NOT_RUN]** | - |
| `gpu_ep_profile_nodes` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | 0 **[NOT_RUN]** | - |
| `cpu_ep_profile_nodes` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | (none) **[NOT_RUN]** | - |
| `numeric_parity` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | NOT_RUN **[NOT_RUN]** | - |
| `task_parity` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | NOT_RUN **[NOT_RUN]** | - |
| `blocker_reason` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | B_BLOCKED_OFFICIAL_ARTIFACT_UNAVAILABLE **[BLOCKED]** | - |
| `evidence_manifest_sha256` | A_EVIDENCE_PENDING **[A_EVIDENCE_PENDING]** | 1237592ef79f5ec302e2f6f5cc2ca602bd7c07c0471a9787260eb51beedf9535 **[PASS]** | - |

> 注意 `driver_adrenalin`：这不是"数字大小"的比较。WDDM `32.0.22018.5` 与 Adrenalin 市场版本号之间
> **没有任何官方换算规则**，所以本机标 `UNKNOWN`，而不是"26.x > 24.12.1 所以满足"。
> 见 `DRIVER_COMPATIBILITY.json` 的 `adrenalin_software_version` 与 `compatibility_findings`。

## 3. 仓库内的历史 gfx1100 证据（**不是**实验 A）

仓库存在 gfx1100 的历史直连证据：
`reports/gfx1100/unified_canary_20261118T2359Z/AMDGPU_CANARY.json`
（RX 7900 XTX、`device_id 0x744C`、`status=GPU_PASS_VALIDATED`、target nodes 1 / cpu nodes 0；
同目录 `AMDGPU_SMOKE.json` 亦为 `GPU_FULL`）。

它能说明的是：**gfx1100 上直连 AMDGPU EP 曾经成功**。它不能说明：

1. 实验 A 的 `run_id` / `git_sha` 绑定 —— 不是本次 A/B 契约的产物；
2. gfx1100 的 **catalog** 是否列出 AMDGPU —— 而这正是 B1/B2 的分水岭；
3. gfx1151 的任何能力。

按 AGENTS.md 第 2 条，不允许把历史 receipt 当成本次 A 的证据，也不允许用它推断 gfx1151。

## 4. 判定逻辑（工具实现，顺序即优先级）

```text
B4  DLL 可注册但 session 失败                  -> GFX1151_RUNTIME_OR_MODEL_COMPATIBILITY_FAILURE
B3  B.catalog 为空 且 B 直连 GPU 执行成功       -> GFX1151_DIRECT_GPU_GO_CATALOG_BLOCKED
B2  A.catalog 列出 AMDGPU 且 B.catalog 为空     -> HOST_SPECIFIC_CATALOG_FILTER_OR_DEPENDENCY
B1  A.catalog 空 且 A 直连成功 且 B.catalog 空  -> DIRECT_PATH_WORKS_ON_GFX1100; CATALOG_PATH_NOT_PROVEN
B5  B 无官方可获取产物 且 catalog 空            -> GFX1151_BLOCKED_OFFICIAL_EP_ARTIFACT
```

本机命中 **B5**，理由（写入 JSON 的 `decision.reasoning`）：

- `B catalog_provider_count=0`（state = `NOT_LISTED`）
- `B direct registration=BLOCKED_NO_DLL`，`gpu_executed=False`
- `B official artifact available=False`
- A 证据未提供 ⇒ 不主张任何依赖 A 的结论

## 5. 把 A 证据补进来的方法（一条命令）

```powershell
.venv-winml\Scripts\python.exe tools\experiment_b_ab_compare.py ^
  --run-id B_GFX1151_AMDGPU_20261120T0336Z ^
  --a-json <实验A的CATALOG_PROBE.json> <实验A的AMDGPU_CANARY.json>
```

支持的 A 输入形态（可叠加，按顺序合并，先到先得）：

| 形态 | 识别方式 | 提取字段 |
| --- | --- | --- |
| AB schema | `schema_version == AMD_GPU_EP_AB_COMPARISON_V1` | 取 `hosts.<gfx>` 块 |
| probe 报告 | 含 `api_diagnostic` / `ep_readiness` | HRESULT、provider 数、catalog 状态、驱动、build |
| canary | `schema == unified_canary_v1` | gfx、DID、节点数、parity、直连状态 |
| smoke | 含 `run_result` | actual_provider、节点数、parity、task oracle、DID |

字段缺失一律 `UNKNOWN`；工具不会用别的字段去补，也不会把 `NOT_RUN` 升级为 `PASS`。

## 6. 若 A 证据到达，可能出现的结果

| A 事实 | 结论 | 对根因的含义 |
| --- | --- | --- |
| A catalog 空，A 直连 PASS | Case **B1** | 两台机器的 **catalog 部署链路**共同失效；不能断言 gfx1151 不支持 |
| A catalog 列出 AMDGPU，B 空 | Case **B2** | 主机特定过滤/依赖差异（DID、驱动、OEM、Store、OS） |
| A 数据缺失 | **A_EVIDENCE_PENDING** | 仅保留 B5，不做 A/B 归因 |
