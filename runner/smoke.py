"""python -m runner.smoke — Tier A seven-task smoke with real assets (if present)."""

from __future__ import annotations

import argparse
import sys

from runner.run_matrix import main as run_matrix_main


def main() -> int:
    argv = ["--tier", "core", "--ep", "both"]
    known = {"--winml-python", "--export-python", "--image", "--run-id",
             "--max-minutes-per-case", "--benchmark", "--pt-oracle"}
    # pass through select args
    import sys as _s
    args = _s.argv[1:]
    passthrough = []
    i = 0
    while i < len(args):
        if args[i] in known:
            passthrough += [args[i]]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                passthrough += [args[i + 1]]
                i += 1
        i += 1
    return run_matrix_main(argv + passthrough)


if __name__ == "__main__":
    sys.exit(main())
