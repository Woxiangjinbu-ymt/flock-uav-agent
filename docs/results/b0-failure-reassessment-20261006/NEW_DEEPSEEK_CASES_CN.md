# DeepSeek B0：补齐13条首次执行漏诊材料

范围只限 cohort-summary 中原旧机制诊断未覆盖的13条 DeepSeek 首次执行失败。全部 generation=1、worker已确认、没有终止服务错误；未采用恢复重跑轨迹，也不把这些13条当作203条失败的全量人工确认。

逐题检查原始 job、checks、agent-state、barrier/window 和同次 trace；trace用命令完全一致且开始时间相差不足5秒关联，完整输入哈希保存在 new-deepseek-cases.json。未运行模型或仿真。

就这13条，观察归组为：5条发现/搜索未闭合、5条指定坐标遭拒绝后替代并宣告完成、2条定向里程被净位移替代、1条把已发现集合当全集。这些是本补充集的描述，不是总体机制发生率。

四条超时轨迹的 tool_chain 为空，不能说Agent没有行动；api_calls仍保留真实动作。其输出的“cancelled by user”由 timeout.reason=Agent task budget exhausted 对照，属于预算取消，不是本轮用户停止实验或服务402。

必须把动作失败与报告状态分开：`[TASK DONE]` 在原 runtime 中会触发 success，但有些终答同时明确承认目标未找到或坐标被替代。仅修正这个状态标记，不会改变已经失分的外部任务；也不能据此证明强制继续执行一定能补齐目标。以下“完成宣告/自判成功”只按具体终答与运行标志分别解释，不一概等同模型隐瞒未完成。

## 值得区分的三件事

- 原目标坐标被服务器明确拒绝，提示任务与环境约束冲突；这不能一概归为Planner找路失败。Agent另一个确定问题是明知改点仍宣告原任务完成。
- 发现了部分目标，不等于发现全部；成功执行斜向绕行，也不等于满足指定航向里程。两者都出现了错误的完成替代。
- 搜索超时只能证明未完成；必须区分实际未观察到目标与已经观察到但指定机迟迟未推进，不能仅按工具错误数量定因。

## 1. 2a55417f｜Multiple Target Inspection 1

场景 `ec1f47f4`；原始检查 **0/4通过**；超时；Agent自判成功=None。

**指令：** Drone 12 在20米先访问 Fixed Target 9 并拍照，再访问 Fixed Target 26 并拍照。

**失分项：** target_is_reached_by_drone {"target_id": "b050bec0", "drone_id": "0daf23a7"}，检查值=0；target_in_photo_taken_by_drone {"target_id": "b050bec0", "drone_id": "0daf23a7"}，检查值=False；target_is_reached_by_drone {"target_id": "2f67f40c", "drone_id": "0daf23a7"}，检查值=0；target_in_photo_taken_by_drone {"target_id": "2f67f40c", "drone_id": "0daf23a7"}，检查值=False。

**首次可定位的偏离/断点：** API 26起飞后，API 27起开始探索多个候选区域；API 35搜索路线被挡，后续转为多机探索。这里能定位搜索扩展的执行序列，但不能把第一次碰障当作根因。预算耗尽时两个指定目标仍未被指定机访问，也没有拍照调用。

**确定事实：** 4个检查全部失败。216次API记录中没有返回目标9的ID；任务结束黑板已有目标26，但本题没有完成其访问/照片。83条命令涉及15架无人机，任务最终被500秒预算取消。

**解释及置信边界：** 搜索覆盖与任务推进调度未闭合；没有在现有资料中证明某次单独路径选择是决定性错误。目标9在原场景真实存在，不是目标数据缺失。 异常轨迹的tool_chain为空，不能恢复全部Planner理由；无法确认若先完成另一目标是否仍能整题通过。

**结论强度：** 搜索未完成和超时确定；具体首个错误决策未知。

