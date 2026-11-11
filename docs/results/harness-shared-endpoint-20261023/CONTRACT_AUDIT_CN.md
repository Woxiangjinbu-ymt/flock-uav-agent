# 跨无人机同 XYZ 目的点失败证据共享：执行契约审计

日期：2026-10-23。范围：只读检查当前服务端与 v4 Harness，不运行模拟器、模型或新试验，不修改生产代码。本文是实现前的语义审计；建议中的 v5 条件不是已实现或已验证结果。所读文件及 SHA-256 见 [审计输入](CONTRACT_AUDIT_SOURCES.json)。

**结论：在明确保证障碍不变、控制器语义一致的单个计划中，严格确认的目的点冲突可以跨 actor 共享精确 XYZ 负证据。普通在线场景不能仅凭 scene ID / 本地 epoch 相同就这样共享。** 服务端目的点碰撞不读取 actor 的尺寸、半径、能力、起点或权限，但读取目的点高度和当前障碍几何；服务端允许外部修改、删除障碍与重新装载场景，现有本地守卫不能完整发现这些变化。

## 1. 目的点判定究竟依赖什么

| 检查 | 源码证据 | 对共享的含义 |
| --- | --- | --- |
| `move_to` 绑定请求的 drone ID 与显式 XYZ；缺 z 才取该机当前高度 | [API](../../../server/api/server.py#L1650)，[命令分支](../../../server/controllers/drone_controller.py#L1051) | 只共享实际发送的完整 XYZ；不能把同 XY、不同 z 合并。当前搜索显式发送 z=20 |
| 命令先检查紧急电量/状态，再检查 `hovering/flying/moving`；路径函数先检查该机最大高度、负高度、起点相关耗电与电量 | [紧急状态](../../../server/controllers/drone_controller.py#L944)，[路径验证](../../../server/controllers/drone_controller.py#L748) | 这些是 actor 或起点相关拒绝，不能生成共享目的点证据。某机在该点遇到明确碰撞不代表另一机已通过状态/电量检查 |
| 单点包装为一个 waypoint；先查目的点，后查起点到终点的线段，最后才更新移动状态/位置 | [路径验证和执行顺序](../../../server/controllers/drone_controller.py#L856) | 精确目的点冲突与路径冲突是不同分支；路径错误不能推广到其他 actor 或其他起点 |
| `_check_position_collision(position)` 只把 XYZ 和固定 `margin=0.0` 传入障碍控制器，没有 drone 参数 | [目的点碰撞](../../../server/controllers/drone_controller.py#L1719) | 当前实现没有 actor 半径、机型、任务能力、感知半径、权限或起点参与目的点几何谓词 |
| 点碰撞使用 `contains` 或 `touches`；height=0 对全部高度阻塞，否则 z≤height 时阻塞；遍历当前障碍集合 | [点碰撞](../../../server/controllers/obstacle_controller.py#L255) | XYZ 和障碍集合/几何/高度不变时，几何拒绝跨 actor 相同。不能推断其他高度、邻近坐标或一个障碍区域 |
| 几何取圆/点半径、椭圆轴长、多边形顶点；XY 投影与 height 判定分开，当前碰撞逻辑不把 obstacle.position.z 加到 height 上 | [几何构建](../../../server/models/obstacle.py#L396)，[高度分支](../../../server/controllers/obstacle_controller.py#L299) | 只证明当前代码的语义；不能替代现实三维障碍/机体碰撞模型。即便起点高度不同，目的点判定仍只看终点 z |
| 路径碰撞读取起终点 XYZ，使用 `max(start_z,end_z)` 过滤高度，再做线段相交 | [路径碰撞](../../../server/controllers/obstacle_controller.py#L71) | 路径失败有不同几何语义，换起点后仍应允许原逻辑重试 |
| 权限在 API 入站统一检查角色，不按 actor 设置碰撞权限；命令回执包含 command_id、drone_id、command、status、message | [角色检查](../../../server/api/server.py#L131)，[move_to API](../../../server/api/server.py#L1650)，[回执](../../../server/controllers/drone_controller.py#L670) | 权限错误不是目的点证据；源 actor 必须与实际请求和回执一致。不能把 A 的失败伪造为 B 的已执行失败 |

明确目的点错误由 `Waypoint 1 conflicts with obstacle: ...` 在 `MOVE_TO` 分支替换为 `Target position conflicts with obstacle: ...`；它与 `Path to waypoint 1 blocked: ...` 不混用。错误文本只包含首个障碍的类型和名称，**不包含可靠障碍 ID、版本或完整几何**。[来源](../../../server/controllers/drone_controller.py#L1063)

现有 `_endpoint_conflict` 仅接受一致 actor 的 `move_to`、`status=error`、`command_outcome=blocked` 及完整匹配的 circle/ellipse/polygon 目的点消息；point 类型目前保守不接受。`command_outcome` 对 partial、矛盾成功字段、未知回执和非几何错误另行处理。最小共享应保留此门槛，不扩展成按关键词猜测。[v4 分类器](../harness-blocked-candidate-20261023/run-20261023-01/source/agent4drone/harness_skills.py#L14)，[回执解释](../../../agent4drone/command_results.py#L40)

## 2. 障碍并非平台级不可变对象

- 自动仿真循环更新移动目标和天气，没有自动障碍运动；这说明默认 tick 不移动障碍，**不证明障碍不可被修改**。[循环](../../../server/main.py#L160)
- `POST /obstacles`、`PUT /obstacles/{id}`、`DELETE /obstacles/{id}` 允许 SYSTEM/ADMIN 增加、改变和删除障碍。更新可改变 position、radius、width、length、vertices、height；直接修改当前障碍对象，删除直接移出集合，没有向 Harness 推送地图版本。[API 创建](../../../server/api/server.py#L2832)，[更新/删除](../../../server/api/server.py#L2911)，[控制器](../../../server/controllers/obstacle_controller.py#L22)，[字段更新](../../../server/models/obstacle.py#L151)
- 同 ID 场景覆盖、set-current 可清空并重装全局控制器；reset/重装也属于计划失效事件。不能把同 scene ID 当作同一障碍快照。[场景覆盖 API](../../../server/api/server.py#L3378)，[set-current](../../../server/controllers/session_controller.py#L260)，[清空/装载](../../../server/controllers/session_controller.py#L557)
- 障碍名称可改且不保证唯一；`add_obstacle` 可按给定 ID 替换已有对象。不能从错误消息中的名称补出障碍 ID/几何，更不能用名称相同认定版本未变。[更新名称](../../../server/models/obstacle.py#L166)，[按 ID 存储](../../../server/controllers/obstacle_controller.py#L42)

因此，无 TTL 的单计划共享只适用于明确隔离外部几何 writer、无场景重装、同一碰撞实现的运行。时间短或没有看到变更日志不足以替代该运行前提；增加 TTL 也不能保证有效期内没有修改。

## 3. 当前 epoch 和外部动作守卫的真实边界

以下 Harness 引用固定指向已封存 v4 源码，避免后续候选修改造成行号与实现前审计混淆。

- `scope` 返回本地 `board.session_id, ledger.index.epoch`；epoch 只在 `SessionEvidenceIndex.clear()` 时增加，黑板 clear 会转调该方法。它不是服务端地图版本。[安装接线](../harness-blocked-candidate-20261023/run-20261023-01/source/agent4drone/harness_integration.py#L140)，[epoch](../../../agent4drone/task_evidence.py#L79)，[黑板 clear](../../../agent4drone/blackboard.py#L38)
- Agent 的 session marker 用 created_at、environment_id、累计命令数下降启发式发现部分 reset，或显式调用 `reset_task_evidence`。普通障碍 PUT/DELETE 不保证改变这些标记；此逻辑也不是每次技能 API 前读取服务端地图。[marker/reset](../../../agent4drone/uav_agent.py#L552)
- runtime 在 start/resume 和每次 generator 推进前比较 scope；deadline/cancel 在 API 前后检查；预算在派发 API 前检查。已终止计划不能 resume/restart。这些保证本地观察到的 epoch/终止后不继续派发，不保证发现未通知的服务器变化。[控制和推进](../harness-blocked-candidate-20261023/run-20261023-01/source/agent4drone/harness_core.py#L200)
- `external_begin` 仅由同一个 Harness middleware 包裹的原工具调用触发。对 public_search，任一被 `_ACTIONS` 分类的外部动作会使 ready/paused 计划 `needs_replan`；活动工具阻止控制操作。其他客户端/控制台/服务端直接变更没有自动经过此钩子，`_ACTIONS` 也没有障碍 CRUD 或 session reload。[middleware](../harness-blocked-candidate-20261023/run-20261023-01/source/agent4drone/harness_integration.py#L64)，[失效](../harness-blocked-candidate-20261023/run-20261023-01/source/agent4drone/harness_core.py#L140)，[动作集合](../../../agent4drone/task_progress.py#L32)
- 当前每轮前读 actor 状态会发现位置变化；progress 读取会检查当前 session ID。但位置未变、scene ID 未变时不能据此发现远处障碍改变；进度也不是每次 API 都读取。[状态检查](../harness-blocked-candidate-20261023/run-20261023-01/source/agent4drone/harness_skills.py#L233)，[进度检查](../../../agent4drone/task_evidence_r2.py#L87)

## 4. 公开失效信号与不能推断的事情

`nearby` 返回局部可感知障碍的 ID、几何、height 和 last_updated；观测范围依赖 actor 的 perceived_radius、位置和 2D/3D 设置。相同 ID 再次出现且几何变化可构成明确静态契约违约；last_updated 单独变化也可能只是名称/描述变更，不能与几何变化画等号。[感知](../../../server/controllers/drone_controller.py#L603)，[公开字段](../../../server/api/server.py#L925)

当前 `PublicSearchPolicy.accept` 只吸收新 ID，遇到已知 ID 直接 continue；黑板更新旧实体不等于 policy 几何已更新。发现同 ID 几何变化时，最小保守处理应退出整个计划，不能仅删除共享 mask 后继续使用旧几何。[policy](../../../agent4drone/coordinated_search_r2.py#L96)

空 nearby、某障碍暂未出现、新增无关 ID、其他 actor 起点变化，都不证明原目的点已解除阻塞。公开 `GET /obstacles/{id}` 可查询已知 ID，但本次错误消息不提供 ID，也不含在当前技能 allowlist 中；不应据名字猜 ID、读取隐藏场景或新增全图查询来支持本次最小修复。即使补一个查询也有并发变更窗口，不能取代静态 writer 隔离。[单障碍查询](../../../server/api/server.py#L2890)，[技能 allowlist](../harness-blocked-candidate-20261023/run-20261023-01/source/agent4drone/harness_integration.py#L128)

## 5. 建议采用的最窄契约与拒绝条件

1. **显式静态运行声明，默认关闭。** 仅 v5 的 public_search 使用。runtime 参数默认 False，integration 独立环境开关仅精确 `1` 视为声明；未声明须在首个公开 API 前拒绝。声明需要部署/runner 实际保证，不能自动从 epoch 或任务名称推导。
2. **范围限定。** 仅同 task、plan、scene、epoch、同一服务端碰撞语义、固定当前 z=20 的精确 XYZ；generator 私有存储，暂停可保留，新计划不可继承。全 fleet 共享候选点排除项，同时保留源 actor、原 move args/receipt/evidence_ref、原前后公开 XYZ 与 plan scope。候选 index 只是本 plan 固定 lattice 的索引，不是跨计划空间标识。
3. **继承 v4 采纳门槛。** 严格目的点回执、actor/command 一致、非 partial/contradictory/unknown，并在原 move 后取得可信公开状态，确认完整 XYZ 未改变，再登记共享。原 action ref 在后续 status/nearby 读取前捕获；不把状态读取 ref 当作失败动作 ref。
4. **失效与违约。** epoch/scene 改变、已知相关外部动作、cancel/deadline/预算终止沿用原控制；新计划不污染。对已见障碍维护深拷贝的几何指纹（type、position、radius、width、length、vertices、height），同 ID 公开几何改变则退出整个计划。指纹检测是可见违约检测，不声称能发现未观察到的更新/删除。
5. **拒绝推广。** 路径、状态、权限、电量、最大高度、部分移动、未知回执、动作/回执 actor 不一致、前后位置改变、未知坐标、不同高度、不同碰撞实现、允许并发几何 writer 或未保证静态者，均不能据此跨 actor 共享。也不能把一个点扩为区域、按障碍名称传播、自动恢复或增加修复预算。

作为对照环境，现有 `World` 使用进程内 TestClient（没有 lifespan/background simulator thread），冻结时间线只更新 targets/environments，`FullWorld` 将 API 作为受锁保护的事务处理；这些代码结构允许建立静态隔离，但新 runner 仍需在运行前明确核验隔离条件并在运行后检查障碍几何摘要一致。摘要只作隔离评估证据，不把隐藏障碍几何输入策略。[World 初始化](../../../scripts/harness_v2_world.py#L58)，[时间线](../../../scripts/harness_v2_world.py#L141)，[FullWorld 事务](../../../scripts/harness_full100_world.py#L94)

本次审计支持按上述条件实现 opt-in 最小候选；不支持无条件上线、动态障碍适用性或性能增益结论。后续应先验收静态声明拒绝、跨 actor 排除与原始证据归属、同 XY 不同 z、路径不共享、几何变更退出、暂停/终止/预算兼容，再进行预先固定的同任务同初态同预算对照。旧日志中的跨 actor 后续失败事件 1/7 只是诊断依据，不是新机制已消除的数量。
