# 重复障碍候选诊断：公开轨迹精确重建

日期：2026-10-23。来源为已封存的 `harness-flight-state-20261023/run-20261023-01`，只分析 `24ac1838-flight_state_v3` 的公开技能调用。此次没有修改生产策略、预算或旧封存，没有启动模拟器、模型或网络调用。

**两组重复失败的直接原因是：失败屏蔽键包含 actor 的起点；actor 成功移动到另一位置后，同一目的点对应一个未屏蔽的新键。旧键从未删除或到期。** 目的点冲突回执被归为普通 `blocked`，未被保存为与起点无关的目的点禁选信息。与此同时，公开感知始终未提供回执所指 Polygon 47/7 的几何，因此现有几何过滤也未排除这些候选。

这说明缺少“目的点冲突”与“路径阻断”的记忆区分，不说明所有 `blocked` 都应永久封禁目的点：路径阻断可能与起点有关。本诊断没有验证任何修复能让整题完成。

## 冻结与重建方法

先核验原 campaign 全部 273 项封存哈希，再将输入和分析脚本复制到独立 [`diagnosis-01`](diagnosis-01/manifest.json)，执行前冻结 50 项文件。原 API 日志经明确筛选，只保留 `phase=task`、`admin=false`、`origin.kind=skill` 的 **532 条公开记录**；不把准备期读取、最终 evaluator 读取、隐藏 checker 或私有快照传给重建过程。

重建执行原冻结 `public_search` 生成器及 `PublicSearchPolicy`，给每个生成的公开调用提供原回执，逐项核对 tool、actor、参数及 action 标志。`public_progress` 使用冻结源码中的原函数，黑板仅由原公开 nearby 更新。分析用子类记录 `choose/accept` 前后状态，调用原方法完成全部策略运算，不添加规则。它是确定性回执回放，不是新一次任务试验。

结果：**532/532 调用顺序和参数一致，109 次 choose、107 次 move 全量重建**；原终态同为 `local_repair_budget_exhausted`，cursor 107、repairs 17、API 532、unit 106。所有冻结输入在执行后无变化，网络尝试为 0。证据见[重建摘要](diagnosis-01/summary.json)、[每次 choose](diagnosis-01/choice-trace.json)、[每次 accept](diagnosis-01/accept-trace.json)及[封存清单](diagnosis-01/seal.json)。

## 第一组：`4e22b83f` → `(1715,175,20)`

目的点的零起始 candidate index 为 **578**；回执对象名是 `Polygon Obstacle 47`。

| 阶段 | 公开读取/动作请求 | 策略状态 |
| --- | --- | --- |
| 第 2 轮首次选择 | 状态 292；move 293；失败后 nearby 295 | 起点 `(1587,169)`，候选 578 未 selected、当前 tried 键不存在；新增覆盖分数单元 14；已知障碍 36 个，目的点及路径均未被已知几何阻挡 |
| 接受首次失败 | 293 报目的点与 Polygon 47 冲突 | 加入 `('4e22b83f',(1587,169),578)`；同起点候选立即不可选，未加入 selected；295 没有提供 Polygon 47 或任何新增障碍 |
| 第 3 轮绕开旧键 | 状态 412；成功 move 413；nearby 415 | 578 的旧键仍命中，所以选择候选 531 到 `(1575,245)`；成功后只把 531 加入 selected，actor 起点变为 `(1575,245)` |
| 再次可选 | 接受 413 成功后 | 旧键仍保留；新键 `('4e22b83f',(1575,245),578)` 不存在。578 的新增分数单元仍为 14、未 selected、无几何阻挡，恢复可选 |
| 第 4 轮重复失败 | 状态 531；move 532；nearby 534 | 再次选择 578；Polygon 47 仍不在 60 个已知障碍中，目的点及路径几何检查均 false；再次收到相同目的点冲突，新增第二个起点键 |

