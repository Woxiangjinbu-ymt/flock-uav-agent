# 悬停完成标准与 input_json 类型修复：本地验收

日期：2026-11-03。按用户要求修复完成判定与工具输入类型，保留 compact 输入、搜索 v9、隔离、归档和恢复；不进入第五项。**最终固定 16 槽及 171 条检查全部符合预期，最终 1,100 项代码测试通过。新增真实 provider 调用 0，Teacher / SFT / RL 均未开始。** 这是本地契约验收，不是自然模型成功率或 token 收益。

## Confirmed Decisions — 悬停怎样才算完成

当前仿真器的 [take_off](../../../server/controllers/drone_controller.py) 已同步到达指定高度并置为 hovering；hover 会设置状态、耗电和记录事件，duration 仅写入历史，没有等待或连续保持时长验证。因此必须区分三个要求：

| 用户要求 | 完成证据 | 不足以证明完成的情况 |
| --- | --- | --- |
| 起飞到 H 米并原地悬停，无持续时间 | 本回合匹配主体/高度的起飞尝试；起飞前公开 XY 锚点；动作后对同一无人机的新鲜状态读取确认高度、XY 未变、空中 hovering、speed 若有则为 0 | 只有起飞回执、只写完成、读到其他无人机或动作前的旧状态 |
| 明确执行一次 hover 命令 | 除上述适用状态条件外，必须有对应无人机的成功 hover 回执、正确顺序及之后的状态读取 | 起飞自动进入 hovering，不能替代明确要求的 hover 动作 |
| 持续悬停 N 秒或“片刻” | 当前接口不足以验证实际持续时间，返回未验证 | duration 参数回显、一个 hover 事件、瞬时 hovering 状态 |

无时长的状态目标已满足时，不要求为补事件而重复 hover。状态与位置核验只使用策略本来可见的公开 ledger；不读私有 checker、不额外查询环境、不补动作、不增加模型调用。位置和速度容差显式为 1e-6。起飞效果未知时，允许后续状态证明当前目标已满足，**不回写历史 unknown，也不自动重放**；明确要求的未知 hover 动作仍不能凭状态冒充已确认执行。

`hover_completion_v1` 当前只完整匹配约定的少量英文单机指令，详见 [实现](../../../agent4drone/hover_completion.py) 和 [冻结协议](PROTOCOL_CN.md)。包含额外条款、多机或不支持措辞的悬停指令保留全文并标为 unsupported；非悬停为 not_applicable。它不是通用自然语言判题器，也不证明连续物理保持或外部并发写入下的状态。

## 实现与版本

[本地 v4 profile](../../../experiments/hover-input-contract-v1/profile.json) 显式启用 `completion_contract=hover_completion_v1` 和 `tool_input_contract=json_object_or_string_v1`，继续使用 `compact_tools_v1 + search_confirm_v9`、evidence_harness 与执行状态 v1。原 profile 默认仍是 off/string_v1；API/GUI 默认未切换，online 授权为 0。

完成结果单列 completion_assessment、task_completed 和 completion_claim_accepted。确认完成才置 task_completed=True，证据不足为 null，不猜成 False；非悬停不作完成评价。success 保留原“正常返回”含义。completion_marker_present 仅记录标记存在，不猜模型自然语言语义。无论是否出现 `[TASK DONE]`，悬停证据不足时用户 output 都明确显示未验证；正常返回时的原文本留在 model_output，原消息和 wire 归档不改。空响应恢复提示也允许诚实结束于未验证，不强迫模型声明完成。

[input_json 适配](../../../agent4drone/tool_input_contract.py) 将原生工具 schema 明确设为 object（优先）或 JSON-object string；全部 28 个生产参数工具及注入的 run_harness 在建图前统一适配，无参工具不改。合法对象只在调用 handler 的边界编码为旧字符串；不补参数、不猜 ID、不重写模型历史或 call ID。非法数组、标量、畸形 JSON、非有限数、非 JSON 值、循环或过深嵌套在 handler 前拒绝，模型可根据错误自行修正接续。

重复键检测覆盖旧内层 JSON 字符串。原生 object 的重复键可能已被上游 JSON 解析折叠，当前层不能事后发现，不能宣称所有原始 arguments 的重复键都已阻断。LangChain 派生 schema 保留 object|string，但根级 additionalProperties 的传输行为不同于 Pydantic 运行时 extra=forbid；本轮保证以实际 schema/wire 与执行边界测试为准。

## Verified Results — 固定本地回合

使用旧开发题 c74 的公开场景，主批 12 槽、明确动作派生 2 槽、时长派生 1 槽、旧输入契约对照 1 槽。派生指令只修改新 study 的公开 command/hash 并记录来源，原场景初态、task/checker、原数据划分均不改。四组输入与源码均在首个 worker 前冻结，每槽新 worker、退出后独立 scorer；网络阻断。模型决策及 usage 为脚本合成。

