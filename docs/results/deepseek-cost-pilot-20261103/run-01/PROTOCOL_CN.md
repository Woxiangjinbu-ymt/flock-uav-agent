# compact/v9 真实 DeepSeek 小规模同期对照协议

日期：2026-11-03。用户已明确授权本轮真实 API 小批，使用 `compact_tools_v1 + search_confirm_v9` 验证成功率、失败恢复与实际 token 开销；暂不进入第五项。固定 **5条件 × 2臂 × 1次，共10槽**，不追加、不重试失败槽、不根据结果补题。首次派发前冻结协议、源码、输入、顺序与预算。本地 smoke 不属于真实模型样本。

## 问题、版本与解释范围

| 臂 | 输入布局 | 搜索版本 | 共同能力 |
| --- | --- | --- | --- |
| C / control，旧整合 | `full_v1` | `observed_target_v7` | `evidence_harness + evidence_r2 + task_execution_state_v1`、applicability、静态障碍合同、原工具和子程序 |
| N / candidate，新整合 | `compact_tools_v1` | `search_confirm_v9` | 与C相同；隔离、归档、恢复、预算与评分链相同 |

本轮直接回答成本收敛前后两个整合包的变化。**C不是原工具基线，也不是上游Agent4Drone原版**；本轮无第三个同期原工具臂。不得把前批原工具成绩与本批拼成同期比较。输入布局和搜索版本同时变更，因而是组合对照，不能拆出单机制因果贡献。原工具成功同样计成功，真实模型自行选择路径，不脚本提交计划。不叠加execution_memory_v7。

compact只删除system中与原生tools重复的工具描述目录，完整native schema、规则、历史回执、reasoning和恢复投影保留。v9保留v7探索/预算，仅在最近有效公开进度≥95%且新可信reach出现时及时核验，严格>95%才完成；不启用被淘汰的v8停滞早退。配置存在不算触发，另核验实际wire和frame中的search_confirmation记录。

## 冻结任务和顺序

| 条件 | task / scene | 顺序 | 目的 |
| --- | --- | --- | --- |
| short_chain | c74d7020 / fb8971ec | C→N | Drone 4起飞到12m并悬停，自主原工具/子程序选择 |
| navigation | 6c3f98ce / 5aabad33 | N→C | Drone 3起飞到14m、访问坐标并悬停，原工具多步执行 |
| search | fdd21809 / be5d648b | C→N | 原困难搜索，检查预算下成本/部分进度/整题成功 |
| unknown_takeoff | c74d7020 / fb8971ec | N→C | 起飞实际成功后丢一次回执，后续新模型决策与剩余悬停 |
| search_complete | b1ad4f3e / 35ce64d8 | C→N | 已知本地可完成搜索，检验自然选择以及v9确认是否触发 |

4个任务、4个场景、5个条件；故障条件复用短链，不能当作第五道独立题。前三任务来自 `experiments/task-coverage-v1-study`，manifest SHA256=`6386876e9e6172655abb899c836b4199909e438846b83be515a5f75aadb48bae`。额外b1来自 `experiments/minimal-harness-full100-v1`，manifest SHA256=`8156cc40a1920b418ac9091772870beececc47910ea00f16ff0c6d8cfb706180`；它在上一轮本地开发已暴露，不是未见测试。该题预先加入是为了提供完成确认的潜在机会，不能将其成功推广为整体搜索改善。

全部保留原指令、fixture、snapshot、timeline、场景和原checker；每槽新worker、退出后独立scorer；每对初态一致。旧manifest中的历史授权仅作来源，未恢复旧队列。原checker与附加公开动作义务核验并列；短链额外关联正确主体起飞、高度和后续hover，导航额外关联位置访问和hover顺序，搜索核验严格>95%。不把正常返回、模型声明或技能completed当作整题成功。

## 故障与恢复

仅unknown_takeoff对固定主体Drone 4（3d229034）的第一次实际成功 `POST /drones/3d229034/command/take_off` 丢失回执，策略看到中性文本 `Request timed out before response was received`。原工具和子程序共用同一公开API层探针；未成功不触发；每槽最多一次，不注入正确动作、私有状态或“已成功”提示。

保留旧契约：错误/unknown反馈→后续实际模型请求→新的模型决策→实际动作与剩余义务完成，才建立恢复机会及成功证据。相同回复预选的并行动作不算见错后的恢复；子程序内部查询单列。无机会的恢复率为null；不为补机会重跑。分别记录重复尝试和实际副作用次数；历史unknown不因后续状态查询改写。ready/paused程序恢复机会与resume单列，不把raw接续写成resume收益。

## 身份、预算与停线

模型/价格已按[官方价格](https://api-docs.deepseek.com/quick_start/pricing/)及[thinking文档](https://api-docs.deepseek.com/guides/thinking_mode/)复核，provider-review.json另存核对信息。滚动别名与fingerprint不等于固定权重。

| 项目 | 固定值 |
| --- | --- |
| 模型 / endpoint | `deepseek-flash` / `https://api.deepseek.com`；文档版本deepseek-flash |
| 参数 | thinking enabled、reasoning_effort high；同步非流式chat completion；SDK重试0；无付费search/fetch |
| 每槽 | 12 HTTP attempts；累计200,000 usage token完整响应后拒绝后续请求；每响应8,192输出token |
| 时间 | 500s任务，60s请求，5s排空，515s worker watchdog |
| 请求正文 | 200,000 bytes；不是token估计 |
| 执行 | 600 iterations、recursion 1800；每计划2,000公开API、16 repair、16搜索轮 |
| 新授权上界 | 10回合、最多120 HTTP；整批保守USD2.00，每槽USD0.50；不继承旧额度 |
| — | — |

请求派发前durable reservation，真实usage返回后结算。不重试当前槽。200,000为响应后阈值，最终完整响应可能超过；

## 预定分析与结论门槛

- 全10槽及完整条件配对：原checker、附加义务、正常/故障分组、部分进度与失败原因；不删失败、不拼历史最好值。
未知usage不填0。
- 两臂实际wire核验layout、完整native schema和共同权限，run_harness版本描述差异单列；无私有checker、canary或探针身份泄漏。原始reasoning保留于归档但不输出到报告。
- 自主start/resume/cancel、实际程序机会、unknown恢复机会/动作、重复副作用，以及v9确认触发。没有触发则不宣称验证了v9自然收益。
- 比较新旧每条件与总token差值，同时报告成功率、部分进度、动作与错误；成功下降不能仅凭token减少称整体改善。运行失败导致的低token与完成后节省分开。

每臂每条件仅一次，结果仅为已知开发场景的描述性同期pilot；不计算显著性、不证明稳定泛化或论文效果门槛。只读分析保存到run之外，所有原始证据封存。无Teacher生产、学生训练或第五项工作。
