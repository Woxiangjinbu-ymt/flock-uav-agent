# terminal_progress_v6 退出与公开确认契约：独立只读审查

日期：2026-10-23。本审查读取生产候选及前一 v5 封存；**未改生产代码、未运行仿真或模型，也未修改旧批次**。受审源码及旧封存 SHA 见 [CONTRACT_AUDIT_SOURCES.json](CONTRACT_AUDIT_SOURCES.json)。静态代码审查未发现阻断；四类边界仍需专项测试及新的配对结果分别验收，本文件不代替测试通过记录。

## 已知问题及最小范围

前一 `b1ad4f3e/shared_endpoint_v5` 最后一次技能公开进度 #772 为 95%、19/20；随后仍有 6 次动作。#800 的成功回执首次报告 `Fixed Target 20`，累计公开“Reached target(s)”名称达到 20 个；#807 报告此前已出现的 `Fixed Target 3`。这支持 #800 时可能已经完成的推断，但该时点没有读取聚合进度，不能将推断写成直接观察。结束后公开 #834 明确返回 100%、20/20，原 checker 通过；frame 却仍缓存 95%，以 `no_public_candidate` 退出。逐条证据见 [previous-v5-public-exit-evidence.json](previous-v5-public-exit-evidence.json)。

冻结 v5 在 `not made_attempt and progress <= 95` 时直接抛出退出，绕过函数尾部不足周期的进度刷新。v6 只在该分支追加**一次普通 `PublicCall("get_mission_progress")`**：无动作、无重试，不调整候选排名、搜索范围、round、repair、常规进度周期或其他退出分支。v6 继承 v5 的静态障碍显式契约；默认 revision 和 v5 行为保留。

## 四类边界

| 边界 | 本次实现的预期行为 | 必须保留的证据与限制 |
| --- | --- | --- |
| 已完成 | 新读数合法且严格 **>95** 时更新 frame 进度与公开停止标记，生成器正常 return，runtime 报 `completed / skill_public_stop_condition` | 不是读取旧缓存推断；记录本次 `evidence_ref`。这仍是适用原完整语法下的公开停止条件，整题 checker 独立保留 |
| 未完成 | 新读数合法但 **<=95**，更新缓存后保留 `no_public_candidate` | 恰好 95 不成功；`terminal_progress_check.status=confirmed` 只表示读数已确认，不表示任务完成；不再尝试新动作 |
| 读取失败 | 错误/运输错误、错误状态或矛盾标志、错误 session/source/scope、缺失或非法数值均不能设置完成；抛 `public_progress_unavailable` | `_check_progress` 拒绝 bool、字符串、NaN/Inf、越界值；失败不以旧缓存兜底，不重试。runtime 的异常读仍按原逻辑保存错误 evidence |
| 预算不足 | 新读仍受原 API/deadline/scope 守卫；API 已满时不 invoke，deadline 前或读后到期不报告完成 | 不新增预算或尾部特权读取；剩余最后 1 次 API 可用于一次确认。预算/中断前未拿到可消费回执时，metadata 可保留 `awaiting_public_read`，不应被分析器当作确认成功 |

## 返回值、作用域与预算链路

新分支要求 normalized `session_id == str(frame.scene_id)`、`source_endpoint == /sessions/current`、`scope == public_session_aggregate_not_full_task_verdict`；拒绝非空 error/运输错误、非成功 status、显式非 True 的 success、显式非 False 的 partial_success，然后调用现有数值校验。生产 `public_progress` 适配器本身会核对原 session ID、公开计数一致性及 target_assignment 的百分比一致性；这些适配器校验失败时返回 error，不提供可用百分比。要求这些 normalized 字段与生产接口一致，未新增私有输入。

原 runtime 在 generator 恢复前检查中断和 scene/epoch；对每个 PublicCall 在 invoke 前检查 API 上限并增加计数，invoke 后再检查 deadline/cancel，下一轮在消费回执前再次检查 scope。新增分支沿用这条路径，不直接调用 client、evaluator 或 checker。读后才写 `evidence_ref`，成功读数严格过阈值才写完成标记；外层不应因 metadata 的 `confirmed` 字样替代完整阈值/状态检查。

静态 diff 限于三个 Harness 文件：注册/继承 v6、描述新契约，以及无候选分支的一次确认。旧 `_check_progress`、周期进度读取、round/repair 退出、原候选策略、server、任务及 checker 未为此改变。其他历史进度调用的接口验收不在本次修复范围内。

## 后续审计口径

新 7 题 × 两版本对照须继续同初态、同预算、独立进程、原 checker 与静态障碍隔离。重点将策略真实动作序列与新增只读调用分开比较：预计触发任务仅增加一次技能进度读取，而不是将 evaluator 原有末次公开读取挪作策略证据。检查原 `no_public_candidate` 边界、API计数、回执内容/来源、对应 evidence ref、runtime 最终标记以及终态控制无重放。

成功率、目标访问数和动作成本不能因为状态名修复而自动宣称提高；新批次需与 v5 同期基准比较，并对照前一 v5 封存核查历史复现。保留读取失败与预算不足的测试负例，不因一个成功题返回 completed 就认定四边界均已在任务实验中覆盖。
