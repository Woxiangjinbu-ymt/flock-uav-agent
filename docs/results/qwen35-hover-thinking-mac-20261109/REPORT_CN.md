# Qwen3.5-2B 开启思考后会不会在宣布完成前执行悬停

**会。在这次 Mac 仿真里，开思考的 Qwen3.5-2B 在第 3 轮向 4 号机执行了独立的 `hover`，仿真返回成功。它第 4 轮查询状态，第 5 轮才首次向用户宣布整项任务完成。** 关思考时，同一题也在第 5 轮宣布完成，但飞行动作只有起飞。[逐轮公开回复、动作与回执](comparison.json)

| 轮次 | 关思考 | 开思考 |
| --- | --- | --- |
| 1 | 查询机队 | 查询机队 |
| 2 | 查询天气 | 4 号机起飞至 12 米，成功 |
| 3 | 4 号机起飞至 12 米，成功 | 向同一架飞机发出 `hover`，成功 |
| 4 | 查询状态，读到 `hovering` | 已收到悬停成功回执，再查询状态 |
| 5 | 宣布整项任务完成 | 宣布整项任务完成 |

开思考组前四轮的公开回复只有工具调用。第 3 轮的 `call_3` 对应仿真请求 12，目标是 `/drones/3d229034/command/hover`。仿真将动作记为成功提交，工具成功回执在 SDK 归档中的序号是 25。第 5 轮回复以 “Task Completed Successfully” 开头，回复结束序号为 38，输入中已包含这份回执。独立评分器读到两组终态均为 12 米、`hovering`，高度与离地两项均通过；两组的差异体现在实际派发的动作。[开思考组的原始仿真日志](../../../experiments/runs/flockcue-qwen35-hover-thinking-mac-20261109/mac-run/trials/mac-qwen35-on/policy/worldlogs/public-api.jsonl) · [原始评分](../../../experiments/runs/flockcue-qwen35-hover-thinking-mac-20261109/mac-run/trials/mac-qwen35-on/evaluation/result.json)

| 本次用量 | 关思考 | 开思考 |
| --- | ---: | ---: |
| 输入 / 输出 token | 47,103 / 365 | 47,518 / 652 |
| 其中输出中的推理 token | 0 | 385 |
| 总 token | 47,468 | 48,170 |
| 回合耗时 | 45.75 秒 | 53.25 秒 |
| 单次请求 P50 / P95（各 5 次，最近秩） | 9.49 / 10.61 秒 | 9.93 / 13.68 秒 |

两组的工具示例和首轮 SDK 请求逐字节相同，实际入模的 token 均通过生成前校验。每组只有一题、一个种子、一次可评分回合。这个结果记录了单题动作顺序的差异；正式 G2 的同题配对成功率仍待测。本次训练、租卡和新增教师调用均为零。[配对验收器](../../../scripts/flockcue_qwen35/verify_thinking_control.py)

## 同题配对条件

两组都使用补全 `input_json` 的起飞工具示例。同一条已见开发指令要求 Drone 4 起飞到 12 米，然后执行 `hover`。关思考组使用已封存回合，开思考组在同一台 M2 Pro Mac 上从原场景单独开始。两组沿用同一份原始模型权重、公开命令编号、31 个工具、逐轮种子、采样参数和独立评分器。任务上限 500 秒，单次请求上限 120 秒；最多 12 次请求、累计 100,000 个 token、每轮最多生成 2,048 个 token。[关思考基线与来源](../qwen35-takeoff-example-mac-20261109/REPORT_CN.md)

运行前从关思考组的首个 SDK 请求重建两种提示。关思考时，官方模板已经替模型写好了思考结束标记，输入为 8,535 个 token；开思考时，模板在思考起始标记后等待模型继续，输入为 8,533 个 token。后续各组读取各自的真实回执。

判定以公开回复和目标机动作的先后为准。要答“是”，须先看到目标机成功起飞，再有该机独立 `hover` 命令的执行和成功回执，且早于第一次向用户表示整项任务完成。读到 `status=hovering`、说“接下来要悬停”或仅完成起飞分别记录。若回合中止且没有完成声明，按实际停因和动作报告。原评分器的高度与离地两项单列。

## 复核与重跑

从项目根目录复核本次两个封存回合时，下面命令只读取归档和分词器。

```sh
set -o pipefail
py=training/.venv-input/bin/python
off=experiments/runs/flockcue-qwen35-takeoff-example-mac-20261109-paired/mac-run
on=experiments/runs/flockcue-qwen35-hover-thinking-mac-20261109/mac-run
PYTHONPATH=scripts:. "$py" -m flockcue_qwen35.verify_thinking_control --preflight "$off"
PYTHONPATH=scripts:. "$py" -m flockcue_qwen35.verify_thinking_control "$off" "$on" | diff - docs/results/qwen35-hover-thinking-mac-20261109/comparison.json
```

使用已准备好的权重与 Mac 模型环境时，以下命令在新目录中运行开思考组。完成后可用 `$out/mac-run` 替换验收器的第二个参数。[权重和环境准备](../qwen35-real-mac-20261109/REPORT_CN.md#在同类-mac-复跑)

```sh
weights="$HOME/.cache/flockcue/Qwen3.5-2B/15852e8c16360a2fea060d615a32b45270f8a8fc"
modelpy="$HOME/.cache/flockcue/venvs/qwen35-mlx-py311/bin/python"
out="$(mktemp -d)/hover-thinking"
PYTHONPATH=scripts:. training/.venv-input/bin/python -m flockcue_qwen35.mac_smoke \
  --takeoff-example --thinking --weights "$weights" --model-python "$modelpy" --output "$out"
```
