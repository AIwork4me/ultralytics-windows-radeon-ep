# Mission R10 最终报告 — AMDGPU EP 文档纠错与升级上报（Escalation）

日期：2026-11-19（Asia/Shanghai）
主机：HP ZBook Ultra G1a 14 / Ryzen AI MAX+ PRO 395 / Radeon 8060S（gfx1151，DID `0x1586`）
判决：**R10_DOC_TRUTH_RESTORED / AMDGPU_R4_BLOCKED_EXTERNAL_UNCHANGED / ESCALATION_DRAFT_READY**

> 一句话：本机 Windows ML 2.x 的 `ExecutionProviderCatalog` 仍然返回**空列表**（`S_OK` +
> 0 个 provider），所以 GPU 判决一个字都没变；但**仓库此前对官方文档的结论是错的**，
> 错在把 docs 分支上的旧快照当成了线上页面。R10 用线上页面 + 页面哈希把这件事钉死，
> 修正了文档/诊断/README，并把"目录为什么是空的"整理成一份可直接提交的上游 issue 草稿。

---

## 1. 完成了什么（按任务书 A–E）

| 编号 | 内容 | 状态 |
| --- | --- | --- |
| R10-A | 官方文档真值核对：抓取 17 个 URL（含 3 个必抓页面、版本固定视图、docs/main 两个 markdown 源、EP 发布矩阵、EP 错误页、BYO 页），逐条记录访问时间（UTC + Asia/Shanghai）、body sha256、text sha256、页面"Last updated"，并做 HTML ↔ markdown 差异比对 | **完成** |
| R10-A | 按核对结果修正 `docs/AMDGPU_EP_PROVISIONING.md`、`runner/probe_amdgpu_ep.py` 的 `EP_REFERENCE`、`README.md` / `README.zh-CN.md` 的 AMDGPU 措辞 | **完成** |
| R10-B | 只读复测：`runner.probe_amdgpu_ep --gfx gfx1151`，记录 OS build、DID、WDDM 驱动、WinML 运行时、`windowsml` SDK、`find_all_providers()` 原始结果、原始 HRESULT、ReadyState、WU 策略/待重启、ORT EP devices、AMDGPU DLL/MSIX 存在性 | **完成**（`CATALOG_EMPTY` / `BLOCKED_EXTERNAL`） |
| R10-C | 新增 `repro/windowsml_amdgpu_catalog_probe.py`（仅标准库 + 官方 `windowsml`，不 import 本仓库）+ `repro/requirements.txt` + `repro/README.md`，实机运行并把真实输出写进 `docs/mission_r10/AMDGPU_EP_MINIMAL_REPRO.md` | **完成** |
| R10-D | 新增 `docs/mission_r10/UPSTREAM_ISSUE_DRAFT_EN.md`（英文，8 节 + 6 个 maintainer 问题），标记 `ESCALATION_DRAFT_READY`，明确"未提交" | **完成** |
| R10-E | 探针幂等化 + `--diff PREVIOUS.json` + `stable_digest` + `update_state`（WU/Store/重启，只读注册表）；监控命令与**可选**计划任务片段写进文档，**未创建任何计划任务** | **完成** |

没有做的事，明确列出：

* 没有安装/卸载任何系统组件、没有改驱动、没有动 `WindowsApps`、没有新建计划任务；
* 没有执行任何 git 命令（add/commit/push/checkout/stash 全未执行）；
* 没有向 Microsoft / AMD / onnxruntime 提交任何 issue 或 PR；
* 没有修改 `reports/mission_r4/`（R4 证据保持字节不变）；
* 没有声明任何 AMDGPU GPU PASS；没有 gfx1100 / gfx1201 的任何物理结论。

## 2. 为什么这样设计

