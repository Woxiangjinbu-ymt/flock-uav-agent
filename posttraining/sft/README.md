# 学生 SFT

[adapter.py](adapter.py)处理 Qwen3.5-2B 的原生消息模板和工具调用。[dev_sample_tokens.py](dev_sample_tokens.py)在相同模板下核对可监督的目标区间、长度与并行工具参数回放。[formatting](formatting/)存放早期 Qwen3 输入格式、独立样本审计以及工具 ID 绑定。开发格式回归沿用已冻结的旧数据清单。

首批可学习的公开决策轮和题目归属见[教师轨迹](../../datasets/teacher/2026-11-11-fold-a/)。后续正式 SFT 数据放入 [datasets/sft](../../datasets/sft/)，训练时使用可核验的示范和训练侧场景。实际生成的样本、训练权重和整题评测在对应实验完成后登记。[当前后训练方案](../../docs/research/posttraining.md)
