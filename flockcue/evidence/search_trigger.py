"""Opt-in public-only activation gate for the R2 coordinated search pilot.

This gate has no scene, checker, task ID, model, or filesystem input. The
calling agent must explicitly enable it and supply a current public progress
receipt at a tool boundary. It does not execute the search skill.
"""
from __future__ import annotations

from dataclasses import dataclass


TASK_PHRASE = "session task progress exceeds 95%"
FLEET_PHRASE = "all targets"
EARLIEST_ELAPSED_S = 400.0
MIN_REMAINING_S = 30.0


@dataclass(frozen=True)
class TriggerDecision:
    activate: bool
    reason: str


def decide_public_search_trigger(*, task_content: str, progress_percent: float,
                                 elapsed_s: float, budget_s: float = 500.0,
                                 enabled: bool = False) -> TriggerDecision:
    if not enabled:
        return TriggerDecision(False, "disabled")
    content = " ".join(task_content.lower().split())
    if TASK_PHRASE not in content or FLEET_PHRASE not in content:
        return TriggerDecision(False, "different_task_wording")
    if not 0 <= progress_percent <= 100 or not 0 <= elapsed_s <= budget_s:
        return TriggerDecision(False, "invalid_public_receipt")
    if progress_percent > 95:
        return TriggerDecision(False, "public_threshold_already_met")
    if elapsed_s < EARLIEST_ELAPSED_S:
        return TriggerDecision(False, "too_early")
    if budget_s - elapsed_s < MIN_REMAINING_S:
        return TriggerDecision(False, "insufficient_remaining_budget")
    return TriggerDecision(True, "late_public_progress_below_threshold")
