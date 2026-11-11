# 单 Agent 回合隔离：修复与验证

2026-10-24。**已修复同一 Agent 的重叠命令互相覆盖、误关闭 Harness 和串归档的问题。** 本次从 reset/轨迹接口审计中选择这一明确缺口，完成生产修复、固定线程顺序的前后对照及 **770 项不重复代码测试**。没有重跑 Benchmark、调用真实模型、增加任务预算或改变搜索策略。

本轮只验收同实例的命令生命周期。完整仿真世界 reset、跨实例/进程隔离、可逐字重建的模型输入输出及 Teacher 数据生产仍未完成。

## 问题与前后对照

[Reset 审计](RESET_AUDIT_CN.md)确认 API 服务共用一个 `agent_instance`，两个 background worker 可同时进入 `execute()`。原入口会改写同一实例上的 Harness、账本、黑板、wire 列表、客户端 deadline 和记录状态；收尾又从 `self._harness` 取对象，可能关闭另一个命令的 Harness。

先保留[原版反例](shared-agent-overlap-counterexample.json)，再以相同后端、相同屏障顺序做[独立前后对照](episode-guard-before-after.json)：A 建立 runtime 并阻塞 → B 请求执行 → A 返回并收尾 → B 再继续或重新显式发起。

| 检查 | 修复前 | 修复后 |
| --- | --- | --- |
| B 在 A 尚未结束时进入 | 覆盖 A 的当前 runtime 和记录状态 | 在 UUID、状态重置、后端调用之前抛 `AgentBusyError` |
| A 的结果归属 | 返回 B 的 harness.task_id | 只返回 A 的 runtime；B 的被拒绝请求不改 A |
| A 收尾 | A 自己未关闭，反而关闭仍执行中的 B | 关闭 A，完成归档/停止记录后才释放占用 |
| 后续 B | 原重叠 B 的 `check_interrupt()` 报 already closed | A 完整退出后，新的显式 B 调用正常完成 |

最终独立检查 **66/66 通过**，不与早期 6 个反例断言或中间运行次数累加。对照使用从两版源码提取的真实入口/收尾函数体及真实 `HarnessRuntime`，后端为可控假函数；不是两个真实模型或仿真任务的成绩。另有真实 Agent 构造、图执行、API handler 和文件导出的专项测试，避免只靠 AST 探针判断集成正确。

[复现材料](episode-guard-replay-01/README_CN.md)冻结修复前后源码、必要 runtime、原 66 项脚本与结果的原字节和 hash。另附默认读取冻结目录的重放入口；该入口尚未执行，不将其当作新增通过数。

## 实际修改

