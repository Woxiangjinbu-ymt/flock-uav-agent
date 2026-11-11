"""Run the preregistered public two-model T37 calibration on the selected A100.

The coordinator uses the student interpreter. PYTHONPATH must include the
campaign's frozen source/scripts. All calibration outputs stay outside source.
"""
from __future__ import annotations


import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import platform
from statistics import mean
import struct
import sys
import time


_workspace = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts").is_dir())
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(_workspace / "scripts"))

from paired_t37_fixtures import LENGTHS, TOOLS, freeze
from paired_t37_stress import StressProcess
from thinking_events import paired_backend, student
from thinking_events.campaign import PAIRED_MODELS, g3_evidence, verify_run
from thinking_events.campaign_storage import atomic_write, digest, json_bytes, locked
from thinking_events.paired_admission import _linux_cpu, _measurements, _session_cpu
from thinking_events.paired_batch import _qualified_service, _reserve
from thinking_events.student_process import StudentProcess, receive, send


def read(path):
    return json.loads(path.read_bytes())


def fixed(path, value):
    raw = json_bytes(value)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError(f"Frozen T37 evidence changed: {path}")
    if not path.exists():
        atomic_write(path, raw)
    return digest(raw)


def check_frozen_inputs(fixtures, root):
    # Canonical JSON sorts keys. The pretrained chat templates serialize tool
    # schema dictionaries in insertion order, so always use the original
    # preregistered field order and prove it reproduces every frozen ID.
    if fixtures["tools"] != TOOLS:
        raise ValueError("Stored T37 tool schema differs from the public specification")
    checked = set()
    for model in PAIRED_MODELS:
        tokenizer, codec, _, _ = paired_backend.load_codec(model, root / model)
        for item in fixtures["schedule"]:
            key = item["fixture"]
            if item["model"] != model or key in checked:
                continue
            fixture = fixtures["histories"][key]
            prompt, _ = codec.render(tokenizer, fixture["messages"], TOOLS, item["arm"] == "on")
            if prompt["input_ids"] != fixture["input_ids"] or prompt["template_sha256"] != fixture["template_sha256"]:
                raise ValueError(f"Cannot reproduce frozen public token IDs and template: {key}")
            checked.add(key)
    if checked != set(fixtures["histories"]):
        raise ValueError("Unexpected public fixtures outside the schedule")
    print(f"Reproduced all {len(checked)} frozen input ID sequences before model load", flush=True)


def reserve(run, lease, seconds):
    reason = _reserve(run, {"lease": lease}, seconds)
    if reason != "admitted":
        raise RuntimeError(f"T37 budget stop: {reason}")


def stress_request(service, tokenizer, codec, fixture, tools, spec, eos, partial):
    started = time.monotonic()
    deadline = started + 120
    prompt, _ = codec.render(tokenizer, fixture["messages"], tools, True)
    if prompt["input_ids"] != fixture["input_ids"]:
        raise ValueError("Stress input differs from frozen public fixture")
    ids = prompt["input_ids"] + fixture["stress_think_prefill_ids"]
    try:
        send(service.sock, {"ids": ids, "seed": spec["seed"], "partial": str(partial)}, deadline)
        result = receive(service.sock, deadline)
        end = time.monotonic()
        if end >= deadline:
            raise TimeoutError
    except TimeoutError:
        service.close()
        raw = partial.read_bytes() if partial.is_file() else b""
        observed = [v[0] for v in struct.iter_unpack("<I", raw)]
        return {"status": "stopped", "stop_reason": "request_timeout", "input_ids": ids,
                "generated_ids": observed, "emitted_observation": "streamed_prefix" if raw else "no_ids_delivered",
                "count_is_lower_bound": True, "request_seconds": time.monotonic() - started}
    if not result.pop("ok", False):
        raise RuntimeError(f"Stress generation failed: {result}")
    first = result.pop("first_token_monotonic")
    result["timing"]["ttft_seconds"] = None if first is None else first - started
    generated = result["generated_ids"]
    if len(generated) != student.MAX_GENERATION or set(generated) & {*eos, tokenizer.convert_tokens_to_ids("</think>")}:
        raise ValueError("Stress output did not remain in the open thought for all 2048 new IDs")
    return {**result, "status": "ok", "stop_reason": None, "input_ids": ids, "request_seconds": end - started}


def check_natural(row, fixture, eos):
    if row.get("stop_reason") == "request_timeout":
        return
    if row.get("status") not in ("ok", "format_error"):
        raise RuntimeError(f"Natural student service error: {row.get('stop_reason')} {row.get('error')}")
    generated = row["generated_ids"]
    if row["input_ids"] != fixture["input_ids"] or not row["usage"] or not (
        row["usage"]["prompt_tokens"] == len(row["input_ids"]) and row["usage"]["completion_tokens"] == len(generated)
        and row["usage"]["total_tokens"] == len(row["input_ids"]) + len(generated)
        and bool(generated and generated[-1] in eos) == row["terminated"]
    ):
        raise ValueError("Natural generation differs from frozen input, usage or EOS")
    if not 0 < row["request_seconds"] <= 120:
        raise ValueError("Natural response completed outside its request deadline")