最终 [local-03 验收](local-03/validation.json)：16 正常返回、16 独立评分、32 个不同进程，171/171 检查通过；8 个公开目标确认、7 个按预定边界未验证、1 个旧版未启用判定。**16/16 是预定正反例行为符合率，不是自然任务成功率。** 原高度/离地 checker 只作辅助，尤其不能判断派生的动作或持续时间。

| 用例 | 新公开完成判定 | 实际动作数 | 原辅助 checker |
| --- | --- | --- | --- |
| 状态目标／旧字符串 | 确认完成 | 1 | 通过 |
| 状态目标／对象 | 确认完成 | 1 | 通过 |
| 缺少最终状态查询 | 未验证 | 1 | 通过 |
| 查询了错误主体 | 未验证 | 1 | 通过 |
| 高度不符 | 未验证 | 1 | 不通过 |
| 状态查询后又降落 | 未验证 | 2 | 不通过 |
| 原工具起飞回执丢失 | 确认完成 | 1 | 通过 |
| 子程序起飞回执丢失 | 确认完成 | 1 | 通过 |
| run_harness 对象输入 | 确认完成 | 2 | 通过 |
| 数组拒绝后修正 | 确认完成 | 1 | 通过 |
| 标量拒绝、未修正 | 未验证 | 0 | 不通过 |
| 畸形字符串拒绝后修正 | 确认完成 | 1 | 通过 |
| 明确要求 hover、未执行 | 未验证 | 1 | 通过 |
| 明确要求 hover、已执行 | 确认完成 | 2 | 通过 |
| duration 回显但无持续证据 | 未验证 | 2 | 通过 |
| 旧 string_v1 对象拒绝 | 旧版未启用 | 0 | 不通过 |

字符串/对象正例的初态、实际动作及完成结果相同，均只有 1 次起飞，没有为状态目标补 hover。原工具与子程序的两个故障例各保留 1 个历史 unknown，后续模型脚本读取状态确认目标，每例实际只起飞 1 次，未重复起飞或补 hover。显式动作例则必须实际 hover 后再读状态。非法输入例的错误回执和原 call ID 可追溯，拒绝调用不产生额外后端动作；修正接续是预定脚本决策，不冒充真实模型能力。

对象用例的原模型调用、下一次请求历史和工具回执全部保留实际输入形式与关联。最终 16 槽均排空、归档 complete、隔离审计通过，worker/scorer 网络尝试均 0。封存共 920 文件、四组相同的 93 个执行源码快照；只读 verify 复算一致。真实原生 schema 与不改下一请求原历史另由 SDK MockTransport 测试验证。[独立原件审计](independent-final-audit.json)另有191/191检查通过，逐槽模型请求数等于预定trace，无额外请求；空响应恢复提示分支本批未触发，仅由专项测试覆盖。

## 开发失败与完整分母

- local-01：准备 16 槽、启动 0。复审发现无完成标记的成功措辞可绕过未验证展示，以及恢复提示强迫完成标记，修复后撤回准备；[记录](local-01-preparation-withdrawal.json)及原快照保留。
- local-02：完整执行 16/16，169/171 检查通过。唯一案例缺陷为 unknown-raw：真实原工具返回超时文本，判定器只接受结构化异常，导致状态已满足却未验证。原 [失败结果](local-02/validation.json)与封存保留，只读复算仍为失败。
- 修复只窄范围支持当前原工具的已知 takeoff 超时整串；参数错误、任意文本、错误主体和未知 hover 仍拒绝。同时消除空响应恢复正文中“只有完成才能结束”的残留提示冲突；这两批间实际改变 hover_completion.py 和 uav_agent.py 两个生产文件，执行路径未扩张。增加 14 个正反例后，重新冻结 local-03 的同 16 槽并全部通过。

因此本轮共执行 **32 个本地回合及 32 个独立评分**，两批各自保留；不能只留下最终正结果，也不能将两批合并成模型成功率。尚未识别的其他原工具异常格式仍保守返回未验证。

此前中间测试日志保留，不将重复执行数累加。

## 保留项、限制与后续边界

对照其 102 个冻结源码，97 个未变，仅 5 个预期接线文件改变；69 个隔离、归档、恢复、搜索及成本保护模块保持原字节。最终源码和文档指纹见 [维护记录](../../maintenance/hover-input-contract-20261103.json)。

不追改前批“checker＋独立 hover 动作”两臂均 4/5 的冻结评分。新状态目标标准只适用于显式版本化的新验证；不能用改标准声称旧版成功率提高。仍须另行冻结协议后做真实对照。复杂指令覆盖、持续时间观测、通用采集与论文机制增量仍是未完成边界。**本轮到本地验收为止，第五项继续暂缓。**

复核最终批（仅只读，无 provider）：

```bash
agent4drone/.venv/bin/python scripts/validate_hover_input_contract.py verify \
  --output docs/results/hover-input-contract-20261103/local-03
```

新建本地复现使用同一入口 `prepare --output 新目录` 后 `run --output 新目录`；禁止覆盖或自动续跑旧目录。配置和完整用法见 [实验说明](../../../experiments/hover-input-contract-v1/README.md)。
