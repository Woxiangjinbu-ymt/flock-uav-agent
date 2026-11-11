"""TEST FIXTURE ONLY. Deterministic actuator, never used by the reproduction CLI."""
import argparse
import os
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int)
parser.add_argument("--config")
parser.add_argument("--logs")
args = parser.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent4drone"))
import logging_config
logging_config.LOG_DIR = Path(args.logs)
import agent_api_service as service
import uvicorn
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reproduction_lifecycle import install
from uav_api_client import UAVAPIClient
from blackboard import PerceptionBlackboard
from langchain_core.runnables import RunnableLambda


class FakeAgent:
    def __init__(self):
        self.client = UAVAPIClient(os.environ["UAV_API_URL"])
        self._session_blackboards = {"fixture": PerceptionBlackboard("fixture")}
        self.calls = 0

    def execute(self, command, callbacks=None):
        self.calls += 1
        board = self._session_blackboards["fixture"]
        if board._step != self.calls - 1:
            raise AssertionError("Shared memory lost between jobs")
        board.next_step()
        mode = os.environ["HARNESS_TEST_MODE"]
        if self.calls > 1:
            # A second task proves continuation and memory retention, not its
            # flight score; no actions are needed and its real checks remain false.
            return {"success": True, "output": "Shared memory preserved", "intermediate_steps": []}
        actions = [("take_off", {"altitude": 13}), ("rotate", {"heading": 206}), ("hover", {})]
        if mode == "timeout_empty":
            actions = []
        elif mode == "timeout_partial":
            actions = actions[:1]
        for action, params in actions:
            result = self.client._request("POST", "/drones/da04d07a/command/" + action, params=params)
            if result.get("status") not in {"success", "completed"}:
                raise ValueError("Fixture action did not succeed")
        if mode == "timeout_stuck":
            time.sleep(60)  # deliberately ignores cancellation; must be killed
        elif mode.startswith("timeout"):
            time.sleep(2.4)  # budget in fixture is 2 seconds
            # Cancellation must propagate through the installed LangChain
            # callback manager, not merely flip the service's job-status string.
            def late_action(_):
                print("LATE_ACTION_ATTEMPT", flush=True)
                return self.client._request("POST", "/drones/da04d07a/command/rotate", params={"heading": 0})
            RunnableLambda(late_action).invoke(None, config={"callbacks": callbacks})
        return {"success": True, "output": "TEST FIXTURE: no LLM used", "intermediate_steps": []}


service.initialize_agent_from_settings = lambda: FakeAgent()
install(service, os.environ["REPRO_CONTROL_TOKEN"])
uvicorn.run(service.app, host="127.0.0.1", port=args.port)
