# Mission R3 最终审阅（CPU-only hardening 与 PR #8 / #9 收口）

日期：2026-11-19（Asia/Shanghai）
执行主机：HP ZBook Ultra G1a 14 / Ryzen AI MAX+ PRO 395 / Radeon 8060S（gfx1151, DID 0x1586）
范围：R3-A 退出码传播、R3-B Harness Contract 完整性、R3-C 只读 EP 探测、R3-D 驱动要求校正、R3-E 证据审计与 PR 收口。

判决：**R3_FINAL_HARDENING_GO**（CPU-only 部分；GPU 结论仍由 R4/R5 决定）

---

## 1. 基线（本 Mission 开始时实测）

| 项 | 值 |
| --- | --- |
| PR #8 head | `13b30f5f86ed2c2e41717658a12fe52a3afa77d2`（base main `d24f737`） |
| PR #9 head | `d6d61f1249e3024f018488fafd59aa8c0c4882a8`（base = PR #8 分支） |
| PR #6 head | `a9075dc80be5ea240f290c74f5e11ab951d372b5`（BEHIND） |
| main | `d24f7377813cddcab744c14c82733e890b881234` |
| 本地 CPU 测试 | 243 passed / 1 skipped |
| 网络 | `github.com:443` 不可达；`api.github.com` 可用（gh 已认证） |

由于 git 传输被阻断，远端分支通过 GitHub API 读取，并用「提交对象重建」写入本地对象库：
按 API 返回的 tree/parents/author/committer/message 逐字节重建 commit 对象，SHA 与远端完全一致
（8 个对象，全部校验通过）。因此本地 `d6d61f1` 与远端 PR #9 head 是同一个对象，后续提交可逐字节复现。

---

## 2. R3-A：退出码传播

### 问题

`runner.run_matrix` 在 `r1_pipeline` 返回非零时直接 `return 1`，把「已分类的外部阻塞」
（应为 2）折叠成「真实失败」（1），并且丢失阶段的原始退出码与首错原因。

### 修复

- `runner/r1_pipeline.py`：新增 `classify_exit(n_pass, n_fail, run_problems)`，
  规则为
  - `run_problems`（校验/契约/写盘失败）→ 1 `EVIDENCE_INVALID`；
  - `n_fail > 0` → 1 `CASE_FAILED`；
  - `n_pass == 0` → 2 `NO_GPU_PASS`（全部阻塞/未执行）；
  - 否则 0 `OK`。
  运行结束打印 `[r1] EXIT classification=... code=...` 并写 `<run_dir>/PIPELINE_EXIT.json`
  （receipts/pass/fail/blocked、run_problems、环境探测错误）。
- `runner/run_matrix.py`：新增 `_run_pipeline()`（流式转发 stdout、落盘
  `<run_dir>/logs/r1_pipeline.log`、提取首错行）、`pipeline_classification()` 与
  `_write_exit_record()`。写 `MATRIX_EXIT.json`：原始 `pipeline_exit_code`、分类、
  脱敏 argv、audit 计数与问题。映射：producer 2 → coordinator 2；producer 1/异常/崩溃 → 1。
- 被捕获的异常不会变成成功：receipt 写盘失败进入 `run_problems` → 1。

### 测试

`tests/unit/test_exit_code_propagation.py`（7 项）：
纯分类函数、真实子进程 `rc=2` 与 `rc=7` 的捕获、`main()` 的 0 / 2 / 1 / 设备未验证路径，
并断言 `MATRIX_EXIT.json` 保留 `pipeline_exit_code` 与 `classification`。

---

## 3. R3-B：Harness Contract 完整性

### 3.1 哈希范围

`HARNESS_DIRS` 由 `("runner", "src/radeon_ort")` 扩展为
`("runner", "src/radeon_ort", "configs", "schemas")`，加 `pyproject.toml`，
与 `docs/GPU_EVIDENCE_CONTRACT.md` 声明的范围一致：模型导出、推理/校验代码、阈值、
Receipt Schema 全部纳入绑定。`reports/`、`__pycache__`、`.pytest_cache`、`.ruff_cache`、
虚拟环境被显式排除（它们是产物，不是产生结论的代码）。

