"""Verify two sealed receipt histories through live teacher HTTP, native 2B and SFT."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from runtime import executor, parallel

TASK = "b3952e2d"
SCENE = "79b207ff"
CASES = ((3, "32K"), (8, "48K"))


def dispatch(folder, settings):
    from openai import OpenAI
    from flockcue.student_context import StudentContext
    from runtime.student_validation import launch_settings
    from posttraining.inference import FALLBACK_CONTEXT

    provider = settings["provider_configs"][settings["selected_provider"]]
    student = launch_settings("http://localhost/v1", "base", FALLBACK_CONTEXT)
    original = executor.read_json(folder / "source.json")
    for name, config in (("student", student), ("teacher", settings)):
        ctx = StudentContext(config["fallback_prompt_limit"], compact=config["compact_receipts"])
        try:
            payload = ctx.apply(dict(original))
        finally:
            ctx.close()
        if name == "student":
            executor.save_json(folder / "student.json", {"messages": payload["messages"], "tools": payload["tools"]})
        else:
            with OpenAI(api_key=executor.provider_key(), base_url=provider["base_url"], timeout=120, max_retries=0) as client:
                reply = client.chat.completions.with_raw_response.create(**payload)
                (folder / "teacher-request.json").write_bytes(reply.http_response.request.content)
                (folder / "teacher-response.json").write_bytes(reply.content)


def run(attempt, stage, out, model_python, weights):
    from posttraining.inference import FALLBACK_CONTEXT, MAX_CONTEXT, MAX_GENERATION, output_budget
    from posttraining.inference.metal import MacModel
    from posttraining.sft import adapter
    from posttraining.sft.history import canonical, sha
    from posttraining.sft.supervision import supervise
    from .context_window import PROMPT_LIMIT, select_window, visible_messages
    from .index_wire import scene_indices
    from .rebuild import blob, public_turn
    from .replay_receipts import verify_contents
    from flockcue.recording.compact_receipts import compact_receipts

    settings_path = stage / "settings.json"
    settings = executor.read_json(settings_path)
    fallback = settings["fallback_prompt_limit"]
    ref = executor.read_json(executor.FALLBACK_REGISTRATION.with_name("results.json"))
    plan = executor.read_json(attempt / "plan.json")
    manifest = executor.read_json(attempt / "manifest.json")
    scene = next(s for s in plan["sessions"] if s["id"] == SCENE)
    record = manifest["scenes"][SCENE]
    if ref["attempt"] != attempt.name or ref["scenes_sha256"][SCENE] != record["seal_sha256"]:
        raise ValueError("Pilot source differs from the registered fallback run")
    parallel.verify_archive(executor, attempt)
    parallel.verify_scene(executor, attempt, plan, scene, record)
    files = executor.read_json(attempt / SCENE / "complete.json")["files"]
    episode, turns = next((ep, ts) for ep, ts, _ in scene_indices(Path("."), attempt / SCENE, files, SCENE,
                           "training_expansion") if ep["task_id"] == TASK)
    archive = attempt / episode["archive_path"]
    contexts = {}
    with archive.open() as stream:
        for event in map(json.loads, stream):
            if event["kind"] == "student_context":
                data = event["data"]
                contexts[data["logical_call_id"]] = data
    tokenizer = adapter.load_tokenizer()
    out.mkdir(parents=True, exist_ok=False)
    results = []
    with archive.open("rb") as stream, MacModel(model_python, weights) as model:
        for n, mode in CASES:
            folder = out / mode
            folder.mkdir()
            turn = turns[n - 1]
            if turn["turn"] != n or not turn["wire_verified"]:
                raise ValueError("Unverified historical turn")
            wire = public_turn(stream, turn)
            request = next(a for a in turn["attempts"] if a["attempt_id"] == turn["response_attempt_id"])
            stream.seek(request["request"][0])
            original = json.loads(blob(json.loads(stream.readline())["data"]["body"]))
            if original["messages"] != wire["messages"] or original["tools"] != wire["tools"]:
                raise ValueError("Source request differs from verified public turn")
            for i, msg in contexts[turn["logical_call_id"]]["omitted"]:
                original["messages"].insert(i, msg)
            executor.save_json(folder / "source.json", original)
            subprocess.run([executor.interpreter("agent"), "-m", __spec__.name, "dispatch", folder, settings_path],
                           cwd=executor.ROOT, check=True)
            body = (folder / "teacher-request.json").read_bytes()
            teacher = json.loads(body)
            response = executor.read_json(folder / "teacher-response.json")
            student = executor.read_json(folder / "student.json")
            if student != {k: teacher[k] for k in student} or teacher["model"] != executor.MODEL:
                raise ValueError("Teacher HTTP and student model inputs differ")
            window = select_window(tokenizer, original["messages"], original["tools"],
                                   fallback_limit=fallback, project=compact_receipts)
            raw = visible_messages(original["messages"], window["start"])
            evidence = verify_contents(raw, teacher["messages"])
            ids = adapter.render(tokenizer, student["messages"], student["tools"], thinking=True)[0]["input_ids"]
            cap = FALLBACK_CONTEXT if mode == "48K" else MAX_CONTEXT
            if (output_budget(len(ids), 0, context_tokens=cap) != (MAX_GENERATION, None)
                    or ("fallback_prompt_limit" in window) != (mode == "48K")
                    or (len(ids) > PROMPT_LIMIT) != (mode == "48K") or not evidence["changed_receipt_instances"]):
                raise ValueError("Unexpected receipt or admission class")
            target = response["choices"][0]["message"]
            training, labels, audit = supervise(tokenizer, teacher["messages"], teacher["tools"], target, context_limit=cap)
            if (training[:len(ids)] != ids or labels[:len(ids)] != [-100] * len(ids)
                    or labels[len(ids):] != training[len(ids):] or labels[-1] != tokenizer.eos_token_id
                    or sha(canonical(ids)) != window["ids_sha256"] or audit["prompt_ids_sha256"] != window["ids_sha256"]):
                raise ValueError("Qwen inference input differs from the masked training prefix")
            measured = model.generate(ids, 1, 42, time.monotonic() + 500)
            if len(measured["generated_ids"]) != 1:
                raise ValueError("2B did not finish prefill and decode")
            executor.save_json(folder / "training.json", {"input_ids": training, "labels": labels})
            results.append(dict(task=TASK, turn=n, window=mode, input_tokens=len(ids), input_ids_sha256=window["ids_sha256"],
                                supervised_tokens=audit["supervised_tokens"], upstream_model=response["model"],
                                upstream_fingerprint=response.get("system_fingerprint"), teacher_http_sha256=sha(body),
                                teacher_response_sha256=sha((folder / "teacher-response.json").read_bytes()),
                                student_weight_sha256=model.metadata["weight_sha256"], student_token=measured["generated_ids"][0],
                                retained_tools=sum(m["role"] == "tool" for m in raw), evidence=dict(evidence)))
            print(f"{mode} passed with {len(ids)} identical input IDs", flush=True)
    executor.save_json(out / "results.json", results)


if __name__ == "__main__":
    if sys.argv[1:2] == ["dispatch"]:
        dispatch(Path(sys.argv[2]), executor.read_json(Path(sys.argv[3])))
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        for name in ("attempt", "stage", "output", "model-python", "weights"):
            parser.add_argument("--" + name, type=Path, required=True)
        args = parser.parse_args()
        run(args.attempt.resolve(), args.stage.resolve(), args.output.resolve(), args.model_python, args.weights)
