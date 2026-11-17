#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")"
run=$(pwd)
exec 9>train-launch.lock
flock -n 9 || exit 75
exec > >(tee -a "$run/train.log") 2>&1
trap 'rc=$?; date -Is; echo "trainer exit=$rc"; echo "$rc" > "$run/train.exit.tmp"; mv "$run/train.exit.tmp" "$run/train.exit"' EXIT
base=/uav/flockcue-20261110/flockcue-a100
py="$base/operations/venv-inference/bin/python"
parent=/uav/flockcue-sft/runs/20261111-first-pass/output
export PYTHONPATH="$run/source" HF_HUB_OFFLINE=1 HF_HUB_DISABLE_PROGRESS_BARS=1 TOKENIZERS_PARALLELISM=false
cd "$run/source"
sha256sum -c "$run/training-source.sha256"
cd "$run/mix"
sha256sum -c "$run/mix.sha256"
cd "$run"
resume=()
if [[ -f output/run.json ]]; then resume+=(--resume); fi
date -Is
"$py" -u -m posttraining.sft.train \
  --model "$base/models/Qwen3.5-2B" --init-adapter "$parent" \
  --init-adapter-sha256 8735ff75258991c9781625f19a9802d93c4c47bc16065e6ab65bdcca5913007e \
  --dataset "$run/mix/train.jsonl.gz" --output "$run/output" --seed 123 --epochs 5 \
  --gradient-accumulation 2 --learning-rate 1e-4 --lr-schedule cosine --warmup-ratio 0.05 \
  --min-learning-rate 2e-5 --max-grad-norm 1 --checkpoint-every 5 "${resume[@]}"
