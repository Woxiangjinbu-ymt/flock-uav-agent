"""Student wire history, token budgets, and atomic generated tool-call parsing."""

from __future__ import annotations

from copy import deepcopy
import json
import re

import student_input_v2 as v2

from .student_process import StudentProcess

MODEL_REVISIONS = {
    "1.7B": "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e",
    "4B": "1cfa9a7208912126459214e8b04321603b3df60c",
}
MAX_CONTEXT = 32768
MAX_GENERATION = 2048
EPISODE_TOKEN_LIMIT = 100000
SAMPLING = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "repetition_penalty": 1.0}


def render(tokenizer, messages, tools, thinking):
    """Validate v2 ID linkage before restoring each assistant's actual reasoning."""
    history = deepcopy(messages)
    reasoning = {}
    for i, msg in enumerate(history):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            body = msg.pop("reasoning_content", None)
            if body is not None:
                if not isinstance(body, str) or any(m in body for m in
                                                    (*v2.base.CONTROL_MARKERS, "<think>", "</think>")):
                    raise v2.InputRejected("history_reasoning", "Invalid historical reasoning content")
                reasoning[i] = body
    prepared = v2.prepare_history(history, tools)
    formatted = deepcopy(prepared["messages"])
    for i, body in reasoning.items():
        formatted[i]["reasoning_content"] = body
    template = v2.template_text()
    text = tokenizer.apply_chat_template(formatted, tools=tools, chat_template=template,
                                         tokenize=False, add_generation_prompt=True,
                                         enable_thinking=thinking, target_message_index=-1)
    encoded = tokenizer(text, add_special_tokens=False, truncation=False, return_offsets_mapping=True)
    # A tool's or user's literal think tag is outside the assistant role boundary.
    spans = [m.span(1) for m in re.finditer(r"<\|im_start\|>assistant\n<think>\n(.*?)\n</think>", text, re.S)
             if m.group(1).strip()]
    history_count = sum(any(start < b and end > a for a, b in spans)
                        for start, end in encoded["offset_mapping"])
    return {"input_ids": list(encoded["input_ids"]), "history_reasoning_tokens": history_count,
            "template_sha256": v2.sha(template.encode()), "prompt_text": text}, prepared


def output_budget(prompt_tokens, spent_tokens):
    available = min(MAX_GENERATION, EPISODE_TOKEN_LIMIT - spent_tokens - prompt_tokens)
    if available < 1:
        return 0, "token_budget"
    if prompt_tokens + available > MAX_CONTEXT:
        return 0, "context_length"
    return available, None


def _decode(tokenizer, ids):
    return tokenizer.decode(ids, skip_special_tokens=False, clean_up_tokenization_spaces=False)


def parse_generation(tokenizer, generated, eos_ids, thinking, prepared, limit):
    """Return a wire-compatible message only when the entire suffix is parseable."""
    opening = tokenizer.convert_tokens_to_ids("<think>")
    closing = tokenizer.convert_tokens_to_ids("</think>")
    terminated = bool(generated and generated[-1] in eos_ids)
    body = generated[:-1] if terminated else generated
    anomalies = []
    if not terminated:
        anomalies.append("generation_truncated" if len(generated) == limit else "missing_eos")
    if any(token in eos_ids for token in body):
        anomalies.append("interior_eos")
    opens = [i for i, token in enumerate(body) if token == opening]
    closes = [i for i, token in enumerate(body) if token == closing]
    answer_ids, reasoning_text, reasoning_tokens = body, "", 0
    if opens or closes:
        if not thinking:
            anomalies.append("unexpected_thinking")
        if opens != [0] or len(closes) > 1 or (closes and closes[0] == 0):
            anomalies.append("malformed_thinking")
            reasoning_tokens = None
        else:
            end = closes[0] if closes else len(body)
            thought_ids = body[1:end]
            reasoning_text = _decode(tokenizer, thought_ids)
            reasoning_tokens = len(thought_ids) if reasoning_text.strip() else 0
            answer_ids = body[end + 1:]
            if not closes:
                anomalies.append("unclosed_thinking")
    message = None
    if not anomalies:
        try:
            text = _decode(tokenizer, answer_ids)
            if closes:
                text = text.lstrip("\n")
            decision = v2.parse_generated_decision(text + "<|im_end|>", prepared["tools"])
            attached = v2.attach_generated_decision(prepared, decision)
            message = attached["messages"][-1]
            used_ids = {entry["original_id"] for entry in prepared["id_sidecar"]}
            next_id = prepared["next_call_index"]
            for call in message.get("tool_calls", []):
                while f"call_{next_id}" in used_ids:
                    next_id += 1
                call["id"] = f"call_{next_id}"
                used_ids.add(call["id"])
                next_id += 1
                call["function"]["arguments"] = json.dumps(call["function"]["arguments"], ensure_ascii=False,
                                                          separators=(",", ":"), allow_nan=False)
            if opens:
                message["reasoning_content"] = reasoning_text
        except v2.InputRejected as error:
            anomalies.append(error.code)
    return {"message": message, "anomalies": anomalies, "reasoning_tokens": reasoning_tokens,
            "terminated": terminated}