1. **先证伪"文档结论"，再谈代码。** 本仓库的 AMPGPU 阻塞判决有两层：一层是"本机目录是空的"
   （可测事实），一层是"官方文档压根没有这个 EP"（引用事实）。第二层错了，会导致所有下游
   判断（驱动要不要升、MIGraphX 25.10.13.09 是不是要求）集体跑偏。R10 把引用事实当成一等
   公民：每个数字都必须带 URL + 访问时间 + body 哈希，且区分"线上页面"和"源码文件"。
2. **HTML 和 markdown 都要抓，且要抓两个分支。** 任务书要求"若不一致就都保存、都记哈希、
   列出具体差异"。实际结果比预期更尖锐：不是 HTML 与 markdown 不一致，而是 **docs 分支
   markdown 与 main 分支 markdown 不一致，线上页面跟 main 一致**。只有同时抓两个分支才能
   定位到"R3-D 读的是哪一个文件"。
3. **探针只加"可比较性"，不改"判据"。** `classify()` 五态机、退出码、`BLOCKED_EP` 语义
   一行都没动；新增的只是 `stable_digest`（幂等）、`--diff`（变化）、`update_state`
   （采集 WU/Store 状态）。这样历史证据与历史判决都不受影响，而"是否发生变化"第一次变成
   可机器判定的问题。
4. **最小复现必须能脱离仓库运行。** 上游维护者不会 clone 这个仓库。所以 repro 只依赖
   `windowsml`，用 `ctypes` 直接调 C API 拿 HRESULT，用 DXGI 拿 DID 和驱动版本（不引 pywin32、
   不查 WMI），并且断言 `radeon_ort` 从未被 import。
5. **上报草稿严格限定范围。** 只声明"本机目录为空"，不推断"所有 AMD GPU 都有问题"，不指认
   Windows ML / AMD 驱动 / Store 三者中谁是责任方——把归因留给 maintainer，问题清单写成
   6 个可回答的问题。

## 3. 文档矛盾的根因（这是本次最重要的发现）

**根因：R3-D 把 `MicrosoftDocs/windows-ai-docs` 仓库 `docs` 分支上的 markdown 快照当成了
"线上页面"，而那份快照是 2026-04-28 的旧版本。线上页面和 `main` 分支都是 2026-11-18 的新版本。**

证据链（全部可复算）：

1. R3-D 原文写"page `ms.date` 2026-04-28"、"页面里 `AMDGPU` 0 次、`2.5.96` 0 次、`24.12`
   0 次"。
2. `docs` 分支文件 `supported-execution-providers.md`：`ms.date: 04/28/2026`，
   `AMDGPU` 0 次，`2.5.96` 0 次，`Deprecated` 0 次，`WebGPU` 0 次，MIGraphX MSIX `1.8.55.0`。
   **与 R3-D 的描述逐条吻合**（包括那个 `ms.date`）。
3. `main` 分支同路径文件：`ms.date: 11/18/2026`，`AMDGPU` 4 次，`2.5.96` 2 次，
   MIGraphX 标注 `(Deprecated)`，另有 WebGPU 与 CG EP 章节。
4. 线上页面（HTML）页脚 "Last updated on **2026-11-18**"，正文与 `main` 一致：2.x 表格第一行
   就是 `AMD GPU | AMDGPUExecutionProvider | AMD`，并给出 "Minimum required
   Microsoft.Windows.AI.MachineLearning version: 2.5.96" 与 "Adrenalin Edition 24.12.1 or
   later"。
5. 线上页面与 `main` 的 body 哈希两次抓取完全一致（`67b29c82…fdb013` / `306bb247…d34e71`），
   排除了"抓取时页面刚好在变"的可能。

因此 R3-D 的结论不是幻觉，而是**取错了文件**：它读到了一个真实存在、但已过期 7 个月的文件，
并把它当成"live page"。这正是任务书里"Never present an old source file as current fact"
所指的失败模式。处置方式是**标注取代（supersede）而不是删除**：
`docs/AMDGPU_EP_PROVISIONING.md` 第 2.2 节保留 R3-D 原文 + 错误来源 URL + 错误原因；
`runner/probe_amdgpu_ep.py` 增加 `EP_REFERENCE_SUPERSEDES` 常量记录同一件事；测试断言这条
注释仍然存在，防止以后被"清理"掉。

