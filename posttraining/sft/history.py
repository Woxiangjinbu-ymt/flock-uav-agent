"""Bind tool calls to receipts before rendering the native student transcript."""
from copy import deepcopy
import hashlib
import json
import re


CONTROL_MARKERS = ("<|im_start|>", "<|im_end|>", "<|endoftext|>")
BLOCK_TAGS = ("<tool_call>", "</tool_call>", "<tool_response>", "</tool_response>")
TEMPLATE_ID = "qwen3-tool-ids-v2"
STATE_SCHEMA = "student-tool-history-v2"


class InputRejected(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def require(condition, code, message, **details):
    if not condition:
        raise InputRejected(code, message, **details)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field: " + key)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def _json(value, code="json_value"):
    try:
        return canonical(value).decode()
    except (TypeError, ValueError) as exc:
        raise InputRejected(code, "Only finite JSON values are supported") from exc


def _tools(tools):
    require(isinstance(tools, list), "tools", "Tools must be an array")
    require(not any(x in _json(tools) for x in CONTROL_MARKERS), "control_token_in_tool_data", "Control token in tool schemas")
    names = set()
    for tool in tools:
        require(isinstance(tool, dict) and tool.get("type") == "function" and isinstance(tool.get("function"), dict), "tools", "Only function schemas are supported")
        function = tool["function"]
        name = function.get("name")
        require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]+", name) and name not in names, "tools", "Tool names must be unique ASCII function names")
        require(isinstance(function.get("parameters"), dict), "tools", "Tool parameters require a JSON schema object")
        names.add(name)
    return names


def _message(original, index):
    require(isinstance(original, dict), "messages", "Each message must be an object")
    message = deepcopy(original)
    role = message.get("role")
    require(role in {"system", "user", "assistant", "tool"}, "role_unsupported", "Unsupported message role", message_index=index)
    require(not message.get("reasoning_content") and not message.get("reasoning"), "reasoning_not_allowed", "Reasoning must be removed before formatting")
    message.pop("reasoning_content", None)
    message.pop("reasoning", None)
    allowed = {"role", "content"} | ({"tool_calls"} if role == "assistant" else set()) | ({"tool_call_id", "name"} if role == "tool" else set())
    require(set(message) <= allowed, "message_fields", "Unsupported message fields would be lost")
    content = message.get("content")
    if role == "assistant" and content is None:
        content = ""
    require(isinstance(content, str), "nontext_unsupported", "Only text content is supported")
    require(not any(x in content for x in CONTROL_MARKERS), "control_token_in_content", "Raw role/end control token in content")
    if role == "assistant":
        require("<think>" not in content and "</think>" not in content, "reasoning_not_allowed", "Assistant reasoning markup is unsupported")
        require(not any(x in content for x in BLOCK_TAGS), "assistant_block_tag", "Assistant prose cannot contain reserved tool block tags")
    if role == "system":
        require(index == 0, "system_position", "System message must be first")
    message["content"] = content
    return message


def _calls(message, names, *, require_ids):
    calls = message.get("tool_calls")
    if calls is None:
        calls = []
    require(isinstance(calls, list), "tool_calls", "Tool calls must be an array")
    require(message["content"].strip() or calls, "empty_assistant", "Assistant needs text or tool calls")
    normalized = []
    for original in calls:
        allowed = {"type", "function", "id"} if require_ids else {"type", "function"}
        require(isinstance(original, dict) and set(original) <= allowed and original.get("type") == "function"
                and isinstance(original.get("function"), dict), "tool_calls", "Only function calls with the declared ID mode are supported")
        call = deepcopy(original)
        if require_ids:
            require(isinstance(call.get("id"), str) and bool(call["id"]), "tool_id_mapping", "History/source calls need nonempty IDs")
        function = call["function"]
        require(set(function) == {"name", "arguments"} and isinstance(function["name"], str) and function["name"] in names,
                "tool_calls", "Call must match an advertised tool")
        args = function["arguments"]
        if isinstance(args, str):
            try:
                args = parse(args)
            except (ValueError, TypeError) as exc:
                raise InputRejected("tool_arguments", "Arguments are not strict JSON") from exc
        require(isinstance(args, dict), "tool_arguments", "Arguments must be a JSON object")
        require(not any(x in _json(args, "tool_arguments") for x in CONTROL_MARKERS), "control_token_in_tool_data", "Control token in arguments")
        function["arguments"] = args
        normalized.append(call)
    if normalized:
        message["tool_calls"] = normalized
    else:
        message.pop("tool_calls", None)
    return normalized


