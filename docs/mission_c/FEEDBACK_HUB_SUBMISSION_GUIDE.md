# Feedback Hub 提交指南（未能自动化，需人工操作）

**状态：未提交。** 本 Mission 运行在自动化会话中，无法操作 Feedback Hub 的图形界面；
因此只提供可执行步骤，**不声称已经提交**。

官方依据：<https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/execution-provider-errors>
（该页面在 "How to file feedback" 一节要求用 Feedback Hub 报 EP 获取问题，
类别为 **Developer Platform > Windows Machine Learning**。）

---

## 1. 为什么还要单独报 Feedback Hub

我们已经在 GitHub 上提交了可复现的技术报告
（<https://github.com/microsoft/WindowsAppSDK/issues/6994>）。但 Windows ML 团队
处理 **EP 下载/安装类** 问题时，通常还需要 Feedback Hub 条目，因为 Feedback Hub
可以把诊断日志直接附到微软内部的工单上。两者互补：

| 渠道 | 作用 |
| --- | --- |
| GitHub issue #6994 | 公开、可长期引用、可被社区检索的技术事实 |
| Feedback Hub | 携带微软内部诊断日志，进入产品组的分诊流程 |

## 2. 提交前准备（本机已完成，可直接引用）

| 物料 | 位置 |
| --- | --- |
| 复现探针（只读） | `tools/mission_c_catalog_internals.py` |
| Catalog 证据 | `reports/mission_c/C1_CATALOG_INTERNALS.json` |
| 机器环境 | `reports/mission_c/C0_BASELINE_AUDIT.json` |
| DML 未受损证明 | `reports/mission_c/C5_DML_REGRESSION.json` |
| ETW 采集计划（如需） | `reports/mission_c/C3_ETW_CAPTURE_MANIFEST.json` |

## 3. 操作步骤

1. `Win + F` 打开 Feedback Hub（或开始菜单搜索 "Feedback Hub"）。
2. 选择 **Report a problem**。
3. 类别选择：**Developer Platform** → **Windows Machine Learning**。
4. 标题（英文，≤ 120 字符）：

   ```
   Windows ML 2.x ExecutionProviderCatalog returns zero providers on Radeon 8060S (gfx1151)
   ```

5. 描述正文：复制 §4 的英文文本。
6. **Recreate my problem** 页：
   - 勾选 *Record* 之前请先关闭其它占用 GPU 的应用；
   - 依次运行：
     ```powershell
     .venv-winml\Scripts\python.exe tools\mission_c_catalog_internals.py
     ```
   - 停止录制。
7. **Attachments（可选但强烈建议）**：
   - `C1_CATALOG_INTERNALS.json`（已脱敏）
   - 若已按 `C3` 采集，附 `WinmlRundown.log`（脱敏后）。
   - **不要** 附原始 ETL 到公开渠道；ETW 可能含进程路径。若需要附 ETL，
     只在 Feedback Hub 内附（不走公开 GitHub）。
8. 提交后记录 Feedback Hub 链接（形如 `feedback-hub:?contextid=...`）。

## 4. 英文描述正文（可直接粘贴）

```
Windows ML 2.x ExecutionProviderCatalog returns S_OK but enumerates ZERO execution
providers on an AMD Radeon 8060S (gfx1151, PCI DID 0x1586), so no EP can be
acquired at all.

Environment
- Windows 11 25H2, build 26200.9974
- HP ZBook Ultra G1a 14, Ryzen AI MAX+ PRO 395, Radeon 8060S (gfx1151)
- PnP: PCI\VEN_1002&DEV_1586&SUBSYS_8D01103C&REV_D1
- Display driver 32.0.22018.5 (HP OEM)
- windowsml 2.5.96 (documented minimum), onnxruntime-windowsml 1.25.2
- WindowsWorkload.WinMLShared.7.0 1.2610.1050.0 installed
- DirectML works on this machine (canary: 1 Dml node, numeric parity allclose)
- Microsoft Store present; AppInstallManager and StoreContext.GetDefault() both succeed

Observed
- WinMLEpCatalogCreate -> S_OK
- WinMLEpCatalogEnumProviders -> S_OK, 0 providers (3/3 runs, separate processes)
- WinMLEpCatalogFindProvider("AMDGPUExecutionProvider", NULL) -> 0x80070490
- WinMLEpCatalogFindProvider(..., "Microsoft.WinML.AMD.GPU.EP.Framework.2_8wekyb3d8bbwe") -> 0x80070490
- WinMLEpCatalogCreateWithMode(0|1|2) -> S_OK, 0 providers; modes 3+ -> E_INVALIDARG
- WinAppSDK 2.3 projection -> 0 providers
- No amdgpu-ep.dll anywhere; no AMD GPU EP package in any scope
- Even WebGpuExecutionProvider (documented requirement: any DX12 GPU) is absent

Control
- Same windowsml 2.5.96 runtime binary (SHA256 8205a572...d7be0) on an RX 7900 XTX
  (gfx1100) host returns 4 providers: AMDGPU (NotReady), MIGraphX (NotReady),
  OpenVINO (NotPresent), WebGpu (NotPresent).

Question
Which inputs does the Windows ML 2.x catalog use to decide that this device gets
no entries, and what is the supported way to acquire the AMD GPU EP
(Store product 9PNKGVD4CPCV, "Windows ML Runtime AMD GPU Execution Provider
Framework 2") on a Radeon 8060S?

Public technical report with full evidence:
https://github.com/microsoft/WindowsAppSDK/issues/6994
```

## 5. 隐私注意事项

- Feedback Hub 会自动附带机器名、会话与部分路径；这是微软内部工单，**不要**
  把其中的内容再转录到公开 GitHub。
- 描述里不要写用户名、SID、序列号、账户邮箱。
- 若附 ETL：先按 `C3` 的脱敏步骤处理，并确认不含个人路径。

## 6. 提交后的状态维护

| 字段 | 填写位置 |
| --- | --- |
| `ESCALATION_STATUS.json` → `feedback_hub.status` | 改为 `SUBMITTED` 并填链接 |
| `feedback_hub.submitted_utc` | ISO8601 UTC |
| 重新运行 verifier | `tools/verify_mission_c_evidence.py` |
