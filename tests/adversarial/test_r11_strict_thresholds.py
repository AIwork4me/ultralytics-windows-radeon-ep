"""R1.1 fail-closed regression: missing declared thresholds and missing metrics.

These are CPU-only adversarial tests. GPU receipts remain frozen.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers_receipts import make_receipt_v2  # noqa: E402
from radeon_ort.receipts import validate_receipt_strict  # noqa: E402
from radeon_ort.task_parity import compare  # noqa: E402


EXPECTED = {"atol": 5e-3, "rtol": 5e-3,
            "task": {"iou_thres": 0.5, "min_recall": 0.90,
                     "min_precision": 0.90}}


def _valid_detect():
    r = make_receipt_v2(
        task_parity_passed=True,
        thresholds_declared=dict(EXPECTED["task"]),
    )
    r["task_parity"]["metrics"].update({
        "recall": 1.0, "precision": 1.0, "matched": 4
    })
    return r


def test_r11_honest_complete_thresholds_pass():
    assert validate_receipt_strict(
        _valid_detect(), expected_thresholds=EXPECTED)["ok"]


def test_r11_missing_task_threshold_is_rejected():
    r = _valid_detect()
    r["task_parity"]["thresholds_declared"].pop("min_recall")
    v = validate_receipt_strict(r, expected_thresholds=EXPECTED)
    assert not v["ok"]
    assert any("min_recall" in p and "missing" in p for p in v["problems"])


def test_r11_missing_metric_cannot_pass_task():
    r = _valid_detect()
    r["task_parity"]["metrics"].pop("recall")
    v = validate_receipt_strict(r, expected_thresholds=EXPECTED)
    assert not v["ok"]
    assert any("required metric" in p and "recall" in p for p in v["problems"])


def test_r11_invalid_high_recall_is_rejected():
    r = _valid_detect()
    r["task_parity"]["metrics"]["recall"] = 0.1
    assert not validate_receipt_strict(
        r, expected_thresholds=EXPECTED)["ok"]


def _classification(pt, cand, drift_limit=0.05):
    return compare(
        {"top1": 0, "top5": [0, 1]},
        {"probs": np.asarray(pt, dtype=np.float32)},
        {"top1": 0, "top5": [0, 1]},
        {"probs": np.asarray(cand, dtype=np.float32)},
        "classify",
        {"task_oracle": {"classify": {
            "require_top1_match": True,
            "max_score_drift": drift_limit,
        }}},
        224,
    )


def test_r11_classification_same_top1_but_excess_drift_fails():
    v = _classification([0.9, 0.1], [0.6, 0.4])
    assert v["metrics"]["top1_match"] is True
    assert v["metrics"]["score_drift"] > 0.05
    assert v["passed"] is False


def test_r11_classification_normal_small_drift_passes():
    v = _classification([0.9, 0.1], [0.89, 0.11])
    assert v["passed"] is True
