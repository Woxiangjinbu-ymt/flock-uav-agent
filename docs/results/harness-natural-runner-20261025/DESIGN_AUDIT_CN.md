# 自然 runner 的截止、收尾与评分边界审查

2026-10-25。此记录是新 runner 实施前的只读设计审查，不是执行验收。未改既有生产源码或冻结计划，未运行 provider、模拟任务、评分或训练。后续实现和本地结果须另行审查。

## 已有语义

- `FullWorld.request` 持有同一 RLock 直到 ASGI 请求结束；500 秒 timer 的 `freeze_score` 必须等锁。timer 不是请求中断器。跨截止动作返回时，已有逻辑恢复动作前快照、标 `committed=false`，随后冻结截止状态；若请求永久不返回，不能假定 timer 已写出可评分快照。
- `HarnessRuntime` 在模型调用、工具调用及技能公开调用边界检查取消/截止；`UAVControlAgent.execute` 同步持有单实例生命周期锁直到异常收尾和导出结束。取消信号不等于线程退出，不能在 execute 仍运行时主动关闭其归档并开始新回合。
- HTTP 审计 hook 是 fail-open 观察器，且 request hook 可早于 transport 准入运行。hook attempt 与真正 delegate 调用须分列；被硬准入拒绝的请求不能记作供应商已收到的 HTTP。
- 既有父协调器只对正常退出、完整 worker 清单、闭合且有效归档及可信评分快照发起独立 scorer。文件存在或 Agent 返回 success 都不能替代这些条件。

## 本轮采用的最小设计及验收条件

1. worker 主线程执行真实 Agent/SDK；只将 non-streaming JSON 的底层 `handle_request + read` 放入一个 daemon I/O 线程。transport 在实际 delegate 前原子检查 task/request 截止、取消、尝试额度及 single-flight；SDK 重试关闭。SSE 和 async 明确拒绝，不能旁路默认网络 transport。完整 JSON 预缓冲属于总请求时限保护，不能沿用“被动消费 SSE”的描述。
2. 请求超时或取消后不给 SDK 返回迟到 response，立即禁止后续请求。Agent 按真实异常收尾；随后 bounded drain 仅私有记录迟到状态和可验证 usage，不回传模型、旧归档或 World。归档可能 valid-but-partial，不能因已经 closed 就写 complete。
3. drain 确认结束且评分快照、worker 清单与归档闭合有效，才允许受控 timeout/cancel 回合退出后独立评分。drain 未结束时保留 incomplete 状态，等待父 watchdog kill + wait；禁止给被强杀回合补造完整归档或借已有 score.pkl 自动评分。
4. 取消记录 `requested_at` 与取得 World 锁后冻结的 `effective_at`。不承诺按外部信号时刻回滚已经在途的动作；生效后禁止新动作。task 截止、request 截止和进程 watchdog 是三种不同边界，分别记录。
5. 自然 worker 仅从原任务和真实公开回执选择技能；无 bootstrap、case 响应分支或黑板预填。场景 transport 只在独立本地验证模块。每回合新进程，worker 退出后才封存和运行 scorer；测试用 post-kill B 不能变成真实异常后的自动续派发规则。

本地矩阵至少应覆盖：准入拒绝时 delegate=0；正常 JSON 完整收尾；迟到工具调用不执行且 usage 只私有结算；跨任务窗动作回滚；锁占用期间取消；不返回请求导致 watchdog/拒评分及新 B 隔离；SSE/async 无旁路。短墙钟窗口必须显式标为 test-only，不声称已经等待或验证原 500 秒耗时，不进行任务收益比较。

本轮在线授权/预算仍为零。
