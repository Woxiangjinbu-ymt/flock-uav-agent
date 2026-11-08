# Qwen3.5-2B 工具指令的 Mac 仿真验收

2026-11-08，在 Mac arm64 上，用 Qwen3.5-2B 官方分词器、聊天模板和预设回复，让 FlockCue 在开发任务 `c74d7020` 上跑了两个回合。两次均从同一初态开始，由另一个进程按原任务检查器给终态评分。这道已见的悬停题用于工程验证，没有计入冻结 600 题的成绩。[逐回合验收结果](verification.json) · [分词器与模板版本](../../audit/qwen35-2b-input-20261108/summary.json)

| 用例 | 仿真中的结果 |
| --- | --- |
| 完整指令 | 6 轮请求。目标机 `3d229034` 执行 `take_off` 和 `hover`，终态为 12.0 米、悬停。原检查器的两项条件是高度达标和已经离地，均通过。 |
| 截断指令 | 2 轮请求。首轮只查询机队。第二轮先生成完整的起飞指令，再生成悬停指令，在第 64 个 token 处截断。整组拒绝派发，仿真记录为 **0 次飞行动作**。目标机停在地面 0.0 米，原任务检查 0/2，符合这个用例的预期。 |
| 双机倒序回执 | 完整回合中先查询 `3d229034`、再查询 `eedfa187`，两次工具名均为 `get_drone_status`。测试在两份仿真回执完成后交换其进入 SDK 请求的顺序。下一轮先看到 `call_3 → eedfa187`，再看到 `call_2 → 3d229034`。每条的调用参数、返回机号和位置均与仿真接口记录一致。 |

解析器先读完当轮生成的 token，验证结束标记、工具名、参数和全部调用的结构，再把整组交给执行器。回执进入下一轮前，按调用 ID 附上原工具名和参数。独立验收从 SDK 归档重新构造了全部 8 次输入，逐个核对输入和预设输出的 token ID，并核对工具派发日志、仿真动作、终态快照及独立评分。[适配器](../../../scripts/flockcue_qwen35/adapter.py) · [独立验收器](../../../scripts/flockcue_qwen35/verify.py) · [本次原始记录](../../../experiments/runs/flockcue-qwen35-tools-mac-20261108/)

另用封存的同一条 DeepSeek 开发轮核对训练与执行的接口。保留思考、同一提示下立即结束思考、预先关闭思考的目标分别有 252、66、63 个 token。新解析器还原的两次调用均为 `get_weather` 和 `sense_nearby_entities`，顺序和参数与教师记录一致。对这三种目标逐 token 截短，全部 378 个非空且还没生成到结束符的前缀均被拒绝。这一步只在内存中解析历史样例。[复算脚本](replay_teacher_example.py) · [原训练目标的 token 摘要](../../audit/qwen35-2b-input-20261108/sample_mask.json)

## 在 Mac 复跑

在项目根目录，使用现有 Agent、server 和分词器环境。此次为 macOS 15.7.4、Python 3.11.16、Transformers 4.57.3 和 tokenizers 0.22.1。四个固定版本的分词文件需要已在本地缓存，首次准备可依照[文件校验与下载步骤](../../audit/qwen35-2b-input-20261108/REPORT_CN.md#复算)。每次重新运行选一个新目录。

```sh
py=training/.venv-input/bin/python
out="$(mktemp -d)/qwen35-tools"
PYTHONPATH=scripts:. "$py" -m flockcue_qwen35.smoke --output "$out"
PYTHONPATH=scripts:. "$py" -m flockcue_qwen35.verify "$out/cpu-run"
"$py" docs/results/qwen35-tools-mac-20261108/replay_teacher_example.py
"$py" -m pytest -q scripts/tests/test_flockcue_qwen35.py scripts/tests/test_thinking_student.py scripts/tests/test_student_input_v2.py
agent4drone/.venv/bin/python -m agent_harness check
```

本轮 93 项针对新旧输入与解析的回归通过。v5 基线的 93 个源码文件通过校验。上面的复跑会生成新的终态快照，比较时可核对派发的动作、回执归属、停因和两项任务检查。本次记录的运行清单 SHA-256 为 `6acc6ddb5251be362cbd90b438605382599808aeb5d250fe189f848da4da7f9f`。[本次运行清单](../../../experiments/runs/flockcue-qwen35-tools-mac-20261108/cpu-run/manifest.json)

本次真实权重生成、教师调用、GPU 卡时和新增训练样本均为零。Qwen3.5-2B 自主完成任务的成功率以及开关思考的影响，待真实权重实验验证。
