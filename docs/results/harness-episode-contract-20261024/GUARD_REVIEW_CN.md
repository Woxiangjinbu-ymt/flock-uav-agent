# 单 Agent 生命周期互斥：代码独立复核

2026-10-24。只读复核最终 `uav_agent.py` / `agent_api_service.py` 及其 cleanup 依赖；[源码 hash](GUARD_REVIEW_SOURCES.json)标识所审版本。未修改生产、既有诊断封存或实验结果，未运行模型、仿真或任务。本记录是代码审查结论，不替代另一项专项测试结果。

**在“同一个 Agent 实例一次只运行一个受保护操作”的限定契约下，未发现主要阻断。** 四个公共入口 `execute`、`reset_task_evidence`、`get_session_summary`、`refresh_session_context` 均先取得同一非重入 gate；拒绝发生在 UUID 分配、ledger/Harness/wire 重置、客户端 API 或记录开关改变之前。懒初始化由单独短锁保护，不会让同一旧式 `__new__` 实例首次竞争时得到两个 gate；不同 Agent 实例仍有各自 gate。

关键边界如下：

- **持有与释放：** gate 覆盖原执行体、异常处理、Harness 收尾、工具链导出和 API 记录停止；`finally` 释放。正常失败返回、异常抛出和取消不能让另一个 execute 提前接管。
- **BaseException：** 原执行体未处理的异常会在仍持有 gate 时，依次尝试关闭当前 Harness、停止 API recorder；前一项失败仍继续后一项。任一 cleanup 失败设置 `_episode_cleanup_failed`，随后四个公共入口拒绝复用，提示新建实例；不会因锁已释放就把不完整清理当作空闲。正常 cleanup 不捏造成功结果，原异常继续向外传播。
- **Reset：** 外部 reset 在忙时拒绝；execute 准备阶段的公开 marker 变化走内部 `_clear_task_evidence`，避免在持锁线程重入公共 gate。这里清的是证据，仍不是恢复世界状态。
- **服务与取消：** 同步 command/session 请求把 busy 映射为 HTTP 409；竞争的后台任务明确失败而不自动排队或重试，已经取消的 busy 任务保留取消状态。取消接口仍只是设置标志，不释放 gate；必须等原 execute 返回，才能复用本实例。

本次修复直接针对[修复前共享实例串归档反例](RESET_AUDIT_CN.md)，未修改搜索策略、工具权限、wire owner 或正文归档。正常每个 execute 使用新 UUID；[模型输入审计](MODEL_INPUT_AUDIT_CN.md)所列同 task_id 手工复用、已关闭 runtime 被直接调用、并行逻辑模型调用等更宽边界仍需单独契约，不能据本次互斥宣称已经支持。

限制保持明确：gate 不隔离不同 Agent 实例共享的 simulator，不阻止其他进程/API 写世界，也不证明远端已派发动作被取消或所有后台活动已排空；它不提供完整 episode reset、逐字模型输入/输出或可训练 Teacher 数据。正式验收应以对应源码 hash 的专项测试为准，覆盖四入口忙时无副作用、正常串行、异常/取消时持锁与释放、cleanup 单项/双项失败及后续拒绝复用。
