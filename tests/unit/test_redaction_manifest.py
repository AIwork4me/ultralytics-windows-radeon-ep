import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort.manifest import build_manifest, verify_manifest, write_manifest  # noqa: E402
from radeon_ort.redaction import redact_path, redact_text, scan_for_leaks  # noqa: E402


def test_redact_username_path():
    out = redact_text(r"C:\Users\secretuser\proj\file.txt")
    assert "secretuser" not in out
    assert "%USERPROFILE%" in out


def test_redact_posix_path():
    out = redact_text("/home/secretuser/x")
    assert "secretuser" not in out


def test_scan_detects_leak():
    leaks = scan_for_leaks("path C:\\Users\\someone\\x and MAC 01:02:03:04:05:06")
    assert any("someone" in l for l in leaks)
    assert any("MAC" in l for l in leaks)


def test_scan_clean_public():
    assert scan_for_leaks("clean text %USERPROFILE%\\models") == []


def test_manifest_roundtrip(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    (d / "a.json").write_text('{"x": 1}')
    sub = d / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("hello")
    mp = write_manifest(d, "RUN1", "test")
    v = verify_manifest(d, mp)
    assert v["ok"], v
    assert len(json_entries(mp)) == 2


def test_manifest_tamper_detected(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    (d / "a.json").write_text('{"x": 1}')
    mp = write_manifest(d, "RUN1", "test")
    (d / "a.json").write_text('{"x": 2}')
    v = verify_manifest(d, mp)
    assert not v["ok"]
    assert v["mismatches"]


def test_manifest_unlisted_file_detected(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    (d / "a.json").write_text('{"x": 1}')
    mp = write_manifest(d, "RUN1", "test")
    (d / "sneaky.json").write_text("{}")
    v = verify_manifest(d, mp)
    assert not v["ok"]
    assert "sneaky.json" in v["unlisted"]


def json_entries(mp):
    import json
    return json.loads(Path(mp).read_text())["entries"]


def test_redact_path_forward_slash():
    assert "\\" not in redact_path(r"C:\Users\u\models\m.onnx").replace("%USERPROFILE%", "")