### 3.1 文档矛盾对照表（旧说法 vs 已验证官方事实）

| # | 仓库旧说法（R3-D，2026-11-19 早先版本） | 已验证官方事实（2026-11-19 抓取） | 来源 URL | 访问时间 | body sha256 |
| --- | --- | --- | --- | --- | --- |
| 1 | "字符串 `AMDGPU` 不出现在页面中（0 次）" | Windows ML **2.x** 表格第一行：`AMD GPU` / `AMDGPUExecutionProvider` / `AMD`；并有一整节 "AMD GPU (AMD)" | <https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers> | 2026-11-19T13:52:29Z（21:52 +08:00） | `67b29c82899ef522791f482da5f1b6845a6d86f3a02eb43ec71e745a27fdb013` |
| 2 | "`2.5.96` 与 `24.12` 也不出现（0 次）" | "Minimum required Microsoft.Windows.AI.MachineLearning version: **2.5.96**"；"**Adrenalin Edition 24.12.1 or later**" | 同上 | 同上 | 同上 |
| 3 | "AMD GPU 行是 MIGraphX，要求 driver 25.10.13.09（精确）" | MIGraphX 单列并标注 **(Deprecated)**；要求 "AMD RDNA 3 or later GPU" + "AMD GPU driver version **25.10.13.09 or later**"（"or later"，非"精确"）；不再代表整个 AMD GPU 行 | 同上 | 同上 | 同上 |
| 4 | "页面 `ms.date` = 2026-04-28" | 线上页面页脚 "Last updated on **2026-11-18**"；`main` 分支 `ms.date: 11/18/2026`；只有 `docs` 分支仍是 `04/28/2026` | 同上 + <https://raw.githubusercontent.com/MicrosoftDocs/windows-ai-docs/main/docs/new-windows-ml/supported-execution-providers.md> | 2026-11-19T13:52:29Z | `306bb2472107fb804412ae03c95025c42f73da95ff730f6b8a705e5193d34e71`（main） |
| 5 | （隐含）EP 通过 Windows Update 的 D-week 可选预览分发 | "With Windows ML **2.5.96 or greater**, execution providers are acquired through the **Microsoft Store**. There are currently no known issues acquiring execution providers in Windows ML 2.5.96 and greater."；WU 暂停/待重启/托管策略只适用 **2.4.89 及更早** | <https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/execution-provider-errors> | 2026-11-19T13:52:29Z | `5cda77adde0d59d4336fdee130ace28bbb33ce69c003f0d47245ef2438204730` |
| 6 | （无此信息）AMD GPU EP 何时进入发布矩阵 | "AMD GPU and MIGraphX (AMD)… current MSIX `1.8.65.0` (2026-10-28)… **AMD GPU EP added**"；"the AMD GPU EP is only compatible with Windows ML 2.5.96 or greater" | <https://github.com/microsoft/WindowsML/wiki/Windows-ML-Execution-Provider-Releases> | 2026-11-19T13:52:29Z | `153355aae9bdf7f407d77fe5873849489cb52d964ad74177694bf516a473e6b4` |
| 7 | "MIGraphX 的 25.10.13.09 不能当作新 EP 的要求" | **这条是对的**：AMD GPU EP 自己的要求是 Adrenalin 24.12.1+；25.10.13.09 仍只属于 MIGraphX | 同 #1 | 同上 | 同上 |

（`docs` 分支旧快照作为"被取代的证据"单独保存：`8fdc3cf4bcfb5021c868c5fa4a557df68167933600c846b10b1530439a7b3404`，
`ms.date: 04/28/2026`。）

## 4. 真实执行过的实验与真实输出

### 4.1 R10-B：只读复测（`runner.probe_amdgpu_ep`）

命令：

