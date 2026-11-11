"""Local-only DeepSeek compatibility candidate for the installed SDK.

No runner integration, credential lookup, or provider execution entry point.
The factory requires the SDK's MockTransport and always uses a synthetic key.
Current official schema: max_tokens; explicit thinking; reasoning history in
tool-enabled thinking requests. Real provider acceptance remains unverified.
"""
from __future__ import annotations

import math
from typing import Literal

import openai
from openai import _base_client
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI


httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
SYNTHETIC_KEY = "synthetic-local-deepseek-candidate-not-a-real-key"
BASE_URL = "https://api.deepseek.com"
MAX_OUTPUT_TOKENS = 393216
EFFORTS = {"low", "high", "max"}


def _tool_ids(message):
    if message.invalid_tool_calls or "function_call" in message.additional_kwargs:
        raise ValueError("Invalid or legacy tool-call format")
    ids = [call.get("id") for call in message.tool_calls]
    if any(not isinstance(ident, str) or not ident for ident in ids) or len(set(ids)) != len(ids):
        raise ValueError("Duplicate or missing tool-call identity")
    return ids


def _validate_messages(messages):
    """Support plain-text roles and complete, unambiguous tool continuations."""
    seen, pending = set(), set()
    if not any(isinstance(m, HumanMessage) for m in messages):
        raise ValueError("A user message is required")
    for message in messages:
        if not isinstance(message, (HumanMessage, SystemMessage, AIMessage, ToolMessage)):
            raise ValueError("Unsupported message role")
        if not isinstance(message.content, str):
            raise ValueError("Only text messages are supported")
        if isinstance(message, ToolMessage):
            if not isinstance(message.tool_call_id, str) or message.tool_call_id not in pending:
                raise ValueError("Tool result does not match a pending call")
            pending.remove(message.tool_call_id)
        elif pending:
            raise ValueError("Tool results must precede the next message")
        if isinstance(message, AIMessage):
            for ident in _tool_ids(message):
                if ident in seen:
                    raise ValueError("Duplicate or missing tool-call identity")
                seen.add(ident)
                pending.add(ident)
    if pending:
        raise ValueError("Missing tool results")