- [uav_agent.py](../../../agent4drone/uav_agent.py)：`execute`、`reset_task_evidence`、`refresh_session_context`、`get_session_summary` 共用同实例非重入锁。拿不到锁即拒绝，不排队或自动重试；不同 Agent 实例仍各有自己的锁。
- 准入覆盖执行、异常处理、Harness 收尾、文件导出及 API recorder 停止。取消标志不会释放锁。内部会话 marker 变化沿用私有证据清理路径，避免在已经持锁的执行线程重入公共 reset。
- 逃逸的 `BaseException` 会在持锁时关闭旧 Harness、停止 recorder，保留原异常；不假装已经生成完整结果档案。任一清理失败后，该实例禁止后续受保护操作，要求新建实例。
- [agent_api_service.py](../../../agent4drone/agent_api_service.py)：同步 command/session 忙时返回 HTTP 409，`detail.code=agent_busy`；异步提交不预占执行权，竞争失败的 job 明确为 failed，不自动续跑，已经 cancelled 的 job 保留状态。worker 启动前发现取消也补记 `completed_at`。
- 新增[专项测试](../../../agent4drone/tests/test_agent_episode_isolation.py)，同步 [服务契约](../../../agent4drone/README_SERVICE.md#command-isolation-and-reset)及 [API 说明](../../../agent4drone/README_API.md)。研究状态和 Roadmap 记录本轮证据及剩余接口缺口。

本轮生产和接口文档的[增量补丁](lifecycle-change.patch)单独保存；当前研究入口为[研究状态](../../RESEARCH_STATUS_CN.md)与[Roadmap](../../CSCWD2027_INCREMENTAL_RESEARCH_ROADMAP_CN.md)。

准入互斥适用于所有 Agent 模式；正常串行调用的原函数体、工具行为和预算保持不变。没有新增 Harness revision，也没有将 observed_target_v7 或其他候选改为默认。

## 验证范围

[32 项新增专项](EPISODE_ISOLATION_TESTS_CN.md)首次执行全部通过，[738 项既有回归](regression-existing.json)全部通过，合计 770 项。稳定日志与 attempt-01 是同一次专项执行的副本，不重复计数。所有检查阻断外部 socket，未调用 provider 或真实仿真任务；ASGI handler、本地脚本模型及可控工具回执仅用于代码验证。

专项覆盖：

1. 双线程竞争、同线程重入、旧式 `__new__` 实例首次并发初始化；四个公共入口在 busy 时不分配命令 UUID、不读写客户端、不改已有状态。
2. 文件导出仍在进行时继续拒绝新命令；正常返回、异常和取消后的释放；清理单项/双项失败后拒绝复用，并保留原异常。
3. 真实 Agent 图和 simple_chain 在成功、明确动作失败、取消、超时四种终态后，测试调用方显式恢复假工具世界并清记忆，再运行同 session ID 的 B；B 成功，A 的结果和已写档案不被改写。
4. 取消已请求但晚模型回复尚未返回时仍 busy；取消检查阻止该回复中的后续工具动作。该测试不证明已经派发的远端物理动作会撤销。
5. 内部可见 reset 不自锁，外部 reset 受互斥且保留 session 作用域；HTTP 409、异步竞争失败且不重试、预取消的 worker 不执行并记录结束时间。

[代码独立复核](GUARD_REVIEW_CN.md)未发现当前限定契约下的阻断问题。其结论、66 项对照检查和 770 项 pytest 是不同验证口径，不合并成任务样本或成功率。

[历史完整性核验](historical-preservation.json)确认此前六批封存、原 full100 manifest 的 410 个文件及原 HTML hash 不变。保留旧成绩及负结果，不用本轮生命周期测试替换历史实验数字。

## 仍未完成的接口

**世界 reset：**`reset_task_evidence` 只清记忆；`World.restore` 只恢复控制器对象图、时钟/RNG等，不能直接当成 `FullWorld` 的新回合。后者还保留关闭状态、评分快照、计时器和日志；World 使用的 server/controllers 及 `FullWorld.active` 是进程级共享状态。此次没有实现原子“世界恢复＋记忆重置＋下一任务启动”，也不允许凭同实例锁宣称多 Agent 共享世界安全。继续复用已有每 trial 新进程的隔离方式，进程池复用另行验收。

**取消与晚到事件：**等待实际 execute/worker 收尾，不能只看 cancelled 标志就重载世界。本次不关闭或回滚任意远端已派发动作，不承诺排空其他线程/进程直接调用的 client、私有辅助方法或服务写入。

**轨迹正文：**[模型输入审计](MODEL_INPUT_AUDIT_CN.md)确认目前 wire/prepared 日志主要保存 hash；steps 导出还丢失 tool_call_id/message_log。两个只有调用 ID 不同的消息链会导出同样的工具链。因此，既有日志不能逐字恢复模型实际输入，也不能直接当 Teacher 数据。本轮没有修改正文归档或 wire owner：正常 execute 使用不同 UUID 时，旧 response 不会被记进新 runtime；手工复用 task_id 和直接调用已关闭 runtime 是另列的防御性边界。

## 下一步

优先补一个默认关闭、范围明确的实际模型 I/O 归档：保存序列化后的请求正文/工具 schema、逻辑调用与 HTTP attempt 引用，以及 handler 返回的结构化 assistant/tool-call 信息；与 hash 逐字核验，不提前消费 response stream、不读取私有评分或认证信息。先用本地 MockTransport 验证正文可重建、重试/异常/取消与跨回合隔离，不先生产训练数据。

随后在已确认的进程隔离方式下验收一条完整的 `初态恢复 → Agent 输入/行动 → 终止 → 隔离评分 → 下一回合` 链，再冻结自然 Agent 选择与接手协议。搜索三题的上轮结果仍为 1/3；本次没有新增搜索能力或模型能力结论，未启动旧 360 次、SFT 或 RL。