```powershell
$env:PYTHONPATH="src"
.venv-winml\Scripts\python.exe -m runner.probe_amdgpu_ep --out reports\mission_r10\PROBE_R10_A_20261119T135033Z.json --gfx gfx1151
```

真实输出（stdout 全文，退出码 2）：

```text
{
  "state": "CATALOG_EMPTY",
  "verdict": "BLOCKED_EP",
  "providers": 0,
  "amd_devices": 0,
  "stable_digest": "c9f7ed5e5cd394aa49c3cb38a8e21e107938263166396a8e4bc2cb2b2f06b492",
  "out": "reports\\mission_r10\\PROBE_R10_A_20261119T135033Z.json"
}
```

采集到的关键字段（摘自 `reports/mission_r10/PROBE_R10_A_20261119T135033Z.json`）：

| 项 | 值 |
| --- | --- |
| `captured_utc` | `2026-11-19T13:50:34Z` |
| OS | `10.0.26200`，build `26200`，UBR `9974`，DisplayVersion `25H2` |
| GPU | `AMD Radeon(TM) 8060S Graphics`，驱动 `32.0.22018.5` |
| `windowsml`（官方 C API） | `2.5.96`，`api_ok=true`，providers **0** |
| 原始 HRESULT | `create=0x00000000`，`enum=0x00000000`，`provider_count=0` |
| 旧 2.3.0 projection | `api_ok=true`，providers **0** |
| ORT | `1.25.2`，providers `DmlExecutionProvider`,`CPUExecutionProvider` |
| ORT EP devices | `CPUExecutionProvider`,`DmlExecutionProvider`（无 AMD） |
| WinML 共享运行时 | `WindowsWorkload.WinMLShared.7.0 1.2610.1050.0` |
| 唯一 AMD EP 包 | `WindowsWorkload.EP.AMD.VitisAI.Framework.1.8 1.8.80.0`（NPU） |
| AMD GPU EP DLL / 包 | 0 / 0 |
| WU 策略 | `NoAutoUpdate`/`AUOptions`/`DisableWindowsUpdateAccess` 等全为 `null`（未设） |
| 待重启 | `RebootRequired`/`RebootPending`/`PackagesPending`/`PostRebootReporting` 均 **false**；`PendingFileRenameOperations` 22 条（软信号） |
| Store | `Microsoft.WindowsStore 22610.1401.8.0` 存在 |
| EP 获取通道 | `microsoft_store`（因 `windowsml=2.5.96`） |
| `state` / `blocked_status` | `CATALOG_EMPTY` / `BLOCKED_EXTERNAL` |

结论与期望一致：**空目录 → `CATALOG_EMPTY` → `BLOCKED_EXTERNAL`，退出码 2**。
`S_OK` 只证明 API 调用成功，**不**证明 AMDGPU 可用；本报告没有、也不会据此声称任何 GPU PASS。

### 4.2 R10-E：幂等性与 `--diff`

连跑两次（间隔 2 秒），第二次带 `--diff`：

```text
{
  "state": "CATALOG_EMPTY",
  "verdict": "BLOCKED_EP",
  "providers": 0,
  "amd_devices": 0,
  "stable_digest": "c9f7ed5e5cd394aa49c3cb38a8e21e107938263166396a8e4bc2cb2b2f06b492",
  "out": "reports\\mission_r10\\PROBE_R10_B_20261119T135036Z.json"
}
[diff] no observable change (same state, catalog, HRESULTs and versions)
```

两次 `stable_digest` 完全相同（`c9f7ed5e…`），证明探针幂等：在主机状态不变时，报告除
`captured_utc` 外逐字段一致。

再以 R4 的历史报告为基线做跨 mission 比对：

```powershell
.venv-winml\Scripts\python.exe -m runner.probe_amdgpu_ep --out reports\mission_r10\PROBE_R10_VS_R4_20261119T135041Z.json --gfx gfx1151 --diff reports\mission_r4\PROBE_AFTER_A1.json
```

```text
[diff] no observable change (same state, catalog, HRESULTs and versions)
```