定位：trace api_calls.index=26,27,35,37。
[首次轨迹](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/agent-traces/toolchains/20261003T194735144272_ec1f47f4_f4c75b4b-d65d-4d2f-8bb0-ef03c5e5dab3_exception.json) · [原始检查](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/2a55417f.checks.json) · [任务/返回](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/2a55417f.job.json) · [worker与黑板](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/2a55417f.agent-state.json)

## 2. 7a1b5ec7｜Fleet Target Sweep (5 Drones) 1

场景 `ec1f47f4`；原始检查 **19/20通过**；正常返回；Agent自判成功=True。

**指令：** 五机分别访问五个指定目标，再各自西118米、南84米、北67米。

**失分项：** target_is_reached_by_drone {"target_id": "6a8dd30c", "drone_id": "2805e749"}，检查值=0。

**首次可定位的偏离/断点：** 探索后未找到 Fixed Target 16，仍让对应Drone 8执行后续定向飞行；最终输出明确列出该目标“Not located”，同时给出[TASK DONE]。这是可定位的未满足子目标被跳过后仍完成宣告。

**确定事实：** 19/20检查通过，唯一失败为Drone 8未到达目标16。原目标16存在于(32,40)，本题API响应与任务后黑板均没有该目标ID。其余四个目标与15段定向检查通过。

**解释及置信边界：** 缺少未解决目标的继续发现或失败收束，导致部分任务被当作整体完成；搜索为何遗漏该区域尚未独立证实。 不能以404证明目标不存在；目标查询API接收名称而非已解析ID的行为也不能单独解释整题失败。

**结论强度：** 跳过必需目标后自判成功确定；搜索遗漏机制为候选。

定位：trace api_calls.index=30,31,187,191。
[首次轨迹](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/agent-traces/toolchains/20261003T200052925814_ec1f47f4_fac4bcf5-ee65-433b-a611-0c85675a77a0_completed.json) · [原始检查](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/7a1b5ec7.checks.json) · [任务/返回](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/7a1b5ec7.job.json) · [worker与黑板](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/7a1b5ec7.agent-state.json)

## 3. ececc780｜Fleet Target Sweep (5 Drones) 3

场景 `ec1f47f4`；原始检查 **19/20通过**；超时；Agent自判成功=None。

**指令：** 五机分别访问目标3、9、27、16、1，再各自西87米、南81米、北113米。

**失分项：** target_is_reached_by_drone {"target_id": "6a8dd30c", "drone_id": "4213a072"}，检查值=0。

**首次可定位的偏离/断点：** API 35起开展多机搜索；API 232中Drone 19已执行北113米，尽管其指定目标16未在任何本题响应或最终黑板中出现。最终仍在追加探索路径(API 235、236)，随后预算耗尽。

**确定事实：** 19/20检查通过，唯一失败是Drone 19未到目标16。238次API、92条命令涉及19架无人机；目标16在原场景存在，但本题没有定位。

**解释及置信边界：** 对未知目标的搜索覆盖与多机任务编排效率不足；绝大多数子任务已完成，最终剩余发现缺口未闭合。 tool_chain为空，不能确定模型内部是否明确决定跳过目标或如何分配搜索预算。

**结论强度：** 一个目标未发现且预算结束确定；具体规划失误候选。

定位：trace api_calls.index=35,36,232,235,236。
[首次轨迹](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/agent-traces/toolchains/20261003T201155573378_ec1f47f4_ea07eb6d-0b4c-44ea-b774-5877c921712e_exception.json) · [原始检查](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/ececc780.checks.json) · [任务/返回](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/ececc780.job.json) · [worker与黑板](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/ececc780.agent-state.json)

## 4. e00435b3｜Fleet Target Sweep (5 Drones) 4

场景 `ec1f47f4`；原始检查 **19/20通过**；正常返回；Agent自判成功=True。

**指令：** 五机访问各目标后，分别完成西100米、南64米、北103米；受阻时也须完成指定航向距离。

