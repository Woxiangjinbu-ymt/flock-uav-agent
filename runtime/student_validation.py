"""Replay the first paired evaluation or compare a new adapter on the same three A scenes."""
import argparse
from collections import Counter
import csv
from io import StringIO
import json
import inspect
import math
from pathlib import Path
import shutil
import signal
import subprocess
from tempfile import TemporaryDirectory
import threading
from urllib.parse import urlsplit

from . import executor as rt, parallel
from posttraining.inference import (EPISODE_TOKEN_LIMIT, EVALUATION_CONTEXTS, EVALUATION_GENERATIONS,
                                   MAX_CONTEXT, MAX_GENERATION, SAMPLING)
from posttraining.sft import adapter as student_adapter, benchmark, checkpoint

NAME = "flockcue-student-a-validation-60"
ARMS = ("base", "sft")
SCENE_IDS = ("72ccacd0", "0293952c", "cc14d231")
ROLES = rt.ROOT / "datasets/original-benchmark/scene-roles.csv"
SFT_RESULT = rt.ROOT / "datasets/results/sft-a100-first-pass/epoch-1/result.json"
FIRST_PAIRS = rt.ROOT / "datasets/results/student-validation-60/pairs.csv"
# The first paired archive contains the original plan, both arms' rows, source and package lists.
REFERENCE = {
    "plan_sha256": "35f48805383d01bfd5b67faec2a7ade743e2a460e1f633b34a65494111bb9540",
    "pairs_sha256": "c3dc528c4296539b18bfdac4c631fc23a6e803ea9ab263c7b4fa27fa555d3746",
    "deployment_sources_digest": "8c1b861800faa4d8e98321e8be0f7e6fdc162415c02e1980b24ace5dadfa9835",
    "deployment_packages_digest": "1193082008deb877ed3f5d4990ba19796b118b56f80c1852965114860da02078",
}
# Same deployment with the optional cumulative-token limit in the student service.
CONFIGURABLE_BUDGET_SOURCES = "b998976a24c70bffa3d9e4ccaa0b2cdb2ea9f3eaaeff24a631487ed33d5fe827"
CONFIGURABLE_CONTEXT_SOURCES = "29341684440500c24d812e0e9b672f40dd1256cae96b459d8ac0412909209d0a"
CONFIGURABLE_GENERATION_SOURCES = "445379898ed9457e3c4124e8dd17ed68ce31b02fee7f4921335d6fe459f27d65"


def build_plan():
    training = rt.read_json(SFT_RESULT)
    release = rt.read_json(rt.ROOT / "datasets/sft/qwen35-2b-first-pass/manifest.json")
    if rt.digest(ROLES.read_bytes()) != release["scene_roles_sha256"]:
        raise ValueError("Scene roles differ from the training release")
    with ROLES.open() as stream:
        ids = {row["session_id"] for row in csv.DictReader(stream) if row["role"] == "internal_validation"}
    canonical = rt.select_scenes(rt.canonical_scenes())
    parent = rt.make_plan(rt.PLAN, canonical)
    encoded = json.dumps(parent, ensure_ascii=False, indent=2) + "\n"
    if rt.digest(encoded.encode()) != rt.PLAN_SHA:
        raise ValueError("Parent deployment differs from the registered 600-task protocol")
    scenes = [scene for scene in canonical if scene[1]["id"] in ids]
    plan = rt.make_plan(NAME, scenes)
    if tuple(s["id"] for s in plan["sessions"]) != SCENE_IDS or any(len(s["tasks"]) != 20 for s in plan["sessions"]):
        raise ValueError("Expected exactly three complete released internal-validation scenes")
    adapter = training["training"]["adapter"]
    if adapter["base_revision"] != rt.read_json(rt.ROOT / "posttraining/sft/model.json")["revision"]:
        raise ValueError("Student revision differs from the trained adapter")
    plan["parent_plan_sha256"] = rt.PLAN_SHA
    plan["protocol"]["temperature"] = SAMPLING["temperature"]
    plan["student"] = {
        "base_revision": adapter["base_revision"], "sft_weights_sha256": adapter["weights_sha256"],
        "sampling": SAMPLING, "thinking": True,
        "budgets": {"context_tokens": MAX_CONTEXT, "generation_tokens": MAX_GENERATION,
                    "episode_tokens": EPISODE_TOKEN_LIMIT},
        "seed_rule": "uint32_be(sha256(task_id/turn)[:4])",
        "workers": 1, "grading": "original check tree with terminal HTTP model errors counted as failures",
    }
    return plan


