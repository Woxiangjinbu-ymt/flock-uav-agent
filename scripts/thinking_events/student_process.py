"""Deadline-bound JSON over an inherited local socket (no network endpoint)."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import threading
import time
import uuid

from .student_timing import absent_timing

SCRIPTS = Path(__file__).resolve().parents[1]
MAX_FRAME = 32 * 1024 * 1024


def _read(sock, size, deadline):
    data = bytearray()
    while len(data) < size:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("student deadline")
            sock.settimeout(remaining)
        block = sock.recv(size - len(data))
        if not block:
            raise EOFError("student pipe closed")
        data.extend(block)
    return bytes(data)


def receive(sock, deadline=None):
    size = struct.unpack("!I", _read(sock, 4, deadline))[0]
    if size > MAX_FRAME:
        raise ValueError("student frame exceeds size limit")
    return json.loads(_read(sock, size, deadline))


def send(sock, value, deadline=None):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > MAX_FRAME:
        raise ValueError("student frame exceeds size limit")
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("student deadline")
        sock.settimeout(remaining)
    sock.sendall(struct.pack("!I", len(raw)) + raw)


def spawn_service(python, role, *, own_group, scripts_root=None):
    source = Path(scripts_root or SCRIPTS).resolve()
    parent, child = socket.socketpair()
    # Model processes do not inherit teacher credentials, campaign paths or
    # private fixture/configuration from the worker's environment.
    allowed = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES")
    env = {k: os.environ[k] for k in allowed if k in os.environ}
    env.update(PYTHONPATH=str(source), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false",
               PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", VLLM_NO_USAGE_STATS="1")
    try:
        process = subprocess.Popen([os.path.abspath(python), "-m", "thinking_events.student_service",
                                    role, str(child.fileno())], cwd=str(source), env=env,
                                   pass_fds=(child.fileno(),), start_new_session=own_group,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    except BaseException:
        parent.close()
        raise
    finally:
        child.close()
    return process, parent


def _public_history(messages, tools):
    allowed = {"system": {"role", "content"}, "user": {"role", "content"},
               "assistant": {"role", "content", "reasoning_content", "tool_calls"},
               "tool": {"role", "content", "name", "tool_call_id"}}
    if not isinstance(messages, list) or not isinstance(tools, list):
        return False
    for msg in messages:
        if not isinstance(msg, dict) or not isinstance(msg.get("role"), str):
            return False
        if not set(msg) <= allowed.get(msg["role"], set()) or not isinstance(msg.get("content", ""), (str, type(None))):
            return False
        if msg.get("reasoning_content") is not None and not isinstance(msg["reasoning_content"], str):
            return False
        calls = msg.get("tool_calls") or []
        if not isinstance(calls, list):
            return False
        for call in calls:
            if not isinstance(call, dict) or not set(call) <= {"type", "id", "function"}:
                return False
            if not isinstance(call.get("function"), dict) or set(call["function"]) != {"name", "arguments"}:
                return False
    return all(isinstance(tool, dict) and set(tool) == {"type", "function"} and
               isinstance(tool["function"], dict) and
               set(tool["function"]) <= {"name", "description", "parameters", "strict"} for tool in tools)


def stopped(reason):
    return {"status": "stopped", "stop_reason": reason, "message": None, "usage": None,
            "generated_ids": None, "input_ids": None, "peak_vram_bytes": None, "peak_allocated_bytes": None,
            "timing": absent_timing(reason)}


class StudentProcess:
    """One fully initialized tokenizer/model pair, serving one request at a time.

    A fixture loads the indicated model's tokenizer, without its weights.
    """
    def __init__(self, python, *, backend="fixture", model="1.7B", fixture_replies=None,
                 model_path=None, revision=None, model_manifest=None, tokenizer_path=None,
                 backend_python=None, startup_timeout=90, scripts_root=None):
        if backend not in {"fixture", "transformers", "vllm"}:
            raise ValueError("unsupported student backend")
        if model not in {"1.7B", "4B", "Qwen3.5-2B", "Qwen3-4B"} or (backend == "fixture" and model == "4B"):
            raise ValueError("unsupported model for this backend")
        if backend != "fixture" and (not model_path or fixture_replies is not None or not model_manifest):
            raise ValueError("real generation requires a verified local model inventory")
        if backend == "vllm" and not backend_python:
            raise ValueError("supply a dedicated pinned vLLM interpreter")
        deadline = time.monotonic() + startup_timeout
        self._lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = False
        self.process, self.sock = spawn_service(python, "render", own_group=True, scripts_root=scripts_root)
        try:
            config = {"backend": backend, "model": model, "revision": revision,
                      "model_path": str(Path(model_path).resolve()) if model_path else None,
                      "model_manifest": model_manifest, "fixture_replies": fixture_replies,
                      "tokenizer_path": str(Path(tokenizer_path).resolve()) if tokenizer_path else None,
                      "backend_python": os.path.abspath(backend_python) if backend_python else None}
            send(self.sock, config, deadline)
            ready = receive(self.sock, deadline)
            if ready.get("status") != "ready":
                raise RuntimeError(f"student startup failed: {ready.get('error')}")
            self.metadata = ready["metadata"]
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def cancel(self):
        self.close()

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                # Reap the renderer and any inference subprocesses including vLLM's
                # engine, before a subsequent slot may start.
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.sock.close()
                self.process.wait()

    def request(self, *, slot, turn, messages, tools, thinking, seed, spent_tokens,
                episode_deadline, request_timeout=120):
        if not isinstance(slot, str) or not slot or type(turn) is not int or turn < 1:
            raise ValueError("invalid slot or turn")
        if type(spent_tokens) is not int or spent_tokens < 0 or type(thinking) is not bool:
            raise ValueError("invalid student budget or mode")
        if type(seed) is not int or not 0 <= seed < 2**32:
            raise ValueError("invalid generation seed")
        if not math.isfinite(episode_deadline) or not math.isfinite(request_timeout) or request_timeout <= 0:
            raise ValueError("invalid request deadline")
        with self._lock:
            identity = {"slot": slot, "turn": turn, "request_id": uuid.uuid4().hex}
            if self._closed:
                return {**identity, **stopped("cancelled")}
            if not _public_history(messages, tools):
                return {**identity, **stopped("input_rejected"), "error": "nonpublic_request_fields"}
            now = time.monotonic()
            if now >= episode_deadline:
                return {**identity, **stopped("episode_deadline")}
            if turn > 12:
                return {**identity, **stopped("request_limit")}
            request_end = now + min(request_timeout, 120)
            deadline = min(request_end, episode_deadline)
            timeout_reason = "episode_deadline" if episode_deadline <= request_end else "request_timeout"
            request = {**identity, "messages": messages, "tools": tools, "thinking": thinking,
                       "seed": seed, "spent_tokens": spent_tokens, "started": now, "deadline": deadline}
            try:
                send(self.sock, request, deadline)
                response = receive(self.sock, deadline)
                if any(response.get(k) != val for k, val in identity.items()):
                    self.cancel()
                    return {**identity, **stopped("response_identity_mismatch")}
                if time.monotonic() >= deadline:
                    raise TimeoutError("late student reply")
                if response.get("stop_reason") == "student_backend_error":
                    self.cancel()
                response["request_seconds"] = time.monotonic() - now
                return response
            except TimeoutError:
                self.cancel()
                return {**identity, **stopped(timeout_reason), "request_seconds": time.monotonic() - now}
            except (EOFError, OSError, ValueError):
                reason = "cancelled" if self._closed else "student_transport_error"
                self.cancel()
                return {**identity, **stopped(reason)}
