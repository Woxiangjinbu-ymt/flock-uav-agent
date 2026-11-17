#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")"
run=$PWD
exec 9>post-training.lock
flock -n 9 || exit 75
exec > >(tee -a "$run/post-training.log") 2>&1
trap 'rc=$?; echo "$(date -Is) evaluation queue exit $rc"; echo "$rc" > "$run/post-training.exit.tmp"; mv "$run/post-training.exit.tmp" "$run/post-training.exit"' EXIT
base=/uav/flockcue-20261110/flockcue-a100
py="$base/operations/venv-inference/bin/python"
first=/uav/flockcue-sft/runs/20261111-first-pass
backup=/mnt/flockcue/20261112-formal
export PYTHONPATH="$run/source" HF_HUB_OFFLINE=1 HF_HUB_DISABLE_PROGRESS_BARS=1 TOKENIZERS_PARALLELISM=false
backed_up=0
backup_completed_epochs() {
  local n folder
  for ((n=backed_up+1; n<=5; n++)); do
    folder="$run/output/epoch-$n"
    [[ -s "$folder/summary.json" ]] || return 0
    if mountpoint -q /mnt; then
      if mkdir -p "$backup/epoch-$n" && cp "$folder"/{adapter.safetensors,adapter_config.json,summary.json} "$backup/epoch-$n/" &&
        (cd "$folder" && sha256sum adapter.safetensors adapter_config.json summary.json) >"$run/epoch-$n.sha256" &&
        (cd "$backup/epoch-$n" && sha256sum -c "$run/epoch-$n.sha256"); then
        echo "$(date -Is) epoch $n weights backed up"
      else
        echo "$(date -Is) could not copy epoch $n to /mnt; primary weights are in $folder"
        return 0
      fi
    fi
    backed_up=$n
  done
}
echo "$(date -Is) waiting for the five-epoch trainer"
while [[ ! -s train.exit ]]; do
  backup_completed_epochs
  sleep 30
done
if [[ "$(cat train.exit)" != 0 || ! -s output/summary.json ]]; then
  echo "Training stopped. See train.log and output/progress.json."
  exit 1
fi
backup_completed_epochs
mkdir -p validation probes
probes=("$run/parent-probes.json")
for n in 1 2 3 4 5; do
  adapter="$run/output/epoch-$n"
  stage="$run/validation/epoch-$n"
  probe="$run/probes/epoch-$n.json"
  echo "$(date -Is) evaluating epoch $n"
  bash "$run/evaluate_adapter.sh" "$adapter" "$stage"
  if [[ ! -s "$probe" ]]; then
    "$py" -u "$run/score_targets.py" --model "$base/models/Qwen3.5-2B" --adapter "$adapter" \
      --first "$first/data/train.jsonl.gz" --closing "$run/source/datasets/sft/qwen35-2b-closing-repair/train.jsonl.gz" \
      --output "$probe" || echo "Diagnostic probe failed for epoch $n, see above."
  fi
  if [[ -s "$probe" ]]; then probes+=("$probe"); fi
done
probe_flags=()
if [[ "${#probes[@]}" = 6 ]]; then probe_flags=(--probes "${probes[@]}"); fi
"$py" "$run/summarize_validation.py" --run "$run" "${probe_flags[@]}"
if mountpoint -q /mnt; then
  if mkdir -p "$backup/selection" && cp "$run/selection/"{summary.json,REPORT_CN.md} "$backup/selection/" &&
    cp "$run"/{training-commit.txt,validation-commit.txt,protocol.json} "$backup/"; then
    echo "$(date -Is) selection and protocol copied to $backup"
  fi
fi
cat "$run/selection/REPORT_CN.md"
