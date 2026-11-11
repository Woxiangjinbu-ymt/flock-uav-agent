# 当前 applicability 与普通 guarded macro：离线控制语义核验

日期：2026-11-03。**本批确认了限定条件下的行为一致：当前 applicability 持续驱动同一组带守卫技能，与普通 guarded macro 在全部 12 个声明案例中的调用、回执、技能 frame 和终态一致。failure 只在 3 个真实失败检查点增加暂停；脚本立即 resume 后恢复为相同轨迹。** 这是控制语义验证，不是模型成绩、自然恢复收益或新算法证明。

## 封存与分母

- [固定案例](../../../../experiments/harness-method-v1/method-cases.json)和[协议](../../../../experiments/harness-method-v1/method-protocol.md)：12 案例 × 3 臂 = **36 个脚本执行**，全部保留、全部 worker 正常退出，36 个独立 PID，串行 join。
- 正式结果：[run-01/report.json](run-01/report.json)，**879/879 检查符合预期**；[只读复核](verification.json) **1066/1066 通过**，含 140 个受 manifest 绑定文件、66 份源码/配置副本。
- [manifest](run-01/manifest.json) SHA-256：`79532166df1d99d1efcbbf19739ced75eaa5183c923d8bad5d718551babdd67c`。
- 预声明案例 SHA-256：`eec6db0109e918eadc01e8bbb118a73b675a0d7c5d4b6f918d88aba9602fd061`；案例没有因运行结果更换。
- 当前 revision 为 `observed_target_v7`。源执行来自工作区，运行前复制并记录 hash，结束后核对未变；不声称子进程从封存副本导入。
- 本批模型调用 **0**，socket 网络尝试 **0**；ASGI 是进程内仿真，不是 provider 请求。启动技能和 resume 全部来自脚本。

每臂的夹具任务实际完成为 **4/12**；另 8/12 是预声明的失效、预算或入口拒绝案例，全部保留。不能把边界断言通过写成“36/36 任务成功”。后验 checker 是执行完毕后在同一 worker 中调用的独立夹具评分函数，读取实际状态/历史；它没有进入技能输入，也不冒充独立 scorer 进程或完整 Agent/SDK 验收。

## 比较的实现

[validate_harness_method.py](../../../../scripts/validate_harness_method.py) 中的测试宏自己驱动生产 `make_skill_factory` 返回的 generator，不调用 `HarnessRuntime._drive`，也没有调度器或 pause/resume 策略。它保留本批共同 scene/epoch、API 上限和未知回执处理；技能内 actor、效果与局部修复守卫由三臂共享。另两臂调用真实 `HarnessRuntime`，分别启用 applicability/failure。

真实动作通过真实 simulator 的 agent 角色公开 API 执行。可修复错误来自预先存在的隐藏静态端点障碍，以及 UAV 的 `max_altitude=10` 无法满足 20 米起飞的真实拒绝。没有临时增删障碍或伪造成功/受阻回执。unknown 测试分别在真实动作已执行后、未派发前丢弃回执，私有注入标记仅供后验审计。

逐例比较原始公开调用/参数/回执、底层 ASGI 请求与响应 hash、检查点、frame、第一次控制返回和终态。物理对象原件完整保留；终态语义比较仅忽略 `session.request_history` 中随机 request_id 和真实墙钟 timestamp。全部障碍几何在各次执行前后保持一致。

## 逐例结果

下表的动作/调用数是**每臂**最终值；三臂在同例内完全相同。任务是否完成由后验夹具 checker 判断。

