# 在线 GRPO

此目录预留给本项目学生的在线 GRPO 训练入口与配置。目前完成教师决策数据的首批构建，SFT 权重和正式 GRPO 训练仍待实施。

按现行路线，GRPO 从首个 2B SFT 权重开始，在同一 FlockCue Harness 中采样多条真实执行轨迹，用隔离环境的任务检查结果计算奖励。测试场景按完整场景留出，最终比较原始 2B、SFT 和 GRPO 的独立整题通过率。[后训练方案](../../docs/POSTTRAINING_PLAN_CN.md)