class _DeepSeekCandidate(ChatOpenAI):
    thinking_mode: Literal["enabled", "disabled"]
    candidate_effort: Literal["low", "high", "max"] | None = None

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        previous = {ident for message in messages if isinstance(message, AIMessage) for ident in _tool_ids(message)}
        if previous.intersection(_tool_ids(result.generations[0].message)):
            raise ValueError("Response reused a prior tool-call identity")
        return result

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        if self.use_responses_api is not False or self.streaming is not False or self.max_retries != 0:
            raise ValueError("Candidate requires synchronous non-streaming Chat Completions without retries")
        if any(k not in {"tools", "tool_choice", "parallel_tool_calls"} for k in kwargs):
            raise ValueError("Unsupported per-call parameter")
        messages = self._convert_input(input_).to_messages()
        _validate_messages(messages)
        payload = super()._get_request_payload(messages, stop=stop, **kwargs)
        if payload.get("stream", False) is not False:
            raise ValueError("Streaming is unsupported")
        maximum = payload.pop("max_completion_tokens", None)
        if type(maximum) is not int or not 1 <= maximum <= MAX_OUTPUT_TOKENS:
            raise ValueError("Invalid output token limit")
        payload["max_tokens"] = maximum
        if payload.get("model") != "deepseek-flash":
            raise ValueError("Candidate only covers deepseek-flash")
        payload["stream"] = False
        payload["extra_body"] = {"thinking": {"type": self.thinking_mode}}
        if self.thinking_mode == "enabled":
            if self.candidate_effort not in EFFORTS:
                raise ValueError("Thinking requires an explicit supported effort")
            payload.pop("temperature", None)
            payload["reasoning_effort"] = self.candidate_effort
            if payload.get("tool_choice") not in (None, "auto", "none"):
                raise ValueError("Thinking candidate supports only auto or none tool choice")
        else:
            if self.candidate_effort is not None:
                raise ValueError("Disabled thinking cannot specify reasoning effort")
            payload.pop("reasoning_effort", None)
        wire = payload["messages"]
        if len(wire) != len(messages):
            raise ValueError("Message mapping changed")
        retain = self.thinking_mode == "enabled" and bool(payload.get("tools"))
        for original, serialized in zip(messages, wire):
            serialized.pop("reasoning_content", None)
            if not isinstance(original, AIMessage) or not retain:
                continue
            reason = original.additional_kwargs.get("reasoning_content")
            if not isinstance(reason, str):
                raise ValueError("Tool-enabled thinking requires every prior assistant reasoning_content")
            serialized["reasoning_content"] = reason
        return payload

    def _create_chat_result(self, response, generation_info=None):
        data = response if isinstance(response, dict) else response.model_dump(warnings=False)
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or choices[0].get("message", {}).get("role") != "assistant":
            raise ValueError("Candidate requires one assistant choice")
        raw = choices[0]["message"]
        reasoning = raw.get("reasoning_content")
        if reasoning is not None and not isinstance(reasoning, str):
            raise ValueError("Invalid reasoning_content type")
        if self.thinking_mode == "enabled" and raw.get("tool_calls") and not isinstance(reasoning, str):
            raise ValueError("Thinking tool response is missing reasoning_content")
        result = super()._create_chat_result(response, generation_info)
        message = result.generations[0].message
        # Reject before a caller could execute a malformed tool response.
        _tool_ids(message)
        message.additional_kwargs.pop("reasoning_content", None)
        if self.thinking_mode == "enabled" and isinstance(reasoning, str):
            message.additional_kwargs["reasoning_content"] = reasoning
        return result

    def _stream(self, *args, **kwargs):
        raise ValueError("Streaming is unsupported by this local candidate")

    def stream(self, *args, **kwargs):
        # BaseChatModel may silently fall back to invoke when streaming=False.
        raise ValueError("Streaming is unsupported by this local candidate")

    async def astream(self, *args, **kwargs):
        raise ValueError("Async/streaming is unsupported by this local candidate")
        yield

    async def _astream(self, *args, **kwargs):
        raise ValueError("Async/streaming is unsupported by this local candidate")
        yield  # Keep the interface an async iterator without issuing requests.

    async def _agenerate(self, *args, **kwargs):
        raise ValueError("Async is unsupported by this local candidate")


def make_local_deepseek_candidate(*, thinking, max_tokens, transport,
                                 reasoning_effort=None, model="deepseek-flash", temperature=None):
    """Build a local SDK candidate; callers own closing both HTTP clients.

    enabled requires explicit low/high/max effort and omits temperature.
    disabled rejects effort and uses temperature=0.1 unless explicitly supplied.
    No real API key or arbitrary endpoint can be passed to this factory.
    """
    if thinking not in {"enabled", "disabled"}:
        raise ValueError("thinking must be explicitly enabled or disabled")
    if type(max_tokens) is not int or not 1 <= max_tokens <= MAX_OUTPUT_TOKENS:
        raise ValueError("max_tokens must be an integer in 1..393216")
    if model != "deepseek-flash":
        raise ValueError("Candidate only covers deepseek-flash")
    if not isinstance(transport, httpx.MockTransport):
        raise ValueError("Local candidate requires SDK MockTransport")
    if thinking == "enabled":
        if reasoning_effort not in EFFORTS or temperature is not None:
            raise ValueError("Enabled thinking requires explicit effort and no temperature")
    elif reasoning_effort is not None:
        raise ValueError("Disabled thinking rejects reasoning effort")
    else:
        temperature = 0.1 if temperature is None else temperature
        if type(temperature) not in (int, float) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("Invalid temperature")

    class RejectAsync(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise ValueError("Async transport is unsupported")

    sync = openai.DefaultHttpxClient(transport=transport, trust_env=False, follow_redirects=False)
    asynchronous = openai.DefaultAsyncHttpxClient(transport=RejectAsync(), trust_env=False, follow_redirects=False)
    return _DeepSeekCandidate(model=model, api_key=SYNTHETIC_KEY, base_url=BASE_URL,
        temperature=temperature, max_tokens=max_tokens, max_retries=0, streaming=False,
        use_responses_api=False, http_client=sync, http_async_client=asynchronous,
        thinking_mode=thinking, candidate_effort=reasoning_effort,
        extra_body={"thinking": {"type": thinking}})
