"""Verify the optional sense envelope with the original tokenizer and local simulator."""
from argparse import ArgumentParser
from collections import Counter
from pathlib import Path
import gzip
import hashlib
import importlib
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime
from itertools import count
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from thinking_events.sense_default import effective_tools, execution_message
from thinking_events.sense_example_prepare import SENSE, ordered_bytes, sense_function, verified_wire_bodies, variant
from thinking_events.campaign_storage import read_jsonl

DEFAULT_RUN = ROOT / "outputs/sense-retest-20261111/server/run"
OUT = Path(__file__).resolve().parent
PLAN_SHA = "8975ceb7acecc1a69abd1cd45002c748f4453c398ebe90fd3cecd555eaeb1364"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def tool_block(name, parameters=()):
    fields = "".join(f"<parameter={key}>\n{value}\n</parameter>\n" for key, value in parameters)
    return f"<tool_call>\n<function={name}>\n{fields}</function>\n</tool_call>"


def fixture_cases(tokenizer, tools):
    from flockcue_qwen35 import adapter

    old = variant({"tools": tools})["tools"]
    new = effective_tools(tools)
    sense = sense_function({"tools": new})
    bare, explicit = tool_block(SENSE), tool_block(SENSE, [("input_json", "{}")])
    cases = {
        "omitted": bare, "explicit": explicit, "legacy_action": tool_block("list_drones"),
        "selected": tool_block(SENSE, [("input_json", '{"drone_ids":["d1"],"detail":"full"}')]),
        "literal_empty_group": tool_block("list_drones") + "\n" + bare,
        "unknown": tool_block(SENSE, [("detail", '"full"')]),
        "mixed": tool_block(SENSE, [("input_json", "{}"), ("detail", '"full"')]),
        "duplicate": tool_block(SENSE, [("input_json", "{}"), ("input_json", "{}")]),
        "illtyped": tool_block(SENSE, [("input_json", "[]")]),
        "incomplete_action_group": bare + "\n" + tool_block("take_off"),
        "truncated": bare[:-8],
    }
    modes = {}
    for thinking in (False, True):
        expected = {}
        for name, answer in cases.items():
            body = ("Use public sense.\n</think>\n" if thinking else "") + answer
            ids = tokenizer.encode(body, add_special_tokens=False) + [248046]
            for label, defs in (("B", old), ("C", new)):
                rendered, prepared = adapter.render(tokenizer, [{"role":"user", "content":"Sense nearby entities."}],
                                                    defs, thinking)
                require(bool(rendered["input_ids"]), "Empty rendered prompt")
                parsed = adapter.parse_generation(tokenizer, ids, adapter.EOS_IDS, thinking, prepared, 2048)
                ok = name in {"explicit", "legacy_action", "selected"} or (name in {"omitted", "literal_empty_group"} and label == "C")
                require((parsed["message"] is not None) is ok, f"{label}/{thinking}/{name} returned {parsed}")
                expected.setdefault(name, {})[label] = parsed["message"]
            if name not in {"omitted", "literal_empty_group"}:
                require(expected[name]["B"] == expected[name]["C"], f"Regression in {name}")
        control = expected["explicit"]["C"]
        require(execution_message(expected["omitted"]["C"]) == execution_message(control) == control,
                "Changing the spelling altered the dispatch envelope or another message field")
        modes[str(thinking)] = expected
    return {"cases": modes, "description_sha256": sha(sense["description"].encode()),
            "sense_parameters": sense["parameters"]}


def parse_replay(run):
    import student_input_v2 as v2
    from flockcue_qwen35 import adapter

    tok = adapter.load_tokenizer()
    all_trials = sorted(run.glob("trials/qwen3p5-2b-*"))
    require(len(all_trials) == 58, "Expected 29 pairs")
    counts, rows = Counter(), []
    controls = None
    for trial in all_trials:
        wires = dict(verified_wire_bodies(sorted((trial / "policy/modelio").glob("*.jsonl"))))
        records = read_jsonl(trial / "private/student-requests.jsonl")
        for idx, row in enumerate(records, 1):
            wire = json.loads(wires[row["request_body_sha256"]])
            old = variant({"tools":wire["tools"]})["tools"]
            new = effective_tools(wire["tools"])
            if controls is None:
                controls = fixture_cases(tok, wire["tools"])
            # prepare_history needs the public messages minus the separately rendered history thinking.
            messages = [{k: v for k, v in m.items() if k != "reasoning_content"} for m in wire["messages"]]
            if row["generated_ids"] is None:
                require(row["status"] not in {"ok","format_error"} and row["message"] is None,
                        "Generation bytes are absent for a parsed response")
                counts["no_generation"] += 1
                rows.append({"slot":trial.name,"turn":idx,"category":"no_generation",
                             "stop_reason":row["stop_reason"],"request_sha256":row["request_body_sha256"]})
                continue
            args = (tok, row["generated_ids"], adapter.EOS_IDS, row["thinking"])
            results = [adapter.parse_generation(*args, v2.prepare_history(messages, t), row["max_new_tokens"])
                       for t in (old, new)]
            before, after = results
            require(before["message"] == row["message"] and before["anomalies"] == row["anomalies"],
                    f"Original B output failed replay at {trial.name}/{idx}")
            changed = before["message"] != after["message"]
            if changed:
                require(before["anomalies"] == ["tool_arguments"] and after["message"] is not None
                        and execution_message(after["message"]) != after["message"],
                        "Behavior changed outside empty sense")
            category = "newly_accepted" if changed else "retained_valid" if before["message"] else "retained_rejected"
            counts[category] += 1
            rows.append({"slot":trial.name,"turn":idx,"category":category,"request_sha256":row["request_body_sha256"],
                         "generated_ids_sha256":sha(ordered_bytes(row["generated_ids"]))})
    require(counts["newly_accepted"] == 32, "Candidate count differs from original diagnosis")
    return {"counts": dict(counts), "controls":controls,"records":rows}


