"""Time full Qwen3.5 text SFT steps on representative unpadded samples on one GPU."""

import argparse
import gzip
import hashlib
import importlib.metadata
import inspect
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import traceback


CASES = ("short", "medium", "long")
PROJECTIONS = {
    "self_attn": {"q_proj", "k_proj", "v_proj", "o_proj"},
    "linear_attn": {"in_proj_qkv", "in_proj_z", "in_proj_b", "in_proj_a", "out_proj"},
}
PACKAGES = ("transformers", "flash-attn", "flash-linear-attention", "fla-core", "causal-conv1d", "triton")
LEARNING_RATE = 2e-4
GIB = 2**30


def write_metrics(path, metrics):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def dataset_rows(path):
    open_file = gzip.open if str(path).endswith(".gz") else open
    with open_file(path, "rt", encoding="utf-8") as source:
        for line in source:
            yield json.loads(line)


def describe_dataset(path):
    rows = []
    for index, sample in enumerate(dataset_rows(path)):
        ids, labels = sample["input_ids"], sample["labels"]
        supervised = sum(token != -100 for token in labels)
        if (not labels or len(ids) != len(labels) or labels[0] != -100 or
                not supervised or labels[-supervised:] != ids[-supervised:]):
            raise ValueError(f"Expected a masked context and a supervised suffix at row {index}")
        rows.append({"row_zero_based": index, "tokens": len(ids), "loss_tokens": supervised})
    if not rows:
        raise ValueError("Empty SFT dataset")
    ordered = sorted(rows, key=lambda row: (row["tokens"], row["row_zero_based"]))
    representative = {"short": ordered[0], "medium": ordered[(len(rows) - 1) // 2], "long": ordered[-1]}
    summary = {
        "samples": len(rows),
        "tokens": sum(row["tokens"] for row in rows),
        "loss_tokens": sum(row["loss_tokens"] for row in rows),
        "dataset_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "representative": representative,
        "case_results": {},
    }
    return rows, summary


def hardware_info(torch):
    device = torch.cuda.get_device_properties(0)
    versions = {}
    for package in PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {
        "gpu": device.name,
        "memory_gib": device.total_memory / GIB,
        "compute_capability": [device.major, device.minor],
        "torch": str(torch.__version__),
        "cuda": torch.version.cuda,
        "packages": versions,
    }


def linear_attention_kernels():
    from transformers.models.qwen3_5 import modeling_qwen3_5

    kernels = {}
    for name in ("torch_chunk_gated_delta_rule", "causal_conv1d_fn"):
        function = getattr(modeling_qwen3_5, name)
        # Transformers 5.22.2 captures its selected implementation in the wrapper.
        if inspect.isfunction(function):
            function = inspect.getclosurevars(function).nonlocals.get("implementation", function)
        module = getattr(function, "__module__", "")
        kernels[name] = f"{module}.{getattr(function, '__name__', type(function).__name__)}"
    return kernels


def load_text_model(path):
    import torch
    from transformers import Qwen3_5ForCausalLM

    model, report = Qwen3_5ForCausalLM.from_pretrained(
        path, local_files_only=True, dtype=torch.bfloat16, use_safetensors=True,
        trust_remote_code=False, output_loading_info=True, attn_implementation="sdpa",
    )
    for key in ("missing_keys", "mismatched_keys", "error_msgs"):
        if report.get(key):
            raise RuntimeError(f"Checkpoint loading {key}: {str(report[key])[:1000]}")
    # The original checkpoint also contains a vision tower and MTP layers.
    unexpected = [key for key in report["unexpected_keys"] if not key.startswith(("mtp.", "model.visual."))]
    if unexpected:
        raise RuntimeError(f"Unexpected checkpoint keys: {unexpected[:10]}")
    return model.to("cuda").requires_grad_(False)


def attach_lora(model, rank, alpha):
    import torch
    from torch import nn
    from torch.nn import functional as F

    class LoRAProjection(nn.Module):
        def __init__(self, base):
            super().__init__()
            self.base = base
            options = {"device": base.weight.device, "dtype": torch.float32}
            self.lora_A = nn.Parameter(torch.empty(rank, base.in_features, **options))
            self.lora_B = nn.Parameter(torch.zeros(base.out_features, rank, **options))
            nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))

        @property
        def weight(self):
            return self.base.weight

        def forward(self, inputs):
            down = F.linear(inputs, self.lora_A.to(inputs.dtype))
            delta = F.linear(down, self.lora_B.to(inputs.dtype))
            return self.base(inputs) + delta * (alpha / rank)

    counts = {}
    first_lora_b = None
    for path, layer in list(model.named_modules()):
        parts = path.split(".")
        if len(parts) < 3 or parts[-1] not in PROJECTIONS.get(parts[-2], ()) or not isinstance(layer, nn.Linear):
            continue
        projection = LoRAProjection(layer)
        if first_lora_b is None:
            first_lora_b = projection.lora_B
        setattr(model.get_submodule(path.rsplit(".", 1)[0]), parts[-1], projection)
        name = ".".join(parts[-2:])
        counts[name] = counts.get(name, 0) + 1
    required = {f"{group}.{name}" for group, names in PROJECTIONS.items() for name in names}
    if missing := required.difference(counts):
        raise RuntimeError(f"Required LoRA projections missing: {sorted(missing)}")
    return counts, first_lora_b


