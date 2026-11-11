"""Launch the unchanged upstream service with private config and per-attempt logs."""
import argparse
import json
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--config", required=True)
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--logs", required=True)
args = parser.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent4drone"))
import logging_config

logging_config.LOG_DIR = Path(args.logs)
import agent_api_service
import uvicorn
from reproduction_lifecycle import install
from reproduction_metrics import install_agent_metrics, METRICS_FILENAME

agent_api_service.AGENT_CONFIG_FILE = str(Path(args.config).resolve())
install(agent_api_service, os.environ["REPRO_CONTROL_TOKEN"])
settings = json.loads(Path(args.config).read_text(encoding="utf-8"))
provider = settings["provider_configs"][settings["selected_provider"]]
if settings["selected_provider"] == "bailian_qwen_flash":
    import uav_agent
    from reproduction_models import model_class
    uav_agent.ChatOpenAI = model_class(settings["selected_provider"])
install_agent_metrics(agent_api_service, Path(args.logs) / METRICS_FILENAME,
                      provider.get("base_url", ""))
uvicorn.run(agent_api_service.app, host="127.0.0.1", port=args.port)
