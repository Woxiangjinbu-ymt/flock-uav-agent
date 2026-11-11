#!/usr/bin/env bash
# Install project-local Python and one isolated environment per component.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_VERSION=3.11.16
INSTALL_DEV=false
if [[ $# -gt 1 ]]; then
  printf 'Usage: bash scripts/setup_python_envs.sh [--dev]\n' >&2
  exit 2
fi
if [[ "${1:-}" == "--dev" ]]; then
  INSTALL_DEV=true
elif [[ $# -ne 0 ]]; then
  printf 'Usage: bash scripts/setup_python_envs.sh [--dev]\n' >&2
  exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
  printf 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/\n' >&2
  exit 1
fi

cd "$PROJECT_ROOT"
export UV_CACHE_DIR="$PROJECT_ROOT/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PROJECT_ROOT/.runtimes/python"
uv python install "$PYTHON_VERSION" --no-bin
PROJECT_PYTHON="$(uv python find "$PYTHON_VERSION" --managed-python)"
"$PROJECT_PYTHON" -c 'import tkinter; print("Python/Tcl ready:", tkinter.Tcl().eval("info patchlevel"))'

for component in server controller agent4drone; do
  venv_python="$PROJECT_ROOT/$component/.venv/bin/python"
  if [[ -e "$PROJECT_ROOT/$component/.venv" ]]; then
    # Do not silently replace an existing environment's interpreter.
    "$venv_python" -c 'import sys, tkinter; assert sys.version_info[:3] == (3, 11, 16), "Existing environment must use Python 3.11.16; recreate this component .venv explicitly."'
  else
    uv venv --python "$PROJECT_PYTHON" "$component/.venv"
  fi
  requirements="$component/requirements.txt"
  if [[ "$component" == "agent4drone" && "$INSTALL_DEV" == true ]]; then
    requirements="$component/requirements-dev.txt"
  fi
  uv pip sync --python "$venv_python" "$requirements"
  uv pip check --python "$venv_python"
done

printf '\nEnvironments ready. See docs/DEPENDENCY_SETUP_CN.md for launch and test commands.\n'
