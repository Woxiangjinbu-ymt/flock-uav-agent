# Qwen3.5-2B 在 Mac 上自主执行悬停任务

2026-11-09，未训练、未量化的 Qwen3.5-2B 在 Mac 仿真中完成了两次查询。第三轮认对了飞机和目标高度，生成的起飞参数层级与工具声明不符，执行器停止派发。**本次开发题 0/1 成功**，原检查器的两项条件均未达成，Drone 4 终态为地面 0.0 米、`idle`。本次只跑关思考，G1/G2 和 600 题成功率均待测。[验收摘要](verification.json) · [独立终态评分](../../../experiments/runs/flockcue-qwen35-real-mac-20261109/mac-run/trials/mac-qwen35-off/evaluation/result.json)

| 轮次 | 模型的选择 | 工具执行 | 输入 / 输出 token | 请求耗时 |
| --- | --- | --- | ---: | ---: |
| 1 | 查询机队 `list_drones` | 返回机队信息，含 Drone 4 的 ID | 8,530 / 15 | 7.34 秒 |
| 2 | 查询天气 `get_weather` | 收到天气回执 | 9,182 / 69 | 8.84 秒 |
| 3 | 让 `3d229034` 起飞到 12 米 | 完整生成，参数校验拒绝；飞行动作数为 0 | 9,466 / 92 | 9.51 秒 |

整题用时 25.91 秒，累计输入 27,178、输出 176、总计 27,354 个 token，关思考的推理 token 为 0。三次请求的 P50 为 8.84 秒、P95 为 9.51 秒（最近秩，样本数 3）；逐轮解码约 40–41 token/秒，MLX 报告的最大分配内存约 4.47 GB。模型加载耗时在回合计时之外。[运行记录](../../../experiments/runs/flockcue-qwen35-real-mac-20261109/smoke.json)

## 第三轮为什么停止

模型在 `take_off` 下分别生成了 `drone_id` 和 `altitude`，并正常生成结束标记。发给模型的工具声明要求唯一的外层参数 `input_json`，系统提示给了带该层的例子，但工具自己的说明只展示了内层机号和高度。离线重放保留原飞机、原高度、工具名和其他输出，只补上这一层，原解析器即接受该指令。这次重放没有派发动作，首跑仍按地面终态计分。工具说明的写法是否影响模型的选择，可以在同题用单独的开发对照验证。[原输出、实发工具声明和重放结果](rejected_call.json) · [重放脚本](replay_format.py)

## 实验条件

本次只使用 v5 已见开发题 `c74d7020`，任务要求 Drone 4 起飞到 12 米，随后执行悬停。模型接收原任务、原来的 31 个工具和逐轮公开回执，自己选择工具和参数。每次从原初态开始，用独立评分进程读取仿真终态。原检查器分别检查高度和是否离地，实际有没有执行悬停命令还要核对动作日志。[同题固定回复验收](../qwen35-tools-mac-20261108/REPORT_CN.md)

本次预先选择关思考、单题一次，从 Qwen 官方固定修订加载文本权重，使用 Mac 自带的 M2 Pro 图形处理器。保留原 500 秒回合、120 秒单次请求、12 次请求、100,000 累计 token 的上限，单次最多生成 2,048 token。采用温度 0.6、top_p 0.95、top_k 20，各轮种子由题号与轮次确定。分词和工具解析沿用通过 Mac 验收的版本。生成进程只接收 token ID，用 MLX-LM 返回原始生成 token；控制器在完整解析成功后才派发工具。[官方权重修订](https://huggingface.co/Qwen/Qwen3.5-2B/tree/15852e8c16360a2fea060d615a32b45270f8a8fc) · [模型进程](../../../scripts/flockcue_qwen35/mac_model.py) · [MLX-LM](https://github.com/ml-explore/mlx-lm)

## 在同类 Mac 复跑

从项目根目录执行，先准备既有的 Agent、server 和 tokenizer 环境。模型进程独立使用 Python 3.11.16、MLX 0.32.4、MLX-LM 0.32.2。原有四个分词文件可按已有步骤校验。下面的权重文件为 4,548,221,488 字节；加载前程序会重新核对官方文件的 SHA-256。[分词文件准备步骤](../../audit/qwen35-2b-input-20261108/REPORT_CN.md#复算) · [Mac 模型环境锁](../../../training/requirements-qwen35-mac-mlx.lock)

```sh
rev=15852e8c16360a2fea060d615a32b45270f8a8fc
weights="$HOME/.cache/flockcue/Qwen3.5-2B/$rev"
shard=model.safetensors-00001-of-00001.safetensors
# 本地已有权重时，可直接从环境准备开始。
curl -fL --retry 2 "https://huggingface.co/Qwen/Qwen3.5-2B/resolve/$rev/$shard" -o "$weights/$shard.part" &&
  mv "$weights/$shard.part" "$weights/$shard"
envdir="$HOME/.cache/flockcue/venvs/qwen35-mlx-py311"
uv venv --allow-existing --python agent4drone/.venv/bin/python "$envdir"
uv pip sync --python "$envdir/bin/python" training/requirements-qwen35-mac-mlx.lock
py=training/.venv-input/bin/python
out="$(mktemp -d)/qwen35-real"
PYTHONPATH=scripts:. "$py" -m flockcue_qwen35.mac_smoke \
  --weights "$weights" --model-python "$envdir/bin/python" --output "$out"
PYTHONPATH=scripts:. "$py" -m flockcue_qwen35.verify_mac_model "$out/mac-run"
```

复核本次已归档的开发回合时，在项目根目录运行以下命令。验收器读取最终的 `slots.jsonl`、worker 和 scorer 的退出封存，并将 SDK 请求、token 和仿真动作互相核对。冻结副本内的早期验收器引用了旧的结果路径；这里使用随报告交付的只读验收器，SHA-256 为 `4431d0f47ddae900803650a9219b6dbfc0f35517aa614b1fc506d985b5b340f4`。[验收器](../../../scripts/flockcue_qwen35/verify_mac_model.py)

```sh
set -o pipefail
run=experiments/runs/flockcue-qwen35-real-mac-20261109/mac-run
report=docs/results/qwen35-real-mac-20261109
py=training/.venv-input/bin/python
PYTHONPATH=scripts:. "$py" -m flockcue_qwen35.verify_mac_model "$run" | diff - "$report/verification.json"
"$py" "$report/replay_format.py" "$run" | diff - "$report/rejected_call.json"
```
