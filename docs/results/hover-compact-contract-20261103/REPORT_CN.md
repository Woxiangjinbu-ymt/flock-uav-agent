# 新增输入说明压缩与只读并行判定：本地交付

2026-11-03。**两处修复已完成本地验收；没有新增 DeepSeek 调用，也未进入第五项。** 新候选为 `general-agent-v5-local`：继续使用 `compact_tools_v1 + search_confirm_v9`，显式选择 `json_object_or_string_v2 + hover_completion_v2`。应用默认和已封存 v4 契约保持。[冻结协议](PROTOCOL_CN.md)、[本地配置](../../../experiments/hover-compact-contract-v2/profile.json)。

## Confirmed Decisions — 本轮改动

输入兼容没有回退。v2 仍在真实 SDK 原生 schema 中声明 object|string，并复用 v1 严格 validator；对象只在旧 handler 边界序列化，原始模型参数、调用 ID 和历史不改。删去29个参数工具各自重复的长说明，将固定输入导语简化为 `Parameters:`，保留参数名、required、取值说明和工具专有语义；共有规则只在 system 中出现一次。未使用会误改工具返回值说明的全局替换。数组/标量、非法 JSON、非有限数等原有校验保留；重复键校验适用于仍为字符串的 JSON，不声称能找回上游解析对象时已经丢失的重复键。

完成判定只放宽起飞前位置锚点的无害并行：`list_drones / get_drone_status / get_weather` 的相连重叠事件须已结束、区间与双向 overlap 记录一致、回执完整有效、同一无人机位置一致，且全部在起飞前结束。未知工具、动作、错误回执、缺失/伪造顺序不豁免。串行锚点不增加无关回执要求；最终状态新鲜性、明确 hover 动作和无法证明持续时长的边界不变。判定本身不产生查询、重试或动作。

旧 v1 分支完整保留；schema_version=4 的 profile 不接受新 selector，v5 只能显式用于本地 mock 链。线上 pilot 的旧 scope 未扩展为 v2。没有切换 API/GUI 应用默认。

## Verified Results — 已验证结果

### 输入成本的本地字节证据

| 指标 | v4 / 输入 v1 | v5 / 输入 v2 | 变化 |
| --- | ---: | ---: | ---: |
| 31个原生工具定义，含29个参数工具 | 37,431 B | 23,078 B | −38.35% |
| 共享输入规则 | 416 B | 314 B | −24.52% |
| 固定历史26个N请求的规范字节总量 | 1,363,172 B | 987,342 B | −27.57% |
| 新本地只读并行同脚本，两版各4请求的实际归档字节总量 | 203,011 B | 145,188 B | −28.48% |

旧 string-only 工具定义为22,044 B；本次消除了 v4 相对它新增字节的93.28%，仍保留对象 union 的必要开销。26请求反事实固定全部历史与模型参数，逐项验证只改变白名单元数据/共享规则；27个C请求保持参考原件。输入历史、模型工具调用、工具回执与 ID 不变，无模型、tokenizer 或动作执行。[反事实审计](counterfactual-01.json)。

** 上一轮真实 v4 token 增加25.51%的原结论保留；新版自然模型是否减少 token、是否仍选择正确参数，需要后续单独授权的真实对照。

### 判定与完整链

一次冻结并执行20/20槽，20个新 worker、20个独立 scorer，全部40个PID不同；**224/224预定证据检查通过**，完整归档和网络阻断通过，1,152个文件封存。无失败批/补跑；单元测试中的临时准备目录不计作已执行回合。[完整验证](local-01/validation.json)、[封存清单](local-01/campaign-seal.json)。

| 条件 | 实际结果 |
| --- | --- |
| 相同脚本：列表与天气真正并行，然后起飞和新状态查询 | v5确认；v4仍未确认。真实ledger存在相互overlap，非伪造顺序 |
| 列表与起飞动作重叠；列表与未知起飞回执重叠 | 两例仍拒绝完成声明，不回退到较旧的好锚点 |
| string/object、原工具和子程序输入 | 按预定兼容；非法输入保留拒绝/纠正链，无静默补字段 |
| 原工具unknown、子程序unknown后的公开查询 | 两例均确认；各只执行一次起飞，未知历史保留、无重放 |
| 缺状态、错主体/高度、落地后陈旧状态、缺明确hover动作 | 不确认；正常返回与完成声明分列 |
| 完整明确hover动作；持续5秒要求 | 前者确认；后者仍无法确证，不把参数回显当实际持续时间 |

封存后独立复审发现，原并行验收helper没有单独强制两条ledger事件分别对应两个不同call ID，也未直接绑定API端点。未改冻结源或补跑；新增[独立关联审计](parallel-independent-audit.json)对全部4个并行槽、8条调用链严格校验barrier→原始HTTP→唯一ledger→唯一公开API的方法、端点、参数与commit及实际重叠，全部通过，确认本批不存在该关联缺口。新增31项独立审计测试含重复ID/丢关联/错端点等负例，通过后与主回归合计1,542项不重复测试；[补充测试日志](parallel-audit-tests.log)。今后复核本批同时运行此独立审计。

完成标签分布为9确认、10未确认、1旧契约未启用；负例预定拒绝也是验收通过，不能把9/20称为自然任务成功率。原 checker 作为辅助保留，尤其不替代动作/时长要求。并行测试在本地脚本层使用一次性 barrier 产生真实重叠，未修改生产 API/Agent 或被测 ledger；不是自然模型选择并行的证据。

对旧真实8个公开ledger只读复核：当前v1的完整assessment与冻结v1相同；v2仅将 `state_hover-candidate` 从未确认改为确认，其余7例结论不变。这是开发证据上的修复诊断，旧报告的公开认证N=2/3、独立真值3/3及token记录均未重写。

### 回归与保护

[测试日志](regression-01.log)。专项测试计数包含在此总数中，不另相加。

与上一轮冻结的107个运行源码相比，仅修改输入契约、完成契约、UAV接线、profile和本地脚本driver这5个文件；旧真实两批347/403个封存文件均完整不变。[源码与历史保护](source-preservation.json)、[最终核验](final-verification.json)。

## Working Hypotheses / Open Questions / Next Actions

当前选择是保留 v5 为显式本地候选；下一轮若授权真实验证，应固定这两个新 selector，在同模型、任务、初态、预算、顺序及统一评分下记录实际 usage，分别看状态目标、明确动作、时长诊断和 unknown 恢复。

本轮真实provider=0，Teacher样本=0，SFT/RL=0。第五项继续暂缓。

## 复现

从仓库根目录运行；新执行必须使用尚不存在的输出目录，不能覆盖 `local-01`：

```sh
agent4drone/.venv/bin/python scripts/validate_hover_compact_contract.py verify --output docs/results/hover-compact-contract-20261103/local-01
agent4drone/.venv/bin/python scripts/audit_hover_compact_contract.py --run docs/results/deepseek-hover-pilot-20261103/run-01 --output /tmp/hover-compact-counterfactual-new.json
agent4drone/.venv/bin/python scripts/audit_hover_compact_parallel.py --campaign docs/results/hover-compact-contract-20261103/local-01 --output /tmp/hover-compact-parallel-new.json
```

完整本地链的 `prepare/run/verify` 用法见[脚本导航](../../../scripts/README.md)。本地 SDK 的合成 usage 不用于 token 结论，独立 scorer 的私有状态不进入策略输入。