`code_state()` 的 dirty 判定改为 `git status --porcelain --untracked-files=all`，
未跟踪的 harness 文件（新模块）不再能绕过 Dirty 检查；被 .gitignore 忽略的文件仍不可见。
契约额外记录 `harness_file_count` 与 `harness_dirs`，审计者无需反推范围。

### 3.2 运行环境绑定（新增）

`RUN_CONTRACT.json` 新增 `environment` 块：python 版本、OS build、ORT 版本与发行名、
`get_available_providers()`（排序）、`windowsml` 版本（解释器只记录 basename，不泄露用户路径）。
每个新 Receipt 的 `contract` 块同样携带。Resume 语义：

| 情况 | 结果 |
| --- | --- |
| 环境漂移 | `ENVIRONMENT_CHANGED: <field> ...` |
| 契约无 environment 块 | `ENVIRONMENT_UNBOUND` |
| 调用方无法提供实时探测 | `ENVIRONMENT_UNVERIFIED` |
| Receipt 环境与契约不同 | `RECEIPT_ENVIRONMENT_MISMATCH` |

因此安装/升级 EP、ORT 或 Windows ML 一定要求新 `run_id`。

### 3.3 证据字节精确

`.gitattributes` 增加 `reports/** -text`：证据文件不做行尾转换，Manifest 哈希在任意
平台/检出下都与提交字节一致。这也修复了一个既有缺陷：某 fingerprint 以 CRLF 提交，
在 Windows 检出上永远显示为修改状态。

### 3.4 篡改回归矩阵

`tests/adversarial/test_contract_tamper_matrix.py`（17 项）覆盖任务书要求的全部类别：

| 篡改 | 断言 |
| --- | --- |
| 修改 thresholds | `HARNESS_CONTENT_CHANGED` / `THRESHOLDS_CONFIG_CHANGED` |
| 修改 Schema | harness 哈希变化 |
| 修改 harness Python | harness 哈希变化 |
| 替换 ONNX | `MODEL_ONNX_MISMATCH` |
| 修改输入 Tensor | `INPUT_TENSOR_MISMATCH` |
| 替换 EP | `RECEIPT_EP_MISMATCH` |
| 修改设备 DID | `DEVICE_IDENTITY_CHANGED` / `RECEIPT_DEVICE_MISMATCH` |
| 注入伪造 PASS | `strict validation failed` → exit 1 |
| 插入额外 Receipt | `unexpected receipt` → exit 1 |
| 删除 Receipt | NOT_RUN → exit 2 |
| Commit HEAD 不匹配 | `CODE_SHA_CHANGED` |
| 未跟踪/脏 harness | `CODE_STATE_DIRTY` |
| 环境变化 | `ENVIRONMENT_CHANGED` |
| reports/ 写入 | harness 哈希不变 |

结论：任何不可信 Receipt 都无法通过最终 GPU PASS 审计。

---

## 4. R3-C：AMDGPU EP 只读探测

旧探测器把未安装 `windowsml` 时的零计数当作「成功调用官方 API」，verdict 恒为
`BLOCKED_EP`，且阻塞时返回 0、可用时返回 1（语义反转）。

新 `runner/probe_amdgpu_ep.py` 实现五态状态机：

| 状态 | 判据 |
| --- | --- |
| `NOT_INSTALLED` | 无官方绑定且无 EP 产物 |
| `CATALOG_EMPTY` | 官方 API 调用成功（S_OK）但返回 0 个 provider |
| `EP_NOT_READY` | Catalog 提供 AMD EP 或存在 EP DLL，但未 Ready/未注册到本进程 |
| `EP_REGISTERED` | 本进程 ORT 枚举到 AMDGPU EP device/provider |
| `GPU_EXECUTED` | 仅在 `--canary-onnx` 下，profiler 显示 AMDGPU 节点 >0 且 CPU 节点 =0 |

- `api_diagnostic`（含原始 HRESULT）与 `ep_readiness` 分离记录；官方 2.5.96 与旧 2.3.0
  Projection 结果分别记录。
