"""Validate the two scene exports and assemble paired A-fold pilot files in frozen order."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from flockcue_session_cpu.verify import require
from thinking_events.campaign_storage import atomic_write

SCENES = ("04cd6269", "ef36e27a")
NAMES = ("keep-reasoning", "remove-reasoning")


def records(file):
    return [json.loads(line) for line in file.read_bytes().splitlines()]


def validate_scene(review, scene_id):
    folder = review / scene_id / "final"
    summary = json.loads((folder / "summary.json").read_bytes())
    require(summary["fold"] == "A" and summary["scene"] == scene_id and summary["phase"] == "final"
            and summary["slots"] == summary["scored"] == 20, "Expected a reviewed 20-task A scene")
    for name, digest in summary["artifacts"].items():
        require(sha256((folder / name).read_bytes()).hexdigest() == digest, f"Changed artifact: {name}")
    selected = [row for row in records(folder / "decisions.jsonl") if row["kept"]]
    paths = [folder / (name + ".jsonl") for name in NAMES]
    require(len(selected) == summary["retained_decisions"], "Selected decision count differs")
    counts = Counter()
    identifiers = set()
    for row, keep, remove in zip(selected, records(paths[0]), records(paths[1]), strict=True):
        sid = row["id"]
        require(keep["sample_id"] == remove["sample_id"] == sid and sid.startswith(scene_id + "/")
                and sid not in identifiers, "Duplicate or mismatched sample ID")
        identifiers.add(sid)
        require(set(keep) == set(remove) == {"sample_id", "messages", "tools", "target"}
                and keep["messages"] == remove["messages"] and keep["tools"] == remove["tools"]
                and remove["target"] == {k: v for k, v in keep["target"].items() if k != "reasoning_content"},
                "Unexpected training field or changed public input, actions or response")
        on, off = (row["tokenization"][k] for k in ("keep_reasoning", "remove_reasoning"))
        require(on["prompt_ids_sha256"] == off["prompt_ids_sha256"] and
                on["masked_prompt_tokens"] == off["masked_prompt_tokens"] and
                on["roundtrip_tool_names"] == off["roundtrip_tool_names"] and
                on["supervised_tokens"] >= off["supervised_tokens"] > 0 and
                off["supervised_reasoning_tokens"] == 0, "Paired labels differ outside current thought")
        counts.update(decisions=1, decisions_with_supervised_thought=int(on["supervised_reasoning_tokens"] > 0),
                      masked_prompt_tokens=on["masked_prompt_tokens"],
                      keep_target_tokens=on["supervised_tokens"], remove_target_tokens=off["supervised_tokens"],
                      keep_thought_tokens=on["supervised_reasoning_tokens"])
    return identifiers, counts, paths


def combine(review):
    ids = set()
    totals = Counter()
    inputs = {name: [] for name in NAMES}
    by_scene = {}
    for scene_id in SCENES:
        scene_ids, counts, paths = validate_scene(review, scene_id)
        require(not ids.intersection(scene_ids), "Sample ID overlaps between scenes")
        ids.update(scene_ids)
        totals.update(counts)
        by_scene[scene_id] = dict(counts)
        for name, path in zip(NAMES, paths, strict=True):
            inputs[name].append(path.read_bytes())
    outputs = {}
    for name, chunks in inputs.items():
        path = review / ("A-" + name + ".jsonl")
        data = b"".join(chunks)
        require(len(data.splitlines()) == len(ids), "Combined file changed the denominator")
        atomic_write(path, data)
        outputs[path.name] = sha256(path.read_bytes()).hexdigest()
    result = {"fold": "A", "source_scenes": by_scene, "totals": dict(totals), "sha256": outputs}
    atomic_write(review / "paired-dataset.json", (json.dumps(result, indent=2) + "\n").encode())
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    print(json.dumps(combine(parser.parse_args().review), ensure_ascii=False))
