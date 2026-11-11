"""Present compact remaining work and sourced terminal facts without gating."""
from execution_memory_v2_context import ExecutionMemoryV2Middleware
from execution_memory_v3_context import QueryDecisionAuditMixin
from harness_integration import MemoryHarnessMiddleware


class ExecutionMemoryV6Middleware(QueryDecisionAuditMixin, ExecutionMemoryV2Middleware):
    def __init__(self, ledger, mode="evidence_memory_v6"):
        if mode != "evidence_memory_v6":
            raise ValueError(f"Unsupported compact remaining-work memory mode: {mode}")
        super().__init__(ledger, "evidence_memory_v2")
        self.variant = mode


class ExecutionMemoryV6HarnessMiddleware(QueryDecisionAuditMixin, MemoryHarnessMiddleware):
    pass
