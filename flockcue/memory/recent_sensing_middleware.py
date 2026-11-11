"""Opt-in compact memory at the same model boundaries as v1.

Only the advisory view changes. Public tools still execute normally, including
repeated or refreshed sensing; there is no cached or synthetic tool response.
"""
from execution_memory_context import ExecutionMemoryMiddleware


class ExecutionMemoryV2Middleware(ExecutionMemoryMiddleware):
    def __init__(self, ledger, mode="evidence_memory_v2"):
        if mode != "evidence_memory_v2":
            raise ValueError(f"Unsupported compact execution memory mode: {mode}")
        super().__init__(ledger, "evidence_memory")
        self.variant = mode