**失分项：** drone_has_moved_directed_distance {"drone_id": "fe5a8e59", "min_distance": 100.0, "heading": 270, "tolerance": 5.0}，检查值=73.08。

**首次可定位的偏离/断点：** API 92的Drone 3正西100米被拒绝后，API 95改飞(601,547)→(555,565)→(501,562)。tool 65明确把这段称为“west completed via detour”，随后进入南向阶段，未补足正西航向距离。

**确定事实：** 仅西向100米检查失败，实际合格航向距离73.08米。工具把x净减少100米当成完成；首段斜飞并不在270°±5°内，后续累计仍不足。19/20检查通过，最终宣称全部完成。

**解释及置信边界：** 无须用API报错次数解释：明确可见的是补救计划把方向约束换成终点/净位移约束，且没有维护剩余定向里程。 没有独立验证哪条替代路线最优；本诊断不需要最优路线假设。

**结论强度：** 任务语义在绕行补救中改变，证据确定。

定位：trace api_calls.index=82,92,95,102,105；tool_chain.index=55,58,65,68。
[首次轨迹](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/agent-traces/toolchains/20261003T201959807879_ec1f47f4_39b13e0e-301b-48af-b832-ba7cfa81e732_completed.json) · [原始检查](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/e00435b3.checks.json) · [任务/返回](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/e00435b3.job.json) · [worker与黑板](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/e00435b3.agent-state.json)

## 5. 2a977256｜Dual Flight Marathon Navigation

场景 `ec1f47f4`；原始检查 **20/26通过**；正常返回；Agent自判成功=True。

**指令：** Drone 3和Drone 10在22米完成各自含起点、多个航点、终点的双马拉松并降落。

**失分项：** drone_has_visited_position {"drone_id": "fe5a8e59", "x": 100.0, "y": 85.0, "z": 22.0, "tolerance": 3.0}，检查值=0；drone_has_visited_position {"drone_id": "fe5a8e59", "x": 913.0, "y": 194.0, "z": 22.0, "tolerance": 3.0}，检查值=0；drone_has_visited_position {"drone_id": "fe5a8e59", "x": 189.0, "y": 666.0, "z": 22.0, "tolerance": 3.0}，检查值=0；drone_has_visited_position {"drone_id": "d748e123", "x": 918.0, "y": 80.0, "z": 22.0, "tolerance": 3.0}，检查值=0；drone_has_visited_position {"drone_id": "d748e123", "x": 930.0, "y": 180.0, "z": 22.0, "tolerance": 3.0}，检查值=0；drone_has_visited_position {"drone_id": "d748e123", "x": 190.0, "y": 714.0, "z": 22.0, "tolerance": 3.0}，检查值=0。

**首次可定位的偏离/断点：** API 12、13明确拒绝两个原始起点；随后tool 8、9把起点改成(67,100,22)、(872,80,22)。最终称所有航点访问完成，但外判6个指定位置均未访问。

**确定事实：** 20/26检查通过。两个起点收到目标位置与圆形障碍冲突的明确回执；失点还包括Drone 3的(913,194)、(189,666)，Drone 10的(930,180)、(190,714)。替代坐标不满足原3米容差。

**解释及置信边界：** 一部分任务坐标与环境冲突；Agent允许用近旁位置替代精确访问，并在最终表述中抹平剩余缺口。 只确认两个起点有原始拒绝证据；其余四个失点不能全部直接判为不可达，需另外做几何/路径证据审查。

**结论强度：** 两个起点遭拒绝及目标替代确定；全部六失点不可达未证明。

定位：trace api_calls.index=12,13,22,23,34；tool_chain.index=6,7,8,9。
[首次轨迹](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/agent-traces/toolchains/20261003T202738383426_ec1f47f4_d36db6d3-383e-4d29-b552-af69dd7c1f93_completed.json) · [原始检查](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/2a977256.checks.json) · [任务/返回](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/2a977256.job.json) · [worker与黑板](./experiments/runs/20261003-143255-full-5c501118/ec1f47f4/2a977256.agent-state.json)

