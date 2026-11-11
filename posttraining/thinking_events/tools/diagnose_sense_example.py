"""Prepare, run and independently score the paired 2B sense-tool example experiment."""


import argparse
import json
from pathlib import Path
import sys

_workspace = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts").is_dir())
if __name__ == "__main__" and not __package__:
    sys.path[:0] = [str(_workspace / "scripts"), str(_workspace / "compat/scripts"), str(_workspace)]



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="reconstruct the frozen prefixes on CPU")
    execute = commands.add_parser("run", help="run or recover the single-A100 experiment")
    report = commands.add_parser("report", help="score archived token IDs on CPU")
    for command in (prepare, execute, report):
        command.add_argument("--work", type=Path, required=True, help="experiment data directory")
        command.add_argument("--model-path", type=Path, required=True, help="pinned local Qwen3.5-2B snapshot")
    for command in (prepare, execute):
        command.add_argument("--archive", type=Path, required=True, help="original 116-slot campaign directory")
    prepare.add_argument("--index", type=Path, required=True, help="frozen 50-row prefix index")
    execute.add_argument("--lease-until", type=int, required=True,
                         help="Unix timestamp for the authorized operating window within the prepaid week")
    report.add_argument("--output", type=Path, required=True, help="directory for regenerated tables and report")
    args = parser.parse_args()
    if args.command == "prepare":
        from thinking_events.sense_example_prepare import prepare as prepare_inputs

        plan = prepare_inputs(args.index, args.archive, args.work, args.model_path)
        print(f"Prepared {len(plan['slots'])} slots in {args.work}")
    elif args.command == "run":
        from thinking_events.sense_example_run import run

        ledger = run(args.work, args.archive, args.model_path, lease_until=args.lease_until)
        print(json.dumps(ledger, ensure_ascii=False))
    else:
        from thinking_events.sense_example_report import report as score

        score(args.work, args.model_path, args.output)
        print(f"Wrote paired results to {args.output}")


if __name__ == "__main__":
    main()
