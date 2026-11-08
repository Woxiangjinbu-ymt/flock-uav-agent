"""Isolated renderer and optional vLLM engine for the student IPC client."""

from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import socket
import sys
import time

import student_input_v2 as v2

from .student_timing import TokenTimes, generation_stats, vllm_timestamps
from . import student
from .student_process import receive, send, spawn_service, stopped


def verify_model(path, model, revision, manifest):
    """Bind all files that the offline tokenizer and model loaders can consume."""
    if revision != student.MODEL_REVISIONS[model] or manifest.get("revision") != revision or manifest.get("model") != model:
        raise ValueError("student revision mismatch")
    files = manifest["files"]
    required = {"config.json", "tokenizer_config.json", "tokenizer.json", "generation_config.json"}
    shards = {name for name in files if name.endswith(".safetensors")}
    if not required <= files.keys() or not shards:
        raise ValueError("incomplete student manifest")
    path = Path(path)
    actual = {entry.name for entry in path.iterdir() if entry.is_file() and
              entry.suffix.lower() in {".json", ".safetensors", ".model", ".txt", ".jinja", ".tiktoken", ".bin"}}
    if not actual <= files.keys() or any(name.endswith(".bin") for name in actual):
        raise ValueError("unregistered student model files")
    for name, expected in files.items():
        if not name or Path(name).name != name or not (path / name).is_file():
            raise ValueError("invalid student model file")
        with (path / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError(f"student checksum mismatch: {name}")
    if "model.safetensors.index.json" in files:
        index = json.loads((path / "model.safetensors.index.json").read_bytes())
        if not index["weight_map"] or set(index["weight_map"].values()) != shards:
            raise ValueError("student model shard index mismatch")
    elif shards != {"model.safetensors"}:
        raise ValueError("sharded weights require the pinned index")
    return hashlib.sha256(v2.canonical(manifest)).hexdigest()


def _cuda():
    import torch
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("student generation requires a BF16 CUDA device")
    return torch


class TransformersModel:
    def __init__(self, path):
        from transformers import AutoModelForCausalLM
        self.torch = _cuda()
        self.model = AutoModelForCausalLM.from_pretrained(str(path), local_files_only=True, dtype=self.torch.bfloat16,
                                                        trust_remote_code=False, use_safetensors=True).to("cuda").eval()

    def generate(self, ids, limit, seed, eos):
        started = time.monotonic()
        clock = TokenTimes()
        self.torch.cuda.reset_peak_memory_stats()
        self.torch.manual_seed(seed)
        prompt = self.torch.tensor([ids], dtype=self.torch.long, device="cuda")
        with self.torch.inference_mode():
            whole = self.model.generate(input_ids=prompt, attention_mask=self.torch.ones_like(prompt),
                                        streamer=clock, do_sample=True, max_new_tokens=limit, eos_token_id=eos,
                                        pad_token_id=self.model.generation_config.pad_token_id, **student.SAMPLING)
        output = whole[0, len(ids):].tolist()
        if clock.count != len(output):
            raise ValueError("streamed token count differs from generated IDs")
        return generation_stats(output, started, time.monotonic(), source="transformers_token_stream",
                                first=clock.first, last=clock.last,
                                reserved=self.torch.cuda.max_memory_reserved(),
                                allocated=self.torch.cuda.max_memory_allocated())


class VllmModel:
    def __init__(self, path):
        if version("vllm") != "0.11.2":
            raise ValueError("expected vLLM 0.11.2")
        from vllm import LLM
        self.torch = _cuda()
        self.model = LLM(model=str(path), dtype="bfloat16", tensor_parallel_size=1,
                         enable_prefix_caching=False, max_model_len=student.MAX_CONTEXT,
                         max_num_seqs=1, trust_remote_code=False, generation_config="vllm", disable_log_stats=False)

    def generate(self, ids, limit, seed, eos):
        from vllm import SamplingParams
        params = SamplingParams(max_tokens=limit, seed=seed, stop_token_ids=eos,
                                detokenize=False, **student.SAMPLING)
        started = time.monotonic()
        (result,) = self.model.generate([{"prompt_token_ids": ids}], params, use_tqdm=False)
        (completion,) = result.outputs
        if list(result.prompt_token_ids) != ids:
            raise ValueError("vLLM changed the frozen input IDs")
        finished = time.monotonic()
        first, last = vllm_timestamps(getattr(result, "metrics", None), started, finished)
        return generation_stats(list(completion.token_ids), started, finished, source="vllm_request_metrics",
                                first=first, last=last, unavailable_reason="vllm_token_timestamps_unavailable")


def device_info(model):
    torch = model.torch
    return {"torch": torch.__version__, "cuda": torch.version.cuda, "device": torch.cuda.get_device_name(),
            "dtype": "bfloat16"}


def serve_engine(sock, config):
    # A distinct interpreter keeps vLLM's dependency set away from the renderer.
    engine = VllmModel(config["model_path"])
    send(sock, {"status": "ready", "metadata": {**device_info(engine), "vllm": version("vllm"),
                                                "engine_python": sys.executable}})
    while True:
        request = receive(sock)
        if set(request) != {"input_ids", "limit", "seed", "eos", "request_id"}:
            raise ValueError("invalid generation envelope")
        result = engine.generate(request["input_ids"], request["limit"], request["seed"], request["eos"])
        send(sock, {**result, "request_id": request["request_id"]})


class Renderer:
    def __init__(self, config):
        self.backend = config["backend"]
        if version("transformers") != "4.57.3":
            raise ValueError("expected Transformers 4.57.3")
        self.fixture = config["fixture_replies"]
        self.metadata = {"backend": self.backend, "model": config["model"], "python": sys.executable,
                         "source": str(Path(__file__).resolve()), "transformers": version("transformers"),
                         "template_sha256": v2.template_hash()}
        if self.backend == "fixture":
            path = Path(config["tokenizer_path"] or v2.base.DEFAULT_TOKENIZER)
            self.tokenizer = v2.load_tokenizer(path)
            self.metadata.update(weights_loaded=False, tokenizer_manifest_sha256=v2.base.TOKENIZER_MANIFEST_SHA256)
        else:
            from transformers import AutoTokenizer
            path = Path(config["model_path"])
            digest = verify_model(path, config["model"], config["revision"], config["model_manifest"])
            self.tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True,
                                                          trust_remote_code=False, use_fast=True)
            self.metadata.update(weights_loaded=True, model_manifest_sha256=digest, revision=config["revision"])
            if self.backend == "transformers":
                self.engine = TransformersModel(path)
                self.metadata.update(device_info(self.engine))
            else:
                self.process, self.engine_sock = spawn_service(config["backend_python"], "engine", own_group=False)
                send(self.engine_sock, {"model_path": str(path)})
                ready = receive(self.engine_sock)
                if ready.get("status") != "ready":
                    raise RuntimeError(f"vLLM startup failed: {ready.get('error')}")
                self.metadata.update(ready["metadata"])
        self.eos = json.loads((path / "generation_config.json").read_bytes())["eos_token_id"]
        if type(self.eos) is int:
            self.eos = [self.eos]
        if not self.eos or any(type(token) is not int or token < 0 for token in self.eos):
            raise ValueError("invalid student EOS config")

    def generate(self, request, ids, limit):
        if self.backend == "fixture":
            started = time.monotonic()
            script = self.fixture[request["slot"]] if isinstance(self.fixture, dict) else self.fixture
            reply = script[request["turn"] - 1]
            if isinstance(reply, dict):
                time.sleep(reply.get("delay", 0))
                reply = reply["text"]
            emitted = self.tokenizer.encode(reply, add_special_tokens=False)[:limit]
            return generation_stats(emitted, started, time.monotonic(), source="cpu_scripted_bulk",
                                    unavailable_reason="fixture_emits_complete_sequence")
        if self.backend == "transformers":
            return self.engine.generate(ids, limit, request["seed"], self.eos)
        key = request["request_id"]
        send(self.engine_sock, {"input_ids": ids, "limit": limit, "seed": request["seed"],
                                "eos": self.eos, "request_id": key}, request["deadline"])
        reply = receive(self.engine_sock, request["deadline"])
        if reply.get("request_id") != key:
            raise ValueError("vLLM response identity mismatch")
        return reply

    def respond(self, request):
        if set(request) != {"request_id", "slot", "turn", "messages", "tools", "thinking",
                            "seed", "started", "deadline", "spent_tokens"}:
            raise ValueError("invalid public student request")
        if time.monotonic() >= request["deadline"]:
            return stopped("request_timeout")
        prompt, prepared = student.render(self.tokenizer, request["messages"], request["tools"], request["thinking"])
        ids = prompt.pop("input_ids")
        prompt.pop("prompt_text")
        limit, reason = student.output_budget(len(ids), request["spent_tokens"])
        audit = {**prompt, "input_ids": ids, "max_new_tokens": limit, "seed": request["seed"],
                 "sampling": student.SAMPLING, "thinking": request["thinking"]}
        if reason:
            return {**stopped(reason), **audit}
        measurement = self.generate(request, ids, limit)
        generated = measurement["generated_ids"]
        first = measurement.pop("first_token_monotonic")
        if first is not None and first < request["started"]:
            raise ValueError("first token predates request start")
        measurement["timing"]["ttft_seconds"] = first - request["started"] if first is not None else None
        if len(generated) > limit or any(type(t) is not int or t < 0 for t in generated):
            raise ValueError("generation exceeded admitted budget or contained invalid IDs")
        parsed = student.parse_generation(self.tokenizer, generated, self.eos, request["thinking"], prepared, limit)
        reasoning = parsed.pop("reasoning_tokens")
        success = parsed["message"] is not None
        return {**audit, **parsed, **measurement, "status": "ok" if success else "format_error",
                "stop_reason": None if success else parsed["anomalies"][0],
                "usage": {"prompt_tokens": len(ids), "completion_tokens": len(generated),
                          "total_tokens": len(ids) + len(generated),
                          "completion_tokens_details": {"reasoning_tokens": reasoning}}}


def main():
    role, fd = sys.argv[1:]
    with socket.socket(fileno=int(fd)) as sock:
        try:
            config = receive(sock)
            if role == "engine":
                serve_engine(sock, config)
                return
            if role != "render":
                raise ValueError("unknown service role")
            service = Renderer(config)
            send(sock, {"status": "ready", "metadata": service.metadata})
            while True:
                request = receive(sock)
                identity = {key: request[key] for key in ("slot", "turn", "request_id")}
                try:
                    result = service.respond(request)
                except v2.InputRejected as exc:
                    result = {**stopped("input_rejected"), "error": exc.code}
                except Exception as exc:
                    send(sock, {**identity, **stopped("student_backend_error"), "error": str(exc)})
                    return
                send(sock, {**identity, **result})
        except (EOFError, BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            send(sock, {"status": "error", "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
