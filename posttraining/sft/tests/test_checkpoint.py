"""A trained native LoRA must reproduce its outputs after the base is reloaded."""

import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path

from posttraining.sft.benchmark import PROJECTIONS, attach_lora
from posttraining.sft.checkpoint import WEIGHTS, load_adapter, save_adapter


@unittest.skipUnless(importlib.util.find_spec("torch") and importlib.util.find_spec("safetensors"),
                     "the training environment supplies torch and safetensors")
class CheckpointTest(unittest.TestCase):
    def test_predictions_survive_export_and_changed_bytes_are_rejected(self):
        import torch
        from torch import nn

        torch.manual_seed(19)
        base = nn.Module()
        base.layer = nn.ModuleDict({
            group: nn.ModuleDict({name: nn.Linear(5, 4) for name in sorted(names)})
            for group, names in PROJECTIONS.items()
        })
        trained = copy.deepcopy(base).requires_grad_(False)
        sample = torch.randn(3, 5)

        def predict(model):
            return torch.stack([projection(sample) for group in model.layer.values()
                                for projection in group.values()])

        attach_lora(trained, rank=2, alpha=4)
        optimizer = torch.optim.SGD((p for p in trained.parameters() if p.requires_grad), lr=0.2)
        predict(trained).square().sum().backward()
        optimizer.step()
        expected = predict(trained).detach()
        self.assertFalse(torch.equal(predict(base), expected))
        with tempfile.TemporaryDirectory() as folder:
            meta = save_adapter(trained, folder, 2, 4, "revision")
            with self.assertRaisesRegex(ValueError, "revision"):
                load_adapter(copy.deepcopy(base), folder, "other")
            reloaded = copy.deepcopy(base)
            self.assertEqual(meta, load_adapter(reloaded, folder, "revision"))
            self.assertTrue(torch.equal(expected, predict(reloaded)))
            weights = Path(folder) / WEIGHTS
            with weights.open("r+b") as handle:
                handle.seek(-1, 2)
                last_byte = handle.read(1)[0]
                handle.seek(-1, 2)
                handle.write(bytes([last_byte ^ 1]))
            with self.assertRaisesRegex(ValueError, "checksum"):
                load_adapter(copy.deepcopy(base), folder, "revision")
