# 自然入口运行器：真实墙钟与排空的本地验收

日期：2026-10-25。独立本地批次；真实 provider 调用 **0**，新增线上预算 **0**，未启动 Teacher 数据生成、SFT 或 RL。原自然验证准备包的 14 个拟议槽未执行，`online_ready=false` 保持不变。

## 实际完成的工作

新增真实墙钟 worker、请求准入与有界排空、独立 scorer 和本地协调器。沿用每 trial 新进程，评分必须等 worker 退出并验哈希；未排空或 watchdog 强杀的 worker 不评分。同步非流式 JSON 的整个请求和正文读取受时限约束，迟到响应只保存在私有诊断，不再交给 Agent 执行动作。

执行一次固定矩阵：**12 个 worker + 10 个 scorer，22 个不同进程**。模型响应由独立 MockTransport fixture 产生，Agent/SDK、Harness 和 ASGI 模拟器是真实代码。worker 不预置计划、不预填观察、不代替 Agent 选择 skill；但 fixture 中的决策仍是脚本，不能称为模型自主能力或 Teacher 示范。

使用原任务 `b1eb0270`：Drone 3 起飞到 14 米，转向 353 度并悬停。同一原初态/任务/检查树，正常任务窗 500 秒，原每计划 API/修复/轮次预算不增加。任务截止场景明确使用 **1 秒派生边界探针**；没有执行持续 500 秒压力测试。具体限额、固定顺序及停止条件见[运行前协议](PROTOCOL_CN.md)。

## 结果

| 场景 | 真实动作与终态 | 模型归档 / 评分 |
| --- | --- | --- |
| skill_success | 起飞、转向、悬停 | 完整；原 checker 2/2 通过 |
| raw_success | 三个原工具依次执行 | 完整；原 checker 2/2 通过 |
| http_error | 起飞后第三 HTTP 返回 500，未转向 | partial；1/2，整题失败；未知 usage 显式保留 |
| request_timeout | 起飞后第三请求超过 0.1 秒；迟到正文仅入私有诊断 | partial；1/2，整题失败 |
| task_deadline | 第三请求跨过 1 秒真实墙钟截止，评分固定在截止边界 | partial；1/2，整题失败 |
| cancel | 等待第三请求期间取消，随后冻结；未转向 | partial；1/2，整题失败 |
| http_limit | 两次请求后阻止第三次实际派发 | partial；1/2，整题失败 |
| usage_missing | 第三 2xx 缺 usage，正文不交给 Agent | — |
| usage_stop | 两次响应共 52 个合成 token 后阻止第三次实际派发 | partial；1/2，整题失败 |
| hung_drain | 已真实起飞、第三请求永久阻塞；请求超时后排空失败，父 kill+wait | 已关闭但 partial；基础设施 incomplete，**不评分** |
| watchdog_hang | 已真实起飞、第三请求永久阻塞；父 watchdog 先 kill+wait | 未写完结束记录；基础设施 incomplete，**不评分** |
| post_kill_B | 恢复原初态，完成相同 skill 序列 | 完整；2/2；首次完整请求仅映射命令 ID 后与首个参考回合逐字一致 |

三个成功场景、七个预定失败场景、两个不评分的基础设施探针是验收矩阵，**不得汇总为 3/12 或 3/10 的 Benchmark 成功率**。没有进行策略收益的前后对照。

已从独立 transport 日志关联实际请求与响应哈希、controller 记录、模型归档正文和真实动作。两个强杀探针均确有第三次请求在途，不是初始化失败；最后的新 B 初态、首次输入、动作和原检查结果均与参考一致，首次请求的 46,064 字节实际无需 ID 替换便完全相同。十个完成 worker/scorer 的网络连接尝试均为空；隐藏 canary/检查树未进入策略正文和 policy 文件。静态写入白名单、独占无 lifespan 的 ASGI、时间线 schema 已核对；初尾障碍几何一致仅有 **10 个完成 worker** 的记录，两次强杀没有尾部几何记录。

## 首次判定与审计修正

