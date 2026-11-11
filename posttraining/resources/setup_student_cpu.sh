#!/usr/bin/env bash
# Install only the three environments needed for the CPU handoff.
set -euo pipefail
cd "$(dirname "$0")"
while [[ ! -d scripts && "$PWD" != / ]]; do cd ..; done
[[ -d scripts ]]
python_bin=${FLOCKCUE_PYTHON:-$(uv python find --managed-python 3.11.16)}
"$python_bin" -c 'import sys; assert sys.version_info[:3] == (3,11,16), sys.version'
while read -r environment requirements; do
  if [[ ! -e "$environment" ]]; then
    uv venv --python "$python_bin" "$environment"
  fi
  "$environment/bin/python" -c 'import sys; assert sys.version_info[:3] == (3,11,16), sys.version'
  uv pip sync --python "$environment/bin/python" "$requirements"
  uv pip check --python "$environment/bin/python"
done <<'ENVS'
agent4drone/.venv agent4drone/requirements-dev.txt
server/.venv server/requirements.txt
training/.venv-input training/requirements-input.lock
ENVS