def simulate(run, parsed, out):
    from harness_episode_local import block_network
    attempts = block_network()
    from harness_v2_world import World
    from harness_v2_online import client_class
    from blackboard import PerceptionBlackboard
    from tool_input_contract import adapt_input_json_tools
    from uav_langchain_tools import create_uav_tools

    for name in ("models", "controllers"):
        sys.modules[name + ".__init__"] = importlib.import_module(name)
    world = World()
    task_ids = sorted({r["task_id"] for r in read_jsonl(run / "plan-slots.jsonl")})
    require(len(task_ids) == 29, "Different dev set")
    compared, evidence = [], []
    for task in task_ids:
        fixtures = run / "study/fixtures"
        fixture = json.loads((fixtures / (task + ".json")).read_bytes())
        blob = (fixtures / (task + ".pkl")).read_bytes()
        observations = []
        for spelling in ("omitted", "explicit"):
            world.restore(blob, fixture["snapshot_sha256"])
            before = world.state_digest()
            board = PerceptionBlackboard(fixture["scene_id"])
            empty_board = board.full()
            client = client_class(world)(base_url="http://testserver")
            handlers = adapt_input_json_tools(create_uav_tools(client, blackboard=board), "json_object_or_string_v2")
            tool = next(t for t in handlers if t.name == SENSE)
            with tempfile.TemporaryDirectory() as temp:
                world.audit_path = Path(temp) / "api.jsonl"
                # The previous phase proved that both thinking modes yield the same executable arguments.
                msg = execution_message(parsed["controls"]["cases"]["False"][spelling]["C"])
                call, = msg["tool_calls"]
                # Restore the same request-audit UUIDs and timestamps in both branches. These
                # fields are part of the simulator snapshot, alongside the actual world state.
                class AuditClock(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return cls.fromtimestamp(world.clock.time(), tz)
                numbers = count(1)
                uuids = SimpleNamespace(uuid4=lambda: uuid.UUID(int=next(numbers)))
                with patch.object(world.server, "datetime", AuditClock), patch.object(world.server, "uuid", uuids):
                    reply = tool.invoke(json.loads(call["function"]["arguments"]))
                requests = read_jsonl(world.audit_path)
            result = json.loads(reply)
            drones = [d["id"] for d in requests[0]["response"]]
            require(requests[0]["endpoint"] == "/drones" and
                    [r["endpoint"] for r in requests[1:]] == [f"/drones/{drone}/nearby" for drone in drones] and
                    all(r["method"] == "GET" and not r["admin"] for r in requests) and drones and
                    result["sensed_drone_ids"] == drones and set(result["per_drone"]) == set(drones)
                    and result["blackboard"] == json.loads(ordered_bytes(board.summary())) and board._step == 1, "Unexpected actual sense effect")
            observations.append({"reply":reply,"requests":requests,"initial_board":empty_board,"final_board":board.full(),
                                 "board_step":board._step,"world_before":before,"world_after":world.state_digest()})
        require(observations[0] == observations[1], f"Different responses, world or board for task {task}")
        evidence.append({"task_id": task, "omitted": observations[0], "explicit": observations[1]})
        observed = observations[0]
        compared.append({"task_id":task,"scene_id":fixture["scene_id"],"snapshot_sha256":fixture["snapshot_sha256"],
                         "api_gets_per_spelling":len(observed["requests"]),"drones":len(drones),
                         **{f"{k}_sha256":sha(ordered_bytes(observed[k])) for k in ("reply","requests","initial_board","final_board")},
                         "world_before":observed["world_before"],"world_after":observed["world_after"],
                         "board_step":observed["board_step"]})
    require(not attempts,"External network attempt")
    artifact = out / "arm-evidence.jsonl.gz"
    artifact.write_bytes(gzip.compress(b"".join(ordered_bytes(row) + b"\n" for row in evidence), mtime=0))
    return {"compared": len(compared), "network_attempts": attempts, "tasks": compared,
            "arm_evidence_sha256": sha(artifact.read_bytes())}


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument("mode", choices=("parse","simulate"))
    cli.add_argument("--run", type=Path, default=DEFAULT_RUN)
    cli.add_argument("--out", type=Path, default=OUT)
    args=cli.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    require(sha((run / "plan-slots.jsonl").read_bytes()) == PLAN_SHA, "Frozen paired plan changed")
    out.mkdir(parents=True, exist_ok=True)
    result_path=out/(args.mode+".json")
    if args.mode == "parse":
        result=parse_replay(run)
    else:
        with tempfile.TemporaryDirectory() as scratch:
            os.chdir(scratch)
            parsed = json.loads((out / "parse.json").read_bytes())
            require(parsed["helper_sha256"] == sha((ROOT / "scripts/thinking_events/sense_default.py").read_bytes()),
                    "Parsed generated cases used another optional sense contract")
            result = simulate(run, parsed, out)
    result["plan_sha256"] = PLAN_SHA
    result["helper_sha256"] = sha((ROOT/"scripts/thinking_events/sense_default.py").read_bytes())
    result_path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(f"{args.mode} passed: {result_path}")


if __name__ == "__main__":
    main()
