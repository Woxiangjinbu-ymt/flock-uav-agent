"""Opt-in, command-local model I/O evidence; never executes or changes a call.

Only HTTP hooks capture final serialized input. Middleware input precedes inner
middleware. Stream bytes are HTTP-client body bytes, not TCP/HTTP framing.
"""
from __future__ import annotations

import base64
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import math
import os
import sys
from pathlib import Path
import threading
import uuid

from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain_core.messages import BaseMessage
import httpx
try:
    import httpx2
except ImportError:
    httpx2 = None

_OWNER_KEY = "_uav_model_io_archive_owner_v1"
_QUARANTINE_KEY = "_uav_model_io_archive_ambiguous_v1"
_REQUEST_BIND_LOCK = threading.RLock()
_ACTIVE = ContextVar("uav_model_io_logical_call", default=None)
_REASONS = frozenset({"hook_setup_unavailable", "unsupported_provider",
    "observation_worker_not_instrumented", "serialization_unsupported",
    "request_body_unavailable", "response_stream_unsupported", "wire_missing",
    "no_model_calls", "open_model_on_close", "open_tool_on_close",
    "response_incomplete", "tool_source_missing", "tool_source_ambiguous",
    "archive_io_error", "observer_error", "model_error", "tool_error",
    "tool_result_id_mismatch", "tool_calls_not_executed", "request_object_reused", "unspecified"})


@dataclass(frozen=True)
class _Owner:
    archive: object
    logical_call_id: str
    attempt_id: str | None = None


def current_archive():
    owner = _ACTIVE.get()
    return owner.archive if owner is not None else None


def record_context_window(window, omitted):
    owner = _ACTIVE.get()
    if owner is not None:
        owner.archive._append("student_context", {"logical_call_id": owner.logical_call_id,
                                                  "window": window, "omitted": omitted})


