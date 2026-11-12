"""
UAV Control Agent using modern LangChain agents.

This module preserves the public behavior of the legacy UAV agent while
switching the internal agent runtime away from langchain_classic.
"""
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai.chat_models import base as openai_chat_base
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from flockcue.tools.blackboard import PerceptionBlackboard
from flockcue.tools.client import UAVAPIClient
from flockcue.tools.catalog import create_uav_tools
from flockcue.logging_config import get_logger
from flockcue.prompt import build_system_prompt, validate_input_layout


logger = get_logger(__name__)
_EPISODE_GATE_INITIALIZATION = threading.Lock()


class AgentBusyError(RuntimeError):
    """An operation was refused before touching an active agent command."""

    code = "agent_busy"


def patch_openai_compatible_reasoning_content() -> None:
    """Preserve DeepSeek thinking-mode reasoning_content across tool calls."""
    if getattr(openai_chat_base, "_uav_reasoning_content_patched", False):
        return

    original_to_message = openai_chat_base._convert_dict_to_message
    original_to_dict = openai_chat_base._convert_message_to_dict

    def convert_dict_to_message_with_reasoning(message_dict: Any) -> Any:
        message = original_to_message(message_dict)
        if (
            isinstance(message, AIMessage)
            and isinstance(message_dict, dict)
            and "reasoning_content" in message_dict
        ):
            message.additional_kwargs["reasoning_content"] = message_dict["reasoning_content"]
        return message

    def convert_message_to_dict_with_reasoning(message: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        message_dict = original_to_dict(message, *args, **kwargs)
        if isinstance(message, AIMessage):
            reasoning_content = message.additional_kwargs.get("reasoning_content")
            if reasoning_content is not None:
                message_dict["reasoning_content"] = reasoning_content
        return message_dict

    openai_chat_base._convert_dict_to_message = convert_dict_to_message_with_reasoning
    openai_chat_base._convert_message_to_dict = convert_message_to_dict_with_reasoning
    openai_chat_base._uav_reasoning_content_patched = True


patch_openai_compatible_reasoning_content()


def sanitize_for_log(value: Any, max_length: int = 1000) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = " ".join(text.split())
    if len(text) > max_length:
        return f"{text[:max_length]}..."
    return text


def empty_token_usage() -> Dict[str, int]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "llm_calls": 0,
    }


def int_token(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def token_usage_from_dict(usage: Dict[str, Any]) -> Dict[str, int]:
    prompt_tokens = int_token(usage.get("input_tokens", usage.get("prompt_tokens")))
    completion_tokens = int_token(usage.get("output_tokens", usage.get("completion_tokens")))
    total_tokens = int_token(usage.get("total_tokens"))
    if not total_tokens:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "llm_calls": 1,
    }


def token_usage_from_message(message: Any) -> Optional[Dict[str, int]]:
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict):
        return token_usage_from_dict(usage)

    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(response_metadata, dict):
        token_usage = response_metadata.get("token_usage")
        if isinstance(token_usage, dict):
            return token_usage_from_dict(token_usage)

    return None


def merge_token_usage(totals: Dict[str, int], usage: Dict[str, int]) -> None:
    totals["prompt_tokens"] += usage["prompt_tokens"]
    totals["completion_tokens"] += usage["completion_tokens"]
    totals["total_tokens"] += usage["total_tokens"]
    totals["llm_calls"] += usage["llm_calls"]


def format_token_usage_for_log(token_usage: Dict[str, Any]) -> str:
    return (
        f"prompt_tokens={int_token(token_usage.get('prompt_tokens'))} "
        f"completion_tokens={int_token(token_usage.get('completion_tokens'))} "
        f"total_tokens={int_token(token_usage.get('total_tokens'))} "
        f"llm_calls={int_token(token_usage.get('llm_calls'))}"
    )


def token_usage_from_llm_result(response: Any) -> Dict[str, int]:
    totals = empty_token_usage()

    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, dict):
        for key in ("token_usage", "usage"):
            usage = llm_output.get(key)
            if isinstance(usage, dict):
                merge_token_usage(totals, token_usage_from_dict(usage))
        if totals["llm_calls"]:
            return totals

    for generation_group in getattr(response, "generations", []) or []:
        for generation in generation_group or []:
            message = getattr(generation, "message", None)
            if message is None:
                continue
            usage = token_usage_from_message(message)
            if usage:
                merge_token_usage(totals, usage)

    return totals


class ProviderTokenLoggingCallback(BaseCallbackHandler):
    def __init__(self, command_id: str, provider: str, model: str):
        self.command_id = command_id
        self.provider = provider
        self.model = model

    def on_llm_end(self, response: Any, **kwargs: Any) -> Any:
        token_usage = token_usage_from_llm_result(response)
        logger.info(
            "Provider LLM call completed command_id=%s provider=%s model=%s %s",
            self.command_id,
            self.provider,
            self.model,
            format_token_usage_for_log(token_usage),
        )


