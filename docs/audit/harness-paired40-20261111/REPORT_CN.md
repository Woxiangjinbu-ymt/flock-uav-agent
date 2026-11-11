# 同一批 40 题的 Agent4Drone 与 FlockCue 历史配对

2026-11-11。比较十月原版 Agent4Drone 首次部署和十一月 FlockCue 教师试采。两批采用相同的原题、场内顺序和整题检查树，模型别名均为 `deepseek-flash`。下表是这两次实际部署的差额。[逐题配对 CSV](tasks.csv) · [原版评分口径](../../results/baseline-deployment-20261005/REPORT_CN.md) · [教师试采口径](../../results/deepseek-teacher-pilot40-live-20261111/PROTOCOL_CN.md)

| 原场景 | 十月通过 | 十一月通过 | 都过 | 旧过新失 | 旧失新过 | 都失 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 巡逻，5 机，04cd6269 | 19/20 | 16/20 | 15 | 4 | 1 | 0 |
| 搜索，10 机，ef36e27a | 18/20 | 10/20 | 10 | 8 | 0 | 2 |
| 合计 | 37/40 | 26/40 | 25 | 12 | 1 | 2 |

这两场净少过 11 题，整题成功率从 92.5% 降至 65.0%，差 −27.5 个百分点。十一月终态通过的 26 题中有 3 题在题初已经满足原检查。整题分数按同一原检查保留这三题；题初到终态的变化和人工审阅后的训练样本数各有独立含义。两场均有历史暴露，而且每场 20 题连续继承世界。只有巡逻和搜索在此有观测。[题初及终态复算](../../results/deepseek-teacher-pilot40-live-20261111/SCORES_COST_CN.md)

## 12 道退步题在何处停止

| 十一月的终止原因 | 退步题数 | 对应场景和原序号 |
| --- | ---: | --- |
| 达单题累计 200,000 token，下一次请求未发 | 6 | 巡逻 7、10、16；搜索 5、6、12 |
| 已派发 12 次模型请求，下一次未发 | 3 | 巡逻 3；搜索 4、7 |
| 下一请求体超过 200,000 字节，本地拒发 | 2 | 搜索 10、14 |
| 供应商以输出长度耗尽结束，缺可执行动作 | 1 | 搜索 19 |

**同样是 500 秒，旧成功轨迹实际可以走得更远。** 按原版每题的请求顺序，从零累计已返回的 usage。在准备发送下一次请求时检查是否已发 12 次或已用至少 200,000 token。12 道退步题的旧轨迹总量全部超过 200,000 token，11 道会失去一个或多个后续请求。例外是搜索第 4 题，原版在最后一笔响应才跨过 200,000。两场 37 道旧成功里共有 15 道会被这两条规则截短。扩大到冻结 600 题，506 道旧成功里有 281 道会被截短。此计算衡量旧轨迹还能否按原样继续；成功究竟在第几个动作达成，需要当时的世界状态评分。[十月逐请求遥测及题目到作业的映射](../../../experiments/runs/20261003-143255-full-5c501118/) · [十一月派发限制](../../specs/04-deepseek-pilot40/plan.md)

**长回执和思考分别挤占了两种不同的预算。** 搜索第 10、14 题被拒的下一请求分别为 201,918 和 220,465 字节。其中历次工具回执正文按 UTF-8 累计为 133,000 和 123,911 字节，单条最大的回执均来自感知工具。正文计数没有包括外层 JSON 的编码开销。搜索第 19 题第 7 次回复的 8,192 个输出 token 全部用于思考，工具调用和最终正文均为空。巡逻第 3 题则在完成 4/5 个航点后用尽 12 次请求，原版用了 15 次。扩大限额或压缩回执能挽回几题仍要实测。[搜索第 10 题请求](../../../outputs/deepseek-teacher-pilot40-live-20261111/run/sessions/ef36e27a/tasks/10-c7a8edb7/provider-wire/turn-09.request.json) · [第 14 题请求](../../../outputs/deepseek-teacher-pilot40-live-20261111/run/sessions/ef36e27a/tasks/14-50dcb2b9/provider-wire/turn-07.request.json) · [第 19 题回复](../../../outputs/deepseek-teacher-pilot40-live-20261111/run/sessions/ef36e27a/tasks/19-3ca9a106/provider-wire/turn-07.response.json) · [巡逻第 3 题评分](../../../outputs/deepseek-teacher-pilot40-live-20261111/run/sessions/04cd6269/tasks/03-a377d715/score.json)

