"""FlockCue policy service for the original 500-second HTTP deployment runner."""
import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import argparse
import hashlib
import json
import os

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from flockcue_session_cpu.teacher_sense import execution_history
from thinking_events.sense_default import effective_tools


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())
PROFILE = ROOT / "experiments/hover-compact-contract-v2/profile.json"


class DeploymentDeepSeek(ChatOpenAI):
    """Keep the native client's settings and add the current tool and history ABI."""

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
        return payload

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--logs", required=True, type=Path)
    args = parser.parse_args()
    settings = json.loads(args.config.read_bytes())
    selected = settings["selected_provider"]
    provider = settings["provider_configs"][selected]
    if selected != "deepseek_official" or provider["default_model"] != "deepseek-flash":
        raise ValueError("The registered deployment requires deepseek_official / deepseek-flash")
    if settings.get("share_blackboard_by_session") is not True or settings.get("toolchain_json_recording") is not True:
        raise ValueError("The original deployment requires shared session blackboards and toolchain recording")

    sys.path.insert(0, str(ROOT / "agent4drone"))
    import logging_config
    from agent_profile import AgentProfile

    logs = args.logs.resolve()
    logs.mkdir(parents=True, exist_ok=True)
    logging_config.LOG_DIR = logs
    # The scheduler supplies the simulator endpoint; all policy switches come
    # from the registered profile, independent of the coordinator's shell.
    for key in list(os.environ):
        if key.startswith("UAV_") and key != "UAV_API_URL":
            del os.environ[key]
    profile = AgentProfile.load(PROFILE)
    os.environ.update(profile.environment(), UAV_MODEL_IO_ARCHIVE_DIR=str(logs / "modelio"))

    import agent_api_service
    import uav_agent
    import uvicorn
    from reproduction_lifecycle import install
    from reproduction_metrics import METRICS_FILENAME, install_agent_metrics

    uav_agent.ChatOpenAI = DeploymentDeepSeek
    agent_api_service.AGENT_CONFIG_FILE = str(args.config.resolve())
    initialize = agent_api_service.initialize_agent_from_settings

    def initialize_registered_agent():
        agent = initialize()
        llm = agent.llm
        effective = {
            "profile": profile.to_dict()["profile_id"],
            "profile_sha256": hashlib.sha256(PROFILE.read_bytes()).hexdigest(),
            "task_progress_mode": agent.task_progress_mode, "layout": agent.input_layout,
            "tool_input_contract": agent.tool_input_contract, "completion_contract": agent.completion_contract,
            "sense_interface": "C", "shared_blackboard": agent.share_blackboard_by_session,
            "model": llm.model_name, "temperature": llm.temperature,
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
