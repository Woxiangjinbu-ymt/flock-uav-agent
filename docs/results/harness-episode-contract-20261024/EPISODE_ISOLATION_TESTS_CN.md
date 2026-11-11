# 同实例 Agent 回合互斥专项验证

日期：2026-10-24。新增 [test_agent_episode_isolation.py](../../../agent4drone/tests/test_agent_episode_isolation.py)，18 个测试函数参数化为 **32 个用例，首次执行全部通过**，pytest 用时2.09秒。仅有 Starlette TestClient 的 AnyIO 弃用警告，无失败。见[日志](episode-isolation-tests.log)、[配置及12项输入哈希](episode-isolation-tests.json)；原首次运行 [log](episode-isolation-tests-attempt-01.log) / [metadata](episode-isolation-tests-attempt-01.json) 保留，稳定文件复制同一次执行，没有重复计数。

## 方法与分母

测试使用真实 `UAVControlAgent` 构造、`execute`、LangChain Agent 图、工具链归档与服务路由；模型回复由脚本提供，公开客户端为内存 fake，不调用 provider、真实模拟器或 Benchmark。socket connect 被 fixture 禁止。并发边界由 `threading.Event`/Barrier 显式控制，无 sleep、无500秒等待；测试中的1/5秒等待只是死锁失败上限，不用于模拟执行成本。

端到端测试没有替换 `_blackboard_for_current_command`、`_build_system_prompt`、`_execute_command`、`_run_agent_once` 或 `_record_toolchain_if_enabled`。少量单独门禁用例用 `__new__` 和受控私有 body 检查懒初始化、重入与 BaseException；这些不能单独证明真实执行链，故另保留真实图及归档用例。

## 覆盖边界

| 边界 | 已核验行为 |
| --- | --- |
| 同实例竞争 | A 停在模型边界时，B execute、reset、summary、refresh 明确 busy；不等待、不排队，UUID、账本/runtime、deadline、客户端/API、模型请求、记录器计数不变。覆盖 off 与 evidence_harness |
| 门禁生命周期 | 同线程重入拒绝；`__new__` 两线程首次调用只创建一个有效门禁；不同实例各自准入，但不据此声称共享世界隔离 |
| reset 与会话读取 | reset/summary/refresh 本身也持有同一门禁；空闲显式 reset 只清对应 session 记忆、无世界 I/O；execute 内公开 reset 标记变化走内部清理，不发生自锁或额外 API |
| 相邻回合 | 真实 simple_chain 的成功、明确动作失败、取消、超时之后，A 实际返回才显式恢复 fake 初态及清记忆，B 正常完成；命令 ID 独立，A 的 Harness 快照与落盘记录保持不变 |
| 持久化尾部 | A 已计算完成但实际 JSON writer 被阻塞时，其他受保护操作仍 busy；归档结束再释放。写入失败不捏造成功归档，记录器停止，允许正常回收后的 B |
| 逃逸 BaseException | 原异常原样保留，先关闭当前 Harness、停止 recorder，再释放。清理阶段仍 busy；任一步清理失败，继续尝试另一清理并标记实例不可复用，四入口均拒绝，要求新实例 |
| 服务同步与异步 | command/session 同步接口返回409及 `agent_busy`；async竞争作业明确 FAILED、无结果且无自动续跑；取消竞争不被覆盖为FAILED |
| 取消与晚回复 | A job 已置 CANCELLED 而脚本模型仍在途时，lease 保留；迟回工具调用被取消守卫拒发。A 实际退出/清理完毕后才允许后续命令。预取消作业不进入 Agent，worker 返回时有 completed_at |

真实客户端、完整世界恢复、跨进程或多 Agent 并发、真实网络外部动作的撤销不在本次范围。取消只阻止继续派发，不承诺撤销已提交动作。完整模型请求正文归档未加入本修复，不能从这些测试推出 Teacher 数据可直接构建。

## 复现

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=agent4drone:agent4drone/tests agent4drone/.venv/bin/python -m pytest agent4drone/tests/test_agent_episode_isolation.py -q
```

使用仓库现有虚拟环境，不安装依赖、不读取 provider 配置。输入哈希覆盖两份修改的生产文件、相关 Harness/证据/黑板/记录器、复用测试 helper 及新增测试；执行前后12项全部一致。32项专项与根代理其他回归、独立反例审计分别报告，不把不同验证类型叠成任务样本。
