"""Use V6's compact view only for fully supported coordinate-route commands.

Other commands retain V5's complete model projection, including after visits
finish. Selection uses original syntax, never outcomes, actors, or case IDs.
All evidence, completion decisions, and tool permissions remain inherited.
"""
from __future__ import annotations

from execution_memory_v5 import ExecutionMemoryLedgerV5
from execution_memory_v6 import ExecutionMemoryLedgerV6
from task_evidence_r3 import explicit_routes


def supports_compact_route(command):
    """Require the existing route grammar to cover all non-whitespace text."""
    command = str(command)
    routes, _ = explicit_routes(command)
    if not routes:
        return False
    cursor = 0
    for route in routes:
        start, end = route["source_span"]
        if command[cursor:start].strip():
            return False
        cursor = end
    return not command[cursor:].strip()


class ExecutionMemoryLedgerV7(ExecutionMemoryLedgerV6):
    """A syntax-scoped projection change; no execution gate or new evidence."""

    def decision_context(self, actor_id=None, decision_kind="continue", plan=None):
        if supports_compact_route(self.command):
            return ExecutionMemoryLedgerV6.decision_context(self, actor_id, decision_kind, plan)
        return ExecutionMemoryLedgerV5.decision_context(self, actor_id, decision_kind, plan)

    def snapshot(self):
        result = super().snapshot()
        result["revision"] = "execution-memory-v7"
        result["projection_policy"] = "v6_complete_route" if supports_compact_route(self.command) else "v5_original"
        return result
