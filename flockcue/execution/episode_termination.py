"""Pure classification of a joined, verified episode; never authorizes new work.

The coordinator adds termination_context to a COPY only after process exit and
exit-seal validation. This function reads no files, changes no ledger, and cannot
prove hashes itself. A continue decision applies only to remaining slots in the
already authorized manifest. Scoring, batch continuation, and archive eligibility
are deliberately independent; none is a whole-task success label.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
import math
from pathlib import PurePath
import re


BUDGET_STOPS = frozenset({"token_limit", "http_attempt_limit", "task_deadline"})
_ISOLATION = frozenset({"archive_unchanged_after_drain", "world_unchanged_after_drain",
                        "late_action_rejected", "canary_absent", "checker_definitions_absent"})


def should_block_cost_on_request_stop(reason):
    """Only known post-response token/attempt caps avoid immediate ledger block.

    This is a fast hook, not final permission to continue. Deadline, cancellation,
    request timeout, unknown usage and unrecognized reasons remain conservative:
    an in-flight request may still have unknown cost. Final classification still
    rejects any existing block, missing settlement, malformed usage or bad seal.
    """
    return not (type(reason) is str and reason in {"token_limit", "http_attempt_limit"})


def _number(value, *, positive=False):
    if type(value) not in {int, float}:
        return False
    try:
        return math.isfinite(value) and (value > 0 if positive else value >= 0)
    except OverflowError:
        return False


def _integer(value):
    return type(value) is int and value >= 0


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _money(value):
    if not isinstance(value, str) or len(value) > 180 or re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", value) is None:
        raise ValueError("Malformed cost")
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0:
        raise ValueError("Malformed cost")
    return amount


def _usage(value):
    return (isinstance(value, dict) and all(_integer(value.get(k)) for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
            and value["total_tokens"] == value["prompt_tokens"] + value["completion_tokens"])


def _score_barrier(worker):
    """Report-level checks supplement mandatory parent verification."""
    context = worker.get("termination_context")
    if (not isinstance(context, dict) or context.get("worker_exited") is not True
            or context.get("exit_seal_verified") is not True):
        return "worker_exit_or_seal_unverified"
    if worker.get("status") != "completed":
        return "worker_unsealed"
    limits = worker.get("limits")
    if not isinstance(limits, dict):
        return "request_limits_missing"
    if (worker.get("drained") is not True or limits.get("closed") is not True
            or limits.get("needs_process_termination") is not False
            or limits.get("threads_still_alive") != []):
        return "transport_not_drained_or_closed"
    isolation = worker.get("isolation")
    if (not isinstance(isolation, dict) or not _ISOLATION <= isolation.keys()
            or any(value is not True for value in isolation.values())):
        return "episode_isolation_unverified"
    agent = worker.get("agent")
    if not isinstance(agent, dict) or type(agent.get("success")) is not bool:
        return "agent_result_invalid"
    info, verified = agent.get("archive_info"), agent.get("archive_verify")
    if (not isinstance(info, dict) or not isinstance(verified, dict) or info.get("closed") is not True
            or verified.get("valid") is not True or agent.get("harness_closed") is not True
            or type(info.get("complete")) is not bool or info["complete"] is not verified.get("complete")):
        return "archive_or_harness_unclosed_or_invalid"
    snapshot, files = worker.get("snapshot"), worker.get("files")
    if (not isinstance(snapshot, dict) or not isinstance(files, dict)
            or snapshot.get("path") != "private/score-world.pkl" or not _hash(snapshot.get("sha256"))
            or not _hash(snapshot.get("state_sha256")) or files.get(snapshot["path"]) != snapshot["sha256"]
            or not _number(snapshot.get("score_seconds"))):
        return "score_snapshot_unsealed"
    archive_path = info.get("path")
    if (not isinstance(archive_path, str) or not archive_path or
            not _hash(files.get("policy/modelio/" + PurePath(archive_path).name))):
        return "archive_not_in_worker_inventory"
    return None


def _limits_error(limits):
    attempts = limits.get("attempts")
    if not isinstance(attempts, list) or limits.get("stop_reason") is not None and not isinstance(limits.get("stop_reason"), str):
        return "request_limits_invalid"
    if not all(isinstance(row, dict) for row in attempts):
        return "request_limits_invalid"
    if (not _integer(limits.get("admitted_http_attempts")) or not _integer(limits.get("delegated_http_attempts"))
            or limits["admitted_http_attempts"] != len(attempts)
            or any(type(row.get("delegated")) is not bool for row in attempts)
            or limits["delegated_http_attempts"] != sum(row["delegated"] for row in attempts)
            or any(not _integer(row.get("attempt_id")) for row in attempts)
            or [row.get("attempt_id") for row in attempts] != list(range(1, len(attempts) + 1))
            or not _integer(limits.get("unknown_usage_attempts"))
            or not isinstance(limits.get("unknown_usage_attempt_ids"), list)):
        return "request_limits_invalid"
    unknown = [row["attempt_id"] for row in attempts if row.get("usage_status") == "unknown"]
    if limits["unknown_usage_attempts"] != len(unknown) or limits["unknown_usage_attempt_ids"] != unknown:
        return "request_usage_counters_inconsistent"
    if unknown or any(row["delegated"] and (not _usage(row.get("usage"))
            or not isinstance(row.get("usage_status"), str)
            or row["usage_status"] not in {"known", "known_late"}) for row in attempts):
        return "unknown_or_invalid_usage"
    if (not _integer(limits.get("known_total_tokens")) or limits["known_total_tokens"] != sum(
            row["usage"]["total_tokens"] for row in attempts if row["delegated"])):
        return "request_usage_counters_inconsistent"
    return None


def _cost_error(worker, snapshot):
    if not isinstance(snapshot, dict) or type(snapshot.get("blocked")) is not bool:
        return "cost_snapshot_invalid"
    # An existing block is authoritative, even when no money remains reserved.
    if snapshot["blocked"]:
        return "cost_ledger_blocked"
    try:
        reserved, locked = _money(snapshot["reserved"]), _money(snapshot["locked_reservation"])
        if reserved != 0 or locked != 0:
            return "cost_unsettled_or_locked"
        if snapshot.get("stop_reason") is not None or snapshot.get("currency") not in {"USD", "CNY"}:
            return "cost_snapshot_invalid"
        trials, attempts = snapshot["trials"], snapshot["attempts"]
        trial_id = worker["trial_id"]
        trial = trials[trial_id]
        if (not isinstance(trials, dict) or not isinstance(attempts, dict)
                or not isinstance(trial, dict) or trial.get("status") not in {"active", "finished"}
                or snapshot.get("active_trial") not in {None, trial_id}
                or (trial["status"] == "active") is not (snapshot.get("active_trial") == trial_id)):
            return "cost_trial_context_invalid"
        if any(not isinstance(a, dict) or a.get("status") != "settled" or not _usage(a.get("usage")) for a in attempts.values()):
            return "cost_unsettled_or_locked"
        delegated = [row for row in worker["limits"]["attempts"] if row.get("delegated") is True]
        relevant = [a for a in attempts.values() if a.get("trial_id") == trial_id]
        expected_ids = [trial_id + ":" + str(i) for i in range(1, len(relevant) + 1)]
        if (trial.get("attempt_ids") != expected_ids or len(relevant) != len(delegated)
                or any(ident not in attempts for ident in expected_ids)):
            return "cost_request_reconciliation_failed"
        for ident, request in zip(expected_ids, delegated):
            entry = attempts[ident]
            if (entry.get("reservation_id") != ident or entry.get("usage") != request.get("usage")
                    or not _hash(entry.get("request_body_sha256"))
                    or entry["request_body_sha256"] != request.get("request_body_sha256")
                    or entry.get("request_body_length") != request.get("request_body_length")):
                return "cost_request_reconciliation_failed"
        with localcontext() as ctx:
            ctx.prec = 400
            total = sum((_money(a["settled_conservative_charge"]) for a in attempts.values()), Decimal(0))
            subtotal = sum((_money(a["settled_conservative_charge"]) for a in relevant), Decimal(0))
            batch_budget, trial_budget = _money(snapshot["batch_budget"]), _money(trial["budget"])
            if (total != _money(snapshot["settled_conservative_charge"]) or subtotal != _money(trial["settled_conservative_charge"])
                    or total > batch_budget or subtotal > trial_budget
                    or _money(snapshot["unallocated_budget"]) != batch_budget - total):
                return "cost_totals_inconsistent"
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation):
        return "cost_snapshot_invalid"
    return None


def classify_episode_termination(worker, cost_snapshot=None):
    """Classify validated report data without unlocking or mutating anything.

    Online reports require a cost snapshot; local no-cost operation requires an
    explicit empty network-attempt list and no online_arm. A complete normal
    archive can be considered for later data review; this does not certify task
    success, data quality, labels/masks, split independence, or Teacher eligibility.
    """
    result = {"schema_version": 1, "category": "infrastructure_error", "reason": "invalid_worker",
        "stop_reason": None, "score_eligible": False, "batch_action": "stop", "archive_training_eligible": False,
        "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval",
        "task_success": "not_evaluated", "cost_context": "unresolved",
        "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work",
        "requires_parent_exit_seal": True}
    if not isinstance(worker, dict):
        return result
    barrier = _score_barrier(worker)
    if barrier:
        result["reason"] = barrier
        return result
    result["score_eligible"] = True
    limits, agent = worker["limits"], worker["agent"]
    result["stop_reason"] = limits.get("stop_reason")
    limits_error = _limits_error(limits)
    if limits_error in {"request_limits_invalid", "request_usage_counters_inconsistent"}:
        result.update(reason=limits_error, score_eligible=False)
        return result
    if cost_snapshot is None:
        if "online_arm" in worker or worker.get("network_attempts") != []:
            result.update(reason="missing_cost_snapshot", cost_context="online_cost_required")
            return result
        result["cost_context"] = "local_no_cost"
    else:
        result["cost_context"] = "durable_cost_snapshot"
        error = _cost_error(worker, cost_snapshot)
        if error:
            result.update(reason=error, cost_stop_reason=cost_snapshot.get("stop_reason") if isinstance(cost_snapshot, dict) else None)
            return result
    if limits_error:
        result["reason"] = limits_error
        return result
    stop = result["stop_reason"]
    lifecycle = worker.get("lifecycle")
    if not isinstance(lifecycle, dict) or type(lifecycle.get("cancelled")) is not bool:
        result["reason"] = "cancellation_state_invalid"
        return result
    if lifecycle["cancelled"] or stop == "cancelled":
        result.update(category="cancelled", reason="cancelled")
        return result
    # A local world deadline can fire before a further HTTP admission check.
    runtime = worker.get("runtime")
    if stop is None and isinstance(runtime, dict) and _number(runtime.get("task_seconds"), positive=True):
        if (worker["snapshot"]["score_seconds"] == runtime["task_seconds"]
                and _number(worker.get("elapsed")) and worker["elapsed"] >= runtime["task_seconds"]
                and worker.get("timer_enabled") is True and worker.get("clock") == "time.monotonic"):
            stop = "task_deadline"
            result["stop_reason"] = stop
    if stop in BUDGET_STOPS:
        error_type = agent.get("error_type")
        permitted_errors = {None, "RequestLimitError"} | ({"TaskDeadlineReached"} if stop == "task_deadline" else set())
        if error_type is not None and (not isinstance(error_type, str) or error_type not in permitted_errors):
            result.update(category="model_or_api_error", reason="budget_stop_with_unrelated_error")
            return result
        result.update(category="budget_exhausted", reason=stop, batch_action="continue")
        return result
    if stop is not None:
        result.update(category="model_or_api_error", reason=stop)
        return result
    if agent["success"] is not True or agent.get("error_type") is not None:
        result.update(category="model_or_api_error", reason="agent_execution_error")
        return result
    if not agent["archive_verify"]["complete"]:
        result["reason"] = "partial_archive_without_normal_budget_stop"
        return result
    result.update(category="normal_return", reason="normal_return", batch_action="continue", archive_training_eligible=True)
    return result
