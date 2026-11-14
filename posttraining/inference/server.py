"""Serve one frozen student on loopback for FlockCue's paired task evaluation."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import threading
import time
import traceback

from posttraining.inference import EPISODE_TOKEN_LIMIT, EVALUATION_CONTEXTS, MAX_CONTEXT, MAX_GENERATION, SAMPLING, output_budget
from posttraining.inference.cuda import TransformersModel
from posttraining.inference.weights import MANIFEST
from posttraining.sft import adapter
from posttraining.sft.history import InputRejected


def rejection(code):
    return 422, {"error": {"type": "student_stop", "code": code, "message": code}}


class Student:
    def __init__(self, model_path, adapter_dir, log, episode_limit=EPISODE_TOKEN_LIMIT, context_tokens=MAX_CONTEXT):
        self.tokenizer = adapter.load_tokenizer(model_path)
        self.engine = TransformersModel(model_path, adapter_dir)
        self.name = "qwen35-2b-sft" if adapter_dir else "qwen35-2b-base"
        base_revision = json.loads(MANIFEST.read_bytes())["revision"]
        self.fingerprint = self.engine.load_info.get("adapter", {}).get("weights_sha256")
        self.info = {
            "status": "ready", "model": self.name, "base_revision": base_revision,
            "adapter_sha256": self.fingerprint, "sampling": SAMPLING, "thinking": True,
            "budgets": {"context_tokens": context_tokens, "generation_tokens": MAX_GENERATION,
                        "episode_tokens": episode_limit},
            "seed_rule": "uint32_be(sha256(task_id/turn)[:4])",
            "template_sha256": adapter.FILES["chat_template.jinja"],
        }
        self.log = log
        self.episodes = {}
        self.lock = threading.Lock()

    def complete(self, body, headers, body_sha256):
        task, lease, request_id = (headers.get("X-Student-" + k) for k in ("Task", "Lease", "Request"))
        try:
            cutoff = float(headers["X-Student-Cutoff"])
        except (ValueError, TypeError):
            return rejection("invalid_task_cutoff")
        if not all((task, lease, request_id)) or not math.isfinite(cutoff):
            return rejection("missing_task_identity")
        if (body.get("model") != self.name or body.get("stream") or
                body.get("n", 1) != 1 or body.get("temperature") != SAMPLING["temperature"]):
            return rejection("unexpected_request_configuration")

        # All calls to this model share one CUDA engine and manual sampling seed.
        with self.lock:
            episode = self.episodes.setdefault(lease, {"task": task, "turn": 0, "spent": 0})
            if episode["task"] != task:
                return rejection("task_lease_mismatch")
            if episode.get("last_id") == request_id:
                if episode["last_body"] != body_sha256:
                    return rejection("changed_request_on_retry")
                return episode["last_response"]
            episode["turn"] += 1
            turn = episode["turn"]
            seed = int.from_bytes(hashlib.sha256(f"{task}/{turn}".encode()).digest()[:4], "big")
            audit = {"model": self.name, "task_id": task, "lease": lease, "turn": turn, "seed": seed,
                     "request_id": request_id, "body_sha256": body_sha256,
                     "spent_before": episode["spent"], "received_at": time.time()}
            try:
                response = self.generate(body, episode, cutoff, audit)
            except InputRejected as exc:
                response = rejection(exc.code)
            except Exception as exc:
                audit["engine_error"] = type(exc).__name__
                traceback.print_exc()
                response = 500, {"error": {"type": "student_engine_error", "message": type(exc).__name__}}
            audit["status"] = response[0]
            audit["stop_reason"] = response[1].get("error", {}).get("code")
            audit["spent_after"] = episode["spent"]
            audit["finished_at"] = time.time()
            with self.log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(audit, separators=(",", ":")) + "\n")
            episode.update(last_id=request_id, last_body=body_sha256, last_response=response)
            return response

    def generate(self, body, episode, cutoff, audit):
        if time.time() >= cutoff:
            return rejection("task_deadline")
        prompt, prepared = adapter.render(self.tokenizer, body["messages"], body.get("tools", []), thinking=True)
        ids = prompt["input_ids"]
        budgets = self.info["budgets"]
        limit, reason = output_budget(len(ids), episode["spent"], budgets["episode_tokens"], budgets["context_tokens"])
        audit.update(prompt_tokens=len(ids), history_reasoning_tokens=prompt["history_reasoning_tokens"],
                     input_ids_sha256=hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest(),
                     max_new_tokens=limit)
        if reason:
            return rejection(reason)
        if time.time() >= cutoff:
            return rejection("task_deadline")

        eos = sorted(adapter.EOS_IDS)
        stats = self.engine.generate(ids, limit, audit["seed"], eos, cutoff=cutoff)
        generated = stats["generated_ids"]
        episode["spent"] += len(ids) + len(generated)
        audit.update(generated_ids=generated, completion_tokens=len(generated),
                     timing=stats["timing"], peak_allocated_bytes=stats["peak_allocated_bytes"])
        if time.time() >= cutoff:
            return rejection("task_deadline")
        result = adapter.parse_generation(self.tokenizer, generated, eos, True, prepared, limit)
        audit["reasoning_tokens"] = result["reasoning_tokens"]
        if result["message"] is None:
            return rejection(result["anomalies"][0])
        message = result["message"]
        finish = "tool_calls" if message.get("tool_calls") else "stop"
        usage = {"prompt_tokens": len(ids), "completion_tokens": len(generated),
                 "total_tokens": len(ids) + len(generated),
                 "completion_tokens_details": {"reasoning_tokens": result["reasoning_tokens"]}}
        return 200, {
            "id": "student-" + audit["request_id"], "object": "chat.completion",
            "created": int(time.time()), "model": self.name, "system_fingerprint": self.fingerprint or self.info["base_revision"],
            "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage,
        }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, self.server.student.info)
        elif self.path == "/v1/models":
            self.reply(200, {"object": "list", "data": [{"id": self.server.student.name, "object": "model"}]})
        else:
            self.reply(404, {"error": "unknown_endpoint"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.reply(404, {"error": "unknown_endpoint"})
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
        except (UnicodeError, ValueError):
            self.reply(400, {"error": "invalid_json"})
            return
        self.reply(*self.server.student.complete(body, self.headers, hashlib.sha256(raw).hexdigest()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--no-episode-token-limit", action="store_true")
    parser.add_argument("--context-tokens", type=int, choices=EVALUATION_CONTEXTS, default=MAX_CONTEXT)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        limit = None if args.no_episode_token_limit else EPISODE_TOKEN_LIMIT
        server.student = Student(args.model, args.adapter, args.log, limit, args.context_tokens)
        print(json.dumps(server.student.info), flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
