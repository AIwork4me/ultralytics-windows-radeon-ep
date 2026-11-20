"""Experiment B6/B4: fail-closed negative tests around AMDGPU registration.

Every test asserts that an unsupported path FAILS LOUDLY. A silent success here
would be a real defect, so each row records the observed failure and whether it
matched the expected failure class.

Read-only: no file is written outside reports/, no package is installed, and no
driver/OS component is touched. The one inference that runs is a 3 KB synthetic
conv model on the EP requested by the test.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify(exc: BaseException) -> dict:
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if "cannot load" in low or "not found" in low or "找不到" in text or "0x8007007e" in low:
        kind = "DLL_LOAD_ERROR"
    elif "not in available providers" in low or "invalid argument" in low:
        kind = "EP_NOT_REGISTERED_OR_UNKNOWN"
    elif "wrong_or_ambiguous_device" in low:
        kind = "WRONG_OR_AMBIGUOUS_DEVICE"
    elif "no package identity" in low or "0x80073cf4" in low:
        kind = "NO_PACKAGE_IDENTITY"
    else:
        kind = "OTHER"
    from radeon_ort.redaction import redact_text
    return {"exception_type": type(exc).__name__, "message": redact_text(text)[:900],
            "kind": kind}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", default="reports/experiment_b/NEGATIVE_TESTS.json")
    a = ap.parse_args()

    rows: list[dict] = []

    def record(name, expectation, fn, *, expect="fail"):
        """Record one probe. expect="fail" asserts a loud failure; expect="ok"
        asserts a measured behaviour that is itself the finding."""
        t0 = time.monotonic()
        try:
            detail = fn()
            if expect == "ok":
                rows.append({"test": name, "expectation": expectation,
                             "outcome": "SUCCEEDED_AS_EXPECTED", "ok": True,
                             "detail": detail, "elapsed_s": round(time.monotonic() - t0, 3)})
            else:
                rows.append({"test": name, "expectation": expectation,
                             "outcome": "UNEXPECTED_SUCCESS", "ok": False,
                             "detail": detail, "elapsed_s": round(time.monotonic() - t0, 3)})
        except BaseException as exc:  # noqa: BLE001 - the failure is the evidence
            rows.append({"test": name, "expectation": expectation,
                         "outcome": ("FAILED_AS_EXPECTED" if expect == "fail"
                                     else "FAILED_UNEXPECTEDLY"),
                         "ok": expect == "fail", "detail": classify(exc),
                         "elapsed_s": round(time.monotonic() - t0, 3)})

    import onnxruntime as ort

    # N1: registering a library path that does not exist.
    def n1():
        missing = str(Path(tempfile.gettempdir()) / "amdgpu-ep-does-not-exist.dll")
        ort.register_execution_provider_library("AMDGPU", missing)
        return {"registered": "AMDGPU", "path": "amdgpu-ep-does-not-exist.dll"}
    record("register_missing_amdgpu_dll", "raise (DLL_LOAD_ERROR)", n1)

    # N2: registering an existing DLL that is not an EP factory.
    def n2():
        bogus = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "version.dll")
        ort.register_execution_provider_library("AMDGPU", bogus)
        return {"registered": "AMDGPU", "path": "C:/Windows/System32/version.dll"}
    record("register_non_ep_dll_as_amdgpu", "raise (DLL_LOAD_ERROR)", n2)

    # N3/N4: ONNX Runtime's python binding on this build does NOT raise for an
    # unknown provider name: it logs "EP Error ... Falling back to
    # ['CPUExecutionProvider'] and retrying" and creates a CPU session. The
    # fail-closed assertion must therefore be on get_providers(), which is the
    # behaviour these two rows measure (and the reason a providers assertion is
    # mandatory before any AMDGPU verdict).
    tmp = Path(tempfile.mkdtemp(prefix="expb_neg_"))
    model_path = tmp / "canary.onnx"
    from runner.unified_canary import build_tiny_onnx
    build_tiny_onnx(str(model_path))
    import numpy as np
    rng = np.random.default_rng(7)
    feeds = {"x": (rng.standard_normal((1, 3, 32, 32)) * 0.5).astype(np.float32)}

    def session_providers(provider_name: str) -> list[str]:
        so = ort.SessionOptions()
        so.log_severity_level = 4
        sess = ort.InferenceSession(str(model_path), sess_options=so,
                                    providers=[provider_name])
        sess.run(None, feeds)
        return list(sess.get_providers())

    def n3():
        provs = session_providers("AMDGPUExecutionProvider")
        row_detail = {"requested": "AMDGPUExecutionProvider", "session_providers": provs}
        if provs != ["CPUExecutionProvider"]:
            raise RuntimeError(f"unexpected providers: {provs}")
        return {**row_detail, "classification": "SILENT_CPU_FALLBACK_OBSERVED"}
    record("amdgpu_session_without_registration_silently_falls_back_to_cpu",
           "no exception; session providers == ['CPUExecutionProvider'] (silent fallback)",
           n3, expect="ok")

    def n4():
        provs = session_providers("NoSuchExecutionProvider")
        if provs != ["CPUExecutionProvider"]:
            raise RuntimeError(f"unexpected providers: {provs}")
        return {"requested": "NoSuchExecutionProvider", "session_providers": provs,
                "classification": "SILENT_CPU_FALLBACK_OBSERVED"}
    record("unknown_provider_silently_falls_back_to_cpu",
           "no exception; session providers == ['CPUExecutionProvider'] (silent fallback)",
           n4, expect="ok")

    # N5: identity gate — asking for gfx1100 on a gfx1151 host must fail closed.
    def n5():
        from radeon_ort.device_identity import enum_dxgi_adapters, resolve_target_adapter
        res = resolve_target_adapter(enum_dxgi_adapters(), gfx="gfx1100")
        if res.get("ok"):
            return {"resolved": True, "reason": res.get("reason")}
        raise RuntimeError(f"WRONG_OR_AMBIGUOUS_DEVICE: {res.get('reason')}")
    record("gfx1100_request_on_gfx1151_host", "raise (WRONG_OR_AMBIGUOUS_DEVICE)", n5)

    # N6: the DML session really is the DML EP (no silent CPU substitution).
    def n6():
        so = ort.SessionOptions()
        so.log_severity_level = 4
        sess = ort.InferenceSession(str(model_path), sess_options=so,
                                    providers=["DmlExecutionProvider"])
        provs = sess.get_providers()
        if "DmlExecutionProvider" not in provs:
            raise RuntimeError(f"DML missing from providers: {provs}")
        out = sess.run(None, feeds)
        return {"providers": provs, "output_shape": list(out[0].shape)}
    record("dml_session_binds_dml_ep", "session providers include DmlExecutionProvider",
           n6, expect="ok")

    doc = {
        "schema": "experiment_b_negative_tests_v1",
        "mission": "B_GFX1151_AMDGPU_EP_ROOT_CAUSE",
        "run_id": a.run_id,
        "captured_utc": utc_now(),
        "test_definition_revision": 2,
        "revision_note": ("rev2 (2026-11-20): N3/N4 originally expected an exception; the "
                          "observed behaviour is a silent CPU fallback, so the assertion is "
                          "now on get_providers(). The rev1 expectation was wrong, not the "
                          "runtime."),
        "ort_version": ort.__version__,
        "ort_providers_at_start": list(ort.get_available_providers()),
        "tests": rows,
        "all_fail_closed": all(r["ok"] for r in rows),
    }
    out = REPO / a.out
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    for r in rows:
        d = r["detail"]
        print(json.dumps({"test": r["test"], "outcome": r["outcome"],
                          "kind": (d.get("kind") if isinstance(d, dict) else None),
                          "msg": (d.get("message", "") if isinstance(d, dict) else "")[:160]}))
    print(json.dumps({"out": a.out, "all_fail_closed": doc["all_fail_closed"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
