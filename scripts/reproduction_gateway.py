"""Selected backend authentication and actual LangChain tool-message roundtrip."""
import json
import os
from pathlib import Path
import sys

from langchain_core.messages import HumanMessage, ToolMessage
from openai import OpenAI
from reproduction_models import adapter_id, model_class

root = Path(__file__).resolve().parents[1]
settings = json.loads((root / "agent4drone/llm_settings.json").read_text())
provider = settings["provider_configs"][settings["selected_provider"]]
key = os.environ["REPRO_GATEWAY_API_KEY"]
report = {"ok": False, "provider": settings["selected_provider"],
          "adapter": adapter_id(settings["selected_provider"]),
          "configured_model": provider["default_model"], "base_url": provider["base_url"],
          "request_parameters": {"temperature": 0.1, "stream": False,
                                 "reasoning_effort": "not sent; provider default",
                                 "enable_thinking": "not sent; provider default"}}
try:
    client = OpenAI(base_url=provider["base_url"], api_key=key, timeout=30, max_retries=0)
    try:
        models = [m.id for m in client.models.list().data]
        report["configured_model_listed"] = provider["default_model"] in models
        report["flash_model_ids"] = [m for m in models if "flash" in m.lower() or "deepseek" in m.lower()]
    except Exception as e:
        # Some gateways expose completions but no /models. A real roundtrip is authoritative.
        report["models_endpoint_error"] = type(e).__name__
    llm = model_class(settings["selected_provider"])(model=provider["default_model"], base_url=provider["base_url"], api_key=key,
                     temperature=0.1, timeout=75, max_retries=0)
    tool = {"type": "function", "function": {"name": "echo_probe", "description": "Echo a harmless readiness token.",
            "parameters": {"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]}}}
    messages = [HumanMessage(content="Call echo_probe with token multiuav_ready. After receiving the tool result, reply READY.")]
    first = llm.bind_tools([tool]).invoke(messages)
    report["first_response_metadata"] = first.response_metadata
    report["first_usage"] = first.usage_metadata
    report["reasoning_content_received"] = bool(first.additional_kwargs.get("reasoning_content"))
    if len(first.tool_calls) != 1 or first.tool_calls[0]["name"] != "echo_probe" or first.tool_calls[0]["args"] != {"token": "multiuav_ready"}:
        raise ValueError("Model did not produce the expected structured tool call")
    call = first.tool_calls[0]
    history = messages + [first, ToolMessage(content='{"token":"multiuav_ready"}', tool_call_id=call["id"])]
    wire = llm._get_request_payload(history)["messages"]
    report["reasoning_content_roundtripped"] = (
        report["reasoning_content_received"] and
        wire[1].get("reasoning_content") == first.additional_kwargs["reasoning_content"])
    if settings["selected_provider"] == "bailian_qwen_flash" and not report["reasoning_content_roundtripped"]:
        raise ValueError("Qwen reasoning history was not preserved")
    final = llm.bind_tools([tool]).invoke(history)
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