def _bytes(value):
    return {"base64": base64.b64encode(value).decode("ascii"),
            "sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}


def _safe(archive, function, *args, **kwargs):
    """Observer failures must never replace the result/exception of the handler."""
    try:
        return function(*args, **kwargs)
    except Exception as exc:
        try:
            archive._error(exc)
        except Exception:
            pass
        return None


class ModelIOArchive:
    def __init__(self, command_id, output_dir=None):
        self.command_id, self.archive_id = str(command_id), uuid.uuid4().hex
        self.path = None
        self._lock, self._closed, self._failed = threading.RLock(), False, False
        self._file, self._sequence, self._complete = None, 0, False
        self._errors, self._reasons = set(), set()
        self._models, self._tools, self._attempts, self._sources = {}, {}, {}, {}
        self._consumed_sources = set()
        try:
            from flockcue import logging_config
            folder = Path(output_dir) if output_dir is not None else logging_config.LOG_DIR / "model_io"
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.path = folder / ("model-io-" + self.archive_id + ".jsonl")
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            self._file = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
            self._append("archive_start", {"input_scope": "wire_only_at_http_hooks"})
        except Exception as exc:
            self._error(exc, io=True)
        except BaseException:
            try:
                if self._file is not None:
                    self._file.close()
                elif "fd" in locals():
                    os.close(fd)
            finally:
                self._closed = True
            raise

    @property
    def closed(self):
        with self._lock:
            return self._closed

    def _error(self, exc, io=False):
        with self._lock:
            if self._closed:
                return
            self._errors.add(type(exc).__name__)
            self._reasons.add("archive_io_error" if io else "observer_error")
            self._failed |= io

    def mark_incomplete(self, reason):
        with self._lock:
            if not self._closed:
                self._reasons.add(reason if reason in _REASONS else "unspecified")

    def _append(self, kind, data):
        # Caller holds the same RLock, including serialization and sequence commit.
        with self._lock:
            if self._closed or self._failed or self._file is None:
                return
            try:
                row = {"schema_version": 1, "archive_id": self.archive_id,
                       "episode_id": self.archive_id, "command_id": self.command_id,
                       "sequence": self._sequence + 1, "kind": kind, "data": data}
                self._file.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
                self._file.flush()
                self._sequence += 1
            except Exception as exc:
                self._error(exc, io=True)

    def _serialize(self, value):
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float) and math.isfinite(value):
            return value
        if isinstance(value, bytes):
            return {"__bytes__": _bytes(value)}
        if isinstance(value, (list, tuple)):
            return [self._serialize(item) for item in value]
        if isinstance(value, dict) and all(isinstance(key, str) for key in value):
            return {key: self._serialize(item) for key, item in value.items()}
        if isinstance(value, BaseMessage):
            # Never inspect ToolMessage.artifact or response_metadata (headers,
            # private checkers and SDK objects are not model-visible messages).
            names = ("type", "content", "additional_kwargs", "id", "name", "tool_calls",
                     "invalid_tool_calls", "usage_metadata", "tool_call_id", "status")
            return {name: self._serialize(getattr(value, name)) for name in names if hasattr(value, name)}
        if isinstance(value, ModelResponse):
            return {"result": self._serialize(value.result),
                    "structured_response": self._serialize(value.structured_response)}
        self.mark_incomplete("serialization_unsupported")
        return {"__unsupported_type__": type(value).__name__}

    def begin_model(self, request):
        with self._lock:
            if self._closed:
                return None
            call = "model-" + str(len(self._models) + 1)
            self._models[call] = {"ended": False, "attempts": []}
            self._append("model_start", {"logical_call_id": call,
                "stage": "outer_middleware_input_before_inner_middleware",
                "input": self._serialize({"system_message": getattr(request, "system_message", None),
                                          "messages": getattr(request, "messages", [])})})
            return call

    def end_model(self, call, result=None, error=None):
        with self._lock:
            model = self._models.get(call)
            if self._closed or model is None or model["ended"]:
                return
            if error is not None:
                self.mark_incomplete("model_error")
            self._append("model_end", {"logical_call_id": call,
                "status": "error" if error else "returned", "error_type": type(error).__name__ if error else None,
                "output": self._serialize(result) if error is None else None,
                "output_scope": "structured_handler_output_not_wire"})
            model["ended"] = True
            messages = getattr(result, "result", [result]) if result is not None else []
            for message in messages:
                extra = getattr(message, "additional_kwargs", {})
                raw = extra.get("tool_calls") if isinstance(extra, dict) else None
                calls = raw if isinstance(raw, list) and raw else getattr(message, "tool_calls", [])
                for item in calls or []:
                    raw_id = item.get("id") if isinstance(item, dict) else None
                    if isinstance(raw_id, str) and raw_id:
                        self._sources.setdefault(raw_id, []).append(call)
                        if len(self._sources[raw_id]) > 1:
                            self.mark_incomplete("tool_source_ambiguous")
                    else:
                        self.mark_incomplete("tool_source_missing")

    def begin_tool(self, request):
        with self._lock:
            if self._closed:
                return None
            span = "tool-" + str(len(self._tools) + 1)
            tool_call = getattr(request, "tool_call", {})
            raw_id = tool_call.get("id") if isinstance(tool_call, dict) else None
            sources = self._sources.get(raw_id, []) if isinstance(raw_id, str) and raw_id else []
            relation = "unique" if len(sources) == 1 else "ambiguous" if sources else "missing"
            if relation != "unique":
                self.mark_incomplete("tool_source_" + relation)
            data = {"tool_span_id": span, "raw_tool_call_id": self._serialize(raw_id),
                    "source_model_id": sources[0] if relation == "unique" else None,
                    "source_relation": relation, "source_candidates": list(sources),
                    "input": self._serialize(tool_call)}
            self._tools[span] = {"ended": False, "raw_id": raw_id}
            if relation == "unique":
                self._consumed_sources.add((raw_id, sources[0]))
            self._append("tool_start", data)
            return span

    def end_tool(self, span, result=None, error=None):
        with self._lock:
            if self._closed or span not in self._tools or self._tools[span]["ended"]:
                return
            mismatch = isinstance(result, BaseMessage) and hasattr(result, "tool_call_id") and result.tool_call_id != self._tools[span]["raw_id"]
            if error is not None:
                self.mark_incomplete("tool_error")
            if mismatch:
                self.mark_incomplete("tool_result_id_mismatch")
            self._append("tool_end", {"tool_span_id": span, "status": "error" if error else "returned",
                "error_type": type(error).__name__ if error else None, "tool_call_id_mismatch": mismatch,
                "output": self._serialize(result) if error is None else None})
            self._tools[span]["ended"] = True

    def request(self, request, call):
        with _REQUEST_BIND_LOCK:
            previous = request.extensions.get(_OWNER_KEY)
            if isinstance(previous, _Owner) or request_is_ambiguous(request):
                request.extensions[_QUARANTINE_KEY] = True
                self.mark_incomplete("request_object_reused")
                if isinstance(previous, _Owner):
                    previous.archive.mark_incomplete("request_object_reused")
                return None
            with self._lock:
                if self._closed or call not in self._models or self._models[call]["ended"]:
                    return None
                attempt = "attempt-" + str(len(self._attempts) + 1)
                owner = _Owner(self, call, attempt)
                record = {"logical_call_id": call, "ended": False, "response": False,
                          "hash": hashlib.sha256(), "length": 0, "chunks": 0, "request_captured": False}
                self._attempts[attempt] = record
                self._models[call]["attempts"].append(attempt)
                request.extensions[_OWNER_KEY] = owner
                body, error = None, None
                try:
                    content = request.content  # Never read()/consume an outbound stream.
                    if not isinstance(content, bytes):
                        raise TypeError()
                    body, record["request_captured"] = _bytes(content), True
                except Exception as exc:
                    error = type(exc).__name__
                    self.mark_incomplete("request_body_unavailable")
                self._append("http_request", {"logical_call_id": call, "attempt_id": attempt,
                    "body_scope": "serialized_http_request_content", "body": body,
                    "error_type": error})  # No URL, headers, authentication or SDK client data.
                return owner
    def response_start(self, owner, response):
        with self._lock:
            item = self._attempts.get(owner.attempt_id)
            if self._closed or item is None or item["ended"] or item["response"]:
                return False
            item["response"] = True
            encoding = response.headers.get("content-encoding", "identity").lower()
            encoding = encoding if encoding in {"identity", "gzip", "deflate", "br", "zstd"} else "other"
            self._append("http_response_start", {"logical_call_id": owner.logical_call_id,
                "attempt_id": owner.attempt_id, "status_code": response.status_code,
                "content_encoding": encoding,
                "body_scope": "buffered_httpx_content" if hasattr(response, "_content") else "httpx_stream_before_content_decoding"})
            return True

    def chunk(self, owner, chunk):
        with self._lock:
            item = self._attempts.get(owner.attempt_id)
            if self._closed or item is None or item["ended"]:
                return
            item["hash"].update(chunk)
            item["length"] += len(chunk)
            item["chunks"] += 1
            self._append("http_response_chunk", {"logical_call_id": owner.logical_call_id,
                "attempt_id": owner.attempt_id, "chunk_index": item["chunks"], "body": _bytes(chunk)})

    def response_end(self, owner, complete=False, error=None):
        with self._lock:
            item = self._attempts.get(owner.attempt_id)
            if self._closed or item is None or item["ended"]:
                return
            item["ended"] = True
            item["complete"] = bool(complete)
            if not complete:
                self.mark_incomplete("response_incomplete")
            self._append("http_response_end", {"logical_call_id": owner.logical_call_id,
                "attempt_id": owner.attempt_id, "complete": bool(complete),
                "status": "complete" if complete else "error" if error else "partial",
                "error_type": type(error).__name__ if error else None,
                "response_observed": item["response"], "chunk_count": item["chunks"],
                "length": item["length"], "sha256": item["hash"].hexdigest()})

    def info(self):
        with self._lock:
            return {"archive_id": self.archive_id, "episode_id": self.archive_id,
                    "command_id": self.command_id, "path": str(self.path) if self.path else None,
                    "state": "incomplete" if self._reasons or self._errors else "closed" if self._closed else "open",
                    "closed": self._closed, "complete": self._closed and self._complete and not (self._failed or self._reasons or self._errors),
                    "sequence": self._sequence, "reasons": sorted(self._reasons), "error_types": sorted(self._errors)}

    def close(self, reason="command_end"):
        with self._lock:
            if self._closed:
                return self.info()
            try:
                for call, model in self._models.items():
                    if not model["ended"]:
                        self.mark_incomplete("open_model_on_close")
                        model["ended"] = True
                        self._append("model_end", {"logical_call_id": call, "status": "partial", "output": None})
                    if not model["attempts"]:
                        self.mark_incomplete("wire_missing")
                for span, tool in self._tools.items():
                    if not tool["ended"]:
                        self.mark_incomplete("open_tool_on_close")
                        tool["ended"] = True
                        self._append("tool_end", {"tool_span_id": span, "status": "partial", "output": None})
                for attempt, item in self._attempts.items():
                    if not item["ended"]:
                        self.response_end(_Owner(self, item["logical_call_id"], attempt))
                pending = [{"raw_tool_call_id": raw_id, "source_model_id": call}
                           for raw_id, calls in self._sources.items() for call in calls
                           if (raw_id, call) not in self._consumed_sources]
                if pending:
                    self.mark_incomplete("tool_calls_not_executed")
                    self._append("tool_calls_unexecuted", {"calls": pending})
                if not self._models:
                    self.mark_incomplete("no_model_calls")
                self._complete = not (self._reasons or self._errors)
                # The reason is a caller-selected fixed status, never exception text.
                allowed = {"command_end", "completed", "failed", "exception", "stream_error", "step_limit", "execute_aborted"}
                self._append("archive_end", {"reason": reason if reason in allowed else "other",
                    "complete": self._complete, "reasons": sorted(self._reasons), "error_types": sorted(self._errors)})
            except BaseException as exc:
                self._error(exc)
                if not isinstance(exc, Exception):
                    raise
            finally:
                primary = sys.exc_info()[1]
                try:
                    if self._file is not None:
                        self._file.close()
                except BaseException as exc:
                    self._error(exc, io=True)
                    if not isinstance(exc, Exception) and primary is None:
                        raise
                finally:
                    self._closed = True
            return self.info()


