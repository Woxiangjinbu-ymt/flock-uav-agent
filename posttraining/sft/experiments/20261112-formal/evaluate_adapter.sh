#!/usr/bin/env bash
# Arguments: exported epoch adapter directory and its independent A60 stage.
set -euo pipefail
umask 077
if [[ $# -ne 2 ]]; then
  echo "usage: $0 ADAPTER STAGE" >&2
  exit 2
fi
src=$(realpath "$(dirname "$0")/source")
candidate=$(realpath "$1")
stage=$(realpath -m "$2")
out="$stage.service"
mkdir -p "$out"
exec 9>"$out/validate.lock"
flock -n 9 || exit 75
. /uav/flockcue-validation60-20261112/env.sh
export PYTHONPATH="$src"
cd "$src"
deployment=/uav/flockcue-20261110/flockcue-a100
infer="$deployment/operations/venv-inference/bin/python"
base="$deployment/models/Qwen3.5-2B"
port=${FLOCKCUE_STUDENT_PORT:-21769}
provider_pid=
stop_provider() {
  if [[ -n "$provider_pid" ]]; then
    kill "$provider_pid" 2>/dev/null || :
    wait "$provider_pid" 2>/dev/null || :
    provider_pid=
  fi
}
finish() {
  rc=$?
  stop_provider
  echo "$rc" > "$out/validate.exit.tmp"
  mv "$out/validate.exit.tmp" "$out/validate.exit"
}
trap finish EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
rm -f "$out/validate.exit"
if [[ ! -d "$stage" ]]; then
  "$FLOCKCUE_CONTROLLER_PYTHON" -m runtime.student_validation prepare --stage "$stage" --adapter "$candidate"
fi
cmp "$candidate/adapter_config.json" "$stage/adapter/adapter_config.json"
cmp "$candidate/adapter.safetensors" "$stage/adapter/adapter.safetensors"
if [[ -f "$stage/summary.json" ]]; then
  "$FLOCKCUE_CONTROLLER_PYTHON" -m runtime.student_validation report --stage "$stage"
  exit 0
fi
"$infer" - "$src/posttraining/inference/requirements-linux-cu128.lock" > "$out/inference-environment.json.tmp" <<'PY'
from importlib import metadata as md
import importlib.util
import json
from pathlib import Path
import sys
import torch

pins = dict(line.split()[0].split("==") for line in Path(sys.argv[1]).read_text().splitlines() if line.strip())
# Check the numerical inference and rendering stack against the first A100 run.
used = ("torch", "transformers", "safetensors", "tokenizers", "jinja2", "triton", "numpy")
for name in used:
    if md.version(name) != pins[name]:
        raise ValueError(f"{name}: expected {pins[name]}, got {md.version(name)}")
for module in ("fla", "causal_conv1d"):
    if importlib.util.find_spec(module) is not None:
        raise ValueError(f"{module} changes the reference PyTorch kernels used in the first A60 run")
print(json.dumps({"python": sys.version, "gpu": torch.cuda.get_device_name(0), "cuda": torch.version.cuda,
                  "packages": sorted((d.metadata["Name"], d.version) for d in md.distributions())}, indent=2))
PY
mv "$out/inference-environment.json.tmp" "$out/inference-environment.json"
for scene in 72ccacd0 0293952c cc14d231; do
  if compgen -G "$stage/sft/$scene/attempt-*/complete.json" >/dev/null; then continue; fi
  if curl --noproxy '*' -s --max-time 1 "http://127.0.0.1:$port/health" >/dev/null; then
    echo "Port $port already occupied" >&2
    exit 1
  fi
  prefix="$out/$scene"
  "$infer" -u -m posttraining.inference.server --model "$base" --adapter "$stage/adapter" --port "$port" \
    --log "$prefix.student-events.jsonl" >> "$prefix.student-service.log" 2>&1 &
  provider_pid=$!
  ready=false
  for ((n=0;n<300;n++)); do
    if ! kill -0 "$provider_pid" 2>/dev/null; then
      tail -20 "$prefix.student-service.log" >&2
      exit 1
    fi
    if curl --noproxy '*' -fsS --max-time 1 "http://127.0.0.1:$port/health" > "$prefix.health.json.tmp" 2>/dev/null; then
      mv "$prefix.health.json.tmp" "$prefix.health.json"
      ready=true
      break
    fi
    sleep 1
  done
  if [[ "$ready" != true ]]; then echo "Student did not become ready" >&2; exit 1; fi
  "$FLOCKCUE_CONTROLLER_PYTHON" -u -m runtime.student_validation run --stage "$stage" \
    --arm sft --scene "$scene" --endpoint "http://127.0.0.1:$port/v1"
  stop_provider
done
"$FLOCKCUE_CONTROLLER_PYTHON" -u -m runtime.student_validation report --stage "$stage"