第二次失败后的 tried 集合同时保留旧起点和新起点两条记录；不是旧记录被覆盖或超时删除。actor 的位置变化由公开状态与成功回执确定，未参考私有世界状态。

## 第二组：`869a8e16` → `(1715,1155,20)`

目的点的零起始 candidate index 为 **592**；回执对象名是 `Polygon Obstacle 7`。

| 阶段 | 公开读取/动作请求 | 策略状态 |
| --- | --- | --- |
| 第 2 轮首次选择 | 状态 312；move 313；失败后 nearby 315 | 起点 `(1785,1225)`，候选 592 未 selected、当前 tried 键不存在；新增覆盖分数单元 12；已知障碍 39 个，目的点及路径均未被已知几何阻挡 |
| 接受首次失败 | 313 报目的点与 Polygon 7 冲突 | 加入 `('869a8e16',(1785,1225),592)`；同起点候选立即不可选，未加入 selected；315 没有提供 Polygon 7 或任何新增障碍 |
| 第 3 轮绕开旧键 | 状态 432；成功 move 433；nearby 435 | 592 的旧键仍命中，所以选择候选 594 到 `(1715,1295)`；成功后只把 594 加入 selected，actor 起点变化 |
| 再次可选 | 接受 433 成功后 | 旧键仍保留；新键 `('869a8e16',(1715,1295),592)` 不存在。592 的新增分数单元仍为 12、未 selected、无几何阻挡，恢复可选 |
| 第 4 轮重复失败 | 状态 552；move 553；nearby 555 | 再次选择 592；Polygon 7 仍不在 61 个已知障碍中，目的点及路径几何检查均 false；再次目的点冲突，新增第二个起点键 |

两组每次 choose 的完整 selected/tried 集合、已知障碍 ID/名称、几何 hash、所选候选及失败后状态见[配对证据](diagnosis-01/target-pairs.json)；候选在每次 move 接受后是否可选的全量轨迹见[候选状态变迁](diagnosis-01/watched-candidate-transitions.json)。

## 代码与公开证据共同支持的因果链

冻结策略 [`choose`](diagnosis-01/source/agent4drone/coordinated_search_r2.py) 仅用 `index in selected` 和 `(actor,start,index) in tried_edges` 排除已尝试候选；`accept` 无条件记录当前边，但仅在 `completed` 时更新 actor 位置、selected 和覆盖信息。源码没有 tried 记录的 TTL、删除、轮数失效或版本替换。轮数和 sequence 仅用于新增障碍的来源记录。

几何过滤依赖 nearby/inherited 的实际障碍字段。两组首次失败后的 nearby 只返回其他已知障碍，没有提供所报对象的几何；中间成功移动虽新增其他障碍，Polygon 47/7 直至此条任务轨迹结束仍未进入 `known_obstacles`。因此已有 blocked 几何对目标点及对应线段均不相交。这里只能说明**缺少公开几何信息**，不能凭对象名称推断多边形坐标、距离、视野边界或真实可绕行路径。

冻结 [`command_results.py`](diagnosis-01/source/agent4drone/command_results.py) 将上述目的点冲突统一归为 `blocked`。冻结 [`harness_skills.py`](diagnosis-01/source/agent4drone/harness_skills.py) 给 `policy.accept` 的是 outcome、实际位置及 nearby 障碍，没有传递“目的点冲突/路径冲突”的细分证据。因此策略未能保留已有公开回执中更具体的限制。

## 对最小修复的约束

可据此设计一个独立版本：只针对明确的**目的点障碍冲突**，保留不依赖起点的候选拒绝证据；普通路径阻断继续使用起点相关的边记忆。证据需有当前任务/场景作用域，不能通过障碍名称补造几何，不能把一般错误或未知结果当成目的点永久不可达。

本诊断只确认上述两组重复选择的原因。候选屏蔽是否能减少错误、节省既有修复预算并改善整题结果，仍须用相同任务初态、相同预算、原 checker 做修复前后对照。**不得据此提高 API、repair、轮数或时间预算，也不预先承诺整题成功。**