class ModelIOArchiveMiddleware(AgentMiddleware):
    def __init__(self, archive):
        self.archive = archive

    def wrap_model_call(self, request, handler):
        call = _safe(self.archive, self.archive.begin_model, request)
        token = _ACTIVE.set(_Owner(self.archive, call))
        try:
            result = handler(request)
        except BaseException as exc:
            try:
                _safe(self.archive, self.archive.end_model, call, error=exc)
            except BaseException:
                pass
            raise
        else:
            _safe(self.archive, self.archive.end_model, call, result)
            return result
        finally:
            _ACTIVE.reset(token)

    async def awrap_model_call(self, request, handler):
        call = _safe(self.archive, self.archive.begin_model, request)
        token = _ACTIVE.set(_Owner(self.archive, call))
        try:
            result = await handler(request)
        except BaseException as exc:
            try:
                _safe(self.archive, self.archive.end_model, call, error=exc)
            except BaseException:
                pass
            raise
        else:
            _safe(self.archive, self.archive.end_model, call, result)
            return result
        finally:
            _ACTIVE.reset(token)

    def wrap_tool_call(self, request, handler):
        span = _safe(self.archive, self.archive.begin_tool, request)
        try:
            result = handler(request)
        except BaseException as exc:
            try:
                _safe(self.archive, self.archive.end_tool, span, error=exc)
            except BaseException:
                pass
            raise
        _safe(self.archive, self.archive.end_tool, span, result)
        return result

    async def awrap_tool_call(self, request, handler):
        span = _safe(self.archive, self.archive.begin_tool, request)
        try:
            result = await handler(request)
        except BaseException as exc:
            try:
                _safe(self.archive, self.archive.end_tool, span, error=exc)
            except BaseException:
                pass
            raise
        _safe(self.archive, self.archive.end_tool, span, result)
        return result


