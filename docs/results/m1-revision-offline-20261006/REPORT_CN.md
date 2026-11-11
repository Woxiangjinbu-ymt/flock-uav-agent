# M1 修订版实现与离线验收

M1-R 第一轮修订已完成代码实现和离线验收。它在 M0 工具与原 M1 观察层之上增加同场景动作证据索引、当前任务相关视图和稳定追加注入。默认仍为 `off`，原 `shadow/summary` 模式保留。原 M1 冻结标签和 fast100 结果未改动，本轮没有启动新的在线实验。

这轮可以确认“证据表示与输入适配可工作”，不能确认任务成功率提高。原 M0/M1 的 80/100 与 78/100 仍是旧实验结果，不能把本次离线回放记为新的 100 题成功率。

## 三项修订

| 修订 | 具体行为 | 保持的边界 |
|---|---|---|
| 同场景动作证据 | 从真实成功或部分成功动作回执中提取明确的到达报告，保留 session、command、actor、工具调用、开始结束序号和原回执位置 | 名称不生成 ID 或目标中心；错误文本、请求参数、模型自述不成为发现 |
| 当前任务相关读取 | 从原文识别明确的“执行者访问目标”关系，保留源文本跨度；区分 unknown、reported_only、localized、ambiguous 及本题执行证据 | 不推导完整任务合同，不改变原执行者，不用历史/他机完成抵消本题义务 |
| 稳定追加模型输入 | system 只增加固定规则；初始化视图附于原用户消息的模型副本；工具批次完成且相关视图变化时，附于新产生的真实工具消息副本 | 原图消息和工具回执不修改；旧模型视图逐字保留；不创建无 tool_call 的工具消息，不添加行动门槛 |

任务视图最多 4 个条目，JSON 预算 3,900 字符，连同标记不超过 4,000 字符。超限条目整项省略并计数，原任务全文持续保留。一次注入的预算不等于整段历史预算；事件触发可以少注入，仍需在线测量总 token。

`evidenced` 仅表示本题、该执行者的对应访问有明确回执；拍照主体、顺序、之后的定向运动及完整任务完成均不由它判定。`unattempted` 表示未识别到对应尝试证据，不证明没有行动。无法解析的语言保留为 `unparsed_reference`，不生成硬性义务。

尝试关联接受明确 target_id，或指定执行者的导航目标 XY 与唯一已知目标的 XY 一致；允许飞行高度与地面目标高度不同。后一种情况明确标为坐标关联，不证明意图或到达。原 `5aa74653` 中对 Fixed Target 7/12 的已有导航均显示 blocked，避免误报为没有尝试。

## 路径与历史的语义约束

路径候选只支持名称明确的静态点目标 Fixed Target / Waypoint。完整/部分路径使用实际返回的 successful_points；没有合法起点时标为“确认经过的点”，不补出起点。嵌套导航缺失内部过程、同机并发顺序不清、或位置经过未测量动作后失效时，不拼接确定路径。

失败的拍照等操作也可能触发服务端紧急降落，因此所有可能产生副作用的动作都会使旧位置失去后续路径起点资格；必须重新取得位置或由可靠动作结果确认终点。移动目标和区域目标的名称报告不会获得静态点定位提示。

名称报告和路径提示都只说明过去的动作曾报告接触，不保证目标中心、目标半径或完整几何边界。`reported_only` 不会触发自动搜索；“利用线索做局部定位并交回指定执行者”的 M2 行动机制留待下一阶段。

每个名称在 session 索引保留最近 8 条回执及总量，模型视图通常显示一个有用来源并给出省略数；本 command 的回执不受该跨任务保留窗口截断。新 command 获得新的任务视图，执行证据不会跨 command 自动成立。

索引随黑板生命周期管理：不同 session、未启用黑板共享、显式 clear/reset 均隔离；旧 epoch 的迟到回执不能重新填入已清空索引。现有 session API 没有稳定 reset epoch，自动检测仅覆盖创建标记/环境标记改变及命令计数回退。**同 ID 重载若保留这些标记，调用方必须执行 `reset_task_evidence(session_id)` 或重建 agent；不能声称能自动识别所有外部重置。** 正式比较应每个方法/场景使用独立 agent 实例。

## 离线验收结果

| 项目 | 结果 |
|---|---:|
| Agent 全测试集 | 398 passed，0 failed |
| 新修订测试 | 35 项，包含参数化反例和真实 agent 图测试 |
| 冻结 M1 回放 | 5 场、100 题、3,390 条工具事件 |
| 原始参数、回执、开始结束顺序、重叠关系 | 全部保持 |
| 回放观察器错误 | 0 |
| 回放 JSON 视图最大长度 | 3,900 字符 |
| 此前诊断的 7 个跨题名称线索 | 7 个均可读取为 reported_only |
| 上述线索含可追溯路径提示 | 5 个 |
| 把这些历史线索误记为本题完成 | 0 |
| 展示的报告来源路径 | 全部可解析回原始回执文字，含嵌套 and_sense 结果 |
| 原封存结果输入/产物哈希 | 874 / 9 项全部一致 |

全测试集有 3 条非失败 warning：1 条现有 Starlette 弃用提示，2 条绘图在 Agg 非交互后端运行的提示。第一次全套检查停在原有 `plt.show()`，中止该本地测试后使用 `MPLBACKEND=Agg` 完整重跑；没有中止或恢复任何实验 attempt。

关键反例覆盖：他机/前题到达、错误文本和任意嵌套文字、名称与 ID 分离、同名不同 ID、部分路径、不完整导航过程、同机并发、非移动错误可能降落、移动/区域目标、重置后的迟到回执、多人原文歧义、摘要超限、空输出重试、异步调用和观察器失败时继续原调用。

