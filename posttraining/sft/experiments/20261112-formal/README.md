# 2026-11-12 续训与选模脚本

本目录保存 A100 实际执行的六个文件及当时的 `execution.sha256`。相对本目录执行 `shasum -a 256 -c execution.sha256` 可逐一核对。启动脚本含归档时的 Linux 部署路径。完整运行目录、配套输入和环境版本见[现场归档](../../../../artifacts/README.md)和[实际训练配置](../../../../datasets/results/sft/continuation/run.json)。

| 文件 | 在该次实验中的作用 |
| --- | --- |
| [protocol.json](protocol.json)、[train.sh](train.sh) | 以首轮权重初始化，固定五个追加 epoch 的数据、优化参数与选模规则 |
| [after_training.sh](after_training.sh)、[evaluate_adapter.sh](evaluate_adapter.sh) | 依次验证五个检查点，保存每场执行回执和原题评分 |
| [score_targets.py](score_targets.py)、[summarize_validation.py](summarize_validation.py) | 计算固定训练样本 loss，按完整分母复核正式整题结果并生成选模报告 |

训练器入口位于 [train.py](../../train.py)，模型服务位于 [posttraining/inference](../../../inference/)。暂停期间可使用[主表复核命令](../../../../datasets/results/sft/README.md)离线读取已有结果。
