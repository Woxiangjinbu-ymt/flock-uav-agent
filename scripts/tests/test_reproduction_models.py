"""Provider wire compatibility, with mocked SDK transport and no paid calls."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@unittest.skipUnless(importlib.util.find_spec("langchain_openai"), "Use agent4drone/.venv")
class QwenAdapterTests(unittest.TestCase):
    def test_sdk_roundtrip_preserves_all_reasoning_tool_ids_and_usage(self):
        import httpx2
        from langchain_core.messages import HumanMessage, ToolMessage
        from reproduction_models import QwenChatOpenAI
        llm = QwenChatOpenAI(model="qwen3.8-flash", api_key="offline-test-key",
                            base_url="https://model.invalid/v1", temperature=.1, max_retries=0)
        requests = []

        def transport(request):
            requests.append(json.loads(request.content))
            index = len(requests)
            message = {"role": "assistant", "content": None, "reasoning_content": f"reasoning-{index}"}
            if index < 3:
                message["tool_calls"] = [{"id": f"call-{index}", "type": "function",
                                          "function": {"name": "echo", "arguments": '{"value":1}'}}]
            else:
                message["content"] = "DONE"
            return httpx2.Response(200, json={"id": f"response-{index}", "object": "chat.completion",
                "created": 0, "model": "qwen3.8-flash", "choices": [{"index": 0, "message": message,
                "finish_reason": "tool_calls" if index < 3 else "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}})

        tools = [{"type": "function", "function": {"name": "echo", "description": "test",
                  "parameters": {"type": "object", "properties": {"value": {"type": "integer"}}}}}]
        messages = [HumanMessage("test")]
        with patch.object(httpx2.HTTPTransport, "handle_request", side_effect=transport):
            for index in range(1, 4):
                answer = llm.bind_tools(tools).invoke(messages)
                self.assertEqual(answer.additional_kwargs["reasoning_content"], f"reasoning-{index}")
                self.assertEqual(answer.usage_metadata["total_tokens"], 13)
                messages.append(answer)
                if answer.tool_calls:
                    self.assertEqual(answer.tool_calls[0]["id"], f"call-{index}")
                    messages.append(ToolMessage(content="1", tool_call_id=f"call-{index}"))
        history = [m for m in requests[-1]["messages"] if m["role"] == "assistant"]
        self.assertEqual([m["reasoning_content"] for m in history], ["reasoning-1", "reasoning-2"])
        self.assertTrue(all(m["content"] in (None, "") for m in history))
        self.assertEqual(requests[-1]["temperature"], .1)
        self.assertNotIn("enable_thinking", requests[-1])
        self.assertEqual(answer.content, "DONE")

    def test_deepseek_keeps_upstream_class(self):
        from langchain_openai import ChatOpenAI
        from reproduction_models import model_class
        self.assertIs(model_class("deepseek_official"), ChatOpenAI)

    def test_unsupported_streaming_fails_instead_of_dropping_reasoning(self):
        from reproduction_models import QwenChatOpenAI
        llm = QwenChatOpenAI(model="qwen3.8-flash", api_key="offline-test-key")
        with self.assertRaisesRegex(ValueError, "nonstreaming"):
            llm._get_request_payload("test", stream=True)


if __name__ == "__main__":
    unittest.main()
