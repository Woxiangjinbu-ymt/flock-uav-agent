# 一处工具示例对 Qwen3.5-2B 悬停任务的影响

2026-11-09，补全起飞工具的示例后，Qwen3.5-2B 在同一题上首次生成了可执行的起飞指令。Drone 4 从地面升至 12 米。模型随后查询状态，看到 `hovering` 就宣布完成。仿真中实际派发的飞行动作只有 `take_off`，题目另外要求的 `hover` 调用仍缺失。[配对验收数据](comparison.json)

| 观察项 | A · 原示例 | B · 完整示例 |
| --- | --- | --- |
| 第三轮的 `take_off` 参数 | 直接传机号和高度，解析器拒绝 | 机号和高度在 `input_json` 内，解析与执行成功 |
| 目标机的终态 | 0 米，`idle` | 12 米，`hovering` |
| 实际飞行动作 | 无 | 起飞一次，随后查询状态 |
| 原检查器（高度、离地） | 0/2 | 2/2 |
| 按题面依次执行起飞和悬停 | 未完成 | 未完成 |
| 请求数、回合耗时 | 3 轮，25.91 秒 | 5 轮，45.75 秒 |
| 输入 / 输出 / 总 token | 27,178 / 176 / 27,354 | 47,103 / 365 / 47,468 |
| 请求 P50 / P95（最近秩） | 8.84 / 9.51 秒，3 次 | 9.49 / 10.61 秒，5 次 |

两组前两轮都先查机队、再查天气。一直到生成第三轮起飞指令之前，两组发送的消息与工具回执逐字相同，模型输入的唯一内容差异仍是那条工具示例。B 的第四轮调用 `get_drone_status`，第五轮直接作答。系统提示原本已要求独立的悬停回执；模型凭起飞后的状态判断完成。两组推理 token 均为 0，本次没有训练、租卡或调用教师。这个结果只涵盖一题、一个种子。[B 的原始模型请求与回复](../../../experiments/runs/flockcue-qwen35-takeoff-example-mac-20261109-paired/mac-run/trials/mac-qwen35-off/policy/modelio/) · [B 的终态评分](../../../experiments/runs/flockcue-qwen35-takeoff-example-mac-20261109-paired/mac-run/trials/mac-qwen35-off/evaluation/result.json)

## 对照设计

基线 A 使用已封存的首次运行。B 从同一初态重新运行一次，同为已见开发题 `c74d7020`。指令要求 Drone 4 起飞至 12 米，然后执行悬停。运行前固定模型为本项目尚未微调、未经量化的官方 Qwen3.5-2B，关闭思考，沿用 A 的逐轮种子、采样设置、工具顺序、公开提示、原任务检查器和请求预算。[基线与逐轮记录](../qwen35-real-mac-20261109/REPORT_CN.md)

B 的唯一实验变量是模型看到的 `take_off` 工具说明中的例子。原示例确实有两层花括号。新示例在两个左括号之间插入 `"input_json": `，示范机号和高度都沿用原值。

```text
A   Example: {{"drone_id": "drone-001", "altitude": 15.0}}
B   Example: {"input_json": {"drone_id": "drone-001", "altitude": 15.0}}
```

这一处编辑同时让示例成为合法 JSON，并显示了调用所需的外层参数。对照测量两者合在一起的效果。系统提示原来就有完整示例。运行前用官方模板和分词器重建 A 的首轮输入，8,530 个 token 与归档逐项一致。按上述变更生成的 B 输入为 8,535 个 token。

判定分两层记录。首先看模型有没有生成可接受的起飞调用、仿真是否将其施加到目标机，再查目标机是否实际收到并执行 `hover`。原评分器单独检查高度与离地两项。每组只有一次开发回合，按原顺序保留所有动作和停止原因。

## 首轮输入校验

首次准备 B 时，实际提示为 8,533 个 token。逐字段对照发现仿真为本次任务分配了新 UUID，它进入了系统提示的执行恢复记录。校验在调用模型生成之前中止；这次工程尝试保留为未评分，生成次数和飞行动作数均为零。随后在 B 的独立运行目录中，让同一任务从创建开始复用 A 的公开编号，避免这个随机差异影响决策。[拦截记录](../../../experiments/runs/flockcue-qwen35-takeoff-example-mac-20261109/mac-run/first-prompt-check.json) · [未评分账本](../../../experiments/runs/flockcue-qwen35-takeoff-example-mac-20261109/smoke.json)

实际运行的 B 在首次生成前通过校验，8,535 个 token 的摘要、提示文本摘要和种子均与预期一致。事后核对了首个实发 SDK 请求的全部字段，以及后续各轮的工具声明和调用设置。A/B 使用同一模型文件摘要、同一初态和同一原评分器。[B 的首轮核验](../../../experiments/runs/flockcue-qwen35-takeoff-example-mac-20261109-paired/mac-run/first-prompt-check.json) · [独立配对验收器](../../../scripts/flockcue_qwen35/verify_example_control.py)

## 复核和复跑

从项目根目录执行。前两条 Python 命令只读原始记录，无需加载模型权重。

```sh
set -o pipefail
py=training/.venv-input/bin/python
a=experiments/runs/flockcue-qwen35-real-mac-20261109/mac-run
b=experiments/runs/flockcue-qwen35-takeoff-example-mac-20261109-paired/mac-run
PYTHONPATH=scripts:. "$py" -m flockcue_qwen35.verify_example_control --preflight "$a"
PYTHONPATH=scripts:. "$py" -m flockcue_qwen35.verify_example_control "$a" "$b" | diff - docs/results/qwen35-takeoff-example-mac-20261109/comparison.json
```

使用首次运行的权重、分词器和 Mac 模型环境时，下面的命令在新目录再跑一次 B。结束后可将 `$out/mac-run` 作为验收器的第二个参数。[环境与权重准备](../qwen35-real-mac-20261109/REPORT_CN.md#在同类-mac-复跑)

```sh
weights="$HOME/.cache/flockcue/Qwen3.5-2B/15852e8c16360a2fea060d615a32b45270f8a8fc"
modelpy="$HOME/.cache/flockcue/venvs/qwen35-mlx-py311/bin/python"
out="$(mktemp -d)/takeoff-example"
PYTHONPATH=scripts:. training/.venv-input/bin/python -m flockcue_qwen35.mac_smoke \
  --takeoff-example --weights "$weights" --model-python "$modelpy" --output "$out"
```
