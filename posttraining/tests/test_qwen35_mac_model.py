"""Check Mac engine boundaries without requiring MLX, Metal or model weights."""
from pathlib import Path
import sys
import time
from types import ModuleType

import pytest

SCRIPTS = (Path(__file__).resolve().parents[2] / "scripts")
sys.path.insert(0, str(SCRIPTS))
from flockcue_qwen35 import mac_model


@pytest.mark.parametrize("ids,expected", [([24, 248046, 42], [24, 248046]), ([24, 27, 42], [24, 27])])
def test_decode_keeps_native_eos_and_cutoff(ids, expected, monkeypatch):
    for name in ("mlx", "mlx.core", "mlx_lm", "mlx_lm.generate", "mlx_lm.sample_utils"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    mx = sys.modules["mlx.core"]
    mx.random = type("Seed", (), {"seed": staticmethod(lambda seed: None)})
    mx.uint32 = object()
    mx.array = lambda data, dtype: data
    mx.reset_peak_memory = lambda: None
    mx.synchronize = lambda stream: None
    mx.get_peak_memory = lambda: 1024
    module = sys.modules["mlx_lm.generate"]
    module.generation_stream = object()

    def generate(prompt, model, *, max_tokens, sampler, prefill_step_size):
        assert prompt == [11111, 0, 123] and max_tokens == 2
        for token in ids[:max_tokens]:
            yield token, None

    module.generate_step = generate
    sys.modules["mlx_lm.sample_utils"].make_sampler = lambda **opts: None
    result = mac_model.infer(None, 248320, [11111, 0, 123], 2, 0)
    assert result["generated_ids"] == expected
    assert result["peak_allocated_bytes"] == 1024


def test_timeout_reaps_child_without_returning_output(tmp_path):
    # A token-only child acknowledges startup and stalls at its first request.
    package = tmp_path / "flockcue_qwen35"
    package.mkdir()
    (package / "__init__.py").touch()
    (package / "mac_model.py").write_text(
        "import os, socket, sys, time\n"
        f"sys.path.append({str(SCRIPTS)!r})\n"
        "from thinking_events.student_process import receive, send\n"
        "with socket.socket(fileno=int(sys.argv[1])) as sock:\n"
        f"    send(sock, {{'status':'ready','metadata':{{'pid':os.getpid(), 'weight_sha256':{mac_model.WEIGHT_SHA256!r}}}}})\n"
        "    receive(sock)\n"
        "    time.sleep(30)\n"
    )
    with mac_model.MacModel(sys.executable, tmp_path, scripts_root=tmp_path) as model:
        with pytest.raises(TimeoutError):
            model.generate([1, 2], 64, 0, time.monotonic() + 0.1)
        assert model.process.poll() is not None
        with pytest.raises(RuntimeError, match="closed"):
            model.generate([1, 2], 64, 0, time.monotonic() + 1)
