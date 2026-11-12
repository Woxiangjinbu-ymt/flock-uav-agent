"""Multi-epoch LoRA must survive a partial accumulation without double-updating."""
from contextlib import ExitStack, nullcontext, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from posttraining.sft import benchmark, train
from posttraining.sft.checkpoint import save_adapter


class ScheduleTest(unittest.TestCase):
    def test_independent_epoch_orders_tail_groups_and_warmup_decay(self):
        order = train.sample_order(5, 2, 123)
        self.assertEqual(sorted(order[:5]), list(range(5)))
        self.assertEqual(sorted(order[5:]), list(range(5)))
        self.assertEqual(order[5:], train.sample_order(5, 1, 124))
        self.assertEqual(list(train.update_windows(3, 2, 2)), [
            (1, 1, 0, 2), (1, 2, 2, 3), (2, 3, 3, 5), (2, 4, 5, 6)])
        rates = [train.learning_rate(i, 10, 1e-4, 2e-5, 2, "cosine") for i in range(1, 11)]
        self.assertEqual(rates[:2], [5e-5, 1e-4])
        self.assertEqual(rates[-1], 2e-5)
        self.assertEqual(rates[1:], sorted(rates[1:], reverse=True))


@unittest.skipUnless(importlib.util.find_spec("torch") and importlib.util.find_spec("safetensors"),
                     "the training environment supplies torch and safetensors")
