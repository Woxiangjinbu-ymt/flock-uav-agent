"""Summarize the sealed failure-analysis cohort without changing active runs."""

import hashlib
import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "docs/results/failure-analysis-20261005"
OUTPUT = ROOT / "docs/audit/COST_BASELINE_20261005.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def distribution(values):
    values = sorted(values)

    def percentile(q):
        position = (len(values) - 1) * q
        low, high = math.floor(position), math.ceil(position)
        return values[low] + (values[high] - values[low]) * (position - low)

    return {
        "count": len(values),
        "mean": statistics.mean(values),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": max(values),
    }


def main():
    hashes = json.loads((SNAPSHOT / "evidence-sha256.json").read_text())
    used_hashes = {}

    def evidence(relative):
        path = ROOT / relative
        actual = digest(path)
        if hashes.get(relative) != actual:
            raise ValueError(f"Evidence not in snapshot or hash mismatch: {relative}")
        used_hashes[relative] = actual
        return json.loads(path.read_text())

    all_rows = {
        model: json.loads((SNAPSHOT / f"{model}-tasks.json").read_text())
        for model in ("deepseek", "qwen")
    }
    paired_keys = {(r["session_id"], r["task_id"]) for r in all_rows["qwen"]}
    telemetry = json.loads((SNAPSHOT / "paired-telemetry.json").read_text())
    result = {
        "source_snapshot": "2026-10-05T09:35:44.985834+08:00",
        "scope": "Same 64 sealed scenes, 1280 tasks per model, configured 500 s budget",
        "definitions": {
            "agent_terminal_seconds": "Job completed_at minus action-window opened_at; includes cancellation/cleanup, not physical flight duration",
            "scoring_ready_seconds": "Barrier frozen_at minus action-window opened_at; includes polling and draining, excludes actual checker computation",
            "task_timeout_rate": "agent_timed_out tasks / all paired tasks, including timeouts whose final checks pass",
            "scenes_with_timeout_rate": "Scenes containing at least one timed-out task / all paired scenes; not scene-deadline violation rate",
            "tokens": "Provider-observed cumulative usage across all calls, including repeated/cached input and failed tasks; not monetary cost",
            "percentiles": "Linear interpolation at (n-1)*q",
        },
        "cohorts": {},
    }
    for model, source_rows in all_rows.items():
        rows = [r for r in source_rows if (r["session_id"], r["task_id"]) in paired_keys]
        assert len(rows) == len(paired_keys) == 1280
        terminal, frozen = [], []
        for row in rows:
            prefix = f'{row["evidence_dir"]}/{row["task_id"]}'
            window = evidence(prefix + ".window.json")
            barrier = evidence(prefix + ".barrier.json")
            end = datetime.fromisoformat(row["completed_at"]).replace(
                tzinfo=ZoneInfo("Asia/Shanghai")
            ).timestamp()
            terminal.append(end - window["opened_at"])
            frozen.append(barrier["frozen_at"] - window["opened_at"])
            assert terminal[-1] >= 0 and frozen[-1] >= 0
            assert abs(window["cutoff_at"] - window["opened_at"] - 500) < 0.001
        scenes = {r["session_id"] for r in rows}
        timeout_rows = [r for r in rows if r["agent_timed_out"]]
        timeout_scenes = {r["session_id"] for r in timeout_rows}
        token_metrics = {}
        for field in ("prompt_tokens", "completion_tokens", "total_tokens", "cache_hit_tokens", "cache_miss_tokens"):
            values = [r["observed_token_usage"].get(field) for r in rows]
            if any(value is None for value in values):
                token_metrics[field] = {"available_tasks": sum(v is not None for v in values)}
                continue
            assert sum(values) == telemetry[model]["llm"]["usage"][field]
            token_metrics[field] = {"total": sum(values), **distribution(values)}
        result["cohorts"][model] = {
            "tasks": len(rows), "scenes": len(scenes),
            "passed": sum(r["task_pass"] for r in rows),
            "task_timeouts": len(timeout_rows),
            "task_timeout_rate": len(timeout_rows) / len(rows),
            "timeouts_with_passing_final_checks": sum(r["task_pass"] for r in timeout_rows),
            "scenes_with_timeout": len(timeout_scenes),
            "scenes_with_timeout_rate": len(timeout_scenes) / len(scenes),
            "agent_terminal_seconds": distribution(terminal),
            "scoring_ready_seconds": distribution(frozen),
            "token_usage": token_metrics,
        }
    result["provenance"] = {
        "analysis_script_sha256": digest(Path(__file__)),
        "snapshot_inputs_sha256": {
            name: digest(SNAPSHOT / name)
            for name in ("deepseek-tasks.json", "qwen-tasks.json", "paired-telemetry.json", "evidence-sha256.json")
        },
        "verified_evidence_files": len(used_hashes),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["cohorts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