即：**自 R4 以来本机的 EP 状态没有任何变化**——这正是"这不是偶发、也不是一次插拔能解决"的
可比较证据。（`--diff` 只做比较，永不改变退出码；`--diff` 文件读不到时在报告里记
`diff_vs_previous.ok=false`，退出码仍由 `state` 决定。）

### 4.3 R10-C：最小复现实机运行

命令与真实输出（退出码 **3**）：

```powershell
.venv-winml\Scripts\python.exe repro\windowsml_amdgpu_catalog_probe.py --gfx gfx1151 --out reports\mission_r10\REPRO_RUN1.json
```

```text
===WINML_AMDGPU_CATALOG_PROBE_JSON_BEGIN===
{
  "blocked_status": "BLOCKED_EXTERNAL",
  "catalog": {
    "api_ok": true,
    "error": null,
    "provider_count": 0,
    "providers": [],
    "raw_hresults": {
      "available": true,
      "create_hresult": "0x00000000",
      "enum_hresult": "0x00000000",
      "enumerated_count": 0
    }
  },
  "checks": [
    {"check": "official_windowsml_package", "detail": "2.5.96", "status": "PASS"},
    {"check": "windows_11_24h2_or_later", "detail": "build=26200 (Windows ML 2.x EPs need 24H2 / build 26100+)", "status": "PASS"},
    {"check": "amd_gpu_adapter_present", "detail": "2 AMD adapter(s)", "status": "PASS"},
    {"check": "catalog_create_hresult", "detail": "0x00000000", "status": "PASS"},
    {"check": "catalog_enum_hresult", "detail": "0x00000000", "status": "PASS"},
    {"check": "catalog_provider_count", "detail": 0, "status": "FAIL"},
    {"check": "amdgpu_provider_listed", "detail": "catalog is empty", "status": "FAIL"}
  ],
  "host": {
    "gpu": {"adapters": [
      {"description": "AMD Radeon(TM) 8060S Graphics", "device_id": "0x1586", "driver_version": "32.0.22018.5", "is_software": false, "subsys_id": "0x8D01103C", "vendor_id": "0x1002"},
      {"description": "AMD Radeon(TM) 8060S Graphics", "device_id": "0x1586", "driver_version": "32.0.22018.5", "is_software": false, "subsys_id": "0x8D01103C", "vendor_id": "0x1002"},
      {"description": "Microsoft Basic Render Driver", "device_id": "0x008C", "driver_version": "10.0.26100.9818", "is_software": true, "vendor_id": "0x1414"}
    ]},
    "windows_build": {"build": 26200, "display_version": "25H2", "major": 10, "minor": 0, "platform": "Windows-11-10.0.26200-SP0", "ubr": 9974}
  },
  "read_only": true,
  "stable_digest": "66baf7b657232a2a0a586566807c3b26c0b6131edc1557fa438ef0466ec6150b",
  "versions": {
    "ep_packages": ["WindowsWorkload.EP.AMD.VitisAI.Framework.1.8 1.8.80.0"],
    "onnxruntime": "1.25.2",
    "windows_ml_shared_runtime": "WindowsWorkload.WinMLShared.7.0 1.2610.1050.0",
    "windowsml_python": "2.5.96"
  }
}
===WINML_AMDGPU_CATALOG_PROBE_JSON_END===
```

（上面为便于阅读压缩了空白与 `captured_utc`/`gfx_requested` 等字段；完整原文见
`docs/mission_r10/AMDGPU_EP_MINIMAL_REPRO.md` 第 3 节与 `reports/mission_r10/REPRO_RUN1.json`。）

