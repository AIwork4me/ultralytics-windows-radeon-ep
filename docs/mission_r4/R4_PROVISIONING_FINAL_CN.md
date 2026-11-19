# Mission R4 最终报告（Windows ML 2.x AMDGPU EP Provisioning）

日期：2026-11-19（Asia/Shanghai）
主机：`host_53f62d2ac2c6` / HP ZBook Ultra G1a 14 / Radeon 8060S（gfx1151, DID `0x1586`）
判决：**R4_BLOCKED_EXTERNAL**

---

## 1. Owner 授权与仓库规则的协调

仓库默认规则（`AGENTS.md` 第 6 条）禁止修改驱动、WindowsApps 与系统组件。本次任务由
Owner 明确授权 A1–A5，因此按「最小范围例外」处理，并把例外写进 `AGENTS.md`：

- 例外只适用于**被点名的物理验证主机**：`host_53f62d2ac2c6`，gfx1151（DID `0x1586`）；
- 每次只允许一个组件，必须记录来源/版本/回滚路径，并在操作前后各做一次只读探测；
- CI、共享 Runner、第三方主机与 fork PR 永不适用；
- 禁止关闭 Defender/安全启动/签名校验、禁止非 OEM 来源驱动、禁止改动 WindowsApps 内容、
  禁止强装签名损坏的 MSIX。

在本主机上，实际执行的系统级操作只有 **A1**（用户级 Python 包安装）。

| 授权项 | 计划 | 实际结果 |
| --- | --- | --- |
| A1 官方 Windows ML Python 包 | `pip install windowsml==2.5.96` | **已执行**（PyPI，用户级） |
| A2 AMD GPU 驱动更新 | 仅在证明是阻塞时 | **未执行**（无证据表明是阻塞；无官方要求可比对） |
| A3 官方 Catalog 动态安装 | 有 Catalog 条目时 | **无法执行**（Catalog 为空，无条目可 `ensure_ready`） |
| A4 官方签名 BYO EP | 仅有可信官方包时 | **未执行**（无官方 2.x AMD GPU EP 包，`BLOCKED_OFFICIAL_EP_ARTIFACT`） |
| A5 Windows Servicing | 仅当诊断为阻塞时 | **未执行**（无暂停更新、无待重启；WU 不提供相关组件） |

---

## 2. 环境锁定（变更前）

| 项 | 值 |
| --- | --- |
| OS | Windows 11 25H2，build 26200.9974 |
| GPU | AMD Radeon(TM) 8060S Graphics，`PCI\VEN_1002&DEV_1586` |
| DXGI LUID | `0x00000000:0x000154E4`（同一物理 GPU 另有一个别名 LUID `0x00000000:0x00CA17CF`，枚举去重后选 index 0） |
| 驱动 | 32.0.22018.5，2025-09-17，`oem21.inf`（HP OEM） |
| AMD 软件包 | `AdvancedMicroDevicesInc-2.AMDRadeonSoftware 10.25.20179.0` |
| Adrenalin 版本 | 未暴露（WMI/DXGI/注册表均不提供） |
| Windows ML 共享运行时 | `WindowsWorkload.WinMLShared.7.0 1.2610.1050.0` |
| Windows App Runtime | 1.7 / 1.8 / 2.7（另有 CBS 1.6 / 2） |
| 唯一 AMD EP 包 | `WindowsWorkload.EP.AMD.VitisAI.Framework.1.8 1.8.80.0`（**NPU**，非 GPU） |
| ORT（`.venv-winml`） | `onnxruntime-windowsml 1.25.2.202605110140`，providers = DML + CPU |
| Windows Update | 未暂停、无待重启；仅提供 1 个音频驱动更新 + Defender 定义 |
| 远程/恢复 | RDP 会话可用；无常驻服务被修改，回滚 = 卸载 `windowsml` |

---

## 3. A1：官方 Windows ML 2.x 接入（已执行）

隔离验证环境 `.venv-r4` 与主环境 `.venv-winml` 均安装：

    pip install "windowsml==2.5.96"

结果（2026-11-19）：

| 检查 | 结果 |
| --- | --- |
| `import windowsml` | 成功 |
| 官方 API 形态 | 扁平 C API（`EpCatalog` / `ExecutionProvider` / `WinMLEpEnsureReady` …） |
| `EpCatalog().find_all_providers()` | 成功返回，**0 个 provider** |
| 原始 HRESULT（`WinMLEpCatalogCreate` / `EnumProviders`） | `0x00000000` / `0x00000000` |
| 旧 2.3.0 Projection（`wasdk`） | `api_ok=true`，**0 个 provider** |
| 与 `onnxruntime-windowsml` 兼容性 | 未叠加第二个 ORT wheel；ORT 仍为 1.25.2 |
| 错误/HRESULT | 无失败调用；无异常 |