## 6. 4ccc0dbc｜Triangle Formation - 3 Drones 1

场景 `35ce64d8`；原始检查 **2/3通过**；正常返回；Agent自判成功=True。

**指令：** 三机22米三角编队，Drone 3必须访问(494,433,22)，另外两机访问各自指定点并悬停。

**失分项：** drone_has_visited_position {"drone_id": "7954b39a", "x": 494.0, "y": 433.0, "z": 22.0, "tolerance": 3.0}，检查值=0。

**首次可定位的偏离/断点：** API 16拒绝Drone 3精确点：与Polygon Obstacle 8冲突；Agent改停(494,445,22)，相差12米，却在输出首尾称三角编队完成。

**确定事实：** 2/3检查通过；唯一失点是(494,433,22)，容差3米。原始回执直接证明给定终点受阻；最终状态与自述均承认12米替代。

**解释及置信边界：** 场景可行性与任务精确坐标冲突，以及完成语义允许“近似完成”覆盖原要求。 未重新运行几何可达性证明；本报告不将所有终点冲突自动归咎于Planner找路能力。

**结论强度：** 目标点拒绝、12米替代和错误完成宣告确定。

定位：trace api_calls.index=16,17,18；tool_chain.index=7,10,24。
[首次轨迹](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/agent-traces/toolchains/20261003T202234126014_35ce64d8_16b5fb12-90d5-4863-b8f0-1b5009c9b2d5_completed.json) · [原始检查](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/4ccc0dbc.checks.json) · [任务/返回](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/4ccc0dbc.job.json) · [worker与黑板](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/4ccc0dbc.agent-state.json)

## 7. 8aa71832｜Target Inspection 1

场景 `d23eb117`；原始检查 **0/2通过**；超时；Agent自判成功=None。

**指令：** Drone 9在18米访问 Fixed Target 7并拍照。

**失分项：** target_is_reached_by_drone {"target_id": "e28fd778", "drone_id": "faebcd42"}，检查值=0；target_in_photo_taken_by_drone {"target_id": "e28fd778", "drone_id": "faebcd42"}，检查值=False。

**首次可定位的偏离/断点：** API 24以名称查询目标返回不存在；API 25开始长路径扫描，API 27只完成6/11点。之后扩大为15机探索，直到预算取消仍没有目标7出现在响应或黑板中，也没有拍照。不能把首次404或首次局部路径受阻单独定为根因。

**确定事实：** 0/2检查通过，282次API、114条命令涉及15机。原场景目标7真实存在于(822,714)。所检查的省略z路径实际成功点仍为18米，因此本例不能误归为“缺z导致落地”。

**解释及置信边界：** 缺少可核验的探索覆盖/剩余未发现区域，使大量移动仍不能定位任务目标。 tool_chain为空；不能从动作多推断工具数限制，也不能证明一个指定替代搜索策略一定成功。

**结论强度：** 目标未发现且未拍照确定；搜索策略根因为候选。

定位：trace api_calls.index=24,25,27,29,31,36。
[首次轨迹](./experiments/runs/20261003-143255-full-5c501118/d23eb117/agent-traces/toolchains/20261003T201403612383_d23eb117_68f64ad4-9524-4d62-92f5-8d6f28cfe951_exception.json) · [原始检查](./experiments/runs/20261003-143255-full-5c501118/d23eb117/8aa71832.checks.json) · [任务/返回](./experiments/runs/20261003-143255-full-5c501118/d23eb117/8aa71832.job.json) · [worker与黑板](./experiments/runs/20261003-143255-full-5c501118/d23eb117/8aa71832.agent-state.json)

## 8. e42d12c5｜Target Assignment Task Completion 1

场景 `d23eb117`；原始检查 **0/1通过**；正常返回；Agent自判成功=True。

