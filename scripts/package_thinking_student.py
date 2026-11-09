"""Build a self-contained no-card handoff from the working tree and a sealed dev study."""
import argparse
import gzip
import hashlib
import io
import json
import os
import tarfile
import tempfile
from pathlib import Path

from thinking_events.archive import BATCHES, verify_sources

ROOT = Path(__file__).resolve().parents[1]
RESULT = Path("docs/results/thinking-student-cpu-20261108")
G3 = Path("docs/results/thinking-events-g3-20261108")
PREFIX = "flockcue-g1g2"


def transfer_files():
    """Select verified artifacts and runtime sources explicitly, including untracked dependencies."""
    lock = json.loads((ROOT / "agent_harness/baseline.lock.json").read_bytes())
    campaign = Path(lock["reference_campaign"])
    original = json.loads((ROOT / campaign / "runs/action/manifest.json").read_bytes())
    cases = Path(lock["cases"])
    names = {
        Path(p) for p in (
            "LICENSE", "agent_harness/__main__.py", "agent_harness/__init__.py", "agent_harness/baseline.lock.json",
            "agent4drone/requirements-dev.txt", "docs/AGENT_EXECUTION_CONTRACT_CN.md", "docs/GENERAL_AGENT_SCOPE_CN.md",
            "docs/audit/teacher-reasoning-usage-20261108/summary.json", "docs/audit/teacher-reasoning-usage-20261108/probe.py",
            "scripts/convert_agent_samples.py", "scripts/student_input.py", "scripts/student_input_v2.py",
            "scripts/prepare_task_dataset.py", "scripts/verify_student_weights.py", "scripts/accept_student_cpu.sh",
            "scripts/setup_student_cpu.sh", lock["profile"], *lock["dependency_files"], *original["sources"])}
    names.update({cases, campaign / "campaign-seal.json", campaign / "runs/action/manifest.json",
                  campaign / "runs/action/summary.json", RESULT / "LINUX_CPU_CN.md"})
    for case in json.loads((ROOT / cases).read_bytes()):
        names.add(cases.parent / case["trace_file"])
    # The historical bundle also exercises preparation of a fresh v2 plan in its CPU tests.
    names.add(Path("experiments/task-splits-v1/manifest.json"))
    for filename in ("__init__.py", "adapter.py", "mac_model.py", "takeoff_example.py"):
        names.add(Path("scripts/flockcue_qwen35") / filename)
    for pattern in ("scripts/thinking_events/*.py", "scripts/tests/test_thinking_*.py", "training/requirements-*",
                    "training/model-manifests/*.json", "training/templates/*", "training/tokenizers/**/*",
                    str(campaign / "studies/action/**/*"), str(G3 / "run-01/*")):
        names.update(p.relative_to(ROOT) for p in ROOT.glob(pattern) if p.is_file())
    for batch in BATCHES:
        run = Path("docs/results") / batch / "run-01"
        names.update([run / "campaign-seal.json", run / "manifest.json"])
    sources, _ = verify_sources(ROOT)
    for source in sources:
        trial = Path(source["archive_path"]).parents[2]
        run = trial.parents[1]
        names.update([Path(source["archive_path"]), trial / "worker-result.json",
                      run / "worker-exit-seals" / (source["episode"] + ".json")])
    return {("LINUX_CPU_CN.md" if name == RESULT / "LINUX_CPU_CN.md" else str(name)): ROOT / name
            for name in names}


def add_member(tar, name, data):
    header = tarfile.TarInfo(f"{PREFIX}/{name}")
    header.size = len(data)
    header.mode = 0o644
    tar.addfile(header, io.BytesIO(data))


def package(study, output):
    from thinking_events.campaign import LEGACY_PROTOCOL, prepare_dev
    output = output.resolve()
    sidecar = output.with_name(output.name + ".sha256")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or sidecar.exists():
        raise FileExistsError(f"Choose a new package path: {output}")
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        scratch = Path(tmp)
        prepared = scratch / "prepared-dev"
        prepare_dev(study, prepared, protocol=LEGACY_PROTOCOL)
        files = transfer_files()
        # Absolute host paths are reconstructed from immutable hashes on the receiver.
        for path in prepared.rglob("*"):
            name = path.relative_to(prepared)
            if path.is_file() and str(name) not in {"manifest.json", "manifest.sha256", "host-binding.json"}:
                files[str(Path("prepared-dev") / name)] = path
        contents = {}
        packed = scratch / "deliver.tar.gz"
        with packed.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w|") as tar:
                for name, path in sorted(files.items()):
                    if path.is_symlink() or not path.is_file():
                        raise ValueError(f"Unexpected transfer source: {path}")
                    data = path.read_bytes()
                    add_member(tar, name, data)
                    contents[name] = hashlib.sha256(data).hexdigest()
                sums = "".join(f"{digest}  {name}\n" for name, digest in contents.items())
                add_member(tar, "SHA256SUMS", sums.encode())
        checksum = hashlib.sha256(packed.read_bytes()).hexdigest()
        (scratch / "checksum").write_text(f"{checksum}  {output.name}\n", encoding="ascii")
        os.link(packed, output)
        try:
            os.link(scratch / "checksum", sidecar)
        except OSError:
            output.unlink()
            raise
    return {"path": str(output), "files": len(contents), "bytes": output.stat().st_size,
            "sha256": checksum}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.study, args.output), indent=2))
