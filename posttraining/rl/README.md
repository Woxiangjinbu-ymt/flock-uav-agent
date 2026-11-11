# 在线 GRPO

首轮在线 GRPO 从可评测的 2B SFT 权重出发，在同一 FlockCue Harness 内采样，用隔离的仿真评分器对整题执行结果计算奖励。训练侧、内部验证和折外评测按[场景角色](../../datasets/teacher/2026-11-11-fold-a/audit/scene-roles.json)区分。当前已完成首批教师数据构建。GRPO 训练器、权重和实测结果待实现。