**指令：** 派遣必要无人机访问所有目标，使会话任务进度超过95%。

**失分项：** task_progress {"expected_progress": 0.95}，检查值=0.9。

**首次可定位的偏离/断点：** tool 4识别“已发现17个目标”后，最终把这17个当作完整分母，写出“17/17=100%>95%”；缺少证明地图目标已经全部发现的过程。

**确定事实：** 唯一进度检查返回0.90，任务失败。源场景有20目标，最终黑板仅17目标；未在黑板的是Fixed Target 1、7、13。不能把未观察到的三个直接等同三个未访问，因为访问与感知不同。

**解释及置信边界：** 局部Memory缺少覆盖完整性的表达，Planner把目前已知目标集合闭合成全局集合，造成过早完成。 不能仅凭17/20推导进度，应以原checker的0.90为准；未本轮重新模拟各目标的累计访问。

**结论强度：** 错误分母与过早完成有直接输出证据。

定位：trace api_calls.index=107,123；tool_chain.index=4,50,51,66。
[首次轨迹](./experiments/runs/20261003-143255-full-5c501118/d23eb117/agent-traces/toolchains/20261003T202110973365_d23eb117_b8d8cf06-f8e2-4cd1-a622-8f278cbc35d1_completed.json) · [原始检查](./experiments/runs/20261003-143255-full-5c501118/d23eb117/e42d12c5.checks.json) · [任务/返回](./experiments/runs/20261003-143255-full-5c501118/d23eb117/e42d12c5.job.json) · [worker与黑板](./experiments/runs/20261003-143255-full-5c501118/d23eb117/e42d12c5.agent-state.json)

## 9. d82e683c｜Triangle Formation - 3 Drones 1

场景 `dcae50c1`；原始检查 **1/3通过**；正常返回；Agent自判成功=True。

**指令：** 三机20米编队，指定点为(473,568)、(545,619)、(375,631)。

**失分项：** drone_has_visited_position {"drone_id": "223a1558", "x": 545.0, "y": 619.0, "z": 20.0, "tolerance": 3.0}，检查值=0；drone_has_visited_position {"drone_id": "ddfd1df9", "x": 375.0, "y": 631.0, "z": 20.0, "tolerance": 3.0}，检查值=0。

**首次可定位的偏离/断点：** API 17、18分别拒绝后两个目标点：多边形与椭圆障碍冲突。Agent改用(545,646)、(375,585)，相差27米、46米，仍以Formation established开头并附[TASK DONE]。

**确定事实：** 1/3检查通过，两处精确访问失败。输出说明了位置偏离和部分可实现，但返回成功标记与外部要求不一致。

**解释及置信边界：** 基准坐标与环境约束冲突；语义上把安全替代当作任务完成，不能仅靠更强找路解决全部精确坐标要求。 未运行几何/仿真反事实验证。

**结论强度：** 两次明确终点冲突和替代坐标确定。

定位：trace api_calls.index=17,18,29；tool_chain.index=8,9,10,11,19,20。
[首次轨迹](./experiments/runs/20261003-143255-full-5c501118/dcae50c1/agent-traces/toolchains/20261003T201821800713_dcae50c1_0019ae0d-930f-4e14-bd5b-5aba1f1c6ca4_completed.json) · [原始检查](./experiments/runs/20261003-143255-full-5c501118/dcae50c1/d82e683c.checks.json) · [任务/返回](./experiments/runs/20261003-143255-full-5c501118/dcae50c1/d82e683c.job.json) · [worker与黑板](./experiments/runs/20261003-143255-full-5c501118/dcae50c1/d82e683c.agent-state.json)

## 10. fb6876d2｜Fleet Target Sweep (5 Drones) 1

场景 `dcae50c1`；原始检查 **19/20通过**；正常返回；Agent自判成功=True。

**指令：** 五机访问指定目标后各完成西85米、南78米、北112米，障碍绕行也不能少方向里程。

