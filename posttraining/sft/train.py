"""Train the native student LoRA on a released FlockCue SFT split."""

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
    if release["schema"] != "flockcue-sft-causal-lm-v1" or release["split"] not in {"baseline_train", "expanded_train"}:
        raise ValueError("Expected a FlockCue training split")
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


def sample_order(size, epochs, seed):
    order = []
    for epoch in range(epochs):
        rows = list(range(size))
        random.Random(seed + epoch).shuffle(rows)
        order.extend(rows)
    return order


def update_windows(size, epochs, accumulation):
    """A short final group closes each epoch. Every decision in a group has equal weight."""
    update = 0
    for epoch in range(1, epochs + 1):
        offset = (epoch - 1) * size
        for start in range(0, size, accumulation):
            update += 1
            yield epoch, update, offset + start, offset + min(start + accumulation, size)


def learning_rate(update, total, peak, floor, warmup, schedule):
    if update <= warmup:
        return peak * update / warmup
    if schedule == "constant" or total == 1:
        return peak
    # With no warmup the first update starts at the peak. The final update uses the floor.
    peak_update = max(1, warmup)
    fraction = (update - peak_update) / (total - peak_update)
    return floor + (peak - floor) * (1 + math.cos(math.pi * fraction)) / 2


def training_parameters(model, parent, expected_sha, identity):
    if parent is None:
        benchmark.attach_lora(model, identity["rank"], identity["alpha"])
    else:
        from posttraining.sft.checkpoint import load_adapter
        loaded = load_adapter(model, parent, identity["base_revision"])
        if (loaded["weights_sha256"], loaded["rank"], loaded["alpha"]) != (
            expected_sha, identity["rank"], identity["alpha"]
        ):
            raise ValueError("Initial adapter weights or LoRA configuration differ from the run")
        for name, parameter in model.named_parameters():
            parameter.requires_grad_(name.endswith((".lora_A", ".lora_B")))
    parameters = {name: p for name, p in model.named_parameters() if p.requires_grad}
    if not parameters or not all(name.endswith((".lora_A", ".lora_B")) for name in parameters):
        raise ValueError("Expected only the native LoRA parameters to be trainable")
    return parameters


