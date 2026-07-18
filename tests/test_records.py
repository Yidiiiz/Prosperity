import pytest

from colony.records import Record


def test_record_has_header_and_indexes(tmp_path):
    rec = Record(tmp_path, "experiments", "demo", config={"rng_seed": 1}, seed=1)
    rec.section("results", "everything nominal")
    rec.finish("PASS demo")
    text = rec.path.read_text(encoding="utf-8")
    assert "utc:" in text and "git:" in text and "rng_seed: 1" in text
    assert "everything nominal" in text and "RESULT: PASS demo" in text
    index = (tmp_path / "INDEX.txt").read_text(encoding="utf-8")
    assert index.count("\n") == 1 and "experiments/" in index and "PASS demo" in index


def test_record_refuses_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr("colony.records._utc_stamp", lambda: "20260101T000000Z")
    Record(tmp_path, "runs", "run_1")
    with pytest.raises(FileExistsError):
        Record(tmp_path, "runs", "run_1")


def test_record_rejects_unknown_kind(tmp_path):
    with pytest.raises(ValueError):
        Record(tmp_path, "scribbles", "x")
