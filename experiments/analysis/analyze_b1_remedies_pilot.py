#!/usr/bin/env python3
"""Offline B1-remedies versus frozen historical-B0 analysis.

Normal mode requires the frozen runner's read-only verification to pass in a
fresh process. --partial permits explicitly unverified diagnostics, never a
final success-rate claim. No model/service/run command is invoked.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ATTEMPT = ROOT / "experiments/runs/20261006-134416-b1-remedies60-1ee84ea7"
DEFAULT_OUTPUT = ROOT / "docs/results/b1-remedies-pilot-deepseek-20261006"
METHOD_COMMIT = "cb80fde8269a09795782a6ef046d04c381b3f3c9"
SCENES = ("ad34350a", "ef36e27a", "3973d0d6")
PACKAGE_LIMITATION = ("本次比较 cb80fde 整包版本与历史原版 B0，差异同时包含早期 B1 导航契约修改和本轮定向补救；"
                      "不能单独归因新增工具/提示，也不是早期 B1 到本轮补救的消融实验。")


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def path_key(path):
    path = Path(path).resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


class Inputs:
    def __init__(self):
        self.hashes, self.cache = {}, {}

    def read(self, path):
        path = Path(path).resolve()
        if path not in self.cache:
            data = path.read_bytes()
            self.hashes[path_key(path)] = sha(data)
            self.cache[path] = json.loads(data)
        return self.cache[path]

    def recheck(self):
        for name, expected in self.hashes.items():
            require(sha((ROOT / name).read_bytes()) == expected, f"Input changed during analysis: {name}")


def verify_completed(attempt):
    """Use frozen Python/checker source and a fresh import cache; verify only."""
    interpreter = attempt / "runtime/controller/.venv/bin/python"
    verifier = attempt / "runtime/scripts/b1_remedies_pilot.py"
    require(interpreter.is_file() and verifier.is_file(), "Frozen verifier/runtime is unavailable")
    completed = subprocess.run([str(interpreter), "-B", str(verifier), "verify", str(attempt)],
                               capture_output=True, text=True, timeout=180)
    if completed.returncode:
        raise ValueError("Frozen read-only verification failed: " + completed.stderr.strip()[:500])
    result = json.loads(completed.stdout)
    require(result.get("valid_complete_run") is True, "Verifier did not confirm a complete run")
    return result


def observed_usage(performance, job_id, fallback, fallback_source):
    def has_tokens(usage):
        return isinstance(usage, dict) and any(type(usage.get(key)) in (int, float)
                    and math.isfinite(usage[key]) and usage[key] >= 0
                    for key in ("total_tokens", "prompt_tokens", "completion_tokens"))

    usage = performance.get("by_job", {}).get(job_id, {}).get("llm", {}).get("usage")
    if has_tokens(usage):
        source = "scene performance.json by_job"
    elif has_tokens(fallback):
        usage, source = fallback, fallback_source
    else:
        usage, source = {}, None
    # Keep one observation intact: partial primary usage does not combine fields
    # from a different observation or infer total_tokens from prompt/completion.
    return {"observed_total_tokens": usage.get("total_tokens"),
            "observed_prompt_tokens": usage.get("prompt_tokens"),
            "observed_completion_tokens": usage.get("completion_tokens"),
            "calls_with_usage": usage.get("calls_with_usage"), "usage_source": source}


def performance_at(inputs, directory):
    path = directory / "performance.json"
    return inputs.read(path) if path.is_file() else {}


def historical_row(inputs, row, ledger):
    require(ledger["generation"] == 1 and ledger["cohort"] == "first_attempt_no_replay"
            and ledger["job_id"] == row["job_id"] and ledger["original_status"] == "scored"
            and ledger["task_pass"] == row["task_pass"] and ledger["deployment_pass"] == row["deployment_pass"],
            "Historical ledger does not match frozen B0 reference")
    directory = (ROOT / row["evidence"]["window"]).parent
    require(directory.resolve() == (ROOT / ledger["evidence_dir"]).resolve(), "Historical evidence generation differs")
    window = inputs.read(ROOT / row["evidence"]["window"])
    barrier = inputs.read(ROOT / row["evidence"]["barrier"])
    seconds = frozen_seconds(barrier, window)
    require(seconds == ledger["scoring_ready_seconds"], "Historical window differs from audited ledger")
    return {"status": "scored", "valid_scored": True, "task_pass": row["task_pass"],
            "diagnostic_external_pass": row["task_pass"], "deployment_pass": row["deployment_pass"],
            "worker_confirmed": row["worker_confirmed"], "agent_timed_out": row["timeout"],
            "provider_error_count": len(row["provider_errors"]), "agent_success": ledger["agent_success"],
            "false_completion": ledger["agent_success"] is True and row["task_pass"] is False,
            "external_pass_self_not_success": row["task_pass"] is True and ledger["agent_success"] is not True,
            "checks_passed": row["checks_passed"], "checks_count": row["checks_count"],
            "window_seconds": seconds, "evidence_directory": path_key(directory),
            **observed_usage(performance_at(inputs, directory), row["job_id"], ledger.get("observed_usage"), "historical first-attempt ledger")}


def frozen_seconds(barrier, window):
    require(barrier.get("quiescent") is True and barrier.get("simulation_paused") is True
            and barrier.get("inflight") == 0 and bool(barrier.get("lease"))
            and barrier["lease"] == window.get("lease")
            and barrier.get("cutoff_at") == window.get("cutoff_at"), "Invalid world barrier")
    seconds = barrier["frozen_at"] - window["opened_at"]
    require(seconds >= 0, "Invalid frozen window duration")
    return seconds


def current_row(inputs, directory, row):
    task_id = row["task_id"]
    prefix = directory / task_id
    worker_path = Path(str(prefix) + ".agent-state.json")
    worker = inputs.read(worker_path) if worker_path.is_file() else {}
    worker_ok = worker.get("worker_finished") is True and worker.get("busy") is False and worker.get("job_id") == row.get("job_id")
    # Match historical ledger.agent_success: the scorer's saved observation,
    # without substituting a worker result that may arrive after the cutoff.
    self_success = row.get("agent_success")
    valid = row["status"] == "scored"
    external = row.get("passed") if valid else row.get("diagnostic_task_pass")
    seconds = None
    barrier_path, window_path = Path(str(prefix) + ".barrier.json"), Path(str(prefix) + ".window.json")
    if barrier_path.is_file() and window_path.is_file():
        barrier, window = inputs.read(barrier_path), inputs.read(window_path)
        seconds = frozen_seconds(barrier, window)
    if valid:
        require(type(external) is bool and external == row.get("task_pass"), "Scored result is incomplete")
        checks = inputs.read(Path(str(prefix) + ".checks.json"))
        leaves = [leaf for leaf in checks if leaf.get("type") == "leaf"]
        require(len(leaves) == row["expected_checks"], "Scored check count mismatch")
        require([(leaf.get("result"), leaf.get("actual")) for leaf in leaves] ==
                [(leaf.get("result"), leaf.get("actual")) for leaf in row["leaves"]], "Saved leaf scores differ")
        require(seconds is not None, "Scored task is missing a frozen world window")
    provider_errors = row.get("provider_errors") or []
    return {"status": row["status"], "valid_scored": valid, "task_pass": external if valid else None,
            "diagnostic_external_pass": external, "deployment_pass": valid and external is True and worker_ok and not provider_errors,
            "worker_confirmed": worker_ok, "agent_timed_out": row.get("agent_timed_out"),
            "provider_error_count": len(provider_errors), "agent_success": self_success,
            "false_completion": self_success is True and external is False,
            "external_pass_self_not_success": external is True and self_success is not True,
            "checks_passed": sum(leaf.get("result") is True for leaf in row.get("leaves", [])) if valid else None,
            "checks_count": row["expected_checks"], "window_seconds": seconds,
            "evidence_directory": path_key(directory),
            **observed_usage(performance_at(inputs, directory), row.get("job_id"), row.get("observed_token_usage"), "saved result observed_token_usage")}


def method_summary(rows, method, verified):
    items = [row[method] for row in rows]
    scored = [item for item in items if item["valid_scored"]]
    passes = sum(item["task_pass"] is True for item in scored)
    final = verified and len(scored) == len(items)
    numeric = lambda field: [item[field] for item in scored if isinstance(item.get(field), (int, float))]
    durations, tokens, calls = numeric("window_seconds"), numeric("observed_total_tokens"), numeric("calls_with_usage")
    return {"planned_tasks": len(items), "status_counts": dict(Counter(item["status"] for item in items)),
            "valid_scored_tasks": len(scored), "task_passes": passes,
            "final_task_pass_rate": passes / len(items) if final and items else None,
            "observed_scored_pass_fraction": passes / len(scored) if scored else None,
            "deployment_passes": sum(item["deployment_pass"] for item in items),
            "worker_confirmed_tasks": sum(item["worker_confirmed"] for item in items),
            "timed_out_tasks": sum(item["agent_timed_out"] is True for item in items),
            "provider_error_tasks": sum(item["provider_error_count"] > 0 for item in items),
            "agent_self_successes": sum(item["agent_success"] is True for item in items),
            "false_completions": sum(item["false_completion"] for item in items),
            "external_pass_self_not_success": sum(item["external_pass_self_not_success"] for item in items),
            "checks_passed": sum(item["checks_passed"] for item in scored),
            "scored_checks": sum(item["checks_count"] for item in scored),
            "window_seconds": {"observed_tasks": len(durations), "sum": sum(durations) if durations else None,
                               "median": statistics.median(durations) if durations else None},
            "usage": {"observed_tasks": len(tokens), "total_tokens": sum(tokens) if tokens else None,
                      "calls_with_usage": sum(calls) if calls else None,
                      "note": "Scored tasks only; missing observations are not imputed, no monetary cost estimate."}}


def pair_summary(rows):
    return dict(Counter(row["paired_outcome"] for row in rows))


def analyze(attempt, output, *, partial=False):
    attempt, output = Path(attempt).resolve(), Path(output).resolve()
    require(not output.is_relative_to(attempt), "Analysis output cannot be inside the experiment")
    require(not output.is_relative_to((ROOT / "experiments/runs").resolve()),
            "Analysis output cannot be anywhere inside experiments/runs")
    inputs = Inputs()
    manifest, plan = inputs.read(attempt / "manifest.json"), inputs.read(attempt / "plan.json")
    verification = {"completed": False, "status": "not_attempted", "error": None}
    if manifest.get("status") == "completed":
        try:
            verification["runner_summary"] = verify_completed(attempt)
            verification.update(completed=True, status="passed")
        except Exception as exc:
            if not partial:
                raise
            verification.update(status="failed", error=str(exc))
    elif not partial:
        raise ValueError("Run is not completed; use --partial only for explicitly provisional diagnostics")
    require(plan["study"]["agent_commit"] == manifest["agent_commit"] == METHOD_COMMIT, "Unexpected method version")
    require([scene["id"] for scene in plan["sessions"]] == list(SCENES)
            and (plan["expected_tasks"], plan["expected_checks"]) == (60, 320), "Unexpected frozen task set")
    baseline, audit = inputs.read(attempt / "baseline.json"), inputs.read(attempt / "baseline-audit.json")
    evidence_directories = {(ROOT / path).resolve().parent for row in baseline["rows"] for path in row["evidence"].values()}
    if baseline["baseline_source"].get("attempt"):
        evidence_directories.add((ROOT / baseline["baseline_source"]["attempt"]).resolve())
    require(not any(output.is_relative_to(directory) for directory in evidence_directories),
            "Analysis output cannot be inside historical baseline evidence")
    for name, expected in plan["study"]["baseline_inputs"].items():
        require(inputs.hashes[path_key(attempt / name)] == expected, "Frozen historical input changed")
    require(audit["readiness"]["ready"] and audit["baseline_sha256"] == inputs.hashes[path_key(attempt / "baseline.json")],
            "Historical B0 reference is not audited")
    source = baseline["baseline_source"]
    require(baseline["status"] == "frozen" and source["agent_commit"] == "1794e45e421fb5de03094f0b63f9ca95f86ab42f"
            and source["cohort"] == "first_attempt_no_replay" and source["generation"] == 1
            and source["model"] == "deepseek-flash" and source["provider"] == "deepseek_official"
            and sum(row["task_pass"] is True for row in baseline["rows"]) == 47,
            "Historical comparison is not the frozen B0 first-attempt reference")
    ledger_rows = inputs.read(ROOT / baseline["baseline_source"]["ledger"])
    ledger = {(row["session_id"], row["task_id"]): row for row in ledger_rows
              if row["model"] == "deepseek" and row["cohort"] == "first_attempt_no_replay"}
    current = inputs.read(attempt / "results.json")
    expected_keys = [(scene["id"], task["id"]) for scene in plan["sessions"] for task in scene["tasks"]]
    require([(row["scene_id"], row["task_id"]) for row in baseline["rows"]] == expected_keys, "B0 task order mismatch")
    require([(row["session_id"], row["task_id"]) for row in current] == expected_keys, "B1 task order mismatch")
    rows = []
    pair_names = {(True, True): "both_pass", (False, False): "both_fail", (False, True): "new_pass", (True, False): "regression"}
    for old, new in zip(baseline["rows"], current):
        key = old["scene_id"], old["task_id"]
        record = manifest["scenes"][key[0]]
        require(record.get("generation", 0) in (0, 1), "Replay generation is not allowed")
        directory = attempt / (record.get("directory") or key[0])
        require(directory.resolve().is_relative_to(attempt), "Unsafe evidence directory")
        row = {name: old[name] for name in ("scene_id", "scene_name", "family", "task_index", "task_id", "task_name", "category", "template", "difficulty")}
        row["b0"] = historical_row(inputs, old, ledger[key])
        row["b1"] = current_row(inputs, directory, new)
        row["paired_outcome"] = pair_names[row["b0"]["task_pass"], row["b1"]["task_pass"]] if row["b1"]["valid_scored"] else "unscored_or_invalid"
        rows.append(row)
    require(len(rows) == 60, "Fixed denominator changed")
    final = verification["completed"]
    require(not final or all(row["b1"]["valid_scored"] for row in rows), "Completed run lacks all 60 valid scores")
    summary = {"schema": 1, "attempt": path_key(attempt), "mode": "verified_final" if final else "provisional_diagnostics",
               "is_final": final, "manifest_status": manifest["status"], "agent_commit": METHOD_COMMIT,
               "verification": verification, "planned_tasks_per_method": 60,
               "methods": {"b0": method_summary(rows, "b0", True), "b1": method_summary(rows, "b1", final)},
               "paired_outcomes": pair_summary(rows), "baseline_source": baseline["baseline_source"],
               "limitations": [*plan["study"]["comparison_limitations"], PACKAGE_LIMITATION],
               "measurement_notes": ["Primary task score is the original external checker, not agent_success.",
                   "Both methods use agent_success at scoring time (B0 first-attempt ledger; B1 saved results row), never a later worker result.",
                   "Unscored/invalid tasks remain listed in the fixed 60 denominator; no final rate is issued for incomplete/unverified runs.",
                   "Window duration is barrier.frozen_at minus window.opened_at; it excludes preparation and later worker-exit wait.",
                   "Observed tokens use scene performance by_job when any numeric token field exists; otherwise saved observed usage. Partial observations keep missing fields null without mixing sources or inferring totals; no costs are inferred."]}
    for group in ("scene_id", "category", "family", "difficulty"):
        grouped = defaultdict(list)
        for row in rows:
            grouped[row[group]].append(row)
        summary["by_" + group] = {name: {"b0": method_summary(items, "b0", True), "b1": method_summary(items, "b1", final),
                                         "paired_outcomes": pair_summary(items)} for name, items in grouped.items()}
    common = [row for row in rows if row["b1"]["valid_scored"]]
    summary["common_scored_comparison"] = {"tasks": len(common), "b0": method_summary(common, "b0", True),
                                            "b1": method_summary(common, "b1", final), "paired_outcomes": pair_summary(common)}
    summary["case_candidates"] = [{"scene_id": row["scene_id"], "task_id": row["task_id"], "task_name": row["task_name"],
                                    "category": row["category"], "paired_outcome": row["paired_outcome"],
                                    "b0_evidence_directory": row["b0"]["evidence_directory"],
                                    "b1_evidence_directory": row["b1"]["evidence_directory"],
                                    "causal_diagnosis": "Not inferred; inspect both first-attempt traces and failed checks."}
                                   for row in rows if row["paired_outcome"] in ("new_pass", "regression")]
    # Historical values consumed here must still agree with the frozen B0 audit.
    for name, expected in audit.get("input_evidence_sha256", {}).items():
        if name in inputs.hashes:
            require(inputs.hashes[name] == expected, f"Audited B0 input changed: {name}")
    if final:
        seal = inputs.read(attempt / "suite-seal.json")
        for name, expected in seal["files"].items():
            path = attempt / name
            require(path.resolve().is_relative_to(attempt), "Unsafe sealed evidence path")
            require(sha(path.read_bytes()) == expected, "Sealed evidence changed during analysis")
            inputs.hashes[path_key(path)] = expected
    inputs.hashes[path_key(Path(__file__))] = sha(Path(__file__).read_bytes())
    inputs.recheck()
    flat = []
    for row in rows:
        item = {key: value for key, value in row.items() if key not in ("b0", "b1")}
        for method in ("b0", "b1"):
            item.update({method + "_" + key: value for key, value in row[method].items()})
        flat.append(item)
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    with (output / "paired-tasks.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(flat[0]))
        writer.writeheader()
        writer.writerows(flat)
    (output / "input-sha256.json").write_text(json.dumps({"algorithm": "sha256", "paths_relative_to": "repository root unless absolute",
                                                        "files": dict(sorted(inputs.hashes.items()))}, indent=2) + "\n")
    b0, b1 = summary["methods"]["b0"], summary["methods"]["b1"]
    report = ["# B1 定向补救与历史 B0：60题对照", "",
              "**已完成并通过冻结验证。**" if final else "**仅未完成/未核验诊断，不是最终成功率。**", "",
              f"B0历史外判通过 {b0['task_passes']}/60；本轮已有效计分 {b1['valid_scored_tasks']}/60，其中通过 {b1['task_passes']}。", ""]
    if final:
        delta = 100 * (b1["final_task_pass_rate"] - b0["final_task_pass_rate"])
        report += [f"最终任务通过率：B0 {b0['final_task_pass_rate']:.2%}，B1 {b1['final_task_pass_rate']:.2%}，差值 {delta:+.2f} 个百分点。", ""]
    report += [f"配对结果：{json.dumps(summary['paired_outcomes'], ensure_ascii=False)}。", "",
               "两侧自判均采用评分时快照：B0首次账本、B1保存的results行；不以稍后worker退出结果替换。", "",
               "| 方法 | 部署通过 | 超时 | 服务错误任务 | 评分时Agent自判成功 | 虚假完成 | 外判过、自判未成功 |",
               "|---|---:|---:|---:|---:|---:|---:|"]
    for name, values in (("B0历史", b0), ("B1本轮", b1)):
        report.append(f"| {name} | {values['deployment_passes']} | {values['timed_out_tasks']} | {values['provider_error_tasks']} | {values['agent_self_successes']} | {values['false_completions']} | {values['external_pass_self_not_success']} |")
    for group, title in (("scene_id", "场景"), ("category", "任务类别（冻结 B0 分类）")):
        report += ["", f"## {title}", "", "| 分组 | B0通过/计划 | B1通过/已计分 | 计划题数 | 新增成功 | 回归 |", "|---|---:|---:|---:|---:|---:|"]
        for name, values in summary["by_" + group].items():
            old, new, pairs = values["b0"], values["b1"], values["paired_outcomes"]
            report.append(f"| {name} | {old['task_passes']}/{old['planned_tasks']} | {new['task_passes']}/{new['valid_scored_tasks']} | {new['planned_tasks']} | {pairs.get('new_pass',0)} | {pairs.get('regression',0)} |")
    report += ["", "## 耗时与可观察用量", "", "仅比较两侧共同已计分任务；缺失值不补零，不估算金额。"]
    for method in ("b0", "b1"):
        values = summary["common_scored_comparison"][method]
        report.append(f"- {method.upper()}：窗口中位秒数 {values['window_seconds']['median']}；可观察 tokens {values['usage']['total_tokens']}（{values['usage']['observed_tasks']}题有记录）。")
    report += ["", "## 案例与解释边界", "", "新增成功与回归逐题见 summary.json 的 case_candidates、paired-tasks.csv。这里只产生需要读轨迹的候选，不从结果变化自动编造机制原因。", ""]
    report += ["- " + limitation for limitation in summary["limitations"]]
    report += ["", "原始外部检查是主指标；agent_success、部署通过和超时分别记录。B0使用历史同模型ID，日期与并发条件不同，不是同期随机实验。三场连续任务存在状态和记忆承接，不能把60题当独立同分布样本或直接作为架构创新证明。", ""]
    if verification["error"]:
        report += ["验证未通过：" + verification["error"], ""]
    (output / "REPORT_CN.md").write_text("\n".join(report))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=Path, default=DEFAULT_ATTEMPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--partial", action="store_true", help="Write clearly provisional diagnostics; never bypass baseline/row consistency checks")
    args = parser.parse_args()
    summary = analyze(args.attempt, args.output, partial=args.partial)
    print(json.dumps({"mode": summary["mode"], "b1_scored": summary["methods"]["b1"]["valid_scored_tasks"],
                      "b1_passed": summary["methods"]["b1"]["task_passes"], "paired_outcomes": summary["paired_outcomes"]}))


if __name__ == "__main__":
    main()
