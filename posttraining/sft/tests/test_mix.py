"""Check sampling weights and A-fold boundaries against the frozen source packs."""
from collections import Counter
import json

import pytest

from posttraining.sft import mix


def test_release_filters_and_replays_exact_rows(tmp_path):
    if not (mix.RELEASE / mix.PUBLIC).exists():
        pytest.skip("The archived teacher public-wire pack is required to verify the original closings")
    output = tmp_path / "mixed"
    manifest = mix.build(output)
    indexes = list(map(json.loads, (output / "index.jsonl").read_bytes().splitlines()))
    counts = Counter(r["train_row_sha256"] for r in indexes)
    reviewed = {r["train_row_sha256"] for r in map(json.loads, (mix.CLOSING / "index.jsonl").read_bytes().splitlines())}
    assert len(counts) == 387 and len(indexes) == 423
    assert set(counts.values()) == {1, 3}
    assert {h for h, n in counts.items() if n == 3} == reviewed
    assert len(reviewed) == 18
    assert all(r["masked_prompt_tokens"] + mix.MAX_GENERATION <= mix.MAX_CONTEXT for r in indexes)
    assert {r["session_id"] for r in indexes} == set(manifest["scene_ids"])
    assert manifest["unique_counts"]["kinds"]["closing"] == 11
    assert manifest["counts"]["kinds"]["closing"] == 29
    assert Counter(reason for r in manifest["excluded"] for reason in r["reasons"]) == {
        "prompt_over_full_generation_reserve": 9, "first_pass_closing_review": 3,
    }
    assert manifest["mixture"]["reviewed_original_terminal_overlap_rejections"] == []
    assert not {r["train_row_sha256"] for r in manifest["excluded"]} & counts.keys()
