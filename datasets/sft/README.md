# SFT 样本

正式首遍的训练样本从[教师轨迹的已审阅决策轮](../teacher/2026-11-11-fold-a/README.md)生成。首版正式 SFT 序列及其损失掩码尚待定稿。

| 数据 | 用途 |
| --- | --- |
| [pilot40-2026-11-11/paired-dataset.json](pilot40-2026-11-11/paired-dataset.json) | 另一次真实两场试采的配对样本。两份本地载荷分别为 `A-keep-reasoning.jsonl` 和 `A-remove-reasoning.jsonl`，各 148 条，SHA-256 在此清单。与正式首遍单独记账。 |
| [dev-hover-2026-11-09](dev-hover-2026-11-09/batch.json) | 4 个已暴露开发回合获得的 16 条格式与筛选样例，供 Qwen3.5-2B 的序列回放与回归验证。 |
| [w3-format-2026-11-03](w3-format-2026-11-03/manifest.json) | 旧 W3 样本契约的 61 条开发样本。冻结加载器校验原文件路径，此处提供导航；调用该加载器时使用[实际封存文件](../../docs/results/agent-samples-student-input-20261103/conversion-run-01/samples.jsonl)。 |

正式训练数据、开发样例与历史试采应分别记录数据来源和覆盖的场景。[试采交付](../../docs/results/deepseek-teacher-pilot40-live-20261111/REPORT_CN.md) · [开发样例报告](../../docs/results/deepseek-sft-dev-examples-20261109/REPORT_CN.md)
