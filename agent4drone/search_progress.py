"""Opt-in public search handoff policy, not an infeasibility/completion oracle.

The caller retains its original action/round/API budgets and all effect guards.
Only successful scoped public aggregate reads advance this state. Two unchanged
periodic reads after positive progress may hand control back; novel exploration
could still succeed later, so that choice carries an explicit opportunity cost.
"""
from __future__ import annotations

import copy
import math

STAGNANT_PERIODIC_READS = 2


class PublicSearchProgress:
    def __init__(self, scene_id, progress_every=10):
        self.scene_id = str(scene_id)
        if type(progress_every) is not int or progress_every <= 0:
            raise ValueError("invalid_progress_interval")
        self.progress_every = progress_every
        self.progress = None
        self.fixed_target_ids = set()
        self.stagnant_reads = 0
        self.stable_since_attempt = 0
        self.last_periodic_attempt = -1
        self.reads = []

    def observe(self, receipt, evidence_ref, *, attempts, trigger, fixed_target_ids=(), pending_target_ids=()):
        if (not isinstance(receipt, dict) or receipt.get("status") not in (None, "success")
                or receipt.get("error") or receipt.get("harness_transport_error")
                or (receipt.get("success") is not None and receipt["success"] is not True)
                or (receipt.get("partial_success") is not None and receipt["partial_success"] is not False)
                or receipt.get("session_id") != self.scene_id
                or receipt.get("source_endpoint") != "/sessions/current"
                or receipt.get("scope") != "public_session_aggregate_not_full_task_verdict"):
            raise ValueError("public_progress_unavailable")
        progress = receipt.get("progress_percentage")
        if (type(progress) not in (int, float) or not math.isfinite(progress) or not 0 <= progress <= 100
                or not isinstance(evidence_ref, str) or not evidence_ref
                or type(attempts) is not int or attempts < 0
                or trigger not in {"entry", "periodic", "new_public_reach", "terminal"}):
            raise ValueError("public_progress_unavailable")
        if self.progress is not None and progress < self.progress:
            raise ValueError("public_search_progress_regressed")
        if ((self.reads and attempts < self.reads[-1]["attempts"])
                or any(row["evidence_ref"] == evidence_ref for row in self.reads)
                or trigger == "periodic" and (attempts == 0 or attempts % self.progress_every
                                               or attempts <= self.last_periodic_attempt)):
            raise ValueError("invalid_public_progress_order")
        targets, pending = set(fixed_target_ids), set(pending_target_ids)
        if any(not isinstance(t, str) or not t for t in targets | pending) or not pending <= targets:
            raise ValueError("invalid_public_target_frontier")
        new_targets = targets - self.fixed_target_ids
        increased = self.progress is None or progress > self.progress
        if increased or new_targets or pending or progress == 0:
            self.stagnant_reads = 0
            self.stable_since_attempt = attempts
        elif trigger == "periodic" and attempts - self.stable_since_attempt >= self.progress_every:
            self.stagnant_reads += 1
        if trigger == "periodic":
            self.last_periodic_attempt = attempts
        self.progress = progress
        self.fixed_target_ids.update(targets)
        decision = ("public_stop_condition" if progress > 95 else
                    "handoff" if trigger == "periodic" and self.stagnant_reads >= STAGNANT_PERIODIC_READS else
                    "continue")
        self.reads.append({"attempts": attempts, "trigger": trigger, "progress_percentage": progress,
            "evidence_ref": evidence_ref, "new_fixed_target_ids": sorted(new_targets),
            "pending_target_ids": sorted(pending), "stagnant_periodic_reads": self.stagnant_reads,
            "decision": decision})
        return decision

    def snapshot(self):
        return copy.deepcopy({"revision": "search_progress_v8", "progress_percentage": self.progress,
            "stagnant_periodic_reads": self.stagnant_reads, "threshold": STAGNANT_PERIODIC_READS,
            "progress_interval_moves": self.progress_every, "stable_since_attempt": self.stable_since_attempt,
            "reads": self.reads, "scope": "public_progress_and_current_plan_fixed_target_observations",
            "mission_completion": "not_evaluated", "infeasibility_proven": False,
            "handoff_semantics": "strategy_stagnation_with_possible_unexplored_opportunities_not_budget_reduction",
            "original_round_api_repair_budgets_unchanged": True})