def summarize(directory, fixtures, backend, campaign_sha, fixtures_sha, records, runtime):
    models, reasons, integrity = {}, {}, {}
    for name, revision in PAIRED_MODELS.items():
        rows = [r for r in records if r["model"] == name]
        warms = [r for r in rows if r["kind"] == "warmup"]
        bins, groups = {}, {}
        for arm in ("on", "off"):
            bins[arm], groups[arm] = {}, {}
            for length in LENGTHS:
                group = [r["response"] for r in rows if (r["kind"], r["arm"], r["target"]) == ("natural", arm, length)]
                times = [r["request_seconds"] if r.get("terminated") else None for r in group]
                bins[arm][str(length)] = times
                groups[arm][str(length)] = {
                    "n": len(group), "natural_ends": sum(t is not None for t in times),
                    "timeouts": sum(r.get("stop_reason") == "request_timeout" for r in group),
                    "short_outputs": sum(len(r["generated_ids"]) < 2 for r in group if r.get("generated_ids") is not None),
                    "mean_complete_seconds": mean(t for t in times if t is not None) if any(times) else None,
                }
        full = []
        for row in rows:
            if row["kind"] == "stress":
                resp = row["response"]
                full.append({"seconds": resp["request_seconds"] if resp["status"] == "ok" else None,
                             "generated_tokens": len(resp["generated_ids"]),
                             "count_is_lower_bound": resp.get("count_is_lower_bound", False)})
        ok = sorted(r["seconds"] for r in full if r["seconds"] is not None)
        functional = {r["arm"] for r in warms if r["response"].get("status") == "ok"
                      and r["response"].get("terminated") and r["response"]["usage"]["completion_tokens_details"]
                      ["reasoning_tokens"] is not None} == {"on", "off"}
        estimates = {}
        if all(all(bins["on"][str(k)]) for k in LENGTHS):
            short, mid, long = [mean(bins["on"][str(k)]) for k in LENGTHS]
            estimates = {"seven_round_seconds": short + 2*mid + 4*long,
                         "twelve_round_seconds": 7*short + 4*mid + long}
            estimates["largest_share_of_episode"] = max(estimates.values()) / 500
        summary = {"revision": revision, "functional_smoke": functional, "natural_seconds": bins,
                   "natural_groups": groups, "workload": estimates, "full_length": full,
                   "full_length_p95_seconds": ok[18] if len(full) == 20 and len(ok) >= 19 else None}
        models[name] = summary
        try:
            _measurements(summary, revision)
            # A truncated natural sample cannot masquerade as a natural end.
            if any(r["kind"] == "natural" and r["response"].get("stop_reason") != "request_timeout"
                   and not r["response"].get("terminated") for r in rows):
                raise ValueError("A natural request exhausted its generation limit without EOS")
        except ValueError as exc:
            reasons[name] = str(exc)
    for r in records:
        name = "records/" + r["id"] + ".json"
        integrity[name] = digest((directory / name).read_bytes())
    archive = {"schema": "paired-t37-measurements-v1", "backend": backend, "campaign_sha256": campaign_sha,
               "models": models, "fixtures_sha256": fixtures_sha, "raw_files_sha256": integrity,
               "request_count": len(records), "wall_seconds": runtime}
    measured = fixed(directory / "measurements.json", archive)
    verdict = {"passed": not reasons, "reasons": reasons, "measurement_sha256": measured}
    fixed(directory / "verdict.json", verdict)
    print(json.dumps(verdict), flush=True)
    return verdict