def source_hashes():
    result = rt.current_source_hashes()
    for path in sorted((rt.ROOT / "posttraining").rglob("*")):
        if any(part.startswith(".") or part == "tests" for part in path.relative_to(rt.ROOT).parts):
            continue
        if path.is_file() and (path.suffix == ".py" or path.name in {"model.json", "tokenizer.json", "chat_template.jinja", "tokenizer_config.json", "config.json"}):
            result[path.relative_to(rt.ROOT).as_posix()] = rt.digest(path.read_bytes())
    return result


def deployment_source_digest(sources):
    """Hash the historical simulation, agent, runtime and native student inference path."""
    shared = {"posttraining/__init__.py", "posttraining/sft/__init__.py", "posttraining/sft/model.json",
              "posttraining/sft/adapter.py", "posttraining/sft/history.py", "posttraining/sft/checkpoint.py"}
    paths = ("flockcue/", "runtime/", "simulation/", "posttraining/inference/", "posttraining/sft/tokenizer/")
    used = {name: digest for name, digest in sources.items()
            if not Path(name).name.startswith("._") and name != "runtime/student_validation.py"
            and (name.startswith(paths) or name in shared)}
    # benchmark.py also contains training-only functions which can change independently.
    used["posttraining/sft/benchmark.py:attach_lora"] = rt.digest(inspect.getsource(benchmark.attach_lora).encode())
    used["posttraining/sft/benchmark.py:PROJECTIONS"] = parallel.json_digest(
        rt, {group: sorted(names) for group, names in benchmark.PROJECTIONS.items()})
    return parallel.json_digest(rt, used)


def candidate_plan(config, sources, uncapped=False, context_tokens=MAX_CONTEXT, generation_tokens=MAX_GENERATION):
    plan = build_plan()
    encoded = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    if rt.digest(encoded.encode()) != REFERENCE["plan_sha256"]:
        raise ValueError("Historical A60 deployment plan mismatch")
    digest = deployment_source_digest(sources)
    if context_tokens not in EVALUATION_CONTEXTS or (not uncapped and context_tokens != MAX_CONTEXT):
        raise ValueError("Historical comparisons use the original context window")
    if generation_tokens not in EVALUATION_GENERATIONS or (not uncapped and generation_tokens != MAX_GENERATION):
        raise ValueError("Historical comparisons use the original generation limit")
    allowed = {CONFIGURABLE_GENERATION_SOURCES}
    if generation_tokens == MAX_GENERATION:
        allowed.add(CONFIGURABLE_CONTEXT_SOURCES)
        if context_tokens == MAX_CONTEXT:
            allowed.add(CONFIGURABLE_BUDGET_SOURCES)
    if not uncapped:
        allowed.add(REFERENCE["deployment_sources_digest"])
    if digest not in allowed:
        raise ValueError("Simulation, harness or student inference sources differ from the historical A60 deployment")
    if config["format"] != checkpoint.FORMAT or config["base_revision"] != plan["student"]["base_revision"]:
        raise ValueError("Candidate adapter has incompatible format or base revision")
    if uncapped:
        plan["student"]["budgets"]["episode_tokens"] = None
        plan["student"]["budgets"]["context_tokens"] = context_tokens
        plan["student"]["budgets"]["generation_tokens"] = generation_tokens
        plan["paired_uncapped"] = True
    else:
        plan["historical_comparison"] = {**REFERENCE, "first_sft_weights_sha256": plan["student"]["sft_weights_sha256"]}
    plan["student"].update(sft_weights_sha256=config["weights_sha256"],
                           adapter_config_digest=parallel.json_digest(rt, config))
    return plan