**失分项：** drone_has_moved_directed_distance {"drone_id": "163e6966", "min_distance": 85.0, "heading": 270, "tolerance": 5.0}，检查值=0.0。

**首次可定位的偏离/断点：** API 226正西85米终点受阻；API 229由(458,669)斜飞到(373,692)，tool 82随后明确写“West leg complete (all net −85 m in X)”。x减少85并不等于沿270°航向移动85米。

**确定事实：** 19/20检查通过。Drone 12西向检查值为0.0；斜飞方向约285°，超出270°±5°。之后没有补执行合格西向距离，最终称全部完成。

**解释及置信边界：** 绕行时改变目标语义，错误用净坐标差作为方向动作的完成证据。 未验证最优补飞路径；根因定位无需假设具体最优路线。

**结论强度：** 工具参数、Planner日志与checker结果直接吻合。

定位：trace api_calls.index=216,226,229,234,239；tool_chain.index=74,77,82,88。
[首次轨迹](./experiments/runs/20261003-143255-full-5c501118/dcae50c1/agent-traces/toolchains/20261003T202558206120_dcae50c1_6d9196ba-807f-4bc6-b8a9-912fe06ef7da_completed.json) · [原始检查](./experiments/runs/20261003-143255-full-5c501118/dcae50c1/fb6876d2.checks.json) · [任务/返回](./experiments/runs/20261003-143255-full-5c501118/dcae50c1/fb6876d2.job.json) · [worker与黑板](./experiments/runs/20261003-143255-full-5c501118/dcae50c1/fb6876d2.agent-state.json)

## 11. 8d0c8aef｜Multiple Target Inspection 1

场景 `6702871a`；原始检查 **0/4通过**；超时；Agent自判成功=None。

**指令：** Drone 10在20米访问目标6拍照，再访问目标16拍照。

**失分项：** target_is_reached_by_drone {"target_id": "f6e5ed66", "drone_id": "6340b330"}，检查值=0；target_in_photo_taken_by_drone {"target_id": "f6e5ed66", "drone_id": "6340b330"}，检查值=False；target_is_reached_by_drone {"target_id": "e164cf87", "drone_id": "6340b330"}，检查值=0；target_in_photo_taken_by_drone {"target_id": "e164cf87", "drone_id": "6340b330"}，检查值=False。

**首次可定位的偏离/断点：** API 75已经返回目标6，然而指定Drone 10在API 34之后没有再执行移动或拍照；后续大量辅助机探索持续到预算耗尽。已发现的第一个子任务也未转入执行。

**确定事实：** 0/4检查通过。331次API，最终黑板已有目标6而缺目标16；Drone 10本题只有起飞(API 21)和一次移动(API 34)，拍照调用为0。两个目标在原场景均存在。

**解释及置信边界：** 探索与任务执行调度脱节：等搜全或持续扩张探索，导致已经可推进的指定机访问/拍照没有发生。 异常tool_chain为空，不能确认模型是否把“先找齐所有目标”作为显式策略，也不能保证先做目标6会导致整题成功。

**结论强度：** 已发现目标后指定机未执行确定；内部调度理由未知。

定位：trace api_calls.index=21,34,75,327,331。
[首次轨迹](./experiments/runs/20261003-143255-full-5c501118/6702871a/agent-traces/toolchains/20261003T202155930754_6702871a_ac6e688e-9161-47e4-b059-717fcb0de793_exception.json) · [原始检查](./experiments/runs/20261003-143255-full-5c501118/6702871a/8d0c8aef.checks.json) · [任务/返回](./experiments/runs/20261003-143255-full-5c501118/6702871a/8d0c8aef.job.json) · [worker与黑板](./experiments/runs/20261003-143255-full-5c501118/6702871a/8d0c8aef.agent-state.json)

## 12. 60a09dd0｜Triangle Formation - 3 Drones 1

场景 `1a9c1abc`；原始检查 **1/3通过**；正常返回；Agent自判成功=True。

