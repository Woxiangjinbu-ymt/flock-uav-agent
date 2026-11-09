"""One real-weight development episode on the Mac's local v5 simulator."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from student_input_v2 import InputRejected
from thinking_events.campaign_storage import SlotLedger, atomic_write, json_bytes
from thinking_events.student import SAMPLING, output_budget
from thinking_events.student_process import _public_history, stopped
from thinking_events.worker import _launch, _manifest, _metrics, _run_with_service
from validate_harness_episode import sha, write
from validate_harness_natural_runner import validate_score, validate_worker

from .adapter import DEFAULT_CACHE, EOS_IDS, REVISION, load_tokenizer, parse_generation, render
from .smoke import MODEL, ROOT, TOKENIZER_DIR, hover_trial, prepare_source

SCOPE = "qwen35_mac_real_weights_dev_hover"
SLOT = "mac-qwen35-off"
ON_SLOT = "mac-qwen35-on"


def model_spec(thinking=False):
    from .mac_model import WEIGHT_FILE, WEIGHT_SHA256

    return {"model": MODEL, "backend": "mlx", "revision": REVISION, "weight_file": WEIGHT_FILE,
            "weight_sha256": WEIGHT_SHA256, "thinking": thinking, "sampling": SAMPLING, "eos_ids": sorted(EOS_IDS)}


def prepare(output, cache, *, takeoff_example=False, thinking=False):
    from agent_harness.__main__ import validate_output

    if thinking and not takeoff_example:
        raise ValueError("The paired thinking-on run requires the corrected take_off example")
    spec = model_spec(thinking)
    run = validate_output(output) / "mac-run"
    common = prepare_source(run, cache, extra_files=[ROOT / "training/requirements-qwen35-mac-mlx.lock"])
    manifest = {**common, "scope": SCOPE, "model": spec, "trials": [hover_trial(ON_SLOT if thinking else SLOT)],
                "seed_rule": "first four bytes of SHA256(task_id/turn), big-endian",
                "formal_slots_started": 0, "teacher_calls": 0}
    if takeoff_example:
        from . import takeoff_example as control

        manifest["intervention"] = control.prepare_variant(run, manifest["sources"])
        manifest["matched_command_id"] = control.match_command_id(run, manifest["sources"])
        manifest["control_baseline"] = {
            "manifest_sha256": control.PAIRED_OFF_MANIFEST_SHA if thinking else control.CONTROL_MANIFEST_SHA,
            "expected_treatment_first_prompt": control.ON_FIRST_PROMPT if thinking else control.FIRST_PROMPT,
        }
    write(run / "manifest.json", manifest)
    (run / "manifest.sha256").write_text(sha((run / "manifest.json").read_bytes()) + "\n")
    return run


class LiveService:
    """Render public SDK requests, generate raw tokens in the model process and dispatch atomically."""

    def __init__(self, model, tokenizer, *, guard_run=None, thinking=False):
        self.model = model
        self.tokenizer = tokenizer
        self.guard_run = guard_run
        self.thinking = thinking
        self.slot = ON_SLOT if thinking else SLOT

    def cancel(self):
        self.model.cancel()

    def request(self, *, slot, turn, messages, tools, thinking, seed, spent_tokens, episode_deadline, request_timeout):
        start = time.monotonic()
        if slot != self.slot or thinking is not self.thinking or turn < 1 or turn > 12 or not _public_history(messages, tools):
            return stopped("input_rejected")
        deadline = min(episode_deadline, start + request_timeout)
        timeout = "episode_deadline" if episode_deadline <= start + request_timeout else "request_timeout"
        if start >= deadline:
            return stopped(timeout)
        try:
            prompt, prepared = render(self.tokenizer, messages, tools, thinking)
        except InputRejected as exc:
            return {**stopped("input_rejected"), "error": exc.code}
        if turn == 1 and self.guard_run is not None:
            from .takeoff_example import check_first_prompt

            check_first_prompt(self.guard_run, prompt, seed, thinking_slot=self.slot if thinking else None)
        ids = prompt["input_ids"]
        limit, reason = output_budget(len(ids), spent_tokens)
        audit = {key: prompt[key] for key in ("input_ids", "template_sha256", "history_reasoning_tokens")}
        audit.update(max_new_tokens=limit, seed=seed, sampling=SAMPLING, thinking=thinking)
        if reason:
            return {**stopped(reason), **audit}
        try:
            measurement = self.model.generate(ids, limit, seed, deadline)
        except TimeoutError:
            self.cancel()
            return {**stopped(timeout), **audit}
        except (OSError, EOFError, ValueError, RuntimeError) as exc:
            self.cancel()
            return {**stopped("student_backend_error"), **audit, "error": f"{type(exc).__name__}: {exc}"}
        generated = measurement["generated_ids"]
        first = measurement.pop("first_token_monotonic")
        measurement["timing"]["ttft_seconds"] = None if first is None else first - start
        result = parse_generation(self.tokenizer, generated, EOS_IDS, thinking, prepared, limit)
        usage = {"prompt_tokens": len(ids), "completion_tokens": len(generated), "total_tokens": len(ids) + len(generated),
                 "completion_tokens_details": {"reasoning_tokens": result["reasoning_tokens"]}}
        if time.monotonic() >= deadline:
            self.cancel()
            return {**stopped(timeout), **audit, **measurement, "usage": usage, "late_generation": True}
        errors = result["anomalies"]
        return {**audit, **measurement, **result, "usage": usage, "status": "format_error" if errors else "ok",
                "stop_reason": errors[0] if errors else None}


def run_trial(run, manifest, model_python, weights):
    from .mac_model import MacModel

    trial, = manifest["trials"]
    slot = trial["trial_id"]
    thinking = manifest["model"]["thinking"]
    result = {"trial_id": slot, "task_id": trial["task_id"], "passed": None, "status": "unscored"}
    for name in ("process-logs", "worker-exit-seals", "scorer-exit-seals"):
        (run / name).mkdir()
    try:
        tokenizer = load_tokenizer(run / "source" / TOKENIZER_DIR)
        with MacModel(model_python, weights, scripts_root=run / "source/scripts") as model:
            runtime = model.metadata
            write(run / "model-runtime.json", runtime)
            result["model_runtime_sha256"] = sha((run / "model-runtime.json").read_bytes())
            fields = ("backend", "model", "revision", "weight_sha256")
            if not runtime.get("weights_loaded") or any(runtime.get(k) != manifest["model"][k] for k in fields):
                raise ValueError("Loaded model does not match the frozen model identity")
            service = LiveService(model, tokenizer, thinking=thinking, guard_run=run if "intervention" in manifest else None)
            proc = _run_with_service(run, manifest, trial, MODEL, thinking, service, False, None)
            result["worker_pid"] = proc["pid"]
        worker = validate_worker(run, trial, proc, manifest=manifest)
        result.update(initial=worker["initial"], stop_reason=worker["limits"]["stop_reason"],
                      agent_returned=worker["agent"]["success"],
                      worker_seal_sha256=sha((run / "worker-exit-seals" / f"{slot}.json").read_bytes()))
        folder = run / "trials" / slot
        usage = _metrics(folder, worker, "mlx")
        usage.update(cost_cny=0, cost_basis="own Mac; no rental or external service",
                     vram_measurement="no dedicated VRAM counter; see MLX memory in per-request audit")
        result["usage"] = usage
        scorer = _launch(run, manifest, trial, "score")
        score = validate_score(run, trial, worker, scorer)
        if scorer["pid"] == proc["pid"]:
            raise ValueError("Scorer reused worker process identity")
        result.update(status="scored", passed=score["checker"]["passed"], scorer_pid=scorer["pid"],
                      scorer_seal_sha256=sha((run / "scorer-exit-seals" / f"{slot}.json").read_bytes()))
    except (OSError, ValueError, RuntimeError, KeyError, EOFError, TimeoutError, subprocess.SubprocessError) as exc:
        result["unscored_reason"] = f"{type(exc).__name__}: {exc}"
    return result


def execute(run, model_python, weights):
    if ROOT != run / "source":
        raise ValueError("Launch the coordinator from its frozen source")
    manifest = _manifest(run)
    thinking = manifest["model"]["thinking"]
    slot = ON_SLOT if thinking else SLOT
    if (type(thinking) is not bool or manifest["scope"] != SCOPE or manifest["trials"] != [hover_trial(slot)]
            or manifest["model"] != model_spec(thinking)):
        raise ValueError("Malformed development trial or different model settings")
    ledger = SlotLedger(run, [{"slot_id": slot, "trial_id": slot, "task_id": manifest["trials"][0]["task_id"]}])
    ledger.initialize()
    if not ledger.claim(slot, (run / "manifest.sha256").read_text().strip()):
        raise ValueError("This development slot has already been claimed")
    result = run_trial(run, manifest, model_python, weights)
    ledger.finish(slot, result)
    report = {"scope": SCOPE, "formal_slots_started": 0, "teacher_calls": 0, "rental_cost_cny": 0, "row": result}
    atomic_write(run.parent / "smoke.json", json_bytes(report))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "scored" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path, help="fresh result directory")
    mode.add_argument("--frozen-run", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--model-python", type=Path, required=True, help="Python environment for native generation")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="pinned local tokenizer files")
    parser.add_argument("--takeoff-example", action="store_true",
                        help="change only the take_off example to the wrapped input_json form")
    parser.add_argument("--thinking", action="store_true",
                        help="enable the native thinking template in the paired takeoff-example run")
    args = parser.parse_args()
    if args.frozen_run:
        return execute(args.frozen_run.resolve(), args.model_python, args.weights)
    # Retain the venv path. Resolving a venv's python symlink selects the system interpreter.
    python = args.model_python.absolute()
    weights = args.weights.resolve(strict=True)
    if not python.is_file():
        parser.error("model Python does not exist")
    run = prepare(args.output, args.cache, takeoff_example=args.takeoff_example, thinking=args.thinking)
    source = run / "source"
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL") if key in os.environ}
    env.update(PYTHONPATH=str(source / "scripts"), PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    return subprocess.run([sys.executable, "-m", "flockcue_qwen35.mac_smoke", "--frozen-run", str(run),
                           "--weights", str(weights), "--model-python", str(python)], env=env, cwd=source).returncode


if __name__ == "__main__":
    raise SystemExit(main())
