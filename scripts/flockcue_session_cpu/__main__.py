"""Run one original 20-task session and score its immutable terminal snapshots."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
import zipfile


ROOT = Path(__file__).resolve().parents[2]
POOL = ROOT / "experiments/flockcue-benchmark-600-v1"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def inventory(root):
    return {path.relative_to(root).as_posix(): sha(path) for path in sorted(root.rglob("*"))
            if path.is_file()}


def verify_files(root, digests):
    for relative, expected in digests.items():
        path = root / relative
        if not path.resolve().is_relative_to(root) or path.is_symlink() or sha(path) != expected:
            raise ValueError(f"File differs from recorded input: {relative}")


def child(role, run):
    from harness_episode_local import block_network
    attempts = block_network()
    inputs = read(run / "run-input.json")
    verify_files(ROOT, inputs["source_sha256"])
    if sha(run / "source-scene.json") != inputs["scene_sha256"]:
        raise ValueError("Scene differs from selected original bytes")
    try:
        if role == "worker":
            from .runtime import run_session
            run_session(read(run / "source-scene.json"), run, fault=inputs["fault"])
        else:
            worker = read(run / "worker-exit.json")
            if worker["role"] != "worker" or worker["returncode"] != 0:
                raise ValueError("A successfully exited session worker is required")
            verify_files(run, worker["files"])
            from .score import score_session
            score_session(run)
    finally:
        write(run / f"{role}-boundary.json", {
            "role": role, "pid": os.getpid(), "network_attempts": attempts,
            "agent_imported": "uav_agent" in sys.modules,
        })
    if attempts or (role == "scorer" and "uav_agent" in sys.modules):
        raise RuntimeError(f"{role} crossed an isolation boundary")


def launch(role, run, env, previous):
    begun = time.monotonic_ns()
    log_path = run / f"{role}.log"
    with log_path.open("xb") as log:
        proc = subprocess.Popen([sys.executable, "-m", "flockcue_session_cpu", "_child", role, str(run)],
                                cwd=run, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        watchdog = False
        try:
            proc.wait(timeout=20 * 560 if role == "worker" else 300)
        except subprocess.TimeoutExpired:
            watchdog = True
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
    verify_files(run, previous)
    files = {name: value for name, value in inventory(run).items() if name not in previous}
    if role == "worker":
        files.update(previous)
    seal = {"role": role, "pid": proc.pid, "returncode": proc.returncode, "started_ns": begun,
            "finished_ns": time.monotonic_ns(), "watchdog_expired": watchdog, "files": files}
    write(run / f"{role}-exit.json", seal)
    if proc.returncode:
        inputs = read(run / "run-input.json")
        # A crashed process cannot attest which in-flight tasks committed.
        write(run / "incomplete.json", {
            "session_id": inputs["session_id"], "failed_role": role,
            "planned_task_ids": inputs["expected_task_ids"], "status": "incomplete_unverified",
            "exit_record": f"{role}-exit.json", "watchdog_expired": watchdog,
        })
        raise RuntimeError(f"{role} exited with {proc.returncode}; see {log_path}")
    return seal


def run_session_cli(output, session_id, fault):
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Choose a new output directory: {output}")
    env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL") if k in os.environ}
    env.update(PYTHONPATH=str(ROOT / "scripts"), PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
               PYTHONHASHSEED="0", HF_HUB_OFFLINE="1", CUDA_VISIBLE_DEVICES="", PYGAME_HIDE_SUPPORT_PROMPT="1")
    subprocess.run([sys.executable, str(POOL / "verify.py")], cwd=ROOT, env=env, check=True, stdout=subprocess.PIPE)
    baseline = json.loads(subprocess.check_output([sys.executable, "-m", "agent_harness", "check"],
                                                  cwd=ROOT, env=env))
    entries = [s for group in read(POOL / "candidate-600.json")["strata"] for s in group["sessions_selected"]]
    selection = next((s for s in entries if s["session_id"] == session_id), None)
    if selection is None:
        raise ValueError(f"Unknown frozen session: {session_id}")
    with zipfile.ZipFile(POOL / "sessions-600.zip") as archive:
        raw = archive.read(selection["filename"])
    if hashlib.sha256(raw).hexdigest() != selection["source_sha256"]:
        raise ValueError("Selected session is not byte-identical to the frozen original")
    lock_path = ROOT / "agent_harness/baseline.lock.json"
    lock = read(lock_path)
    reference = read(ROOT / lock["reference_campaign"] / "runs/action/manifest.json")
    sources = {**reference["sources"], **lock["dependency_files"]}
    extra_sources = [lock_path, ROOT / lock["profile"], POOL / "verify.py", ROOT / "scripts/reproduction.py",
                     ROOT / "controller/requirements.txt",
                     *(ROOT / "scripts/flockcue_session_cpu" / name for name in
                       ("__init__.py", "__main__.py", "runtime.py", "score.py")),
                     *(ROOT / "controller" / name for name in ("app_version.py", "app_settings.py", "utils.py",
                       "api_server.py", "check_ui/__init__.py", "check_ui/agent_checker.py",
                       "check_ui/agent_client.py", "check_ui/result_compare_window.py"))]
    for path in extra_sources:
        sources[path.relative_to(ROOT).as_posix()] = sha(path)
    verify_files(ROOT, sources)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "source-scene.json").write_bytes(raw)
    write(output / "run-input.json", {"scope": "stateful_session_cpu_regression", "session_id": session_id,
          "scene_sha256": selection["source_sha256"], "dataset_manifest_sha256": sha(POOL / "manifest.json"),
          "expected_task_ids": selection["task_ids_in_original_order"], "fault": fault, "baseline": baseline,
          "source_sha256": sources, "system": platform.system(), "machine": platform.machine(),
          "python_version": platform.python_version()})
    worker = launch("worker", output, env, inventory(output))
    scorer = launch("scorer", output, env, {**worker["files"], "worker-exit.json": sha(output / "worker-exit.json")})
    verify_files(ROOT, sources)
    return {"run": str(output), "session_id": session_id, "fault": fault,
            "worker_pid": worker["pid"], "scorer_pid": scorer["pid"]}


def acceptance(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    scenarios = {"normal": "none", "restart": "none", "deadline": "deadline", "interrupted": "abort-after-1"}
    # Each run owns a separate simulator, policy, child process group and output directory.
    with ThreadPoolExecutor(max_workers=len(scenarios)) as executor:
        futures = [executor.submit(run_session_cli, output / folder, "04cd6269", fault)
                   for folder, fault in scenarios.items()]
        for future in futures:
            future.result()
    args = [sys.executable, "-m", "flockcue_session_cpu.verify", "--run", str(output / "normal"),
            "--restart", str(output / "restart"), "--timeout", str(output / "deadline"),
            "--interrupted", str(output / "interrupted")]
    verifier = Path(__file__).with_name("verify.py")
    verifier_sha = sha(verifier)
    raw = subprocess.check_output(args, cwd=ROOT / "scripts")
    if sha(verifier) != verifier_sha:
        raise ValueError("Verification source changed during audit")
    result = json.loads(raw)
    write(output / "verification.json", result)
    write(output / "verification-seal.json", {
        "verifier_sha256": verifier_sha, "verification_sha256": sha(output / "verification.json"),
        "runs": {name: {file: sha(output / name / file) for file in
                        ("run-input.json", "worker-exit.json", "scorer-exit.json")} for name in scenarios},
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--session", default="04cd6269", help="one of the 30 frozen session IDs")
    run.add_argument("--fault", default="none", choices=["none", "abort-after-1", "deadline"])
    accept = sub.add_parser("accept", help="run and verify two fresh sessions and both fault scenarios")
    accept.add_argument("--output", type=Path, required=True)
    part = sub.add_parser("_child", help=argparse.SUPPRESS)
    part.add_argument("role", choices=["worker", "scorer"])
    part.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "_child":
        child(args.role, args.directory.resolve())
    elif args.command == "accept":
        print(json.dumps(acceptance(args.output), ensure_ascii=False))
    else:
        print(json.dumps(run_session_cli(args.output, args.session, args.fault), ensure_ascii=False))


if __name__ == "__main__":
    main()
