"""Recompute paired results from a campaign's journal and independently sealed artifacts."""
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from prepare_task_dataset import parse_json, safe_path
from .campaign import PAIRED_PROTOCOL, ROOT, g3_evidence, verify_run
from .campaign_storage import SlotLedger, digest, json_bytes, locked, read_jsonl
from .paired_scores import CELLS, PRIMARY, score_pairs
from .paired_simulation import G3_FILE, STATUS_CN, new_output
from .worker import _metrics


def _read(root, name):
    return parse_json(safe_path(root, name).read_bytes())


def _inventory(run, folder, name, checksum, required):
    data = safe_path(run, name).read_bytes()
    if digest(data) != checksum:
        raise ValueError(f"Changed exit seal: {name}")
    inventory = parse_json(data)
    if not isinstance(inventory, dict) or not required.issubset(inventory):
        raise ValueError(f"Incomplete exit seal: {name}")
    for relative, expected in inventory.items():
        if digest(safe_path(folder, relative).read_bytes()) != expected:
            raise ValueError(f"Changed sealed file: {folder.name}/{relative}")
    return inventory


def _check_worker(run, slot, result, backend):
    sid = slot["slot_id"]
    folder = safe_path(run, f"trials/{sid}")
    inventory = _inventory(run, folder, f"worker-exit-seals/{sid}.json", result.get("worker_seal_sha256"),
                           {"worker-result.json", "private/score-world.pkl"})
    worker = _read(folder, "worker-result.json")
    limits = worker["limits"]
    if (worker["status"] != "completed" or (worker["trial_id"], worker["task_id"]) != (sid, slot["task_id"])
            or type(worker["pid"]) is not int or worker["pid"] != result.get("worker_pid")
            or not worker["drained"] or not limits["closed"] or limits["needs_process_termination"]
            or limits["threads_still_alive"] or worker["network_attempts"] != [] or not all(worker["isolation"].values())
            or worker["snapshot"]["path"] != "private/score-world.pkl"
            or worker["snapshot"]["sha256"] != inventory["private/score-world.pkl"]):
        raise ValueError(f"Invalid worker or scoring snapshot: {sid}")
    fixture = _read(run, f"study/fixtures/{slot['task_id']}.json")
    initial = worker["initial"]
    if (initial.get("original_state_sha256") != fixture["state_sha256"]
            or initial.get("public_startup_sha256") != fixture["public_startup_sha256"]):
        raise ValueError(f"Worker initial state differs from frozen task: {sid}")
    audit_name = "private/student-requests.jsonl"
    attempts = []
    if (folder / audit_name).exists():
        if audit_name not in inventory:
            raise ValueError(f"Student requests are missing from the worker seal: {sid}")
        attempts = read_jsonl(folder / audit_name)
        for index, attempt in enumerate(attempts, 1):
            turn = attempt.get("turn")
            if (type(turn) is not int or turn != index or not 1 <= turn <= len(slot["turn_seeds"])
                    or attempt.get("slot") != sid or attempt.get("seed") != slot["turn_seeds"][turn - 1]
                    or ("thinking" in attempt and attempt["thinking"] is not slot["thinking"])):
                raise ValueError(f"Student request differs from its assigned slot: {sid}")
    usage = _metrics(folder, worker, backend)
    recorded = result.get("usage")
    if not isinstance(recorded, dict) or any(recorded.get(k) != value for k, value in usage.items()):
        raise ValueError(f"Recorded usage differs from the sealed requests: {sid}")
    if result.get("raw_stop_reason", result.get("stop_reason")) != limits["stop_reason"]:
        raise ValueError(f"Stop reason differs from the worker: {sid}")
    return folder, worker, inventory, attempts


