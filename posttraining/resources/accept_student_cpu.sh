#!/usr/bin/env bash
# Run from an unpacked handoff. Writes all runtime evidence into a fresh directory.
set -euo pipefail
cd "$(dirname "$0")"
while [[ ! -d scripts && "$PWD" != / ]]; do cd ..; done
[[ -d scripts ]]
out=${1:-"$PWD/tmp/accept-cpu"}
if [[ "$out" != /* || -e "$out" ]]; then
  echo "Pass a new absolute output directory (inside the bundle use tmp/<name>)." >&2
  exit 2
fi
if [[ -f SHA256SUMS ]]; then
  if command -v sha256sum >/dev/null; then sha256sum -c SHA256SUMS >/dev/null
  else shasum -a 256 -c SHA256SUMS >/dev/null; fi
else
  echo "Extract the transfer archive before running this command." >&2
  exit 2
fi
mkdir -p "$out"
export PYTHONPATH="$PWD/scripts" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 CUDA_VISIBLE_DEVICES=""
export FLOCKCUE_TOKENIZER_PYTHON="$PWD/training/.venv-input/bin/python"
if [[ -d "$PWD/training/tokenizers/qwen35-2b" ]]; then
  export FLOCKCUE_QWEN35_TOKENIZER="$PWD/training/tokenizers/qwen35-2b"
fi
export THINKING_DEV_STUDY="$PWD/prepared-dev/study"
agent="$PWD/agent4drone/.venv/bin/python"
for python in "$agent" "$PWD/server/.venv/bin/python" "$FLOCKCUE_TOKENIZER_PYTHON"; do
  "$python" -c 'import sys; assert sys.version_info[:3] == (3,11,16), sys.version'
done
"$agent" - "$PWD" >"$out/host.json" <<'PY'
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

root = Path(sys.argv[1])
environments = {}
for name, executable, lock in (
    ("agent", "agent4drone/.venv/bin/python", "agent4drone/requirements-dev.txt"),
    ("server", "server/.venv/bin/python", "server/requirements.txt"),
    ("tokenizer", "training/.venv-input/bin/python", "training/requirements-input.lock"),
):
    python = str(root / executable)
    subprocess.run(["uv", "pip", "check", "--python", python], check=True, capture_output=True)
    environments[name] = {"python": python, "lock": lock, "dependencies_checked": True,
                          "lock_sha256": hashlib.sha256((root / lock).read_bytes()).hexdigest()}
print(json.dumps({"system": platform.system(), "release": platform.release(), "machine": platform.machine(),
                  "implementation": platform.python_implementation(), "python_version": platform.python_version(),
                  "environments": environments}, indent=2))
PY
"$agent" -m agent_harness check >"$out/baseline-check.json"
"$agent" -m agent_harness smoke --output "$out/baseline" >"$out/baseline-smoke.json"
"$agent" -m thinking_events replay --output "$out/replay" >"$out/replay-summary.json"
for f in sources.json attempts.jsonl turns.jsonl g3.json; do
  cmp "$out/replay/$f" "docs/results/thinking-events-g3-20261108/run-01/$f"
done
cp -R prepared-dev "$out/dev-run"
"$agent" -m thinking_events run-dev --run "$out/dev-run" --g3 "$out/replay/g3.json" >"$out/dev-readiness.json"
"$agent" - "$out" <<'PY'
from decimal import Decimal
import json
from pathlib import Path
import sys

output = Path(sys.argv[1]).resolve()
baseline = json.loads((output / "baseline-check.json").read_bytes())
smoke = json.loads((output / "baseline-smoke.json").read_bytes())["verification"]
assert baseline["source_files"] == 93 and baseline["provider_calls"] == 0
assert smoke["valid"] and smoke["trials"] == 2 and smoke["provider_calls"] == 0
ready = json.loads((output / "dev-readiness.json").read_bytes())
binding = json.loads((output / "dev-run/host-binding.json").read_bytes())
assert Path(binding["workspace"]) == Path.cwd().resolve()
assert Path(binding["study"]) == output / "dev-run/study"
assert Path(binding["source"]) == output / "dev-run/source"
assert ready["g3"]["verified"] and ready["g3"]["status"] == "pass"
assert ready["reason"] == "awaiting_linux_preflight_and_t37"
assert ready["G1"] == ready["G2"] == "indeterminate"
assert ready["planned_slots"] == 116 and ready["slot_statuses"] == {"unstarted": 116}
assert all(ready[key] == 0 for key in (
    "formal_launches_this_invocation", "gpu_launches", "gpu_billed_seconds", "provider_calls", "training_hours"))
assert Decimal(ready["charged_cny"]) == 0
PY
"$agent" -m thinking_events.worker smoke --output "$out/student-smoke" --student-python "$FLOCKCUE_TOKENIZER_PYTHON" >"$out/student-smoke.json"
agent_tests=()
student_tests=()
for suite in scripts/tests/test_thinking_*.py; do
  case "$suite" in
    scripts/tests/test_thinking_student*|scripts/tests/test_thinking_paired_backend.py) student_tests+=("$suite");;
    *) agent_tests+=("$suite");;
  esac
done
"$agent" -m pytest -q "${agent_tests[@]}" --junitxml="$out/agent-tests.xml" >"$out/agent-tests.log"
"$FLOCKCUE_TOKENIZER_PYTHON" -m pytest -q "${student_tests[@]}" --junitxml="$out/student-tests.xml" >"$out/student-tests.log"
"$agent" - "$out" <<'PY'
import json
import sys
from pathlib import Path
from xml.etree import ElementTree

out = Path(sys.argv[1])
def read(name):
    return json.loads((out / name).read_bytes())

counts = {}
for suite in ("agent", "student"):
    result = ElementTree.parse(out / (suite + "-tests.xml")).getroot().find("testsuite")
    counts[suite] = {k: int(result.attrib[k]) for k in ("tests", "failures", "errors", "skipped")}
summary = {"platform": read("host.json"), "baseline": read("baseline-check.json"),
           "baseline_smoke": read("baseline-smoke.json")["verification"], "pytest": counts,
           "student_smoke": read("student-smoke.json"), "cpu_preflight": read("dev-readiness.json")}
(out / "verification.json").write_text(json.dumps(summary, indent=2) + "\n")
PY
cat "$out/dev-readiness.json"
echo "CPU acceptance passed. Evidence: $out"
