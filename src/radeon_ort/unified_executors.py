"""unified_executors — real-hardware executors for predict().

InProcessExecutor: runs each EP attempt in THIS interpreter using the rca2a
session helpers (LUID-pinned AMDGPU, ordinal-pinned DML), with an independent
CPU ORT oracle computed first. Subprocess isolation (fresh interpreter per EP,
FILE stdio, phase watchdog) is the production posture (rca2a_main); this slice
keeps the same evidence assembly in-process for the gfx1100 smoke while the
policy engine above stays executor-agnostic.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .unified_runtime import AttemptResult


class InProcessExecutor:
    def __init__(self, gfx: str = "gfx1100"):
        self.gfx = gfx

    def execute(self, ep: str, ctx: dict) -> AttemptResult:
        if ep == "cpu":
            return self._cpu(ctx)
        if ep == "amdgpu":
            return self._amdgpu(ctx)
        if ep == "dml":
            return self._dml(ctx)
        raise ValueError(ep)

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _oracle(model_path: str, feeds: dict):
        import numpy as np
        import onnxruntime as ort

        cpu = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        outs = cpu.run(None, feeds)
        return dict(zip([o.name for o in cpu.get_outputs()], outs))

    @staticmethod
    def _parity(ref: dict, got: dict, atol=5e-3, rtol=5e-3):
        import numpy as np

        if set(ref) != set(got):
            return {"allclose": False, "max_abs": float("inf"),
                    "reason": "output names differ"}
        worst = 0.0
        ok = True
        for k in sorted(ref):
            a, b = np.asarray(got[k], np.float64), np.asarray(ref[k], np.float64)
            if a.shape != b.shape or a.size == 0 or not np.isfinite(a).all():
                ok = False
                continue
            d = float(np.max(np.abs(a - b)))
            worst = max(worst, d)
            ok = ok and d <= atol + rtol * float(np.max(np.abs(b)))
        return {"allclose": ok, "max_abs": worst}

    @staticmethod
    def _profile_nodes(sess, needle: str) -> tuple[int, int]:
        import json as _json

        prof = Path(sess.end_profiling())
        data = _json.loads(prof.read_text(encoding="utf-8"))
        ep_nodes = sum(1 for e in data if e.get("cat") == "Node"
                       and needle in str(e.get("args", {}).get("provider", "")).lower())
        cpu_nodes = sum(1 for e in data if e.get("cat") == "Node"
                        and "cpuexecutionprovider" in str(
                            e.get("args", {}).get("provider", "")).lower())
        return ep_nodes, cpu_nodes

    # -- EP attempts -------------------------------------------------------
    def _cpu(self, ctx: dict) -> AttemptResult:
        try:
            outs = self._oracle(ctx["model_path"], ctx["inputs"])
            return AttemptResult(ep="cpu", ok=True, status="FULL", outputs=outs,
                                 actual_provider="CPUExecutionProvider",
                                 device_binding_confidence="CPU_HOST")
        except Exception as e:  # noqa: BLE001
            return AttemptResult(ep="cpu", ok=False, error=f"{type(e).__name__}: {e}")

    def _amdgpu(self, ctx: dict) -> AttemptResult:
        from .unified_identity import build_chain

        gfx = ctx.get("target_gfx") or self.gfx
        chain = build_chain(gfx)
        if chain.physical is None:
            return AttemptResult(ep="amdgpu", status="BLOCKED",
                                 error=";".join(chain.failures) or "no physical GPU")
        if chain.ep_device is None or chain.ep_device.get("luid_match") is False:
            return AttemptResult(ep="amdgpu", status="BLOCKED",
                                 error="ep_device binding unproven: "
                                       + ";".join(chain.failures))
        try:
            import onnxruntime as ort

            from .rca2a_ep import create_amdgpu_session

            ref = self._oracle(ctx["model_path"], ctx["inputs"])
            so = ort.SessionOptions()
            so.enable_profiling = True
            so.log_severity_level = 3
            so.profile_file_prefix = "unified_amdgpu_profile"
            sess = create_amdgpu_session(ctx["model_path"], sess_options_override=so,
                                         target_luid=chain.physical.get("luid_key"))
            if "AMDGPUExecutionProvider" not in sess.get_providers():
                return AttemptResult(ep="amdgpu", error="SILENT_CPU_FALLBACK_BLOCKED")
            outs = dict(zip([o.name for o in sess.get_outputs()],
                            sess.run(None, ctx["inputs"])))
            par = self._parity(ref, outs)
            ep_nodes, cpu_nodes = self._profile_nodes(sess, "amdgpu")
            return AttemptResult(
                ep="amdgpu", ok=par["allclose"] and ep_nodes > 0 and cpu_nodes == 0,
                status="FULL" if (ep_nodes > 0 and cpu_nodes == 0) else "PARTIAL",
                target_ep_profile_nodes=ep_nodes, cpu_profile_nodes=cpu_nodes,
                numeric_oracle=par,
                device_identity={k: v for k, v in chain.physical.items()
                                 if k != "adapter"},
                device_binding_confidence=chain.confidence(),
                actual_provider="AMDGPUExecutionProvider", outputs=outs)
        except Exception as e:  # noqa: BLE001
            return AttemptResult(ep="amdgpu", error=f"{type(e).__name__}: {e}")

    def _dml(self, ctx: dict) -> AttemptResult:
        try:
            import onnxruntime as ort

            from .device_identity import enum_dxgi_adapters, resolve_target_adapter

            res = resolve_target_adapter(enum_dxgi_adapters(), gfx=self.gfx)
            if not res.get("ok"):
                return AttemptResult(ep="dml", status="BLOCKED", error=res["reason"])
            adapter = res["adapter"]
            from .dml_session import create_dml_session

            ref = self._oracle(ctx["model_path"], ctx["inputs"])
            so = ort.SessionOptions()
            so.enable_profiling = True
            so.log_severity_level = 3
            so.profile_file_prefix = "unified_dml_profile"
            sess = create_dml_session(ctx["model_path"], device_id=adapter.index,
                                      sess_options_override=so)
            if "DmlExecutionProvider" not in sess.get_providers():
                return AttemptResult(ep="dml", error="DML provider absent")
            outs = dict(zip([o.name for o in sess.get_outputs()],
                            sess.run(None, ctx["inputs"])))
            par = self._parity(ref, outs)
            ep_nodes, cpu_nodes = self._profile_nodes(sess, "dml")
            return AttemptResult(
                ep="dml", ok=par["allclose"] and ep_nodes > 0 and cpu_nodes == 0,
                status="FULL" if (ep_nodes > 0 and cpu_nodes == 0) else "PARTIAL",
                target_ep_profile_nodes=ep_nodes, cpu_profile_nodes=cpu_nodes,
                numeric_oracle=par,
                device_identity={"description": adapter.description,
                                 "device_id": hex(adapter.device_id),
                                 "luid": adapter.luid_str, "dxgi_index": adapter.index},
                device_binding_confidence=(
                    "DML_ADAPTER_PINED_BY_DXGI_ORDINAL+LUID_RECORDED"),
                actual_provider="DmlExecutionProvider", outputs=outs)
        except Exception as e:  # noqa: BLE001
            return AttemptResult(ep="dml", error=f"{type(e).__name__}: {e}")
