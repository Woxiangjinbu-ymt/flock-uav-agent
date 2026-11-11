"""Regression checks for export integrity and publication of a complete split."""
import shutil

import pytest

from posttraining.sft import export


def test_changed_public_payload_cannot_create_split(tmp_path, monkeypatch):
    release = tmp_path / "source"
    (release / "audit").mkdir(parents=True)
    (release / "public").mkdir()
    for name in ("audit/manifest.json", export.PROJECTION, "audit/episodes.jsonl"):
        shutil.copyfile(export.RELEASE / name, release / name)
    (release / export.PUBLIC).write_bytes(b"unregistered compressed data")
    monkeypatch.setattr(export, "load_tokenizer", lambda path: None)
    with pytest.raises(ValueError, match="Unexpected SHA-256.*reviewed-compatible"):
        export.export(tmp_path / "sft", release=release)
    assert list(tmp_path.iterdir()) == [release]


def test_interrupted_export_leaves_no_partial_release(tmp_path, monkeypatch):
    def interrupted(stage, release, tokenizer):
        (stage / "train.jsonl.gz").write_bytes(b"partial")
        raise ValueError("source validation failed midstream")

    monkeypatch.setattr(export, "load_tokenizer", lambda path: None)
    monkeypatch.setattr(export, "write_split", interrupted)
    with pytest.raises(ValueError, match="midstream"):
        export.export(tmp_path / "sft")
    assert list(tmp_path.iterdir()) == []


def test_existing_release_is_preserved(tmp_path):
    (tmp_path / "train.jsonl.gz").write_bytes(b"previous release")
    with pytest.raises(FileExistsError):
        export.export(tmp_path)
    assert (tmp_path / "train.jsonl.gz").read_bytes() == b"previous release"