def load_llm_settings(settings_path: str = "llm_settings.json") -> Optional[Dict[str, Any]]:
    """Load LLM settings from JSON file."""
    try:
        path = Path(settings_path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load LLM settings from {settings_path}: {e}")
    return None


def bool_setting(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


class UAVControlAgent:
    """Intelligent agent for controlling UAVs using natural language."""

    command_id_factory = staticmethod(uuid.uuid4)

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        uav_api_key: Optional[str] = None,
        llm_provider: str = "ollama",
        llm_model: str = "llama2",
        llm_api_key: Optional[str] = None,
        llm_base_url: Optional[str] = None,
        temperature: float = 0.1,
        verbose: bool = True,
        debug: bool = False,
        share_blackboard_by_session: bool = False,
        toolchain_json_recording: bool = False,
        task_progress_mode: Optional[str] = None,
        model_io_archive: Optional[bool] = None,
        model_io_archive_dir: Optional[str] = None,
        input_layout: Optional[str] = None,
        completion_contract: Optional[str] = None,
        tool_input_contract: Optional[str] = None,
    ):
        from flockcue.harness.hover import validate_completion_contract
        from flockcue.tools.inputs import validate_input_json_contract
        self.completion_contract = validate_completion_contract(
            os.environ.get("UAV_COMPLETION_CONTRACT", "off") if completion_contract is None else completion_contract
        )
        self.tool_input_contract = validate_input_json_contract(
            os.environ.get("UAV_TOOL_INPUT_CONTRACT", "string_v1") if tool_input_contract is None else tool_input_contract
        )
        self.input_layout = validate_input_layout(
            os.environ.get("UAV_AGENT_INPUT_LAYOUT", "full_v1") if input_layout is None else input_layout
        )
        mode = task_progress_mode if task_progress_mode is not None else os.environ.get("UAV_TASK_PROGRESS_MODE", "evidence_harness")
        self.task_progress_mode = str(mode).strip().lower()
        if self.task_progress_mode != "evidence_harness":
            raise ValueError("FlockCue requires the evidence_harness task progress mode")
        self._task_progress = None
        self._task_progress_middleware = None
        self._episode_lock = threading.Lock()
        self._model_io_archive_enabled = (os.environ.get("UAV_MODEL_IO_ARCHIVE") == "1"
                                          if model_io_archive is None else bool(model_io_archive))
        self._model_io_archive_dir = model_io_archive_dir or os.environ.get("UAV_MODEL_IO_ARCHIVE_DIR")
        self._model_io_archive = None
        self._model_io_archive_error = None
        self._model_request_audit_enabled = (self.task_progress_mode.startswith("evidence")
                                             or os.environ.get("UAV_MODEL_REQUEST_AUDIT") == "1"
                                             or self._model_io_archive_enabled)
        self._model_wire_requests = []
        self._model_wire_errors = []
        self.client = UAVAPIClient(base_url, api_key=uav_api_key)
        self.verbose = verbose
        self.debug = debug
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.max_iterations = 150
        self.max_empty_response_retries = 2
        self.share_blackboard_by_session = share_blackboard_by_session
        self.toolchain_json_recording = toolchain_json_recording
        self._session_blackboards: Dict[str, PerceptionBlackboard] = {}
        logger.info(
            "Initializing UAVControlAgent provider=%s model=%s base_url=%s",
            llm_provider,
            llm_model,
            base_url,
        )

        if self.debug:
            print("\n" + "=" * 60)
            print("🔧 UAV Agent Initialization - Debug Mode")
            print("=" * 60)
            print(f"UAV API Server: {base_url}")
            print(f"LLM Provider: {llm_provider}")
            print(f"LLM Model: {llm_model}")
            print(f"Temperature: {temperature}")
            print(f"Verbose: {verbose}")
            print()

        if self.debug:
            print("🔌 Testing UAV API connection...")
        session = None
        try:
            session = self.client.get_current_session()
            logger.info(
                "Connected to UAV API session_id=%s task=%s",
                session.get("id"),
                session.get("task"),
            )
            if self.debug:
                print("✅ Connected to UAV API")
                print(f"   Session: {session.get('name', 'Unknown')}")
                print(f"   Task: {session.get('task', 'Unknown')}")
                print()
        except Exception as e:
            logger.warning("Could not connect to UAV API at %s: %s", base_url, e)
            if self.debug:
                print(f"⚠️  Warning: Could not connect to UAV API: {e}")
                print(f"   Make sure the UAV server is running at {base_url}")
                print()

        self.llm = self._create_llm(
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            temperature=temperature,
        )

        if self.debug:
            print("🔧 Creating UAV control tools...")
        initial_session_id = session.get("id") if isinstance(session, dict) else None
        self.blackboard = PerceptionBlackboard(session_id=initial_session_id)
        self.tools = self._create_tools(self.blackboard)
        logger.info("Created %d UAV control tools", len(self.tools))
        if self.debug:
            print(f"✅ Created {len(self.tools)} tools")
            print(f"   Tools: {', '.join([tool.name for tool in self.tools[:5]])}...")
            print()

        if self.debug:
            print("🤖 Creating modern agent runtime...")
        self.agent = self._create_agent_runtime()
        logger.info("Agent runtime created")
        if self.debug:
            print("✅ Agent runtime created")
            print()

        if self.debug:
            print("🔄 Refreshing session context...")
        self.session_context: Dict[str, Any] = {}
        self.refresh_session_context()

        if self.debug:
            print("=" * 60)
            print("✅ UAV Agent Initialization Complete!")
            print("=" * 60)
            print()

    def _create_llm(
        self,
        llm_provider: str,
        llm_model: str,
        llm_api_key: Optional[str],
        llm_base_url: Optional[str],
        temperature: float,
    ) -> Any:
        if self.debug:
            print(f"🤖 Initializing LLM provider: {llm_provider}")

        if llm_provider == "ollama":
            if self.debug:
                print(f"   Using Ollama with model: {llm_model}")
                print("   Ollama URL: http://localhost:11434 (default)")
            llm = ChatOllama(model=llm_model, temperature=temperature)
            if self.debug:
                print("✅ Ollama LLM initialized")
                print()
            return llm

        if llm_provider not in ["openai", "openai-compatible"]:
            raise ValueError(
                f"Unknown LLM provider: {llm_provider}. "
                "Use 'ollama', 'openai', or 'openai-compatible'"
            )

        if not llm_api_key:
            raise ValueError(
                f"API key is required for {llm_provider} provider. "
                "Use --llm-api-key or set environment variable."
            )

        if llm_provider == "openai":
            final_base_url = llm_base_url or "https://api.openai.com/v1"
            provider_name = "OpenAI"
        else:
            if not llm_base_url:
                raise ValueError("llm_base_url is required for openai-compatible provider")
            final_base_url = llm_base_url
            provider_name = "OpenAI-Compatible API"

        if self.debug:
            masked = "*" * (len(llm_api_key) - 4) + llm_api_key[-4:] if len(llm_api_key) > 4 else "****"
            print(f"   Provider: {provider_name}")
            print(f"   Base URL: {final_base_url}")
            print(f"   Model: {llm_model}")
            print(f"   API Key: {masked}")

        llm = ChatOpenAI(
            model=llm_model,
            temperature=temperature,
            api_key=llm_api_key,
            base_url=final_base_url,
        )
        if getattr(self, "_model_request_audit_enabled", False):
            try:
                from flockcue.recording.request import attach_request_hooks
                if getattr(self, "_model_io_archive_enabled", False) or self.task_progress_mode == "evidence_harness":
                    attach_request_hooks(llm, self._audit_model_request, self._aaudit_model_request,
                                         self._audit_model_response, self._aaudit_model_response)
                else:
                    attach_request_hooks(llm, self._audit_model_request, self._aaudit_model_request)
            except Exception as exc:
                self._model_wire_errors.append("setup:" + type(exc).__name__)
        if self.debug:
            print(f"✅ {provider_name} LLM initialized")
            print()
        return llm

    def _audit_model_request(self, request) -> None:
        if getattr(self, "_model_io_archive_enabled", False):
            try:
                from flockcue.recording.wire import archive_request, current_archive
                owner = current_archive()
                # Never attribute a detached/late request to whichever command
                # is current now. The direct observation worker is explicitly
                # outside this archive, but retains its existing hash audit.
                if getattr(self, "_in_observation_worker", False):
                    # A nested bare llm.invoke inherits its parent's ContextVar
                    # but is not that graph call. Keep only its old hash audit.
                    pass
                elif owner is None:
                    # This also quarantines any previously bound Request
                    # reused outside a logical model call.
                    archive_request(request)
                    return
                elif owner is not getattr(self, "_model_io_archive", None) or owner.closed:
                    return
                else:
                    if archive_request(request) is None:
                        return
            except Exception as exc:
                self._note_model_io_archive_error(exc)
        try:
            from flockcue.recording.request import request_record
            record = request_record(request)
            role = "observation_worker" if getattr(self, "_in_observation_worker", False) else "main"
            record["agent_role"] = role
            previous = next((item for item in reversed(self._model_wire_requests)
                             if item.get("agent_role", "main") == role), None)
            common = 0
            if previous:
                for a, b in zip(previous["message_sha256"], record["message_sha256"]):
                    if a != b:
                        break
                    common += 1
            record["common_prefix_messages"] = common
            record["previous_message_count"] = len(previous["message_sha256"]) if previous else 0
            record["request_index"] = len(self._model_wire_requests)
            ledger = getattr(self, "_task_progress", None)
            model_calls = getattr(ledger, "model_requests", [])
            record["middleware_request_index"] = len(model_calls) - 1 if model_calls else None
            harness = getattr(self, "_harness", None)
            if harness is not None:
                harness.wire_start(request, record)
                request.extensions["uav_harness_owner"] = harness
            self._model_wire_requests.append(record)
        except Exception as exc:
            self._model_wire_errors.append(type(exc).__name__)

    async def _aaudit_model_request(self, request) -> None:
        self._audit_model_request(request)

    def _audit_model_response(self, response) -> None:
        if getattr(self, "_model_io_archive_enabled", False):
            try:
                from flockcue.recording.wire import archive_response, request_is_ambiguous
                archive_response(response)
                if request_is_ambiguous(response.request):
                    return
            except Exception as exc:
                self._note_model_io_archive_error(exc)
        try:
            harness = response.request.extensions.get("uav_harness_owner")
            if harness is not None and not getattr(harness, "_closed", False):
                harness.wire_response(response)
        except Exception as exc:
            self._model_wire_errors.append(type(exc).__name__)

    async def _aaudit_model_response(self, response) -> None:
        self._audit_model_response(response)

    def _create_tools(self, blackboard: Optional[PerceptionBlackboard] = None) -> List[Any]:
        return create_uav_tools(self.client, blackboard=blackboard)

    def _current_session_id(self) -> Optional[str]:
        try:
            session = self.client.get_current_session()
        except Exception as e:
            logger.warning("Could not determine current session for blackboard scope: %s", e)
            return None
        if not isinstance(session, dict):
            return None
        session_id = session.get("id")
        if session_id and getattr(self, "task_progress_mode", "off").startswith("evidence"):
            # Use metadata from the already-required request, never another API
            # call or global entity snapshot. These signals detect visible
            # resets; callers must explicitly reset memory for same-ID reloads
            # that preserve every marker (the server exposes no reset epoch).
            markers = getattr(self, "_evidence_session_markers", {})
            stats = session.get("statistics")
            stats = stats if isinstance(stats, dict) else {}
            marker = (session.get("created_at"), stats.get("environment_id"), stats.get("total_commands_executed"))
            previous = markers.get(str(session_id))
            decreased = bool(previous and isinstance(previous[2], (int, float)) and
                             isinstance(marker[2], (int, float)) and marker[2] < previous[2])
            if previous and (marker[:2] != previous[:2] or decreased):
                # Command preparation already owns the episode gate. This
                # internal observed-reset path must not reacquire that gate.
                self._clear_task_evidence(str(session_id))
            markers[str(session_id)] = marker
            self._evidence_session_markers = markers
        return str(session_id) if session_id else None

    def reset_task_evidence(self, session_id: Optional[str] = None) -> None:
        """Clear idle-agent memory after world reset; never reset the world.

        A cancellation request alone does not make the agent idle. The caller
        must wait for execute() to return before restoring a shared world and
        clearing evidence. Busy resets have no memory/client side effects.
        """
        with self._exclusive_episode("reset_task_evidence"):
            self._clear_task_evidence(session_id)

    def _clear_task_evidence(self, session_id: Optional[str] = None) -> None:
        boards = getattr(self, "_session_blackboards", {})
        for sid, board in list(boards.items()):
            if session_id is None or sid == session_id:
                board.clear()
                del boards[sid]
        current = getattr(self, "blackboard", None)
        if current is not None and (session_id is None or current.session_id == session_id):
            current.clear()

    def _blackboard_for_current_command(self) -> PerceptionBlackboard:
        session_id = self._current_session_id()
        if not self.share_blackboard_by_session or not session_id:
            return PerceptionBlackboard(session_id=session_id)
        blackboard = self._session_blackboards.get(session_id)
        if blackboard is None:
            blackboard = PerceptionBlackboard(session_id=session_id)
            self._session_blackboards[session_id] = blackboard
        return blackboard

    def _build_system_prompt(self) -> str:
        prompt = build_system_prompt(self.tools, input_layout=getattr(self, "input_layout", "full_v1"),
                                    tool_input_contract=getattr(self, "tool_input_contract", "string_v1"))
        if getattr(self, "_harness", None) is not None:
            from flockcue.harness.integration import HARNESS_RULES
            prompt += "\n\n" + HARNESS_RULES
        if getattr(self, "completion_contract", "off") in {"hover_completion_v1", "hover_completion_v2"}:
            from flockcue.harness.hover import completion_rules
            rules = completion_rules(getattr(self, "_completion_command", ""), contract=self.completion_contract)
            if rules:
                prompt = prompt.replace("1. Always end the final answer with [TASK DONE].",
                    "1. Use [TASK DONE] only when the scoped hover completion evidence is verified; "
                    "otherwise state that completion is unverified without this marker.", 1)
                prompt += "\n\n" + rules
        return prompt

    def _create_agent_runtime(self) -> Any:
        from flockcue.tools.inputs import adapt_input_json_tools
        self.tools = adapt_input_json_tools(self.tools, getattr(self, "tool_input_contract", "string_v1"))
        middleware = getattr(self, "_task_progress_middleware", None)
        middlewares = [middleware] if middleware is not None else []
        archive = getattr(self, "_model_io_archive", None)
        if archive is not None:
            try:
                from flockcue.recording.wire import ModelIOArchiveMiddleware
                # Outermost observes rejections as well as successful tool calls.
                # Its graph input is not the final provider input; HTTP bytes are.
                middlewares.insert(0, ModelIOArchiveMiddleware(archive))
            except Exception as exc:
                self._note_model_io_archive_error(exc)
        if middlewares:
            return create_agent(
                model=self.llm,
                tools=self.tools,
                system_prompt=self._build_system_prompt(),
                middleware=middlewares,
            )
        return create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=self._build_system_prompt(),
        )

    def _initialize_task_progress(self, command: str, command_id: str) -> None:
        """Start a command-scoped ledger backed by the session's shared public evidence."""
        from flockcue.evidence.contacts import SessionEvidenceIndexR2, TaskEvidenceLedgerR2, progress_tool
        from flockcue.harness.integration import install_harness

        inherited = self.blackboard.full() if self.share_blackboard_by_session else None
        index = getattr(self.blackboard, "task_evidence_index", None)
        if index is None:
            index = SessionEvidenceIndexR2(self.blackboard.session_id)
            self.blackboard.task_evidence_index = index
        if not isinstance(index, SessionEvidenceIndexR2):
            raise ValueError("Changing evidence type requires a fresh session blackboard")
        ledger = TaskEvidenceLedgerR2(command, command_id, index=index, aggregate_feedback=True,
                                      inherited_blackboard=inherited, max_summary_chars=3900)
        self._task_progress_middleware = install_harness(self, ledger)
        if ledger.aggregate:
            self.tools = [*self.tools, progress_tool(self.client, self.blackboard.session_id)]
        self._task_progress = ledger

    def _recursion_limit_for_steps(self) -> int:
        """Allow enough graph turns to complete the configured number of tool steps."""
        return max(25, self.max_iterations * 3)

    def refresh_session_context(self) -> None:
        """Refresh session context information."""
        with self._exclusive_episode("refresh_session_context"):
            self._refresh_session_context()

    def _refresh_session_context(self) -> None:
        try:
            session = self.client.get_current_session()
            self.session_context = {
                "session_id": session.get("id"),
                "task_type": session.get("task"),
                "task_description": session.get("task_description"),
                "status": session.get("status"),
            }
        except Exception as e:
            logger.warning("Could not refresh session context: %s", e)
            if self.verbose:
                print(f"Warning: Could not refresh session context: {e}")

    def get_session_summary(self) -> str:
        """Get a summary of the current session."""
        # This method shares the client recorder/deadline with execute(). Even
        # read-only requests must not enter another command's API archive.
        with self._exclusive_episode("get_session_summary"):
            return self._get_session_summary()

    def _get_session_summary(self) -> str:
        try:
            session = self.client.get_current_session()
            progress = self.client.get_task_progress()
            drones = self.client.list_drones()

            summary = f"""
=== Current Session Summary ===
Session: {session.get('name', 'Unknown')}
Task: {session.get('task', 'Unknown')} - {session.get('task_description', '')}
Status: {session.get('status', 'Unknown')}

Progress: {progress.get('progress_percentage', 0)}% ({progress.get('status_message', 'Unknown')})
Completed: {progress.get('is_completed', False)}

Drones: {len(drones)} available
"""
            for drone in drones:
                summary += (
                    f"  - {drone.get('name')} ({drone.get('id')}): {drone.get('status')}, "
                    f"Battery: {drone.get('battery_level', 0):.1f}%\n"
                )
            return summary.strip()
        except Exception as e:
            return f"Error getting session summary: {e}"

    def _normalize_tool_input(self, args: Any) -> Any:
        if isinstance(args, dict) and "input_json" in args and len(args) == 1:
            raw = args.get("input_json")
            if isinstance(raw, dict) and getattr(self, "tool_input_contract", "string_v1") in {"json_object_or_string_v1", "json_object_or_string_v2"}:
                # A derived step view only; raw model messages and wire remain intact.
                try:
                    return json.loads(json.dumps(raw, ensure_ascii=False, allow_nan=False))
                except (TypeError, ValueError):
                    return args  # Keep rejected arguments inspectable; do not execute or repair them here.
            if isinstance(raw, str):
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw
        return args

    def _message_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        parts.append(str(text))
            return "\n".join(part for part in parts if part).strip()
        if content is None:
            return ""
        return str(content)

    def _extract_intermediate_steps(self, messages: List[Any]) -> List[Any]:
        steps: List[Any] = []
        pending: Dict[str, Any] = {}

        for message in messages:
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                log_text = self._message_text(getattr(message, "content", ""))
                for tool_call in tool_calls:
                    tool_name = tool_call.get("name", "unknown_tool")
                    tool_input = self._normalize_tool_input(tool_call.get("args"))
                    action = SimpleNamespace(
                        tool=tool_name,
                        tool_input=tool_input,
                        log=log_text or f"Preparing to call tool '{tool_name}'",
                        message_log=None,
                    )
                    pending[tool_call.get("id", f"{tool_name}-{len(steps)}")] = action

            tool_call_id = getattr(message, "tool_call_id", None)
            if tool_call_id and tool_call_id in pending:
                action = pending.pop(tool_call_id)
                observation = self._message_text(getattr(message, "content", ""))
                steps.append((action, observation))

        for action in pending.values():
            steps.append((action, ""))

        return steps

    @staticmethod
    def _empty_token_usage() -> Dict[str, int]:
        return empty_token_usage()

    @staticmethod
    def _int_token(value: Any) -> int:
        return int_token(value)

    def _token_usage_from_message(self, message: Any) -> Optional[Dict[str, int]]:
        return token_usage_from_message(message)

    def _extract_token_usage(self, messages: List[Any]) -> Dict[str, int]:
        totals = self._empty_token_usage()
        for message in messages:
            usage = self._token_usage_from_message(message)
            if not usage:
                continue
            merge_token_usage(totals, usage)
        return totals

    def _extract_tool_names(self, steps: List[Any]) -> List[str]:
        tool_names: List[str] = []
        for step in steps:
            action = step[0] if isinstance(step, (list, tuple)) and step else step
            tool_name = getattr(action, "tool", None)
            if tool_name:
                tool_names.append(str(tool_name))
        return tool_names

    def _ingest_stream_messages(
        self,
        messages: List[Any],
        pending: Dict[str, Any],
        steps: List[Any],
    ) -> bool:
        updated = False

        for message in messages:
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                log_text = self._message_text(getattr(message, "content", ""))
                for tool_call in tool_calls:
                    tool_call_id = tool_call.get("id", f"{tool_call.get('name', 'tool')}-{len(steps)}")
                    if tool_call_id in pending:
                        continue
                    tool_name = tool_call.get("name", "unknown_tool")
                    tool_input = self._normalize_tool_input(tool_call.get("args"))
                    pending[tool_call_id] = SimpleNamespace(
                        tool=tool_name,
                        tool_input=tool_input,
                        log=log_text or f"Preparing to call tool '{tool_name}'",
                        message_log=None,
                    )
                    steps.append((pending[tool_call_id], ""))
                    updated = True

            tool_call_id = getattr(message, "tool_call_id", None)
            if tool_call_id and tool_call_id in pending:
                observation = self._message_text(getattr(message, "content", ""))
                for idx in range(len(steps) - 1, -1, -1):
                    action, existing_observation = steps[idx]
                    if action is pending[tool_call_id]:
                        if existing_observation != observation:
                            steps[idx] = (action, observation)
                            updated = True
                        break

        return updated

    def _stream_updates_to_steps(
        self,
        command: str,
        config: Dict[str, Any],
        step_callback: Optional[Any],
    ) -> Dict[str, Any]:
        pending: Dict[str, Any] = {}
        steps: List[Any] = []
        collected_messages: List[Any] = []
        seen_message_keys: set[str] = set()
        limit_reached = False

        def message_key(message: Any) -> str:
            message_id = getattr(message, "id", None)
            if message_id:
                return str(message_id)
            tool_call_id = getattr(message, "tool_call_id", None)
            content = self._message_text(getattr(message, "content", ""))
            tool_calls = getattr(message, "tool_calls", None) or []
            return f"{message.__class__.__name__}:{tool_call_id}:{content}:{tool_calls}"

        try:
            for chunk in self.agent.stream(
                {"messages": [{"role": "user", "content": command}]},
                config=config,
                stream_mode="updates",
            ):
                for data in chunk.values():
                    messages = data.get("messages", [])
                    for message in messages:
                        key = message_key(message)
                        if key not in seen_message_keys:
                            seen_message_keys.add(key)
                            collected_messages.append(message)
                    if self._ingest_stream_messages(messages, pending, steps) and step_callback:
                        step_callback(list(steps))
                    if len(steps) >= self.max_iterations:
                        limit_reached = True
                        break
                if limit_reached:
                    break
        except Exception as e:
            return {
                "messages": collected_messages,
                "intermediate_steps": list(steps),
                "error": e,
            }

        return {
            "messages": collected_messages,
            "intermediate_steps": list(steps),
            "stopped_due_to_step_limit": limit_reached,
        }

    def _extract_final_output(self, messages: List[Any]) -> str:
        for message in reversed(messages):
            if getattr(message, "tool_call_id", None):
                continue
            if getattr(message, "tool_calls", None):
                continue
            content = self._message_text(getattr(message, "content", ""))
            if content:
                return content
        return ""

    def _last_observation_preview(self, steps: List[Any], max_length: int = 500) -> str:
        if not steps:
            return ""
        last_step = steps[-1]
        observation = ""
        if isinstance(last_step, (list, tuple)) and len(last_step) >= 2:
            observation = self._message_text(last_step[1])
        else:
            observation = self._message_text(last_step)
        return sanitize_for_log(observation, max_length=max_length)

    def _last_response_message(self, messages: List[Any]) -> Optional[Any]:
        for message in reversed(messages):
            if getattr(message, "tool_call_id", None):
                continue
            return message
        return None

    def _message_finish_reason(self, message: Any) -> Optional[str]:
        response_metadata = getattr(message, "response_metadata", None)
        if isinstance(response_metadata, dict):
            for key in ("finish_reason", "stop_reason"):
                if response_metadata.get(key):
                    return str(response_metadata[key])
            message_info = response_metadata.get("message")
            if isinstance(message_info, dict) and message_info.get("finish_reason"):
                return str(message_info["finish_reason"])
        additional_kwargs = getattr(message, "additional_kwargs", None)
        if isinstance(additional_kwargs, dict):
            for key in ("finish_reason", "stop_reason"):
                if additional_kwargs.get(key):
                    return str(additional_kwargs[key])
        return None

    def _log_empty_terminal_response(
        self,
        command_id: str,
        messages: List[Any],
        steps: List[Any],
        token_usage: Dict[str, Any],
        retry_index: int,
    ) -> None:
        message = self._last_response_message(messages)
        response_metadata = getattr(message, "response_metadata", None) if message is not None else None
        logger.warning(
            "Agent returned empty terminal response command_id=%s retry=%d message_class=%s "
            "content=\"%s\" tool_call_count=%d finish_reason=%s response_metadata=\"%s\" "
            "steps=%d %s last_observation=\"%s\"",
            command_id,
            retry_index,
            message.__class__.__name__ if message is not None else None,
            sanitize_for_log(self._message_text(getattr(message, "content", ""))) if message is not None else "",
            len(getattr(message, "tool_calls", None) or []) if message is not None else 0,
            self._message_finish_reason(message) if message is not None else None,
            sanitize_for_log(response_metadata, max_length=500) if response_metadata is not None else "",
            len(steps),
            format_token_usage_for_log(token_usage),
            self._last_observation_preview(steps),
        )

    def _empty_response_recovery_command(
        self,
        original_command: str,
        steps: List[Any],
        retry_index: int,
    ) -> str:
        tool_names = self._extract_tool_names(steps)
        recent_tools = ", ".join(tool_names[-10:]) if tool_names else "none"
        last_observation = self._last_observation_preview(steps, max_length=1200) or "none"
        final_rule = "The final answer must end with [TASK DONE]."
        finish_rule = "finish only when the mission is complete."
        if getattr(self, "completion_contract", "off") in {"hover_completion_v1", "hover_completion_v2"}:
            from flockcue.harness.hover import parse_hover_contract
            if parse_hover_contract(original_command)["kind"] != "not_applicable":
                final_rule = ("Use [TASK DONE] only when the scoped hover completion evidence is verified; "
                              "otherwise state that completion is unverified without this marker.")
                finish_rule = "finish with the verified result or explicitly state why completion remains unverified."
        return (
            "Continue the UAV mission after an empty assistant response.\n"
            "Do not restart or repeat completed actions unless current state verification proves it is required.\n"
            "Check the current drone/session state, continue from the last completed observation, and "
            f"{finish_rule}\n"
            f"{final_rule}\n\n"
            f"Original command:\n{original_command}\n\n"
            f"Recovery attempt: {retry_index}\n"
            f"Completed tool step count: {len(steps)}\n"
            f"Recent tools: {recent_tools}\n"
            f"Last completed observation: {last_observation}"
        )

    def _incomplete_output_reason(self, output: str) -> Optional[str]:
        if not output.strip():
            return "Agent stopped without a final response; task completion was not verified."
        if getattr(self, "completion_contract", "off") in {"hover_completion_v1", "hover_completion_v2"}:
            from flockcue.harness.hover import parse_hover_contract
            if parse_hover_contract(getattr(self, "_completion_command", ""))["kind"] != "not_applicable":
                # An honest incomplete/unsupported final answer is a normal
                # return. Evidence assessment, not a string marker, decides
                # whether a completion claim can be accepted.
                return None
        if "[TASK DONE]" not in output:
            return "Agent final response did not include [TASK DONE]; task completion was not verified."
        return None

    def _runtime_metadata_for_toolchain(self, empty_response_retries: int) -> Dict[str, Any]:
        session_context = getattr(self, "session_context", {}) or {}
        blackboard = getattr(self, "blackboard", None)
        session_id = session_context.get("session_id")
        if not session_id and blackboard is not None:
            session_id = getattr(blackboard, "session_id", None)
        metadata = {
            "provider": getattr(self, "llm_provider", None),
            "model": getattr(self, "llm_model", None),
            "uav_base_url": getattr(getattr(self, "client", None), "base_url", None),
            "session_id": session_id,
            "task_type": session_context.get("task_type"),
            "task_description": session_context.get("task_description"),
            "max_iterations": getattr(self, "max_iterations", None),
            "empty_response_retries": empty_response_retries,
            "share_blackboard_by_session": getattr(self, "share_blackboard_by_session", None),
        }
        if getattr(self, "completion_contract", "off") != "off" or getattr(self, "tool_input_contract", "string_v1") != "string_v1":
            metadata.update(completion_contract=getattr(self, "completion_contract", "off"),
                            tool_input_contract=getattr(self, "tool_input_contract", "string_v1"),
                            input_layout=getattr(self, "input_layout", "full_v1"))
        if getattr(self, "task_progress_mode", "off") != "off":
            metadata["task_progress_mode"] = self.task_progress_mode
            metadata["task_progress_active"] = getattr(self, "_task_progress", None) is not None
        return metadata

    def _record_toolchain_if_enabled(
        self,
        result: Dict[str, Any],
        *,
        command_id: str,
        command: str,
        started_at: str,
        completed_at: str,
        status: str,
        empty_response_retries: int,
        tool_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if getattr(self, "completion_contract", "off") in {"hover_completion_v1", "hover_completion_v2"}:
            from flockcue.harness.hover import assess_hover_completion
            ledger = getattr(self, "_task_progress", None)
            try:
                assessment = assess_hover_completion(command, ledger.snapshot() if ledger is not None else {},
                                                     contract=self.completion_contract)
            except Exception as exc:
                assessment = {"schema": self.completion_contract.replace("_", "-"), "status": "unverified",
                    "task_completed": None, "completion_claim_accepted": False,
                    "reasons": ["completion_observer_error:" + type(exc).__name__], "evidence_refs": []}
            model_output = str(result.get("output", ""))
            marker_present = "[TASK DONE]" in model_output
            assessment = {**assessment, "completion_marker_present": marker_present,
                          "run_returned_normally": bool(result.get("success"))}
            result = {**result, "completion_assessment": assessment,
                      "task_completed": assessment.get("task_completed"), "model_output": model_output}
            if assessment.get("completion_claim_accepted") is False:
                result["output"] = "Task completion is not verified by the public evidence: " + ", ".join(
                    str(reason) for reason in assessment.get("reasons", ["completion_evidence_missing"]))
            # success remains the existing run-status field; task completion is
            # an explicit separate result, never supplied by a private checker.
        archive_info = self._finish_model_io_archive(status)
        if archive_info is not None:
            result = {**result, "model_io_archive": archive_info}
        worker_usage = getattr(self, "_observation_worker_token_usage", None)
        harness = getattr(self, "_harness", None)
        if harness is not None:
            harness.close(status)
            result = {**result, "harness": harness.snapshot()}
        if worker_usage and worker_usage.get("llm_calls"):
            result = {**result, "token_usage": dict(result.get("token_usage") or self._empty_token_usage())}
            merge_token_usage(result["token_usage"], worker_usage)
        if not getattr(self, "toolchain_json_recording", False):
            stop_recording = getattr(getattr(self, "client", None), "stop_api_call_recording", None)
            if callable(stop_recording):
                stop_recording()
            return result

        try:
            from flockcue.recording.toolchain import (
                request_history_jsonl_path,
                tool_chain_from_steps,
                write_request_history_jsonl,
                write_toolchain_json,
            )

            steps = result.get("intermediate_steps", [])
            ordered_tool_names = tool_names if tool_names is not None else self._extract_tool_names(steps)
            get_api_calls = getattr(getattr(self, "client", None), "get_api_call_records", None)
            api_calls = get_api_calls() if callable(get_api_calls) else []
            record = {
                "schema_version": 1,
                "command_id": command_id,
                "started_at": started_at,
                "completed_at": completed_at,
                "status": status,
                "success": bool(result.get("success", False)),
                "command": command,
                "output": str(result.get("output", "")),
                "token_usage": result.get("token_usage") or self._empty_token_usage(),
                "runtime": self._runtime_metadata_for_toolchain(empty_response_retries),
                "tool_names": ordered_tool_names,
                "tool_chain": tool_chain_from_steps(steps),
                "api_calls": api_calls,
            }
            if "completion_assessment" in result:
                record.update(completion_assessment=result["completion_assessment"],
                              task_completed=result.get("task_completed"), model_output=result["model_output"])
            if getattr(self, "_model_request_audit_enabled", False):
                record["model_requests_wire"] = self._model_wire_requests
                record["model_request_audit_errors"] = self._model_wire_errors
            if archive_info is not None:
                record["model_io_archive"] = archive_info
            if harness is not None:
                record["harness"] = result["harness"]
            ledger = getattr(self, "_task_progress", None)
            if ledger is not None:
                try:
                    record["task_progress"] = ledger.snapshot()
                except Exception:
                    logger.exception("Could not snapshot task progress command_id=%s", command_id)
            request_history_file = None
            try:
                request_history_path = request_history_jsonl_path(record)
                write_request_history_jsonl(record, api_calls)
                request_history_file = str(request_history_path)
                record["request_history_file"] = request_history_file
            except Exception as e:
                logger.warning("Could not write request-history JSONL command_id=%s error=%s", command_id, e)
            path = write_toolchain_json(record)
            recorded_result = {**result, "tool_chain_file": str(path)}
            if request_history_file is not None:
                recorded_result["request_history_file"] = request_history_file
            return recorded_result
        except Exception as e:
            logger.warning("Could not write tool-chain JSON command_id=%s error=%s", command_id, e)
            return result
        finally:
            stop_recording = getattr(getattr(self, "client", None), "stop_api_call_recording", None)
            if callable(stop_recording):
                stop_recording()

    def _run_agent_once(
        self,
        command: str,
        config: Dict[str, Any],
        step_callback: Optional[Any],
    ) -> Dict[str, Any]:
        if step_callback:
            return self._stream_updates_to_steps(command, config, step_callback)
        return self.agent.invoke(
            {"messages": [{"role": "user", "content": command}]},
            config=config,
        )

    def _note_model_io_archive_error(self, exc):
        self._model_io_archive_error = type(exc).__name__
        archive = getattr(self, "_model_io_archive", None)
        if archive is not None:
            try:
                archive.mark_incomplete("observer_error")
            except Exception:
                pass

    def _finish_model_io_archive(self, reason):
        if not getattr(self, "_model_io_archive_enabled", False):
            return None
        archive = getattr(self, "_model_io_archive", None)
        if archive is not None:
            try:
                info = archive.close(reason)
                error = getattr(self, "_model_io_archive_error", None)
                if error:
                    info = {**info, "complete": False, "state": "incomplete",
                            "error_types": sorted(set(info.get("error_types", []) + [error]))}
                return info
            except Exception as exc:
                self._model_io_archive_error = type(exc).__name__
        return {"state": "incomplete", "complete": False, "path": None,
                "reasons": ["archive_unavailable"],
                "error_types": [self._model_io_archive_error] if getattr(self, "_model_io_archive_error", None) else []}

    @contextmanager
    def _exclusive_episode(self, operation: str):
        # Older test adapters use __new__; initialize their instance gate under
        # a lock too, rather than racing two different lazily created locks.
        with _EPISODE_GATE_INITIALIZATION:
            gate = getattr(self, "_episode_lock", None)
            if gate is None:
                gate = self._episode_lock = threading.Lock()
        if not gate.acquire(blocking=False):
            raise AgentBusyError(
                f"agent_busy: cannot {operation} while this agent has an active operation; "
                "wait for it to return before retrying or resetting the shared world")
        try:
            if getattr(self, "_episode_cleanup_failed", False):
                raise AgentBusyError(
                    "agent_busy: previous aborted-command cleanup failed; create a fresh agent instance")
            yield
        finally:
            # Keep ownership through exception/cancellation handling and file
            # export. A job status flag is not proof its execution has drained.
            gate.release()

    def execute(
        self,
        command: str,
        callbacks: Optional[List[Any]] = None,
        step_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Run one command exclusively; a busy attempt performs no execution."""
        with self._exclusive_episode("execute"):
            try:
                return self._execute_command(command, callbacks, step_callback)
            except BaseException:
                # The legacy body handles Exception and exports its result.
                # BaseException (e.g. async cancellation) can bypass that path:
                # invalidate the old runtime and stop recording before reuse,
                # without inventing a successfully persisted command result.
                harness = getattr(self, "_harness", None)
                cleanup = []
                cleanup.append(lambda: self._finish_model_io_archive("execute_aborted"))
                if harness is not None:
                    cleanup.append(lambda: harness.close("execute_aborted"))
                stop = getattr(getattr(self, "client", None), "stop_api_call_recording", None)
                if callable(stop):
                    cleanup.append(stop)
                for finish in cleanup:
                    try:
                        finish()
                    except BaseException as exc:
                        self._episode_cleanup_failed = True
                        logger.error("Aborted command cleanup failed: %s", type(exc).__name__)
                raise

    def _execute_command(
        self,
        command: str,
        callbacks: Optional[List[Any]] = None,
        step_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Execute a natural language command.

        Returns:
            Dictionary with 'output', 'intermediate_steps', and 'success' keys.
        """
        command_id = str(self.command_id_factory())
        self._completion_command = command
        self._model_io_archive = None
        self._model_io_archive_error = None
        if getattr(self, "_model_io_archive_enabled", False):
            try:
                from flockcue.recording.wire import ModelIOArchive
                self._model_io_archive = ModelIOArchive(command_id, getattr(self, "_model_io_archive_dir", None))
                if self.llm_provider not in {"openai", "openai-compatible"}:
                    self._model_io_archive.mark_incomplete("unsupported_provider")
                if any(e.startswith("setup:") for e in getattr(self, "_model_wire_errors", [])):
                    self._model_io_archive.mark_incomplete("hook_setup_unavailable")
            except Exception as exc:
                self._model_io_archive_error = type(exc).__name__
        self._task_progress = None
        self._task_progress_middleware = None
        self._model_wire_requests = []
        self._harness = None
        self._harness_task_started = time.monotonic()
        if getattr(getattr(self, "client", None), "_harness_deadline", None) is not None:
            self.client._harness_deadline = None
        def check_harness_cancellation():
            for callback in callbacks or []:
                check = getattr(callback, "_check_cancellation", None)
                if callable(check):
                    check()
        self._harness_cancel_check = check_harness_cancellation
        self._model_wire_errors = [e for e in getattr(self, "_model_wire_errors", []) if e.startswith("setup:")]
        self._in_observation_worker = False
        self._observation_worker_token_usage = empty_token_usage()
        self._observation_worker_task_started = time.monotonic()
        self._public_search_task_started = time.monotonic()
        started_at = datetime.now().isoformat()
        start_api_recording = getattr(getattr(self, "client", None), "start_api_call_recording", None)
        stop_api_recording = getattr(getattr(self, "client", None), "stop_api_call_recording", None)
        if getattr(self, "toolchain_json_recording", False):
            if callable(start_api_recording):
                start_api_recording()
        elif callable(stop_api_recording):
            stop_api_recording()
        sanitized_command = sanitize_for_log(command)
        logger.info(
            "Command started command_id=%s provider=%s model=%s command_length=%d command=\"%s\"",
            command_id,
            self.llm_provider,
            self.llm_model,
            len(command),
            sanitized_command,
        )
        if self.debug:
            print(f"\n{'=' * 60}")
            print("🎯 Executing Command")
            print(f"{'=' * 60}")
            print(f"Command: {command}")
            print(f"{'=' * 60}\n")

        try:
            self.blackboard = self._blackboard_for_current_command()
            self.tools = self._create_tools(self.blackboard)
            self._initialize_task_progress(command, command_id)
            self.agent = self._create_agent_runtime()

            if self.debug:
                print("🔄 Invoking modern agent runtime...")

            config: Dict[str, Any] = {"recursion_limit": self._recursion_limit_for_steps()}
            provider_token_callback = ProviderTokenLoggingCallback(
                command_id=command_id,
                provider=self.llm_provider,
                model=self.llm_model,
            )
            config["callbacks"] = [provider_token_callback]
            if callbacks:
                config["callbacks"].extend(callbacks)

            all_intermediate_steps: List[Any] = []
            total_token_usage = self._empty_token_usage()
            empty_response_retries = 0
            current_command = command
            max_attempts = self.max_empty_response_retries + 1

            for attempt_index in range(max_attempts):
                if attempt_index > 0:
                    self.agent = self._create_agent_runtime()
                    empty_response_retries += 1

                def attempt_step_callback(attempt_steps: List[Any]) -> None:
                    if step_callback:
                        step_callback(all_intermediate_steps + list(attempt_steps))

                result = self._run_agent_once(
                    current_command,
                    config,
                    attempt_step_callback if step_callback else None,
                )

                messages = result.get("messages", [])
                output = self._extract_final_output(messages)
                attempt_steps = self._extract_intermediate_steps(messages)
                if step_callback and not attempt_steps:
                    attempt_steps = result.get("intermediate_steps", [])
                all_intermediate_steps.extend(attempt_steps)
                token_usage = self._extract_token_usage(messages)
                merge_token_usage(total_token_usage, token_usage)
                tool_names = self._extract_tool_names(all_intermediate_steps)

                stream_error = result.get("error")
                if stream_error is not None:
                    logger.error(
                        "Command failed command_id=%s error=%s %s tools=%s",
                        command_id,
                        stream_error,
                        format_token_usage_for_log(total_token_usage),
                        tool_names,
                    )
                    result_payload = {
                        "success": False,
                        "output": f"Error executing command: {str(stream_error)}",
                        "intermediate_steps": all_intermediate_steps,
                        "token_usage": total_token_usage,
                        "empty_response_retries": empty_response_retries,
                    }
                    return self._record_toolchain_if_enabled(
                        result_payload,
                        command_id=command_id,
                        command=command,
                        started_at=started_at,
                        completed_at=datetime.now().isoformat(),
                        status="stream_error",
                        empty_response_retries=empty_response_retries,
                        tool_names=tool_names,
                    )

                if result.get("stopped_due_to_step_limit") or len(all_intermediate_steps) >= self.max_iterations:
                    logger.warning(
                        "Command stopped at step limit command_id=%s steps=%d %s tools=%s",
                        command_id,
                        len(all_intermediate_steps),
                        format_token_usage_for_log(total_token_usage),
                        tool_names,
                    )
                    result_payload = {
                        "success": False,
                        "output": (
                            output
                            or f"Stopped after reaching the maximum of {self.max_iterations} ReAct tool step(s)."
                        ),
                        "intermediate_steps": all_intermediate_steps,
                        "token_usage": total_token_usage,
                        "empty_response_retries": empty_response_retries,
                    }
                    return self._record_toolchain_if_enabled(
                        result_payload,
                        command_id=command_id,
                        command=command,
                        started_at=started_at,
                        completed_at=datetime.now().isoformat(),
                        status="step_limit",
                        empty_response_retries=empty_response_retries,
                        tool_names=tool_names,
                    )

                incomplete_reason = self._incomplete_output_reason(output)
                if incomplete_reason:
                    if not output.strip() and attempt_index < max_attempts - 1:
                        self._log_empty_terminal_response(
                            command_id,
                            messages,
                            all_intermediate_steps,
                            total_token_usage,
                            empty_response_retries,
                        )
                        current_command = self._empty_response_recovery_command(
                            command,
                            all_intermediate_steps,
                            empty_response_retries + 1,
                        )
                        continue

                    logger.warning(
                        "Command stopped without verified completion command_id=%s steps=%d reason=%s retries=%d %s tools=%s last_observation=\"%s\"",
                        command_id,
                        len(all_intermediate_steps),
                        incomplete_reason,
                        empty_response_retries,
                        format_token_usage_for_log(total_token_usage),
                        tool_names,
                        self._last_observation_preview(all_intermediate_steps),
                    )
                    result_payload = {
                        "success": False,
                        "output": incomplete_reason,
                        "intermediate_steps": all_intermediate_steps,
                        "token_usage": total_token_usage,
                        "empty_response_retries": empty_response_retries,
                    }
                    return self._record_toolchain_if_enabled(
                        result_payload,
                        command_id=command_id,
                        command=command,
                        started_at=started_at,
                        completed_at=datetime.now().isoformat(),
                        status="failed",
                        empty_response_retries=empty_response_retries,
                        tool_names=tool_names,
                    )

                intermediate_steps = all_intermediate_steps
                token_usage = total_token_usage
                break
            else:
                intermediate_steps = all_intermediate_steps
                token_usage = total_token_usage
                output = ""

            if step_callback:
                step_callback(intermediate_steps)

            if self.debug:
                print(f"\n{'=' * 60}")
                print("✅ Command Execution Complete")
                print(f"{'=' * 60}")
                print("Success: True")
                print(f"Intermediate steps: {len(intermediate_steps)}")
                print(f"{'=' * 60}\n")

            logger.info(
                "Command completed command_id=%s success=True steps=%d retries=%d %s tools=%s",
                command_id,
                len(intermediate_steps),
                empty_response_retries,
                format_token_usage_for_log(token_usage),
                tool_names,
            )
            result_payload = {
                "success": True,
                "output": output,
                "intermediate_steps": intermediate_steps,
                "token_usage": token_usage,
                "empty_response_retries": empty_response_retries,
            }
            return self._record_toolchain_if_enabled(
                result_payload,
                command_id=command_id,
                command=command,
                started_at=started_at,
                completed_at=datetime.now().isoformat(),
                status="completed",
                empty_response_retries=empty_response_retries,
                tool_names=tool_names,
            )
        except Exception as e:
            logger.exception("Command failed command_id=%s error=%s", command_id, e)
            if self.debug:
                print(f"\n{'=' * 60}")
                print("❌ Command Execution Failed")
                print(f"{'=' * 60}")
                print(f"Error: {str(e)}")
                print(f"{'=' * 60}\n")

            result_payload = {
                "success": False,
                "output": f"Error executing command: {str(e)}",
                "intermediate_steps": [],
                "token_usage": self._empty_token_usage(),
            }
            return self._record_toolchain_if_enabled(
                result_payload,
                command_id=command_id,
                command=command,
                started_at=started_at,
                completed_at=datetime.now().isoformat(),
                status="exception",
                empty_response_retries=0,
                tool_names=[],
            )
