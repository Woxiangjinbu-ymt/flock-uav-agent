"""Exercise the paired wire codecs and verify their measured token counts without a GPU."""
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from flockcue_qwen35.adapter import DEFAULT_CACHE
from thinking_events import paired_backend as paired
from thinking_events import student_service as service
from thinking_events.student_process import StudentProcess

PYTHON = Path(os.environ.get("FLOCKCUE_TOKENIZER_PYTHON", ROOT / "training/.venv-input/bin/python"))
QWEN35 = Path(os.environ.get("FLOCKCUE_QWEN35_TOKENIZER", DEFAULT_CACHE))
MESSAGES = [{"role": "user", "content": "Check the fleet."}]
TOOLS = [{"type": "function", "function": {"name": "list_drones",
          "parameters": {"type": "object", "properties": {}}}}]


@pytest.mark.parametrize("model", [paired.MAIN, paired.REFERENCE])
def test_process_switches_paired_modes_and_reconstructs_token_usage(model):
    if model == paired.MAIN and not QWEN35.is_dir():
        pytest.skip("Install the pinned Qwen3.5 tokenizer or set FLOCKCUE_QWEN35_TOKENIZER")
    modes = {"on": True, "off": False}
    scripts = {slot: paired.fixture_replies(model, thinking) for slot, thinking in modes.items()}
    # A cut second call invalidates the entire generated group, including the first complete call.
    scripts["broken"] = [scripts["off"][0].replace("<|im_end|>", "\n<tool_call>{<|im_end|>")]
    directory = QWEN35 if model == paired.MAIN else None
    tok, codec, eos, template_hash = paired.load_codec(model, directory)
    with StudentProcess(PYTHON, model=model, tokenizer_path=directory, fixture_replies=scripts) as client:
        assert client.metadata["weights_loaded"] is False
        assert client.metadata["template_sha256"] == template_hash
        assert client.metadata["tokenizer_files"] == paired.tokenizer_files(model)
        assert client.metadata["revision"] == paired.pinned_manifest(model)["revision"]
        for slot, thinking in modes.items():
            history = MESSAGES[:]
            spent = 0
            for turn in (1, 2):
                prompt, _ = codec.render(tok, history, TOOLS, thinking)
                result = client.request(slot=slot, turn=turn, thinking=thinking, seed=93, messages=history,
                                        tools=TOOLS, spent_tokens=spent, episode_deadline=time.monotonic() + 20)
                assert result["status"] == "ok" and result["seed"] == 93
                assert result["input_ids"] == prompt["input_ids"]
                assert result["generated_ids"] == tok.encode(scripts[slot][turn - 1], add_special_tokens=False)
                assert result["generated_ids"][-1] in eos
                assert result["usage"] == {
                    "prompt_tokens": len(result["input_ids"]), "completion_tokens": len(result["generated_ids"]),
                    "total_tokens": len(result["input_ids"]) + len(result["generated_ids"]),
                    "completion_tokens_details": {"reasoning_tokens": len(tok.encode("Read the fleet.")) if thinking else 0},
                }
                assert (result["history_reasoning_tokens"] > 0) == (turn == 2 and thinking)
                spent += result["usage"]["total_tokens"]
                if turn == 1:
                    message = result["message"]
                    call, = message["tool_calls"]
                    assert call["function"] == {"name": "list_drones", "arguments": "{}"}
                    history.extend([message, {"role": "tool", "tool_call_id": call["id"], "content": "[]"}])
                else:
                    assert result["message"]["content"] == "Done. [TASK DONE]"
        malformed = client.request(slot="broken", turn=1, thinking=False, seed=93, messages=MESSAGES,
                                   tools=TOOLS, spent_tokens=0, episode_deadline=time.monotonic() + 20)
        assert malformed["status"] == "format_error" and malformed["message"] is None


@pytest.mark.parametrize("model", [paired.MAIN, paired.REFERENCE])
def test_model_aliases_do_not_accept_other_weights(tmp_path, model):
    other = paired.MAIN if model == paired.REFERENCE else paired.REFERENCE
    wrong = paired.pinned_manifest(other)
    with pytest.raises(ValueError, match="paired student manifest or revision mismatch"):
        service.verify_model(tmp_path, model, wrong["revision"], wrong)


@pytest.mark.parametrize("field", ["missing_keys", "mismatched_keys", "unexpected_keys", "error_msgs"])
def test_paired_2b_rejects_partly_initialized_text_weights(monkeypatch, tmp_path, field):
    report = {name: [] for name in ("missing_keys", "mismatched_keys", "unexpected_keys", "error_msgs")}
    report[field] = ["model.layers.0.self_attn.q_proj.weight"]

    def load(path, *, output_loading_info, **settings):
        assert output_loading_info and settings["local_files_only"] and settings["use_safetensors"]
        return object(), report

    module = SimpleNamespace(AutoModelForCausalLM=None, Qwen3_5ForCausalLM=SimpleNamespace(from_pretrained=load))
    monkeypatch.setitem(sys.modules, "transformers", module)
    monkeypatch.setattr(service, "_cuda", lambda: SimpleNamespace(bfloat16="bfloat16"))
    with pytest.raises(ValueError, match=field):
        service.TransformersModel(tmp_path, paired.MAIN)