| 案例 | 终态/原因 | 动作尝试 / 技能公开调用 | 任务完成 | failure 暂停 |
| --- | --- | ---: | --- | ---: |
| 正常短链 | completed | 3 / 10 | 是 | 0 |
| 正常搜索 | completed | 13 / 49 | 是 | 0 |
| 固定隐藏端点受阻 | completed | 23 / 90 | 是 | 1 |
| 起飞能力不足后禁用 actor | completed | 13 / 53 | 是 | 1 |
| unknown，动作已生效 | unknown_action_effect_observed_do_not_replay | 1 / 4 | 否 | 0 |
| unknown，动作未派发 | unknown_action_effect_observed_do_not_replay | 1 / 4 | 否 | 0 |
| actor 公开状态改变 | actor_state_changed_between_units | 1 / 5 | 否 | 0 |
| 修复预算为 1，第二次拒绝 | local_repair_budget_exhausted | 2 / 6 | 否 | 1 |
| scene epoch 改变 | scene_scope_changed | 1 / 3 | 否 | 0 |
| API 预算为 3 | api_budget_exhausted | 1 / 3 | 否 | 0 |
| 完整命令追加拍照义务 | unsupported_skill_or_full_task_grammar | 0 / 0 | 否 | 0 |
| 未声明静态障碍合同 | shared_endpoint_requires_static_obstacle_contract | 0 / 0 | 否 | 0 |

每臂合计 59 次动作尝试、227 次技能公开调用；含公共启动观测的实际 ASGI 请求为 260 次。unknown 未派发案例仍保留一次动作尝试，故不是全部尝试都成为 ASGI 请求。每臂有 42 个检查点，其中 3 个 failed checkpoint，另有 4 条已知失败动作回执、2 条未知动作回执和 7 次 needs_replan 终态。失败回执数、检查点数和必须退出数不能互换。

## 首次交接与最终执行

| 案例 | failure 首次返回的位置 | applicability / macro 首次返回 |
| --- | --- | --- |
| 固定端点受阻 | 第 8 次技能公开调用、第二次动作后 paused | 最终第 90 次调用、第 23 次动作后 completed |
| 起飞能力不足 | 第 4 次调用、第一次动作后 paused | 最终第 53 次调用、第 13 次动作后 completed |
| 修复预算耗尽 | 第 4 次调用、第一次动作后 paused | 第 6 次调用、第二次动作后 needs_replan |

三个暂停位置逐一对应真实 failed checkpoint，而不只是总数相等。failure 中第一次起飞失败已在暂停前禁用 actor；暂停不是“还没作任何局部处理”。两个 unknown 案例均只尝试一次动作，随后只读一次对应 actor 状态，再退出，游标没有把后读状态改写成过去动作成功。

这些暂停可成为真实模型重新决策的机会，本批没有模型，因此没有测量“节省 3 次模型调用”、token、模型恢复率或等待时间。立即脚本 resume 仅用于保持后续决策一致、隔离控制规则差别。

## 测试与复核

专项 **42 tests passed**，包括不调用 runtime 的宏驱动、动作/终态变异、同暂停次数但错误交接位置、网络拒绝计数、精确元数据规范化、封存篡改及重新生成 hash 后的语义错误拒绝。

在仓库根目录，未额外设置 PYTHONPATH：

```bash
agent4drone/.venv/bin/python -m pytest scripts/tests/test_harness_method_validation.py -q --tb=short --show-capture=no
agent4drone/.venv/bin/python scripts/validate_harness_method.py --verify --output docs/results/harness-method-validation-20261103/mechanism/run-01
```

正式运行命令是相同脚本移除 `--verify`；输出目录已经存在时拒绝覆盖。脚本源码 SHA-256 为 `973c4462883f707c6b5b7fae7e7713cb6be6b867c23a6063c73f34a8635cd750`。

开发包 `tmp/harness-method-dev-01` 保留。它与正式批使用同一组 12 夹具，早期 621 项检查通过；随后在不改夹具/运行链的前提下增加交接精确位置、底层 API 与封存验证等审计检查。开发执行不能与本批相加宣称更多独立样本。

## 可主张与仍需真实验证的部分

本批支持当前实现对这些声明案例的**条件行为一致**与失败检查点交接差异；因此普通 guarded macro 是等价的强工程基线，不能作为另一种独立算法虚增对照。共享技能、固定 simulator 时间和立即脚本恢复均属于前提。没有证明任意 generator、并发、取消、墙钟截止或动态世界中的普遍等价。

后续需要另授权的真实模型证据包括：同最终版本和同底层能力的自然选择与失败后行动，公平事件规则和实际暴露数，完整任务义务评分，以及总 token/时间/动作成本。公开恢复投影与范围化共享各需独立消融。此处没有进入第五项、付费采数或训练，也没有把新文档/测试数量换算为论文新颖性。
