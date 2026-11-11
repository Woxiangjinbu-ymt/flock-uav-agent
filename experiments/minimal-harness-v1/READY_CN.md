# 最小 Harness 同技能实验准备

> **2026-10-15 15:40:32 更新：**用户已授权在线执行，五臂队列已按冻结顺序启动，见 [LAUNCH_CN.md](LAUNCH_CN.md) 与 [campaign.json](campaign.json)。下文保留启动前准备记录；实际状态以运行登记和封存结果为准。

状态：已离线准备和预检，未启动在线模型。实现、离线数据及研究限制见[验收报告](../../docs/results/minimal-harness-offline-20261015/REPORT_CN.md)。

- [study.json](frozen/study.json)：五臂、共同协议、固定探索顺序、指标与完整哈希清单。
- [plans/](frozen/plans/)：每臂 100 题、605 检查点、原 5 个场景、500 秒；首次探索共 500 次任务执行。
- [shared/source_hashes.json](frozen/shared/source_hashes.json)：所有臂共用一个源码包，180 个源码／依赖描述文件。
- [eligibility.json](frozen/eligibility.json)：7 个简单链任务、6 个搜索任务、87 个不支持任务；不能过滤掉后者。
- [preflight.json](preflight.json)、[预检日志](preflight-tests.log)：冻结副本专项测试 69 项通过，3 个依赖环境通过检查；没有 provider 可用性探测。

运行 `agent4drone/.venv/bin/python scripts/minimal_harness_pilot.py verify` 可检查冻结包；`preflight` 可重新执行离线预检。`prepare` 拒绝覆盖现有包。该脚本没有在线启动子命令，尚未登记或启动任何付费任务；正式执行时须通过既有 reproduction runner 建立 attempt、身份与封存记录，并使用 `plans/` 中的环境及共享源码包。不能把 `plans/` JSON 直接当作已完成或已注册的运行。

R2 参照不含技能提示；四个同技能臂具有相同动作策略、提示、观测、强制失效检查和修复能力，只改变检查点交接策略。适用性臂允许已声明的局部修复继续；failure 臂在可处理失败后交回模型。已完成停止条件优先检查。K=4、2000 次技能 API、16 次局部修复、16 轮搜索均为冻结的工程配置，未基于这次结果调优。

线上由模型选择技能，禁止套用离线的强制提交／恢复脚本。完整任务成功率及配对 W/L 为主；单次开发集探索只能提供描述性证据，不能宣称显著提升、泛化或非劣性。
