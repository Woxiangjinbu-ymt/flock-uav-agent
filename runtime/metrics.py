"""Harness telemetry; never stores prompts, response text, keys, or URL queries.

One agent process serves one scene and one job at a time. HTTP attempts are
observed separately from logical model calls, so SDK retries and calls finishing
after cancellation remain visible. None of these hooks alter model arguments.
"""
from __future__ import annotations

from collections import Counter
import contextlib
import importlib
import json
import logging
from pathlib import Path
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

METRICS_FILENAME = "reproduction-metrics.jsonl"
SCHEMA_VERSION = 1


class EventWriter:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, event, **values):
        record = {"schema_version": SCHEMA_VERSION, "event": event,
                  "timestamp": time.time(), **values}
        with self._lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")

    def safe_emit(self, event, **values):
        # A disk failure must not change the agent's decision/cancellation path.
        try:
            self.emit(event, **values)
        except Exception as exc:
            logging.getLogger(__name__).error("Telemetry write failed (%s)", type(exc).__name__)


def _number(value):
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def extract_usage(value):
    """Normalize only reported numeric usage; missing cache data stays unknown."""
    if not isinstance(value, dict):
        return None
    prompt = _number(value.get("prompt_tokens", value.get("input_tokens")))
    completion = _number(value.get("completion_tokens", value.get("output_tokens")))
    total = _number(value.get("total_tokens"))
    if prompt is None and completion is None and total is None:
        return None
    details = value.get("prompt_tokens_details") or value.get("input_token_details") or {}
    if not isinstance(details, dict):
        details = {}
    hit = _number(value.get("prompt_cache_hit_tokens", details.get("cached_tokens", details.get("cache_read"))))
    miss = _number(value.get("prompt_cache_miss_tokens"))
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": total if total is not None else (prompt or 0) + (completion or 0),
            "cache_hit_tokens": hit, "cache_miss_tokens": miss}


def result_usage(response):
    output = getattr(response, "llm_output", None) or {}
    if not isinstance(output, dict):
        output = {}
    usages = [extract_usage(output.get("token_usage")), extract_usage(output.get("usage"))]
    for group in getattr(response, "generations", []) or []:
        for generation in group or []:
            message = getattr(generation, "message", None)
            metadata = getattr(message, "response_metadata", None) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            usages.extend([extract_usage(metadata.get("token_usage")),
                           extract_usage(getattr(message, "usage_metadata", None))])
    usages = [usage for usage in usages if usage]
    if not usages:
        return None
    # LLMResult and AIMessage commonly contain the same usage: do not add them.
    result = dict(usages[0])
    for usage in usages[1:]:
        for key in result:
            if result[key] is None and usage[key] is not None:
                result[key] = usage[key]
    return result


