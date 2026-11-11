"""DeepSeek API authentication + LangChain tool-message compatibility probe (two requests)."""
import json
import os
from pathlib import Path
import sys

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from openai import OpenAI

settings = json.loads(Path(sys.argv[1]).read_text())
provider = settings["provider_configs"][settings["selected_provider"]]
key = os.environ["DEEPSEEK_API_KEY"]
report = {"ok": False, "configured_model": provider["default_model"], "base_url": provider["base_url"]}
try:
    client = OpenAI(base_url=provider["base_url"], api_key=key, timeout=30, max_retries=0)
    try:
        models = [m.id for m in client.models.list().data]
        report["configured_model_listed"] = provider["default_model"] in models
        report["flash_model_ids"] = [m for m in models if "flash" in m.lower() or "deepseek" in m.lower()]
    except Exception as e:
        # Some gateways expose completions but no /models. A real roundtrip is authoritative.
        report["models_endpoint_error"] = type(e).__name__
    llm = ChatOpenAI(model=provider["default_model"], base_url=provider["base_url"], api_key=key,
                     temperature=0.1, timeout=75, max_retries=0)
    tool = {"type": "function", "function": {"name": "echo_probe", "description": "Echo a harmless readiness token.",
            "parameters": {"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]}}}
    messages = [HumanMessage(content="Call echo_probe with token multiuav_ready. After receiving the tool result, reply READY.")]
    first = llm.bind_tools([tool]).invoke(messages)
    report["first_response_metadata"] = first.response_metadata
    report["first_usage"] = first.usage_metadata
    if len(first.tool_calls) != 1 or first.tool_calls[0]["name"] != "echo_probe" or first.tool_calls[0]["args"] != {"token": "multiuav_ready"}:
        raise ValueError("Model did not produce the expected structured tool call")
    call = first.tool_calls[0]
    final = llm.bind_tools([tool]).invoke(messages + [first, ToolMessage(content='{"token":"multiuav_ready"}', tool_call_id=call["id"])])
    report["final_response_metadata"] = final.response_metadata
    report["final_usage"] = final.usage_metadata
    report["final_text"] = final.content
    if not final.content or final.tool_calls:
        raise ValueError("Tool response did not produce a terminal text answer")
    report["ok"] = True
except Exception as e:
    # No request headers or exception strings: providers can echo authentication material.
    report["error_type"] = type(e).__name__
    report["http_status"] = getattr(e, "status_code", None)
print(json.dumps(report, ensure_ascii=False, default=str))
sys.exit(0 if report["ok"] else 2)
