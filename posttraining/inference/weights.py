"""Verify the original text-model snapshot before inference."""
import argparse
import hashlib
import json
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / "sft/model.json"


def verify_weights(path):
    """Bind all files that the offline tokenizer and model loaders can consume."""
    manifest = json.loads(MANIFEST.read_bytes())
    files = manifest["files"]
    required = {"config.json", "tokenizer_config.json", "tokenizer.json", "chat_template.jinja", "model.safetensors.index.json"}
    shards = {name for name in files if name.endswith(".safetensors")}
    if not required <= files.keys() or not shards:
        raise ValueError("incomplete student manifest")
    path = Path(path)
    actual = {entry.name for entry in path.iterdir() if entry.is_file() and
              entry.suffix.lower() in {".json", ".safetensors", ".model", ".txt", ".jinja", ".tiktoken", ".bin"}}
    optional = {name: sha for name, sha in manifest.get("optional_files", {}).items() if name in actual}
    if not actual <= files.keys() | optional.keys() or any(name.endswith(".bin") for name in actual):
        raise ValueError("unregistered student model files")
    for name, expected in (files | optional).items():
        if not name or Path(name).name != name or not (path / name).is_file():
            raise ValueError("invalid student model file")
        with (path / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError(f"student checksum mismatch: {name}")
    if "model.safetensors.index.json" in files:
        index = json.loads((path / "model.safetensors.index.json").read_bytes())
        if not index["weight_map"] or set(index["weight_map"].values()) != shards:
            raise ValueError("student model shard index mismatch")
    elif shards != {"model.safetensors"}:
        raise ValueError("sharded weights require the pinned index")
    return hashlib.sha256(MANIFEST.read_bytes()).hexdigest()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path, help="local Qwen3.5-2B snapshot directory")
    cli.add_argument("--download", action="store_true", help="fetch the registered checkpoint revision first")
    args = cli.parse_args()
    manifest = json.loads(MANIFEST.read_bytes())
    if args.download:
        from huggingface_hub import snapshot_download
        snapshot_download(manifest["repository"], revision=manifest["revision"], allow_patterns=list(manifest["files"]), local_dir=args.directory)
    print(json.dumps({"model": manifest["model"], "revision": manifest["revision"], "manifest_sha256": verify_weights(args.directory)}))


if __name__ == "__main__":
    main()