def summarize(records):
    tokens = sum(r["tokens"] for r in records)
    targets = sum(r["supervised_tokens"] for r in records)
    seconds = sum(r["sec"] for r in records)
    return {
        "steps": len(records), "tokens": tokens, "supervised_tokens": targets,
        "training_step_seconds": seconds, "tokens_per_training_second": tokens / seconds,
        "mean_loss_per_supervised_token": sum(r["loss"] * r["supervised_tokens"] for r in records) / targets,
        "peak_allocated_gib": max(r["peak_allocated_gib"] for r in records),
        "peak_reserved_gib": max(r["peak_reserved_gib"] for r in records),
    }


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
        size = len(samples)
        order = sample_order(size, args.epochs, args.seed)
        updates = math.ceil(size / args.gradient_accumulation) * args.epochs
        warmup = min(math.ceil(updates * args.warmup_ratio), updates - 1)
        legacy = (args.epochs == 1 and args.gradient_accumulation == 1 and args.init_adapter is None
                  and args.learning_rate == benchmark.LEARNING_RATE and args.lr_schedule == "constant"
                  and not args.warmup_ratio and args.max_grad_norm is None)
        identity = {
            "schema": "flockcue-first-pass-lora-v1", "model_manifest_sha256": model_hash,
            "base_revision": model_meta["revision"], "release_manifest_sha256": release_hash,
            "dataset_sha256": release["files"][args.dataset.name]["sha256"],
            "seed": args.seed, "order_one_based": [i+1 for i in order], "epochs": args.epochs,
            "rank": 16, "alpha": 32, "dropout": 0, "base_dtype": "bfloat16",
            "adapter_dtype": "float32", "batch_size": 1, "gradient_accumulation": args.gradient_accumulation,
            "optimizer": "fused AdamW", "lr": args.learning_rate, "betas": [0.9, 0.999],
            "eps": 1e-8, "weight_decay": 0.01, "lr_schedule": args.lr_schedule,
            "gradient_checkpointing": "nonreentrant",
        }
        if not legacy:
            identity.update(schema="flockcue-sft-lora-v2", init_adapter_sha256=args.init_adapter_sha256,
                            min_lr=args.min_learning_rate, warmup_ratio=args.warmup_ratio,
                            warmup_updates=warmup, optimizer_updates=updates, max_grad_norm=args.max_grad_norm,
                            loss_weighting="mean of per-decision target-token means within update",
                            shuffle="independent random.Random(seed + zero_based_epoch)")
        if args.resume:
            original = json.loads(run_path.read_bytes())
            if original["identity"] != identity:
                raise ValueError("Resume configuration differs from the recorded run")
            if summary_path.exists():
                summary = json.loads(summary_path.read_bytes())
                status.update(status="completed", completed_steps=summary["steps"], total_steps=len(order),
                              checkpoint_step=summary["steps"], updated_at=now())
                if not legacy:
                    status.update(completed_updates=summary["optimizer_updates"], total_updates=updates)
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
                **({"init_adapter": str(args.init_adapter.resolve())} if args.init_adapter else {}),
            })
        owns_run = True
        status.update(total_steps=len(order), status="loading", checkpoint_step=0, updated_at=now())
        benchmark.write_metrics(progress_path, status)
        model = benchmark.load_text_model(args.model)
        parameters = training_parameters(model, args.init_adapter, args.init_adapter_sha256, identity)
        first_lora_b = next(p for name, p in parameters.items() if name.endswith(".lora_B"))
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.train()
        if not model.is_gradient_checkpointing:
            raise RuntimeError("Gradient checkpointing is inactive")
        optimizer = torch.optim.AdamW(parameters.values(), lr=args.learning_rate, fused=True)
        records = []
        if args.resume and state_path.exists():
            records = restore_checkpoint(torch, state_path, parameters, optimizer, identity, order)
        windows = list(update_windows(size, args.epochs, args.gradient_accumulation))
        completed = len(records)
        if completed and completed not in {end for _, _, _, end in windows}:
            raise ValueError("Checkpoint falls inside an unfinished gradient accumulation")
        # Steps later than the last atomic checkpoint are safely replayed after an interruption.
        temp_log = steps_path.with_suffix(".tmp")
        temp_log.write_text("".join(json.dumps(r)+"\n" for r in records), encoding="utf-8")
        temp_log.replace(steps_path)
        status.update(status="running", completed_steps=len(records), checkpoint_step=len(records),
                      trainable_parameters=sum(p.numel() for p in parameters.values()), updated_at=now())
        benchmark.write_metrics(progress_path, status)
        with steps_path.open("a", encoding="utf-8", buffering=1) as log:
            for epoch, update, begin, end in windows:
                if end <= completed:
                    continue
                lr = learning_rate(update, updates, args.learning_rate, args.min_learning_rate,
                                   warmup, args.lr_schedule)
                for group in optimizer.param_groups:
                    group["lr"] = lr
                if not legacy:
                    status.update(epoch=epoch, active_update=update, completed_updates=update - 1,
                                  total_updates=updates, learning_rate=lr)
                for i in range(begin, end):
                    row_index = order[i]
                    ids, start, ref = samples[row_index]
                    step = i + 1
                    status.update(active_step=step, active_row=row_index+1, active_tokens=len(ids), updated_at=now())
                    benchmark.write_metrics(progress_path, status)
                    input_ids = ids.to(device="cuda", dtype=torch.long).unsqueeze(0)
                    record = benchmark.measure_step(
                        torch, model, optimizer, input_ids, input_ids[0, start:], start,
                        accumulation_size=end-begin, first_in_update=i == begin, last_in_update=step == end,
                        max_grad_norm=args.max_grad_norm)
                    if step == 1:
                        grad_max = first_lora_b.grad.abs().max().item()
                        if not math.isfinite(grad_max) or grad_max == 0:
                            raise FloatingPointError(f"Invalid first adapter gradient: {grad_max}")
                        record["first_lora_B_grad_abs_max"] = grad_max
                    record.update(step=step, row=row_index+1, session_id=ref["session_id"], task_id=ref["task_id"],
                                  logical_call_id=ref["logical_call_id"], tokens=len(ids),
                                  supervised_tokens=ref["supervised_tokens"], finished_at=now())
                    if not legacy:
                        record.update(epoch=epoch, optimizer_update=update, update_applied=step == end,
                                      accumulation_size=end-begin, learning_rate=lr)
                    records.append(record)
                    log.write(json.dumps(record) + "\n")
                    status.update(completed_steps=step, last_loss=record["loss"], updated_at=now())
                    print(f"{step}/{len(order)} row={row_index+1} tokens={len(ids)} "
                          f"loss={record['loss']:.5f} step_sec={record['sec']:.2f}", flush=True)
                at_epoch_end = end == size * epoch
                if not legacy:
                    status["completed_updates"] = update
                    if at_epoch_end:
                        # Export before advancing the checkpoint. A crash replays this update and export.
                        directory = root / f"epoch-{epoch}"
                        adapter = save_adapter(model, directory, identity["rank"], identity["alpha"], model_meta["revision"])
                        current = records[end-size:end]
                        benchmark.write_metrics(directory / "summary.json", {
                            "epoch": epoch, "status": "completed", "finished_at": now(),
                            "adapter": adapter, **summarize(current),
                            "optimizer_updates": sum(r["update_applied"] for r in current),
                            "first_learning_rate": current[0]["learning_rate"], "last_learning_rate": lr,
                        })
                if update == 1 or update % args.checkpoint_every == 0 or at_epoch_end:
                    save_checkpoint(torch, state_path, parameters, optimizer, records, identity)
                    status["checkpoint_step"] = end
                benchmark.write_metrics(progress_path, status)
        adapter = save_adapter(model, root, identity["rank"], identity["alpha"], model_meta["revision"])
        summary = {
            "status": "completed", "finished_at": now(), "epochs": args.epochs, "adapter": adapter,
            **summarize(records),
        }
        if not legacy:
            summary.update(optimizer_updates=updates,
                           epoch_summaries=[f"epoch-{n}/summary.json" for n in range(1, args.epochs + 1)])
            status.update(completed_updates=updates, total_updates=updates)
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
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=benchmark.LEARNING_RATE)
    parser.add_argument("--lr-schedule", choices=("constant", "cosine"), default="constant")
    parser.add_argument("--min-learning-rate", type=float, default=0.0, help="Final cosine learning rate")
    parser.add_argument("--warmup-ratio", type=float, default=0.0, help="Fraction of updates used for linear warmup")
    parser.add_argument("--gradient-accumulation", type=int, default=1, help="Decisions per optimizer update")
    parser.add_argument("--max-grad-norm", type=float, help="Clip the accumulated global LoRA gradient norm")
    parser.add_argument("--init-adapter", type=Path, help="Warm-start a fresh optimizer with these adapter weights")
    parser.add_argument("--init-adapter-sha256", help="Required SHA-256 of the warm-start adapter weights")
    parser.add_argument("--checkpoint-every", type=int, default=10, help="Save optimizer state every N updates")
    parser.add_argument("--resume", action="store_true", help="Continue from the last atomic checkpoint")
    args = parser.parse_args()
    if min(args.checkpoint_every, args.epochs, args.gradient_accumulation) < 1:
        parser.error("checkpoint interval, epochs and gradient accumulation must be positive")
    if not math.isfinite(args.learning_rate) or not 0 <= args.min_learning_rate <= args.learning_rate or args.learning_rate <= 0:
        parser.error("learning rate must be positive and finite, with 0 <= min-learning-rate <= learning-rate")
    if args.lr_schedule == "constant" and args.min_learning_rate:
        parser.error("--min-learning-rate applies to --lr-schedule cosine")
    if not 0 <= args.warmup_ratio < 1:
        parser.error("--warmup-ratio must be in [0, 1)")
    if args.max_grad_norm is not None and not (0 < args.max_grad_norm < math.inf):
        parser.error("--max-grad-norm must be finite and positive")
    if (args.init_adapter is None) != (args.init_adapter_sha256 is None):
        parser.error("--init-adapter and --init-adapter-sha256 must be supplied together")
    if args.init_adapter_sha256 is not None and (len(args.init_adapter_sha256) != 64
            or any(c not in "0123456789abcdef" for c in args.init_adapter_sha256)):
        parser.error("--init-adapter-sha256 must be a lowercase SHA-256 hex digest")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / ".train.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        train(args)


if __name__ == "__main__":
    main()
