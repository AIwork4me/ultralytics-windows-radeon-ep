"""Experiment A final classifier — shared by negative tests and the result
assembly. Pure function over evidence dicts; no I/O; fails closed.

Mission A_GFX1100_CATALOG_VS_DIRECT_REGISTRATION (steps 8-9).
"""
from __future__ import annotations

DIRECT_STATES = ("DIRECT_NOT_RUN", "DIRECT_REGISTERED_NO_EXEC",
                 "DIRECT_GPU_EXECUTED", "DIRECT_FAILED")
CATALOG_STATES = ("CATALOG_EMPTY", "CATALOG_NOT_LISTED", "CATALOG_LISTED_NOT_READY",
                  "CATALOG_READY_NO_LIBRARY_PATH", "CATALOG_REGISTERED_NO_EXEC",
                  "CATALOG_GPU_EXECUTED", "CATALOG_REGISTRATION_FAILED",
                  "CATALOG_NOT_RUN")


def classify_direct(a3: dict) -> str:
    """a3 = DIRECT_REGISTRATION_CANARY.json contents (or {})."""
    if not a3:
        return "DIRECT_NOT_RUN"
    v = a3.get("verdict")
    if v == "GFX1100_DIRECT_AMDGPU_GPU_EXECUTED":
        return "DIRECT_GPU_EXECUTED"
    if v == "BLOCKED_EP_DLL_NOT_FOUND":
        return "DIRECT_FAILED"
    if a3.get("gpu_worker", {}).get("direct_ep_registered"):
        return "DIRECT_REGISTERED_NO_EXEC"
    return "DIRECT_FAILED"


def classify_catalog(a1: dict, a4: dict) -> str:
    """a1 = CATALOG_PROBE.json; a4 = CATALOG_REGISTRATION_CANARY.json."""
    if not a1:
        return "CATALOG_NOT_RUN"
    cls = a1.get("classification")
    if cls == "CATALOG_EMPTY" or a1.get("amdgpu_listed_any_run") is False:
        return "CATALOG_EMPTY"
    if cls == "CATALOG_AMDGPU_NOT_LISTED":
        return "CATALOG_NOT_LISTED"
    if not a4:
        return "CATALOG_LISTED_NOT_READY"
    v = a4.get("verdict")
    if v == "GFX1100_CATALOG_AMDGPU_GPU_EXECUTED":
        return "CATALOG_GPU_EXECUTED"
    if v == "CATALOG_NOT_READY":
        return "CATALOG_LISTED_NOT_READY"
    if v == "CATALOG_READY_NO_LIBRARY_PATH":
        return "CATALOG_READY_NO_LIBRARY_PATH"
    if v == "GFX1100_CATALOG_EMPTY":
        return "CATALOG_EMPTY"
    if v == "CATALOG_REGISTRATION_FAILED":
        return "CATALOG_REGISTRATION_FAILED"
    return "CATALOG_LISTED_NOT_READY"


FINAL_MATRIX = {
    # (direct, catalog) -> mission verdict label
    ("DIRECT_GPU_EXECUTED", "CATALOG_GPU_EXECUTED"): "A_DIRECT_AND_CATALOG_GPU_GO",
    ("DIRECT_GPU_EXECUTED", "CATALOG_EMPTY"): "A_DIRECT_GPU_GO_CATALOG_EMPTY",
    ("DIRECT_GPU_EXECUTED", "CATALOG_NOT_LISTED"): "A_DIRECT_GPU_GO_CATALOG_EMPTY",
    ("DIRECT_GPU_EXECUTED", "CATALOG_LISTED_NOT_READY"): "A_DIRECT_GPU_GO_CATALOG_NOT_READY",
    ("DIRECT_GPU_EXECUTED", "CATALOG_READY_NO_LIBRARY_PATH"): "A_DIRECT_GPU_GO_CATALOG_NOT_READY",
    ("DIRECT_GPU_EXECUTED", "CATALOG_REGISTRATION_FAILED"): "A_DIRECT_GPU_GO_CATALOG_REGISTRATION_FAILED",
    ("DIRECT_GPU_EXECUTED", "CATALOG_NOT_RUN"): "A_DIRECT_GPU_GO_CATALOG_NOT_READY",
    ("DIRECT_NOT_RUN", "CATALOG_GPU_EXECUTED"): "A_CATALOG_READY_GPU_NOT_VALIDATED",
    ("DIRECT_REGISTERED_NO_EXEC", "CATALOG_GPU_EXECUTED"): "A_CATALOG_READY_GPU_NOT_VALIDATED",
    ("DIRECT_REGISTERED_NO_EXEC", "CATALOG_LISTED_NOT_READY"): "A_BLOCKED_DEPENDENCY",
    ("DIRECT_FAILED", "CATALOG_GPU_EXECUTED"): "A_DIRECT_GPU_REGRESSION",
    ("DIRECT_FAILED", "CATALOG_LISTED_NOT_READY"): "A_NO_GO",
    ("DIRECT_FAILED", "CATALOG_EMPTY"): "A_NO_GO",
    ("DIRECT_NOT_RUN", "CATALOG_NOT_RUN"): "A_BLOCKED_DEPENDENCY",
}


def classify_final(direct: str, catalog: str) -> str:
    return FINAL_MATRIX.get((direct, catalog), "A_UNCLASSIFIED_COMBINATION")
