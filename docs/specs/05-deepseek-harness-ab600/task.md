# 600 题首遍执行记录

2026-11-11。

| 顺序 | 执行项 | 核对位置 |
| --- | --- | --- |
| 1 | 核实 600 个任务双键、3,705 个叶检查、十月场序、环境源码与已安装依赖。 | [冻结执行计划](../../../experiments/plans/flockcue-600-first-pass.json) |
| 2 | 准备隔离目录，接入当前 FlockCue 策略和 C 版感知，保持原版请求参数、时钟、并发及收费重试行为。 | [批次入口](../../../scripts/run_flockcue_deployment600.py) · [Agent 服务](../../../scripts/flockcue_deployment_agent.py) |
| 3 | 回环教师经真实 HTTP 仿真验证动作、模型历史与评分；官方服务预检确认模型和工具回传。 | [联合验收](../../../scripts/verify_flockcue_deployment600.py) · [登记及摘要](../../results/flockcue-deployment600-20261111/registration.json) |
| 4 | 启动 8 个独立 worker，核对实发请求的模型字段、回执和评分，监测至所有场景收束。 | [实际运行目录](../../../outputs/flockcue-deployment600-20261111/experiments/runs/20261111-134339-flockcue-600-first-pass-d505975c/) |
| 5 | — | [计分入口](../../../scripts/report_flockcue_deployment600.py) · [本批结果目录](../../results/flockcue-deployment600-20261111/) |
