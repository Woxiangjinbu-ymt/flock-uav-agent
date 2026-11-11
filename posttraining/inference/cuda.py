"""Transformers BF16 text inference for Qwen3.5-2B on a single CUDA device."""
import json
import time
from . import SAMPLING
from .timing import TokenTimes, generation_stats
from .weights import MANIFEST, verify_weights


def _cuda():
    import torch
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("student generation requires a BF16 CUDA device")
    return torch


class TransformersModel:
    def __init__(self, path, adapter_dir=None):
        from transformers import Qwen3_5ForCausalLM
        self.torch = _cuda()
        verify_weights(path)
        loaded, report = Qwen3_5ForCausalLM.from_pretrained(str(path), local_files_only=True, dtype=self.torch.bfloat16,
                                                trust_remote_code=False, use_safetensors=True,
                                                output_loading_info=True)
        for field in ("missing_keys", "mismatched_keys", "error_msgs", "unexpected_keys"):
            problems = report[field]
            # The pinned 2B checkpoint also ships the vision tower and MTP.
            if field == "unexpected_keys":
                problems = [k for k in problems if not k.startswith(("mtp.", "model.visual."))]
            if problems:
                raise ValueError(f"Unusable student checkpoint: {field}: {sorted(map(str, problems))[:4]}")
        self.load_info = {"class": type(loaded).__name__, "config_model_type": loaded.config.model_type,
                          "missing_keys": 0, "mismatched_keys": 0, "error_msgs": 0,
                          "unused_checkpoint_keys": len(report["unexpected_keys"])}
        self.model = loaded.to("cuda")
        if adapter_dir is not None:
            from ..sft.checkpoint import load_adapter
            revision = json.loads(MANIFEST.read_bytes())["revision"]
            self.load_info["adapter"] = load_adapter(self.model, adapter_dir, revision)
        self.model.eval()

    def generate(self, ids, limit, seed, eos):
        started = time.monotonic()
        clock = TokenTimes()
        self.torch.cuda.reset_peak_memory_stats()
        self.torch.manual_seed(seed)
        prompt = self.torch.tensor([ids], dtype=self.torch.long, device="cuda")
        with self.torch.inference_mode():
            whole = self.model.generate(input_ids=prompt, attention_mask=self.torch.ones_like(prompt),
                                        streamer=clock, do_sample=True, max_new_tokens=limit, eos_token_id=eos,
                                        pad_token_id=self.model.generation_config.pad_token_id, **SAMPLING)
        output = whole[0, len(ids):].tolist()
        if clock.count != len(output):
            raise ValueError("streamed token count differs from generated IDs")
        return generation_stats(output, started, time.monotonic(), source="transformers_token_stream",
                                first=clock.first, last=clock.last,
                                reserved=self.torch.cuda.max_memory_reserved(),
                                allocated=self.torch.cuda.max_memory_allocated())
