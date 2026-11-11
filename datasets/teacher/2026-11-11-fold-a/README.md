# DeepSeek 教师轨迹 · 2026-11-11 A 折

数据来自 FlockCue 正式 600 题首遍。A 折 12 个完整场景、240 题作为训练侧，另 3 个场景、60 题作为内部验证侧。B 折留作首个学生的折外评测。[300 题归属](audit/episodes.jsonl) · [场景划分](audit/scene-roles.json)

| 文件 | 记录单位与用途 |
| --- | --- |
| [public/reviewed-compatible-public-turns.jsonl.gz](public/reviewed-compatible-public-turns.jsonl.gz) | 随 Git 分发的 386 个训练侧决策轮，来自 110 道题。已核查执行证据和当前 2B 模板长度，可用于随后制作 SFT 数据。 |
| `public/training-public-turns.jsonl.gz` | 本地完整训练侧包，3,417 个可重建决策轮，含成功与失败行为。文件大小和 SHA-256 见清单。 |
| `public/validation-public-turns.jsonl.gz` | 本地内部验证包，1,009 个可重建决策轮。用于检查泛化与选参。 |
| [audit/manifest.json](audit/manifest.json) | 25 个发布文件的登记路径、大小、SHA-256。审阅理由和评分仅在 `audit/` 中。 |
| [audit/reviewed-qwen-projection.jsonl](audit/reviewed-qwen-projection.jsonl) | 从保留轮定位原场景、题目、模型逻辑轮、训练包行与学生 token 检查。 |

每个公开记录含当轮可见的 `messages`、`tools`、实际教师 `target` 和执行后才产生的 `post_action_receipts`。仅前两项可用于构造当轮输入。训练侧和内部验证侧的归档分别存储，数据本身有历史开发暴露。[发布报告](../../../docs/results/flockcue-teacher-trajectories-a-20261111/REPORT_CN.md) · [格式与证据定位](../../../docs/results/flockcue-teacher-trajectories-a-20261111/WIRE_FORMAT_CN.md)

`python3 datasets/teacher/2026-11-11-fold-a/verify.py` 核验 Git 随附的索引、报告、样例和 386 个决策轮。若已取回两份完整包，放入 `public/` 后加上 `--full`；校验会要求全部 25 个封存文件到齐。
