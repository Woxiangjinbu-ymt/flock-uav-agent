#!/usr/bin/env bash
# Run the sealed Mac session on a Linux SSH host and bring its verified results back.
set -euo pipefail
if [[ $# -eq 0 || ${1:-} == --help || ${1:-} == -h ]]; then
  echo "用法: bash scripts/replay_linux_cpu.sh [SSH 选项] 用户@主机或SSH别名"
  exit 0
fi

root=$(cd "$(dirname "$0")/.." && pwd -P)
base=flockcue-g1g2-cpu-20261108.tar.gz
addon=flockcue-session-cpu-addon-20261108.tar.gz
cd "$root/outputs"
shasum -a 256 -c "$base.sha256" "$addon.sha256"
local_dir=$(mktemp -d "$PWD/flockcue-linux-cpu-XXXXXXXX")
remote_dir="flockcue-runs/${local_dir##*/}"
printf '服务器目录 ~/%s，Mac 结果目录 %s\n' "$remote_dir" "$local_dir"
ssh_cmd=(ssh -T "$@")

# The remote path contains only our fixed prefix and mktemp's alphanumeric suffix.
COPYFILE_DISABLE=1 tar -cf - "$base" "$base.sha256" "$addon" "$addon.sha256" |
  "${ssh_cmd[@]}" "cd && umask 077 && mkdir -p flockcue-runs && mkdir '$remote_dir' && cd '$remote_dir' && tar -xf -"

"${ssh_cmd[@]}" "bash -s -- '$remote_dir' '$base' '$addon'" <<'LINUX'
set -euo pipefail
cd "$HOME/$1"
if [[ $(uname -s) != Linux ]]; then echo '目标主机需要是 Linux。' >&2; exit 1; fi
exec > >(tee setup-and-run.log) 2>&1
echo "Linux 架构 $(uname -m)"
sha256sum -c "$2.sha256" "$3.sha256"
tar -xzf "$2"
tar -xzf "$3"
cd flockcue-g1g2
sha256sum -c SHA256SUMS > /dev/null
sha256sum -c SHA256SUMS.session-cpu > /dev/null

export PATH="$PATH:$HOME/.local/bin"
if ! command -v uv >/dev/null; then
  curl -fsSL https://astral.sh/uv/install.sh -o ../install-uv.sh
  UV_UNMANAGED_INSTALL="$PWD/../tools" sh ../install-uv.sh
  export PATH="$PWD/../tools:$PATH"
fi
uv python install 3.11.16
python_bin=$(uv python find --managed-python 3.11.16)
"$python_bin" -c 'import sys, tkinter; assert sys.version_info[:3] == (3, 11, 16)'
for component in agent4drone server controller; do
  lock="$component/requirements.txt"
  if [[ $component == agent4drone ]]; then lock="$component/requirements-dev.txt"; fi
  uv venv --python "$python_bin" "$component/.venv"
  uv pip sync --python "$component/.venv/bin/python" "$lock"
  uv pip check --python "$component/.venv/bin/python"
done
controller/.venv/bin/python -c 'import tkinter, tksheet'
echo "正在运行场景 04cd6269 的正常、重启、超时和服务中断验收……"
PYTHONPATH=scripts agent4drone/.venv/bin/python -m flockcue_session_cpu accept \
  --output tmp/linux-session > ../accept.stdout.json
LINUX

# Only the Linux result comes back. The Mac reference stays on the Mac.
"${ssh_cmd[@]}" "cd && cat '$remote_dir/flockcue-g1g2/tmp/linux-session/verification.json'" \
  > "$local_dir/verification.partial"
mv "$local_dir/verification.partial" "$local_dir/verification.json"
"$root/agent4drone/.venv/bin/python" - "$root" "$local_dir/verification.json" <<'COMPARE'
import json
import sys
from pathlib import Path

root, report = map(Path, sys.argv[1:])
mac = json.loads((root / "docs/results/flockcue-mac-session-20261108/verification.json").read_bytes())
linux = json.loads(report.read_bytes())
for name, expected in mac.items():
    actual = linux[name]
    config = actual["configuration"]
    if config["system"] != "Linux" or actual["scene_sha256"] != expected["scene_sha256"]:
        raise SystemExit(f"{name} 的系统或场景与约定不符，请查看 {report}")
    for key in ("baseline", "source_map_sha256", "python_version"):
        if config[key] != expected["configuration"][key]:
            raise SystemExit(f"{name} 的 {key} 不一致，请查看 {report}")
    for observed, reference in zip(actual["tasks"], expected["tasks"], strict=True):
        for key in ("task_id", "status", "reason", "checker_passed", "checker_leaf_results", "deployment_passed"):
            if observed.get(key) != reference.get(key):
                raise SystemExit(f"{name} 题目 {reference['task_id']} 的 {key} 与 Mac 不同，结果见 {report}")
    print(f"{name}: {actual['scored']}/{actual['planned']} 题获得独立评分，检查通过 {actual['raw_passed']} 题")
print(f"Linux 无卡复验通过，四组逐题结果与 Mac 一致。结果文件 {report}")
COMPARE
