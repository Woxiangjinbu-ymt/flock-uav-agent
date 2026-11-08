"""Replay the sealed teacher development archives from the repository root."""

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
    args = parser.parse_args()
    try:
        gate = replay(args.output)
    except (ValueError, OSError) as error:
        parser.exit(1, f"replay: {error}\n")
    print(json.dumps({"status": gate["status"], "counts": gate["counts"],
                      "interval": gate["interval"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
