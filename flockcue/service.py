"""FlockCue policy service for the original 500-second HTTP deployment runner."""
import argparse
import atexit
import hashlib
import json
import os
from pathlib import Path
from typing import ClassVar
from uuid import NAMESPACE_URL, uuid4, uuid5

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from flockcue.profile import PROFILE, environment, load_profile
from flockcue.provider import effective_tools, execution_history


class DeploymentDeepSeek(ChatOpenAI):
    """Keep the native client's settings and add the current tool and history ABI."""

    context_window: ClassVar = None

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(messages, stop=stop, **kwargs)
        if "tools" in payload:
            payload["tools"] = effective_tools(payload["tools"])
        for message, wire in zip(messages, payload["messages"], strict=True):
            if isinstance(message, AIMessage):
                reasoning = message.additional_kwargs.get("reasoning_content")
                if isinstance(reasoning, str):
                    wire["reasoning_content"] = reasoning
        return self.context_window.apply(payload) if self.context_window is not None else payload

    def _create_chat_result(self, response, generation_info=None):
        data = response if isinstance(response, dict) else response.model_dump(warnings=False)
        # The observer archives the HTTP body before this execution-only mapping.
        # Reuse the pilot's handling of empty sensing arguments and original IDs.
        if data.get("choices"):
            choices = [{**choice, "message": execution_history(choice["message"])}
                       for choice in data["choices"]]
            data = {**data, "choices": choices}
        result = super()._create_chat_result(data, generation_info)
        for generation, choice in zip(result.generations, data["choices"], strict=True):
            reasoning = choice["message"].get("reasoning_content")
            if isinstance(reasoning, str):
                generation.message.additional_kwargs["reasoning_content"] = reasoning
        return result


def student_window():
    from runtime.lifecycle import STUDENT_WINDOW
    window = STUDENT_WINDOW.get()
    if window is None:
        raise RuntimeError("Student request outside an admitted evaluation task")
    return window


def paired_command_id():
    task, _, _ = student_window()
    return uuid5(NAMESPACE_URL, "flockcue/paired-student/" + task)


class DeploymentStudent(DeploymentDeepSeek):
    """Use the same tool/history ABI and send task accounting only as transport headers."""

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        task, lease, cutoff = student_window()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        payload["temperature"] = 0.6
        payload["extra_headers"] = {
            **(payload.get("extra_headers") or {}),
            "X-Student-Task": task, "X-Student-Lease": lease,
            "X-Student-Cutoff": str(cutoff), "X-Student-Request": uuid4().hex,
        }
        return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--logs", required=True, type=Path)
    args = parser.parse_args()
    settings = json.loads(args.config.read_bytes())
    selected = settings["selected_provider"]
    provider = settings["provider_configs"][selected]
    student = selected == "flockcue_student" and provider["default_model"] in {
        "qwen35-2b-base", "qwen35-2b-sft",
    }
    if not student and (selected != "deepseek_official" or provider["default_model"] != "deepseek-flash"):
        raise ValueError("Unregistered FlockCue deployment model")
    if settings.get("share_blackboard_by_session") is not True or settings.get("toolchain_json_recording") is not True:
        raise ValueError("The original deployment requires shared session blackboards and toolchain recording")

    from flockcue import logging_config

    logs = args.logs.resolve()
    logs.mkdir(parents=True, exist_ok=True)
    logging_config.LOG_DIR = logs
    # The scheduler supplies the simulator endpoint; all policy switches come
    # from the registered profile, independent of the coordinator's shell.
    for key in list(os.environ):
        if key.startswith("UAV_") and key != "UAV_API_URL":
            del os.environ[key]
    profile = load_profile()
    os.environ.update(environment(profile), UAV_MODEL_IO_ARCHIVE_DIR=str(logs / "modelio"))

    from flockcue import api as agent_api_service
    from flockcue import agent as uav_agent
    import uvicorn
    from runtime.lifecycle import install
    from runtime.metrics import METRICS_FILENAME, install_agent_metrics

    uav_agent.ChatOpenAI = DeploymentStudent if student else DeploymentDeepSeek
    if settings.get("context_policy") == "qwen-complete-turns-30720":
        from flockcue.student_context import StudentContext
        context = StudentContext(settings.get("fallback_prompt_limit"), compact=settings.get("compact_receipts", False))
        uav_agent.ChatOpenAI.context_window = context
        atexit.register(context.close)
    agent_api_service.AGENT_CONFIG_FILE = str(args.config.resolve())
    initialize = agent_api_service.initialize_agent_from_settings

    def initialize_registered_agent():
        agent = initialize()
        if student:
            # The command ID appears in the recovery prompt. Transport leases and jobs
            # remain independent, while the two policies see the same public ID.
            agent.command_id_factory = paired_command_id
        llm = agent.llm
        effective = {
            "profile": profile["profile_id"],
            "profile_sha256": hashlib.sha256(PROFILE.read_bytes()).hexdigest(),
            "task_progress_mode": agent.task_progress_mode, "layout": agent.input_layout,
            "tool_input_contract": agent.tool_input_contract, "completion_contract": agent.completion_contract,
            "sense_interface": "C", "shared_blackboard": agent.share_blackboard_by_session,
            "model": llm.model_name, "temperature": 0.6 if student else llm.temperature,
            "model_max_output_tokens": llm.max_tokens, "sdk_max_retries": llm.root_client.max_retries,
            "max_iterations": agent.max_iterations, "recursion_limit": agent._recursion_limit_for_steps(),
            "empty_response_retries": agent.max_empty_response_retries,
        }
        (logs / "flockcue-effective.json").write_text(json.dumps(effective, indent=2) + "\n")
        return agent

    agent_api_service.initialize_agent_from_settings = initialize_registered_agent
    install(agent_api_service, os.environ["REPRO_CONTROL_TOKEN"])
    install_agent_metrics(agent_api_service, logs / METRICS_FILENAME, provider["base_url"])
    uvicorn.run(agent_api_service.app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
