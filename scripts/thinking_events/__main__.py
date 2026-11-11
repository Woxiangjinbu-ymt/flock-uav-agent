"""Replay the teacher archives and prepare the paired student development study."""

import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from . import VERSION
from .archive import read_turns, verify_sources
from .events import extract_events
from .labels import label_turns
from .report import summarize_g3

ROOT = Path(__file__).resolve().parents[2]


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def write_lines(path, rows):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False))
            stream.write("\n")


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replay(output):
    """Stage the whole batch, reserve a fresh directory, and publish the gate last."""
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"replay requires a new output directory: {output}")
    sources, manifest = verify_sources(ROOT)
    decisions = []
    attempts = []
    for source in sources:
        turns, source_attempts = read_turns(source)
        attempts.extend(source_attempts)
        for raw_turn in turns:
            prefix = raw_turn.pop("prefix")
            decisions.append({**raw_turn, **extract_events(prefix), "split_usage": "development",
                              "schema_version": VERSION})
    decisions.sort(key=lambda row: (row["batch"], row["episode"], row["turn"]))
    attempts.sort(key=lambda row: (row["batch"], row["episode"], row["request_sequence"]))
    rows, schedule = label_turns(decisions)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix=".g3-replay-") as staging:
        directory = Path(staging)
        write_json(directory / "sources.json", manifest)
        write_lines(directory / "attempts.jsonl", attempts)
        write_lines(directory / "turns.jsonl", rows)
        gate = summarize_g3(rows, attempts, schedule,
                            source_sha256=file_digest(directory / "sources.json"),
                            turns_sha256=file_digest(directory / "turns.jsonl"),
                            attempts_sha256=file_digest(directory / "attempts.jsonl"))
        write_json(directory / "g3.json", gate)
        output.mkdir()
        published = []
        try:
            for name in ("sources.json", "attempts.jsonl", "turns.jsonl", "g3.json"):
                (directory / name).rename(output / name)
                published.append(output / name)
        except OSError:
            for path in published:
                path.unlink()
            output.rmdir()
            raise
    return gate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser("replay", help="recompute G3 from four sealed development batches")
    runner.add_argument("--output", required=True, type=Path, help="new local output directory")
    prepare = commands.add_parser("prepare-dev", help="freeze the 29-task development study and 116 planned slots")
    prepare.add_argument("--study", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--legacy", action="store_true", help="rebuild the historical 1.7B/4B task layout")
    dev = commands.add_parser("run-dev", help="check readiness or execute a frozen paired batch")
    dev.add_argument("--run", type=Path, required=True)
    dev.add_argument("--g3", type=Path, required=True)
    dev.add_argument("--backend", choices=("fixture", "transformers", "vllm"),
                     help="omit for readiness only; fixture runs the local acceptance study")
    dev.add_argument("--student-python", type=Path, help="interpreter for the student service")
    dev.add_argument("--backend-python", type=Path, help="separate inference interpreter, if selected at T37")
    dev.add_argument("--model-root", type=Path, help="contains offline Qwen3.5-2B and Qwen3-4B snapshots")
    dev.add_argument("--qwen35-tokenizer", type=Path, help="local fixture tokenizer directory")
    dev.add_argument("--max-new", type=int, help="stop after claiming this many new slots; rerun to resume")
    admit = commands.add_parser("admit-t37", help="verify measured T37 evidence and seal the selected backend")
    for name in ("run", "linux-cpu", "session-cpu", "backend-lock", "lease", "student-python"):
        admit.add_argument("--" + name, type=Path, required=True)
    admit.add_argument("--backend", choices=("transformers", "vllm"), required=True)
    admit.add_argument("--backend-python", type=Path)
    admit.add_argument("--fallback-reason", type=Path, help="failed Transformers verdict for a vLLM admission")
    settle = commands.add_parser("settle-prepaid", help="record occupied card seconds and incremental cost at stage end")
    settle.add_argument("--run", type=Path, required=True)
    simulate = commands.add_parser("simulate-paired", help="exercise the revised gate rules with synthetic outcomes on CPU")
    report = commands.add_parser("report", help="replay independent slot scores, or explicit synthetic results")
    for command in (simulate, report):
        command.add_argument("--run", type=Path, required=True, help="prepared Qwen3.5-2B/4B campaign")
        command.add_argument("--output", type=Path, required=True, help="new report directory, outside the prepared run")
    report.add_argument("--results", type=Path, help="explicit synthetic results; omit to report the executed run")
    report.add_argument("--g3", type=Path, help="G3 evidence for the executed run")
    args = parser.parse_args()
    try:
        if args.command == "replay":
            gate = replay(args.output)
            result = {"status": gate["status"], "counts": gate["counts"], "interval": gate["interval"],
                      "output": str(args.output)}
        elif args.command == "prepare-dev":
            from .campaign import LEGACY_PROTOCOL, PAIRED_PROTOCOL, prepare_dev
            result = prepare_dev(args.study, args.output, protocol=LEGACY_PROTOCOL if args.legacy else PAIRED_PROTOCOL)
            result["output"] = str(args.output)
        elif args.command == "admit-t37":
            from .paired_admission import admit_t37
            result = admit_t37(args.run, backend=args.backend, linux_cpu=args.linux_cpu, session_cpu=args.session_cpu,
                               backend_lock=args.backend_lock, lease=args.lease, student_python=args.student_python,
                               backend_python=args.backend_python, fallback_reason=args.fallback_reason)
        elif args.command == "settle-prepaid":
            from .campaign_storage import RentalBudget, locked
            with locked(args.run, ".coordinator.lock"):
                result = RentalBudget(args.run).settle_prepaid()
        elif args.command == "run-dev":
            if args.backend is None:
                if any(v is not None for v in (
                    args.student_python, args.backend_python, args.model_root, args.qwen35_tokenizer, args.max_new
                )):
                    raise ValueError("Select --backend when requesting batch execution")
                from .campaign import run_dev
                result = run_dev(args.run, args.g3)
            else:
                from .paired_batch import execute_batch
                if args.student_python is None:
                    raise ValueError("Select the student service with --student-python")
                result = execute_batch(args.run, args.g3, backend=args.backend, student_python=args.student_python,
                                       model_root=args.model_root, backend_python=args.backend_python,
                                       tokenizer=args.qwen35_tokenizer, max_new=args.max_new)
        elif args.command == "report" and args.results is None:
            from .paired_actual_report import report_actual
            from .paired_batch import DEFAULT_G3
            result = report_actual(args.run, args.output, g3=args.g3 or DEFAULT_G3)
        else:
            from .campaign import PAIRED_PROTOCOL, verify_run
            from .campaign_storage import read_jsonl
            from .paired_simulation import report_paired, simulate_paired
            campaign = verify_run(args.run)
            if campaign["schema"] != PAIRED_PROTOCOL:
                raise ValueError("Paired scoring requires a revised Qwen3.5-2B campaign")
            if args.output.resolve().is_relative_to(args.run.resolve()):
                raise ValueError("Keep synthetic reports outside the prepared campaign")
            plan = read_jsonl(args.run / "plan-slots.jsonl")
            if args.command == "simulate-paired":
                simulate_paired(plan, args.output)
            else:
                report_paired(plan, args.results, args.output)
            result = {"evidence": "synthetic", "output": str(args.output), "formal_launches": 0}
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        parser.exit(1, f"{args.command}: {error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
