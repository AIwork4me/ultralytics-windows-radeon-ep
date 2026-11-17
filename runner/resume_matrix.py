"""python -m runner.resume_matrix --run-id <id> ... — continue an interrupted matrix run.

Receipts are immutable; finished cases are skipped; only NOT_RUN cases execute.
"""

from __future__ import annotations

import argparse
import sys

from runner.run_matrix import main as run_matrix_main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--tier", default="core")
    ap.add_argument("--ep", choices=["amdgpu", "dml", "both"], default="both")
    ap.add_argument("--winml-python", default=None)
    ap.add_argument("--export-python", default=None)
    ap.add_argument("--image", default=None)
    ap.add_argument("--benchmark", action="store_true")
    a, extra = ap.parse_known_args()
    argv = ["--tier", a.tier, "--ep", a.ep, "--run-id", a.run_id, "--resume"]
    if a.winml_python:
        argv += ["--winml-python", a.winml_python]
    if a.export_python:
        argv += ["--export-python", a.export_python]
    if a.image:
        argv += ["--image", a.image]
    if a.benchmark:
        argv += ["--benchmark"]
    return run_matrix_main(argv + extra)


if __name__ == "__main__":
    sys.exit(main())
