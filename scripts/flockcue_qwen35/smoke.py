"""Run two Qwen3.5 native-output fixtures through the locked local hover task."""
import argparse
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from thinking_events.campaign_storage import run_local_slots
from thinking_events.student import output_budget
from thinking_events.student_process import _public_history, stopped
from thinking_events.worker import run_slot
from thinking_events.worker_smoke import ACTOR, OTHER
from student_input_v2 import InputRejected
from validate_harness_episode import check_hashes, sha, write

from .adapter import DEFAULT_CACHE, EOS_IDS, FILES, REVISION, load_tokenizer, parse_generation, render

ROOT = Path(__file__).resolve().parents[2]
TOKENIZER_DIR = Path("training/tokenizers/qwen35-dev")
MODEL = "Qwen3.5-2B"
TRUNCATED_CAP = 64


def native_call(name, arguments=None):
    blocks = ["<tool_call>", f"<function={name}>"]
    if arguments is not None:
        blocks.extend(["<parameter=input_json>", json.dumps(arguments, separators=(",", ":")), "</parameter>"])
    return "\n".join([*blocks, "</function>", "</tool_call>"])


def fixture_replies():
    fleet = native_call("list_drones")
    read = native_call("get_drone_status", {"drone_id": ACTOR})
    other = native_call("get_drone_status", {"drone_id": OTHER})
    takeoff = native_call("take_off", {"drone_id": ACTOR, "altitude": 12})
    hover = native_call("hover", {"drone_id": ACTOR})
    # Thinking-on prompts already open the thought. Each reply closes it.
    success = [fleet, read + "\n" + other, takeoff, hover, read, "Done. [TASK DONE]"]
    return {
        "cpu-success": [
            ("Read fleet identities." if i == 0 else "") + "\n</think>\n\n" + action + "<|im_end|>"
            for i, action in enumerate(success)
        ],
        "cpu-truncated": [fleet + "<|im_end|>", takeoff + "\n" + hover + "<|im_end|>"],
    }


def prepare(output, cache):
    from agent_harness.__main__ import checked_reference, prepare_locked, validate_output

    output = validate_output(output)
    lock = json.loads((ROOT / "agent_harness/baseline.lock.json").read_bytes())
    ref = checked_reference(lock)
    check_hashes(cache, FILES)
    run = output / "cpu-run"
    run.mkdir(parents=True)
    source = run / "source"
    with TemporaryDirectory() as scratch:
        baseline = Path(scratch) / "baseline"
        prepare_locked(lock, ref, baseline)
        shutil.copytree(baseline / "source", source)
        shutil.copytree(baseline / "inputs", run / "inputs")
    dependencies = [
        ROOT / lock["profile"], ROOT / "training/requirements-input.lock",
        ROOT / "scripts/student_input.py", ROOT / "scripts/student_input_v2.py",
        ROOT / "scripts/prepare_task_dataset.py", *sorted((ROOT / "scripts/thinking_events").glob("*.py")),
        *sorted((ROOT / "scripts/flockcue_qwen35").glob("*.py")),
    ]
    files = {p.relative_to(ROOT): p for p in dependencies}
    files.update({TOKENIZER_DIR / name: cache / name for name in FILES})
    sources = dict(ref["sources"])
    for relative, origin in files.items():
        dest = source / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, dest)
        sources[str(relative)] = sha(dest.read_bytes())

    runtime = {"task_seconds": 500, "drain_seconds": 0.5, "request_limits": {
        "max_total_tokens": 100000, "max_http_attempts": 12, "max_output_tokens": 2048,
        "per_request_timeout_seconds": 120}}
    replies = fixture_replies()
    write(run / "fixture-replies.json", replies)
    manifest = {"scope": "student_cpu_regression", "workspace": str(ROOT), "study": str(run / "inputs"),
                "sources": sources, "inputs": ref["inputs"], "private_canary": ref["private_canary"],
                "fixture_sha256": sha((run / "fixture-replies.json").read_bytes()),
                "trials": [{"trial_id": slot, "task_id": "c74d7020", "scheduler": "applicability",
                            "runtime": runtime, "watchdog_seconds": 515} for slot in replies]}
    check_hashes(source, sources)
    write(run / "manifest.json", manifest)
    (run / "manifest.sha256").write_text(sha((run / "manifest.json").read_bytes()) + "\n")
    return run


