#!/usr/bin/env python3
"""Offline first-attempt B0 cohort audit; writes only the four reassessment files.

Run with: PYTHONDONTWRITEBYTECODE=1 python3 experiments/analysis/reassess_b0_failures.py
Uses saved checker responses and raw worker/telemetry records. Does not run a
model, simulator, GUI, network request or modify original evidence. Old mechanism
labels are provenance-matched annotations, not newly proved causal diagnoses.
"""
from __future__ import annotations

import collections
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "docs/results/baseline-deployment-20261005"
OLD = ROOT / "docs/results/failure-analysis-20261005"
OUT = ROOT / "docs/results/b0-failure-reassessment-20261006"
INPUTS = {}
EXPECTED = {}
COUNTS = collections.Counter()


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    path = Path(path)
    raw = path.read_bytes()
    key = str(path.relative_to(ROOT))
    checksum = digest(raw)
    if key in EXPECTED:
        require(checksum == EXPECTED[key], f"Saved input checksum changed: {key}")
    if key in INPUTS:
        require(checksum == INPUTS[key], f"Input changed during audit: {key}")
    INPUTS[key] = checksum
    return json.loads(raw)


def register_manifest(root):
    manifest = read(root / "SHA256SUMS.json")
    for filename, checksum in manifest.items():
        EXPECTED[str((root / filename).relative_to(ROOT))] = checksum
    hashes = read(root / "evidence-sha256.json")
    for filename, checksum in hashes.items():
        # Snapshots can have different mutable manifests; immutable consumed task
        # files are checked against the baseline closure's later manifest first.
        EXPECTED.setdefault(filename, checksum)


def classify(row):
    if row["deployment_pass"]:
        return "deployment_pass"
    if row["provider_errors"]:
        return "terminal_provider_error"
    if row["original_status"] == "not_run":
        return "not_run_after_execution_stop"
    if row["original_status"] != "scored":
        return "unscored_execution_interruption"
    if not row["worker_confirmed"]:
        return "scored_without_worker_confirmation"
    require(row["original_passed"] is False, "Unexplained deployment failure")
    return "observed_task_failure_timeout" if row["agent_timed_out"] else "observed_task_failure_non_timeout"


def eligible(row):
    return (row["original_status"] == "scored" and row["original_passed"] is False
            and row["worker_confirmed"] and not row["provider_errors"])


def read_telemetry(directory):
    path = directory / "agent-traces/reproduction-metrics.jsonl"
    data = path.read_bytes()
    key = str(path.relative_to(ROOT))
    checksum = digest(data)
    require(key in EXPECTED and checksum == EXPECTED[key], f"Telemetry checksum changed: {key}")
    INPUTS[key] = checksum
    errors = collections.defaultdict(list)
    for line in data.splitlines():
        event = json.loads(line)
        if event.get("event") == "llm_error" and isinstance(event.get("status_code"), int) and event["status_code"] >= 400:
            errors[event.get("job_id")].append({key: event.get(key) for key in ("timestamp", "error_type", "status_code", "run_id")})
    return errors


