"""Reconstruct the frozen metadata API response offline, without a server."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
ATTEMPT = ROOT / "experiments/runs/20261006-214358-m1r-fast100-b4e9b6f3"
sys.path.insert(0, str(ATTEMPT / "runtime/server"))
from models.session import Session

outputs = {}
reconstruction_differences = {}
for stage in ("before", "after"):
    saved = json.loads((ATTEMPT / f"35ce64d8/b1ad4f3e.{stage}.json").read_text())
    # This is a reconstruction of the public metadata serializer, not a claim
    # that the model called get_session_info or received this exact response.
    metadata = Session.from_dict(saved).to_dict(data=False)
    assert not set(metadata).intersection({"targets", "drones", "history", "tasks", "obstacles"})
    progress = metadata["statistics"]["task_progress"]
    assert progress["progress_percentage"] == 80
    assert progress["details"] == {"total_targets": 20, "visited_targets": 16, "unvisited_targets": 4}
    original = saved["statistics"]["task_progress"]
    for key in ("task_type", "progress_percentage", "is_completed", "details"):
        assert progress[key] == original[key]
    # Rehydrating stored visit history can deduplicate repeated reach records;
    # record such ancillary differences instead of claiming a byte-exact replay.
    reconstruction_differences[stage] = {key: {"reconstructed": progress[key], "saved": original.get(key)}
                                       for key in progress if progress[key] != original.get(key)}
    outputs[stage] = metadata
(OUT / "aggregate-feedback-replay.json").write_text(json.dumps({
    "scope": "public metadata reconstructed offline; no task-success or actual model-input claim",
    "defined_but_unregistered_tool": "get_session_info", "endpoint": "/sessions/current",
    "field": "statistics.task_progress", "reconstructed_metadata": outputs,
    "ancillary_reconstruction_differences": reconstruction_differences,
}, ensure_ascii=False, indent=2) + "\n")
print("PASS: existing metadata serializer reports 80% before and after, with no entity/history payload")