def prepare(stage, adapter_dir=None, uncapped=False, context_tokens=MAX_CONTEXT, generation_tokens=MAX_GENERATION):
    import zipfile
    if stage.exists():
        raise FileExistsError(f"Stage already exists: {stage}")
    if uncapped and adapter_dir is None:
        raise ValueError("The uncapped paired evaluation requires an explicit SFT checkpoint")
    if not uncapped and (context_tokens != MAX_CONTEXT or generation_tokens != MAX_GENERATION):
        raise ValueError("A different token budget requires a fresh paired evaluation")
    stage.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=stage.parent) as tmp:
        new = Path(tmp) / "stage"
        (new / "scenes").mkdir(parents=True)
        sources = source_hashes()
        if adapter_dir is None:
            plan = build_plan()
        else:
            # The service loads the exact registered files even if a training run updates its output.
            frozen = new / "adapter"
            frozen.mkdir()
            for name in (checkpoint.CONFIG, checkpoint.WEIGHTS):
                shutil.copyfile(adapter_dir/name, frozen/name)
            config = rt.read_json(frozen / checkpoint.CONFIG)
            if rt.digest((frozen / checkpoint.WEIGHTS).read_bytes()) != config["weights_sha256"]:
                raise ValueError("Candidate adapter checksum mismatch")
            plan = candidate_plan(config, sources, uncapped, context_tokens, generation_tokens)
            if not uncapped:
                shutil.copyfile(FIRST_PAIRS, new / "historical-pairs.csv")
                if rt.digest((new / "historical-pairs.csv").read_bytes()) != REFERENCE["pairs_sha256"]:
                    raise ValueError("Historical A60 pairs checksum mismatch")
        rt.save_json(new / "plan.json", plan)
        rt.save_json(new / "sources.json", sources)
        with zipfile.ZipFile(rt.ZIP) as archive:
            for scene in plan["sessions"]:
                (new / "scenes" / scene["file"]).write_bytes(archive.read(scene["file"]))
        new.rename(stage)
    print(f"Frozen 60 tasks from {len(plan['sessions'])} scenes, {plan['expected_checks']} checks/arm", flush=True)


def validate_plan(stage, live=False):
    plan = rt.read_json(stage / "plan.json")
    sources = rt.read_json(stage / "sources.json")
    if "historical_comparison" in plan or plan.get("paired_uncapped"):
        frozen = stage/"adapter"
        config = rt.read_json(frozen/checkpoint.CONFIG)
        expected = candidate_plan(config, sources, uncapped=plan.get("paired_uncapped", False),
                                  context_tokens=plan["student"]["budgets"]["context_tokens"],
                                  generation_tokens=plan["student"]["budgets"]["generation_tokens"])
        if rt.digest((frozen/checkpoint.WEIGHTS).read_bytes()) != config["weights_sha256"]:
            raise ValueError("Frozen candidate adapter checksum mismatch")
        if "historical_comparison" in plan and rt.digest((stage/"historical-pairs.csv").read_bytes()) != REFERENCE["pairs_sha256"]:
            raise ValueError("Frozen historical A60 pairs checksum mismatch")
    else:
        expected = build_plan()
    if plan != expected:
        raise ValueError("Saved validation plan differs from the approved held-out subset")
    if live and sources != source_hashes():
        raise ValueError("Stage sources differ from the frozen run")
    for scene in plan["sessions"]:
        if rt.digest((rt.DATA / scene["file"]).read_bytes()) != scene["sha256"]:
            raise ValueError(f"Scene changed: {scene['id']}")
    return plan


def same_or_save(path, value):
    if path.exists():
        if rt.read_json(path) != value:
            raise ValueError(f"Frozen {path.name} differs from this run")
    else:
        rt.save_json(path, value)


def launch_settings(endpoint, arm):
    url = urlsplit(endpoint)
    if url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1"} or url.path != "/v1" or url.query or url.fragment or url.username:
        raise ValueError("The student endpoint must be a loopback HTTP /v1 service")
    tag = f"qwen35-2b-{arm}"
    return {"selected_provider": "flockcue_student", "share_blackboard_by_session": True,
            "toolchain_json_recording": True, "uav_api_key": "", "provider_configs": {
                "flockcue_student": {"type": "openai-compatible", "default_model": tag, "default_models": [tag],
                                     "base_url": endpoint, "api_key": "local-student", "requires_api_key": True}}}


def check_provider(endpoint, arm, plan):
    student = plan["student"]
    expected = {key: student[key] for key in ("base_revision", "thinking", "sampling", "budgets", "seed_rule")}
    expected.update(status="ready", model=f"qwen35-2b-{arm}",
                    adapter_sha256=student["sft_weights_sha256"] if arm == "sft" else None)
    if "adapter_config_digest" in student:
        expected["template_sha256"] = student_adapter.FILES["chat_template.jinja"]
    health = rt.API(endpoint.removesuffix("/v1"), "").call("GET", "/health", timeout=5)
    if any(k not in health or health[k] != v for k,v in expected.items()):
        raise ValueError(f"Student service does not match registered {arm} arm: {health!r}")
    return expected