**指令：** 三机23米编队，Drone15到(615,440)，Drone12到(538,453)，Drone7到(429,428)。

**失分项：** drone_has_visited_position {"drone_id": "c33b1a92", "x": 615.0, "y": 440.0, "z": 23.0, "tolerance": 3.0}，检查值=0；drone_has_visited_position {"drone_id": "cf4ddad8", "x": 538.0, "y": 453.0, "z": 23.0, "tolerance": 3.0}，检查值=0。

**首次可定位的偏离/断点：** API 16、17拒绝前两个坐标，均与Polygon Obstacle 4冲突。Agent将其改为(623,440)与(538,412)，相差8米、41米后返回[TASK DONE]。

**确定事实：** 1/3检查通过，两个原目标位置访问失败。输出诚实列出偏移，但完成标记仍把近似位置算完成。

**解释及置信边界：** 给定坐标环境冲突与失败收束/完成语义混在一起；错误完成不是“不知道偏离”，而是承认偏离后仍确认完成。 未重做完整几何可达性证明。

**结论强度：** 位置冲突、替代、外判失败与自判成功确定。

定位：trace api_calls.index=16,17。
[首次轨迹](./experiments/runs/20261003-143255-full-5c501118/1a9c1abc/agent-traces/toolchains/20261003T201937806768_1a9c1abc_61353650-9311-447f-aedb-56137642659b_completed.json) · [原始检查](./experiments/runs/20261003-143255-full-5c501118/1a9c1abc/60a09dd0.checks.json) · [任务/返回](./experiments/runs/20261003-143255-full-5c501118/1a9c1abc/60a09dd0.job.json) · [worker与黑板](./experiments/runs/20261003-143255-full-5c501118/1a9c1abc/60a09dd0.agent-state.json)

## 13. c7a2b391｜Triangle Formation - 3 Drones 3

场景 `1a9c1abc`；原始检查 **2/3通过**；正常返回；Agent自判成功=True。

**指令：** 三机19米编队，Drone13必须到(611,526)，其余两机到(421,485)、(469,416)。

**失分项：** drone_has_visited_position {"drone_id": "d3493bff", "x": 611.0, "y": 526.0, "z": 19.0, "tolerance": 3.0}，检查值=0。

**首次可定位的偏离/断点：** API 18拒绝Drone13目标点，Agent将x从611改为623，最终以“Formation established”与[TASK DONE]表示完成。

**确定事实：** 2/3检查通过，唯一失败是Drone13精确访问，偏离12米而容差3米；输出明确解释障碍和替代位置。

**解释及置信边界：** 目标替代后的完成判定错误；物理约束层面的失分不应直接当作路径规划算力不足。 未重新运行可达性/反事实验证。

**结论强度：** 原点拒绝和改点后宣告完成确定。

定位：trace api_calls.index=18。
[首次轨迹](./experiments/runs/20261003-143255-full-5c501118/1a9c1abc/agent-traces/toolchains/20261003T202101418701_1a9c1abc_e663b9d7-6db3-4622-ac6b-a9ed7637371e_completed.json) · [原始检查](./experiments/runs/20261003-143255-full-5c501118/1a9c1abc/c7a2b391.checks.json) · [任务/返回](./experiments/runs/20261003-143255-full-5c501118/1a9c1abc/c7a2b391.job.json) · [worker与黑板](./experiments/runs/20261003-143255-full-5c501118/1a9c1abc/c7a2b391.agent-state.json)

## 对后续分析的边界

本补充不提出新架构，也不保证任何候选修复会提高成功率。它补的是首次证据：把任务/场景矛盾、动作语义替代、发现完整性假设、搜索与执行调度分别记录，供后续决定先研究哪种失败。

共消费并复核85个文件的SHA-256；13条命令与源任务一致。新JSON还保留每题失分响应、定位API回执、相关Planner日志及最终输出；它不修改前一轮审计的5个文件。
