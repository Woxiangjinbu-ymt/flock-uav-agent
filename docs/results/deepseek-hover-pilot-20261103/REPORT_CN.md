# 修复后 v4：DeepSeek 悬停、输入类型与恢复同期对照

2026-11-03。**8/8真实模型回合（本地仿真）、8个独立评分已完成。新版验证了对象输入兼容和更诚实的时长表达，但未显示任务成功率提升，实际token增加，并暴露一次过保守完成拒绝。** 本轮结束，不补跑、不切应用默认，不进入第五项。

## Confirmed Decisions — 对照与统一标准

按用户授权和[事前冻结协议](PROTOCOL_CN.md)，比较修复前 v3 选择器 C=`off/string_v1` 与修复后 v4 N=`hover_completion_v1/json_object_or_string_v1`；两臂均为 `compact_tools_v1 + search_confirm_v9 + evidence_harness/evidence_r2 + task_execution_state_v1`。因此本批比较的是完成/输入修复包，不是原工具 vs Harness，也不是 full/v7 vs compact/v9，更不能拆为两项机制各自的因果收益。

一个已知开发场景 c74/fb8971ec，4条件×2臂、各一次，顺序 CN/NC/CN/NC。状态目标、明确动作、回执丢失为3对支持任务；5秒持续悬停为单独诊断。派生指令只改封存公开command及来源，原初态/场景/task/checker保留，未使用新test或生成Teacher。

完成标准对两臂相同：独立scorer核验实际起飞效果、终态hovering/高度12/原XY/零速度及无后续旅行；明确动作额外要求真实hover及顺序。两臂另用同一冻结纯函数离线评估公开ledger；candidate在线gate再独立记录。运行正常返回、任务真值、公开完成证据和原高度/离地checker四者不混用。时长缺乏持续观测，统一为null/不可确证，不计入支持任务成功率。

