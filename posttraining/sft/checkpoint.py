"""Persist and restore the native Qwen LoRA projections used for SFT."""

import hashlib
import json
import math
from pathlib import Path
import tempfile

from .benchmark import attach_lora


WEIGHTS = "adapter.safetensors"
CONFIG = "adapter_config.json"
FORMAT = "flockcue-lora/1"


def _parameters(model):
    return {name: parameter for name, parameter in model.named_parameters()
            if name.endswith((".lora_A", ".lora_B"))}


def _sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def save_adapter(model, directory, rank, alpha, base_revision):
    """Write reloadable weights and return their manifest after a readback check."""
    import torch
    from safetensors.torch import load_file, save_file

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    tensors = {name: parameter.detach().cpu().contiguous()
               for name, parameter in _parameters(model).items()}
    if not tensors or any(t.dtype != torch.float32 or not torch.isfinite(t).all() for t in tensors.values()):
        raise ValueError("Expected finite FP32 LoRA parameters")
    with tempfile.TemporaryDirectory(dir=directory) as staging:
        weights = Path(staging) / WEIGHTS
        save_file(tensors, weights)
        recovered = load_file(weights)
        if recovered.keys() != tensors.keys() or any(not torch.equal(t, recovered[k]) for k, t in tensors.items()):
            raise ValueError("Adapter differs after safetensors readback")
        config = {
            "format": FORMAT, "base_revision": base_revision, "rank": rank, "alpha": alpha,
            "weights_sha256": _sha256(weights), "tensor_count": len(tensors),
            "parameter_count": sum(t.numel() for t in tensors.values()),
        }
        config_file = Path(staging) / CONFIG
        config_file.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        weights.replace(directory / WEIGHTS)
        config_file.replace(directory / CONFIG)
    return config


def load_adapter(model, directory, expected_revision):
    """Attach a checked adapter to a freshly loaded base for inference."""
    import torch
    from safetensors.torch import load_file

    directory = Path(directory)
    config = json.loads((directory / CONFIG).read_text(encoding="utf-8"))
    if config["format"] != FORMAT or config["base_revision"] != expected_revision:
        raise ValueError("Adapter format or base model revision mismatch")
    rank, alpha = config["rank"], config["alpha"]
    if type(rank) is not int or rank < 1 or not math.isfinite(alpha) or alpha <= 0:
        raise ValueError("Invalid adapter rank or scale")
    weights = directory / WEIGHTS
    if _sha256(weights) != config["weights_sha256"]:
        raise ValueError("Adapter weight checksum mismatch")
    saved = load_file(weights)
    if _parameters(model):
        raise ValueError("Model already has an adapter")
    attach_lora(model, rank, alpha)
    destinations = _parameters(model)
    if saved.keys() != destinations.keys() or len(saved) != config["tensor_count"]:
        raise ValueError("Adapter projection names differ from base model")
    if sum(t.numel() for t in saved.values()) != config["parameter_count"]:
        raise ValueError("Adapter parameter count mismatch")
    for name, parameter in destinations.items():
        value = saved[name]
        if value.shape != parameter.shape or value.dtype != parameter.dtype or not torch.isfinite(value).all():
            raise ValueError(f"Invalid adapter tensor: {name}")
    with torch.no_grad():
        for name, parameter in destinations.items():
            parameter.copy_(saved[name]).requires_grad_(False)
    return config
