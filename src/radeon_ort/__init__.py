"""radeon_ort — independent Windows ML / ORT validation harness for AMD Radeon GPUs.

Fail-closed by design: a GPU PASS requires positive evidence (device identity,
target-EP profile node count, numeric parity, task oracle), never absence of errors.
"""

__version__ = "0.1.0"

from .receipts import ReceiptStatus, build_receipt, validate_receipt_strict  # noqa: F401
