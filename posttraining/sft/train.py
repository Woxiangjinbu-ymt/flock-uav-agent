"""Train one epoch on a released FlockCue SFT split with the measured A100 LoRA setup."""

import argparse
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random

from posttraining.inference.weights import MANIFEST as MODEL_MANIFEST, verify_weights
from posttraining.sft import benchmark


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_release(torch, path, model_revision):
    """Verify both released files and keep only the tokens a supervised step consumes."""
    manifest_path = path.with_name("manifest.json")
    release = json.loads(manifest_path.read_bytes())
    if (release["schema"], release["split"]) != ("flockcue-sft-causal-lm-v1", "baseline_train"):
        raise ValueError("Expected the FlockCue baseline_train SFT release")
    if release["tokenizer"]["revision"] != model_revision:
        raise ValueError("Training tokenizer and model revisions differ")
    index_path = path.with_name("index.jsonl")
    for source in (path, index_path):
        if digest(source) != release["files"][source.name]["sha256"]:
            raise ValueError(f"Release checksum mismatch for {source}")
    samples = []
    with gzip.open(path, "rb") as rows, index_path.open("rb") as index:
        for number, (line, index_line) in enumerate(zip(rows, index, strict=True), 1):
            ref = json.loads(index_line)
            if ref["row"] != number or hashlib.sha256(line).hexdigest() != ref["train_row_sha256"]:
                raise ValueError(f"Index does not match training row {number}")
            row = json.loads(line)
            ids, labels = row["input_ids"], row["labels"]
            start, targets = ref["masked_prompt_tokens"], ref["supervised_tokens"]
            if not (0 < start < len(ids) and start + targets == len(ids) and
                    labels == [-100] * start + ids[start:]):
                raise ValueError(f"Invalid causal supervision at row {number}")
            samples.append((torch.tensor(ids, dtype=torch.int32), start, ref))
    counts = release["counts"]
    if (len(samples), sum(s for _, s, _ in samples), sum(r["supervised_tokens"] for _, _, r in samples)) != (
        counts["decisions"], counts["masked_prompt_tokens"], counts["supervised_tokens"]
    ) or not samples:
        raise ValueError("Training sample counts differ from the release")
    return samples, release, digest(manifest_path)


def save_checkpoint(torch, path, parameters, optimizer, records, identity):
    # The previous checkpoint remains usable if the process exits during serialization.
    weights = {name: p.detach().cpu() for name, p in parameters.items()}
    if not all(torch.isfinite(tensor).all().item() for tensor in weights.values()):
        raise FloatingPointError("Adapter contains nonfinite values")
    checkpoint = {
        "identity": identity, "adapter": weights, "optimizer": optimizer.state_dict(),
        "cpu_rng": torch.get_rng_state(), "cuda_rng": torch.cuda.get_rng_state(),
        "records": records,
    }
    temp = path.with_suffix(".tmp")
    with temp.open("wb") as output:
        torch.save(checkpoint, output)
        output.flush()
        os.fsync(output.fileno())
    temp.replace(path)


def restore_checkpoint(torch, path, parameters, optimizer, identity, order):
    checkpoint = torch.load(path, weights_only=True, map_location="cpu")
    records = checkpoint["records"]
    if checkpoint["identity"] != identity or len(records) > len(order):
        raise ValueError("Checkpoint identity or completed-step count differs from run")
    if any(r["step"] != i or r["row"] != order[i-1]+1 for i, r in enumerate(records, 1)):
        raise ValueError("Checkpoint sample order differs from run")
    weights = checkpoint["adapter"]
    if weights.keys() != parameters.keys():
        raise ValueError("Checkpoint adapter parameters differ from model")
    with torch.no_grad():
        for name, p in parameters.items():
            value = weights[name]
            if p.shape != value.shape or p.dtype != value.dtype:
                raise ValueError(f"Checkpoint adapter shape or dtype mismatch: {name}")
            p.copy_(value)
    optimizer.load_state_dict(checkpoint["optimizer"])
    torch.set_rng_state(checkpoint["cpu_rng"])
    torch.cuda.set_rng_state(checkpoint["cuda_rng"])
    return records


