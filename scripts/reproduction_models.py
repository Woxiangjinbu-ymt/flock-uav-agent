"""Provider wire compatibility without changing Agent4Drone's tools or prompts."""
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

QWEN_ADAPTER_ID = "qwen-reasoning-content-v1"


class QwenChatOpenAI(ChatOpenAI):
    """Preserve Qwen's nonstandard reasoning field across nonstreaming tool turns.

    ChatOpenAI intentionally discards this field; Qwen 3.8 expects it back in
    assistant history. Keep it separate from visible content, never concatenate.
    The runner uses invoke(), not streaming or the Responses API.
    """
    use_responses_api: bool | None = False

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        body = response if isinstance(response, dict) else response.model_dump()
        for generation, choice in zip(result.generations, body["choices"], strict=True):
            reasoning = choice["message"].get("reasoning_content")
            if isinstance(reasoning, str):
                generation.message.additional_kwargs["reasoning_content"] = reasoning
        return result

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(messages, stop=stop, **kwargs)
        if payload.get("stream") or "messages" not in payload:
            raise ValueError("Qwen reproduction adapter requires nonstreaming Chat Completions")
        for message, wire in zip(messages, payload["messages"], strict=True):
            if isinstance(message, AIMessage):
                reasoning = message.additional_kwargs.get("reasoning_content")
                if isinstance(reasoning, str):
                    wire["reasoning_content"] = reasoning
        return payload


def model_class(selected_provider):
    return QwenChatOpenAI if selected_provider == "bailian_qwen_flash" else ChatOpenAI


def adapter_id(selected_provider):
    return QWEN_ADAPTER_ID if selected_provider == "bailian_qwen_flash" else "upstream-chatopenai"