def _scan(messages, tools, *, allow_incomplete=False):
    require(isinstance(messages, list) and bool(messages), "messages", "Nonempty history required")
    names = _tools(tools)
    normalized, links, pending, seen = [], [], {}, set()
    saw_user = False
    for index, original in enumerate(messages):
        message = _message(original, index)
        role = message["role"]
        if role != "tool":
            require(not pending, "tool_receipt_missing", "No pending calls may cross a user/assistant boundary", message_index=index)
        if role == "user":
            saw_user = True
        if role == "assistant":
            for call in _calls(message, names, require_ids=True):
                original_id = call["id"]
                require(original_id not in seen, "tool_id_mapping", "Call IDs must be globally unique across history")
                seen.add(original_id)
                canonical_id = f"call_{len(links)+1}"
                function = call["function"]
                link = {"original_id": original_id, "canonical_id": canonical_id, "name": function["name"],
                        "arguments_sha256": sha(canonical(function["arguments"])), "call_message_index": index,
                        "receipt_message_index": None, "origin": "source_history"}
                links.append(link)
                pending[original_id] = link
                call["id"] = canonical_id
        if role == "tool":
            original_id = message.get("tool_call_id")
            require(isinstance(original_id, str) and original_id in pending, "tool_id_mapping", "Receipt ID is missing, duplicate, unknown or from a previous round", message_index=index)
            link = pending.pop(original_id)
            require("name" not in message or message["name"] == link["name"], "tool_id_mapping", "Receipt name differs from its call")
            message["tool_call_id"], message["name"] = link["canonical_id"], link["name"]
            link["receipt_message_index"] = index
            link["receipt_content_sha256"] = sha(message["content"].encode())
        normalized.append(message)
    require(saw_user, "task_missing", "User task must remain in history")
    if not allow_incomplete:
        require(not pending, "tool_receipt_missing", "All historical tool calls need exactly one receipt")
        require(normalized[-1]["role"] in {"user", "tool"}, "target_boundary", "Ready history must end with user/tool feedback")
    return normalized, links, list(pending)


def _seal_state(state):
    state["state_sha256"] = sha(canonical({key: value for key, value in state.items() if key != "state_sha256"}))
    return state


def _validate_prepared(state, *, ready=True):
    require(isinstance(state, dict) and state.get("schema") == STATE_SCHEMA and state.get("template_id") == TEMPLATE_ID,
            "prepared_state", "Unsupported prepared history")
    require(state.get("state_sha256") == sha(canonical({k: v for k, v in state.items() if k != "state_sha256"})),
            "prepared_state", "Prepared history changed after validation")
    history, links, pending = _scan(state.get("messages"), state.get("tools"), allow_incomplete=not ready)
    require(history == state["messages"] and type(state.get("next_call_index")) is int and state["next_call_index"] == len(links)+1,
            "prepared_state", "Prepared IDs/counter are not canonical in call order")
    sidecar = state.get("id_sidecar")
    require(isinstance(sidecar, list) and len(sidecar) == len(links), "prepared_state", "ID sidecar length differs from history")
    originals = set()
    for actual, audit in zip(links, sidecar):
        require(isinstance(audit, dict) and isinstance(audit.get("original_id"), str) and bool(audit["original_id"])
                and audit.get("origin") in {"source_history", "runtime_assigned"}
                and (audit["origin"], audit["original_id"]) not in originals, "prepared_state", "IDs must remain unique within their explicit provenance namespaces")
        originals.add((audit["origin"], audit["original_id"]))
        require(all(actual.get(key) == audit.get(key) for key in ("canonical_id", "name", "arguments_sha256", "call_message_index", "receipt_message_index", "receipt_content_sha256")),
                "prepared_state", "ID sidecar differs from validated messages")
    require(state.get("pending_ids") == [link["canonical_id"] for link in links if link["receipt_message_index"] is None],
            "prepared_state", "Pending IDs differ from validated history")
    if "new_tool_calls" in state:
        require(state["messages"][-1]["role"] == "assistant" and state["new_tool_calls"] == state["messages"][-1].get("tool_calls", []),
                "prepared_state", "Attached calls differ from the validated assistant message")
    if ready:
        require(state.get("status") == "ready" and not pending, "prepared_state", "History is not ready for a decision")


