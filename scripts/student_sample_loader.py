#!/usr/bin/env python3
"""Framework-neutral tokenized sample loading and padding; no model or optimizer.

Default smoke mode accepts explicitly marked development examples. Production
training loading is disabled until collection provenance and review are bound
to a frozen training dataset; self-declared row fields cannot authorize it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError("Nonfinite JSON number")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)


def safe(root, name):
    if not isinstance(name, str) or not name:
        raise ValueError("Unsafe sample path")
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Unsafe sample path")
    path = root / relative
    if any((root / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)):
        raise ValueError("Symlink in token package")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Sample path escapes package")
    return path


def validate_tokens(row, *, purpose="smoke"):
    if purpose not in {"smoke", "training"}:
        raise ValueError("Unsupported loading purpose")
    if purpose == "training":
        raise ValueError("Production training loading is not implemented; development smoke only")
    if not isinstance(row, dict) or not isinstance(row.get("sample_id"), str) or not row["sample_id"]:
        raise ValueError("Sample identity missing")
    source = row.get("source")
    if not isinstance(source, dict) or source.get("split") not in {"development", "train", "dev"}:
        raise ValueError("Test/unknown split cannot enter development or training loader")
    if type(row.get("training_eligible")) is not bool:
        raise ValueError("Missing explicit sample eligibility")
    ids, attention, labels = (row.get(k) for k in ("input_ids", "attention_mask", "labels"))
    if not all(isinstance(v, list) for v in (ids, attention, labels)) or not ids or len({len(v) for v in (ids, attention, labels)}) != 1:
        raise ValueError("Misaligned or empty token arrays")
    if any(type(v) is not int or v < 0 for v in ids):
        raise ValueError("Invalid input token ID")
    if any(type(v) is not int or v not in {0, 1} for v in attention):
        raise ValueError("Invalid attention mask")
    if any(type(v) is not int or v < 0 and v != -100 for v in labels):
        raise ValueError("Invalid label token")
    start, length = row.get("target_start"), row.get("sequence_length")
    if type(start) is not int or type(length) is not int or not 1 <= start < length <= len(ids):
        raise ValueError("Invalid current-assistant token boundary")
    cap, pad = row.get("max_length"), row.get("pad_token_id")
    if type(cap) is not int or cap < len(ids) or type(pad) is not int or pad < 0:
        raise ValueError("Invalid sample length cap or padding token")
    if ids[length:] != [pad] * (len(ids) - length):
        raise ValueError("Padding IDs do not match the declared tokenizer")
    if attention != [1] * length + [0] * (len(ids) - length):
        raise ValueError("Attention must preserve the full sequence and right-pad only")
    if labels[:start] != [-100] * start or labels[start:length] != ids[start:length] or labels[length:] != [-100] * (len(ids) - length):
        raise ValueError("Loss mask must supervise only the complete current assistant suffix")
    return {"sample_id": row["sample_id"], "sequence_length": length,
            "prompt_tokens": start, "supervised_tokens": length - start, "split": source["split"]}


def load_package(folder, *, purpose="smoke"):
    if purpose != "smoke":
        raise ValueError("Production training loading is not implemented; development smoke only")
    folder = Path(folder).resolve()
    raw = safe(folder, "manifest.json").read_bytes()
    if sha(raw) != safe(folder, "manifest.sha256").read_text().strip():
        raise ValueError("Token package manifest changed")
    manifest = parse(raw)
    if manifest.get("schema") != "student-input-bundle-v1" or type(manifest.get("training_eligible")) is not bool:
        raise ValueError("Unsupported token package schema or eligibility")
    cap, pad = manifest.get("selected_smoke_length"), manifest.get("pad_token_id")
    if type(cap) is not int or cap < 1 or type(pad) is not int or pad < 0:
        raise ValueError("Invalid token package length cap or padding token")
    files = manifest.get("files")
    if not isinstance(files, dict) or "encoded.jsonl" not in files:
        raise ValueError("Missing encoded sample inventory")
    if any(p.is_symlink() for p in folder.rglob("*")):
        raise ValueError("Symlink in token package")
    inventory = {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()}
    if inventory != set(files) | {"manifest.json", "manifest.sha256"}:
        raise ValueError("Token package inventory changed")
    for name, expected in files.items():
        if not isinstance(expected, str) or sha(safe(folder, name).read_bytes()) != expected:
            raise ValueError("Token file hash mismatch: " + name)
    encoded = safe(folder, "encoded.jsonl").read_bytes()
    if encoded and not encoded.endswith(b"\n"):
        raise ValueError("Unterminated encoded sample")
    records = [parse(line) for line in encoded.splitlines()]
    if type(manifest.get("encoded_samples")) is not int or manifest["encoded_samples"] != len(records):
        raise ValueError("Encoded sample count does not match manifest")
    seen = set()
    for row in records:
        validate_tokens(row, purpose=purpose)
        if row.get("schema") != "student-encoded-decision-v1" or row["max_length"] != cap or row["pad_token_id"] != pad:
            raise ValueError("Sample tokenizer or length cap does not match package")
        if row["training_eligible"] != manifest["training_eligible"]:
            raise ValueError("Sample eligibility does not match package")
        if row["sample_id"] in seen:
            raise ValueError("Duplicate encoded sample ID")
        seen.add(row["sample_id"])
    return manifest, records


def collate(records, *, pad_token_id, purpose="smoke", pad_to_length=None):
    if not records:
        raise ValueError("Empty batch")
    if type(pad_token_id) is not int or pad_token_id < 0:
        raise ValueError("Invalid padding token")
    summaries = [validate_tokens(row, purpose=purpose) for row in records]
    if any(row["pad_token_id"] != pad_token_id for row in records):
        raise ValueError("Batch padding token must match the sample tokenizer")
    if len({r["sample_id"] for r in records}) != len(records):
        raise ValueError("Duplicate sample in batch")
    needed = max(r["sequence_length"] for r in records)
    width = needed if pad_to_length is None else pad_to_length
    if type(width) is not int or width < needed:
        raise ValueError("Batch padding cannot truncate any sample")
    if any(width > row["max_length"] for row in records):
        raise ValueError("Batch padding exceeds the sample length cap")
    batch = {"input_ids": [], "attention_mask": [], "labels": []}
    for row in records:
        length = row["sequence_length"]
        batch["input_ids"].append(row["input_ids"][:length] + [pad_token_id] * (width - length))
        batch["attention_mask"].append([1] * length + [0] * (width - length))
        batch["labels"].append(row["labels"][:length] + [-100] * (width - length))
    return batch, summaries


def smoke(folder, *, pad_token_id, batch_size=2, purpose="smoke"):
    if type(batch_size) is not int or not 1 <= batch_size <= 32:
        raise ValueError("Invalid bounded batch size")
    manifest, rows = load_package(folder, purpose=purpose)
    if pad_token_id != manifest["pad_token_id"] or type(pad_token_id) is not int:
        raise ValueError("Smoke padding token must match package tokenizer")
    if not rows:
        raise ValueError("No encoded sample available for loading smoke")
    reports = []
    for offset in range(0, len(rows), batch_size):
        chunk = rows[offset:offset + batch_size]
        batch, summaries = collate(chunk, pad_token_id=pad_token_id, purpose=purpose)
        reports.append({"sample_ids": [r["sample_id"] for r in summaries],
                        "shape": [len(chunk), len(batch["input_ids"][0])],
                        "supervised_tokens": sum(r["supervised_tokens"] for r in summaries),
                        "padding_tokens": sum(mask.count(0) for mask in batch["attention_mask"])})
    return {"schema": "student-sample-loading-smoke-v1", "passed": True, "purpose": purpose,
            "samples": len(rows), "batches": reports, "optimizer_steps": 0, "model_weights_loaded": False,
            "package_manifest_sha256": sha((Path(folder) / "manifest.json").read_bytes()),
            "loader_sha256": sha(Path(__file__).read_bytes()), "backend": "framework_neutral_python_arrays",
            "scope": "JSONL integrity, current-assistant loss mask and batch padding; not framework/GPU training"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pad-token-id", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--purpose", choices=("smoke", "training"), default="smoke")
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(args.package.resolve()):
        raise ValueError("Output must be new and outside the sealed token package")
    result = smoke(args.package, pad_token_id=args.pad_token_id, batch_size=args.batch_size, purpose=args.purpose)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "batches"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
