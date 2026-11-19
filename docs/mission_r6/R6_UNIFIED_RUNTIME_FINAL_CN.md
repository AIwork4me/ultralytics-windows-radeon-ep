# Mission R6 最终报告（统一 Runtime 与三平台 RC）

日期：2026-11-19（Asia/Shanghai）
基线：`ed60ed4`（R3 合并到 PR #9 分支后的 head；含 R3-A/B/C 全部修复）
判决：**UNIFIED_RUNTIME_DESIGN_GO** / **GFX1151_PHYSICAL_GO（DML）** /
gfx1100 `PENDING_PHYSICAL_HOST` / gfx1201 `PENDING_PHYSICAL_HOST` /
**THREE_PLATFORM_RC_CONDITIONAL_GO**

---

## 1. PR #6 复核结论：不直接合并

PR #6（`a9075dc8`）是 2026-11-18 的统一 Runtime 垂直切片，设计方向正确，但代码存在
任务书 R6-B 列出的全部 P0 缺陷。直接在 main 上合并会把旧验证缺陷带进主线，因此：

- PR #6 **不合并**，保留其讨论与历史；
- 以 R3 之后的 main/PR #9 代码为基线，重新实现统一入口（本文第 2–5 节）；
- 在 PR #6 下留评论说明其归属（新实现替代，原分支保留为设计参考）。

逐项对照：

| R6-B 要求 | PR #6 现状 | 本实现 |
| --- | --- | --- |
| target_gfx 完整传递 | 执行器默认 `gfx="gfx1100"`，predict 未传 gfx | gfx 必填或由 DXGI DID 推断，多/零候选即拒绝；执行器只用解析后的 gfx |
| 禁止默认 gfx1100 | 违反 | 无默认架构 |
| DML device_id ↔ DXGI ordinal 证据级别 | 未记录 | 绑定标签 `EP_DEVICE_SELECTED_ORDINAL_MAPPING`，并在 device_binding 里保留 `mapping_assumption` |
| 非 UNPROVEN 任意字符串不能过 GPU FULL | 用 `\"UNPROVEN\" not in conf` 子串判断 | 白名单：AMDGPU 只认 `PHYSICAL_BINDING_CONFIRMED`，DML 只认 ordinal mapping 标签 |
| GPU FULL 与 TASK VERIFIED 分开 | 混在 status | `gpu_full` 与 `task_verified` 两个独立布尔；task 未跑时如实 NOT_RUN |
| Parity 按输出名 + shape/dtype/NaN/Inf/空输出 | 只比数值，无 dtype/NaN/空检查 | 逐输出检查 by-name、shape、dtype、finite、空维度、allclose 聚合 |
| 输入 SHA 含名称/dtype/shape/字节 | 只拼字节 | `input_fingerprint`：每输入 name+dtype+shape+nbytes+sha256，再算聚合 SHA |
| 隔离进程/超时/watchdog/独立日志 | 同进程执行 | 每次尝试独立子进程（`runner._ep_worker`），父进程超时 kill，独立日志 |
| CPU 授权不可隐式推导 | 基本正确 | 仅 `allow_cpu=True` 且 policy 允许；provider/env/异常都不构成授权 |
| GPU 会话不得静默 CPU | 只查 provider 列表 | 同时查 active provider、requested providers、profiler CPU 节点数 |
| 失败报告首个错误与实际路径 | 只给 error 字符串 | `attempts[]` 保留每步 ep/error/nodes/binding/exit_code/log |
| RunResult 与 R3 Receipt Contract 一致 | 无 | 结果写 receipt JSON 并给 `receipt_sha256`，附 `code_provenance`（git head/harness sha/dirty） |

## 2. 三层执行策略（R6-C）

| Policy | 行为 | 失败时 |
| --- | --- | --- |
| `require_amdgpu` | 只尝试 AMDGPU | 立即拒绝（不进入 DML/CPU） |
| `prefer_amdgpu_allow_dml` | AMDGPU → 独立 DML 尝试 | 保留 AMDGPU 首错；不隐式 CPU |
| `gpu_first_allow_cpu` | AMDGPU → DML → CPU（仅 `allow_cpu=True`） | CPU 成功标 `CPU_ONLY_USER_AUTHORIZED`，绝不标 GPU |
| `cpu_opt_in` | 直接 CPU（显式授权） | 与三层 fallback 区分，仅作直接 CPU 模式 |

`allow_cpu=True` 与 `require_amdgpu` / `prefer_amdgpu_allow_dml` 组合是 ContractError，
防止把「同意 CPU」当成万能开关。

## 3. 开发者 API（R6-D）

    from radeon_ort.unified_runtime import predict

    result = predict(
        model_path="models/onnx/yolo26n.raw_b1_fp32_static.onnx",
        inputs={"images": tensor},
        policy="prefer_amdgpu_allow_dml",
        target_gfx="gfx1151",       # 省略则按 DXGI DID 推断，绝不默认
        allow_cpu=False,
        require_full_gpu=True,
        task="detect",
    )