def prepare_history(messages, tools, *, prior=None):
    """Canonicalize outer IDs only, preserve every receipt's actual position."""
    if prior is not None:
        _validate_prepared(prior, ready=False)
        # A runtime transition extends already canonical messages. Compare the
        # raw prefix before renumbering: normalization could otherwise hide an
        # ID swap and attach A's new receipt to B's earlier call.
        require(isinstance(messages, list) and tools == prior["tools"]
                and messages[:len(prior["messages"])] == prior["messages"],
                "prepared_transition", "Runtime history prefix must be unchanged before canonicalization")
    normalized, links, _ = _scan(messages, tools)
    if prior is not None:
        require(tools == prior["tools"] and normalized[:len(prior["messages"])] == prior["messages"],
                "prepared_transition", "A next round must retain the attached canonical history prefix")
        require(len(links) >= len(prior["id_sidecar"]), "prepared_transition", "Prior calls disappeared")
        for link, old in zip(links, prior["id_sidecar"]):
            require(link["canonical_id"] == old["canonical_id"], "prepared_transition", "Prior call numbering changed")
            link["original_id"], link["origin"] = old["original_id"], old["origin"]
    state = _seal_state({"schema": STATE_SCHEMA, "template_id": TEMPLATE_ID, "status": "ready",
                        "messages": normalized, "tools": deepcopy(tools), "id_sidecar": links,
                        "next_call_index": len(links)+1, "pending_ids": [],
                        "input_sha256": sha(canonical({"messages": messages, "tools": tools})),
                        "namespace": "canonical outer linkage only; business strings are not rewritten"})
    _validate_prepared(state)
    return state


def _target(original, tools, history_links, *, source_ids):
    message = _message(original, 1)
    require(message["role"] == "assistant", "target", "Target must be an assistant decision")
    calls = _calls(message, _tools(tools), require_ids=source_ids)
    target_links, seen = [], {link["original_id"] for link in history_links}
    if source_ids:
        for call in calls:
            require(call["id"] not in seen, "tool_id_mapping", "Target ID duplicates a historical/current call ID")
            seen.add(call["id"])
            target_links.append({"original_id": call.pop("id"), "name": call["function"]["name"],
                                 "arguments_sha256": sha(canonical(call["function"]["arguments"])), "serialized_in_target": False})
    return message, target_links


def attach_generated_decision(prepared, decision):
    """Assign runtime IDs after complete parsing; returns data, executes nothing."""
    _validate_prepared(prepared)
    normalized, _ = _target(decision, prepared["tools"], [], source_ids=False)
    state = deepcopy(prepared)
    for call in normalized.get("tool_calls", []):
        canonical_id = f"call_{state['next_call_index']}"
        call["id"] = canonical_id
        # Only canonical messages are extended. Provenance distinguishes the
        # new runtime ID from an old source ID that happened to be call_N.
        state["id_sidecar"].append({"original_id": canonical_id, "canonical_id": canonical_id, "origin": "runtime_assigned",
            "name": call["function"]["name"], "arguments_sha256": sha(canonical(call["function"]["arguments"])),
            "call_message_index": len(state["messages"]), "receipt_message_index": None})
        state["next_call_index"] += 1
    state["messages"].append(normalized)
    state["pending_ids"] = [call["id"] for call in normalized.get("tool_calls", [])]
    state["status"] = "awaiting_tool_receipts" if state["pending_ids"] else "assistant_completed"
    state["new_tool_calls"] = deepcopy(normalized.get("tool_calls", []))
    _seal_state(state)
    _validate_prepared(state, ready=False)
    return state
