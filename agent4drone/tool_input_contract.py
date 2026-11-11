"""Versioned input_json representation at the public tool-schema boundary.

The compatibility mode changes representation, not tool parameters or actions.
Original model messages and tool-call objects stay untouched: validation supplies
a string only to the existing tool handler. The legacy mode is an exact no-op.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


INPUT_JSON_CONTRACTS = ("string_v1", "json_object_or_string_v1", "json_object_or_string_v2")
_COMPAT = "json_object_or_string_v1"


def validate_input_json_contract(value: str) -> str:
    if type(value) is not str or value not in INPUT_JSON_CONTRACTS:
        raise ValueError("input_json_contract must be string_v1, json_object_or_string_v1 or json_object_or_string_v2")
    return value


def _unique_object(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("input_json must not contain duplicate object keys")
        value[key] = child
    return value


def _reject_constant(_value):
    raise ValueError("input_json numbers must be finite")


def _check_json_value(value, active, depth=0):
    if depth > 128:
        raise ValueError("input_json exceeds the supported JSON nesting depth")
    kind = type(value)
    if value is None or kind in (str, bool, int):
        return
    if kind is float:
        if not math.isfinite(value):
            raise ValueError("input_json numbers must be finite")
        return
    if kind not in (list, dict):
        raise ValueError("input_json must contain only JSON values")
    if id(value) in active:
        raise ValueError("input_json must not contain cyclic values")
    active.add(id(value))
    try:
        if kind is dict:
            if any(type(key) is not str for key in value):
                raise ValueError("input_json object keys must be strings")
            children = value.values()
        else:
            children = value
        for child in children:
            _check_json_value(child, active, depth + 1)
    finally:
        active.remove(id(value))


def _handler_string(value):
    """Validate one JSON object, without mutating caller-owned values."""
    if type(value) is str:
        try:
            parsed = json.loads(value, object_pairs_hook=_unique_object,
                                parse_constant=_reject_constant)
        except (ValueError, TypeError, RecursionError, OverflowError) as exc:
            raise ValueError("input_json must be a valid JSON object string without duplicate keys or nonfinite numbers") from exc
    elif type(value) is dict:
        parsed = value
    else:
        raise ValueError("input_json must be a JSON object or a JSON-object string")
    if type(parsed) is not dict:
        raise ValueError("input_json must represent an object, not an array or scalar")
    _check_json_value(parsed, set())
    # Preserve a legal legacy string byte-for-byte; only the explicit object
    # representation needs serialization for the unchanged legacy backend.
    if type(value) is str:
        return value
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class InputJsonObjectOrStringV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # LangChain derives the provider schema from field annotations and drops
    # validators while creating its subset schema. Keep the accepted union in
    # the annotation itself, not only json_schema_input_type on the validator.
    input_json: StrictStr | dict[str, Any] = Field(description=(
        "A JSON object (preferred), or a legacy string encoding that same object. "
        "Arrays/scalars, duplicate keys and nonfinite numbers are invalid. "
        "Use the tool's documented parameter names and values; no fields are inferred."
    ))

    @field_validator("input_json", mode="before", json_schema_input_type=StrictStr | dict[str, Any])
    @classmethod
    def object_or_legacy_string(cls, value):
        return _handler_string(value)


class InputJsonObjectOrStringV2(InputJsonObjectOrStringV1):
    """Same validator/envelope as v1; shared prompt rules replace field prose."""

    # Keep the actual union annotation: LangChain derives provider schemas from
    # it. Field() clears v1's repeated description without changing validation.
    input_json: StrictStr | dict[str, Any] = Field()


def adapt_input_json_tools(tools, contract: str):
    """Clone parameter tools with an explicit schema; do not rewrite messages.

    The accepted envelope is still exactly {"input_json": ...}. The native schema
    now states both supported representations. No-argument tools remain intact.
    Existing handlers, callbacks, response formats and call IDs remain unchanged.
    """
    validate_input_json_contract(contract)
    if contract == "string_v1":
        return tools
    schema = InputJsonObjectOrStringV1 if contract == _COMPAT else InputJsonObjectOrStringV2
    result = []
    for item in tools:
        properties = item.args
        if "input_json" not in properties:
            result.append(item)
            continue
        if set(properties) != {"input_json"}:
            raise ValueError("The input_json contract only supports one explicit input_json argument")
        if item.args_schema is schema:
            result.append(item)
            continue
        if properties["input_json"].get("type") != "string":
            raise ValueError("Unsupported pre-existing input_json schema")
        if contract == _COMPAT:
            # This branch remains byte-compatible with the frozen v1 wires.
            description = re.sub(r"JSON string", "JSON object (preferred) or legacy JSON-object string",
                                 item.description, flags=re.IGNORECASE)
            description += ("\nInput representation: input_json accepts a JSON object (preferred) "
                            "or a legacy string encoding that object. Parameter meaning and required "
                            "fields above are unchanged; no missing values are inferred.")
        else:
            # Only remove the fixed input preamble. Other mentions (including
            # an actual returned input_json string) describe backend semantics.
            description = item.description.replace("Input should be a JSON string with:", "Parameters:")
        metadata = {**(item.metadata or {}), "input_json_contract": contract}
        result.append(item.model_copy(update={"args_schema": schema,
                                              "description": description, "metadata": metadata}))
    return result


def tool_input_rules(contract: str) -> str:
    """Return only the representation rules for an explicitly selected contract."""
    validate_input_json_contract(contract)
    if contract == "string_v1":
        return ("- For tools with an `input_json` argument, pass a JSON string.\n"
                '- Example: {"input_json": "{\\\"drone_id\\\": \\\"abcdefgh\\\", \\\"altitude\\\": 15.0}"}')
    if contract == "json_object_or_string_v2":
        return ("- For input_json, pass a JSON object (preferred) or a legacy JSON-object string.\n"
                "- The value must encode one object. Nested JSON values are allowed; duplicate keys "
                "and nonfinite numbers are not. Keep required fields; never infer missing values.\n"
                '- Example: {"input_json": {"drone_id": "abcdefgh", "altitude": 15.0}}')
    return ("- For tools with an `input_json` argument, pass a JSON object (preferred). "
            "A legacy JSON-object string is also accepted; both have the same parameter meaning.\n"
            '- Example: {"input_json": {"drone_id": "abcdefgh", "altitude": 15.0}}\n'
            "- The input_json value must represent one object. Do not use arrays, scalars, "
            "duplicate keys, or nonfinite numbers. Keep the tool's required fields; do not invent missing required values.")
