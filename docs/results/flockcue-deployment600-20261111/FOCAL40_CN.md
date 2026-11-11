# 两个原场景的三次 DeepSeek 部署

| 同序原题 | 十月 Agent4Drone | 十一月 FlockCue 教师试采 | 十一月 FlockCue 正式首跑 |
| --- | ---: | ---: | ---: |
| 巡逻 04cd6269 | 19/20 | 16/20 | 20/20 |
| 搜索 ef36e27a | 18/20 | 10/20 | 20/20 |
| 合计 | 37/40 | 26/40 | **40/40** |

沿用十月的部署协议后，FlockCue 在试采丢失的 14 题上全部通过。其中 12 题十月曾通过，另 2 题十月也曾失败。十月与试采均通过的 25 题和仅试采通过的 1 题，本次也全部通过。两个场景的封条已校验，正式首跑 40 个作业均已结束，按冻结时的独立检查回包重放原树，186/186 个叶检查通过。十月三次失败均为原检查未过，三批都以整题检查为分数。两场在 600 题内，均有开发暴露。[逐题三方表](focal40.csv) · [十月与试采配对](../../audit/harness-paired40-20261111/REPORT_CN.md) · [正式批次登记](registration.json)

## 试采阈值会截到正式轨迹的哪一段

| 按十月、试采结果分组 | 正式有第 13 次尝试 | 尝试前已返回 ≥20 万 token | 任一派发条件会截短 | 存在请求体 >20 万字节 | 存在有回执的输出 >8,192 token | 存在 HTTP 尝试 >60 秒 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 十月过、试采失（12 题） | 8 | 9 | **10** | 6 | 0 | 1 |
| 两次均失（2 题） | 1 | 2 | **2** | 2 | 0 | 0 |
| 两次均过（25 题） | 2 | 5 | **5** | 1 | 0 | 0 |
| 十月失、试采过（1 题） | 0 | 0 | 0 | 0 | 0 | 0 |
| 全部 40 题 | 11 | 16 | **17** | 9 | 0 | 1 |

沿实际正式请求，只在发下一次之前计入已经返回的 usage。按 12 次或累计 20 万 token 两条派发条件，17 题共 147 次后续尝试无法按原样发出，其中十月过、试采失的 12 题占 117 次。9 题共有 110 次请求体超过 20 万字节，各项有重叠。巡逻第 10、16 题在试采均触及 token 限制，正式却各用了 8 次请求就通过，分别为 127,680、172,349 token。两次部署所走的路径有差异。[逐题前缀及阈值](focal40.csv) · [试采派发规则](../../specs/04-deepseek-pilot40/plan.md)

## 能从动作和回执确认的差异

- **巡逻第 3 题，试采 4/5，正式 5/5。** 两次都用多航点移动工具。正式分段绕行，在第 11 回合收到到达最后航点 (285,605,17) 的回执，第 12 回合查询位置，第 13 回合只提交完成说明。即使第 13 次请求受限，五个航点在此前都已经有到访记录。这个例子说明，统计被省去的请求数不能直接推导整题失分。[正式动作和到位回执](../../../outputs/flockcue-deployment600-20261111/experiments/runs/20261111-134339-flockcue-600-first-pass-d505975c/04cd6269/agent-traces/toolchains/20261111T134511471638_04cd6269_120af8ac-e670-44b2-97da-c2ceb89ba19f_completed.json) · [正式评分](../../../outputs/flockcue-deployment600-20261111/experiments/runs/20261111-134339-flockcue-600-first-pass-d505975c/04cd6269/a377d715.checks.json)
- **搜索第 10 题，试采 0/4，正式 4/4。** 试采在双机导航中拒发 201,918 字节的请求，未发出拍照动作。正式路径里已有两次悬停和两次拍照，第二次拍照在第 16 回合。正式第 9 次派发前累计已用 20 万 token，第 10 次起请求体首次越过字节线（214,152 字节），最大 318,107 字节。[正式动作和回执](../../../outputs/flockcue-deployment600-20261111/experiments/runs/20261111-134339-flockcue-600-first-pass-d505975c/ef36e27a/agent-traces/toolchains/20261111T142506366279_ef36e27a_31b9cf8a-b279-441f-812e-ff9d46405263_completed.json) · [正式评分](../../../outputs/flockcue-deployment600-20261111/experiments/runs/20261111-134339-flockcue-600-first-pass-d505975c/ef36e27a/c7a8edb7.checks.json)
- **搜索第 14 题，试采 7/16，正式 16/16。** 试采的第 7 次候选请求为 220,465 字节，本地拒发。正式第 7 次请求为 202,793 字节并已派发。第 11、12、15 回合各下发四次定向移动，终态四项到达及十二项方向距离检查均通过。正式最大请求 385,735 字节。两次均使用显式导航或多航点工具。[正式动作和回执](../../../outputs/flockcue-deployment600-20261111/experiments/runs/20261111-134339-flockcue-600-first-pass-d505975c/ef36e27a/agent-traces/toolchains/20261111T143144960483_ef36e27a_eb60c9b6-78ec-49cc-b04a-1d6e7af98490_completed.json) · [正式评分](../../../outputs/flockcue-deployment600-20261111/experiments/runs/20261111-134339-flockcue-600-first-pass-d505975c/ef36e27a/50dcb2b9.checks.json)
- **搜索第 19 题，试采 2/4，正式 4/4。** 两次都生成两份覆盖路径。试采第 7 回合的 8,192 个输出 token 全用于思考，未生成动作。正式第 7 个逻辑回合第一次 HTTP 在 285.56 秒后遭遇 `RemoteProtocolError`，同一请求经 SDK 自动重试，27.36 秒后收到工具调用。该次成功响应的输出为 5,285 token，含 4,849 个思考 token。正式随后用显式航点补飞，两项到达与两项覆盖检查均通过。失败尝试缺少完整响应和 usage。40 道题中其余 39 份模型归档完整，此题的轨迹需标记缺口。[正式模型归档](../../../outputs/flockcue-deployment600-20261111/experiments/runs/20261111-134339-flockcue-600-first-pass-d505975c/ef36e27a/agent-traces/modelio/model-io-96cbcbf05d7f4508b681b54791d7bf04.jsonl) · [正式航迹工具](../../../outputs/flockcue-deployment600-20261111/experiments/runs/20261111-134339-flockcue-600-first-pass-d505975c/ef36e27a/agent-traces/toolchains/20261111T144158128204_ef36e27a_730eb387-bc6e-476d-9211-10455e6f997d_completed.json) · [正式评分](../../../outputs/flockcue-deployment600-20261111/experiments/runs/20261111-134339-flockcue-600-first-pass-d505975c/ef36e27a/3ca9a106.checks.json) · [试采第 7 次回复](../../../outputs/deepseek-teacher-pilot40-live-20261111/run/sessions/ef36e27a/tasks/19-3ca9a106/provider-wire/turn-07.response.json)