def install_agent_metrics(service, metrics_path, provider_base_url):
    """Install after reproduction_lifecycle.install; return a test-only undo hook.

    The Provider callback is first in upstream's callback list and run_inline
    ensures it records an end before the later cancellation callback can raise.
    HTTPX observation independently records transport errors and SDK retries.
    """
    import httpx
    from flockcue import agent as uav_agent

    writer = EventWriter(metrics_path)
    writer.emit("monitor_started", component="agent")
    original_callback = uav_agent.ProviderTokenLoggingCallback
    original_process = service.process_agent_command
    transports = [httpx]
    # Newer OpenAI/LangChain defaults use httpx2; explicit legacy clients still
    # use httpx. Observe either without injecting a different client/transport.
    with contextlib.suppress(ImportError):
        transports.append(importlib.import_module("httpx2"))
    originals = {cls: cls.send for module in transports for cls in (module.Client, module.AsyncClient)}
    active = {"job_id": None, "run_id": None}
    calls = {}
    completed_calls = set()
    lock = threading.RLock()
    parsed = urlsplit(provider_base_url)
    provider_origin = (parsed.scheme, parsed.hostname, parsed.port)
    provider_path = parsed.path.rstrip("/")

    def context():
        with lock:
            return dict(active)

    class MetricsCallback(original_callback):
        run_inline = True

        def _start(self, run_id):
            rid = str(run_id)
            with lock:
                if rid in calls:
                    return
                active["run_id"] = rid
                calls[rid] = (time.monotonic(), active["job_id"])
                job_id = active["job_id"]
            writer.safe_emit("llm_start", run_id=rid, job_id=job_id,
                             command_id=self.command_id, provider=self.provider, model=self.model)

        def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
            self._start(run_id)

        def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
            self._start(run_id)

        def _end(self, run_id, event, **values):
            rid = str(run_id)
            with lock:
                # Cancellation can raise in the following callback after our
                # successful end was recorded. That is not a provider failure.
                if rid in completed_calls:
                    return
                started, job_id = calls.pop(rid, (None, active["job_id"]))
                completed_calls.add(rid)
                if active["run_id"] == rid:
                    active["run_id"] = None
            writer.safe_emit(event, run_id=rid, job_id=job_id, command_id=self.command_id,
                             provider=self.provider, model=self.model,
                             duration_seconds=time.monotonic() - started if started is not None else None,
                             **values)

        def on_llm_end(self, response, *, run_id, **kwargs):
            self._end(run_id, "llm_end", usage=result_usage(response))
            return super().on_llm_end(response, run_id=run_id, **kwargs)

        def on_llm_error(self, error, *, run_id, **kwargs):
            self._end(run_id, "llm_error", error_type=type(error).__name__,
                      status_code=_number(getattr(error, "status_code", None)))

    def process(job_id, command):
        started = time.monotonic()
        with lock:
            active.update(job_id=str(job_id), run_id=None)
        writer.safe_emit("job_start", job_id=str(job_id))
        try:
            return original_process(job_id, command)
        finally:
            writer.safe_emit("job_end", job_id=str(job_id), duration_seconds=time.monotonic() - started)
            with lock:
                active.update(job_id=None, run_id=None)

    def begin(request):
        url = request.url
        origin = (url.scheme, url.host, url.port)
        if origin != provider_origin or not (url.path == provider_path or url.path.startswith(provider_path + "/")):
            return None
        call = {**context(), "request_id": uuid.uuid4().hex,
                "retry_index": _number(request.headers.get("x-stainless-retry-count"))}
        writer.safe_emit("http_start", **call)
        return call, time.monotonic()

    def end(observation, response=None, error=None):
        if observation is None:
            return
        call, started = observation
        values = {**call, "duration_seconds": time.monotonic() - started}
        if error is not None:
            writer.safe_emit("http_error", **values, error_type=type(error).__name__)
            return
        usage = None
        # Inspect only an already-buffered response. Never consume a stream or
        # change when its body is read by the SDK.
        if response.is_stream_consumed:
            with contextlib.suppress(Exception):
                body = response.json()
                if isinstance(body, dict):
                    usage = extract_usage(body.get("usage"))
        writer.safe_emit("http_end", **values, status_code=response.status_code, usage=usage)

    def wrap_send(original):
        def send(client, request, *args, **kwargs):
            observation = begin(request)
            try:
                response = original(client, request, *args, **kwargs)
            except BaseException as exc:
                end(observation, error=exc)
                raise
            end(observation, response=response)
            return response
        return send

    def wrap_async_send(original):
        async def asend(client, request, *args, **kwargs):
            observation = begin(request)
            try:
                response = await original(client, request, *args, **kwargs)
            except BaseException as exc:
                end(observation, error=exc)
                raise
            end(observation, response=response)
            return response
        return asend

    uav_agent.ProviderTokenLoggingCallback = MetricsCallback
    service.process_agent_command = process
    for module in transports:
        module.Client.send = wrap_send(originals[module.Client])
        module.AsyncClient.send = wrap_async_send(originals[module.AsyncClient])

    def uninstall():
        uav_agent.ProviderTokenLoggingCallback = original_callback
        service.process_agent_command = original_process
        for cls, original in originals.items():
            cls.send = original

    return uninstall


def _cpu_seconds(value):
    days, _, clock = value.rpartition("-")
    parts = [float(part) for part in clock.split(":")]
    return (int(days) * 86400 if days else 0) + sum(v * 60 ** i for i, v in enumerate(reversed(parts)))