def verify_scene(rows):
    directory = ROOT / rows[0]["evidence_dir"]
    raw = read(directory / "results.json")
    state = read(directory / "scene.json")
    errors = read_telemetry(directory)
    require(len(raw) == len(rows) == 20, "Scene task count changed")
    by_id = {row["task_id"]: row for row in raw}
    for row in rows:
        original = by_id[row["task_id"]]
        for field, source in (("original_status", "status"), ("original_passed", "passed"),
                              ("agent_timed_out", "agent_timed_out"), ("agent_success", "agent_success"),
                              ("job_id", "job_id"), ("expected_checks", "expected_checks")):
            require(row[field] == original.get(source), f"Raw result field mismatch: {row['task_id']}/{field}")
        require(row["original_leaves"] == original["leaves"], "Raw leaf definitions/outcomes changed")
        require(row["provider_errors"] == errors.get(row["job_id"], []), "Provider-error accounting mismatch")
        require(row["worker_confirmed"] == (original.get("continuation_safe") is True), "Worker-accounting mismatch")
        counted = row["original_status"] == "scored" and not row["provider_errors"]
        require(row["task_pass"] == (bool(row["original_passed"]) if counted else False), "Task-accounting mismatch")
        require(row["deployment_pass"] == (row["task_pass"] and row["worker_confirmed"]), "Deployment-accounting mismatch")
        COUNTS["raw_results_checked"] += 1
        if row["original_status"] != "scored":
            require(row["original_passed"] is None and not row["worker_confirmed"], "Unscored evidence contradiction")
            require(row["failure_origin"] == state.get("error") and row["failure_origin"], "Missing stop provenance")
            continue
        prefix = directory / row["task_id"]
        checks = read(Path(str(prefix) + ".checks.json"))
        leaves = [leaf for leaf in checks if leaf.get("type") == "leaf"]
        require(len(leaves) == row["expected_checks"], "Saved checker count mismatch")
        for leaf, expected in zip(leaves, original["leaves"]):
            definition = expected["definition"]
            require(leaf["endpoint"] == (definition.get("endpoint") or definition.get("path"))
                    and leaf["parameters"] == definition.get("parameters", {}), "Checker request mismatch")
            require(leaf.get("actual") == expected.get("actual") and leaf["result"] == expected["result"], "Checker value mismatch")
            require(leaf["response"]["result"] == leaf["actual"], "Original response/actual mismatch")
            require(leaf["result"] == (leaf["actual"] == definition.get("expect", True)), "Leaf expected-value mismatch")
        barrier = read(Path(str(prefix) + ".barrier.json"))
        window = read(Path(str(prefix) + ".window.json"))
        require(barrier["quiescent"] is True and barrier["inflight"] == 0 and barrier["simulation_paused"] is True,
                "Scored world was not frozen")
        require(barrier["lease"] == window["lease"] and barrier["cutoff_at"] == window["cutoff_at"], "Lease mismatch")
        require(row["scoring_ready_seconds"] == barrier["frozen_at"] - window["opened_at"], "Timing mismatch")
        if row["worker_confirmed"]:
            worker = read(Path(str(prefix) + ".agent-state.json"))
            require(worker["worker_finished"] is True and worker["busy"] is False and worker["job_id"] == row["job_id"],
                    "Worker exit was not confirmed")
            COUNTS["confirmed_worker_records_checked"] += 1
        else:
            require("Worker did not exit" in state.get("error", ""), "Unconfirmed worker lacks stop evidence")
            COUNTS["scored_without_worker_confirmation"] += 1
        if row["agent_timed_out"]:
            read(Path(str(prefix) + ".timeout.json"))
        COUNTS["saved_checker_and_barrier_records_checked"] += 1


def verify_old_diagnosis(item, first):
    path = ROOT / item["trace_path"]
    trace = read(path)
    directory = path.parent.parent.parent
    job = read(directory / f"{item['task_id']}.job.json")
    require(trace["command"] == job["command"], "Old trace command does not match job")
    delta = abs((dt.datetime.fromisoformat(trace["started_at"]) - dt.datetime.fromisoformat(job["created_at"])).total_seconds())
    require(delta < 5 and abs(delta - item["trace_time_match_seconds"]) < .00001, "Old trace timestamp match changed")
    same = directory == ROOT / first["evidence_dir"]
    if same:
        require(job["job_id"] == first["job_id"] and eligible(first), "Same-generation diagnosis has wrong job/cohort")
    return {"model": item["model"], "session_id": item["session_id"], "task_id": item["task_id"],
            "legacy_bucket": item["bucket"], "trace_path": item["trace_path"],
            "trace_job_id": job["job_id"], "trace_start_delta_seconds": delta,
            "first_attempt_job_id": first["job_id"], "first_attempt_evidence_dir": first["evidence_dir"],
            "first_attempt_category": classify(first),
            "match": "same_first_attempt" if same else "different_execution_generation_not_applicable"}


