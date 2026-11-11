"""Expose the existing all-drones sense default in the Qwen retest."""
from copy import deepcopy

from thinking_events.sense_example_prepare import EXAMPLE, SENSE, sense_function, variant


DEFAULT_EXAMPLE = (
    "Omitting input_json senses all listed drones with the default settings.\n"
    '        Default (all listed drones): {} or {"input_json": {}}\n'
    '        Selected drones (use IDs from the fleet reply): {"input_json": '
    '{"drone_ids": ["drone-001", "drone-002"], "detail": "summary"}}'
)


def effective_tools(old_tools):
    """Apply the registered B description, then publish its optional envelope."""
    if not any(t["function"]["name"] == SENSE for t in old_tools):
        return old_tools
    body = variant({"tools": old_tools})
    function = sense_function(body)
    parameters = function["parameters"]
    if (parameters.get("required") != ["input_json"] or set(parameters["properties"]) != {"input_json"}
            or not function["description"].endswith(EXAMPLE)):
        raise ValueError("Unexpected registered sense contract")
    function["description"] = function["description"].removesuffix(EXAMPLE) + DEFAULT_EXAMPLE
    del parameters["required"]
    return body["tools"]


def execution_message(parsed_msg):
    """Translate a successfully parsed empty sense call to the existing harness ABI."""
    if parsed_msg is None:
        return None
    message = deepcopy(parsed_msg)
    for call in message.get("tool_calls", []):
        function = call["function"]
        if function["name"] == SENSE and function["arguments"] == "{}":
            function["arguments"] = '{"input_json":{}}'
    return message
