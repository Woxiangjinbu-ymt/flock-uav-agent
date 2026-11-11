# Episode reset 与执行隔离：审计及修复前后对照

2026-10-24。范围：现有 World/FullWorld、Agent 的证据 reset、Harness 生命周期及 API 服务并发/取消。本审计未修改生产或历史封存，未运行仿真、模型、HTTP 服务或读取 provider 配置。先用[纯线程反例](reproduce_shared_agent_overlap.py)确认缺口；[原始机器结果](shared-agent-overlap-counterexample.json)保留 6 个断言及修复前源码 hash。父代理实施修复后，以[同调度对照脚本](verify_episode_guard.py)独立验证最终候选，**66 个检查全部通过**，见[最终机器记录](episode-guard-before-after.json)。两批检查不相加为测试或任务成功率。

复核结论：本次同实例 Agent 的准入与收尾缺口已解决，未发现阻断性问题；不等于完整 World reset 或多实例共享世界隔离已经验收。下方前两节记录**修复前**事实，旧 Agent/service 行号来自备份源码；链接当前文件仅作定位。历史反例脚本按当时实时源码抽取，复现旧版本应使用机器记录对应的备份，不能拿当前新增 wrapper 当旧版方法运行。

## 修复前已确认缺口：共享 Agent 的命令生命周期未隔离

服务只有一个 `agent_instance`，异步任务入口可让两个 background worker 同时进入同一实例的 `execute`；同步入口也使用该实例，未共享一个命令级互斥边界。参见 [service 全局对象](../../../agent4drone/agent_api_service.py:109)、[background dispatch](../../../agent4drone/agent_api_service.py:249)、[同步入口](../../../agent4drone/agent_api_service.py:345)。

`execute` 在任何互斥检查之前重置 `self._harness`、`_task_progress`、`_model_wire_requests`、client 的 deadline/记录状态，随后重建 blackboard、tools 和 agent。结束时，[收尾函数](../../../agent4drone/uav_agent.py:1097)再次从 `self._harness` 取得对象并 `close`，并非持有本次命令自己的 runtime。相关入口见 [execute](../../../agent4drone/uav_agent.py:1193)。

反例的确定顺序为：A 建立 Harness A 并停在假后端 → B 建立 Harness B 并停住 → A 返回并收尾 → B 继续。实际结果：

- A 返回的 `harness.task_id` 是 **episode-B**，A 自己的 Harness 仍未关闭。
- B 尚未返回，Harness B 已被 A 的收尾关闭。
- B 调用真实 `check_interrupt()` 得到 `harness task already closed`，本应独立的 B 因 A 收尾而失败。

这是执行结果串归档及错误终止的可复现缺口，不只是“可能需要更好架构”。反例没有证明真实 UAV 动作已经跨任务泄漏；无需执行危险动作，已有状态归属与生命周期错误足以支持修复。

## 修复前 reset/隔离链的实际边界

| 层级 | 已有保证 | 不能等同于什么 |
| --- | --- | --- |
| [World.restore](../../../scripts/harness_v2_world.py:104) | 恢复 controller 对象图、clock、RNG、tick，重置 started/events，结构 hash 校验 | 不是 Agent reset，也不重置 FullWorld 的 closed、score_blob、请求/物理日志与 timer |
| [FullWorld](../../../scripts/harness_full100_world.py:58) | 单次任务时钟不可重启；deadline 下事务提交/回滚、冻结评分；request/freeze 共用锁 | 不能直接在同一对象上 restore 后 start 新 episode；World 使用全局 server/controller 绑定及模块时钟，FullWorld.active 也是进程级变量，不能据此声称多个对象可并发隔离 |
| [Agent.reset_task_evidence](../../../agent4drone/uav_agent.py:577) | 清空选定 session 黑板/索引，旧 runtime 的 epoch 守卫可拒绝继续；下一命令重建命令账本 | 不恢复物理环境、不终止正在执行的命令，也不建立并发 reset/execute 边界；同 ID 且所有公开 marker 不变的 reload 需调用方显式 reset，代码已明确写出该限制 |
| [HarnessRuntime](../../../agent4drone/harness_core.py:206) | 单 runtime 的 control 互斥、scene/epoch/deadline/cancel 守卫、终态拒绝重放、close | 两个 runtime 有各自的锁，不能保护共享 Agent 上的 `_harness` 等字段，也不构成 service 的 episode 锁 |
| [服务取消](../../../agent4drone/agent_api_service.py:317) | 标记 job CANCELLED，由 callback/运行时后续检查发现；任务结束再次检查状态 | 标记返回时不表示 execute 或已派发的外部工具已经停止；不能据此释放 episode 所有权或安全重置环境 |

此前固定对照采用**每 trial 新进程、完整快照恢复、空黑板、新 index/runtime**，没有走共享服务并发入口。本发现不追溯否定这些已封存成绩，也不建议把 FullWorld.restore 直接包装为已验证的跨 episode reset。

## 确定实施的窄修复