class NativeFixture:
    """Receive public SDK messages and return a complete parsed message or a stop."""

    def __init__(self, tokenizer, replies):
        self.tokenizer = tokenizer
        self.replies = replies
        self.metadata = {"backend": "fixture", "model": MODEL, "revision": REVISION,
                         "weights_loaded": False, "transformers": version("transformers"),
                         "tokenizers": version("tokenizers"), "template_sha256": FILES["chat_template.jinja"]}

    def request(self, *, slot, turn, messages, tools, thinking, seed, spent_tokens, **_timing):
        if not _public_history(messages, tools) or turn > len(self.replies[slot]):
            return stopped("input_rejected")
        try:
            meta, prepared = render(self.tokenizer, messages, tools, thinking)
        except InputRejected as exc:
            return {**stopped("input_rejected"), "error": exc.code}
        ids = meta["input_ids"]
        limit, reason = output_budget(len(ids), spent_tokens)
        if reason:
            return stopped(reason)
        if (slot, turn) == ("cpu-truncated", 2):
            limit = min(limit, TRUNCATED_CAP)
        full = self.tokenizer.encode(self.replies[slot][turn - 1], add_special_tokens=False)
        generated = full[:limit]
        result = parse_generation(self.tokenizer, generated, EOS_IDS, thinking, prepared, limit)
        anomalies = result["anomalies"]
        return {**result, "status": "format_error" if anomalies else "ok",
                "stop_reason": anomalies[0] if anomalies else None, "seed": seed, "thinking": thinking,
                "template_sha256": meta["template_sha256"], "history_reasoning_tokens": meta["history_reasoning_tokens"],
                "input_ids": ids, "generated_ids": generated, "max_new_tokens": limit,
                "usage": {"prompt_tokens": len(ids), "completion_tokens": len(generated),
                          "total_tokens": len(ids) + len(generated),
                          "completion_tokens_details": {"reasoning_tokens": result["reasoning_tokens"]}}}

    def cancel(self):
        # CPU fixture requests are synchronous. No generator or model can outlive the worker.
        pass


def execute(run):
    if ROOT != run / "source":
        raise ValueError("Run the coordinator from its frozen source")
    manifest = json.loads((run / "manifest.json").read_bytes())
    check_hashes(run, {"fixture-replies.json": manifest["fixture_sha256"]})
    replies = json.loads((run / "fixture-replies.json").read_bytes())
    service = NativeFixture(load_tokenizer(ROOT / TOKENIZER_DIR), replies)
    plan = [{"slot_id": name, "trial_id": name, "task_id": "c74d7020"} for name in replies]

    def run_fixture(row):
        name = row["slot_id"]
        return run_slot(run, name, model=MODEL, thinking=(name == "cpu-success"), service=service,
                        reverse_receipts=(name == "cpu-success"))

    rows = run_local_slots(run, plan, backend_sha256=sha((run / "manifest.json").read_bytes()), execute=run_fixture)
    summary = {"scope": "fixed_native_token_suffixes_on_local_v5_simulator", "gpu_hours": 0,
               "model_weight_generations": 0, "teacher_calls": 0, "formal_slots_started": 0, "rows": rows}
    write(run.parent / "smoke.json", summary)
    if [row["passed"] for row in rows] != [True, False]:
        raise RuntimeError(f"Unexpected checker results; see {run.parent / 'smoke.json'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path, help="fresh directory for the independent runs and evidence")
    mode.add_argument("--frozen-run", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE,
                        help="directory containing the four pinned tokenizer files")
    args = parser.parse_args()
    if args.frozen_run:
        execute(args.frozen_run.resolve())
        return
    run = prepare(args.output, args.cache)
    source = run / "source"
    env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL") if k in os.environ}
    env.update(PYTHONPATH=str(source / "scripts"), PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    subprocess.run([sys.executable, "-m", "flockcue_qwen35.smoke", "--frozen-run", str(run)],
                   check=True, env=env, cwd=source)
    verified = subprocess.run([sys.executable, "-m", "flockcue_qwen35.verify", str(run)],
                              check=True, env=env, cwd=source, stdout=subprocess.PIPE, text=True)
    proof = json.loads(verified.stdout)
    write(run.parent / "verified.json", proof)
    print(json.dumps(proof, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
