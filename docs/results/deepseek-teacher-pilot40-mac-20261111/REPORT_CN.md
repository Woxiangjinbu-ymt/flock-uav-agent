# Mac 上的 DeepSeek 教师连续会话验收

2026-11-11。冻结的两场各 20 题，在 Mac CPU 上使用本地固定回复、真实 Agent 和仿真工具。每场只冷启动一次，按原序进题。每题清空对话历史并继承该场的世界、机队位置和共享黑板。[场景、题序与种子](../../specs/04-deepseek-pilot40/pilot40.json)

## 本次实测

两场合计正常结束 40/40 题，耗时 **239.6 秒（约 4.0 分钟）**，包含准备、逐题仿真、封存及独立评分。两场之间重新载入原场景。核验了 38 次场内相邻题的世界、时钟及黑板交接。[核验摘要](summary.json) · [完整归档与复算 JSON](../../../outputs/deepseek-teacher-pilot40-mac-20261111/sealed-pilot40.tar.gz)

| 原场景 | 按原序完成并评分 | 题初通过 | 终态通过 | 固定回复 | 工具回执 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `04cd6269` 区域巡逻，5 架 | 20/20 | 0 | 1 | 100 | 80 |
| `ef36e27a` 区域搜索，10 架 | 20/20 | 0 | 0 | 100 | 80 |

200 次模拟请求全部保存原始请求与响应及逐次起止时刻，160 次后续请求正确回传历史思考，160 个工具调用按 ID 对齐了工具回执和仿真调用。正常批次模拟用量为 240,000 个输入 token、6,400 个输出 token，其中思考 1,800 个。用量由固定回复注入，上述时延只描述本地传输。[逐轮记录](../../../outputs/deepseek-teacher-pilot40-mac-20261111/verification/normal.json)

完整重评分逐项一致，题初和终态各有 40 棵树、186 个叶检查。唯一由题初未通过变成终态通过的是巡逻场景第 1 题 `a05a871a`。原检查树验证 Drone 1 达到 9 米且已离地。这项成绩表征固定动作演练，真实 DeepSeek 教师的任务成功率与示范保留率有待采集。[重评分记录](../../../outputs/deepseek-teacher-pilot40-mac-20261111/verification/rescore.json)

## 预算停线

| 独立故障运行 | 底层本地派发 | 对账结果 | 40 个计划题位的去向 |
| --- | ---: | --- | --- |
| — | 1 次 | — | 首题中断后完成独立评分，39 题未启动。 |
| 首条固定回复省去 usage | 1 次 | — | 首题中断后完成独立评分，39 题未启动。 |

连续会话、DeepSeek 消息适配器、教师传输及账本的 157 项回归全部通过。篡改计时、调用 ID、题初评分、请求账本或删去未启动题位等 9 个负对照均被拒绝。[额度不足](../../../outputs/deepseek-teacher-pilot40-mac-20261111/verification/budget-stop.json) · [用量缺失](../../../outputs/deepseek-teacher-pilot40-mac-20261111/verification/missing-usage.json) · [负对照](../../../outputs/deepseek-teacher-pilot40-mac-20261111/verification/tamper-results.json)

最初联调时，固定终止回复缺少 Agent 要求的 `[TASK DONE]`，第一题被运行器拦下。补齐标记后两场完整结束，随后增加逐次计时，在全新账本及场景上重跑。表中数字均来自最后同一版代码的完整运行，先前工程记录保存在本地运行目录的 `engineering-*` 子目录。

## 轨迹与评分如何核对

每题记录实际经 SDK 序列化的请求、独立字段中的助手回复与思考、工具调用 ID、返回给模型的工具回执、仿真动作和封存世界。后续请求逐条对照先前的助手消息、推理文本和回执。感知工具使用 29 题复测中冻结的新说明。逐题索引可直接打开原始请求、回复、执行记录和两次评分。[逐题材料](TASKS_CN.md)

运行器关闭该题的动作入口、排空已接收的动作，再封存终态。会话执行进程退出后，独立评分进程在各题的题初与终态运行同一套原始检查树。第三个无网络、未加载策略模型的进程从已封存世界重跑原检查树，比对叶节点、返回值和整树结果。模型自报完成只记录在轨迹里，是否通过以原检查树结果为准。[独立复算器](rescore.py)

## 正式采数的启动条件

1. 研究协议目前要求 G1、G2、G3 均通过。2B 在新工具说明的 29 题整题复测中，开思考 1/29、关思考 0/29。开臂 26 个失败可归因，成功数还差 2 题；G2 净增 1 题、覆盖 1 个父场景，门槛是净增至少 3 题、覆盖至少 2 个父场景。G3 已通过。是否改变这道启动门槛是单独的研究决定。[整题复测报告](../flockcue-a100-g1g2-retest-20261111/REPORT_CN.md)
本次两场入口只接本地固定回复。正式入口还要接上真实传输与凭据，沿用发送前预留，并在中断恢复时核对已派发的请求、未结算锁额及可证实的题间交接，避免重复付费。试采清单、A 折用途及实际账户币种的支出上限仍待确认。派发前按实际账户核对当日价格。[试采方案与预算](../../specs/04-deepseek-pilot40/plan.md)
第一场 20 题完成独立评分后即可筛轨迹，只保留有可归因行动、回执完整、独立检查通过的合格决策轮，并排除题初已满足、口头宣告及无进展重复等情形。可据此开始构建首批 SFT 样本，实际保留量由真实轨迹决定。[逐轮取舍约定](../../specs/04-deepseek-pilot40/spec.md)

## 本地复现

从项目根目录运行，`--output` 指向新的实验目录。正常批次使用 `normal`，两种故障分别使用 `budget-stop`、`missing-usage`，每个批次单独建目录。验证结果写在运行目录之外。

```sh
agent4drone/.venv/bin/python scripts/run_teacher_pilot40.py run --case normal --output /tmp/flockcue-pilot40/normal
agent4drone/.venv/bin/python scripts/run_teacher_pilot40.py verify --run /tmp/flockcue-pilot40/normal --output /tmp/flockcue-pilot40/verification/normal.json
agent4drone/.venv/bin/python docs/results/deepseek-teacher-pilot40-mac-20261111/rescore.py --run /tmp/flockcue-pilot40/normal --output /tmp/flockcue-pilot40/verification/rescore.json
```
