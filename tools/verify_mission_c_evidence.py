"""Mission C evidence verifier (fail-closed, CPU-only).

Checks that reports/mission_c/ is internally consistent, hash-complete, unique,
redacted, and that the root-cause verdicts do not exceed the evidence they cite.

    python tools/verify_mission_c_evidence.py --run-id C_GFX1151_AMDGPU_20261120T2153Z

Exit 0 = ALL_OK; exit 1 = at least one failure (each printed as FAIL <id>).
It never contacts the GPU and never writes outside stdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "reports" / "mission_c"
REQUIRED = [
    "C0_BASELINE_AUDIT.json", "C0_AB_COMPARISON_V2.json", "C0_AB_RECONCILIATION.json",
    "C1_API_RUNTIME_DIFFERENTIAL.json", "C1_CATALOG_INTERNALS.json",
    "C2_STORE_PACKAGE_ELIGIBILITY.json", "C2_DRIVER_COMPATIBILITY.json",
    "C3_ETW_CAPTURE_MANIFEST.json", "C4_PROVISIONING_LEDGER.json",
    "C5_AMDGPU_PHYSICAL_VALIDATION.json", "C5_DML_REGRESSION.json",
    "C6_ROOT_CAUSE_MATRIX.json", "C7_ESCALATION_STATUS.json", "MANIFEST.sha256",
]
FORBIDDEN_SUFFIXES = {".pt", ".onnx", ".etl", ".pth", ".ckpt"}
# local-only identifiers that must never reach a published evidence file
LEAK_PATTERNS = [
    re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s\"]+", re.IGNORECASE),
    re.compile(r"\bS-1-5-21-\d+-\d+-\d+-\d+\b"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b"),
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()
    fails: list[str] = []
    checks = 0

    def check(cid: str, ok: bool, detail: str = "") -> None:
        nonlocal checks
        checks += 1
        if not ok:
            fails.append(f"FAIL {cid}: {detail}")

    # 1. required files -----------------------------------------------------
    for name in REQUIRED:
        check(f"REQ:{name}", (OUT / name).is_file(), "missing")

    # 2. manifest completeness ---------------------------------------------
    manifest = (OUT / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
    listed = {}
    for line in manifest:
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        listed[rel.strip()] = digest.strip()
    actual = {p.relative_to(OUT).as_posix() for p in OUT.rglob("*")
              if p.is_file() and p.name != "MANIFEST.sha256"}
    check("MANIFEST:complete", set(listed) == actual,
          f"unlisted={sorted(actual - set(listed))} missing={sorted(set(listed) - actual)}")
    for rel, digest in listed.items():
        p = OUT / rel
        if p.is_file():
            check(f"HASH:{rel}", sha256_file(p) == digest, "sha256 mismatch")

    # 3. run id consistency + uniqueness ------------------------------------
    for name in REQUIRED:
        if not name.endswith(".json"):
            continue
        doc = load(name)
        if "run_id" in doc:
            check(f"RUNID:{name}", doc["run_id"] == args.run_id,
                  f"expected {args.run_id} got {doc['run_id']}")
    other_dirs = [d for d in (REPO / "reports").iterdir()
                  if d.is_dir() and d.name != "mission_c"]
    reused = []
    for d in other_dirs:
        for p in d.rglob("*.json"):
            try:
                if args.run_id in p.read_text(encoding="utf-8", errors="ignore"):
                    reused.append(str(p.relative_to(REPO)))
            except OSError:
                continue
    check("RUNID:unique", not reused, f"run_id also appears in {reused[:3]}")

    # 4. device identity ----------------------------------------------------
    audit = load("C0_BASELINE_AUDIT.json")
    host = audit["host"]
    check("IDENTITY:gfx", host["gfx"] == "gfx1151", host["gfx"])
    check("IDENTITY:did", host["device_id"].lower() == "0x1586", host["device_id"])
    check("IDENTITY:vendor", host["vendor_id"].lower() == "0x1002", host["vendor_id"])
    check("IDENTITY:driver", host["driver_version"] == "32.0.22018.5", host["driver_version"])
    check("IDENTITY:build", host["os_build"].startswith("26200."), host["os_build"])

    # 5. catalogue claims must match the probe ------------------------------
    internals = load("C1_CATALOG_INTERNALS.json")
    recomputed = audit["recomputed"]
    check("CATALOG:enum_hresult",
          str(recomputed["catalog_enum_hresult"]).lower() == "0x00000000",
          str(recomputed["catalog_enum_hresult"]))
    check("CATALOG:empty", recomputed["catalog_provider_count"] == 0,
          str(recomputed["catalog_provider_count"]))
    modes = recomputed["create_with_mode"] or []
    accepted = [m for m in modes if m.get("hresult") == "0x00000000"]
    accepted_empty = accepted and all(m.get("provider_count") == 0 for m in accepted)
    check("CATALOG:modes", bool(accepted) and bool(accepted_empty),
          "an accepted mode returned providers")
    finds = recomputed["find_provider"] or []
    check("CATALOG:find_provider_not_found",
          bool(finds) and all(f.get("found") is False for f in finds), str(finds))
    check("CATALOG:1586_not_blacklisted",
          internals["device_1586_explicitly_unsupported"] is False,
          "0x1586 appears as unsupported")
    check("CATALOG:store_products",
          len(internals["amdgpu_store_products"]) >= 1
          and any(p.get("product_id") for p in internals["amdgpu_store_products"]),
          "no Store product id recovered from the signed binary")
    check("CATALOG:table_digest_matches_audit",
          internals["catalog_table_digest"] == recomputed["catalog_table_digest"],
          "catalogue digest differs between files")

    # 6. physical validation must not claim more than it measured -----------
    phys = load("C5_AMDGPU_PHYSICAL_VALIDATION.json")
    gates = phys["gates"]
    if phys["status"] != "PASS_AMDGPU_PHYSICAL":
        check("PHYS:no_gpu_claim_when_blocked",
              gates["target_ep_profile_nodes"] in (0, None)
              and gates["actual_provider"] is None
              and gates["numeric_parity"] in ("NOT_RUN", None),
              "a blocked run must not carry positive GPU gates")
        check("PHYS:status_is_not_pass", phys["status"] in
              ("NOT_RUN", "BLOCKED", "BLOCKED_EP"), phys["status"])
    else:
        check("PHYS:pass_requires_nodes", gates["target_ep_profile_nodes"] > 0,
              "PASS without profiler nodes")

    # 7. DML preservation ---------------------------------------------------
    dml = load("C5_DML_REGRESSION.json")
    check("DML:status", dml["status"] == "PASS_DML_ONLY", dml["status"])
    check("DML:nodes", dml["canary"]["dml_provider_node_counts"].get(
        "DmlExecutionProvider", 0) > 0, "no DML profiler nodes")
    check("DML:parity", dml["canary"]["dml_parity_allclose"] is True, "parity not allclose")
    check("DML:amdgpu_blocked", dml["amdgpu_control"]["returncode"] == 34,
          f"rc={dml['amdgpu_control']['returncode']}")

    # 8. root cause must not exceed evidence --------------------------------
    rca = load("C6_ROOT_CAUSE_MATRIX.json")
    overall = rca["overall"]
    check("RCA:blocker", overall["CONFIRMED_PROXIMATE_BLOCKER"] == "CATALOG_EMPTY",
          overall["CONFIRMED_PROXIMATE_BLOCKER"])
    check("RCA:underlying_unverified",
          overall["UNDERLYING_ROOT_CAUSE"] == "UNVERIFIED",
          "underlying cause claimed without instrumentation")
    statuses = {h["id"]: h["status"] for h in rca["hypotheses"]}
    check("RCA:hypothesis_ids", set(statuses) == {f"H{i}" for i in range(1, 11)},
          str(sorted(statuses)))
    for hid, st in statuses.items():
        check(f"RCA:{hid}:vocabulary",
              st in rca["verdict_vocabulary"], f"bad status {st}")
    # a CONFIRMED_ROOT_CAUSE anywhere would need instrumentation we do not have
    check("RCA:no_uninstrumented_confirmed_root_cause",
          all(st != "CONFIRMED_ROOT_CAUSE" for st in statuses.values()),
          "CONFIRMED_ROOT_CAUSE claimed without ETW/official confirmation")

    # 9. escalation honesty -------------------------------------------------
    esc = load("C7_ESCALATION_STATUS.json")
    check("ESC:status_vocabulary", esc["status"] in (
        "NOT_NEEDED", "ESCALATION_READY_NOT_POSTED", "ESCALATION_POSTED",
        "ESCALATION_ACKNOWLEDGED", "AWAITING_MAINTAINER_RESPONSE"), esc["status"])
    if esc["status"] == "ESCALATION_READY_NOT_POSTED":
        check("ESC:not_claimed_posted", esc.get("posted") in (None, "", {}),
              "status says not posted but a URL is recorded")

    # 10. ETW must be honest about privileges -------------------------------
    etw = load("C3_ETW_CAPTURE_MANIFEST.json")
    check("ETW:admin_flag", etw["privilege_evidence"]["is_admin"] is False,
          "host reports admin; capture status should be revisited")
    check("ETW:blocked_status", etw["status"] in
          ("BLOCKED_ADMIN_DEPENDENCY", "CAPTURED", "NOT_AVAILABLE"), etw["status"])

    # 11. redaction + forbidden artifacts -----------------------------------
    for p in OUT.rglob("*"):
        if not p.is_file():
            continue
        check(f"NO_BINARY:{p.name}", p.suffix.lower() not in FORBIDDEN_SUFFIXES,
              "forbidden artifact type in evidence package")
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in LEAK_PATTERNS:
            check(f"REDACT:{p.name}", not pat.search(text),
                  f"leak pattern {pat.pattern} matched")

    print(f"checks={checks} failures={len(fails)}")
    for f in fails:
        print(f)
    print("VERDICT: ALL_OK" if not fails else "VERDICT: FAIL")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