def process_tree_sample(pids):
    """Sample RSS (KiB) and cumulative CPU seconds via macOS/Linux ps."""
    result = subprocess.run(["ps", "-axo", "pid=,ppid=,rss=,time="], capture_output=True,
                            text=True, timeout=5, check=True)
    processes = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        with contextlib.suppress(ValueError):
            pid, parent, rss = map(int, fields[:3])
            processes[pid] = {"parent": parent, "rss_kib": rss, "cpu_seconds": _cpu_seconds(fields[3])}
    selected = set(int(pid) for pid in pids)
    while True:
        children = {pid for pid, row in processes.items() if row["parent"] in selected}
        if children.issubset(selected):
            break
        selected.update(children)
    return {pid: processes[pid] for pid in selected if pid in processes}


class ResourceMonitor:
    """One monitor per worker OR one for all workers (do not sum both levels).

    RSS is a sampled sum across live process trees, not a physical-memory claim;
    shared pages may be counted more than once. CPU deltas omit work performed
    by a process that starts and exits entirely between samples.
    """
    def __init__(self, path, pids=(), interval=1.0):
        self.writer = EventWriter(path)
        self.pids = set(pids)
        self.interval = max(0.1, float(interval))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._last_cpu = {}
        self._cpu = 0.0
        self._last_time = time.monotonic()

    def add_pids(self, *pids):
        with self._lock:
            self.pids.update(int(pid) for pid in pids)

    def remove_pids(self, *pids):
        with self._lock:
            self.pids.difference_update(int(pid) for pid in pids)

    def sample(self):
        with self._lock:
            roots = set(self.pids)
        try:
            rows = process_tree_sample(roots)
            now = time.monotonic()
            # The first observation sets the baseline rather than charging
            # processes' CPU use before the monitor was attached.
            delta = sum(max(0, row["cpu_seconds"] - self._last_cpu.get(pid, row["cpu_seconds"]))
                        for pid, row in rows.items())
            self._cpu += delta
            elapsed = now - self._last_time
            self.writer.safe_emit("resource_sample", pids=sorted(rows),
                                  rss_kib=sum(row["rss_kib"] for row in rows.values()),
                                  cpu_seconds_observed=self._cpu,
                                  cpu_percent=100 * delta / elapsed if elapsed else 0,
                                  root_pids=sorted(roots))
            self._last_cpu = {pid: row["cpu_seconds"] for pid, row in rows.items()}
            self._last_time = now
        except Exception as exc:
            self.writer.safe_emit("resource_error", error_type=type(exc).__name__)

    def start(self):
        if self._thread is not None:
            raise RuntimeError("Monitor already started")
        self.writer.emit("monitor_started", component="resources", sample_interval_seconds=self.interval)
        self.sample()

        def loop():
            while not self._stop.wait(self.interval):
                self.sample()

        self._thread = threading.Thread(target=loop, name="reproduction-resources", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=6)
        self.sample()


def _latencies(values):
    values = sorted(v for v in values if isinstance(v, (int, float)))
    def percentile(fraction):
        if not values:
            return None
        index = (len(values) - 1) * fraction
        lower = int(index)
        upper = min(lower + 1, len(values) - 1)
        return values[lower] + (values[upper] - values[lower]) * (index - lower)
    return {"count": len(values), "mean_seconds": sum(values) / len(values) if values else None,
            "p50_seconds": percentile(.5), "p95_seconds": percentile(.95),
            "max_seconds": values[-1] if values else None}


def _usage_totals(usages):
    usages = [usage for usage in usages if usage]
    keys = ("prompt_tokens", "completion_tokens", "total_tokens", "cache_hit_tokens", "cache_miss_tokens")
    result = {key: sum(u[key] for u in usages if u.get(key) is not None)
              if any(u.get(key) is not None for u in usages) else None for key in keys}
    result["calls_with_usage"] = len(usages)
    result["calls_with_cache_usage"] = sum(u.get("cache_hit_tokens") is not None for u in usages)
    return result