def _check_score(run, slot, result, folder, worker, inventory):
    sid = slot["slot_id"]
    seal_name = f"scorer-exit-seals/{sid}.json"
    score_files = _inventory(run, folder, seal_name, result.get("scorer_seal_sha256"),
                             {"evaluation/result.json", "evaluation/private-task.json"})
    if any(not name.startswith("evaluation/") for name in score_files) or set(inventory) & set(score_files):
        raise ValueError(f"Worker and scorer files overlap: {sid}")
    score = _read(folder, "evaluation/result.json")
    task = _read(folder, "evaluation/private-task.json")
    original = {k: v for k, v in task["execution_check_apis"].items() if k != "_episode_private_canary"}
    if (task.get("id") != slot["task_id"] or digest(json_bytes(original)) != slot["checker_sha256"]
            or score["trial_id"] != sid or type(score["pid"]) is not int or score["pid"] != result.get("scorer_pid")
            or score["pid"] == worker["pid"] or score["worker_pid"] != worker["pid"]
            or score["worker_result_sha256"] != inventory["worker-result.json"]
            or score["snapshot_sha256"] != worker["snapshot"]["sha256"] or score["private_canary_present"] is not True
            or score["checker_tree_matches_original"] is not True or score["agent_imported"] is not False
            or score["network_attempts"] != [] or score["checker"]["errors"] != []
            or type(score["checker"]["passed"]) is not bool or score["checker"]["passed"] is not result.get("passed")):
        raise ValueError(f"Independent checker disagrees with the recorded result: {sid}")
    failure = result.get("failure_evidence")
    if failure is not None:
        if (result["passed"] or not isinstance(failure, dict) or failure.get("kind") not in {"decision_error", "tool_format"}
                or not isinstance(failure.get("reference"), str) or not str(failure.get("detail", "")).strip()
                or failure["reference"].split("#", 1)[0] not in (inventory | score_files)):
            raise ValueError(f"Task attribution lacks a slot-specific evidence reference: {sid}")
    return score_files["evaluation/result.json"]


def _terminal(run, row, execution):
    sid = row["slot_id"]
    terminal = safe_path(run, f"terminal-results/{sid}.json")
    if row["status"] in {"unstarted", "started"}:
        if terminal.exists() or row.get("result") is not None:
            raise ValueError(f"Unfinished journal with a terminal receipt; finish recovery first: {sid}")
        return None
    raw = terminal.read_bytes()
    result = parse_json(raw)
    if (not isinstance(result, dict) or json_bytes(result) != json_bytes(row.get("result"))
            or result.get("status") != row["status"] or result.get("trial_id") != sid
            or result.get("task_id") != row["task_id"] or result.get("evidence_kind") != execution["scope"]
            or "synthetic_checker" in result):
        raise ValueError(f"Receipt identity, evidence kind or payload disagrees with journal: {sid}")
    metadata = result.get("student_metadata")
    if metadata is not None or row["status"] == "scored":
        formal = execution["scope"] == "formal"
        if (not isinstance(metadata, dict) or metadata.get("model") != row["model"]
                or metadata.get("backend") != execution["backend"] or metadata.get("weights_loaded") is not formal):
            raise ValueError(f"Model, backend or loaded-weight identity is inconsistent: {sid}")
        if formal:
            from .paired_backend import manifest_path

            manifest = _read(run / "source", manifest_path(row["model"]))
            if (metadata.get("revision") != manifest["revision"]
                    or metadata.get("model_manifest_sha256") != digest(json_bytes(manifest).rstrip(b"\n"))):
                raise ValueError(f"Student checkpoint differs from the frozen model: {sid}")
    evidence = {"terminal_sha256": digest(raw), "terminal": terminal.relative_to(run).as_posix()}
    if row["status"] == "scored" or result.get("worker_seal_sha256"):
        folder, worker, inventory, attempts = _check_worker(run, row, result, execution["backend"])
        evidence["worker_seal_sha256"] = result["worker_seal_sha256"]
        if row["status"] == "scored":
            from .paired_worker import _diagnostics

            verified = _diagnostics(worker, attempts, result["passed"])
            if (any(result.get(key) != value for key, value in verified.items())
                    or result.get("failure_evidence") != verified.get("failure_evidence")):
                raise ValueError(f"Recorded failure or stop attribution differs from its sealed evidence: {sid}")
            evidence["checker_result_sha256"] = _check_score(run, row, result, folder, worker, inventory)
            evidence["checker_result"] = f"trials/{sid}/evaluation/result.json"
            evidence["scorer_seal_sha256"] = result["scorer_seal_sha256"]
            agent_file = "policy/agent-result.json"
            agent = _read(folder, agent_file) if agent_file in inventory else {}
            evidence["agent_completion_marker"] = (any("[TASK DONE]" in ((a.get("message") or {}).get("content") or "")
                                                       for a in attempts) if attempts else None)
            evidence["harness_completed"] = agent.get("task_completed")
            evidence["student_requests"] = f"trials/{sid}/private/student-requests.jsonl" if attempts else None
            evidence["public_actions"] = f"trials/{sid}/policy/worldlogs/public-api.jsonl" if (
                "policy/worldlogs/public-api.jsonl" in inventory) else None
            # Completion diagnostics are separate from the independently sealed task score.
            row["result"] = {**result, "agent_claimed_done": evidence["agent_completion_marker"],
                             "harness_completed": evidence["harness_completed"]}
    elif result.get("usage") is not None:
        raise ValueError(f"Usage reported without a sealed worker: {sid}")
    if row["status"] == "unscored" and (not result.get("unscored_reason") or result.get("passed") is not None):
        raise ValueError(f"Unscored receipt lacks a reason or claims a score: {sid}")
    return evidence