class _SyncTee:
    def __init__(self, stream, owner):
        self.stream, self.owner = stream, owner
    def __iter__(self):
        try:
            for chunk in self.stream:
                _safe(self.owner.archive, self.owner.archive.chunk, self.owner, chunk)
                yield chunk
        except BaseException as exc:
            try:
                _safe(self.owner.archive, self.owner.archive.response_end, self.owner, error=exc)
            except BaseException:
                pass
            raise
        else:
            _safe(self.owner.archive, self.owner.archive.response_end, self.owner, complete=True)
    def close(self):
        try:
            result = self.stream.close()
        except BaseException as exc:
            try:
                _safe(self.owner.archive, self.owner.archive.response_end, self.owner, error=exc)
            except BaseException:
                pass
            raise
        else:
            _safe(self.owner.archive, self.owner.archive.response_end, self.owner)
            return result


class _AsyncTee:
    def __init__(self, stream, owner):
        self.stream, self.owner = stream, owner
    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                _safe(self.owner.archive, self.owner.archive.chunk, self.owner, chunk)
                yield chunk
        except BaseException as exc:
            try:
                _safe(self.owner.archive, self.owner.archive.response_end, self.owner, error=exc)
            except BaseException:
                pass
            raise
        else:
            _safe(self.owner.archive, self.owner.archive.response_end, self.owner, complete=True)
    async def aclose(self):
        try:
            result = await self.stream.aclose()
        except BaseException as exc:
            try:
                _safe(self.owner.archive, self.owner.archive.response_end, self.owner, error=exc)
            except BaseException:
                pass
            raise
        else:
            _safe(self.owner.archive, self.owner.archive.response_end, self.owner)
            return result


