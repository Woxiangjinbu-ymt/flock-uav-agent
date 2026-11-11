"""Summarize only the snapshot's paired sealed scenes; no service calls.

Run with controller/.venv/bin/python, passing the analysis folder.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from reproduction_metrics import summarize_performance, terminal_provider_errors, _latencies


def read(path):
    return json.loads(path.read_text())


def main():
    out = Path(sys.argv[1]).resolve()
    rows = {m: read(out / f"{m}-tasks.json") for m in ("deepseek", "qwen")}
    q_scenes = {x["session_id"] for x in rows["qwen"]}
    evidence = read(out / "evidence-sha256.json")
    result = {}
    for model, records in rows.items():
        selected = [x for x in records if x["session_id"] in q_scenes]
        files = []
        for dirname in sorted({x["evidence_dir"] for x in selected}):
            directory = ROOT / dirname
            path = directory / "agent-traces/reproduction-metrics.jsonl"
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            assert sha == read(directory / "complete.json")["files"]["agent-traces/reproduction-metrics.jsonl"]
            evidence[str(path.relative_to(ROOT))] = sha
            files.append(path)
        perf = summarize_performance(files)
        assert perf["malformed_records"] == 0
        errors = []
        for row in selected:
            errors.extend(terminal_provider_errors(ROOT / row["evidence_dir"] / "agent-traces/reproduction-metrics.jsonl", row["job_id"]))
        result[model] = {k: perf[k] for k in ("llm", "http", "models", "malformed_records")}
        result[model]["terminal_provider_errors"] = errors
        result[model]["by_bucket"] = {}
        for bucket in ("pass", "near_miss", "zero_pass", "other_partial"):
            chosen = [perf["by_job"][x["job_id"]] for x in selected if x["bucket"] == bucket]
            result[model]["by_bucket"][bucket] = {
                "tasks": len(chosen),
                "calls": _latencies([x["llm"]["started_calls"] for x in chosen]),
                "llm_total_seconds": sum(x["llm"]["latency"]["count"] * (x["llm"]["latency"]["mean_seconds"] or 0) for x in chosen),
            }
    (out / "paired-telemetry.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (out / "evidence-sha256.json").write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    main()