def _cost(run, scope):
    raw = safe_path(run, "cost-ledger.json").read_bytes()
    ledger = parse_json(raw)

    def money(value):
        try:
            amount = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("Invalid campaign charge") from exc
        if not amount.is_finite() or amount < 0:
            raise ValueError("Invalid campaign charge")
        return amount

    fees, entries = ledger.get("ancillary_fees", {}), ledger["entries"]
    seconds = ledger["gpu_billed_seconds"]
    if (type(seconds) is not int or seconds < 0 or any(type(e["billed_seconds"]) is not int
            or e["billed_seconds"] < 0 for e in entries) or sum(e["billed_seconds"] for e in entries) != seconds
            or sum(money(e["charged_cny"]) for e in entries) + sum(money(v) for v in fees.values()) != money(ledger["paid_cny"])
            or len({e["lease"] for e in entries}) != len(entries)):
        raise ValueError("Campaign billing totals disagree with settlements")
    if scope == "local_fixture" and (seconds or money(ledger["paid_cny"]) or ledger["reservation"] or entries):
        raise ValueError("Local fixture has a rental charge or reservation")
    cost = {"ledger_sha256": digest(raw), "settled_cny": ledger["paid_cny"], "gpu_billed_seconds": seconds,
            "outstanding_reservation": ledger["reservation"], "training_hours": 0, "teacher_calls": 0}
    if any("prepaid_terms" in row for row in [*entries, ledger["reservation"] or {}]):
        cost["billing_mode"] = "prepaid_week"
        cost["weekly_order_price_cny"] = None
    return cost


def _unstarted_calibration(run, campaign_sha, default_reason):
    results = {}
    for backend in ("transformers", "vllm"):
        folder = safe_path(run, "t37/" + backend)
        verdict_file = safe_path(folder, "verdict.json")
        if not verdict_file.exists():
            continue
        raw = verdict_file.read_bytes()
        verdict = parse_json(raw)
        measured = safe_path(folder, "measurements.json").read_bytes()
        samples = parse_json(measured)
        if (type(verdict.get("passed")) is not bool or verdict.get("measurement_sha256") != digest(measured)
                or samples.get("campaign_sha256") != campaign_sha or samples.get("backend") != backend):
            raise ValueError("T37 verdict or measurement binding differs from this campaign")
        results[backend] = {"passed": verdict["passed"], "reasons": verdict.get("reasons"),
                            "verdict_sha256": digest(raw), "measurement_sha256": digest(measured)}
    if not results:
        return results, default_reason
    if any(result["passed"] for result in results.values()):
        return results, "t37_passed_formal_batch_unstarted"
    if "vllm" in results:
        return results, "t37_no_qualified_backend"
    return results, "t37_transformers_failed_vllm_not_qualified"