def stats(rows, matches):
    failed = [row for row in rows if not row["deployment_pass"]]
    candidates = [row for row in rows if eligible(row)]
    old_covered = lambda row: (row["model"], row["session_id"], row["task_id"]) in matches
    cross = collections.Counter((row["original_status"], row["original_passed"], row["worker_confirmed"],
                                 bool(row["provider_errors"]), row["agent_timed_out"]) for row in failed)
    return {
        "planned_tasks": len(rows), "planned_checks": sum(row["expected_checks"] for row in rows),
        "deployment_passes": len(rows) - len(failed), "deployment_failures": len(failed),
        "original_status_counts": dict(collections.Counter(row["original_status"] for row in rows)),
        "exclusive_outcome_categories": dict(collections.Counter(classify(row) for row in rows)),
        "raw_checker_passes": sum(row["original_passed"] is True for row in rows),
        "raw_checker_failures": sum(row["original_passed"] is False for row in rows),
        "terminal_provider_error_tasks": sum(bool(row["provider_errors"]) for row in rows),
        "provider_status_counts": dict(collections.Counter(str(error["status_code"]) for row in rows for error in row["provider_errors"])),
        "unscored_tasks": sum(row["original_status"] != "scored" for row in rows),
        "worker_unconfirmed_including_unexecuted": sum(not row["worker_confirmed"] for row in rows),
        "scored_worker_unconfirmed": sum(row["original_status"] == "scored" and not row["worker_confirmed"] for row in rows),
        "timed_out_tasks": sum(row["agent_timed_out"] is True for row in rows),
        "timed_out_raw_checker_passes": sum(row["agent_timed_out"] is True and row["original_passed"] is True for row in rows),
        "eligible_observed_task_failures": len(candidates),
        "eligible_agent_self_success_external_failure": sum(row["agent_success"] is True for row in candidates),
        "eligible_with_matching_old_diagnosis": sum(old_covered(row) for row in candidates),
        "eligible_without_matching_old_diagnosis": sum(not old_covered(row) for row in candidates),
        "deployment_failures_without_matching_old_diagnosis": sum(not old_covered(row) for row in failed),
        "failure_cross_counts": [{"original_status": key[0], "original_passed": key[1], "worker_confirmed": key[2],
                                  "has_provider_error": key[3], "agent_timed_out": key[4], "count": count}
                                 for key, count in sorted(cross.items(), key=lambda pair: str(pair[0]))],
        "uncovered_eligible_tasks": [{"session_id": row["session_id"], "task_id": row["task_id"],
                                       "evidence_dir": row["evidence_dir"], "category": classify(row)}
                                      for row in candidates if not old_covered(row)],
    }


