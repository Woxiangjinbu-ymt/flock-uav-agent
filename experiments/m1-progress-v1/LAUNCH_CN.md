# M1 阶段启动记录

2026-10-06 15:26（北京时间）已启动 fast100 双臂对照。

- M0：冻结 B1 `cb80fde8269a09795782a6ef046d04c381b3f3c9`。
- M1：`3161fc88c7d4c9646e2190eaa22816fedc02f380`，标签 `m1-progress-state-v1`。
- 每臂 100 题 / 605 项检查，官方 DeepSeek，500 秒，2 个 worker。
- 先 M0 后 M1；M0 完成并核验后由持久任务自动启动 M1。原 B1 的 57 题结果不拼入本轮。
- 首个 M0 attempt：`experiments/runs/20261006-152635-m1-fast100-m0-723f8ec6`。已确认两场启动、模型请求返回和首批原判题结果落盘。
- 持久任务状态：[status.json](jobs/20261006-152635-7ddb4eb4/status.json)；双臂登记：[runs.json](runs.json)。读取这些实时文件判断当前进度，本页只记录启动事实。
- 已更新现有跟进任务，每 10 分钟检查；正常执行时静默，完成或故障时汇报。不会自动补跑中断题或推进 M2/M3。

离线证据见[验收记录](../../docs/results/m1-progress-offline-20261006/verification.json)与[真实轨迹回放](../../docs/results/m1-progress-offline-20261006/README_CN.md)。当前尚无 M1 在线效果结论。