def render_actual(report):
    formal = report["scope"] == "formal"
    title = {"formal": "正式开发筛查", "local_fixture": "本机夹具验收", "cpu_preflight": "准入前停线"}[report["scope"]]
    rows = [f"# 29 题同题开关配对 · {title}", ""]
    if formal:
        rows.append(f"Qwen3.5-2B 的正式 G1 **{STATUS_CN[report['formal_G1']]}**，G2 **{STATUS_CN[report['formal_G2']]}**。")
    elif report["scope"] == "cpu_preflight":
        rows.append(f"尚无批次启动。停因 {report['stop_reason']}。正式 G1、G2 待测。")
        for backend, result in report.get("calibration", {}).items():
            rows.append(f"- {backend} 的 T37 {'通过' if result['passed'] else '未通过'}。"
                        f"原始测量摘要 SHA-256 为 `{result['measurement_sha256']}`。")
    else:
        rows.append("本机替身通过实际 worker、仿真和独立评分器产生本报告。正式 G1、G2 仍待实测。")
    cost = report["cost"]
    charge = (f"包周服务器本阶段已归集 {cost['gpu_billed_seconds']} 卡秒，新增费用 {cost['settled_cny']} 元。"
              if cost.get("billing_mode") == "prepaid_week" else
              f"已结算 {cost['gpu_billed_seconds']} 卡秒、{cost['settled_cny']} 元。")
    rows.extend(["", f"四臂共 116 个计划槽，已启动 {report['started_slots']} 槽。"
                 + charge
                 + ("尚有未结算的租赁预留。" if cost["outstanding_reservation"] else ""),
                 "", "| 模型 | 思考 | 成功 / 已评分 / 计划 | 已知推理 / 全部 token | 请求 P50 / P95 秒 | 回合 P50 / P95 秒 |",
                 "| --- | --- | ---: | ---: | ---: | ---: |"])

    def quantile(metric):
        if not metric["n"]:
            return "—（0）"
        return f"{metric['p50']:.2f} / {metric['p95']:.2f}（n={metric['n']}）"
    for model, result in report["by_model"].items():
        for mode, arm in result["arms"].items():
            usage = arm["usage"]
            tokens = " / ".join(f"{usage[k]['known_sum']}（完整 {usage[k]['measured_slots']}/29）"
                                for k in ("reasoning_tokens", "total_tokens"))
            rows.append(f"| {model} | {'开' if mode == 'on' else '关'} | {arm['success']} / {arm['scored']} / 29 |"
                        f" {tokens} | {quantile(usage['request_seconds'])} | {quantile(usage['episode_seconds'])} |")
    for model, result in report["by_model"].items():
        role = "主模型" if model == PRIMARY else "描述性参照"
        rows.extend(["", f"## {model}（{role}）", "",
                     f"完整配对 {29-result['missing_pairs']}/29，净增 {result['net_gain']:+d} 题，"
                     f"{result['improved_scenes']} 个父场景净改善。开臂有证据可归因的执行失败 "
                     f"{result['arms']['on']['attributable_failures']}/29。", "",
                     "| 父场景 | 均成功 | 仅开成功 | 仅关成功 | 均失败 | 缺对 | 净增 |",
                     "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"])
        for scene in result["by_scene"]:
            cells = " | ".join(str(scene["four_cells"][key]) for key in CELLS)
            rows.append(f"| {scene['scene_id']} | {cells} | {scene['missing_pairs']} | {scene['net_gain']:+d} |")
        rows.extend(["", "| 停止原因 | 开臂 / 29 | 关臂 / 29 | 仅开停止 | 仅关停止 |",
                     "| --- | ---: | ---: | ---: | ---: |"])
        for key, label in (("episode_deadline", "回合墙钟"), ("request_timeout", "请求超时"),
                           ("context_length", "上下文超长"), ("any", "以上三类去重"),
                           ("token_budget", "累计 token 上限"), ("request_limit", "请求次数上限"),
                           ("generation_truncated", "生成截断")):
            stops = result["stop_pairs"].get(key, {})
            on, off = [result["arms"][mode]["stop_counts"].get(key, 0) for mode in ("on", "off")]
            rows.append(f"| {label} | {on} | {off} | {stops.get('only_on', '—')} | {stops.get('only_off', '—')} |")
        rows.extend(["", "| 旁证 | 思考开 | 思考关 |", "| --- | ---: | ---: |"])
        for field, label in (("agent_claimed_done", "输出显式完成标记的槽数"), ("harness_completed", "Harness 确认完成的槽数")):
            rows.append(f"| {label} | {result['arms']['on'][field]} | {result['arms']['off'][field]} |")
    rows.extend(["", "独立 checker 决定题目成功。逐槽评分文件、失败证据、停因、用量缺失数和超限偏向均可从 gates.json 复查。", ""])
    return "\n".join(rows)


