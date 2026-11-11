"""Eight small paid tool roundtrips; this is not a full-context throughput test."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import reproduction as r


def main():
    settings, provider, key = r.selected_config()
    if settings["selected_provider"] != "bailian_qwen_flash" or not key:
        raise ValueError("Qwen configuration with a key is required")
    env = {**os.environ, "REPRO_GATEWAY_API_KEY": key}
    started = time.monotonic()

    def probe(index):
        begin = time.monotonic()
        try:
            p = subprocess.run([str(ROOT / "agent4drone/.venv/bin/python"),
                                str(ROOT / "scripts/reproduction_gateway.py")],
                               env=env, capture_output=True, text=True, timeout=210)
            report = json.loads(p.stdout)
            return {"worker": index, "exit_code": p.returncode,
                    "wall_seconds": time.monotonic() - begin,
                    **{k: v for k, v in report.items() if k != "flash_model_ids"}}
        except Exception as error:
            return {"worker": index, "ok": False, "error_type": type(error).__name__}

    with ThreadPoolExecutor(max_workers=8) as pool:
        reports = list(pool.map(probe, range(1, 9)))
    result = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "workers": 8,
              "model": provider["default_model"], "base_url": provider["base_url"],
              "ok": all(v.get("ok") and v.get("exit_code") == 0 for v in reports),
              "wall_seconds": time.monotonic() - started, "reports": reports,
              "limitation": "Short parallel tool roundtrips only; no sustained long-context TPM or balance guarantee."}
    r.save_json(ROOT / "docs/audit/QWEN38_PREPARATION_2026-10-04/concurrency.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "reports"}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