RunResult 至少包含：`requested_policy, actual_ep, attempted_eps, physical_gpu,
device_binding_confidence, model_sha256, input_sha256, target_ep_profile_nodes,
cpu_profile_nodes, numeric_parity, task_parity, fallback_reason, runtime_status,
code_provenance, receipt_sha256`（另有 `gpu_full / task_verified / attempts`）。

CLI：

    .venv-winml\Scripts\python -m runner.unified_predict --model <onnx> --inputs-npz <npz> --policy prefer_amdgpu_allow_dml --gfx gfx1151 --out result.json

退出码：0 = GPU_FULL/GPU_FULL_DML/CPU_ONLY_USER_AUTHORIZED；2 = 参数契约错误；
30 = 阻塞；31 = AMDGPU 失败；34 = 设备/身份阻塞。

## 4. 真机端到端结果（本机 gfx1151）

    runner.unified_predict --model models\onnx\yolo26n.raw_b1_fp32_static.onnx \
      --inputs-npz <zeros 1x3x640x640 fp32> \
      --policy prefer_amdgpu_allow_dml --gfx gfx1151 \
      --run-id GFX1151_R6_CANARY_20261119T1335

    runtime_status      : GPU_FULL_DML
    actual_ep           : dml
    target_ep_profile_nodes : 1
    cpu_profile_nodes   : 0
    device_binding      : EP_DEVICE_SELECTED_ORDINAL_MAPPING
    verifier            : RUNRESULT_VALID
    exit code           : 0

同一次调用里 AMDGPU 尝试返回 `BLOCKED_EP: amdgpu-ep.dll not found`，被记录在
`attempts[0]` 与 `fallback_reason` 中，没有被隐藏。证据：
`reports/mission_r6/GFX1151_R6_CANARY_20261119T1335/`。

这证明统一入口在本机能真实执行 GPU（DML）并给出可校验的 RunResult，而不是只跑假执行器。

## 5. 测试

`tests/unit/test_unified_runtime.py`（16 项）覆盖：三/四策略分支、CPU 授权边界、
gfx 不支持、CPU 节点混入、零节点、非有限输出、空输出、任意绑定标签、静默 CPU、
输入指纹五维变化、verifier 反伪造、首错追踪。

全仓：**292 passed / 1 skipped**（R3 前基线 243 passed / 1 skipped，新增 33 项 R3 测试 + 16 项 R6 测试）。

## 6. 三平台矩阵

| Platform | AMDGPU | DML | Unified Runtime | 状态 |
| --- | --- | --- | --- | --- |
| gfx1151 / 8060S | BLOCKED_EP（R4 证据） | **9/9 Physical PASS** | **Physical GPU_FULL_DML** | CONDITIONAL_GO |
| gfx1100 / 7900 XTX | PENDING_PHYSICAL_HOST | PENDING_PHYSICAL_HOST | PENDING_PHYSICAL_HOST | 交接见 `docs/cross_host/GFX1100_HANDOFF.md` |
| gfx1201 / 9070 XT | PENDING_PHYSICAL_HOST | PENDING_PHYSICAL_HOST | PENDING_PHYSICAL_HOST | 交接见 `docs/cross_host/GFX1201_HANDOFF.md` |

跨架构合成测试（tests 中的 gfx 选择/DID 分支）只能证明代码不会错选，**不能**替代物理验证；
本机不会产生任何 gfx1100 / gfx1201 的 PASS。

## 7. Release Candidate 冻结项

| 项 | 值 |
| --- | --- |
| Git commit | `ed60ed4`（R3 修复）+ 本报告所在提交 |
| Python | 3.12.10 |
| Windows ML | `windowsml 2.5.96` |
| ORT | `onnxruntime-windowsml 1.25.2.202605110140` |
| DirectML | 随 ORT windowsml wheel（DmlExecutionProvider） |
| AMDGPU EP | 未安装（BLOCKED_EP），无 DLL 可冻 |
| 模型 Catalog | `catalog/model_catalog.json`（快照固定） |
| Thresholds | `configs/thresholds.yaml` + run 内 `thresholds_snapshot.json` |
| RunResult Schema | `docs/unified/CONTRACTS.md` + `unified_run_v1` receipt |
| 设备身份规则 | DID pinning（0x1586 / 0x7448,0x744C / 0x7550,0x7551），LUID 仅作本机佐证 |

## 8. 结论

统一 Runtime 的设计与实现（策略、证据门禁、开发者入口）为 **GO**；gfx1151 的 DML 路径为
**PHYSICAL_GO**，AMDGPU 仍为外部阻塞。三平台 RC 只能给 **CONDITIONAL_GO**：
必须等 gfx1100 / gfx1201 两台真机按交接文档跑出各自的证据，才能授予
`THREE_PLATFORM_RC_GO`。