def main():
    register_manifest(BASE)
    register_manifest(OLD)
    read(BASE / "verification.json")
    previous_summary = read(OLD / "summary.json")
    allrows = read(BASE / "tasks.json")
    rows = [row for row in allrows if row["cohort"] == "first_attempt_no_replay"]
    require(len(rows) == 3000 and all(row["generation"] == 1 for row in rows), "Wrong cohort/generation")
    indexed = {(row["model"], row["session_id"], row["task_id"]): row for row in rows}
    require(len(indexed) == 3000, "Duplicate task keys")
    scenes = collections.defaultdict(list)
    for row in rows:
        scenes[row["model"], row["session_id"]].append(row)
    require(len(scenes) == 150, "Wrong scene count")
    for group in scenes.values():
        verify_scene(group)
    legacy = read(OLD / "mechanisms.json")
    require(len({(item["model"], item["session_id"], item["task_id"]) for item in legacy}) == len(legacy), "Duplicate old diagnoses")
    reconciled = [verify_old_diagnosis(item, indexed[item["model"], item["session_id"], item["task_id"]]) for item in legacy]
    matches = {(item["model"], item["session_id"], item["task_id"]): item for item in reconciled if item["match"] == "same_first_attempt"}
    old_by_key = {(item["model"], item["session_id"], item["task_id"]): item for item in reconciled}
    models = {model: stats([row for row in rows if row["model"] == model], matches) for model in ("deepseek", "qwen")}
    require(all(value["planned_tasks"] == 1500 and value["planned_checks"] == 9396 for value in models.values()), "Wrong per-model denominator")
    excluded = [item for item in reconciled if item["match"] != "same_first_attempt"]
    summary = {
        "schema_version": 1, "cohort": "first_attempt_no_replay", "source": str((BASE / "tasks.json").relative_to(ROOT)),
        "primary_metric": "first-attempt deployment: saved task check pass AND worker confirmed AND no terminal provider error",
        "interpretation": "An observed task failure eligible for behavioral analysis is not yet a diagnosed planning failure. Timeout is a measured outcome, not a root cause.",
        "missing_policy": "Unexecuted and unscored tasks remain deployment failures in the fixed 1500 denominator, but are not treated as observed planning/execution failures.",
        "verification_scope": "All 3000 raw result rows, saved checker leaf values/responses and world barriers for 2927 scored tasks, confirmed-worker records, per-job terminal HTTP error events, and consumed input hashes. No model or simulator rerun; original check-tree evaluator was not re-executed in this audit (its earlier verification.json is an input).",
        "models": models,
        "legacy_diagnosis_reconciliation": {
            "previous_snapshot_captured_at": previous_summary["captured_at"],
            "previous_scope": previous_summary["scope"],
            "previous_cohort_task_counts": {key: value["tasks"] for key, value in previous_summary["cohorts"].items()},
            "old_records": len(legacy), "same_first_attempt_records": len(matches),
            "excluded_other_generation_records": len(excluded),
            "same_first_attempt_by_model": dict(collections.Counter(item["model"] for item in matches.values())),
            "excluded_other_generation_by_model": dict(collections.Counter(item["model"] for item in excluded)),
            "note": "Matched legacy bucket labels retain their old interpretation; this audit only proves trace/job/generation identity, not causal correctness. The old 429 records are not this cohort's mechanism-frequency denominator.",
            "excluded_other_generation": excluded,
        },
        "verification_counts": dict(COUNTS),
    }
    ledger = []
    for row in rows:
        if row["deployment_pass"]:
            continue
        key = row["model"], row["session_id"], row["task_id"]
        old = old_by_key.get(key)
        entry = {name: row[name] for name in ("model", "cohort", "session_id", "scene_name", "family", "scene_difficulty",
                 "generation", "task_index", "task_id", "name", "task_difficulty", "original_status", "original_passed",
                 "task_pass", "deployment_pass", "worker_confirmed", "agent_timed_out", "agent_success", "accounting_reason",
                 "failure_origin", "job_id", "evidence_dir")}
        entry.update({"failure_category": classify(row), "eligible_observed_task_failure": eligible(row),
                      "provider_errors": json.dumps(row["provider_errors"], ensure_ascii=False),
                      "legacy_diagnosis_status": old["match"] if old else "no_old_diagnosis",
                      "matched_legacy_bucket": old["legacy_bucket"] if old and key in matches else "",
                      "legacy_trace_path": old["trace_path"] if old else "",
                      "old_diagnosis_covers_first_attempt": key in matches})
        ledger.append(entry)
    source = Path(__file__)
    INPUTS[str(source.relative_to(ROOT))] = digest(source.read_bytes())
    # Recheck every consumed file after assembling results, without changing raw evidence.
    for name, checksum in INPUTS.items():
        require(digest((ROOT / name).read_bytes()) == checksum, f"Input changed during audit: {name}")
    summary["verification_counts"]["consumed_files_rehashed"] = len(INPUTS)
    summary["verification_counts"]["consumed_inputs_matched_prior_hash_manifest"] = sum(name in EXPECTED for name in INPUTS)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "cohort-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    with (OUT / "failure-ledger.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(ledger[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(ledger)
    (OUT / "input-sha256.json").write_text(json.dumps({"algorithm": "sha256", "paths_relative_to": "repository root", "files": dict(sorted(INPUTS.items()))}, indent=2) + "\n")
    lines = ["# B0 首次执行失败：分母与证据边界复核", "",
             "本报告只分析原版 B0 的首次执行，不采用恢复重跑成绩，不运行模型或仿真，也不修改运行架构。", "",
             "输入固定为 baseline-deployment-20261005/tasks.json 中 cohort=first_attempt_no_replay：DeepSeek Flash 与 Qwen 各 75 场、1500 题、9396 个检查点；全部 generation=1。", "",
             "| 互斥结果分类 | DeepSeek | Qwen |", "|---|---:|---:|"]
    labels = [("deployment_pass", "首次部署成功"), ("observed_task_failure_non_timeout", "可观察任务失败，非超时"),
              ("observed_task_failure_timeout", "可观察任务失败，超时"), ("terminal_provider_error", "终止服务错误"),
              ("scored_without_worker_confirmation", "已计分但 worker 退出未确认"),
              ("unscored_execution_interruption", "执行中断且未计分"), ("not_run_after_execution_stop", "停止后未执行")]
    for key, label in labels:
        lines.append(f"| {label} | {models['deepseek']['exclusive_outcome_categories'].get(key, 0)} | {models['qwen']['exclusive_outcome_categories'].get(key, 0)} |")
    lines += ["", "部署失败仍计入固定 1500 题分母；但是未执行、未计分、服务错误和退出未确认不能全部解释为 Planner 失败。", "",
              "DeepSeek 部署失败 257 题中，203 题属于外判失败、worker 已确认且无终止服务错误（169 非超时、34 超时）。37 题服务错误中 25 题外判失败、12 题外判通过；状态码为 36 次 402、1 次 429。另有 1 题未计分中断和 16 题未执行。", "",
              "Qwen 部署失败 350 题中，281 题满足同一可观察失败条件（89 非超时、192 超时）。另有 13 题已冻结计分但 worker 退出未确认（12 题外判失败、1 题外判通过，全部超时），以及 56 题未执行。没有已记录终止服务错误。", "",
              "超时与失败不是同一集合：DeepSeek 共 35 题超时，其中 1 题外判通过；Qwen 共 220 题超时，其中 16 题外判通过。203/281 是后续行为分析的候选集合，不是已经证明由规划导致的失败数。", "",
              "## 旧机制诊断可复用到哪里", "",
              "旧报告是中期快照：DeepSeek 为选择恢复后的 1500 题，Qwen 为 1280 题，不能把原来的 429 条机制记录直接当作首次执行的全量机制统计。", "",
              "逐条检查旧 trace 的文件哈希、所在 evidence generation、command、开始时间与 job 的对应关系：DeepSeek 217 条旧记录中仅 190 条属于相同首次执行，27 条来自重跑；Qwen 212 条中仅 200 条相同，12 条来自重跑。不同代际的 39 条不得用于解释本次首次执行结果，即使 task_id 相同。", "",
              "同代匹配共 390 条，全部属于可观察任务失败。仍有 DeepSeek 13 题、Qwen 81 题可观察失败没有同代旧诊断；按全部部署失败口径则分别有 67、150 题没有覆盖。旧 bucket 仅保留为旧标签，本次没有重新证明其因果解释。", "",
              "未覆盖任务逐条见 cohort-summary.json 的 uncovered_eligible_tasks；39 条不适用的旧重跑记录见 legacy_diagnosis_reconciliation.excluded_other_generation。failure-ledger.csv 保留全部 607 条首次部署失败及每条诊断覆盖状态，没有丢弃未执行项。", "",
              "## 核验范围与复现", "",
              f"核对 3000 条原始 results、2927 条已计分任务的 checks 响应/叶节点值及 barrier/window、2914 条 worker 退出确认、全部首次场景终止服务事件；消费的 {len(INPUTS)} 个文件在生成前再次计算 SHA-256，其中 {sum(name in EXPECTED for name in INPUTS)} 个与既有归档哈希匹配。", "",
              "本轮未重跑模型、未重放仿真，也未重新执行完整原 checker 检查树；原 checker 回放由既有 baseline-deployment/verification.json 记录，本轮核对其消费证据、原响应和计分字段。输入清单见 input-sha256.json。", "",
              "```bash", "PYTHONDONTWRITEBYTECODE=1 python3 experiments/analysis/reassess_b0_failures.py", "```", ""]
    (OUT / "COHORT_AUDIT_CN.md").write_text("\n".join(lines))
    print(json.dumps({"models": {model: {key: value[key] for key in ("planned_tasks", "deployment_failures", "eligible_observed_task_failures", "eligible_with_matching_old_diagnosis", "eligible_without_matching_old_diagnosis")} for model, value in models.items()}, "ledger_rows": len(ledger), "matched_old": len(matches), "excluded_old_replays": len(excluded), "consumed_files": len(INPUTS)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
