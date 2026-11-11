"""Pressure-only engine. The isolated worker has no simulator or tool dispatcher."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time

from thinking_events.student_process import receive, send
from thinking_events.student_timing import generation_stats, vllm_timestamps
from thinking_events import student


def engine_child(fd):
    from thinking_events import student_service as engines
    with socket.socket(fileno=fd) as sock:
        try:
            config = receive(sock)
            kind, model = config["backend"], config["model"]
            engine_cls = engines.TransformersModel if kind == "transformers" else engines.VllmModel
            engine = engine_cls(config["path"], model)
            if kind == "transformers":
                engine.model.generation_config.suppress_tokens = [config["close_id"], *config["eos"]]
                engine.model.generation_config.min_new_tokens = student.MAX_GENERATION
            send(sock, {"ready": True, "device": engines.device_info(engine)})
            while True:
                request = receive(sock)
                ids, seed, eos = request["ids"], request["seed"], config["eos"]
                if kind == "transformers":
                    # Preserve every delivered ID even when the parent's watchdog
                    # kills the worker. The small unbuffered file is local only.
                    original = engines.TokenTimes
                    with open(request["partial"], "wb", buffering=0) as stream:
                        class PressureClock(original):
                            def put(self, value):
                                super().put(value)
                                if self.count:
                                    stream.write(struct.pack("<I", value.item()))
                        engines.TokenTimes = PressureClock
                        try:
                            result = engine.generate(ids, student.MAX_GENERATION, seed, eos)
                        finally:
                            engines.TokenTimes = original
                else:
                    from vllm import SamplingParams
                    params = SamplingParams(max_tokens=student.MAX_GENERATION, min_tokens=student.MAX_GENERATION,
                                            bad_words=["</think>"], seed=seed, stop_token_ids=eos, detokenize=False,
                                            **student.SAMPLING)
                    started = time.monotonic()
                    (result,) = engine.model.generate([{"prompt_token_ids": ids}], params, use_tqdm=False)
                    if list(result.prompt_token_ids) != ids:
                        raise ValueError("vLLM changed the pressure prompt")
                    finished = time.monotonic()
                    first, last = vllm_timestamps(getattr(result, "metrics", None), started, finished)
                    result = generation_stats(list(result.outputs[0].token_ids), started, finished,
                                              source="vllm_request_metrics", first=first, last=last,
                                              unavailable_reason="vllm_token_timestamps_unavailable")
                send(sock, {"ok": True, **result})
        except (EOFError, BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            send(sock, {"error": f"{type(exc).__name__}: {exc}"})


class StressProcess:
    def __init__(self, python, scripts_root, config):
        self.sock, child = socket.socketpair()
        allowed = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES")
        env = {k: os.environ[k] for k in allowed if k in os.environ}
        env.update(PYTHONPATH=str(scripts_root), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                   PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", TOKENIZERS_PARALLELISM="false",
                   HF_HUB_DISABLE_TELEMETRY="1", VLLM_NO_USAGE_STATS="1")
        try:
            self.process = subprocess.Popen([os.path.abspath(python), str(Path(__file__).resolve()), str(child.fileno())],
                                            pass_fds=(child.fileno(),), cwd=scripts_root, env=env,
                                            start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        finally:
            child.close()
        try:
            deadline = time.monotonic() + 180
            send(self.sock, config, deadline)
            ready = receive(self.sock, deadline)
            if not ready.get("ready"):
                raise RuntimeError(f"Stress engine startup failed: {ready}")
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.sock.close()
        self.process.wait()


if __name__ == "__main__":
    engine_child(int(sys.argv[1]))
