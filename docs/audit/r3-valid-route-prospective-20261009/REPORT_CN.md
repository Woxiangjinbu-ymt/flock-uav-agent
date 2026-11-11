# R3 下一步：独立可执行航线集的生成与验证

日期：2026-10-09。状态：**离线验证及四次在线配对均完成**。这批任务是新构造的机制挑战集，不能计入原 MultiUAV benchmark。在线结果与逐项分析见 [结果报告](../../results/r3-valid-route-20261009/REPORT_CN.md)。

## 为什么做这一步

前次审计发现，原题中的一些指定坐标落在服务端无法进入的障碍区域；这些题目即使 R3 正确发现漏点，R2 和 R3 都没有一条可完成原检查的执行路径。本轮先独立构造并冻结**确实存在合法执行路径**的新题，再测试 R3 的“剩余航点反馈”能否带来行为与分数变化。任务集选择规则在模型调用前写入 [PROTOCOL_CN.md](PROTOCOL_CN.md)。

## 已完成的离线证据

| 证据 | 结果 |
| --- | --- |
| 候选种子 | 固定流 `2026100901`–`2026100912`，顺序生成；前 2 个即通过，拒绝 0 个；没有按模型表现挑题 |
| 场景 | `50f133e6`、`9d63875d`；各 1 道双无人机航线题，2 架无人机各 12 个坐标，26 个原检查叶节点 |
| 离线合法性 | 每段原服务端障碍检查 1 米安全余量通过；两机单条路线分别约 2.63/2.97 km、2.78/2.75 km；估算电量消耗均 <17% |
| 原服务端试飞 | 两个场景的 `land_all`/`charge_all` 准备通过；各机 `take_off`、一次原生 `move_along_path` 到达全部 12 点、`land` 成功 |
| 原检查器 | 每场景 26/26 叶检查为真、整题通过；这是人工指定可行路径的**存在性证据**，不是 agent 成绩 |
| R3 适用范围 | 严格语法解析两题均返回 `recognized_coordinate_routes`，每题识别 2 架无人机 × 12 点 |
| 可复现性 | 重新按两个固定种子生成，场景 SHA-256、场景 ID、任务 ID 均与封存清单一致 |

原始数据和每题哈希见 [manifest.json](../../../experiments/r3-valid-route-v1/manifest.json) 与 [data](../../../experiments/r3-valid-route-v1/data)。原命令回执和检查树见 [server-dry-run.json](../../../experiments/r3-valid-route-v1/server-dry-run.json)。生成器为 [build_valid_route_scenes.py](../../../experiments/analysis/build_valid_route_scenes.py)，服务端试飞脚本为 [validate_valid_route_scenes.py](../../../experiments/analysis/validate_valid_route_scenes.py)。

## 已封存的在线比较

[frozen/study.json](../../../experiments/r3-valid-route-v1/frozen/study.json) 固定了两题共 52 个检查、R2 的 commit `69dd34c067ec97b746db5180684594c546a35172`、经审查的 R3 patch、同一 DeepSeek 官方 `deepseek-flash` 配置、500 秒单题窗口、原检查器、无自动模型重试和独立场景启动。每道题只有一个任务，因此每个臂均从相同原始 JSON 和空 blackboard 开始。预先指定运行次序：

1. `50f133e6`：R2 → R2＋R3。
2. `9d63875d`：R2＋R3 → R2。

对比时必须逐题报告整题通过、26 个检查叶、首个漏点与原命令回执、耗时、模型调用和 token、超时。两题只能作机制探索；若 R2 两题均通过，就没有可救回的对照失败，不应把 R3 同分解释为增益。

## 在线阻断与保存的记录

首次 `50f133e6` R2 启动在端口绑定时遇到本地沙箱 `PermissionError: [Errno 1] Operation not permitted`。当时**没有服务进程、模型请求或评分**。完整失败尝试留在 [attempt manifest](../../../experiments/runs/20261009-193949-valid-route-50f133e6-control-d916793e/manifest.json)，其登记另存于 [infrastructure-failures.json](../../../experiments/r3-valid-route-v1/infrastructure-failures.json)，并从首次可评分尝试的队列移出。单独的本地端口探针在放宽沙箱后成功。

随后正式在线运行请求被自动审批拒绝。审批理由是：虽已授权配对实验，但尚未明确授权把这两道本地场景和任务上下文发往具体外部模型提供方 `api.deepseek.com`。当时没有绕过审批或调用模型。用户在下一轮明确确认该目的地与数据外发后，按上述封存顺序运行四次首次可评分尝试。[runs.json](../../../experiments/r3-valid-route-v1/runs.json) 中四个记录均为 `completed_and_verified`；两题的 R2 和 R3 均各通过 26/26 检查。

## 解释边界

这批题经过“存在合法路线”筛选，不能代表原 1,500 题的难度分布。服务端试飞使用已知路线，不能替代 agent 自主完成任务。四次在线运行说明 R3 的路线证据机制可以识别并追踪可执行长航线，但**没有显示整题或检查分数增益**。两臂从同一场景 JSON、相同起始无人机状态启动；环境模拟在任务前出现轻微随机天气漂移，因此任务启动时世界状态并非逐字段完全一致。两题样本不足以估计泛化效果。
