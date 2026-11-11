"""Map only C's optional empty sensing envelope at the provider-to-Agent boundary."""
import re

from thinking_events.sense_default import SENSE

EMPTY_JSON_OBJECT = re.compile(r"[ \t\n\r]*\{[ \t\n\r]*\}[ \t\n\r]*")


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
