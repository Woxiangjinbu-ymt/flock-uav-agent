"""Offline telemetry tests; HTTPX MockTransport never opens a socket."""
import asyncio
import importlib.util
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reproduction_metrics as m


class MetricsTests(unittest.TestCase):
    def test_late_sampler_cannot_change_sealed_bytes(self):
        # Reproduce the production order: background ps blocks, stop's join
        # expires, scene is sealed, then the old sample finally returns.
        entered, release = threading.Event(), threading.Event()
        calls = []
        def sample(_pids):
            calls.append(threading.current_thread().name)
            if len(calls) > 1:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test did not release sampler")
            return {}
        with tempfile.TemporaryDirectory() as td, patch.object(m, "process_tree_sample", side_effect=sample):
            path = Path(td) / m.METRICS_FILENAME
            monitor = m.ResourceMonitor(path, interval=.1).start()
            try:
                self.assertTrue(entered.wait(2))
                monitor.stop(join_timeout=.01)
                before = path.read_bytes()
                sealed = hashlib.sha256(before).hexdigest()
                self.assertTrue(monitor._thread.is_alive())
                # No second, potentially blocking final sample on this path.
                self.assertEqual(len(calls), 2)
                events = [json.loads(line) for line in before.splitlines()]
                self.assertIn("ResourceMonitorStopTimeout", [e.get("error_type") for e in events])
                release.set()
                monitor._thread.join(timeout=2)
                self.assertFalse(monitor._thread.is_alive())
                monitor.stop()
                monitor.sample()
                monitor.writer.safe_emit("resource_error", error_type="LateFailure")
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), sealed)
                with self.assertRaises(RuntimeError):
                    monitor.start()
            finally:
                release.set()
                monitor._thread.join(timeout=2)

    def test_close_waits_for_inflight_write_then_rejects_future_writes(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        real_dumps = json.dumps
        def slow_dumps(value, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release write")
            return real_dumps(value, **kwargs)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / m.METRICS_FILENAME
            writer = m.EventWriter(path)
            with patch.object(m.json, "dumps", side_effect=slow_dumps):
                emit = threading.Thread(target=lambda: writer.emit("resource_sample"))
                closer = threading.Thread(target=lambda: (writer.close(), closed.set()))
                emit.start()
                try:
                    self.assertTrue(entered.wait(2))
                    closer.start()
                    self.assertFalse(closed.wait(.02))
                    release.set()
                    emit.join(2); closer.join(2)
                    self.assertTrue(closed.is_set())
                finally:
                    release.set()
                    emit.join(2)
                    if closer.ident is not None: closer.join(2)
            before = path.read_bytes()
            writer.emit("late_sample")
            writer.close()
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(before.splitlines()), 1)

    def test_normal_stop_keeps_final_sample_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td, patch.object(m, "process_tree_sample", return_value={}) as sample:
            path = Path(td) / m.METRICS_FILENAME
            monitor = m.ResourceMonitor(path, interval=60).start()
            monitor.stop()
            before = path.read_bytes()
            self.assertEqual(sample.call_count, 2)
            self.assertEqual(json.loads(before.splitlines()[-1])["sample_thread_exited"], True)
            monitor.stop()
            self.assertEqual(path.read_bytes(), before)

    @unittest.skipUnless(importlib.util.find_spec("httpx2") and importlib.util.find_spec("langchain_openai"), "Requires the production SDK stack")
    def test_production_default_httpx2_transport_is_observed(self):
        import httpx2
        from langchain_core.callbacks import BaseCallbackHandler
        from langchain_openai import ChatOpenAI
        class ProviderCallback(BaseCallbackHandler):
            def __init__(self, command_id, provider, model):
                self.command_id, self.provider, self.model = command_id, provider, model
            def on_llm_end(self, response, **kwargs):
                pass
        module = SimpleNamespace(ProviderTokenLoggingCallback=ProviderCallback)
        body = {"id": "offline", "object": "chat.completion", "created": 0, "model": "test",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "SECRET_RESPONSE"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
                          "prompt_cache_hit_tokens": 8, "prompt_cache_miss_tokens": 2}}
        # Use the same default client construction as production. Stub the
        # transport's network boundary, rather than supply a legacy httpx client.
        llm = ChatOpenAI(model="test", api_key="offline-fake-key", base_url="https://model.invalid")
        self.assertIsInstance(llm.root_client._client, httpx2.Client)
        def process(job, command):
            callback = module.ProviderTokenLoggingCallback("command", "openai-compatible", "test")
            llm.invoke(command, config={"callbacks": [callback]})
        service = SimpleNamespace(process_agent_command=process)
        original_send = httpx2.Client.send
        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, uav_agent=module), \
                patch.object(httpx2.HTTPTransport, "handle_request", return_value=httpx2.Response(200, json=body)) as network:
            path = Path(td) / m.METRICS_FILENAME
            undo = m.install_agent_metrics(service, path, "https://model.invalid")
            try:
                service.process_agent_command("job", "SECRET_PROMPT")
            finally:
                undo()
            report = m.summarize_performance(td)
            self.assertEqual(network.call_count, 1)
            self.assertEqual(report["llm"]["completed_calls"], 1)
            self.assertEqual(report["http"]["attempts"], 1)
            self.assertEqual(report["http"]["status_counts"], {"200": 1})
            self.assertEqual(report["http"]["retry_metadata_available"], 1)
            self.assertEqual(report["llm"]["usage"]["total_tokens"], 12)
            self.assertEqual(report["llm"]["usage"]["cache_hit_tokens"], 8)
            self.assertNotIn("SECRET", path.read_text())
        self.assertIs(httpx2.Client.send, original_send)

    def test_usage_merges_cache_metadata_without_double_counting(self):
        response = SimpleNamespace(llm_output={"token_usage": {
            "prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}}, generations=[[
                SimpleNamespace(message=SimpleNamespace(
                    response_metadata={"token_usage": {"prompt_tokens": 12, "completion_tokens": 3,
                        "prompt_cache_hit_tokens": 9, "prompt_cache_miss_tokens": 3}},
                    usage_metadata={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}))]])
        self.assertEqual(m.result_usage(response), {"prompt_tokens": 12, "completion_tokens": 3,
            "total_tokens": 15, "cache_hit_tokens": 9, "cache_miss_tokens": 3})
        self.assertIsNone(m.extract_usage({"irrelevant": 123}))
        self.assertIsNone(m.extract_usage({"input_tokens": 2})["cache_hit_tokens"])

    @unittest.skipUnless(importlib.util.find_spec("httpx"), "Run HTTP instrumentation tests in agent4drone/.venv")
    def test_http_retries_usage_and_cancelled_callback_preserve_privacy(self):
        import httpx
        base_ends = []
        class Callback:
            def __init__(self, command_id, provider, model):
                self.command_id, self.provider, self.model = command_id, provider, model
            def on_llm_end(self, response, **kwargs):
                base_ends.append(response)
        module = SimpleNamespace(ProviderTokenLoggingCallback=Callback)
        service = SimpleNamespace(process_agent_command=None)
        run_id = uuid.uuid4()
        original_response = SimpleNamespace(llm_output={"token_usage": {
            "prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}}, generations=[])
        def execute(job, command):
            callback = module.ProviderTokenLoggingCallback("command-1", "openai-compatible", "fake-model")
            callback.on_chat_model_start({}, [["SECRET_PROMPT"]], run_id=run_id)
            responses = iter([httpx.Response(429), httpx.Response(503), httpx.Response(200, json={
                "choices": [{"message": {"content": "SECRET_RESPONSE"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15,
                          "prompt_cache_hit_tokens": 8, "prompt_cache_miss_tokens": 4}})])
            with httpx.Client(transport=httpx.MockTransport(lambda request: next(responses))) as client:
                for retry in range(3):
                    client.post("https://model.invalid/v1/chat/completions?secret=SECRET_QUERY",
                                headers={"Authorization": "Bearer SECRET_KEY", "x-stainless-retry-count": str(retry)},
                                json={"prompt": "SECRET_BODY"})
            callback.on_llm_end(original_response, run_id=run_id)
            # A downstream cancellation callback may raise after telemetry end.
            callback.on_llm_error(RuntimeError("SECRET_ERROR"), run_id=run_id)
            raise RuntimeError("simulated timeout cancellation")
        service.process_agent_command = execute
        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, uav_agent=module):
            path = Path(td) / m.METRICS_FILENAME
            undo = m.install_agent_metrics(service, path, "https://model.invalid/v1")
            try:
                with self.assertRaisesRegex(RuntimeError, "simulated timeout"):
                    service.process_agent_command("job-1", "SECRET_COMMAND")
            finally:
                undo()
            report = m.summarize_performance(td)
            self.assertEqual(report["llm"]["completed_calls"], 1)
            self.assertEqual(report["llm"]["failed_calls"], 0)
            self.assertEqual(report["llm"]["usage"]["total_tokens"], 15)
            self.assertEqual(report["llm"]["usage"]["cache_hit_tokens"], 8)
            self.assertEqual(report["http"]["attempts"], 3)
            self.assertEqual(report["http"]["retry_attempts"], 2)
            self.assertEqual(report["http"]["rate_limited_429"], 1)
            self.assertEqual(report["http"]["server_errors_5xx"], 1)
            self.assertIn("job-1", report["by_job"])
            self.assertNotIn("SECRET", path.read_text())
            self.assertIs(service.process_agent_command, execute)
            self.assertEqual(base_ends, [original_response])

    @unittest.skipUnless(importlib.util.find_spec("httpx"), "Run HTTP instrumentation tests in agent4drone/.venv")
    def test_async_error_and_non_provider_requests(self):
        import httpx
        class Callback:
            pass
        module = SimpleNamespace(ProviderTokenLoggingCallback=Callback)
        service = SimpleNamespace(process_agent_command=lambda job, command: None)
        async def exercise():
            def fail(request):
                raise httpx.ReadTimeout("secret error details")
            async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
                for url in ("https://model.invalid/v1/chat/completions", "http://127.0.0.1:8000/sessions"):
                    with self.assertRaises(httpx.ReadTimeout):
                        await client.post(url)
        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, uav_agent=module):
            undo = m.install_agent_metrics(service, Path(td) / m.METRICS_FILENAME, "https://model.invalid/v1")
            try:
                asyncio.run(exercise())
            finally:
                undo()
            report = m.summarize_performance(td)
            self.assertEqual(report["http"]["attempts"], 1)
            self.assertEqual(report["http"]["errors"], 1)
            self.assertEqual(report["http"]["unfinished_attempts"], 0)

    @unittest.skipUnless(importlib.util.find_spec("langchain_openai"), "Run real callback integration in agent4drone/.venv")
    def test_real_langchain_sdk_cancellation_keeps_completed_usage(self):
        import httpx
        from langchain_core.callbacks import BaseCallbackHandler
        from langchain_openai import ChatOpenAI
        class ProviderCallback(BaseCallbackHandler):
            def __init__(self, command_id, provider, model):
                self.command_id, self.provider, self.model = command_id, provider, model
            def on_llm_end(self, response, **kwargs):
                pass
        class Cancellation(BaseCallbackHandler):
            raise_error = True
            run_inline = True
            def on_llm_end(self, response, **kwargs):
                raise RuntimeError("cancel after completed response")
        body = {"id": "offline-response", "object": "chat.completion", "created": 0, "model": "test",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "SECRET_RESPONSE"}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24,
                          "prompt_cache_hit_tokens": 15, "prompt_cache_miss_tokens": 5}}
        module = SimpleNamespace(ProviderTokenLoggingCallback=ProviderCallback)
        with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))) as client:
            llm = ChatOpenAI(model="test", api_key="offline-fake-key", base_url="https://model.invalid/v1", http_client=client)
            def process(job, command):
                callback = module.ProviderTokenLoggingCallback("command", "openai-compatible", "test")
                llm.invoke(command, config={"callbacks": [callback, Cancellation()]})
            service = SimpleNamespace(process_agent_command=process)
            with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, uav_agent=module):
                path = Path(td) / m.METRICS_FILENAME
                undo = m.install_agent_metrics(service, path, "https://model.invalid/v1")
                try:
                    with self.assertRaisesRegex(RuntimeError, "cancel after"):
                        service.process_agent_command("job", "SECRET_PROMPT")
                finally:
                    undo()
                report = m.summarize_performance(td)
                self.assertEqual(report["llm"]["completed_calls"], 1)
                self.assertEqual(report["llm"]["failed_calls"], 0)
                self.assertEqual(report["llm"]["usage"]["total_tokens"], 24)
                self.assertEqual(report["llm"]["usage"]["cache_hit_tokens"], 15)
                self.assertEqual(report["http"]["attempts"], 1)
                self.assertEqual(report["http"]["retry_metadata_available"], 1)
                self.assertNotIn("SECRET", path.read_text())

    def test_usage_recovered_without_end_callback_and_duplicates_not_counted(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / m.METRICS_FILENAME
            writer = m.EventWriter(path)
            writer.emit("llm_start", run_id="run", job_id="job")
            writer.emit("http_start", run_id="run", job_id="job", request_id="request", retry_index=0)
            writer.emit("http_end", run_id="run", job_id="job", request_id="request", status_code=200,
                        duration_seconds=7, usage=m.extract_usage({"prompt_tokens": 100, "completion_tokens": 5}))
            report = m.summarize_performance([td, path])
            self.assertEqual(report["files"], 1)
            self.assertEqual(report["llm"]["unfinished_calls"], 1)
            self.assertEqual(report["llm"]["http_usage_without_success_callback"], 1)
            self.assertEqual(report["llm"]["usage"]["total_tokens"], 105)

    def test_summary_distinguishes_unknown_zero_and_incomplete_records(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / m.METRICS_FILENAME
            writer = m.EventWriter(path)
            writer.emit("llm_start", run_id="run", job_id="job")
            writer.emit("llm_error", run_id="run", job_id="job", duration_seconds=4, error_type="ReadTimeout")
            with path.open("a") as stream:
                stream.write('{"schema_version": 1, "partial')
            report = m.summarize_performance(td)
            self.assertEqual(report["malformed_records"], 1)
            self.assertEqual(report["llm"]["failed_calls"], 1)
            self.assertIsNone(report["llm"]["usage"]["total_tokens"])
            self.assertIsNone(report["resources"]["peak_sampled_tree_rss_kib"])

    def test_process_tree_and_resource_sampling(self):
        ps = SimpleNamespace(stdout="10 1 100 00:02.00\n11 10 200 00:01.20\n12 11 300 1-01:02:03\n99 1 9999 00:10.00\n")
        with patch.object(m.subprocess, "run", return_value=ps):
            rows = m.process_tree_sample([10])
        self.assertEqual(set(rows), {10, 11, 12})
        self.assertEqual(rows[12]["cpu_seconds"], 90123)
        samples = [{10: {"rss_kib": 100, "cpu_seconds": 2}},
                   {10: {"rss_kib": 200, "cpu_seconds": 3}}, {}]
        with tempfile.TemporaryDirectory() as td, patch.object(m, "process_tree_sample", side_effect=samples):
            monitor = m.ResourceMonitor(Path(td) / m.METRICS_FILENAME, pids=[10])
            monitor.sample()
            monitor.sample()
            monitor.stop()
            report = m.summarize_performance(td)
        self.assertEqual(report["resources"]["peak_sampled_tree_rss_kib"], 200)
        self.assertEqual(report["resources"]["cpu_seconds_observed"], 1)

    def test_legacy_fallback_is_explicit_and_never_added_to_new_telemetry(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "agent.log").write_text(
                "Provider LLM call completed command_id=x provider=p model=m "
                "prompt_tokens=100 completion_tokens=7 total_tokens=107 llm_calls=1\n")
            self.assertIsNone(m.summarize_performance(td)["llm"]["usage"]["total_tokens"])
            legacy = m.summarize_performance(td, include_legacy=True)
            self.assertEqual(legacy["llm"]["usage"]["total_tokens"], 107)
            m.EventWriter(Path(td) / m.METRICS_FILENAME).emit("monitor_started", component="agent")
            self.assertEqual(m.summarize_performance(td, include_legacy=True)["source"], "telemetry")

    def test_empty_roots_and_percentile_distribution(self):
        self.assertEqual(m.summarize_performance([])["files"], 0)
        latency = m._latencies([1, 2, 3, 4, 10])
        self.assertEqual(latency["p50_seconds"], 3)
        self.assertAlmostEqual(latency["p95_seconds"], 8.8)


if __name__ == "__main__":
    unittest.main()