def deployment_packages():
    command = ("import importlib.metadata as m,json,sys; "
               "print(json.dumps({'python':sys.version.split()[0],'packages':"
               "sorted([(d.metadata['Name'],d.version) for d in m.distributions()])}))")
    return {role: json.loads(subprocess.check_output([str(rt.interpreter(role)), "-c", command]))
            for role in rt.ENVIRONMENTS}


def scene_generations(arm_root, scene):
    return sorted((arm_root / scene["id"]).glob("attempt-*"))


def sealed_record(arm_root, scene):
    complete = [p for p in scene_generations(arm_root, scene) if (p/"complete.json").is_file()]
    if len(complete) > 1:
        raise ValueError(f"Multiple completed generations for {arm_root.name}/{scene['id']}")
    if complete:
        return {"directory": str(complete[0].relative_to(arm_root))}


def arm_identity(stage, arm):
    paths = [stage/"plan.json", stage/"sources.json", stage/"packages.json", stage/arm/"provider.json",
             stage/arm/"agent-settings.json"]
    return {str(p.relative_to(stage)): rt.digest(p.read_bytes()) for p in paths}


def run(stage, arm, endpoint, scene_id):
    plan = validate_plan(stage, live=True)
    packages = deployment_packages()
    if "adapter_config_digest" in plan["student"]:
        if "historical_comparison" in plan and arm != "sft":
            raise ValueError("The candidate stage uses the archived base and first-SFT controls")
        if parallel.json_digest(rt, packages) != REFERENCE["deployment_packages_digest"]:
            raise ValueError("Simulation, controller or agent packages differ from the historical A60 deployment")
        if scene_id and any(not sealed_record(stage/arm, s) for s in plan["sessions"][:SCENE_IDS.index(scene_id)]):
            raise ValueError("Candidate scenes must run in the registered order")
    settings = launch_settings(endpoint, arm)
    arm_root = stage / arm
    arm_root.mkdir(exist_ok=True)
    same_or_save(stage/"packages.json", packages)
    same_or_save(arm_root/"agent-settings.json", settings)
    same_or_save(arm_root/"provider.json", check_provider(endpoint, arm, plan))
    same_or_save(arm_root/"manifest.json", {"arm": arm, "identity_sha256": parallel.json_digest(rt, arm_identity(stage,arm))})
    stop = threading.Event()
    previous = {s: signal.signal(s, lambda *_: stop.set()) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        for scene in plan["sessions"]:
            if scene_id and scene["id"] != scene_id:
                continue
            if record := sealed_record(arm_root, scene):
                parallel.verify_scene(rt, arm_root, plan, scene, record)
                continue
            rt.check_stop(stop)
            check_provider(endpoint, arm, plan)
            directory = arm_root / scene["id"] / f"attempt-{len(scene_generations(arm_root,scene))+1:04d}"
            directory.mkdir(parents=True)
            class Progress:
                def put(self, update):
                    scored = [row for row in update["rows"] if row["status"] == "scored"]
                    rt.save_json(arm_root/"progress.json", {
                        "scene": scene["id"], "directory": str(directory.relative_to(arm_root)),
                        "updated_at": parallel.timestamp(), "status": update["status"],
                        "scene_scored": len(scored), "scene_checker_passed": sum(r["task_pass"] for r in scored),
                    })
            result = parallel.run_scene(rt, arm_root, plan, scene, directory, "local-student", stop, Progress())
            if result["status"] != "completed":
                raise RuntimeError(f"Incomplete scene; evidence retained in {directory}: {result['error']}")
            parallel.verify_scene(rt, arm_root, plan, scene, sealed_record(arm_root,scene))
    finally:
        for s,handler in previous.items():
            signal.signal(s, handler)


def scored_tasks(stage, arm, plan):
    arm_root = stage / arm
    if rt.read_json(arm_root/"manifest.json") != {"arm":arm, "identity_sha256":parallel.json_digest(rt,arm_identity(stage,arm))}:
        raise ValueError("Arm identity differs from its frozen inputs")
    tasks = {}
    for scene in plan["sessions"]:
        record = sealed_record(arm_root,scene)
        if not record:
            raise ValueError(f"Incomplete arm {arm}, scene {scene['id']}")
        directory = arm_root / record["directory"]
        verified = parallel.verify_scene(rt, arm_root, plan, scene, record)
        terminal = {}
        errors = {}
        commands = {}
        with (directory/"agent-traces/reproduction-metrics.jsonl").open() as stream:
            for event in map(json.loads, stream):
                if event["event"] == "llm_start":
                    commands[event["job_id"]] = event["command_id"]
                if event["event"] == "llm_error":
                    job = event["job_id"]
                    errors[job] = event.get("student_error_code") or event.get("error_type") or "llm_error"
                    if isinstance(event.get("status_code"), int) and event["status_code"] >= 400:
                        terminal[job] = event["status_code"]
        command_stops = {}
        if "historical_comparison" in plan:
            for path in (directory/"agent-traces/toolchains").glob("*.json"):
                chain = rt.read_json(path)
                command_stops[chain["command_id"]] = chain["status"]
        for row in verified:
            job = row["job_id"]
            if job in terminal:
                reason = errors[job]
            elif row["agent_timed_out"]:
                reason = "task_deadline"
            elif not row["continuation_safe"] or row["agent_status"] == "failed":
                reason = "agent_failed"
            elif not row["task_pass"]:
                reason = errors.get(job, "original_checks_failed")
            else:
                reason = ""
            tasks[row["task_id"]] = {
                "pass": row["task_pass"] and job not in terminal, "checker": row["task_pass"],
                "terminal_http_status": terminal.get(job), "timed_out": row["agent_timed_out"],
                "failure_reason": reason, "agent_status": row["agent_status"],
                "passed_checks": sum(leaf["result"] for leaf in row["leaves"]),
            }
            if "historical_comparison" in plan:
                stop = command_stops.get(commands.get(job), "unrecorded")
                if job in terminal or row["agent_timed_out"]:
                    stop = reason
                tasks[row["task_id"]]["stop_reason"] = stop
    return tasks


def report_candidate(stage, plan):
    scores = scored_tasks(stage, "sft", plan)
    with (stage/"historical-pairs.csv").open(newline="", encoding="utf-8") as stream:
        historical = list(csv.DictReader(stream))
    expected = [(scene["id"], str(i), task["id"], str(len(task["leaves"])))
                for scene in plan["sessions"] for i, task in enumerate(scene["tasks"], 1)]
    if ([(r["session_id"], r["ordinal"], r["task_id"], r["checks"]) for r in historical] != expected
            or set(scores) != {key[2] for key in expected}):
        raise ValueError("A60 result does not cover the exact registered tasks and scene order")
    counts = {arm: {measure: Counter() for measure in ("pass", "checker")} for arm in ARMS}
    pairs = []
    for previous in historical:
        score = scores[previous["task_id"]]
        row = {key: previous[key] for key in ("session_id", "family", "ordinal", "task_id", "task",
                                                "content", "difficulty", "checks")}
        for arm in ARMS:
            for measure in ("pass", "checker"):
                prior = {"True": True, "False": False}[previous[f"{arm}_{measure}"]]
                outcome = {(False, True): "gained", (True, False): "regressed",
                           (True, True): "both_pass", (False, False): "both_fail"}[prior, score[measure]]
                row[f"historical_{arm}_{measure}"] = prior
                row[f"{measure}_vs_{arm}"] = outcome
                counts[arm][measure][outcome] += 1
        row.update({f"candidate_{k}": v for k, v in score.items()})
        pairs.append(row)
    comparison = {}
    for arm, measures in counts.items():
        comparison[arm] = {}
        for measure, count in measures.items():
            comparison[arm][measure] = {
                "historical_passes": count["both_pass"] + count["regressed"],
                **{key: count[key] for key in ("both_pass", "both_fail", "gained", "regressed")},
                "delta_percentage_points": 100 * (count["gained"] - count["regressed"]) / len(pairs),
            }
    by_scene = {}
    for scene in plan["sessions"]:
        rows = [scores[t["id"]] for t in scene["tasks"]]
        by_scene[scene["id"]] = {"family": scene["family"], "tasks": len(rows),
                                 "formal_passes": sum(r["pass"] for r in rows),
                                 "original_checker_passes": sum(r["checker"] for r in rows),
                                 "passed_leaf_checks": sum(r["passed_checks"] for r in rows),
                                 "leaf_checks": sum(len(t["leaves"]) for t in scene["tasks"])}
    summary = {
        "tasks": len(pairs), "leaf_checks_per_arm": plan["expected_checks"],
        "adapter_sha256": plan["student"]["sft_weights_sha256"],
        "formal_passes": sum(s["pass"] for s in scores.values()),
        "original_checker_passes": sum(s["checker"] for s in scores.values()),
        "passed_leaf_checks": sum(s["passed_checks"] for s in scores.values()),
        "stop_reasons": dict(Counter(s["stop_reason"] for s in scores.values())),
        "by_scene": by_scene,
        "historical_controls": plan["historical_comparison"], "paired_comparison": comparison,
        "scene_generations": {s["id"]: len(scene_generations(stage/"sft", s)) for s in plan["sessions"]},
    }
    return save_report(stage, pairs, summary)


def report(stage):
    plan = validate_plan(stage)
    if "historical_comparison" in plan:
        return report_candidate(stage, plan)
    by_arm = {arm:scored_tasks(stage,arm,plan) for arm in ARMS}
    pairs = []
    outcomes = {(True,True):"both_pass", (False,False):"both_fail",
                (False,True):"gained",(True,False):"regressed"}
    for scene in plan["sessions"]:
        original = rt.read_json(rt.DATA / scene["file"])
        for i,task in enumerate(scene["tasks"],1):
            info = {arm: by_arm[arm][task["id"]] for arm in ARMS}
            change = outcomes[info["base"]["pass"],info["sft"]["pass"]]
            checker_change = outcomes[info["base"]["checker"],info["sft"]["checker"]]
            row = dict(session_id=scene["id"], family=scene["family"], ordinal=i, task_id=task["id"],
                       task=task["name"], content=original["tasks"][i-1]["content"], difficulty=task["difficulty"],
                       checks=len(task["leaves"]), outcome=change, checker_outcome=checker_change)
            for arm in ARMS:
                row.update({f"{arm}_{key}":value for key,value in info[arm].items()})
            pairs.append(row)
    counts = Counter(row["outcome"] for row in pairs)
    gains, losses = counts["gained"], counts["regressed"]
    discordant = gains+losses
    p = min(1.0, 2*sum(math.comb(discordant,k) for k in range(min(gains,losses)+1))/2**discordant)
    result = {"tasks":len(pairs), "leaf_checks_per_arm":plan["expected_checks"],
              "arms":{a:sum(row[f"{a}_pass"] for row in pairs) for a in ARMS},
              "pairs":{k:counts[k] for k in ("both_pass","gained","regressed","both_fail")},
              "net_tasks":gains-losses,"delta_percentage_points":100*(gains-losses)/len(pairs),
              "exact_mcnemar_two_sided_p":p,
              "original_checker_only": {
                  "arms":{a:sum(row[f"{a}_checker"] for row in pairs) for a in ARMS},
                  "pairs":{k:sum(row["checker_outcome"]==k for row in pairs) for k in outcomes.values()}},
              "scene_generations":{a:{s["id"]:len(scene_generations(stage/a,s)) for s in plan["sessions"]} for a in ARMS}}
    return save_report(stage, pairs, result)


def save_report(stage, pairs, result):
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(pairs[0]))
    writer.writeheader()
    writer.writerows(pairs)
    temporary = stage/"pairs.csv.tmp"
    temporary.write_text(buffer.getvalue(), encoding="utf-8")
    temporary.replace(stage/"pairs.csv")
    rt.save_json(stage/"summary.json",result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command",required=True)
    for name in ("prepare","run","report"):
        parser = commands.add_parser(name)
        parser.add_argument("--stage",type=Path,required=True)
        if name == "prepare":
            parser.add_argument("--context-tokens", type=int, choices=EVALUATION_CONTEXTS, default=MAX_CONTEXT)
            parser.add_argument("--generation-tokens", type=int, choices=EVALUATION_GENERATIONS, default=MAX_GENERATION)
            parser.add_argument("--no-episode-token-limit", action="store_true",
                                help="Run both arms live with no cumulative per-task token limit")
            parser.add_argument("--adapter", type=Path,
                                help="Freeze these LoRA files and evaluate one SFT arm against archived A60 controls")
        if name == "run":
            parser.add_argument("--arm",choices=ARMS,required=True)
            parser.add_argument("--endpoint",required=True)
            parser.add_argument("--scene",choices=SCENE_IDS)
    args = cli.parse_args()
    stage = args.stage.resolve()
    rt.use_stage(stage)
    if args.command == "prepare":
        prepare(stage, args.adapter, args.no_episode_token_limit, args.context_tokens, args.generation_tokens)
    else:
        with parallel.attempt_lock(stage):
            if args.command == "run":
                run(stage,args.arm,args.endpoint,args.scene)
            else:
                report(stage)


if __name__ == "__main__":
    main()