**子程序的直接执行集中在两道成功题。** 核对 358 份教师回复，模型只在搜索第 1、20 题显式调用了 `run_harness`，两题均通过。第 20 题结束前还触及了 token 上限。全批 526 个模型工具调用都找到对应回执。子程序执行出错或回执丢失均缺乏解释这 12 次退步的直接证据。工具说明占用多少输入、自动附加反馈是否帮助下一步决策、重复查询是否值得，适合在相同题初状态做单因素对照。[逐工具审计](../../results/deepseek-teacher-pilot40-live-20261111/TOOL_RECEIPT_METHOD_CN.md) · [搜索场逐轮工具记录](../../../outputs/deepseek-teacher-pilot40-live-20261111/audit/tool-receipts-ef36e27a.json)

## 跨月比较的边界

- 十月归档客户端发送 temperature=0.1；十月预检与十一月 358 份实际回复返回的服务指纹不同。十一月显式使用 thinking enabled/high。十月两场缺少可逐请求证明后台版本相同的指纹证据。指纹变化对任务成功率的影响未知。[十月服务预检](../../../experiments/runs/20261003-143255-full-5c501118/gateway_preflight.json) · [十一月响应汇总](../../results/deepseek-teacher-pilot40-live-20261111/summary.json)
- 两场首题的机队平面位置分别一致。比较原版题初快照与 FlockCue 准备完成时的同机 ID 坐标，40 题中有 30 题至少一架机的 XY 已不同。例如巡逻第 10 题中 Drone 1 分别从 (600,150) 和 (138,613) 开始。整场成绩包含这种累积影响；判断某一回合是哪个机制导致失败，需要在同一保存状态比较。[十月巡逻场原件](../../../experiments/runs/20261003-143255-full-5c501118/04cd6269/) · [十一月巡逻场原件](../../../outputs/deepseek-teacher-pilot40-live-20261111/run/sessions/04cd6269/)

## 配对表复核方法

1. 十月逐题 CSV 取 `model=deepseek`、`cohort=first_attempt_no_replay`，用场景 ID 与任务 ID 连接十一月离线复算的 `terminal.passed`。逐题核序。二元结果用 1 和 0 表示。两场 80 棵题初、终态检查树的 372 个叶检查已按冻结快照复算。[十月逐题原表](../../results/baseline-deployment-20261005/tasks.csv) · [十一月独立复算明细](../../../outputs/deepseek-teacher-pilot40-live-20261111/audit/rescore-all.json)
2. 十月取每场的 `<task_id>.job.json` 对应作业，按 `agent-traces/reproduction-metrics.jsonl` 中 HTTP 事件顺序累计。两场每次已发请求均有成功回执和 usage，分题总数、总 token 与原表一致。`oct_http_calls_after_pilot_limits` 是受上述两条停线影响的历史后续请求数，未模拟请求体积、单请求时长或输出长度限制。十一月只统计实际派发请求的 usage。
3. `same_fleet_xy_before_task` 仅比较题初全部同机 ID 的 (x,y)。`nov_stop` 依据实际请求证据细分两次 `invalid_request:ValueError` 和一次 `agent_incomplete`。`nov_passed_leaves` 为诊断字段，主分取原检查树的整题布尔结果。[配对表](tasks.csv)
