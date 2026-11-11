# 原部署协议 600 题验收记录

2026-11-11。以下项目对应已批准方案的派发条件及收束交付。

- [x] 原序 30 场、600 个唯一席位和 3,705 个叶检查匹配十月封存；同题历史首次通过 506 题。[原计划](../../../experiments/plans/flockcue-600-first-pass.json)
- [x] 归档调度器的并发、超时、黑板隔离与原评分回归 5/5 通过。[原测试日志](../../results/flockcue-600-original-protocol-preflight-20261111/october-runner-tests.txt)
- [x] 固定回复通过实际部署 Agent 和真实 HTTP 仿真。C 版感知得到 10 架机的回执，执行子程序完成三个动作，最终原检查 2/2，worker 正常退出。[联合验收](../../results/flockcue-deployment600-20261111/joint-check.json)
- [x] 官方网关成功返回两次预检，正式运行显式使用 deepseek-flash；任务模型的温度、步数、重试与原协议一致。[正式登记](../../results/flockcue-deployment600-20261111/registration.json)
- [x] 30 场封存并验证，600 道题均独立重放原检查树。56 题未通过，终止性服务错误为 0。[首遍评分](../../results/flockcue-deployment600-20261111/REPORT_CN.md)
- [x] 同题配对为 494 题都过、50 题仅 FlockCue 过、12 题仅原版过、44 题都失。[逐题表](../../results/flockcue-deployment600-20261111/tasks.csv) · [两期用量与耗时](../../results/flockcue-deployment600-20261111/RESOURCE_COMPARISON_CN.md)
- [x] 62 道不一致题关联两期执行、原检查与子程序调用，原试采两场另行核查请求限制与状态继承。[不一致题轨迹](../../results/flockcue-deployment600-20261111/MECHANISMS_CN.md) · [焦点 40 题](../../results/flockcue-deployment600-20261111/FOCAL40_CN.md)