def measure_step(torch, model, optimizer, input_ids, labels, target_start, *,
                 accumulation_size=1, first_in_update=True, last_in_update=True, max_grad_norm=None):
    """Backpropagate one decision's mean loss; step once the entire update is accumulated."""
    if first_in_update:
        optimizer.zero_grad(set_to_none=True)
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    with torch.autocast("cuda", dtype=torch.bfloat16):
        hidden = model.model(input_ids=input_ids, use_cache=False, return_dict=True).last_hidden_state
        # Position t-1 predicts target t. Only the supervised suffix requires logits.
        logits = model.lm_head(hidden[0, target_start - 1:-1, :])
        loss = torch.nn.functional.cross_entropy(logits.float(), labels)
    torch.cuda.synchronize()
    forward_end = time.perf_counter()
    loss_value = loss.item()
    if not math.isfinite(loss_value):
        raise FloatingPointError(f"Nonfinite loss: {loss_value}")
    (loss if accumulation_size == 1 else loss / accumulation_size).backward()
    torch.cuda.synchronize()
    backward_end = time.perf_counter()
    grad_norm = None
    if last_in_update:
        if max_grad_norm is not None:
            parameters = [p for group in optimizer.param_groups for p in group["params"]]
            grad_norm = torch.nn.utils.clip_grad_norm_(
                parameters, max_grad_norm, error_if_nonfinite=True).item()
        optimizer.step()
    torch.cuda.synchronize()
    step_end = time.perf_counter()
    elapsed = step_end - started
    allocated = torch.cuda.max_memory_allocated() / GIB
    reserved = torch.cuda.max_memory_reserved() / GIB
    return {
        **({"grad_norm_before_clip": grad_norm} if grad_norm is not None else {}),
        "loss": loss_value,
        "forward_sec": forward_end - started,
        "backward_sec": backward_end - forward_end,
        "optimizer_sec": step_end - backward_end,
        "sec": elapsed,
        "tokens_per_sec": input_ids.shape[1] / elapsed,
        "peak_allocated_gib": allocated,
        "peak_reserved_gib": reserved,
    }


