import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort.task_oracles import (depth_errors, detection_match, iou_xyxy,  # noqa: E402
                                     mask_iou, obb_rotated_iou, pose_keypoint_oks,
                                     semantic_miou)
from radeon_ort.task_adapters import classify_topk, softmax  # noqa: E402


def test_iou_identical():
    assert abs(iou_xyxy([0, 0, 10, 10], [0, 0, 10, 10]) - 1.0) < 1e-9


def test_iou_disjoint():
    assert iou_xyxy([0, 0, 5, 5], [10, 10, 15, 15]) == 0.0


def test_detection_match_perfect():
    pred = [{"box": [0, 0, 10, 10], "score": 0.9, "cls": 0}]
    ref = [{"box": [0, 0, 10, 10], "score": 1.0, "cls": 0}]
    m = detection_match(pred, ref)
    assert m["tp"] == 1 and m["fp"] == 0 and m["fn"] == 0


def test_detection_match_wrong_class_is_miss():
    pred = [{"box": [0, 0, 10, 10], "score": 0.9, "cls": 1}]
    ref = [{"box": [0, 0, 10, 10], "score": 1.0, "cls": 0}]
    m = detection_match(pred, ref)
    assert m["tp"] == 0 and m["fn"] == 1


def test_mask_iou():
    a = np.zeros((4, 4), bool)
    a[:2] = True
    b = np.zeros((4, 4), bool)
    b[:2] = True
    assert mask_iou(a, b) == 1.0
    b2 = np.zeros((4, 4), bool)
    b2[2:] = True
    assert mask_iou(a, b2) == 0.0


def test_semantic_miou_two_classes():
    gt = np.array([[0, 0], [1, 1]])
    pred = np.array([[0, 0], [1, 1]])
    assert abs(semantic_miou(pred, gt, 2) - 1.0) < 1e-9
    pred2 = np.array([[1, 1], [0, 0]])
    assert abs(semantic_miou(pred2, gt, 2)) < 1e-9


def test_depth_errors():
    ref = np.array([1.0, 2.0, 3.0])
    pred = np.array([1.1, 2.0, 3.3])
    e = depth_errors(pred, ref)
    assert e["mae"] > 0 and e["valid_px"] == 3


def test_pose_oks_perfect():
    k = np.array([[10.0, 10.0, 1.0], [20.0, 20.0, 1.0]])
    assert pose_keypoint_oks(k, k) > 0.999


def test_classify_topk():
    logits = np.array([[0.1, 5.0, 1.0, 0.2]])
    r = classify_topk(logits, k=3)
    assert r["top1"] == 1
    assert r["probs_topk"][0] > 0.5
    s = softmax(logits[0])
    assert abs(s.sum() - 1.0) < 1e-6


def test_obb_rotated_iou_same_box():
    cv2 = pytest.importorskip("cv2")
    poly = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=np.float32)
    assert obb_rotated_iou(poly, poly) > 0.99