def train(args):
    import torch
    from posttraining.sft.checkpoint import save_adapter

    root = args.output
    run_path = root / "run.json"
    progress_path = root / "progress.json"
    state_path = root / "checkpoint.pt"
    summary_path = root / "summary.json"
    steps_path = root / "steps.jsonl"
    if not args.resume and any((root / name).exists() for name in (
        "run.json", "progress.json", "checkpoint.pt", "steps.jsonl",
        "summary.json", "adapter.safetensors", "adapter_config.json",
    )):
        raise FileExistsError(f"Run already exists at {root}, use --resume")
    if args.resume and not run_path.is_file():
        raise FileNotFoundError(f"No run configuration at {run_path}")
    status = {"status": "initializing", "updated_at": now(), "completed_steps": 0}
    owns_run = False
    try:
        model_meta = json.loads(MODEL_MANIFEST.read_bytes())
        model_hash = verify_weights(args.model)
        samples, release, release_hash = read_release(torch, args.dataset, model_meta["revision"])
        order = list(range(len(samples)))
        random.Random(args.seed).shuffle(order)
        identity = {
            "schema": "flockcue-first-pass-lora-v1", "model_manifest_sha256": model_hash,
            "base_revision": model_meta["revision"], "release_manifest_sha256": release_hash,
            "dataset_sha256": release["files"][args.dataset.name]["sha256"],
            "seed": args.seed, "order_one_based": [i+1 for i in order], "epochs": 1,
            "rank": 16, "alpha": 32, "dropout": 0, "base_dtype": "bfloat16",
            "adapter_dtype": "float32", "batch_size": 1, "gradient_accumulation": 1,
            "optimizer": "fused AdamW", "lr": benchmark.LEARNING_RATE, "betas": [0.9, 0.999],
            "eps": 1e-8, "weight_decay": 0.01, "lr_schedule": "constant",
            "gradient_checkpointing": "nonreentrant",
        }
        if args.resume:
            original = json.loads(run_path.read_bytes())
            if original["identity"] != identity:
                raise ValueError("Resume configuration differs from the recorded run")
            if summary_path.exists():
                summary = json.loads(summary_path.read_bytes())
                status.update(status="completed", completed_steps=summary["steps"], total_steps=len(order),
                              checkpoint_step=summary["steps"], updated_at=now())
                benchmark.write_metrics(progress_path, status)
                print(json.dumps(summary), flush=True)
                return
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
            raise RuntimeError("This training run requires BF16 CUDA")
        torch.manual_seed(args.seed)
        torch.cuda.manual_seed_all(args.seed)
        if not args.resume:
            benchmark.write_metrics(run_path, {
                "started_at": now(), "model": str(args.model.resolve()),
                "dataset": str(args.dataset.resolve()), "identity": identity,
                "hardware": benchmark.hardware_info(torch), "checkpoint_every": args.checkpoint_every,
            })
        owns_run = True
        status.update(total_steps=len(order), status="loading", checkpoint_step=0, updated_at=now())
        benchmark.write_metrics(progress_path, status)
        model = benchmark.load_text_model(args.model)
        _, first_lora_b = benchmark.attach_lora(model, identity["rank"], identity["alpha"])
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.train()
        if not model.is_gradient_checkpointing:
            raise RuntimeError("Gradient checkpointing is inactive")
        parameters = {name: p for name, p in model.named_parameters() if p.requires_grad}
        optimizer = torch.optim.AdamW(parameters.values(), lr=benchmark.LEARNING_RATE, fused=True)
        records = []
        if args.resume and state_path.exists():
            records = restore_checkpoint(torch, state_path, parameters, optimizer, identity, order)
        # Steps later than the last atomic checkpoint are safely replayed after an interruption.
        temp_log = steps_path.with_suffix(".tmp")
        temp_log.write_text("".join(json.dumps(r)+"\n" for r in records), encoding="utf-8")
        temp_log.replace(steps_path)
        status.update(status="running", completed_steps=len(records), checkpoint_step=len(records),
                      trainable_parameters=sum(p.numel() for p in parameters.values()), updated_at=now())
        benchmark.write_metrics(progress_path, status)
        with steps_path.open("a", encoding="utf-8", buffering=1) as log:
            for i in range(len(records), len(order)):
                row_index = order[i]
                ids, start, ref = samples[row_index]
                step = i + 1
                status.update(active_step=step, active_row=row_index+1, active_tokens=len(ids), updated_at=now())
                benchmark.write_metrics(progress_path, status)
                input_ids = ids.to(device="cuda", dtype=torch.long).unsqueeze(0)
                record = benchmark.measure_step(torch, model, optimizer, input_ids, input_ids[0, start:], start)
                if step == 1:
                    grad_max = first_lora_b.grad.abs().max().item()
                    if not math.isfinite(grad_max) or grad_max == 0:
                        raise FloatingPointError(f"Invalid first adapter gradient: {grad_max}")
                    record["first_lora_B_grad_abs_max"] = grad_max
                record.update(step=step, row=row_index+1, session_id=ref["session_id"], task_id=ref["task_id"],
                              logical_call_id=ref["logical_call_id"], tokens=len(ids),
                              supervised_tokens=ref["supervised_tokens"], finished_at=now())
                records.append(record)
                if step == 1 or step % args.checkpoint_every == 0 or step == len(order):
                    save_checkpoint(torch, state_path, parameters, optimizer, records, identity)
                    status["checkpoint_step"] = step
                log.write(json.dumps(record) + "\n")
                status.update(completed_steps=step, last_loss=record["loss"], updated_at=now())
                benchmark.write_metrics(progress_path, status)
                print(f"{step}/{len(order)} row={row_index+1} tokens={len(ids)} "
                      f"loss={record['loss']:.5f} step_sec={record['sec']:.2f}", flush=True)
        adapter = save_adapter(model, root, identity["rank"], identity["alpha"], model_meta["revision"])
        tokens = sum(r["tokens"] for r in records)
        supervised = sum(r["supervised_tokens"] for r in records)
        seconds = sum(r["sec"] for r in records)
        summary = {
            "status": "completed", "finished_at": now(), "steps": len(records), "epochs": 1,
            "tokens": tokens, "supervised_tokens": supervised, "training_step_seconds": seconds,
            "tokens_per_training_second": tokens / seconds,
            "mean_loss_per_supervised_token": sum(r["loss"]*r["supervised_tokens"] for r in records) / supervised,
            "peak_allocated_gib": max(r["peak_allocated_gib"] for r in records),
            "peak_reserved_gib": max(r["peak_reserved_gib"] for r in records),
            "adapter": adapter,
        }
        benchmark.write_metrics(summary_path, summary)
        status.update(status="completed", updated_at=now())
        benchmark.write_metrics(progress_path, status)
        print(json.dumps(summary), flush=True)
    except BaseException as error:
        if owns_run:
            status.update(status="failed", error=f"{type(error).__name__}: {error}", updated_at=now())
            benchmark.write_metrics(progress_path, status)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True, help="released train.jsonl.gz with manifest and index")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=123)
    parser.add_argument("--checkpoint-every", type=int, default=10)
    parser.add_argument("--resume", action="store_true", help="Continue from the last atomic checkpoint")
    args = parser.parse_args()
    if args.checkpoint_every <= 0:
        parser.error("--checkpoint-every must be positive")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / ".train.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        train(args)


if __name__ == "__main__":
    main()