幂等性：三次运行 `captured_utc` 分别为 `2026-11-19T13:52:19Z` / `2026-11-19T13:52:21Z` /
`2026-11-19T13:58:41Z`（第三次在 `datetime.UTC` 清理之后），`stable_digest` 三次都是
`66baf7b657232a2a0a586566807c3b26c0b6131edc1557fa438ef0466ec6150b`。
脱敏检查：`reports/mission_r10/*.json` 对用户主目录路径（`C:\Users\<name>`）、`LUID`、
`COMPUTERNAME`、`USERNAME` 的扫描结果为 `NO_MATCH`，`radeon_ort.redaction.scan_file` 返回 `LEAKS= []`；同一目录下 8 个 JSON 全部通过。

值得单独记一笔的观察：DXGI 枚举出 **两个完全相同的 8060S 条目**（index 0/1，同 DID、同
子系统、同驱动）+ 一个 `Microsoft Basic Render Driver`。这与 R4 记录"同一物理 GPU 有两个
LUID 别名"一致，也解释了为什么会话里看到重复适配器；本报告不据此推断任何硬件异常。

### 4.4 测试与静态检查（真实结果）

```text
$ .venv-winml\Scripts\python.exe -m pytest -q
2 failed, 410 passed in 60.89s

FAILED tests/hardware/test_gpu_canary.py::test_amdgpu_canary_executes_on_gpu
FAILED tests/hardware/test_gpu_canary.py::test_amdgpu_parity_against_cpu
```

两个失败都是**预期内**的：它们是需要真实 AMDGPU EP 的硬件金丝雀，本机 `amdgpu-ep.dll` 不存在，
异常文本就是 `BLOCKED_EP: amdgpu-ep.dll not found in site-packages or WindowsApps`。
按任务书，这是 `BLOCKED_EP` 的预期表现，不是回归。

```text
$ .venv-winml\Scripts\python.exe -m pytest tests/unit tests/contracts tests/adversarial -q
389 passed
```

```text
$ .venv-winml\Scripts\python.exe -m ruff check src runner tests
Found 192 errors.
```

说明（必须如实写清）：这 192 条**全部是既有问题，位于 R10 不拥有的文件**里
（`runner/rca2a_main.py` 12 条、`tests/adversarial/test_attempt_isolation.py` 11 条、
`runner/r1_pipeline.py` 11 条……），原因是本机 ruff 为 0.17.5、而仓库 `pyproject.toml` 只设了
`line-length` / `target-version`，未锁 `select`，新版 ruff 的默认规则集比写代码时更宽。
R10 自己改/建的文件单独检查为 **All checks passed**：

```text
$ .venv-winml\Scripts\python.exe -m ruff check runner/probe_amdgpu_ep.py tests/unit/test_probe_amdgpu_ep_states.py
All checks passed!
```

按"不修改不属于自己的文件"的硬规则，R10 不去批量修这 192 条；它作为**遗留缺口**记录在 §7。

另外注意：`pytest` 的用例总数在本次任务期间从 369 → 410 持续上升，因为同一工作树里有其它
mission 的 agent 在并发新增测试；本报告引用的 410 是 2026-11-19T21:57+08:00 的快照。

## 5. 通过 / 失败 清单

| 项 | 结果 | 说明 |
| --- | --- | --- |
| 官方文档真值核对（17 URL，含哈希） | **PASS** | 线上页面与 main 分支一致，docs 分支被证伪 |
| `docs/AMDGPU_EP_PROVISIONING.md` 与官方事实一致 | **PASS** | 新增 §2.1/2.2/2.3/§9；R3-D 原文保留并标注 SUPERSEDED |
| `EP_REFERENCE` 与官方事实一致 | **PASS** | 3 个 EP 条目均带 URL + 访问时间 + 哈希；`EP_REFERENCE_SUPERSEDES` 记录取代关系 |
| README 双语 AMDGPU 措辞 | **PASS** | 只说"官方有文档、但本机目录为空、驱动单升不解决" |
| R10-B 只读复测 | **PASS（BLOCKED_EXTERNAL）** | `CATALOG_EMPTY`，0 provider，S_OK，退出码 2 |
| 探针幂等 | **PASS** | 两次 `stable_digest` 相同；与 R4 比对"no observable change" |
| `--diff` 变化检测 | **PASS** | 单元测试覆盖 provider 增/删、HRESULT 变、版本漂移、ORT device 变化、不可读输入 |
| 最小复现（脱离仓库） | **PASS** | 实机跑通，退出码 3，`stable_digest` 幂等，未 import 仓库 |
| 上游 issue 草稿 | **PASS（ESCALATION_DRAFT_READY）** | 英文 8 节 + 6 问题，未提交 |
| 单元测试 | **PASS** | 探针测试 9 → 19 条，全绿 |
| 全量 pytest | **PARTIAL（预期）** | 410 passed / 2 failed（均为 `BLOCKED_EP` 硬件金丝雀） |
| ruff（全仓库） | **FAIL（既有）** | 192 条既有问题，0 条位于 R10 文件 |
| 任何 AMDGPU GPU PASS | **未声明** | 没有 EP、没有 canary、没有 profiler 节点 |

