# DeepSeek 教师轨迹 · 2026-11-11 A 折

首遍 FlockCue 600 题中的 12 个完整场景、240 题用于训练侧，另 3 场、60 题用于内部验证。B 折 15 场、300 题固定为学生独立测试集。完整运行记录包含教师在 B 折的表现，学生训练、示范筛选与选模均排除这些场景。本题库有历史开发暴露。[本次 A 折发布角色](audit/scene-roles.json) · [论文全量场景角色](../../original-benchmark/scene-roles.csv) · [逐题取舍](audit/episodes.jsonl)

| 数据 | 用途 |
| --- | --- |
| [public/reviewed-compatible-public-turns.jsonl.gz](public/reviewed-compatible-public-turns.jsonl.gz) | Git 随附的 386 个经审阅的训练侧决策轮，覆盖 110 道题。 |
| `public/training-public-turns.jsonl.gz` | 完整训练侧 3,417 个可重建决策轮，含成功与失败行为。 |
| `public/validation-public-turns.jsonl.gz` | 独立的内部验证包，1,009 个可重建决策轮。 |
| [audit/](audit/) | 封存清单、原报告、题目与轮次索引、证据边界和学生投影。 |
| `raw/` | 重建所需的本地原始证据，含父清单、15 个 A 折场景的题目派发记录、封存摘要、工具执行记录和 HTTP 原件。 |

每个公开记录的 `messages`、`tools` 是当前输入，`target` 是教师实发回复，`post_action_receipts` 在动作执行后才产生。原始报告保留发布时的名称与路径。清单中 `docs/results/…` 所列文件在本包的 `audit/`，`outputs/…` 所列数据在 `public/`。[原发布报告](audit/REPORT_CN.md) · [原始格式](audit/WIRE_FORMAT_CN.md) · [SHA-256 清单](audit/manifest.json)

运行 `python3 datasets/teacher/2026-11-11-fold-a/verify.py` 检查 Git 随附的 23 项。当前工作区额外保存两个完整公开包和原始证据。取回完整包后，用 `--full` 逐一核验全部 25 项。[从原始执行证据重建](../../../posttraining/trajectories/README.md)