十月与试采均失的**搜索第 3 题**，十月漏了起点到访（3/4），试采 0/4 且未下发降落。正式分别在第 13 回合下发绕行起点的航线、第 20 回合导航至终点、第 21 回合降落，四项检查全部通过。**搜索第 13 题**，十月漏了 Drone 6 向东 92 米（19/20），试采 11/20。正式第 5、6 回合各向五机下发北移 97 米、东移 92 米，第 7–9 回合继续调整，20/20 通过。第 6 次正式请求已达 260,611 字节。[搜索第 3 题执行](../../../outputs/flockcue-deployment600-20261111/experiments/runs/20261111-134339-flockcue-600-first-pass-d505975c/ef36e27a/agent-traces/toolchains/20261111T141732540365_ef36e27a_4af8bc73-6a7b-4022-9d3b-7d4b0e26c579_completed.json) · [搜索第 13 题执行](../../../outputs/flockcue-deployment600-20261111/experiments/runs/20261111-134339-flockcue-600-first-pass-d505975c/ef36e27a/agent-traces/toolchains/20261111T142848981521_ef36e27a_395eef7a-c78b-4333-99e0-b50e36e89b9e_completed.json) · [十月原检查存档](../../../experiments/runs/20261003-143255-full-5c501118/ef36e27a/)

14 道试采失败题在两次 FlockCue 部署中均无已执行的预定义子程序。正式巡逻第 7 题有一次 `public_search` 调用，回执为不支持；最终花了 75 次 HTTP、785 万余 token，在 500 秒窗口内通过。两场正式首跑中只在巡逻第 1 题完成了一个 `simple_chain`。这些结果支持继续检查请求限制、直接工具路径和环境继承对表现的影响。独立衡量子程序或其他模块的贡献仍需要同状态消融。[巡逻第 7 题工具回执](../../../outputs/flockcue-deployment600-20261111/experiments/runs/20261111-134339-flockcue-600-first-pass-d505975c/04cd6269/agent-traces/toolchains/20261111T135427340108_04cd6269_22349c96-469d-4655-ab0f-76a586e42a57_step_limit.json) · [巡逻第 1 题工具回执](../../../outputs/flockcue-deployment600-20261111/experiments/runs/20261111-134339-flockcue-600-first-pass-d505975c/04cd6269/agent-traces/toolchains/20261111T134358802725_04cd6269_b3065317-0980-461f-8300-2d2bb62cba71_completed.json) · [试采逐工具审计](../../results/deepseek-teacher-pilot40-live-20261111/TOOL_RECEIPT_METHOD_CN.md)

## 核算和归因边界

按场景、题号、作业及命令绑定原件，校验正式 490 份实际请求体的字节长度与 SHA-256。489 份完整的 HTTP 200 响应都有 usage，全部服务指纹与同日试采相同，最大已知单次输出 8,172 token、最大成功 HTTP 耗时 44.34 秒。CSV 中 `formal_total_observed_tokens` 只计已返回的 usage。每个正式请求均带 `temperature=0.1`，未显式设置思考强度或输出上限。试采显式启用高强度思考、禁用 SDK 自动重试。十月这两场缺少逐次响应指纹。[正式设置](registration.json) · [试采设置和服务记录](../../results/deepseek-teacher-pilot40-live-20261111/summary.json) · [十月预检](../../../experiments/runs/20261003-143255-full-5c501118/gateway_preflight.json)

`first_*` 为从 1 开始的正式 HTTP 尝试序号，CSV 的归档路径相对于登记文件中的 `attempt` 目录。空值表示没有观察到越线。字节为实际请求体的 UTF-8 长度。`suffix_omitted_by_either` 仅计从首次达到派发次数或累计 token 条件起的全部后续请求。HTTP 时长只描述观测耗时，具体超时行为还受传输阶段影响。两场 40 题沿场继承世界，试采与正式题初同机 ID 的全队 XY 仅 11/40 一致。每题在正式终态运行了一次独立检查，未在离线截断点另行评分，因此不能把 17 道轨迹截短直接计成 17 道失败。[逐题三方表及原件索引](focal40.csv) · [首跑核分实现](../../../scripts/report_flockcue_deployment600.py)