def run_case(args):
    import torch

    _, summary = describe_dataset(args.dataset)
    row = summary["representative"][args.case]
    sample = next(sample for i, sample in enumerate(dataset_rows(args.dataset)) if i == row["row_zero_based"])
    path = Path(args.output) / f"{args.case}.json"
    state = {
        "case": args.case, "status": "loading", "sample": row, "model": args.model,
        "rank": args.rank, "alpha": args.alpha, "lora_dropout": 0, "batch_size": 1,
        "gradient_accumulation": 1, "padding": 0, "gradient_checkpointing": "nonreentrant",
        "amp": "bf16", "optimizer": "fused AdamW", "lr": LEARNING_RATE, "measured_steps": args.steps,
        "warmup_steps": args.warmup, "lm_head": "only supervised causal predecessors", "steps": [],
    }
    write_metrics(path, state)
    try:
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
            raise RuntimeError("BF16 CUDA device required")
        torch.manual_seed(123)
        torch.cuda.manual_seed_all(123)
        state["hardware"] = hardware_info(torch)
        write_metrics(path, state)
        model = load_text_model(args.model)
        counts, first_lora_b = attach_lora(model, args.rank, args.alpha)
        trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.train()
        if not model.is_gradient_checkpointing:
            raise RuntimeError("Activation checkpointing inactive")
        optimizer = torch.optim.AdamW(trainable, lr=LEARNING_RATE, fused=True)
        input_ids = torch.tensor([sample["input_ids"]], device="cuda", dtype=torch.long)
        target_start = row["tokens"] - row["loss_tokens"]
        labels = torch.tensor(sample["labels"][target_start:], device="cuda", dtype=torch.long)
        state.update(status="running", projections=counts, trainable_params=sum(p.numel() for p in trainable),
                     attention=model.config._attn_implementation, linear_attention_kernels=linear_attention_kernels())
        write_metrics(path, state)

        for step in range(args.warmup + args.steps):
            record = measure_step(torch, model, optimizer, input_ids, labels, target_start)
            if step == 0:
                # Validate a real update outside the timed region.
                grad_max = first_lora_b.grad.abs().max().item()
                if not math.isfinite(grad_max) or grad_max == 0:
                    raise RuntimeError(f"Invalid adapter gradient max: {grad_max}")
                state["first_lora_B_grad_abs_max"] = grad_max
            record["phase"] = "warmup" if step < args.warmup else "measured"
            state["steps"].append(record)
            write_metrics(path, state)
            print(json.dumps({"case": args.case, **record}), flush=True)

        measured = state["steps"][args.warmup:]
        state.update(status="ok", median_sec=statistics.median(r["sec"] for r in measured),
                     peak_allocated_gib=max(r["peak_allocated_gib"] for r in measured),
                     peak_reserved_gib=max(r["peak_reserved_gib"] for r in measured))
        write_metrics(path, state)
        return 0
    except Exception as exc:
        state.update(status="oom" if isinstance(exc, torch.OutOfMemoryError) else "error",
                     error=repr(exc), traceback=traceback.format_exc())
        if torch.cuda.is_initialized():
            state.update(failure_peak_allocated_gib=torch.cuda.max_memory_allocated() / GIB,
                         failure_peak_reserved_gib=torch.cuda.max_memory_reserved() / GIB)
        write_metrics(path, state)
        print(state["traceback"], flush=True)
        return 1


def estimate_epoch(rows, summary):
    lengths = summary["representative"]
    results = summary["case_results"]
    points = [(lengths[case]["tokens"], results[case]["median_sec"]) for case in CASES]

    def seconds(tokens):
        left, right = points[:2] if tokens <= points[1][0] else points[1:]
        if right[0] == left[0]:
            return left[1]
        return left[1] + (tokens - left[0]) * (right[1] - left[1]) / (right[0] - left[0])

    return sum(seconds(row["tokens"]) for row in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True, help="Isolated metrics directory outside repo")
    parser.add_argument("--case", choices=("all",) + CASES, default="all")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--describe", action="store_true", help="Print the dataset summary without loading a model")
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--alpha", type=float, default=32)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--steps", type=int, default=2)
    args = parser.parse_args()
    if min(args.rank, args.steps) < 1 or args.warmup < 0:
        parser.error("rank and steps must be positive, warmup nonnegative")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    if args.worker:
        return run_case(args)

    rows, summary = describe_dataset(args.dataset)
    if args.describe:
        print(json.dumps(summary, indent=2))
        return 0
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "summary.json"
    if destination.exists():
        raise FileExistsError(f"Choose a fresh metrics directory: {destination}")
    write_metrics(destination, summary)
    cases = CASES if args.case == "all" else (args.case,)
    for case in cases:
        completed = subprocess.run([
            sys.executable, __file__, "--worker", "--model", args.model, "--dataset", args.dataset,
            "--case", case, "--output", args.output, "--rank", str(args.rank), "--alpha", str(args.alpha),
            "--warmup", str(args.warmup), "--steps", str(args.steps),
        ])
        path = output / f"{case}.json"
        result = json.loads(path.read_text()) if path.exists() else {}
        if completed.returncode and result.get("status") not in {"oom", "error"}:
            result.update(status="worker_failed", returncode=completed.returncode)
        summary["case_results"][case] = result
        write_metrics(destination, summary)
    success = all(result.get("status") == "ok" for result in summary["case_results"].values())
    if success and len(cases) == len(CASES):
        summary["estimated_epoch_training_only_sec"] = estimate_epoch(rows, summary)
        summary["estimator"] = "piecewise linear per-sample time by unpadded length, summed over actual rows"
    write_metrics(destination, summary)
    return int(not success)


if __name__ == "__main__":
    sys.exit(main())
