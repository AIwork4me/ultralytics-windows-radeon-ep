"""Contract tests: schema files themselves are valid, catalog entries match model schema."""

import json
import sys
from pathlib import Path

import jsonschema
import pytest
from jsonschema.validators import Draft7Validator

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))


@pytest.mark.parametrize("name", ["receipt", "manifest", "host", "model"])
def test_schema_self_valid(name):
    s = json.loads((REPO / "schemas" / f"{name}.json").read_text(encoding="utf-8"))
    Draft7Validator.check_schema(s)


def test_catalog_entries_match_model_schema(tmp_path):
    cat = REPO / "catalog" / "model_catalog.json"
    if not cat.is_file():
        pytest.skip("catalog not generated yet")
    data = json.loads(cat.read_text(encoding="utf-8"))
    schema = json.loads((REPO / "schemas" / "model.json").read_text(encoding="utf-8"))
    for cid, e in data["entries"].items():
        jsonschema.validate(e, schema)


def test_receipt_fixture_roundtrip():
    sys.path.insert(0, str(REPO / "tests"))
    from helpers_receipts import make_receipt
    r = make_receipt()
    jsonschema.validate(r, json.loads((REPO / "schemas" / "receipt.json").read_text(encoding="utf-8")))


def test_catalog_has_tier_a_seven_tasks():
    cat = REPO / "catalog" / "model_catalog.json"
    if not cat.is_file():
        pytest.skip("catalog not generated yet")
    data = json.loads(cat.read_text(encoding="utf-8"))
    tier_a = [e for e in data["entries"].values() if e.get("tier") == "A"]
    tasks = {e["task"] for e in tier_a if e["family"] == "YOLO26"}
    assert {"detect", "segment", "semantic", "depth", "classify", "pose", "obb"} <= tasks
