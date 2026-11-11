# 后训练

当前学生为 Qwen3.5-2B。先核对真实 DeepSeek 示范，再在同一 FlockCue 运行环境中比较学生基座、SFT 和在线 GRPO 的整题通过率。

| 目录 | 当前内容 |
| --- | --- |
| [trajectories/](trajectories/README.md) | 从真实 HTTP、工具执行和独立审阅表重建 300 题的证据索引和公开教师包。 |
| [sft/](sft/README.md) | 学生原生分词器、工具回执绑定、目标区间掩码与已审阅样本的逐轮 token 核验。 |
| [inference/](inference/README.md) | 固定 2B 基座的权重验证、Mac Metal 和单卡 CUDA 推理。 |
| [rl/](rl/README.md) | 首轮在线 GRPO 的训练边界和当前实现状态。 |

公开包、审计表和划分见[教师数据](../datasets/teacher/2026-11-11-fold-a/README.md)。轨迹重建使用 Python 3.11 标准库。SFT 格式验证使用 [requirements.txt](requirements.txt) 中的依赖。
