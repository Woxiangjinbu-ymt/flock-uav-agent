"""Deterministic local-only agent for executor isolation tests; never calls an LLM."""
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
from blackboard import PerceptionBlackboard
from langchain_core.runnables import RunnableLambda
from uav_api_client import UAVAPIClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reproduction_lifecycle import install


class FakeAgent:
    def __init__(self):
        self.client = UAVAPIClient(os.environ["UAV_API_URL"])
        self._session_blackboards = {"fixture": PerceptionBlackboard("fixture")}
        self.calls = 0

    def execute(self, command, callbacks=None):
        self.calls += 1
        board = self._session_blackboards["fixture"]
        if board._step != self.calls - 1:
            raise AssertionError("Scene blackboard was lost between tasks")
        board.next_step()
        print(f"FIXTURE_CALL {self.calls}", flush=True)
        mode = os.environ.get("HARNESS_TEST_MODE", "success")
        if self.calls == 1 and mode == "provider_error":
            from reproduction_metrics import EventWriter, METRICS_FILENAME
            job_id = next(c.job_id for c in callbacks if hasattr(c, "job_id"))
            EventWriter(Path(args.logs) / METRICS_FILENAME).emit(
                "llm_error", job_id=job_id, run_id="fixture-quota", error_type="APIStatusError", status_code=402)
            return {"success": False, "output": "LOCAL FIXTURE: provider unavailable", "intermediate_steps": []}
        if self.calls == 1 and mode == "parallel":
            directory = Path(os.environ["HARNESS_TEST_DIRECTORY"])
            (directory / (".ready-" + os.environ["HARNESS_TEST_SCENE"])).touch()
            deadline = time.monotonic() + 10
            while len(list(directory.glob(".ready-*"))) < int(os.environ.get("HARNESS_TEST_PARALLEL_COUNT", "2")):
                if time.monotonic() >= deadline:
                    raise AssertionError("The second scene never ran concurrently")
                time.sleep(.05)
            time.sleep(.6)
        elif self.calls == 1 and mode == "timeout":
            time.sleep(2.5)
            # The lifecycle callback must raise before this runnable can run.
            def late(_):
                print("LATE_ACTION_ATTEMPT", flush=True)
            RunnableLambda(late).invoke(None, config={"callbacks": callbacks})
        elif self.calls == 1 and mode in {"stuck", "interrupt"}:
            time.sleep(60)
        else:
            time.sleep(.6)
        return {"success": True, "output": "LOCAL FIXTURE: no LLM requests", "intermediate_steps": []}


service.initialize_agent_from_settings = lambda: FakeAgent()
install(service, os.environ["REPRO_CONTROL_TOKEN"])
uvicorn.run(service.app, host="127.0.0.1", port=args.port)
