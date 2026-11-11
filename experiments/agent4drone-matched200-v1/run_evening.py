"""One local, delayed execution of the registered 2026-11-11 comparison."""
from datetime import datetime
import fcntl
from http.client import HTTPSConnection
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "outputs/agent4drone-matched200-v1"
RESULT = ROOT / "docs/results/agent4drone-matched200-20261111"
CLOCK = ZoneInfo("Asia/Shanghai")
START = datetime(2026, 11, 11, 18, 5, tzinfo=CLOCK)
NEXT_PEAK = datetime(2026, 11, 12, 9, tzinfo=CLOCK)


def save(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    temp.replace(path)


def state(status, **details):
    data = {"status": status, "updated_at": datetime.now(CLOCK).isoformat(),
            "scheduled_for": START.isoformat(), "pid": os.getpid(), **details}
    save(STAGE / "evening-status.json", data)
    print(json.dumps(data, ensure_ascii=False), flush=True)


def balance(name):
    """Record the read-only account window separately from attributed request usage."""
    connection = None
    try:
        config = json.loads((ROOT / "agent4drone/llm_settings.json").read_bytes())
        provider = config["provider_configs"]["deepseek_official"]
        if provider["base_url"].rstrip("/") != "https://api.deepseek.com":
            raise ValueError("Unexpected balance service")
        key = os.environ.get("DEEPSEEK_API_KEY") or provider["api_key"]
        connection = HTTPSConnection("api.deepseek.com", timeout=10)
        connection.request("GET", "/user/balance", headers={"Authorization": "Bearer " + key})
        response = connection.getresponse()
        if response.status != 200:
            print(f"Balance snapshot HTTP {response.status}", flush=True)
            return
        cny = next(row for row in json.loads(response.read())["balance_infos"] if row["currency"] == "CNY")
        save(RESULT / name, {"captured_at": datetime.now(CLOCK).isoformat(),
                             "currency": "CNY", "total_balance": cny["total_balance"]})
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        print(f"Balance snapshot unavailable ({type(exc).__name__})", flush=True)
    finally:
        if connection is not None:
            connection.close()


def main():
    os.chdir(ROOT)
    with (STAGE / ".evening.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runs = STAGE / "experiments/runs"
        if runs.is_dir() and any(p.is_dir() for p in runs.iterdir()):
            raise RuntimeError("A first attempt already exists")
        state("waiting")
        while (delay := START.timestamp() - time.time()) > 0:
            time.sleep(min(60, delay))
        if datetime.now(CLOCK) >= NEXT_PEAK:
            state("missed_scheduled_window")
            return 1
        state("starting")
        RESULT.mkdir(parents=True, exist_ok=True)
        balance("balance-before.json")
        runner = ROOT / "scripts/run_agent4drone_matched_subset.py"
        rc = subprocess.run([sys.executable, runner, "run"], cwd=ROOT).returncode
        balance("balance-after.json")
        if not runs.is_dir() or not any(p.is_dir() for p in runs.iterdir()):
            state("preflight_failed", runner_exit=rc)
            return rc or 1
        state("scoring", runner_exit=rc)
        reporter = ROOT / "scripts/report_agent4drone_matched200.py"
        report_rc = subprocess.run([sys.executable, reporter, "--stage", STAGE], cwd=ROOT).returncode
        state("completed" if rc == report_rc == 0 else "needs_review", runner_exit=rc, report_exit=report_rc)
        return rc or report_rc


if __name__ == "__main__":
    raise SystemExit(main())