[原始 run-01](run-20261025-01/summary.json) 保留 **335/336**，`passed=false`。唯一失败为 `hung_drain:oracle:undelivered_response_2_absent_from_archive`。原审计器把所有强杀都要求为“无 response_end”，但 hung_drain 在请求超时返回主线程后已经正常关闭归档，记录的是 **response_observed=false、complete=false、0 chunks**，随后才因 I/O 仍未排空等待父强杀；watchdog_hang 才是没有结束记录。

这属于审计断言错误，运行器行为没有因此修改。原始输出、首次失败 summary 和 378 文件 campaign seal 均保留；更正审计器后只读复核同一份输出，不重复运行任务，也不把修正后的结果写回旧封存目录。[完整复算结果](revalidation-summary.json)为 **336/336 通过**，全部原文件哈希不变。

[审计器复核说明](INDEPENDENT_REVALIDATION_CN.md)分别记录 243 项正文关联和 1,096 项文件/进程/证据检查；[第二独立验收](INDEPENDENT_ACCEPTANCE_CN.md)直接读取原始文件，329 项通过。这些与主验收有重叠，不能相加为独立实验分母。额外审计的首版也保留：一处误要求首请求必须出现命令 ID，另两处混同了归档请求意图与实际 dispatch；修正审计口径后通过，均没有改变或重跑任务。实际为 38 条归档请求意图、36 次派发，准入拒绝的两次没有收到响应。

## 代码与验证范围

- `scripts/harness_request_limits.py`：同步非流式准入、请求时限、usage、限额与排空；84 项专项测试通过，覆盖 httpx/httpx2。
- `scripts/harness_natural_runner.py`：真实墙钟 worker、静态写入契约、取消线性化、隔离 scorer。
- `scripts/harness_natural_local.py`：禁网络/禁 provider 配置的本地模型 fixture。
- `scripts/validate_harness_natural_runner.py`：固定本地矩阵、进程回收、校验和封存；77 项纯协调器测试通过。
- `scripts/audit_harness_natural_runner.py`：独立正文/传输/动作关联审计；33 个纯合成 unittest 方法通过，含上述归档终止差异和故意损坏证据的反例。
- 两项既有 FullWorld 原子截止/晚动作回滚测试通过；它们与本批“模型等待期间的截止/取消”是不同覆盖，不合并成所有竞态已验收。

测试日志、源码前后哈希分别见 `request-limits-attempt01`、`coordinator-tests-attempt01`、`oracle-tests-attempt01`、`world-cutoff-attempt01` 的 `.log/.json`。84/77/33/2 四组测试分别报告，不与完整回合或只读审计的分母混合。[旧材料完整性核查](baseline-integrity.json)确认：旧完整回合 417 文件、自然准备记录 16 文件及计划 8 文件、冻结/当前各 80 份运行源码、原 HTML 和 AGENTS 均未改变。各组有重叠，不把文件数累加为独立证据数。

本轮新增上述 5 个脚本、3 个专项测试文件和本目录；更新研究状态与 Roadmap，稳定长期设定 AGENTS 不堆入本轮细节。最终代码/测试及两份总览的副本存于 `delivery-source/`，版本映射见 `delivery-manifest.json`；本目录整体由 `artifact-seal.json` 封存。冻结 run-01 内保留原审计器，当前交付审计器则含最小断言修正，二者不可混称同一源码版本。

## 仍然成立的限制与下一步

本实现只支持**同步、非流式 JSON**。HTTP 次数有实际派发上限；完整缓存非流式正文与此前被动 SSE 归档的适用范围不同；异步/流式不在本批验收内。

取消首先停止新准入，拿到 World 锁后才冻结；锁内已进入的动作不承诺追溯撤销。无法排空或无法回收时应停止未来在线批次。本地预定强杀之后运行 B 只用于检查隔离，不是在线续跑策略。当前非 GET 动作计数还包含碰撞 POST，复杂任务计量时须区分查询和物理命令。

达到这些条件后，才为已准备的 7 题 × A/F 协议建立独立执行版本，检查自然选择、真实 paused 恢复和 raw 接手；机会为零则如实报告，不自动补题、增加预算或进入训练。环境接口通过不等于训练闭环或论文创新已完成。
