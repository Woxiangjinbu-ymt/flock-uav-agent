"""Read the Linux preflight and raw T37 measurements before a T38 claim."""

import math
import os
from pathlib import Path
import platform
from statistics import mean

from prepare_task_dataset import parse_json, safe_path

from .campaign import PAIRED_MODELS
from .campaign_storage import atomic_write, digest, json_bytes, locked


def _duration(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= 120


def _linux_cpu(evidence):
    host, baseline = evidence["platform"], evidence["baseline"]
    if (host["system"] != "Linux" or host["python_version"] != "3.11.16"
            or baseline["baseline"] != "general-agent-v5-local" or baseline["source_files"] != 93
            or baseline["provider_calls"] != 0 or evidence["baseline_smoke"]["valid"] is not True):
        raise ValueError("Linux CPU baseline or smoke verification failed")
    for suite in ("agent", "student"):
        tests = evidence["pytest"][suite]
        if tests["tests"] <= 0 or any(tests[key] != 0 for key in ("failures", "errors", "skipped")):
            raise ValueError(f"Linux CPU test suite incomplete: {suite}")


def _session_cpu(evidence):
    reference = None
    for mode in ("normal", "restart"):
        run = evidence[mode]
        if (run["configuration"]["system"] != "Linux" or run["session_id"] != "04cd6269"
                or (run["planned"], run["scored"], run["checker_leaves"], run["verified_transitions"])
                != (20, 20, 79, 19) or run["worker_pid"] == run["scorer_pid"]):
            raise ValueError("Linux session replay is incomplete")
        results = [(task["task_id"], task["checker_leaf_results"]) for task in run["tasks"]]
        if len({task for task, _ in results}) != 20 or (reference is not None and reference != results):
            raise ValueError("Linux session checker results changed on restart")
        reference = results


def _measurements(samples, revision):
    if samples.get("revision") != revision or samples.get("functional_smoke") is not True:
        raise ValueError("Missing functional smoke for the selected model revision")
    means = []
    for arm in ("on", "off"):
        for length in ("4096", "8192", "13000"):
            durations = samples["natural_seconds"][arm][length]
            if not isinstance(durations, list) or len(durations) != 3:
                raise ValueError("T37 requires three natural requests per arm and length")
            # A timed-out off-arm request is reported but not used in the on-arm estimate.
            if any(not _duration(t) and not (arm == "off" and t is None) for t in durations):
                raise ValueError("Missing or invalid natural latency for T37")
            if arm == "on":
                means.append(mean(durations))
    short, medium, long = means
    if max(short + 2 * medium + 4 * long, 7 * short + 4 * medium + long) > 250:
        raise ValueError("T37 estimated model wait exceeds 250 seconds")
    full = samples["full_length"]
    if not isinstance(full, list) or len(full) != 20:
        raise ValueError("T37 requires twenty full-length attempts per model")
    complete = []
    for row in full:
        elapsed, tokens = row["seconds"], row["generated_tokens"]
        if type(tokens) is not int or not 0 <= tokens <= 2048:
            raise ValueError("Invalid T37 token count")
        if elapsed is None:  # Right-censored at the protocol's 120-second limit.
            continue
        if tokens != 2048 or not _duration(elapsed):
            raise ValueError("Invalid completed T37 full-length measurement")
        complete.append(elapsed)
    if len(complete) < 19:
        raise ValueError("T37 full-length P95 is indeterminate")


def read_admission(run, campaign_sha, backend):
    """The referenced files travel with the run, allowing an offline report replay."""
    directory = Path(run) / "t37"
    raw = safe_path(directory, "admission.json").read_bytes()
    return _check_admission(directory, raw, campaign_sha, backend)


def _check_admission(directory, raw, campaign_sha, backend):
    evidence = parse_json(raw)
    if (evidence.get("schema") != "paired-t37-admission-v1" or
            evidence.get("campaign_sha256") != campaign_sha or evidence.get("backend") != backend):
        raise ValueError("T37 admission belongs to another campaign or backend")
    host = evidence["host"]
    if (host.get("system") != "Linux" or host.get("card") != "A100 40GB" or host.get("cards") != 1
            or host.get("bf16") is not True or host.get("batch_size") != 1):
        raise ValueError("T37 requires one A100 40GB with BF16 batch size one")
    required = {"linux_cpu", "session_cpu", "backend_lock", "measurement_archive"}
    if backend == "vllm":
        required.add("transformers_fallback_reason")
    if not required.issubset(evidence["artifacts"]):
        raise ValueError("T37 admission is missing required artifacts")
    for key, item in evidence["artifacts"].items():
        data = safe_path(directory, item["path"]).read_bytes()
        if not data or digest(data) != item["sha256"]:
            raise ValueError(f"T37 artifact digest differs: {key}")
        if key == "linux_cpu":
            _linux_cpu(parse_json(data))
        elif key == "session_cpu":
            _session_cpu(parse_json(data))
        elif key == "measurement_archive":
            measurements = parse_json(data)
            if (measurements.get("schema") != "paired-t37-measurements-v1"
                    or measurements.get("backend") != backend
                    or measurements.get("campaign_sha256") != campaign_sha
                    or set(measurements["models"]) != set(PAIRED_MODELS)):
                raise ValueError("T37 measurements belong to another campaign, model set or backend")
            if "raw_files_sha256" in measurements:
                folder = safe_path(directory, item["path"]).parent
                records = measurements["raw_files_sha256"]
                if (len(records) != 80 or measurements.get("request_count") != 80
                        or digest(safe_path(folder, "fixtures.json").read_bytes()) != measurements["fixtures_sha256"]):
                    raise ValueError("Incomplete calibration archive or changed fixtures")
                for path, expected in records.items():
                    if digest(safe_path(folder, path).read_bytes()) != expected:
                        raise ValueError(f"Changed calibration record: {path}")
            for model, revision in PAIRED_MODELS.items():
                _measurements(measurements["models"][model], revision)
    return evidence, digest(raw)


def admit_t37(run, *, backend, linux_cpu, session_cpu, backend_lock, lease, student_python,
              backend_python=None, fallback_reason=None):
    """Re-evaluate a finished calibration before publishing its admission."""
    from .campaign import verify_run
    from .paired_batch import _qualified_service

    run = Path(run).resolve()
    if backend not in ("transformers", "vllm") or platform.system() != "Linux":
        raise ValueError("Admission must be created on the calibrated Linux host")
    with locked(run, ".coordinator.lock"):
        verify_run(run)
        campaign_sha = (run / "campaign.sha256").read_text().strip()
        t37 = run / "t37"
        folder = safe_path(t37, backend)
        measurements = (folder / "measurements.json").read_bytes()
        verdict = parse_json((folder / "verdict.json").read_bytes())
        if verdict.get("passed") is not True or verdict.get("measurement_sha256") != digest(measurements):
            raise ValueError("Selected backend did not pass its sealed T37 verdict")
        files = {"linux_cpu": linux_cpu, "session_cpu": session_cpu, "backend_lock": backend_lock,
                 "measurement_archive": folder / "measurements.json", "verdict": folder / "verdict.json"}
        if backend == "vllm":
            if not fallback_reason or parse_json(Path(fallback_reason).read_bytes()).get("passed") is not False:
                raise ValueError("vLLM admission requires a failed Transformers verdict")
            files["transformers_fallback_reason"] = fallback_reason
        elif fallback_reason:
            raise ValueError("Unexpected fallback reason for Transformers")
        packages = ["torch", "cuda", "transformers", "tokenizers"]
        if backend == "vllm":
            packages.append("vllm")
        interpreters = {"student": os.path.abspath(student_python),
                        "backend": os.path.abspath(backend_python or student_python)}
        versions = {}
        for model, revision in PAIRED_MODELS.items():
            path = folder / f"metadata-{model}.json"
            metadata = parse_json(path.read_bytes())
            observed = {key: metadata[key] for key in packages}
            if (metadata["model"] != model or metadata["revision"] != revision or metadata["backend"] != backend
                    or metadata["python"] != interpreters["student"] or
                    metadata.get("engine_python", metadata["python"]) != interpreters["backend"]
                    or (versions and versions != observed)):
                raise ValueError("The measured model, engine or renderer differs from the requested run")
            _qualified_service(metadata, {"versions": observed, "backend": backend})
            versions = observed
            files[f"metadata_{model}"] = path
        quote = parse_json(Path(lease).read_bytes())
        if quote.get("billing_mode") == "prepaid_week":
            reservation = parse_json((run / "cost-ledger.json").read_bytes())["reservation"] or {}
            if reservation.get("prepaid_terms") != quote and not (t37 / "admission.json").exists():
                raise ValueError("Prepaid admission must use the same budget terms as calibration")
        artifacts = {}
        for key, path in files.items():
            path = Path(path).resolve()
            data = path.read_bytes()
            sha = digest(data)
            if not path.is_relative_to(t37):
                # Content-address external inputs so a failed admission attempt cannot overwrite a prior receipt.
                path = t37 / "inputs" / f"{key}-{sha}{path.suffix}"
                path.parent.mkdir(exist_ok=True)
                if not path.exists():
                    atomic_write(path, data)
            artifacts[key] = {"path": path.relative_to(t37).as_posix(), "sha256": sha}
        evidence = {"schema": "paired-t37-admission-v1", "backend": backend, "campaign_sha256": campaign_sha,
                    "host": {"system": platform.system(), "node": platform.node(), "card": "A100 40GB",
                             "cards": 1, "bf16": True, "batch_size": 1},
                    "versions": versions, "interpreters": interpreters, "lease": quote, "artifacts": artifacts}
        raw = json_bytes(evidence)
        _, fingerprint = _check_admission(t37, raw, campaign_sha, backend)
        target = t37 / "admission.json"
        if target.exists() and target.read_bytes() != raw:
            raise ValueError("A different T37 admission was already published")
        if not target.exists():
            atomic_write(target, raw)
        return {"backend": backend, "admission_sha256": fingerprint, "path": str(target)}