- 输出 `system_dependencies`、`error_codes`、`ep_reference`（EP 名称/版本要求/来源 URL）、
  `recommended_actions`。
- 退出码：0 = `EP_REGISTERED`/`GPU_EXECUTED`，2 = 阻塞。默认只读，不安装、不改系统。

测试 `tests/unit/test_probe_amdgpu_ep_states.py`（9 项）覆盖五态判定、阻塞/可用退出码、
错误码透出。

真实探测结果（`reports/mission_r4/PROBE_AFTER_A1.json`）：`state=CATALOG_EMPTY`，
官方 API `create/enum = 0x00000000`，provider 数 0，详见 R4 报告。

---

## 5. R3-D：驱动要求校正

实测（2026-11-19 抓取 Microsoft Learn 源文档）：

- 页面中 `AMDGPU` 出现次数 **0**；`2.5.96`、`24.12` 出现次数 **0**。
- WinML 2.x 的 AMD GPU EP 是 `MIGraphXExecutionProvider`，要求「driver 25.10.13.09（精确）」，
  且明确不支持 GenAI；AMD NPU EP 是 `VitisAIExecutionProvider`（Adrenalin 25.6.3–25.9.1）。
- 因此任务书中「AMDGPUExecutionProvider = Windows ML 2.5.96+ / Adrenalin 24.12.1+」的说法
  **无法用现行官方文档复现**，不得再把 MIGraphX 的 25.10.13.09 当作新版 AMDGPU EP 的安装条件。

已落到仓库：`docs/AMDGPU_EP_PROVISIONING.md` 第 2 节重写（记录 EP 名称、版本、要求、
页面 ms.date 与来源 URL），探测器 `ep_reference` 同步记录；WDDM 驱动号（32.0.22018.5）与
Adrenalin 版本号不做大小推导。

---

## 6. R3-E：证据审计

- PR #8 历史 18 Receipts：保持只读，未编辑；`docs/LEGACY_RECEIPTS.md` 的「Verify only」策略不变。
- 旧 Manifest 与阈值快照：未改动；新契约要求新 `run_id`，旧运行不可 Resume。
- 新 `RUN_CONTRACT` 兼容性：新增字段 `environment` 为可选，旧契约读取时报
  `ENVIRONMENT_UNBOUND`（fail-closed），不静默降级。
- 隐私：`environment` 只记录版本与 provider 名，不记录绝对路径；`MATRIX_EXIT.json` 的 argv
  经 `redaction.redact_path` 脱敏。
- Secret scan：见第 8 节精确 HEAD 的 CI 结果。

---

## 7. 工程门禁

| 门禁 | 结果 |
| --- | --- |
| compileall（src/runner/tests） | PASS |
| ruff（改动文件） | PASS（仓库其余历史告警不属于本 PR） |
| pytest unit+contracts+adversarial | **276 passed / 1 skipped**（基线 243+1，新增 33 项） |
| 历史覆盖 | 未删除任何既有测试 |

---

## 8. 提交与合并门禁

- 新提交：`ff1499e`（R3-A/B/C 代码 + 测试）、`<docs commit>`（AGENTS 例外、契约文档、
  R4 证据与报告）。
- 合并顺序遵守任务书：先 PR #8，再重定 PR #9 base/diff，最后 PR #9；不使用 force-push，
  不删除失败历史。
- 精确 HEAD 的 Windows CPU CI 与 Secret Scan 结果记录在 `reports/mission_r3_r6/EXECUTION_LEDGER.json`。

---

## 9. 结论

R3 的四项工程缺陷（退出码折叠、哈希范围缺口、未跟踪文件绕过、探测器语义反转）都已修复并有
对抗性回归测试；驱动要求已按现行官方文档校正。CPU-only 结论为 **GO**。

GPU 结论不变：AMDGPU EP 在本机仍不可用（`CATALOG_EMPTY`），R5 的 AMDGPU 部分保持
`BLOCKED`，不得以 R3 的 GO 推导任何 GPU PASS。