## 6. 开发者价值

1. **把"引用事实"变成可复算的产物。** 以前一句"文档没写"就能改变工程方向；现在每个断言都
   带 URL + 访问时间 + body 哈希，任何人可以重抓一次对比。这类错误（读旧快照）在任何依赖
   外部文档的项目里都会重演，这份方法可以直接复用。
2. **"空目录"第一次成为可比较的信号。** `stable_digest` + `--diff` 让"今天和昨天一样吗"
   从人工肉眼变成一条命令；这正是等待上游修复期间唯一需要的运维动作。
3. **上游沟通成本被压到最低。** `repro/` 里一个文件、一个依赖，任意机器可跑；issue 草稿
   自带 HRESULT、逐项 PASS/FAIL、已排除项和 6 个具体问题，维护者不需要读本仓库任何代码。
4. **判据没有被"顺手放宽"。** 五态机、退出码、`BLOCKED_EP` 语义、R4 证据全部原样保留；
   新增能力都是加法。R10 修的是"我们对文档的说法"，不是"我们对 GPU 的判据"。
5. **避免了一次错误驱动的驱动升级。** 若按旧（错误）结论，唯一"可做"的动作是升驱动；而
   真实情况是 EP 包根本没被提供，升驱动不会产生目录条目。R10 把这条路明确堵死并写进文档。

## 7. 证据路径

| 路径 | 内容 |
| --- | --- |
| `reports/mission_r10/DOC_CITATIONS.json` | 17 个 URL 的访问时间（UTC + Asia/Shanghai）、body/text 大小与 sha256、页面 Last updated、跨次抓取稳定性 |
| `reports/mission_r10/_raw/*.html`、`*.txt`、`*_main.text`、`*_docsbranch.text` | 抓取到的原始 HTML 与 markdown（含被证伪的 docs 分支快照） |
| `reports/mission_r10/PROBE_R10_A_20261119T135033Z.json` | 只读复测 A（schema v3，含 `update_state`、`stable_digest`、`observation`） |
| `reports/mission_r10/PROBE_R10_B_20261119T135036Z.json` | 只读复测 B（同 digest）+ `diff_vs_previous` |
| `reports/mission_r10/PROBE_R10_VS_R4_20261119T135041Z.json` | 与 R4 基线的跨 mission 比对（no observable change） |
| `reports/mission_r10/PROBE_20261119T134753Z.json` | 首次 R10 复测（schema v2，EP_REFERENCE 修正前，保留作前后对照） |
| `reports/mission_r10/REPRO_RUN1.json`、`REPRO_RUN2.json`、`REPRO_RUN3.json` | 最小复现三次运行的完整输出（幂等证明，同 digest） |
| `docs/mission_r10/AMDGPU_EP_MINIMAL_REPRO.md` | 最小复现说明 + 真实输出 + 脱敏说明 + 监控命令（含可选计划任务片段，未创建） |
| `docs/mission_r10/UPSTREAM_ISSUE_DRAFT_EN.md` | 上游 issue 草稿（`ESCALATION_DRAFT_READY`，未提交） |
| `docs/mission_r10/R10_AMDGPU_EP_ESCALATION_FINAL_CN.md` | 本文件 |
| `docs/AMDGPU_EP_PROVISIONING.md` | 修正后的权威技术文档（§2.1 现行事实 / §2.2 被取代记录 / §9 引用表） |
| `runner/probe_amdgpu_ep.py` | 探针（`EP_REFERENCE`、`stable_digest`、`--diff`、`update_state`） |
| `repro/windowsml_amdgpu_catalog_probe.py`、`repro/requirements.txt`、`repro/README.md` | 可独立运行的最小复现 |
| `tests/unit/test_probe_amdgpu_ep_states.py` | 19 条测试（9 条原有 + 10 条 R10 新增） |