在 `UAVControlAgent` 建立**同实例的一次命令执行所有权**：第二次 execute 在任何共享字段、client 记录或 deadline 改写前明确返回 busy/拒绝；所有退出路径以 `finally` 释放，收尾只能关闭和归档本 episode 的对象。同步、异步服务继续经同一入口，不另建调度器、训练框架或 per-job Agent 池。

证据 reset 纳入同一边界：来自其他执行者的 reset 在 busy 时拒绝；当前 execute 内 `_current_session_id` 检测 marker 后调用的内部 reset 应继续有效，不能简单把“执行中禁止一切 reset”套在现有内部调用上。取消只请求中断，所有权保留到原 execute 实际退出。外部 simulator 恢复仍由调用方在空闲边界完成；本修复不声称回滚已经发出的远程动作。

最小验证不需要模型或仿真：复用本反例，要求第二次调用被拒绝且 A 的 `_harness`、账本、client audit、deadline 不变；A 的结果只含 A，且仅关闭 A。再用 barrier 覆盖 busy 时外部 reset、正常顺序 A→B、异常/取消后的释放、取消标志已置但原调用未退出时仍 busy，以及内部可见 marker reset 保持有效。保留既有 runtime 终态/epoch 测试即可；无需为本修复重跑七题成功率或扩大自然模型队列。

该修复的目标是**可复用 episode 的状态归属与生命周期正确性**。它不增加搜索能力，不代表后训练环境已经完整验收；训练数据/轨迹接口仍由同一轮独立审计决定。

## 最终候选的独立验证

对照直接抽取修复前备份与最终候选的真实方法体，候选包括 wrapper、私有执行体、gate、reset、summary、refresh 和 service handler；使用同一个屏障后端、真实 `HarnessRuntime`。未导入 Agent/service 模块、初始化真实 provider 或调用网络；endpoint 装饰器及 I/O 协作者替换为假对象，实际完整构造器与 HTTP 框架集成由本轮独立专项测试覆盖。

两臂都执行“A 进入后端 → B 尝试 → 读取状态 → 释放 A 并等待返回 → 再提交 B”。旧版重现 A 结果归档 B、A runtime 未关闭、B 提前被关闭；新版 B 显式 `agent_busy`、异步 job 为 `FAILED` 且无执行结果，未进入后端。B 尝试前后 A 的 Harness/ledger/board/tools/agent/callback 身份、wire 内容、deadline、启动时钟及 client 记录完全相同。A 结果只含 A，A 完整返回后 B 成功并正常关闭自己的 runtime。

其他检查确认：

- busy 时 public reset、summary、refresh 均无共享状态或 API 副作用；同步 command/summary 映射为 HTTP 409 的真实 handler 路径经过验证。
- 正常收尾已经关闭 runtime、但 recorder 尚未返回时，B 仍被拒绝；租约保持到完整函数退出。
- cancel 仅置状态时 B 仍被拒绝，A 实际退出后才可复用；排队即取消的 worker 不执行命令，返回时保留 `CANCELLED` 并写 `completed_at`。
- 普通异常与逃逸 `BaseException` 均释放锁并关闭 runtime；后者保留原异常，未伪造已归档命令结果。若 runtime 关闭或 recorder 停止任一失败，仍尝试另一项，标记实例不可复用；四个受保护入口随后都明确拒绝且无副作用。
- 内部可见 session marker 重置在已有租约中仍能清空证据，不重入同一锁；空闲 public reset 只清记忆，不调用世界 API。
- 五个原方法体在仅去除函数名、docstring 后 AST 完全一致：执行、证据清理、summary、refresh 与结果收尾。新增逻辑集中在准入、逃逸异常清理及服务拒绝语义，没有新增搜索策略。

最终验证源码 SHA-256：`uav_agent.py=d9837f5095cd1313006df0e003697c27fb992bb63d64f8e4c770cf1e2f84d310`；`agent_api_service.py=b65dd5b6c5efb42b5e6dce4c90e35b5fefd7b35b7f865d1a163f36a046166f32`。每项结果、原版/shared 源码 hash、脚本 hash 均在机器记录中，运行期间源码未变化。

已追加[独立源码归档与重放说明](episode-guard-replay-01/README_CN.md)：修复前后两份 Agent/service、实际使用的 `harness_core.py` 及原 hash 上下文均已冻结，见 [manifest](episode-guard-replay-01/MANIFEST.json)。`original/` 保存本次已运行脚本与 JSON 原字节；另存的 `replay_episode_guard.py` 默认读取归档，不依赖 `/tmp` 或后续主干。**本次只归档，重放适配版尚未执行，未新增测试计数。**

残余边界：实例锁不跨 Agent/进程协调同一个世界；不替调用方恢复仿真状态、RNG、分数、时钟，也不回滚已派发远程动作。取消请求与执行退出仍是两个时点；即使收到 `completed_at`，也不据此声称外部异步系统已完成事务。当前结果是纯确定性功能证据，不是新的 Benchmark 分数、自然模型表现或后训练验收。
