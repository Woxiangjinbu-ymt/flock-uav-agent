"""Observe split fact/plan declarations without changing tool execution."""
from execution_memory_v2_context import ExecutionMemoryV2Middleware
from execution_memory_v3_context import QueryDecisionAuditMixin
from harness_integration import MemoryHarnessMiddleware


class ExecutionMemoryV4Middleware(QueryDecisionAuditMixin, ExecutionMemoryV2Middleware):
    def __init__(self, ledger, mode="evidence_memory_v4"):
        if mode != "evidence_memory_v4":
            raise ValueError(f"Unsupported split query-decision memory mode: {mode}")
        super().__init__(ledger, "evidence_memory_v2")
        self.variant = mode


class ExecutionMemoryV4HarnessMiddleware(QueryDecisionAuditMixin, MemoryHarnessMiddleware):
    pass