_TEES = [(module, type("ArchiveSyncTee", (_SyncTee, module.SyncByteStream), {}),
          type("ArchiveAsyncTee", (_AsyncTee, module.AsyncByteStream), {}),
          type("ArchiveDualTee", (_SyncTee, _AsyncTee, module.SyncByteStream, module.AsyncByteStream), {}))
         for module in (httpx, httpx2) if module is not None]


def request_is_ambiguous(request):
    return request.extensions.get(_QUARANTINE_KEY) is True


def archive_request(request):
    owner = _ACTIVE.get()
    if owner is not None:
        return _safe(owner.archive, owner.archive.request, request, owner.logical_call_id)
    # A previously bound object reused outside a model context cannot retain its
    # former attribution. Quarantine it without writing any closed archive.
    previous = request.extensions.get(_OWNER_KEY)
    if isinstance(previous, _Owner):
        request.extensions[_QUARANTINE_KEY] = True
        _safe(previous.archive, previous.archive.mark_incomplete, "request_object_reused")
    return None


def archive_response(response):
    owner = None
    try:
        if request_is_ambiguous(response.request):
            return
        owner = response.request.extensions.get(_OWNER_KEY)
        if not isinstance(owner, _Owner) or not owner.archive.response_start(owner, response):
            return
        if hasattr(response, "_content"):
            owner.archive.chunk(owner, response.content)
            owner.archive.response_end(owner, complete=True)
            return
        for module, sync, async_, dual in _TEES:
            if isinstance(response.stream, module.SyncByteStream) and isinstance(response.stream, module.AsyncByteStream):
                response.stream = dual(response.stream, owner)
                return
            if isinstance(response.stream, module.AsyncByteStream):
                response.stream = async_(response.stream, owner)
                return
            if isinstance(response.stream, module.SyncByteStream):
                response.stream = sync(response.stream, owner)
                return
        owner.archive.mark_incomplete("response_stream_unsupported")
    except Exception as exc:
        if isinstance(owner, _Owner):
            _safe(owner.archive, owner.archive._error, exc)


async def aarchive_request(request):
    return archive_request(request)


async def aarchive_response(response):
    archive_response(response)