def _summarize_events(events):
    starts = {e["run_id"] for e in events if e["event"] == "llm_start"}
    ends = {e["run_id"]: e for e in events if e["event"] in ("llm_end", "llm_error")}
    http_ends = [e for e in events if e["event"] == "http_end"]
    resources = [e for e in events if e["event"] == "resource_sample"]
    usages = []
    recovered = 0
    # HTTP usage wins for a logical call when available: DeepSeek cache fields
    # may be discarded by LangChain. Count each HTTP response exactly once,
    # including multiple billed responses inside one logical SDK invocation.
    http_usage_by_run = {}
    for event in http_ends:
        if event.get("usage"):
            http_usage_by_run.setdefault(event.get("run_id"), []).append(event["usage"])
    for run_id in starts | set(ends) | set(http_usage_by_run):
        if run_id in http_usage_by_run:
            usages.extend(http_usage_by_run[run_id])
            if run_id not in ends or ends[run_id]["event"] != "llm_end":
                recovered += len(http_usage_by_run[run_id])
        elif ends.get(run_id, {}).get("usage"):
            usages.append(ends[run_id]["usage"])
    status = Counter(str(e["status_code"]) for e in http_ends)
    http_starts = [e for e in events if e["event"] == "http_start"]
    http_errors = [e for e in events if e["event"] == "http_error"]
    completed_http = {e["request_id"] for e in http_ends + http_errors}
    cpu_by_source = {}
    for event in resources:
        cpu_by_source[event.get("_source", "")] = max(cpu_by_source.get(event.get("_source", ""), 0), event["cpu_seconds_observed"])
    return {
        "llm": {"started_calls": len(starts),
                "completed_calls": sum(e["event"] == "llm_end" for e in ends.values()),
                "failed_calls": sum(e["event"] == "llm_error" for e in ends.values()),
                "unfinished_calls": len(starts - set(ends)),
                "latency": _latencies(e.get("duration_seconds") for e in ends.values()),
                "usage": _usage_totals(usages), "http_usage_without_success_callback": recovered},
        "http": {"attempts": len(http_starts), "responses": len(http_ends),
                 "errors": len(http_errors), "status_counts": dict(status),
                 "rate_limited_429": status.get("429", 0),
                 "server_errors_5xx": sum(v for k, v in status.items() if k.startswith("5")),
                 "retry_attempts": sum((e.get("retry_index") or 0) > 0 for e in http_starts),
                 "retry_metadata_available": sum(e.get("retry_index") is not None for e in http_starts),
                 "unfinished_attempts": sum(e["request_id"] not in completed_http for e in http_starts),
                 "latency": _latencies(e.get("duration_seconds") for e in http_ends + http_errors)},
        "resources": {"samples": len(resources),
                      "peak_sampled_tree_rss_kib": max((e["rss_kib"] for e in resources), default=None),
                      "peak_sampled_tree_cpu_percent": max((e["cpu_percent"] for e in resources), default=None),
                      "cpu_seconds_observed": sum(cpu_by_source.values()) if resources else None,
                      "errors": sum(e["event"] == "resource_error" for e in events)},
    }


def summarize_performance(root):
    """Aggregate telemetry under an attempt or scene. Does not change any files.

    Pass a JSONL file, directory, or list of either. A parallel runner should point at
    its selected scene generations, not directories containing superseded
    retries, when computing scored-only resource use. A whole attempt naturally
    includes the cost of abandoned generations as well.
    """
    roots = [Path(item) for item in root] if isinstance(root, (list, tuple, set)) else [Path(root)]
    files = sorted({path.resolve() for item in roots
                    for path in ([item] if item.is_file() else item.rglob(METRICS_FILENAME))})
    events, malformed = [], 0
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
                if not isinstance(event, dict) or event.get("schema_version") != SCHEMA_VERSION or "event" not in event:
                    raise ValueError("Invalid telemetry record")
                event["_source"] = str(path)
                events.append(event)
            except (ValueError, TypeError):
                malformed += 1
    result = {"schema_version": SCHEMA_VERSION, "source": "telemetry", "files": len(files),
              "malformed_records": malformed, **_summarize_events(events)}
    jobs = {}
    for event in events:
        if event.get("job_id"):
            jobs.setdefault(event["job_id"], []).append(event)
    result["by_job"] = {job: _summarize_events(group) for job, group in sorted(jobs.items())}
    result["models"] = sorted({e["model"] for e in events if e.get("model")})
    result["resource_scope_note"] = "Peak is the largest sampled monitored process tree; CPU percent uses 100% per core. For simultaneous fleet RSS, attach one fleet-level monitor."
    return result
