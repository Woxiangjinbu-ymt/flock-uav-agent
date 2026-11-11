"""Present evidenced completion and remaining work without gating tools."""
from execution_memory_v2_context import ExecutionMemoryV2Middleware
from execution_memory_v3_context import QueryDecisionAuditMixin
from harness_integration import MemoryHarnessMiddleware


class ExecutionMemoryV5Middleware(QueryDecisionAuditMixin, ExecutionMemoryV2Middleware):
    def __init__(self, ledger, mode="evidence_memory_v5"):
        if mode != "evidence_memory_v5":
            raise ValueError(f"Unsupported remaining-work memory mode: {mode}")
        super().__init__(ledger, "evidence_memory_v2")
        self.variant = mode


class ExecutionMemoryV5HarnessMiddleware(QueryDecisionAuditMixin, MemoryHarnessMiddleware):
    pass