## 8. 剩余缺口与下一步

**缺口（如实列出，不掩饰）**

1. **目录为什么是空的，仍然未知。** 现在能确定"EP 包没被提供"，不能确定是 Store 定向、
   Windows ML 过滤，还是驱动的隐藏条件。这需要上游回答（issue 草稿的 1–3 问）。
2. **gfx1151 是否被官方支持，无公开矩阵可查。** 官方页面只给 OS/驱动要求，不给 gfx 列表。
3. **Adrenalin 版本无法只读判定。** 本机 WDDM `32.0.22018.5`（2025-09-17）与 "Adrenalin
   24.12.1+" 不同量纲，只能按发布日期旁证，不能证明；OEM 包是否暴露 Adrenalin 版本也没有
   文档。
4. **无官方签名的 2.x AMD GPU EP 可分发件。** 官方 BYO 页明确 `onnxruntime-ep-amdgpu`
   "must be built from source"，因此 A4 仍然 `BLOCKED_OFFICIAL_EP_ARTIFACT`。
5. **ruff 全仓库 192 条既有问题**，位于 R10 不拥有的文件；R10 未修（不越界）。
6. **issue 未提交。** 需要仓库 Owner 决定是否/何时提交，以及是否附 Feedback Hub 记录。

**下一步（按优先级）**

1. 把 `UPSTREAM_ISSUE_DRAFT_EN.md` 提交到 `microsoft/WindowsAppSDK`（或 Windows ML 反馈渠道）
   并附 `repro` 输出；同时按官方指引在 Feedback Hub 提交 "Developer Platform > Windows
   Machine Learning" 记录并回链。
2. 在等待期间只做**只读**监控：每次 Windows 更新后跑
   `runner.probe_amdgpu_ep --diff <上一次>`，或跑 `repro` 脚本比较 `stable_digest`。
   一旦出现 `ReadyState=NotPresent`，立即按 §5 Phase 3 走 `ensure_ready_async()`。
3. 若上游确认 gfx1151 支持且 EP 可获取：新 `run_id` + `--ep amdgpu,dml` 走严格契约，
   AMDGPU 收据必须有正 profiler 节点、0 CPU 节点、CPU-ORT 逐名 parity、任务 oracle。
4. 若上游确认需要某驱动版本：先记录 HP/AMD 官方安装包与回滚路径，再按 AGENTS.md 的最小范围
   例外流程逐组件执行，前后各一次只读探测。
5. 驱动更新**不是**第一动作：EP 包不存在时，换驱动不会产生目录条目（R10 已把这条写进文档）。

---

### 附：本次 R10 的边界声明

* 只读为主：除 `pip install windowsml==2.5.96`（R4 的 A1，已在此之前完成）之外，本次没有任何
  系统级写入；探针、repro、注册表读取全部只读。
* 未改 `reports/mission_r4/`；R4 的 `BLOCKED_EXTERNAL` 判决与证据保持原样。
* 未创建计划任务、未安装 EP、未改驱动、未提交上游、未执行 git 命令。
* 语言：本报告中文，判决标签与状态码保持英文；`docs/AMDGPU_EP_PROVISIONING.md` 与上游草稿
  保持英文以匹配其既有语言与受众。