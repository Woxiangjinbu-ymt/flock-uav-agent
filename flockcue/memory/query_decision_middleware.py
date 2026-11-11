"""Record concise query decisions without changing model or tool execution.

The response observer runs before its tools execute. Its audit is never fed
back as evidence, and a missing or malformed declaration has no control effect.
"""
from execution_memory_v2_context import ExecutionMemoryV2Middleware
from harness_integration import MemoryHarnessMiddleware


class QueryDecisionAuditMixin:
    def _decision_capture(self):
        try:
            return self.ledger.query_decision_context()
        except Exception as exc:
            self._error(exc)
            return None

    def _decision_response(self, response, context):
        if context is None:
            return
        try:
            for message in getattr(response, "result", []):
                self.ledger.record_model_response(
                    message.content, getattr(message, "tool_calls", []), context=context)
        except Exception as exc:
            # Audit failure must not suppress a response, retry the model, or
            # prevent any tool in the original response from executing.
            self._error(exc)

    def wrap_model_call(self, request, handler):
        def observed(prepared):
            context = self._decision_capture()
            response = handler(prepared)
            self._decision_response(response, context)
            return response
        return super().wrap_model_call(request, observed)

    async def awrap_model_call(self, request, handler):
        async def observed(prepared):
            context = self._decision_capture()
            response = await handler(prepared)
            self._decision_response(response, context)
            return response
        return await super().awrap_model_call(request, observed)


class ExecutionMemoryV3Middleware(QueryDecisionAuditMixin, ExecutionMemoryV2Middleware):
    def __init__(self, ledger, mode="evidence_memory_v3"):
        if mode != "evidence_memory_v3":
            raise ValueError(f"Unsupported query-decision memory mode: {mode}")
        super().__init__(ledger, "evidence_memory_v2")
        self.variant = mode


class ExecutionMemoryV3HarnessMiddleware(QueryDecisionAuditMixin, MemoryHarnessMiddleware):
    pass
