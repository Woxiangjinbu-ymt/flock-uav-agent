# 原版 Agent4Drone 同日 200 题对照

2026 年 11 月 11 日从冻结的 600 题中选出 10 场，保留每场全部 20 题及原题序，共 1,303 个叶检查。白天下午的 FlockCue 已在这些任务上完成一次部署。晚间仅新增十月归档的原版 Agent4Drone，使用当天官方 `deepseek-flash`。北京时间 18:05 安排一次正式首遍。[200 道原题](tasks.csv) · [冻结计划](../plans/agent4drone-matched200-v1.json) · [运行状态](../../outputs/agent4drone-matched200-v1/evening-status.json)

## 如何选场

100 题可运行 5 个完整场景，至多覆盖 15 个“任务族 × 难度”组合中的 5 个。200 题覆盖 10 个不同组合。按五档难度各选两个不同任务族的场景，保持三族的场数差不超过一。对 90 种满足条件的布局，依次最大化机队规模和目标数量的取值覆盖，再最小化这两个维度的场数分布与 600 题之间的绝对偏差。每档难度抽 A、B 折各一场，每个任务族中两折的场数最多相差一。通过预定种子、冻结父清单摘要和候选编码的 SHA-256 破同分。选择过程只读取基准结构和冻结折表，已有成绩、用量和轨迹在名单锁定后关联。[选择器](select.py) · [元数据、摘要及未覆盖组合](selection.json)

| 任务族 | 场数 | 题数 | 入选难度 |
| --- | ---: | ---: | --- |
| 区域巡逻 | 3 | 60 | Easy、Intermediate、Moderate |
| 区域搜索 | 3 | 60 | Easy、Hard、Extreme |
| 目标分配 | 4 | 80 | Intermediate、Moderate、Hard、Extreme |

每档难度 40 题，A、B 折各 100 题。覆盖母集全部 7 种机队规模（3 至 30 架）及 9 种目标数量（3 至 100 个）。每个选中组合仅有一个场景。该设计优先保留结构覆盖，整体结论以这 200 题为分母。

## 实验条件与如何读分数

- 三组按同一场景 ID 和任务 ID 配对。锁定后查询，十月原版通过 **168/200（84%）**，11 月 11 日下午 FlockCue 通过 **182/200（91%）**。预登记主比较为白天 FlockCue 与晚间原版在这 200 题的首次独立通过数、逐题增减、按场景和任务族的差异。若晚间原版通过 N 题，则白天 FlockCue 比同日原版多过 182 − N 题，原版跨期变动为 N − 168 题。十月这 200 题含 6 个终止性服务故障，另看两期均按检查计分的题位。[600 题同键记录](../../docs/results/flockcue-deployment600-20261111/tasks.csv)
- 原版的 Agent、模型客户端、SDK 重试、仿真、独立评分、场内连续状态与共享黑板采用十月源码及依赖，8 个并发场景、每题 500 秒。逐题分母、超时与服务错误沿用同一首次部署规则。仅在现有 HTTP 响应观测位置增加返回模型、指纹和推理 token 旁录。记录预检与实际任务的服务指纹，以及时延差异对固定时间窗口的可能影响。每个系统沿自己的场内动作继承状态。[批次入口](../../scripts/run_agent4drone_matched_subset.py) · [正式 FlockCue 参照](../../docs/results/flockcue-deployment600-20261111/REPORT_CN.md)
将同场实耗放回原序 8 个工位，两个历史批次的墙钟估算分别为 91 和 64 分钟。[官方价格和时段](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) · [十月计量](../../docs/results/baseline-deployment-20261005/tasks.csv) · [当日 FlockCue 计量](../../docs/results/flockcue-deployment600-20261111/calls.csv)

冻结计划 SHA-256 为 `d1c252e1f377c8c79a612063594451f7d553960dcea67474ba7b16a9711b7ec3`。在仓库根目录执行 `python3 experiments/agent4drone-matched200-v1/select.py` 可离线验证选场、顺序和全部检查。晚间进程使用本机防空闲睡眠，启动后检查预检、原始部署与最终对照报告的退出状态。[单次调度入口](run_evening.py) · [运行日志](../../outputs/agent4drone-matched200-v1/evening.log)
