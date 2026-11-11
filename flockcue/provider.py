"""The public sensing contract and execution-only normalization used by the teacher."""
from copy import deepcopy
from hashlib import sha256
import re

SENSE = "sense_nearby_entities"
EMPTY_JSON_OBJECT = re.compile(r"[ \t\n\r]*\{[ \t\n\r]*\}[ \t\n\r]*")

DEFAULT_EXAMPLE = (
    "Omitting input_json senses all listed drones with the default settings.\n"
    '        Default (all listed drones): {} or {"input_json": {}}\n'
    '        Selected drones (use IDs from the fleet reply): {"input_json": '
    '{"drone_ids": ["drone-001", "drone-002"], "detail": "summary"}}'
)


def effective_tools(tools):
    """Publish the original optional empty envelope in the DeepSeek wire schema."""
    if not any(t["function"]["name"] == SENSE for t in tools):
        return tools
    adapted = deepcopy(tools)
    function, = (t["function"] for t in adapted if t["function"]["name"] == SENSE)
    parameters = function["parameters"]
    description = function["description"]
    if (parameters.get("required") != ["input_json"] or set(parameters["properties"]) != {"input_json"}
            or sha256(description.encode()).hexdigest() != "0b8598b6208e83141241204931ea897edc361a8cbbc58f835e14a6595a43394d"):
        raise ValueError("Unexpected sense tool contract")
    function["description"] = description[:description.rindex("Example:")] + DEFAULT_EXAMPLE
    del parameters["required"]
    return adapted


def execution_history(message):
    """Return the executable assistant message, preserving raw bytes in the provider archive."""
    calls = message.get("tool_calls") or []
    adapted = []
    for call in calls:
        function = call["function"]
        arguments = function["arguments"]
        if function["name"] == SENSE and isinstance(arguments, str) and EMPTY_JSON_OBJECT.fullmatch(arguments):
            call = {**call, "function": {**function, "arguments": '{"input_json":{}}'}}
        adapted.append(call)
    return {**message, "tool_calls": adapted} if adapted != calls else message
