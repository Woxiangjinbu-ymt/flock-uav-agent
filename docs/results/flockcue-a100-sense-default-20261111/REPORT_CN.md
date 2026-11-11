# A100 上允许无参感知的 29 题整题复测

2026-11-11。Qwen3.5-2B 开思考独立成功 **1/29**，关思考 **0/29**。G1 **未通过**，G2 **未通过**。共恢复 58 份终态运行原 checker，其中 196 个叶检查的完整结果与封存评分一致。服务器与 Mac 独立重评分文件逐字节一致。[预登记口径](PROTOCOL_CN.md) · [逐题配对](PAIRS_CN.md) · [门槛及证据](gates.json)

## 门槛判定

- G1 的开臂成功 1 题，可归因执行失败 25 题，另有 2 题达到预算上限、1 个失败的任务归因未知。两臂的 58 题位均已独立评分。门槛分别为至少 3 个成功和 3 个可归因执行失败。
- G2 配对净增 +1 题，覆盖 1 个净改善场景，缺对 0。门槛分别为净增至少 3 题、至少两个净改善场景。没有触发冻结的开臂超时或上下文偏倚条件。
- G3 沿用原实测证据，状态为通过。40 题付费教师试采待用户决定。本批教师调用 0 次、训练步数 0。[G3](../thinking-events-g3-20261108/REPORT_CN.md) · [试采方案](../../specs/04-deepseek-pilot40/plan.md)

| 父场景 | 均成功 | 仅开成功 | 仅关成功 | 均失败 | 缺对 | 净增 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `4acc59187dc88f05` | 0 | 0 | 0 | 6 | 0 | +0 |
| `b21d0cbb1b3ff556` | 0 | 0 | 0 | 6 | 0 | +0 |
| `b441ff645d01eac2` | 0 | 0 | 0 | 5 | 0 | +0 |
| `e2c0529e33a1ba3d` | 0 | 1 | 0 | 5 | 0 | +1 |
| `ed5f1545d9fddb96` | 0 | 0 | 0 | 6 | 0 | +0 |

## 感知默认值和动作

上机前，Mac 在 29 个冻结初态比对无参形式与显式 `input_json={}` 的公开请求、回执、黑板及终态。实测共有 225 笔模型请求，225 笔有实际输入 ID 可逐个重建，223 笔有原始生成可用冻结分词器重放。严格解析后的整组中出现 31 个无参感知调用，其中 31 个实际进入工具，31 个能按模型轮次与调用 ID 核对公开回执。只有该工具的空参数对象在执行边界被补为显式空对象。[Mac 等价实测](../flockcue-sense-default-mac-20261111/REPORT_CN.md) · [逐调用核验](intervention-audit.json)

模型传输层的 169 个成功消息（含 usage）都与 SDK 收到的响应原文相符，另 56 次未返回可执行决策。31 次无参感知均取得真实机队与附近实体回执，总计 122 个公开 GET。[执行与回执核验](SDK-receipts.json)

工具参数导致的回合终止合计从 B 的 55/58 降至 C 的 52/58。感知工具参数终止由 33 减至 1 个，其他工具参数终止由 22 增至 51 个。C 中最多的是 `navigate_to`（25 个），其次是 `move_along_path`（9 个）及 `navigate_to_and_sense`（6 个）。这些是各回合最后一次实际生成的首个错误工具，后续轨迹随已执行动作而变化。

上一批使用 B 说明，开 1/29、关 0/29。与其逐题比较，开启臂有 1 题从失败变成功、1 题从成功变失败；关闭臂相应为 0 题、0 题。本次使用已暴露开发题，每题每臂一次冻结种子。[B 整题报告](../flockcue-a100-g1g2-retest-20261111/REPORT_CN.md)

## 用量与耗时

| 条件 | 臂 | 完整回合思考 token（回合数） | 完整回合总 token（回合数） | 请求时延 P50 / P95 秒（请求数） | 回合时延 P50 / P95 秒（回合数） |
| --- | --- | ---: | ---: | ---: | ---: |
| B | 开 | 11,903（27/29） | 867,606（27/29） | 5.80 / 16.69（83） | 16.07 / 59.02（29） |
| B | 关 | 0（29/29） | 732,150（29/29） | 3.05 / 8.95（76） | 6.94 / 26.40（29） |
| C | 开 | 16,447（27/29） | 1,236,850（27/29） | 6.33 / 26.43（113） | 30.76 / 55.92（29） |
| C | 关 | 0（29/29） | 1,187,175（29/29） | 3.95 / 14.11（112） | 23.60 / 32.84（29） |

| C 停因 | 开 | 关 |
| --- | ---: | ---: |
| `malformed_thinking` | 2 | 0 |
| `token_budget` | 2 | 0 |
| `tool_arguments` | 23 | 29 |

单张 A100 40 GB、BF16、batch=1。逐次请求未取得 usage 时保留缺失，表内 token 为完整计量回合的已知合计。[原始运行证据](../../../outputs/sense-default-retest-20261111/server/run) · [服务器封存清单](../../../outputs/sense-default-retest-20261111/server/payload.sha256)

## Mac 复算

从项目根目录读取已封存运行，两个命令分别复算原检查树并重放学生输入与生成。结果写到运行目录以外。通过 SHA-256 清单核对的文件数量及远端、Mac 上的摘要均记录在[回传核验](../../../outputs/sense-default-retest-20261111/verification/transfer.json)。

```sh
run=outputs/sense-default-retest-20261111/server/run
agent4drone/.venv/bin/python scripts/report_sense_retest.py --run "$run" --g3 docs/results/thinking-events-g3-20261108/run-01/g3.json --world-python "$PWD/agent4drone/.venv/bin/python" --output /tmp/flockcue-default-gates.json
~/.cache/flockcue/venvs/qwen35-mlx-py311/bin/python scripts/audit_sense_default_retest.py --run "$run" --baseline outputs/sense-retest-20261111/server/run --output /tmp/flockcue-default-intervention.json
```
