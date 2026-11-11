import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import student_sample_loader as loader


def example(ident="one", length=7, padding=0):
    tokens = list(range(10, 10 + length))
    return {"schema": "student-encoded-decision-v1", "sample_id": ident, "input_ids": tokens + [0] * padding,
            "attention_mask": [1] * length + [0] * padding,
            "labels": [-100] * 4 + tokens[4:] + [-100] * padding,
            "target_start": 4, "sequence_length": length,
            "source": {"split": "development", "source_kind": "local_scripted", "synthetic": True},
            "training_eligible": False, "max_length": 32, "pad_token_id": 0}


def package(root, rows, **metadata):
    root.mkdir(parents=True, exist_ok=True)
    raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
    (root / "encoded.jsonl").write_bytes(raw)
    manifest = {"schema": "student-input-bundle-v1", "training_eligible": False,
                "selected_smoke_length": 32, "pad_token_id": 0, "encoded_samples": len(rows),
                "files": {"encoded.jsonl": loader.sha(raw)}, **metadata}
    body = json.dumps(manifest).encode()
    (root / "manifest.json").write_bytes(body)
    (root / "manifest.sha256").write_text(loader.sha(body) + "\n")
    return root


def test_variable_length_padding_never_labels_history_or_crosses_examples():
    first, second = example("first", 7, 2), example("second", 10)
    original = copy.deepcopy([first, second])
    batch, details = loader.collate([first, second], pad_token_id=0)
    assert [first, second] == original
    assert batch["input_ids"][0] == list(range(10, 17)) + [0] * 3
    assert batch["attention_mask"] == [[1] * 7 + [0] * 3, [1] * 10]
    assert batch["labels"][0] == [-100] * 4 + [14, 15, 16] + [-100] * 3
    assert batch["labels"][1] == [-100] * 4 + list(range(14, 20))
    assert [d["supervised_tokens"] for d in details] == [3, 6]


@pytest.mark.parametrize("change", [
    lambda r: r["labels"].__setitem__(0, r["input_ids"][0]),
    lambda r: r["labels"].__setitem__(5, -100),
    lambda r: r["labels"].__setitem__(5, 12345),
    lambda r: r["labels"].append(-100),
    lambda r: r["attention_mask"].__setitem__(1, 0),
    lambda r: r.__setitem__("target_start", 0),
    lambda r: r.__setitem__("target_start", 7),
    lambda r: r.__setitem__("target_start", True),
    lambda r: r.__setitem__("sequence_length", 6),
    lambda r: r["input_ids"].__setitem__(0, True),
    lambda r: r["labels"].__setitem__(5, False),
])
def test_loss_mask_and_alignment_corruption_is_rejected(change):
    row = example()
    change(row)
    with pytest.raises(ValueError):
        loader.validate_tokens(row)


def test_padding_tokens_must_never_receive_loss():
    row = example(padding=2)
    row["labels"][-1] = row["input_ids"][-1]
    with pytest.raises(ValueError, match="Loss mask"):
        loader.validate_tokens(row)


@pytest.mark.parametrize("split", ["development", "dev", "test"])
def test_training_cannot_promote_a_nontrain_row(split):
    row = example()
    row["source"].update(split=split, source_kind="real_provider", synthetic=False)
    row["training_eligible"] = True
    with pytest.raises(ValueError):
        loader.validate_tokens(row, purpose="training")


@pytest.mark.parametrize("change", [
    lambda r: r["source"].__setitem__("synthetic", True),
    lambda r: r["source"].pop("synthetic"),
    lambda r: r["source"].__setitem__("source_kind", "local_scripted"),
    lambda r: r.__setitem__("training_eligible", False),
])
def test_train_identity_does_not_replace_provenance_and_review(change):
    row = example()
    row["source"].update(split="train", source_kind="real_provider", synthetic=False)
    row["training_eligible"] = True
    change(row)
    with pytest.raises(ValueError):
        loader.validate_tokens(row, purpose="training")


def test_self_declared_training_identity_cannot_enable_production_loading(tmp_path):
    row = example()
    row["source"].update(split="train", source_kind="real_provider", synthetic=False)
    row["training_eligible"] = True
    with pytest.raises(ValueError, match="not implemented"):
        loader.validate_tokens(row, purpose="training")
    root = package(tmp_path / "data", [row], training_eligible=True)
    with pytest.raises(ValueError, match="not implemented"):
        loader.load_package(root, purpose="training")


def test_test_cannot_enter_even_smoke_loader():
    row = example()
    row["source"]["split"] = "test"
    with pytest.raises(ValueError):
        loader.validate_tokens(row)


@pytest.mark.parametrize("width", [0, 6, True])
def test_padding_cannot_silently_truncate(width):
    with pytest.raises(ValueError, match="cannot truncate"):
        loader.collate([example()], pad_token_id=0, pad_to_length=width)


@pytest.mark.parametrize("mutation", ["file", "manifest", "extra", "symlink"])
def test_package_tampering_rejected(tmp_path, mutation):
    root = package(tmp_path / "data", [example()])
    assert len(loader.load_package(root)[1]) == 1
    if mutation in {"file", "manifest"}:
        with (root / ("encoded.jsonl" if mutation == "file" else "manifest.json")).open("a") as stream:
            stream.write(" ")
    elif mutation == "extra":
        (root / "unsealed.json").write_text("{}")
    else:
        (root / "extra").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        loader.load_package(root)


def test_duplicate_samples_and_empty_packages_are_explicit(tmp_path):
    root = package(tmp_path / "duplicate", [example(), example()])
    with pytest.raises(ValueError, match="Duplicate encoded"):
        loader.load_package(root)
    empty = package(tmp_path / "empty", [])
    with pytest.raises(ValueError, match="No encoded sample"):
        loader.smoke(empty, pad_token_id=0)


def test_smoke_loads_all_samples_in_incomplete_last_batch_without_training(tmp_path):
    root = package(tmp_path / "data", [example(str(i), 5 + i) for i in range(3)])
    result = loader.smoke(root, pad_token_id=0, batch_size=2)
    assert result["passed"] and result["samples"] == 3
    assert [r["shape"][0] for r in result["batches"]] == [2, 1]
    assert result["optimizer_steps"] == 0 and result["model_weights_loaded"] is False
    with pytest.raises(ValueError):
        loader.smoke(root, pad_token_id=0, purpose="training")


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_ambiguous_json_rejected(raw):
    with pytest.raises(ValueError):
        loader.parse(raw)


@pytest.mark.parametrize("metadata", [
    {"schema": "other"}, {"training_eligible": 0}, {"training_eligible": True},
    {"selected_smoke_length": True}, {"selected_smoke_length": 6},
    {"pad_token_id": True}, {"pad_token_id": 99}, {"encoded_samples": True}, {"encoded_samples": 2},
])
def test_package_metadata_must_bind_actual_rows(tmp_path, metadata):
    root = package(tmp_path / "data", [example()], **metadata)
    with pytest.raises(ValueError):
        loader.load_package(root)


def test_wrong_padding_id_and_overcap_batch_rejected(tmp_path):
    row = example(padding=2)
    row["input_ids"][-1] = 99
    with pytest.raises(ValueError, match="Padding IDs"):
        loader.validate_tokens(row)
    with pytest.raises(ValueError, match="padding token"):
        loader.collate([example()], pad_token_id=99)
    with pytest.raises(ValueError, match="exceeds"):
        loader.collate([example()], pad_token_id=0, pad_to_length=33)
    root = package(tmp_path / "data", [example()])
    with pytest.raises(ValueError, match="padding token"):
        loader.smoke(root, pad_token_id=99)