class TrainingTest(unittest.TestCase):
    def test_warm_start_tail_windows_and_replaying_a_partial_update(self):
        import torch
        from torch import nn

        class Core(nn.Module):
            def __init__(self):
                super().__init__()
                self.emb = nn.Embedding(12, 4)
                self.blocks = nn.ModuleDict({
                    group: nn.ModuleDict({name: nn.Linear(4, 4) for name in sorted(names)})
                    for group, names in benchmark.PROJECTIONS.items()})

            def forward(self, input_ids, **kwargs):
                x = self.emb(input_ids)
                y = sum(layer(x) for group in self.blocks.values() for layer in group.values())
                return SimpleNamespace(last_hidden_state=y)

        class TinyModel(nn.Module):
            def __init__(self):
                super().__init__()
                self.model = Core()
                self.lm_head = nn.Linear(4, 12)

            def gradient_checkpointing_enable(self, **kwargs):
                self.is_gradient_checkpointing = True

        torch.manual_seed(24)
        base = TinyModel().requires_grad_(False)
        revision = json.loads(train.MODEL_MANIFEST.read_bytes())["revision"]
        # Use nonzero parent adapters to distinguish a warm start from new LoRA initialization.
        parent = deepcopy(base)
        benchmark.attach_lora(parent, 16, 32)
        with torch.no_grad():
            for name, p in parent.named_parameters():
                if name.endswith(".lora_B"):
                    p.normal_(std=0.01)
        with TemporaryDirectory() as tmp, ExitStack() as stack, redirect_stdout(io.StringIO()):
            root = Path(tmp)
            meta = save_adapter(parent, root / "parent", 16, 32, revision)
            args = SimpleNamespace(model=root, dataset=root/"train.jsonl.gz", init_adapter=root/"parent",
                                   init_adapter_sha256=meta["weights_sha256"], epochs=2, learning_rate=1e-4,
                                   min_learning_rate=2e-5, warmup_ratio=0.05, lr_schedule="cosine",
                                   gradient_accumulation=2, max_grad_norm=1.0, checkpoint_every=1,
                                   seed=91, resume=False)
            config = {"base_revision":revision, "rank":16, "alpha":32}
            warm = deepcopy(base)
            trained = train.training_parameters(warm, args.init_adapter, args.init_adapter_sha256, config)
            self.assertEqual(set(trained), {n for n,p in parent.named_parameters() if p.requires_grad})
            self.assertTrue(all(torch.equal(trained[n], p) for n,p in parent.named_parameters() if n in trained))
            with self.assertRaisesRegex(ValueError, "Initial adapter"):
                train.training_parameters(deepcopy(base), args.init_adapter, "0"*64, config)

            samples = []
            for i in range(3):
                ids = torch.arange(i+3, dtype=torch.int32)
                ref = dict(session_id="s", task_id="t", logical_call_id=str(i), supervised_tokens=len(ids)-1)
                samples.append((ids, 1, ref))
            stack.enter_context(patch.object(train, "verify_weights", return_value="model"))
            stack.enter_context(patch.object(train, "read_release", return_value=(
                samples, {"files": {"train.jsonl.gz": {"sha256": "rows"}}}, "release")))
            stack.enter_context(patch.object(benchmark, "hardware_info", return_value={"test":"cpu"}))
            stack.enter_context(patch.object(benchmark, "load_text_model", side_effect=lambda _: deepcopy(base)))

            # Exercise the production update, checkpoint and export paths with real CPU tensors.
            to = torch.Tensor.to
            def on_cpu(tensor, *positional, **kwargs):
                if kwargs.get("device") == "cuda":
                    kwargs["device"] = "cpu"
                return to(tensor, *positional, **kwargs)
            stack.enter_context(patch.object(torch.Tensor, "to", on_cpu))
            stack.enter_context(patch.object(torch, "autocast", return_value=nullcontext()))
            for name in ("is_available", "is_bf16_supported"):
                stack.enter_context(patch.object(torch.cuda, name, return_value=True))
            for name in ("synchronize", "reset_peak_memory_stats", "set_rng_state"):
                stack.enter_context(patch.object(torch.cuda, name, return_value=None))
            stack.enter_context(patch.object(torch.cuda, "get_rng_state", side_effect=torch.get_rng_state))
            for name in ("max_memory_allocated", "max_memory_reserved", "is_current_stream_capturing"):
                stack.enter_context(patch.object(torch.cuda, name, return_value=0))
            adam = torch.optim.AdamW
            stack.enter_context(patch.object(torch.optim, "AdamW", side_effect=lambda params, lr, fused: adam(params, lr=lr)))
            args.output = root / "complete"
            args.output.mkdir()
            train.train(args)
            partial = root / "partial"
            partial.mkdir()
            step = benchmark.measure_step
            calls = 0
            def interrupt(*pos, **kw):
                nonlocal calls
                calls += 1
                if calls == 5:
                    raise RuntimeError("test interruption inside update")
                return step(*pos, **kw)
            args.output = partial
            with patch.object(benchmark, "measure_step", interrupt), self.assertRaisesRegex(RuntimeError, "interruption"):
                train.train(args)
            self.assertEqual(json.loads((partial/"progress.json").read_bytes())["checkpoint_step"], 3)
            args.resume = True
            train.train(args)
            for directory in ("epoch-1", "epoch-2", "."):
                self.assertEqual((partial/directory/"adapter.safetensors").read_bytes(),
                                 (root/"complete"/directory/"adapter.safetensors").read_bytes())
            self.assertNotEqual((partial/"epoch-1"/"adapter.safetensors").read_bytes(),
                                (partial/"epoch-2"/"adapter.safetensors").read_bytes())
            summary = json.loads((partial/"summary.json").read_bytes())
            self.assertEqual((summary["steps"], summary["optimizer_updates"]), (6, 4))
            records = [json.loads(x) for x in (partial/"steps.jsonl").read_text().splitlines()]
            self.assertEqual([r["update_applied"] for r in records], [False, True, True]*2)
            self.assertEqual([r["accumulation_size"] for r in records], [2, 2, 1]*2)
            self.assertTrue(all(("grad_norm_before_clip" in r) == r["update_applied"] for r in records))
            self.assertEqual(records[-1]["learning_rate"], 2e-5)
            args.learning_rate = 2e-4
            with self.assertRaisesRegex(ValueError, "Resume configuration"):
                train.train(args)

            # A run.json written by the original first-pass trainer remains resumable.
            args.output = root / "legacy"
            args.output.mkdir()
            args.init_adapter = args.init_adapter_sha256 = args.max_grad_norm = None
            args.epochs = args.gradient_accumulation = 1
            args.min_learning_rate = args.warmup_ratio = 0.0
            args.lr_schedule = "constant"
            previous_identity = {
                "schema":"flockcue-first-pass-lora-v1", "model_manifest_sha256":"model",
                "base_revision":revision, "release_manifest_sha256":"release", "dataset_sha256":"rows",
                "seed":91, "order_one_based":[2, 3, 1], "epochs":1,
                "rank":16, "alpha":32, "dropout":0, "base_dtype":"bfloat16", "adapter_dtype":"float32",
                "batch_size":1, "gradient_accumulation":1, "optimizer":"fused AdamW", "lr":0.0002,
                "betas":[0.9, 0.999], "eps":1e-8, "weight_decay":0.01, "lr_schedule":"constant",
                "gradient_checkpointing":"nonreentrant"}
            (args.output/"run.json").write_text(json.dumps({"identity":previous_identity}))
            train.train(args)
            self.assertEqual(json.loads((args.output/"summary.json").read_bytes())["steps"], 3)
