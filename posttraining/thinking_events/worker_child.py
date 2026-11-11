"""One-shot v5 simulation or scorer process with a preconnected student transport."""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import sys
import time


def write_frame(stream, value):
    stream.write((json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode())
    stream.flush()


def read_frame(stream):
    raw = stream.readline(8 << 20)
    if not raw:
        raise EOFError("student pipe closed")
    if not raw.endswith(b"\n"):
        raise ValueError("incomplete or oversized student pipe record")
    return json.loads(raw)


def _frozen_modules(root, *, packages=("agent_harness", "agent4drone", "server", "scripts", "flockcue", "posttraining"),
                    record_aliases=False):
    names = {}
    owners = {}
    for name, mod in list(sys.modules.items()):
        file = getattr(mod, "__file__", None)
        if file and Path(file).resolve().is_relative_to(root):
            relative = Path(file).resolve().relative_to(root)
            if (
                relative.parts[0] not in packages
                or any(part.startswith(".venv") for part in relative.parts)
            ):
                continue
            path = str(relative)
            if record_aliases:
                names[name] = path
                continue
            if path in owners and owners[path] is not mod:
                raise RuntimeError(
                    f"Source loaded under multiple names: {path}, {owners[path].__name__}, {name}"
                )
            if path not in owners:
                names[name] = path
                owners[path] = mod
    return names


def _sdk_factory(reverse_receipts):
    from langchain_openai import ChatOpenAI

    class ReverseParallelFixture(ChatOpenAI):
        # Change only the wire order of already completed same-name tool results.
        # This exercises the SDK archive and token renderer on the identical payload.
        def _get_request_payload(self, *args, **kwargs):
            payload = super()._get_request_payload(*args, **kwargs)
            messages = payload["messages"]
            for idx, msg in enumerate(messages[:-2]):
                calls = msg.get("tool_calls", [])
                if len(calls) == 2 and calls[0]["function"]["name"] == calls[1]["function"]["name"]:
                    pair = messages[idx + 1 : idx + 3]
                    if [m.get("tool_call_id") for m in pair] == [c["id"] for c in calls]:
                        messages[idx + 1 : idx + 3] = reversed(pair)
            return payload

    def construct(**kwargs):
        kwargs.update(temperature=0.6, top_p=0.95)
        return (ReverseParallelFixture if reverse_receipts else ChatOpenAI)(**kwargs)

    return construct


def _run_worker(args, run, manifest, trial, attempts):
    from agent_profile import AgentProfile
    import harness_natural_runner as runner
    from harness_request_limits import RequestLimitError

    profile = AgentProfile.load(run / "source/experiments/hover-compact-contract-v2/profile.json")
    base = profile.to_dict()
    if base["profile_id"] != "general-agent-v5-local" or trial["scheduler"] != base["harness"]["scheduler"]:
        raise ValueError("student requires the v5 harness")
    if "agent_profile" in manifest or "online_pilot" in manifest:
        raise ValueError("student worker requires a dedicated run manifest")

    # These policy selectors are process-local. The sealed legacy runner retains
    # its separate mock profile and its coordinator's provider-specific admission.
    def config(_manifest, _trial, _factory):
        return {
            "model": args.model,
            "task_progress_mode": base["agent"]["task_progress_mode"],
            "harness_revision": base["harness"]["revision"],
            "harness_enabled": True,
            "execution_contract": profile.execution_contract,
            "implementation_budget": profile.implementation_budget(),
            "transport": "local_student_pipe",
        }

    runner.online_arm_configuration = config
    runner.online_environment = lambda _: profile.environment()
    runner.online_variant_agent_options = lambda _: {
        k: base["agent"][k] for k in ("input_layout", "completion_contract", "tool_input_contract")
    }
    folder = run / "trials" / args.trial
    with socket.socket(fileno=args.fd) as channel, channel.makefile("rwb") as stream:

        def transport(httpx, controls):
            turn = spent = 0

            def respond(request):
                nonlocal turn, spent
                turn += 1
                payload = json.loads(request.content)
                seed = int.from_bytes(
                    hashlib.sha256(f"{trial['task_id']}/{turn}".encode()).digest()[:4], "big"
                )
                deadline = controls["deadline"]
                record = {
                    "slot": args.trial,
                    "turn": turn,
                    "messages": payload["messages"],
                    "tools": payload.get("tools", []),
                    "thinking": args.thinking,
                    "seed": seed,
                    "spent_tokens": spent,
                    "episode_deadline": deadline,
                    "request_timeout": trial["runtime"]["request_limits"]["per_request_timeout_seconds"],
                }
                start = time.monotonic()
                write_frame(stream, record)
                result = read_frame(stream)
                if result["turn"] != turn or result["slot"] != args.trial:
                    raise RequestLimitError("response_identity_mismatch")
                row = {
                    **result,
                    "request_body_sha256": hashlib.sha256(request.content).hexdigest(),
                    "seed": seed,
                    "spent_before": spent,
                    "round_trip_seconds": time.monotonic() - start,
                    "remaining_seconds": max(0.0, deadline - time.monotonic()),
                }
                with (folder / "private/student-requests.jsonl").open("a") as out:
                    out.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")
                if result["status"] != "ok":
                    raise RequestLimitError(result.get("stop_reason") or result["status"])
                usage = result["usage"]
                spent += usage["total_tokens"]
                msg = result["message"]
                data = {
                    "id": f"student-{turn}",
                    "object": "chat.completion",
                    "created": 1,
                    "model": args.model,
                    "choices": [
                        {
                            "index": 0,
                            "message": msg,
                            "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop",
                        }
                    ],
                    "usage": usage,
                }
                if turn == args.cancel_at:
                    controls["request_cancel"]()
                return httpx.Response(200, json=data, request=request)

            return httpx.MockTransport(respond)

        runner.run_worker(
            run,
            args.trial,
            transport,
            network_attempts=attempts,
            model_factory=_sdk_factory(args.reverse_receipts),
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--trial", required=True)
    parser.add_argument("--mode", required=True, choices=("worker", "score"))
    parser.add_argument("--fd", type=int)
    parser.add_argument("--model")
    parser.add_argument("--thinking", action="store_true")
    parser.add_argument("--reverse-receipts", action="store_true")
    parser.add_argument("--cancel-at", type=int)
    args = parser.parse_args()
    run = args.run.resolve()
    if Path(__file__).resolve().parents[2] != run / "source":
        raise ValueError("student child must run exclusively from the frozen source")
    from harness_episode_local import block_network
    from harness_natural_runner import read_inputs, run_scorer

    attempts = block_network()
    # The legacy simulator enumerates *.py when binding clocks and includes
    # __init__.py. Point those two import spellings to their existing package.
    for name in ("models", "controllers"):
        sys.modules[name + ".__init__"] = importlib.import_module(name)
    manifest, trial, *_ = read_inputs(run, args.trial)
    if args.mode == "score":
        run_scorer(run, args.trial, network_attempts=attempts)
    else:
        _run_worker(args, run, manifest, trial, attempts)
    modules = _frozen_modules(run / "source")
    required = {"harness_natural_runner", "api.server", "models.drone", "controllers.drone_controller"}
    if args.mode == "worker":
        required.update({"agent_profile", "uav_agent", "uav_api_client"})
    if required.difference(modules):
        raise ValueError(
            f"Required modules absent from frozen source: {sorted(required.difference(modules))}"
        )
    target = run / "process-logs" / f"{args.trial}-{args.mode}-modules.json"
    target.write_text(json.dumps(modules, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