说明：Microsoft 文档明确指出 Python API 不应使用 `EnsureAndRegisterCertifiedAsync()`
（它不会把 EP 注册进 Python 的 ORT 环境），正确路径是
`provider.ensure_ready_async()` 之后 `ort.register_execution_provider_library(...)`。
本机没有 provider 可调用，因此该路径无法继续。

---

## 4. A2/A3/A4/A5：为什么没有执行

### 4.1 A2 驱动

- 现行官方文档（2026-11-19 抓取）**没有** `AMDGPUExecutionProvider` 这一条目，也没有
  「Windows ML 2.5.96+ / Adrenalin 24.12.1+」的要求；AMD GPU 行是 MIGraphX，要求
  「driver 25.10.13.09（精确）」。把 MIGraphX 的要求套用到新 EP 上是错误的。
- Windows WDDM 驱动号（32.0.22018.5）与 Adrenalin 版本号不同量纲，不能凭数字大小断言兼容。
- Windows Update 未提供 AMD 显示驱动；当前驱动是 HP OEM 包。
- 更换一块正常工作的 OEM 显示驱动，在没有证明阻塞的情况下违反「最小变更」。

### 4.2 A3 Catalog 动态安装

`find_all_providers()` 返回空列表，没有 `AMDGPUExecutionProvider`（也没有 MIGraphX /
VitisAI）可以 `ensure_ready()`。**没有可下载对象**，因此不是「安装失败」，而是「Catalog
未向该设备提供任何 EP」。

### 4.3 A4 BYO 官方签名 EP

本机与可核实渠道均无官方签名的 2.x AMD GPU EP MSIX；仓库历史中出现的
`MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8 ... amdgpu-ep.dll` 属于另一台机器、另一代
（1.8-era）包。按 AGENTS.md 第 2 条与任务书 A4 的限制，不得把它当作 2.x EP 使用。
记录 `BLOCKED_OFFICIAL_EP_ARTIFACT`。

### 4.4 A5 Windows Servicing

更新未暂停、无待重启事务；系统运行时（WinMLShared 7.0、WindowsAppRuntime 1.7/1.8/2.7）
已满足 WinML 2.x 的运行时前提。没有证据表明 servicing 是阻塞原因。

补充观察（不构成结论）：AppXDeployment 日志中存在 `Windows.Workload.PhiSilica.STX.amd64`
等 Workload 包部署失败（`0x80073D3B`）与扩展注册被拒（`0x80070005`）的记录。这与
「本机没有 AMD GPU EP 包」一致，但无法仅凭只读日志证明 EP 缺失就是这些失败造成的，故不作为
已证结论。

---

## 5. 结论与判据

**R4_BLOCKED_EXTERNAL**：

1. 官方 Windows ML 2.x API 正常（绑定已安装、Catalog 可调用、S_OK）；
2. 主机满足 OS / 运行时前提；
3. 但操作系统 Catalog 不向本设备提供任何 EP，且本机没有 AMD GPU EP 产物；
4. 因此无法完成「注册到当前 Python ORT 进程 → AMDGPU Canary 执行」链路。

未声明任何 AMDGPU GPU PASS；未进行 AMDGPU 执行尝试。

### 最小复现

    .venv-winml\Scripts\python.exe -m runner.probe_amdgpu_ep --out reports\mission_r4\PROBE_AFTER_A1.json --gfx gfx1151

期望（阻塞时）：`state=CATALOG_EMPTY`、`verdict=BLOCKED_EP`、退出码 2；证据见
`reports/mission_r4/PROBE_AFTER_A1.json`。

### 升级路径（按优先级）

1. 等待/触发 Windows Update 的 EP 包（D-week 可选预览），使 Catalog 出现
   `ReadyState=NotPresent` 的 AMD GPU EP；随后 `ensure_ready_async()` →
   `ort.register_execution_provider_library` → Canary。
2. 若 Microsoft 发布官方签名的 2.x AMD GPU EP MSIX：校验发布者/包身份/SHA256 后走 A4，
   并明确标注 `bootstrap=byo_msix`。
3. 驱动只在 EP 文档给出**该 EP 自身**的明确最低版本、且本机低于该版本时才更新，并保留
   HP/AMD 官方安装包与回滚路径。

---

## 6. 证据文件

| 文件 | 内容 |
| --- | --- |
| `reports/mission_r3_r6/BASELINE_AUDIT.json` | 变更前基线（git/OS/GPU/驱动/Python/EP/MSIX） |
| `reports/mission_r4/PRE_PROVISION_SNAPSHOT.json` | A1 前后环境快照与操作记录 |
| `reports/mission_r4/PROBE_AFTER_A1.json` | 只读探测（五态机、HRESULT、依赖、建议） |