def verify_archive(path):
    """Check bytes, lifecycle and source joins independently of the end claim.

    `valid` means internally consistent evidence, including explicit partials;
    `complete` additionally requires captured wire and unambiguous tool joins.
    This is not authenticity proof or a Teacher-data quality verdict.
    """
    errors, rows, attempts, models, tools, sources = [], [], {}, {}, {}, {}
    coverage, ended, consumed, pending_event = True, False, set(), None
    try:
        raw = Path(path).read_bytes()
        if not raw.endswith(b"\n"):
            raise ValueError("unterminated_line")
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
        identity = None
        def decode(body):
            value = base64.b64decode(body["base64"], validate=True)
            if len(value) != body["length"] or hashlib.sha256(value).hexdigest() != body["sha256"]:
                raise ValueError("body_hash")
            return value
        for sequence, row in enumerate(rows, 1):
            key = (row["archive_id"], row["episode_id"], row["command_id"])
            identity = identity or key
            if row["schema_version"] != 1 or row["sequence"] != sequence or key != identity or key[0] != key[1] or ended:
                raise ValueError("order_or_identity")
            kind, data = row["kind"], row["data"]
            if kind == "archive_start":
                if sequence != 1: raise ValueError("duplicate_start")
            elif sequence == 1:
                raise ValueError("missing_start")
            elif kind == "model_start":
                call = data["logical_call_id"]
                if call in models: raise ValueError("duplicate_model")
                models[call] = {"ended": False, "attempts": []}
            elif kind == "model_end":
                call = data["logical_call_id"]
                if call not in models or models[call]["ended"]: raise ValueError("model_end")
                models[call]["ended"] = True
                coverage &= data["status"] == "returned"
                output = data.get("output")
                messages = output.get("result", [output] if output.get("type") == "ai" else []) if isinstance(output, dict) else []
                for message in messages:
                    extra = message.get("additional_kwargs", {})
                    declared = extra.get("tool_calls") if isinstance(extra, dict) else None
                    declared = declared if isinstance(declared, list) and declared else message.get("tool_calls", [])
                    for item in declared or []:
                        raw_id = item.get("id") if isinstance(item, dict) else None
                        if isinstance(raw_id, str) and raw_id:
                            sources.setdefault(raw_id, []).append(call)
                            coverage &= len(sources[raw_id]) == 1
                        else:
                            coverage = False
            elif kind == "tool_start":
                span, raw_id = data["tool_span_id"], data["raw_tool_call_id"]
                if span in tools: raise ValueError("duplicate_tool")
                candidates = sources.get(raw_id, []) if isinstance(raw_id, str) and raw_id else []
                relation = "unique" if len(candidates) == 1 else "ambiguous" if candidates else "missing"
                expected = candidates[0] if relation == "unique" else None
                if (data["source_relation"], data["source_model_id"], data["source_candidates"]) != (relation, expected, candidates):
                    raise ValueError("tool_source")
                if isinstance(data["input"], dict) and data["input"].get("id") != raw_id: raise ValueError("raw_tool_id")
                tools[span] = {"ended": False, "raw_id": raw_id}
                coverage &= relation == "unique"
                if relation == "unique": consumed.add((raw_id, expected))
            elif kind == "tool_end":
                span = data["tool_span_id"]
                if span not in tools or tools[span]["ended"]: raise ValueError("tool_end")
                tools[span]["ended"] = True
                coverage &= data["status"] == "returned" and not data.get("tool_call_id_mismatch", False)
                output = data.get("output")
                if isinstance(output, dict) and "tool_call_id" in output:
                    coverage &= output["tool_call_id"] == tools[span]["raw_id"]
            elif kind == "http_request":
                attempt, call = data["attempt_id"], data["logical_call_id"]
                if attempt in attempts or call not in models or models[call]["ended"]: raise ValueError("attempt_owner")
                models[call]["attempts"].append(attempt)
                attempts[attempt] = {"hash": hashlib.sha256(), "length": 0, "chunks": 0,
                                     "logical": call, "started": False, "ended": False}
                if data["body"] is not None: decode(data["body"])
                else: coverage = False
            elif kind in {"http_response_start", "http_response_chunk", "http_response_end"}:
                item = attempts[data["attempt_id"]]
                if item["logical"] != data["logical_call_id"] or item["ended"]: raise ValueError("response_owner")
                if kind == "http_response_start":
                    if item["started"]: raise ValueError("duplicate_response")
                    item["started"] = True
                    if not isinstance(data["status_code"], int): raise ValueError("status_code")
                elif kind == "http_response_chunk":
                    if not item["started"]: raise ValueError("chunk_before_response")
                    value = decode(data["body"])
                    item["hash"].update(value)
                    item["length"] += len(value)
                    item["chunks"] += 1
                    if data["chunk_index"] != item["chunks"]: raise ValueError("chunk_order")
                else:
                    if (data["length"], data["chunk_count"], data["sha256"]) != (item["length"], item["chunks"], item["hash"].hexdigest()): raise ValueError("response_hash")
                    if data["response_observed"] != item["started"]: raise ValueError("response_observed")
                    coverage &= data["complete"] is True and data["status"] == "complete" and item["started"]
                    item["ended"] = True
            elif kind == "tool_calls_unexecuted":
                if pending_event is not None: raise ValueError("duplicate_unexecuted")
                pending_event = data["calls"]
                coverage = False
            elif kind == "archive_end":
                pending = [{"raw_tool_call_id": raw_id, "source_model_id": call}
                           for raw_id, calls in sources.items() for call in calls
                           if (raw_id, call) not in consumed]
                if pending_event != (pending or None): raise ValueError("unexecuted_declarations")
                coverage &= bool(models) and all(model["attempts"] for model in models.values())
                coverage &= not data["reasons"] and not data["error_types"]
                if data["complete"] is True and not coverage: raise ValueError("false_complete")
                ended = True
            else:
                raise ValueError("unknown_kind")
        if not ended or any(not item["ended"] for group in (models, tools, attempts) for item in group.values()):
            raise ValueError("unclosed_archive")
    except Exception as exc:
        errors.append(type(exc).__name__)
    complete = bool(not errors and ended and coverage and rows[-1]["data"].get("complete") is True)
    return {"valid": not errors, "complete": complete, "record_count": len(rows), "error_types": errors}