模型使用官方 deepseek-flash、thinking enabled/high，SDK retries=0；53个实际响应均返回同一model及fingerprint `bc19ba97cbe016479903222bb1578db6`。这只记录本批身份，不能保证未来滚动别名权重不变。[官方定价](https://api-docs.deepseek.com/quick_start/pricing/)和[参数文档](https://api-docs.deepseek.com/guides/thinking_mode/)于本轮重新核对，见[预检](provider-preflight.json)。每槽500秒、12HTTP、200k usage token响应后停线、8192输出；

## Verified Results — 核心结果

| 指标 | 对照 C | 修复版 N | 解读 |
| --- | ---: | ---: | --- |
| 支持任务独立真值完成 | 3/3 | 3/3 | 本批未提高实际任务完成率 |
| 支持任务共同公开完成认证 | 3/3 | 2/3 | N有1次只读并行引起的过保守拒绝，见下 |
| input_json参数调用 | 22：21 string＋1 object | 17：全部object | 按真实响应位置计数，不重复数后续历史 |
| 实际表示类型错误 | 1 | 0 | C的object被旧string schema拒绝；N对象兼容已真实走通 |
| 实际工具字段语义错误 | 1 | 0 | C一次run_harness多余字段；不因小样本声称所有语义错误消失 |
| 受控回执丢失后的恢复 | 1/1机会成功 | 1/1机会成功 | 均有见错后新模型状态查询、保留unknown、无重复起飞，无增量成功率证据 |
| 支持任务实际动作 | 6 | 5 | N故障例少一次冗余hover；正常状态例仍均有额外hover |
| 全部HTTP | 27 | 26 | 请求少1次仍未抵消输入增加 |
| 全部仿真API/动作 | 52 / 8 | 37 / 7 | 包含子程序内部公开读取，不等于模型调用数 |
| 全部实际token | 257,099 | 322,691 | **+25.51%** |
| 仅支持任务实际token | 191,285 | 235,097 | **+22.90%**；也是两臂同样成功子集 |
| 时长诊断实际token | 65,814 | 87,594 | +33.09%，单列，不作为执行效率成绩 |
| 全部prompt / completion token | 253,617 / 3,482 | 319,873 / 2,818 | 增量主要出现在输入；输出减少未抵消 |
| 保守USD结算 | 0.0802635 | 0.0993435 | — |

reserved/locked=0，无未知usage、预算截断、未运行或追加调用。两臂均4个正常返回，8个归档valid且complete。逐请求、配对、缓存字段、原call ID与三层判定见[独立分析](analysis-01/analysis.json)及其[可读报告](analysis-01/REPORT_CN.md)。

全批cache hit输入C192,384/N252,416，miss输入C61,233/N67,457，53响应均报告缓存字段。worker任务窗口合计C44.58s/N37.28s；单次开发批的时延不是稳定吞吐结论。

| 条件 | C实际token | N实际token | 差异 | 动作C→N |
| --- | ---: | ---: | ---: | ---: |
| 无时长悬停 | 54,793 | 73,991 | +35.04% | 2 → 2 |
| 明确执行 hover | 79,232 | 87,705 | +10.69% | 2 → 2 |
| 起飞回执丢失 | 57,260 | 73,401 | +28.19% | 2 → 1 |
| 持续5秒诊断 | 65,814 | 87,594 | +33.09% | 2 → 2 |

## 悬停行为、参数与恢复意味着什么

**无时长悬停并没有普遍减少hover。** 正常状态目标两臂仍各执行takeoff＋hover；明确动作题两臂都按要求执行takeoff→hover。故障题中C采用子程序、后续又hover并查询，N采用原工具，见到未知效果后直接查询状态并完成确认，未额外hover。两例未知历史均保留，实际起飞各1次，任何来源的后续重复起飞尝试均0。程序内部读状态或同一旧回复预选动作没有被冒充见错后的新模型恢复。

**对象输入兼容获得真实接口证据，但样本仍小。** N17次原生object在原始HTTP、模型消息和下一请求历史中保持关联，没有类型/字段拒绝；C在unknown槽出现1次object→string schema拒绝，在正常state槽有1次run_harness多余字段拒绝。错误回执、被拒调用和后端效果分别审计；两种错误原件保留，不能把传输unknown算成参数错误，也不能把零次观察误写成全面消除错误。

**5秒题的新回答更准确。** 两臂都提交了带duration=5的hover，但当前仿真只是参数/事件记录，不能证明持续保持5秒。C原始最终回答宣称已经悬停5秒并带完成标记；N原始回答明确时长“reported but unverified”，没有完成标记，在线gate也显示未验证。此处是证据与表述的改进，不是新增任务成功；也不等于证明物理上没有保持5秒。

## 新暴露的完成门限问题

N的state_hover独立真值为True，但共同公开判定为unverified，原因 `fresh_pre_takeoff_position_anchor_required`。原始位置并未缺失：list_drones的e1在序号1–3返回正确主体XY=(660,734)，只与get_weather的e2（2–4）重叠；两次只读查询都在起飞e7（7–8）前结束，没有动作交叉。

ledger对任意并行标记uncertain，位置锚点又要求全局certain且无overlap，因此把这个可核验的只读并行情形保守拒绝。**这是完成证据门限过严，不是无人机未完成任务。** 本轮保留N公开认证2/3，未用事后规则重评分，未在运行中修改实现。事实与只读诊断见[完成审计](completion-independent-audit.json)。下一次有界修正应区分只读并行与可能改变同主体状态的动作交叉，仍保留真正时序未知时的拒绝。

## 为什么请求更少却token更多

[首请求诊断](input-overhead-diagnostic.json)比较同一state指令、同初态且尚无模型输出历史的实际请求：原生工具JSON字节 **22,044→37,431**，system字节 **9,994→10,693**，供应商报告的首prompt token **7,883→11,227（+42.42%）**。两臂均31工具/29个input_json参数工具。

compact目录仍然保留；新增兼容层在每个参数工具的description和input_json属性说明中重复携带表示规则，连同union schema与完成规则增加了每次请求负担。这能解释输入膨胀的直接来源，但不能据字节量分摊全部token或证明每个后续决策的因果贡献。本轮的工具次数与路径也不同。正确结论是：**类型兼容修复有效，本批输入表示仍不够经济；不能把v4称为更省token的版本。**

## 验证、保留项与后续边界

最终1,317项不重复pytest通过，3个既有依赖弃用warning，见[日志](final-regression-01.log)。先完成[8槽真实SDK本地烟测](local-01/summary.json)及[独立分析](local-analysis-01/analysis.json)，再冻结同一107源码/12输入/8槽/协议的真实批次。烟测合成usage不计真实token，也不算Teacher。开发期协调器测试首次因分析测试文件尚未落盘发生setup错误，落盘后37项及最终全套通过；没有因此启动真实失败批或隐藏模型回合。

新增评分与分析均在回合外。上一真实批403封存文件和旧4/5口径未改。

当前保留修复版作为受控研究候选，**不回退已验证的对象兼容、隔离、归档或恢复修复，也不切换应用默认**。下一步建议只收敛两处：压缩新增native schema/描述中的重复规则；修正只读并行导致的位置锚点过严拒绝。两者需新版本本地正反例后再决定真实验证。本轮没有擅自实施后续修正或追加API。

本批一场景、每条件各一次，不能宣称稳定成功率、恢复增益、统计显著性或泛化，也不能独立归因完成规则和输入契约；未重新检验搜索收益。真实批已结束，**第五项、正式Teacher采集、SFT与RL均未启动**。


```bash
agent4drone/.venv/bin/python scripts/deepseek_hover_pilot.py verify \
  --run docs/results/deepseek-hover-pilot-20261103/run-01
```