def run_measurement(args):
    run = args.run.resolve()
    if platform.system() != "Linux":
        raise ValueError("T37 requires the verified Linux host")
    verify_run(run)
    if not g3_evidence(args.g3).get("verified") or g3_evidence(args.g3)["status"] != "pass":
        raise ValueError("Missing verified G3 pass")
    _linux_cpu(read(args.linux_cpu))
    _session_cpu(read(args.session_cpu))
    root = args.model_root.resolve()
    common = run / "t37"
    directory = common / args.backend
    directory.mkdir(parents=True, exist_ok=True)
    with locked(common, ".measurement.lock"):
        campaign_sha = (run / "campaign.sha256").read_text().strip()
        if args.backend == "vllm" and read(common / "transformers/verdict.json")["passed"] is not False:
            raise ValueError("Fallback needs a recorded Transformers failure")
        fixture_file = common / "public-fixtures.json"
        if fixture_file.exists():
            fixtures = read(fixture_file)
        else:
            fixtures = freeze(root)
            fixed(fixture_file, fixtures)
        fixtures_sha = fixed(directory / "fixtures.json", fixtures)
        if not (common / "public-fixtures.sha256").exists():
            atomic_write(common / "public-fixtures.sha256", (fixtures_sha + "\n").encode())
        if (common / "public-fixtures.sha256").read_text().strip() != fixtures_sha:
            raise ValueError("Public calibration fixtures have changed")
        check_frozen_inputs(read(fixture_file), root)
        lease = read(args.lease)
        config = {"backend": args.backend, "backend_python": os.path.abspath(args.backend_python or sys.executable),
                  "student_python": os.path.abspath(sys.executable), "node": platform.node(),
                  "campaign_sha256": campaign_sha, "fixtures_sha256": fixtures_sha, "lease": lease,
                  "model_root": str(root), "source_scripts": str(run / "source/scripts")}
        fixed(directory / "configuration.json", config)
        if (directory / "verdict.json").exists():
            verdict = read(directory / "verdict.json")
            if digest((directory / "measurements.json").read_bytes()) != verdict["measurement_sha256"]:
                raise ValueError("Stored measurement archive differs from verdict")
            return verdict
        start_path = directory / "start.json"
        if not start_path.exists():
            fixed(start_path, {"unix": time.time()})
        records_dir = directory / "records"
        records_dir.mkdir(exist_ok=True)
        observed = []
        for model, revision in PAIRED_MODELS.items():
            tokenizer, codec, eos, _ = paired_backend.load_codec(model, root / model)
            schedule = [s for s in fixtures["schedule"] if s["model"] == model]
            for phase in ("natural", "stress"):
                with ExitStack() as stack:
                    service = None
                    for s in schedule:
                        if (s["kind"] == "stress") != (phase == "stress"):
                            continue
                        path = records_dir / (s["id"] + ".json")
                        fixture = fixtures["histories"][s["fixture"]]
                        if path.exists():
                            prior = read(path)
                            if any(prior[k] != v for k, v in s.items()) or prior["fixtures_sha256"] != fixtures_sha:
                                raise ValueError(f"Unexpected calibration record: {path}")
                            observed.append(prior)
                            continue
                        if service is None or service.process.poll() is not None:
                            stack.close()
                            reserve(run, lease, 180)
                            print(f"Loading {args.backend} {model} for {phase}", flush=True)
                            if phase == "natural":
                                service = stack.enter_context(StudentProcess(
                                    config["student_python"], backend=args.backend, model=model, model_path=root / model,
                                    model_manifest=paired_backend.pinned_manifest(model), revision=revision,
                                    backend_python=config["backend_python"], scripts_root=run / "source/scripts",
                                    startup_timeout=180))
                                _qualified_service(service.metadata, {"backend": args.backend, "versions": service.metadata})
                                fixed(directory / ("metadata-" + model + ".json"), service.metadata)
                            else:
                                service = StressProcess(config["backend_python"], run / "source/scripts", {
                                    "backend": args.backend, "model": model, "path": str(root / model),
                                    "eos": eos, "close_id": tokenizer.convert_tokens_to_ids("</think>")})
                                stack.callback(service.close)
                        reserve(run, lease, 121)
                        started = time.time()
                        print(f"Starting {s['id']}", flush=True)
                        partial = records_dir / (s["id"] + ".partial")
                        if phase == "natural":
                            response = service.request(slot=s["id"], turn=1, messages=fixture["messages"],
                                tools=TOOLS, thinking=s["arm"] == "on", seed=s["seed"], spent_tokens=0,
                                episode_deadline=time.monotonic()+180)
                            try:
                                check_natural(response, fixture, eos)
                            except (RuntimeError, ValueError):
                                fixed(records_dir / (s["id"] + f".invalid-{time.time_ns()}.json"),
                                      {**s, "fixtures_sha256": fixtures_sha, "started_unix": started,
                                       "finished_unix": time.time(), "response": response})
                                raise
                        else:
                            response = stress_request(service, tokenizer, codec, fixture, TOOLS,
                                                      s, eos, partial)
                        record = {**s, "fixtures_sha256": fixtures_sha, "started_unix": started,
                                  "finished_unix": time.time(), "response": response}
                        fixed(path, record)
                        partial.unlink(missing_ok=True)
                        observed.append(record)
                        print(f"{s['id']} {response['status']} {response.get('request_seconds')} s "
                              f"{len(response.get('generated_ids') or [])} IDs", flush=True)
        return summarize(directory, fixtures, args.backend, campaign_sha, fixtures_sha, observed,
                         time.time() - read(start_path)["unix"])


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("run", "model-root", "lease", "g3", "linux-cpu", "session-cpu"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--backend", choices=("transformers", "vllm"), required=True)
    p.add_argument("--backend-python", type=Path)
    args = p.parse_args()
    try:
        passed = run_measurement(args)["passed"]
    except Exception as exc:
        log = args.run.resolve() / "t37" / args.backend
        log.mkdir(exist_ok=True, parents=True)
        atomic_write(log / "interruption.json", json_bytes({"error": f"{type(exc).__name__}: {exc}",
                                                            "unix": time.time()}))
        raise
    sys.exit(0 if passed else 2)
