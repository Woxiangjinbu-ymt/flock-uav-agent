"""Verify the two approved model snapshots against the shipped revision-specific digests."""
import argparse
import json
from pathlib import Path

from thinking_events.student_service import verify_model

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["1.7B", "4B"], required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--download", action="store_true", help="fetch missing snapshot files on the target host")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "training/model-manifests" / f"{args.model}.json").read_bytes())
    directory = args.directory.resolve()
    if args.download:
        from huggingface_hub import snapshot_download
        snapshot_download(manifest["repository"], revision=manifest["revision"],
                          allow_patterns=list(manifest["files"]), local_dir=directory)
    digest = verify_model(directory, args.model, manifest["revision"], manifest)
    print(json.dumps({"model": args.model, "revision": manifest["revision"],
                      "verified_files": len(manifest["files"]), "manifest_sha256": digest}))


if __name__ == "__main__":
    main()
