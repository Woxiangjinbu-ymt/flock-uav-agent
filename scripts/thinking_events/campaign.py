"""Prepare and verify a relocatable 29-task campaign. Formal inference requires T37."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import hashlib
import os
import platform
import shutil
import sys
from tempfile import TemporaryDirectory

from prepare_task_dataset import flatten, parse_json, safe_path

from .campaign_storage import (
    RentalBudget, SlotLedger, atomic_write, digest, json_bytes, locked, read_jsonl, save_rows,
)
from .student import EPISODE_TOKEN_LIMIT, MAX_CONTEXT, MAX_GENERATION, MODEL_REVISIONS, SAMPLING

ROOT = Path(__file__).resolve().parents[2]
DATASET_SHA = "4d2da10b0a6ea753a924ec22f6f16ad29d59711a94ecef6fbd013cbadf226f4e"
# Two independent CPython 3.11.16 materializations of the locked five dev
# scenes produced these exact 29 snapshots and five timelines. Check this pin
# before a transported pickle can reach the worker's deserializer.
DEV_PICKLES_SHA = "de8a8f8d3d0204ec02d11d050e97dd38045c9153d643690a479fe312673d9789"
G3_SHA = "fcb9a63f1a99b24e30372d70cc06b37f889feba129829100cdf214477edca8a4"
MODELS = MODEL_REVISIONS
LEGACY_PROTOCOL = "thinking-events-dev-v1"
PAIRED_PROTOCOL = "thinking-events-dev-v2"
PAIRED_MODELS = {"Qwen3.5-2B": "15852e8c16360a2fea060d615a32b45270f8a8fc",
                 "Qwen3-4B": MODEL_REVISIONS["4B"]}
# Checksum of the corrected tool source in the paired Mac hover experiment.
PAIRED_TOOLS_SHA = "8fc01b2a4e8488f5d5ee3171cc673adf12fec611b47dc24526171cbb873f990f"
RUNTIME = {"task_seconds": 500, "drain_seconds": 0.5,
           "request_limits": {"max_http_attempts": 12, "max_total_tokens": EPISODE_TOKEN_LIMIT,
                              "max_output_tokens": MAX_GENERATION, "per_request_timeout_seconds": 120}}


def _checked(base, name, sha):
    data = safe_path(base, name).read_bytes()
    if digest(data) != sha:
        raise ValueError(f"Content digest mismatch: {name}")
    return data


def _sealed(base, name, seal):
    return parse_json(_checked(base, name, safe_path(base, seal).read_text().strip()))


def _check_pickle_inventory(files):
    hashes = {name: sha for name, sha in files.items() if name.endswith(".pkl")}
    if digest(json_bytes(hashes)) != DEV_PICKLES_SHA:
        raise ValueError("Transported snapshot/timeline differs from the frozen dev inputs")


def _copy_files(src, dest, files):
    for name, expected in files.items():
        data = _checked(src, name, expected)
        target = safe_path(dest, name)
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_bytes(data)


def _study_inputs(study):
    """Authenticate every selected fixture against its originally assigned dev task."""
    m = _sealed(study, "manifest.json", "manifest.sha256")
    if (m.get("scope"), m.get("split"), m.get("purpose"), m.get("origin_manifest_sha256"),
        m.get("timeline_seconds")) != ("task_dataset_materialization_v1", "dev", "development", DATASET_SHA, 501):
        raise ValueError("Expected frozen dev/development study with the full 501-second timeline")
    origin = parse_json(_checked(study, "origin-manifest.json", DATASET_SHA))
    originals = {row["task_id"]: row for row in origin["tasks"] if row["split"] == "dev"}
    planned = [r["task_id"] for r in m["tasks"]]
    if set(planned) != set(originals) or len(planned) != 29 or len(originals) != 29:
        raise ValueError("Development task set differs from the 29 sealed tasks")
    if any(r.get("restore_verified") is not True or r.get("checker_errors") != 0
           or r["scene_id"] != originals[r["task_id"]]["scene_id"] for r in m["tasks"]):
        raise ValueError("Development restore/checker preflight incomplete")
    inputs = {"origin-manifest.json": DATASET_SHA, "preflight.json": m["files"]["preflight.json"]}
    tasks = []
    for task_id, original in originals.items():
        name = f"fixtures/{task_id}.json"
        src = original["scene_file"]
        raw = _checked(study, name, m["files"][name])
        fixture = parse_json(raw)
        # Recheck command and full checker definitions rather than trusting the
        # fixture's reported restore result. No unpickling occurs during prepare.
        if (any(fixture.get(k) != v for k, v in original.items() if k != "quality")
                or fixture["source"] != src or fixture["source_sha256"] != origin["files"][src]
                or fixture["checks"] != flatten(original["original_task"]["execution_check_apis"])
                or fixture["timeline_file"] != f"scene-{original['scene_id']}.timeline.pkl"):
            raise ValueError(f"Task/checker/scene binding differs: {task_id}")
        files = {name: digest(raw), f"fixtures/{task_id}.pkl": fixture["snapshot_sha256"],
                 f"fixtures/{fixture['timeline_file']}": fixture["timeline_sha256"],
                 src: origin["files"][src]}
        for filename, sha in files.items():
            if m["files"].get(filename) != sha or inputs.get(filename, sha) != sha:
                raise ValueError(f"Study input seal differs: {task_id}/{filename}")
        inputs.update(files)
        tasks.append({"task_id": task_id, "scene_id": original["scene_id"], "task_family": original["task_family"],
                      "fixture_sha256": digest(raw), "snapshot_sha256": fixture["snapshot_sha256"],
                      "timeline_sha256": fixture["timeline_sha256"], "command_sha256": original["command_sha256"],
                      "checker_sha256": digest(json_bytes(original["original_task"]["execution_check_apis"]))})
    if len({r["scene_id"] for r in tasks}) != 5:
        raise ValueError("Expected five development parent scenes")
    _check_pickle_inventory(inputs)
    # The materializer records its originating host path. Export a portable
    # materialization manifest for the selected files; preserve the dataset pin.
    portable = {**m, "origin_dataset": "experiments/task-splits-v1", "files": dict(inputs)}
    return sorted(tasks, key=lambda r: (r["scene_id"], r["task_id"])), inputs, portable


def _frozen_sources(root, protocol):
    lock_name = "agent_harness/baseline.lock.json"
    baseline = parse_json(safe_path(root, lock_name).read_bytes())
    orig = safe_path(root, baseline["reference_campaign"])
    sealed = parse_json(_checked(orig, "campaign-seal.json", baseline["campaign_seal_sha256"]))["files"]
    ref = parse_json(_checked(orig, "runs/action/manifest.json", sealed["runs/action/manifest.json"]))
    if platform.python_version() != ref["python"].split()[0]:
        raise ValueError("Use CPython " + ref["python"].split()[0] + " for this experiment")
    sources = {**ref["sources"], **baseline["dependency_files"], baseline["profile"]: sealed["selection-source/" + baseline["profile"]]}
    extras = [lock_name, "scripts/prepare_task_dataset.py", "scripts/convert_agent_samples.py",
              "scripts/student_input.py", "scripts/student_input_v2.py", "training/requirements-input.lock",
              "training/templates/qwen3-tool-ids-v2.jinja"]
    if protocol == LEGACY_PROTOCOL:
        for backend in ("inference", "vllm"):
            extras.extend(f"training/requirements-{backend}-linux.{suffix}" for suffix in ("in", "lock"))
        legacy_models = MODELS
    else:
        extras.extend(["scripts/flockcue_qwen35/" + name for name in
                       ("__init__.py", "adapter.py", "mac_model.py", "takeoff_example.py")])
        extras.append("training/model-manifests/Qwen3.5-2B.json")
        legacy_models = {"4B": MODEL_REVISIONS["4B"]}
    for model, revision in legacy_models.items():
        name = f"training/model-manifests/{model}.json"
        expected = parse_json(safe_path(root, name).read_bytes())
        if expected["revision"] != revision or expected["repository"] != "Qwen/Qwen3-" + model:
            raise ValueError(f"Model manifest differs from the approved revision: {model}")
        extras.append(name)
    for pattern in ("scripts/thinking_events/*.py", "training/tokenizers/qwen3-1.7b-input-v1/*"):
        extras.extend(str(p.relative_to(root)) for p in sorted(root.glob(pattern)) if p.is_file())
    for name in extras:
        sources[name] = digest(safe_path(root, name).read_bytes())
    return sources, baseline, ref


def turn_seed(task_id, turn):
    if type(turn) is not int or not 1 <= turn <= 12:
        raise ValueError("Turn exceeds frozen request bounds")
    return int.from_bytes(hashlib.sha256(f"{task_id}/{turn}".encode()).digest()[:4], "big")


def planned_slots(tasks, protocol=PAIRED_PROTOCOL):
    if protocol not in (LEGACY_PROTOCOL, PAIRED_PROTOCOL):
        raise ValueError("Unknown paired-study protocol")
    models = MODELS if protocol == LEGACY_PROTOCOL else PAIRED_MODELS
    slots = []
    for model in models:
        for index, task in enumerate(tasks):
            first = index % 2 == 0
            for thinking in (first, not first):
                arm = "on" if thinking else "off"
                prefix = "qwen3-" + model if protocol == LEGACY_PROTOCOL else model
                slot_id = f"{prefix.lower().replace('.', 'p')}-{arm}-{task['task_id']}"
                slots.append({**task, "slot_id": slot_id, "trial_id": slot_id, "model": model, "thinking": thinking,
                              "arm": arm, "order": len(slots) + 1,
                              "turn_seeds": [turn_seed(task["task_id"], turn) for turn in range(1, 13)]})
    return slots


def _paired_config(sources):
    from flockcue_qwen35 import adapter, mac_model, takeoff_example
    if adapter.REVISION != PAIRED_MODELS["Qwen3.5-2B"]:
        raise ValueError("2B adapter is not pinned to the main model")
    return {
        "main": "Qwen3.5-2B", "reference": "Qwen3-4B",
        "tools_source_sha256": sources[takeoff_example.SOURCE],
        "qwen35_tokenizer_files": adapter.FILES,
        "qwen35_weight": {"file": mac_model.WEIGHT_FILE, "bytes": mac_model.WEIGHT_BYTES,
                          "sha256": mac_model.WEIGHT_SHA256},
        "qwen35_adapter_sha256": sources["scripts/flockcue_qwen35/adapter.py"],
        "qwen3_template_sha256": sources["training/templates/qwen3-tool-ids-v2.jinja"],
        "qwen3_adapter_sha256": sources["scripts/thinking_events/student.py"],
        "qwen3_manifest_sha256": sources["training/model-manifests/4B.json"],
        "linux_backend": "pending_compatibility_and_t37", "paid_launch_enabled": False,
    }


def prepare_dev(study, output, *, protocol=PAIRED_PROTOCOL):
    study, output = Path(study).resolve(), Path(output).absolute()
    if output.exists() or output.is_symlink() or output.resolve().is_relative_to(study):
        raise FileExistsError(f"Use a fresh directory outside the materialized study: {output}")
    tasks, inputs, portable_study = _study_inputs(study)
    slots = planned_slots(tasks, protocol)
    sources, baseline, ref = _frozen_sources(ROOT, protocol)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix=".prepare-dev-") as tmp:
        stage = Path(tmp)
        _copy_files(study, stage / "study", inputs)
        raw_study = json_bytes(portable_study)
        study_seal = (digest(raw_study) + "\n").encode()
        (stage / "study/manifest.json").write_bytes(raw_study)
        (stage / "study/manifest.sha256").write_bytes(study_seal)
        inputs.update({"manifest.json": digest(raw_study), "manifest.sha256": digest(study_seal)})
        _copy_files(ROOT, stage / "source", sources)
        if protocol == PAIRED_PROTOCOL:
            from flockcue_qwen35.takeoff_example import prepare_variant
            tool_example = prepare_variant(stage, sources)
        save_rows(stage / "plan-slots.jsonl", slots)
        campaign = {"schema": protocol, "split": "dev", "purpose": "development",
                    "dataset_sha256": DATASET_SHA, "g3_reference_sha256": G3_SHA,
                    "sources": sources, "inputs": inputs,
                    "model_revisions": MODELS if protocol == LEGACY_PROTOCOL else PAIRED_MODELS,
                    "plan_slots_sha256": digest((stage / "plan-slots.jsonl").read_bytes()),
                    "agent_profile": ref["agent_profile"], "profile_file": baseline["profile"],
                    "study": "study", "workspace_source": "source", "runtime": RUNTIME,
                    "formal_admission": {"host_preparation": "T36a", "calibration": "T37",
                                         "open_estimated_seconds_max": 250, "full_p95_required": True},
                    "generation": {"dtype": "bfloat16", "batch_size": 1,
                                   "context_tokens": MAX_CONTEXT, **SAMPLING},
                    "cost_caps": {"cards": 1, "card": "A100 40GB", "seconds": 72000, "total_cny": 200,
                                  "training_hours": 0, "teacher_calls": 0}}
        if protocol == PAIRED_PROTOCOL:
            campaign["paired_protocol"] = _paired_config(sources)
            campaign["tool_example"] = tool_example
        else:
            campaign["backend_candidates"] = {"transformers": "4.57.3", "vllm": "0.11.2"}
        payload = json_bytes(campaign)
        (stage / "campaign.json").write_bytes(payload)
        (stage / "campaign.sha256").write_text(digest(payload) + "\n")
        SlotLedger(stage, slots).initialize()
        RentalBudget(stage).initialize()
        # Claim the directory exclusively; the seal is published after copying
        # all campaign inputs. Never replace an existing user's run.
        output.mkdir()
        try:
            for item in sorted(stage.iterdir(), key=lambda p: p.name == "campaign.sha256"):
                item.rename(output / item.name)
            bind_host(output)
        except Exception:
            shutil.rmtree(output)
            raise
    return {"schema": campaign["schema"], "tasks": len(tasks), "parent_scenes": 5, "slots": len(slots),
            "campaign_sha256": digest(payload), "formal_launches": 0}


def verify_run(run):
    run = Path(run).resolve()
    campaign = _sealed(run, "campaign.json", "campaign.sha256")
    protocol = campaign["schema"]
    models = MODELS if protocol == LEGACY_PROTOCOL else PAIRED_MODELS
    if (protocol not in (LEGACY_PROTOCOL, PAIRED_PROTOCOL) or campaign["split"] != "dev" or
            campaign["purpose"] != "development" or campaign["dataset_sha256"] != DATASET_SHA
            or campaign["g3_reference_sha256"] != G3_SHA or campaign["model_revisions"] != models):
        raise ValueError("Invalid dev campaign identity")
    _check_pickle_inventory(campaign["inputs"])
    for folder, items in (("source", campaign["sources"]), ("study", campaign["inputs"])):
        base = safe_path(run, folder)
        for name, sha in items.items():
            _checked(base, name, sha)
        for file in base.rglob("*"):
            rel = file.relative_to(base)
            if file.is_symlink() or (file.is_file() and str(rel) not in items):
                raise ValueError(f"Unexpected frozen file: {folder}/{rel}")
    _checked(run, "plan-slots.jsonl", campaign["plan_slots_sha256"])
    rows = read_jsonl(run / "plan-slots.jsonl")
    if (len(rows) != 116 or len({r["task_id"] for r in rows}) != 29 or
            Counter((r["model"], r["thinking"]) for r in rows) !=
            {(m, t): 29 for m in models for t in (True, False)}):
        raise ValueError("Invalid four-arm dev slot population")
    if protocol == PAIRED_PROTOCOL:
        tasks, _, _ = _study_inputs(run / "study")
        if rows != planned_slots(tasks, protocol) or campaign["paired_protocol"] != _paired_config(campaign["sources"]):
            raise ValueError("Revised pairing differs from frozen tasks, seeds or models")
        if campaign["paired_protocol"]["tools_source_sha256"] != PAIRED_TOOLS_SHA:
            raise ValueError("Revised campaign does not contain the reviewed tool example")
    SlotLedger(run, rows).read()
    return campaign


def bind_host(run):
    run = Path(run).resolve()
    with locked(run, ".binding.lock"):
        campaign = verify_run(run)
        binding = {"campaign_sha256": (run / "campaign.sha256").read_text().strip(),
                   "workspace": str(ROOT), "study": str(run / "study"), "source": str(run / "source"),
                   "python_executable": os.path.abspath(sys.executable)}
        rows = read_jsonl(run / "plan-slots.jsonl")
        trials = [{"trial_id": r["slot_id"], "task_id": r["task_id"], "scheduler": "applicability",
                   "runtime": RUNTIME, "watchdog_seconds": 515} for r in rows]
        legacy = {**binding, "scope": "thinking_events_dev_local_backend", "local_model": "frozen-student",
                  "inputs": campaign["inputs"], "sources": campaign["sources"], "trials": trials,
                  "private_canary": "private-dev-" + binding["campaign_sha256"][:32]}
        raw = json_bytes(legacy)
        atomic_write(run / "manifest.json", raw)
        atomic_write(run / "manifest.sha256", (digest(raw) + "\n").encode())
        atomic_write(run / "host-binding.json", json_bytes(binding))
        return binding


def g3_evidence(path, *, reference_sha=G3_SHA):
    try:
        path = Path(path)
        raw = path.read_bytes()
        if digest(raw) != reference_sha:
            raise ValueError("G3 certificate differs from the frozen T36 reference")
        g = parse_json(raw)
        if g["gate"] != "G3" or g["denominator"] != 183 or g["integrity_issues"]:
            raise ValueError("G3 decision incomplete")
        if set(g["inputs_sha256"]) != {"sources.json", "turns.jsonl", "attempts.jsonl"}:
            raise ValueError("G3 inputs incomplete")
        for name, sha in g["inputs_sha256"].items():
            _checked(path.parent, name, sha)
        if g["status"] not in ("pass", "fail", "indeterminate"):
            raise ValueError("Unknown G3 status")
        return {"status": g["status"], "verified": True, "sha256": digest(raw),
                "turns_sha256": g["inputs_sha256"]["turns.jsonl"]}
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return {"verified": False, "status": "indeterminate", "reason": str(exc)}


def run_dev(run, g3):
    """Persist the no-card decision. CPU fixtures have a separate run identity."""
    run = Path(run).resolve()
    with locked(run, ".coordinator.lock"):
        binding = bind_host(run)
        plan = read_jsonl(run / "plan-slots.jsonl")
        rows = SlotLedger(run, plan).read()
        evidence = g3_evidence(g3)
        if not evidence["verified"]:
            reason = "g3_evidence_invalid"
        elif evidence["status"] != "pass":
            reason = "g3_" + evidence["status"]
        else:
            reason = "awaiting_linux_preflight_and_t37"
        log = RentalBudget(run).initialize()
        result = {"scope": "cpu_preflight", "campaign_sha256": binding["campaign_sha256"],
                  "g3": evidence, "G1": "indeterminate", "G2": "indeterminate", "reason": reason,
                  "slot_statuses": dict(Counter(r["status"] for r in rows)),
                  "planned_slots": len(rows), "formal_launches_this_invocation": 0,
                  "gpu_launches": 0, "provider_calls": 0, "training_hours": 0,
                  "gpu_billed_seconds": log["gpu_billed_seconds"], "charged_cny": log["paid_cny"]}
        atomic_write(run / "dev-readiness.json", json_bytes(result))
        return result
