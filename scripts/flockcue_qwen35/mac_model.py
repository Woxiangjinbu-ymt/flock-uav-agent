"""Text-only Metal inference for the pinned Qwen3.5-2B checkpoint on a Mac."""
from collections import Counter
from contextlib import closing
import hashlib
from importlib.metadata import version
import math
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import uuid

from thinking_events.student import MAX_CONTEXT, MAX_GENERATION, SAMPLING
from thinking_events.student_process import receive, send
from thinking_events.student_timing import generation_stats

from .adapter import EOS_IDS, FILES, REVISION

# HF LFS SHA-256 from the Qwen/Qwen3.5-2B tree at REVISION.
WEIGHT_FILE = "model.safetensors-00001-of-00001.safetensors"
WEIGHT_BYTES = 4548221488
WEIGHT_SHA256 = "aa33250c4fc64891ddfaba3a314fd9542ea371843c387178b425fbcc5ed680b1"
PREFILL_STEP = 512


class MacModel:
    """One warm engine. Only token IDs and generation parameters cross the socket."""

    def __init__(self, model_python, weights_dir, *, scripts_root=None, startup_timeout=180):
        source = Path(scripts_root or Path(__file__).resolve().parents[1]).resolve()
        env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL") if k in os.environ}
        env.update(PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
                   HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
        cmd = [os.path.abspath(model_python), "-m", "flockcue_qwen35.mac_model"]
        self.sock, child_sock = socket.socketpair()
        self._request_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = False
        with child_sock:
            fd = child_sock.fileno()
            try:
                self.process = subprocess.Popen([*cmd, str(fd), str(Path(weights_dir).resolve())], cwd=source,
                                                env=env, pass_fds=(fd,), stdin=subprocess.DEVNULL)
            except BaseException:
                self.sock.close()
                raise
        try:
            ready = receive(self.sock, time.monotonic() + startup_timeout)
            if ready.get("status") != "ready":
                raise RuntimeError(f"Mac model failed to load: {ready.get('error')}")
            self.metadata = ready["metadata"]
            if self.metadata["pid"] != self.process.pid or self.metadata["weight_sha256"] != WEIGHT_SHA256:
                raise RuntimeError("Model process identity mismatch")
        except BaseException:
            self.cancel()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cancel()

    def cancel(self):
        # This engine is a single process. Metal kernels belong to that process.
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self.process.kill()
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
            self.process.wait()

    def generate(self, input_ids, limit, seed, deadline):
        if not math.isfinite(deadline):
            raise ValueError("A finite monotonic generation deadline is required")
        with self._request_lock:
            if self._closed:
                raise RuntimeError("Mac model process is closed")
            request_id = uuid.uuid4().hex
            try:
                send(self.sock, {"input_ids": input_ids, "limit": limit, "seed": seed,
                                 "request_id": request_id}, deadline)
                reply = receive(self.sock, deadline)
                if time.monotonic() >= deadline:
                    raise TimeoutError("Late model reply")
                if reply.get("request_id") != request_id:
                    raise RuntimeError("Wrong model reply ID")
                if "error" in reply:
                    raise RuntimeError(reply["error"])
                return reply["measurement"]
            except BaseException:
                self.cancel()
                raise


def load_engine(weights):
    import mlx.core as mx
    from mlx.utils import tree_flatten
    from mlx_lm.utils import load_model

    shard = weights / WEIGHT_FILE
    if shard.stat().st_size != WEIGHT_BYTES:
        raise ValueError("Pinned Qwen3.5-2B weight size differs")
    with shard.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != WEIGHT_SHA256:
        raise ValueError("Pinned Qwen3.5-2B weight SHA-256 differs")
    config = (weights / "config.json").read_bytes()
    if hashlib.sha256(config).hexdigest() != FILES["config.json"]:
        raise ValueError("Pinned Qwen3.5-2B configuration differs")
    if not mx.metal.is_available():
        raise RuntimeError("This backend requires Apple Metal")
    mx.set_default_device(mx.gpu)
    with TemporaryDirectory() as tmp:
        # Give the loader only the verified files, including when the local
        # cache also contains another version's optional config or weight shard.
        local = Path(tmp)
        (local / WEIGHT_FILE).symlink_to(shard.resolve())
        (local / "config.json").write_bytes(config)
        model, config_dict = load_model(local, strict=True)
    loaded = tree_flatten(model.parameters())
    precision = Counter(str(value.dtype).removeprefix("mlx.core.") for _, value in loaded)
    metadata = {"backend": "mlx", "model": "Qwen3.5-2B", "revision": REVISION,
                "weights_loaded": True, "weight_file": WEIGHT_FILE, "weight_bytes": WEIGHT_BYTES,
                "weight_sha256": digest, "config_sha256": FILES["config.json"], "quantized": False,
                "checkpoint_dtype": config_dict["text_config"]["dtype"],
                "precision": dict(precision), "text_model_class": type(model).__module__,
                "pid": os.getpid(), "engine_python": sys.executable, "python_version": platform.python_version(),
                "macos_version": platform.mac_ver()[0], "mlx": version("mlx"), "mlx_lm": version("mlx-lm"),
                "mlx_metal": version("mlx-metal"), "device": mx.device_info(mx.gpu),
                "sampling": SAMPLING, "prefill_step_size": PREFILL_STEP, "eos_ids": sorted(EOS_IDS)}
    return model, config_dict["text_config"]["vocab_size"], metadata


def infer(model, vocab_size, input_ids, limit, seed):
    import mlx.core as mx
    from mlx_lm.generate import generate_step, generation_stream
    from mlx_lm.sample_utils import make_sampler

    if (not isinstance(input_ids, list) or not input_ids
            or any(type(token) is not int or not 0 <= token < vocab_size for token in input_ids)):
        raise ValueError("Invalid input token IDs")
    if type(limit) is not int or not 1 <= limit <= MAX_GENERATION or len(input_ids) + limit > MAX_CONTEXT:
        raise ValueError("Invalid decode budget")
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("Invalid sampling seed")
    mx.random.seed(seed)
    sampler = make_sampler(temp=SAMPLING["temperature"], top_p=SAMPLING["top_p"], top_k=SAMPLING["top_k"])
    mx.reset_peak_memory()
    generated = []
    started = time.monotonic()
    first = last = None
    with closing(generate_step(mx.array(input_ids, dtype=mx.uint32), model, max_tokens=limit,
                               sampler=sampler, prefill_step_size=PREFILL_STEP)) as steps:
        for token, _ in steps:
            last = time.monotonic()
            if first is None:
                first = last
            generated.append(token)
            if token in EOS_IDS:
                break
    # MLX-LM pipelines the next decode step. Drain that stream before timing
    # the completed request or accepting another independent prompt.
    mx.synchronize(generation_stream)
    return generation_stats(generated, started, time.monotonic(), first=first, last=last,
                            allocated=mx.get_peak_memory(), source="mlx_metal_token_stream")


def serve(fd, weights):
    with socket.socket(fileno=fd) as sock:
        try:
            model, vocab_size, metadata = load_engine(weights)
            send(sock, {"status": "ready", "metadata": metadata})
        except Exception as error:
            send(sock, {"status": "error", "error": f"{type(error).__name__}: {error}"})
            return
        while True:
            try:
                request = receive(sock)
            except EOFError:
                return
            request_id = request["request_id"]
            try:
                if set(request) != {"input_ids", "limit", "seed", "request_id"}:
                    raise ValueError("Invalid token-only generation request")
                result = infer(model, vocab_size, request["input_ids"], request["limit"], request["seed"])
                send(sock, {"request_id": request_id, "measurement": result})
            except Exception as error:
                send(sock, {"request_id": request_id, "error": f"{type(error).__name__}: {error}"})
                return


if __name__ == "__main__":
    serve(int(sys.argv[1]), Path(sys.argv[2]))