def report_actual(run, output, *, g3=None):
    from .paired_batch import verify_execution

    run, output = Path(run).resolve(), Path(output)
    if output.resolve().is_relative_to(run):
        raise ValueError("Publish the standalone report outside the campaign")
    g3 = Path(g3) if g3 is not None else ROOT / G3_FILE
    with locked(run, ".coordinator.lock"):
        campaign = verify_run(run)
        if campaign["schema"] != PAIRED_PROTOCOL:
            raise ValueError("Expected the frozen Qwen3.5-2B/Qwen3-4B paired plan")
        cert = g3_evidence(g3)
        plan = read_jsonl(run / "plan-slots.jsonl")
        rows = SlotLedger(run, plan).read()
        calibration = {}
        if (run / "batch-execution.json").exists():
            execution = verify_execution(run, campaign, g3)
            fingerprint = digest(safe_path(run, "batch-execution.json").read_bytes())
            stop = None
            if (run / "batch-status.json").exists():
                status = _read(run, "batch-status.json")
                if (status.get("scope") == execution["scope"] and status.get("backend") == execution["backend"]
                        and status.get("slot_statuses") == Counter(r["status"] for r in rows)):
                    stop = status.get("stop_reason")
        else:
            readiness = _read(run, "dev-readiness.json")
            campaign_sha = (run / "campaign.sha256").read_text().strip()
            expected_reason = ("g3_evidence_invalid" if not cert["verified"] else "g3_" + cert["status"]
                               if cert["status"] != "pass" else "awaiting_linux_preflight_and_t37")
            if (readiness.get("scope") != "cpu_preflight" or readiness.get("campaign_sha256") != campaign_sha
                    or readiness.get("g3") != cert or readiness.get("reason") != expected_reason
                    or readiness.get("formal_launches_this_invocation") != 0
                    or readiness.get("slot_statuses") != {"unstarted": len(plan)}
                    or any(row["status"] != "unstarted" for row in rows) or (run / "selected-backend.sha256").exists()):
                raise ValueError("Expected an unstarted campaign with its CPU readiness record")
            execution = {"scope": "cpu_preflight", "backend": None, "campaign_sha256": campaign_sha,
                         "admission_sha256": None}
            fingerprint = None
            calibration, stop = _unstarted_calibration(run, campaign_sha, expected_reason)
        scope = execution["scope"]
        if scope not in {"formal", "local_fixture", "cpu_preflight"} or (scope == "local_fixture") != (execution["backend"] == "fixture"):
            raise ValueError("Unsupported execution scope/backend")
        evidence = {}
        for row in rows:
            if row.get("backend_sha256") != (None if row["status"] == "unstarted" else fingerprint):
                raise ValueError(f"Claim differs from the batch execution: {row['slot_id']}")
            proof = _terminal(run, row, execution)
            if proof is not None:
                evidence[row["slot_id"]] = proof
        gates = score_pairs(plan, rows)
        formal = scope == "formal"
        counts = Counter(r["status"] for r in rows)
        starts = len(rows) - counts["unstarted"]
        report = {"schema": "actual-paired-report-v1", "scope": scope, "backend": execution["backend"],
                  "plan_sha256": campaign["plan_slots_sha256"], "campaign_sha256": execution["campaign_sha256"],
                  "execution_sha256": fingerprint, "slots_sha256": digest((run / "slots.jsonl").read_bytes()),
                  "admission_sha256": execution["admission_sha256"], "planned_slots": len(plan), "started_slots": starts,
                  "formal_launches": starts if formal else 0, "slot_counts": dict(counts), "slot_evidence": evidence, "stop_reason": stop,
                  "G3": cert, **gates, "formal_G1": gates["G1"]["status"] if formal else "unmeasured",
                  "formal_G2": gates["G2"]["status"] if formal else "unmeasured", "cost": _cost(run, scope),
                  "eligible_for_collection": formal and cert["status"] == "pass" and
                    all(gates[key]["status"] == "pass" for key in ("G1", "G2"))}
        if calibration:
            report["calibration"] = calibration
    with new_output(output) as stage:
        (stage / "gates.json").write_bytes(json_bytes(report))
        (stage / "REPORT_CN.md").write_text(render_actual(report), encoding="utf-8")
    return {k: report[k] for k in ("scope", "planned_slots", "started_slots", "formal_launches", "formal_G1",
                                    "formal_G2", "eligible_for_collection")}
