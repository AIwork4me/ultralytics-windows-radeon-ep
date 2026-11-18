# GFX1201 独立硬件验证 — 最终报告（中文）

**run_id**: GFX1201_DIAG_20261118T2359Z ｜ **日期**: 2026-11-18/19 ｜ **代码基线**: main `7cef0c06`（含已合并 PR #4）

## 结论（先说真话）

**`GFX1201_NO_GO_HARDWARE_ABSENT`（整体 NOT_RUN，fail-closed）**

本任务书假设执行主机为 RX 9070 XT (gfx1201)。**物理核查证明本机 AMD 独显是 RX 7900 XTX
（PCI\VEN_1002&DEV_744C = gfx1100）**，另 Intel UHD 770 与两个虚拟显示适配器。
依据 AGENTS.md 规则 2（无该硬件不得宣称）与任务书 Gate M0（缺真卡 → M2=NOT_RUN）：

- 七任务（detect/segment/classify/pose/OBB/semantic/depth）gfx1201 真机执行：**全部 NOT_RUN**
- 15 分钟有效稳态 soak：**NOT_RUN**（见 SOAK.json）
- 本机 gfx1100 上的一切成功（canary/smoke）**只证明代码路径正确，绝不冒充 gfx1201 证据**

## 本轮真实完成的工作

1. **B0 设备身份缺陷修复（红→绿）**：`0x7480→gfx1201` 错误映射移除（Navi 48 正确 ID 为
   0x7550/0x7551，pci.ids 权威）；非 gfx1151 目标"泛选全部 AMD 适配器"的静默选择漏洞改为
   per-gfx DID 钉定；gfx1151 名字回退的泛化 "Radeon" 针名漏洞收紧为 "8060s"。红测试 12 失败
   → 修复后 20 通过 + 1 跳过；本机真实 DXGI 枚举验证：gfx1100 正确选中，gfx1151/gfx1201
   如实报错并指名发现的硬件。
2. **Windows ML 2.x Catalog 只读发现**（本机首次记录）：projection 可用，Catalog 列出
   AMDGPUExecutionProvider（ReadyState=SUCCESS, Certification=UNCERTIFIED）与
   MIGraphXExecutionProvider（SUCCESS/CERTIFIED）；library_path 均为空 → BYO/MSIX 注册
   （WinML.AMD.GPU.EP.Framework.2 v1.8.65.0）仍为可用 DLL 路径。未做任何安装动作。
3. **统一运行入口真机 smoke（gfx1100，明确标注）**：`runner.unified_predict` CLI 三条路径
   全部符合契约——AMDGPU `GPU_FULL`+`RUNRESULT_VALID`（exit 0）、DML 策略下 AMDGPU 直接过
   （exit 0）、gfx1201 请求 `BLOCKED`（exit 34）指名 gfx1100 硬件；契约错误 exit 2。
4. **证据冻结**：HOST_FINGERPRINT / EP_INSTALL_AND_PROBE / DEVICE_IDENTITY / GPU_CANARY /
   MODEL_TASK_MATRIX / SOAK(NOT_RUN) / AMDGPU_RCA_REGISTER / MIN_REPRODUCE / manifest + 脱敏扫描。

## 何处需要真 RX 9070 XT 才能继续

按 `MIN_REPRODUCE.txt` 在真卡主机执行：预期 `GPU_FULL`；若 AMDGPU EP MSIX 缺 gfx1201
kernel images → 如实 `BLOCKED_UNSUPPORTED_ARCH`（不改驱动）。DML 为独立对照路径；
CPU 仅在用户显式 `--allow-cpu` 后可用（本运行入口已强制实现）。

## 三平台现状一句话

- **gfx1151 (8060S)**：历史 main 证据（16 模型 × 2 EP），未在新统一入口回归。
- **gfx1100 (7900 XTX)**：PR #4 已合并（RCA2A，42 收据/900s soak）；本分支新增统一入口 smoke。
- **gfx1201 (9070 XT)**：**无硬件，NOT_RUN**；设备身份映射与运行入口已备好，等卡即测。
