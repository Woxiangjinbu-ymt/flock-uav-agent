"""Build a reproducible session-runner overlay for the published CPU handoff."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import tarfile
import tempfile

from package_thinking_student import PREFIX, add_member

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs/flockcue-g1g2-cpu-20261108.tar.gz"
BASE_SHA = "2a64964cefff1fe5ef5e156b58dffe49c2ed266828ea612703bae17a2af58706"
DATASET = "experiments/flockcue-benchmark-600-v1/"
# File allowlist bounds the overlay even if someone supplies a different local run.
ALLOWED = {
    "benchmark/benchmark.zip", "controller/requirements.txt", "scripts/reproduction.py",
    *(f"controller/{name}.py" for name in ("app_version", "app_settings", "utils", "api_server",
      "check_ui/__init__", "check_ui/agent_checker", "check_ui/agent_client", "check_ui/result_compare_window")),
    *(f"scripts/flockcue_session_cpu/{name}.py" for name in ("__init__", "__main__", "runtime", "score", "verify")),
    *(DATASET + name for name in ("candidate-600.json", "historical-reference-600.json", "manifest.json",
      "manifest.sha256", "proposed-folds.json", "sessions-600.zip", "verify.py", "README_CN.md")),
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pack(acceptance, base, output):
    if sha(base) != BASE_SHA:
        raise ValueError("Requires the original 20261108 G1/G2 CPU handoff")
    with tarfile.open(base, "r:gz") as archive:
        lines = archive.extractfile(f"{PREFIX}/SHA256SUMS").read().decode().splitlines()
    existing = {name: digest for digest, name in (line.split("  ", 1) for line in lines)}

    seal = json.loads((acceptance / "verification-seal.json").read_bytes())
    run = acceptance / "normal/run-input.json"
    if sha(run) != seal["runs"]["normal"]["run-input.json"]:
        raise ValueError("Accepted source inventory changed")
    required = json.loads(run.read_bytes())["source_sha256"]
    required["scripts/flockcue_session_cpu/verify.py"] = seal["verifier_sha256"]
    for name, digest in required.items():
        if name in existing:
            if digest != existing[name]:
                raise ValueError(f"Overlay would change a sealed base file: {name}")
        elif name not in ALLOWED or sha(ROOT / name) != digest:
            raise ValueError(f"Missing or changed accepted source: {name}")

    output = output.resolve()
    sidecar = output.with_name(output.name + ".sha256")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or sidecar.exists():
        raise FileExistsError(f"Choose an unused output path: {output}")
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        packed = Path(tmp) / "overlay.tar.gz"
        digests = {}
        with packed.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w|") as archive:
                for name in sorted(ALLOWED - existing.keys()):
                    path = ROOT / name
                    if path.is_symlink() or not path.is_file():
                        raise ValueError(f"Unexpected overlay source: {name}")
                    data = path.read_bytes()
                    digests[name] = hashlib.sha256(data).hexdigest()
                    if name in required and digests[name] != required[name]:
                        raise ValueError(f"Source changed during packaging: {name}")
                    add_member(archive, name, data)
                sums = "".join(f"{digest}  {name}\n" for name, digest in digests.items())
                add_member(archive, "SHA256SUMS.session-cpu", sums.encode())
        checksum = sha(packed)
        checksum_path = Path(tmp) / "checksum"
        checksum_path.write_text(f"{checksum}  {output.name}\n", encoding="ascii")
        os.link(packed, output)
        try:
            os.link(checksum_path, sidecar)
        except OSError:
            output.unlink()
            raise
    return {"base_sha256": BASE_SHA, "files": len(digests), "bytes": output.stat().st_size, "sha256": checksum}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", type=Path, default=ROOT / "outputs/flockcue-mac-session-20261108")
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(pack(args.acceptance, args.base, args.output), indent=2))