真实 LangChain 图使用脚本模型检查 invoke/stream 两种路径：工具名称、调用次数、原结果与关闭模式一致；evidence_shadow 的模型消息与关闭模式一致；evidence_prompt 只有固定提醒；evidence 模式保留此前的消息前缀。实际 SDK 序列化也通过 MockTransport 在本地验证：3 次 HTTP 请求、2 次原工具调用，已发消息前缀与工具定义保持一致，HTTP 正文哈希与实际字节相符。模拟传输未连接模型服务。

100 题回放以原账本的场景与任务顺序运行，并重放原事件的 begin/end 序号；输入方法的只有原指令、真实工具参数及回执。世界快照、隐藏检查、旧模型最终回答和任务得分未输入方法。先完成回放，再与此前诊断的名称引用核对。由于旧动作固定，回放不能估计新策略成功率、时间、token 或服务端缓存命中。

## 代表性案例

在 `35ce64d8 / 20742d37` 的五机任务开始时，Fixed Target 10/11 可读到此前 Drone 3 的路径回执：`(154,120,18) → (154,41,18) → (154,129,18)`，来源为先前 command 的 `e39`，起点证据为 `e31`。

这两个目标的 ID 和精确坐标仍为空，知识等级仍是 reported_only；本题指定的 Drone 13 和 Drone 7 都保持“未识别到对应尝试”。系统把可以继续调查的线索交给模型，并保留访问之后的定向运动要求；它没有把 Drone 3 的历史接触转移为两架指定无人机的完成。

7 个引用分布于此前已诊断的 5 道题，其中包含原来成功的题。这里的“恢复线索”不是“新增 7 个成功”，也不能推出能挽回其中所有失败。

## 模式与遥测

由构造参数 `task_progress_mode` 或环境变量 `UAV_TASK_PROGRESS_MODE` 选择，显式构造参数优先。

| 模式 | 用途 |
|---|---|
| `off` | 默认，关闭状态中间件 |
| `shadow` / `summary` | 原 M1 v1 模式，语义保留 |
| `evidence_shadow` | M1-R 索引和离线可见遥测，不向模型注入视图 |
| `evidence_prompt` | 与 M1-R 同样的固定提醒，不注入动态视图，供后续提示对照 |
| `evidence` | 完整 M1-R 视图与追加注入 |

启用 `share_blackboard_by_session=True` 才跨同 session 的 command 复用。工具链落盘沿用 `toolchain_json_recording=True`；本轮没有修改任何运行中的配置或公共 runner。

工具链保留原始 events，并增加 `task_progress.task_evidence`：原文关系、最终视图、每次注入文本、模型请求消息哈希、共同前缀长度与空输出重试的 segment 边界。该层明确标为 provider 序列化前的 LangChain 消息，不能冒充线上正文。

对现有 OpenAI-compatible/DeepSeek 通路，另加 `model_requests_wire`，记录实际 HTTP 正文字节哈希、逐消息哈希、工具定义哈希和中间件请求索引；SDK 自动重试会单独留痕。请求钩子附到原有 HTTP 客户端，保留其超时、代理、连接和重试设置；不保存请求头或密钥，不读取响应流。钩子依赖当前锁定 SDK 的底层客户端入口，未来依赖变化若不可用，会失败开放并记录 `model_request_audit_errors`，验收不能把空记录当作通过。

M1-R 三种模式默认开启该请求哈希记录。后续为 M0 增加同等只读遥测可显式设置 `UAV_MODEL_REQUEST_AUDIT=1`；未设置时关闭模式的模型客户端构造保持原样。Ollama 没有本轮 HTTP 钩子支持，应只使用其明确标注的序列化前记录。输入/输出与 cache hit/miss usage 沿用现有 provider 遥测。

## 可复核入口与下一步

实现：[task_evidence.py](../../../agent4drone/task_evidence.py)、[task_evidence_context.py](../../../agent4drone/task_evidence_context.py)、[model_request_audit.py](../../../agent4drone/model_request_audit.py)。集成改动限定于 [uav_agent.py](../../../agent4drone/uav_agent.py) 与 [blackboard.py](../../../agent4drone/blackboard.py)。原 [task_progress.py](../../../agent4drone/task_progress.py) 未改动。

离线复核可在仓库根目录执行：

```bash
MPLBACKEND=Agg agent4drone/.venv/bin/python -m pytest -q agent4drone/tests
agent4drone/.venv/bin/python experiments/analysis/replay_m1_revision.py --output /tmp/m1-r-replay-check
```

[summary.json](summary.json) 给出回放汇总；[replay.json](replay.json) 保留固定 100 题的初始/最终视图；[recovered-references.json](recovered-references.json) 保存 7 个代表引用；[pytest.xml](pytest.xml) 保存全测试结果；[input-sha256.json](input-sha256.json) 和 [source-sha256.json](source-sha256.json) 分别记录回放输入与当前实现；[verification.json](verification.json) 保存封存结果完整性和验收状态。

当前实现位于工作区，尚未作为新的在线实验方法提交冻结；旧 `m1-progress-state-v1` 仍指向 `3161fc88c7d4c9646e2190eaa22816fedc02f380`。现有无关工作区修改保持原状。

建议下一步是审阅后冻结 M1-R，并另行决定是否执行 M0 / 固定提醒 / M1-R 的完整 fast100 对照。主指标仍为原任务成功率、同题 W/L、逐场差异，时间、token、cache 为成本约束。只有在线结果才能决定是否进入 dev240；本轮没有推进付费实验或 M2。
