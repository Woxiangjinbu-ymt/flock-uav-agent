# 原版 B0 首次执行失败再核查：12 题案例簿

本案例簿从 [首次执行任务索引](./docs/results/baseline-deployment-20261005/tasks.json) 中严格选择 `cohort=first_attempt_no_replay`。24条模型记录全部 `generation=1`，证据路径没有 `retries`；按原指令与 job 创建时间匹配唯一 toolchain，避免同场景相同指令的其他任务混入。机器可读明细、原始记录路径及SHA-256见 [cases.json](cases.json)。

这是有目的的机制抽样，不代表各原因在全量失败中的占比。原始任务分母和分数没有改变；没有运行模型、重放任务或修改任何运行代码。下文“允许观测”仅指在线工具能返回的信息；完整场景快照与checker参数只用于离线核验，绝不当作Agent事先知道的信息。

证据级别：**A级**为参数、回执、原始状态或冻结源码直接吻合；**B级**为合理但仍需独立验证的原因解释；**未知级**表示日志不能恢复该结论。缺少某个动作不自动等于Planner/Memory架构缺陷。`agent_success`和`[TASK DONE]`是运行时口径，自然语言是否承认部分完成单独判断。

## 选样与首次结果

| task_id | 类型 | DeepSeek首次 | Qwen首次 | 重要边界 |
|---|---|---|---|---|
| `82077301` | 同一句距离指令的两种失败：阈值舍入与动作/关系混淆 | 2/3 | 2/3 | 无显式provider错误/超时 |
| `25d30560` | 指定航点位于不可穿越障碍内：至少一个要求在场景几何中无解 | 3/4 | 3/4 | 无显式provider错误/超时 |
| `f30cc643` | 用户写1米，隐藏“已起飞”检查要求5米 | 3/4 | 3/4 | 无显式provider错误/超时 |
| `55b61526` | 未发现真实目标，却从局部搜索推出“不存在” | 2/3 | 2/3 | qwen超时 |
| `7af66f50` | 到达历史提示不等于拍照瞬间包含目标 | 0/4 | 3/4 | 无显式provider错误/超时 |
| `9c0b52a0` | 将已发现目标当全部目标；同时存在历史进度继承 | 1/1 | 0/1 | DS before已100%，仅辅助对照 |
| `4ccc9027` | 首次执行也有明确实体替换和覆盖计划句柄误用 | 1/1 | 0/1 | 无显式provider错误/超时 |
| `bfc721cc` | 三条队形直线和九张照片不等于足够面积覆盖 | 4/4 | 3/4 | 无显式provider错误/超时 |
| `f817698f` | 80米命令成功却因二进制浮点比较失分 | 11/12 | 12/12 | 无显式provider错误/超时 |
| `cf8ee51f` | 38/39的末尾落地缺失；另一模型首次是402，重跑成功不能混入 | 3/39 | 38/39 | deepseek有provider错误；qwen超时 |
| `d46027e4` | 真实电量不足与起点前超时是两种失败 | 16/23 | 0/23 | qwen超时 |
| `cba90ef3` | 一次模型返回后零工具退出：日志能证明停止，不能证明内在理由 | 2/2 | 0/2 | 无显式provider错误/超时 |

## 成功对照的限制

主要正对照至少四项：DeepSeek `cba90ef3`、`4ccc9027`、`bfc721cc`，Qwen `f817698f`，均满足 `deployment_pass=true`、`worker_confirmed=true`、`provider_errors=[]`，且有实际成功动作。覆盖任务的before/after也分别核对了0→100%、22.375%→100%。它们只能说明原版工具与执行循环有完成这些任务的能力，不能由一个配对样本证明某一种策略必然优越。

DeepSeek `9c0b52a0` 虽满足部署通过条件，但进入本题前进度已100%；不拿它当独立完成贡献。DeepSeek `cf8ee51f` 首次为402余额失败且3/39，不把旧分析引用的generation2 39/39混入成功对照。旧Qwen `29ec452f/0dab4a45`错Drone20案例同属generation2，未入选；本簿实体错误使用首次 `4ccc9027`。

## 1. 82077301：同一句距离指令的两种失败：阈值舍入与动作/关系混淆

**用户要求。** 两机先到指定位置，Drone 5 再移动到距 Drone 3 至多 19 m；这描述最终相对距离，不是只飞 19 m。

**允许观测与离线事实边界。** 列表和 get_drone_status 提供正确实体 ID、两机坐标；工具随后返回实际到达坐标。所需几何量可从允许观测计算，无需隐藏 checker。

**模型方案。** DeepSeek 沿两机连线计算“恰好 19 m”的落点并保留一位小数。Qwen 明确将要求解释成朝 Drone 3 移动 19 m，最终报告也列出约 194.4 m 间距。

**实际工具/API。** DeepSeek 将 Drone 5 导航到 (462.1,433.2,17)，Drone 3 在 (454,416,17)，距离 sqrt(8.1²+17.2²)=19.011838417 m。Qwen 调 move_towards_and_sense(distance=19,heading=205.245)，实际只缩短19 m。两机悬停等动作真实发生。

**首个可定位偏离。** DeepSeek 首个关键偏离是把计算出的边界坐标舍入到超出允许上限；Qwen 首个关键偏离是选错所需物理量（动作长度替代最终间距）。

**checker失分与完成表述。** 两模型均2/3；two_drones_distance返回 DeepSeek 19.012、Qwen 194.378，阈值为19。DeepSeek文本明确宣称已在19 m内；Qwen虽然写“完成”，同时公开了194.4 m，不宜把它说成隐瞒观测。

**证据级别、竞争解释及日志限制。** A级：参数、最终位置、距离重算与失分一致。没有证据显示实体 ID 错误或 Memory 丢失；无需诉诸新Planner架构。

**最小待验证假设。** 先离线核验“最终关系 vs 单动作长度”的语义样例与带舍入坐标的数值余量；验证只加窄范围后置几何检查能否发现此偏离。不要据此直接加全任务合同门控。

原始证据与唯一执行映射：

- **deepseek / generation 1**：job `7008cc8d-afb8-46ad-8245-12cabaf8050f`，toolchain `3dd090f6-06cf-4617-a381-0fe3e2a6a206`；[job](./experiments/runs/20261003-143255-full-5c501118/fb8971ec/82077301.job.json) · [toolchain](./experiments/runs/20261003-143255-full-5c501118/fb8971ec/agent-traces/toolchains/20261003T144810075586_fb8971ec_3dd090f6-06cf-4617-a381-0fe3e2a6a206_completed.json) · [checks](./experiments/runs/20261003-143255-full-5c501118/fb8971ec/82077301.checks.json) · [before](./experiments/runs/20261003-143255-full-5c501118/fb8971ec/82077301.before.json) · [after](./experiments/runs/20261003-143255-full-5c501118/fb8971ec/82077301.after.json) · [agent-state](./experiments/runs/20261003-143255-full-5c501118/fb8971ec/82077301.agent-state.json)。

- **qwen / generation 1**：job `43a564d7-25f4-4a1e-82f5-6fd13a6b191c`，toolchain `674f3ca1-20f3-4e76-9ae4-59eeb206ff7e`；[job](./experiments/runs/20261004-142512-full-aca112a7/fb8971ec/82077301.job.json) · [toolchain](./experiments/runs/20261004-142512-full-aca112a7/fb8971ec/agent-traces/toolchains/20261004T150359524051_fb8971ec_674f3ca1-20f3-4e76-9ae4-59eeb206ff7e_completed.json) · [checks](./experiments/runs/20261004-142512-full-aca112a7/fb8971ec/82077301.checks.json) · [before](./experiments/runs/20261004-142512-full-aca112a7/fb8971ec/82077301.before.json) · [after](./experiments/runs/20261004-142512-full-aca112a7/fb8971ec/82077301.after.json) · [agent-state](./experiments/runs/20261004-142512-full-aca112a7/fb8971ec/82077301.agent-state.json)。

## 2. 25d30560：指定航点位于不可穿越障碍内：至少一个要求在场景几何中无解

**用户要求。** Drone 1必须访问(288,205,16)，随后到(771,156,16)；另一个Drone 7完成两点巡逻。

**允许观测与离线事实边界。** 最初附近感知并未保证掌握全部障碍，但命令返回目标点冲突，并给出 Circle Obstacle 4；后续允许观测/黑板有中心(288,181)、半径41、height=0。离线完整场景仅用于核实，不是在线输入。

**模型方案。** 两模型先尝试原点，收到障碍错误后选择最近安全点，再继续第二个指定点。DeepSeek还把安全点从y=240缩至226；Qwen用y=227。

**实际工具/API。** 原move_along_path/navigate_to返回“Waypoint/Target position conflicts with circle obstacle Circle Obstacle 4”。绕行后Drone7两个点与Drone1第二点完成；Drone1只能到(288,226)/(288,227)。

**首个可定位偏离。** 首个偏离不是模型擅自漏点，而是原点被执行器拒绝。指定点距障碍中心24 m，距圆外至少17 m，而checker仅容许3 m。冻结模型/碰撞代码把height=0视作任何高度不可穿越，抬高也不解此目标。

**checker失分与完成表述。** 两模型均3/4，唯一失败是该点visited_position。文本均公开说明此点不可安全占据，但同时使用“patrol complete”及[TASK DONE]；runtime success=True属于部分完成与结束标记混用，不是毫无保留地假称到过原点。

**证据级别、竞争解释及日志限制。** A级：障碍几何、工具拒绝与容差可直接证明该指定点在当前合法运动接口下不可达。未证明场景其他所有路线均无解，结论仅限这一要求。

**最小待验证假设。** 先做离线任务可满足性标注，保留原分母并另列规格缺陷；若改评测，需独立版本而非在线绕过障碍或偷偷改变目标。

原始证据与唯一执行映射：

- **deepseek / generation 1**：job `29cb5727-fe2f-43db-8d87-f7424b1cea03`，toolchain `fdcde96b-1763-4325-a079-11ce1dffb6e6`；[job](./experiments/runs/20261003-143255-full-5c501118/cc05a425/25d30560.job.json) · [toolchain](./experiments/runs/20261003-143255-full-5c501118/cc05a425/agent-traces/toolchains/20261003T152218375177_cc05a425_fdcde96b-1763-4325-a079-11ce1dffb6e6_completed.json) · [checks](./experiments/runs/20261003-143255-full-5c501118/cc05a425/25d30560.checks.json) · [before](./experiments/runs/20261003-143255-full-5c501118/cc05a425/25d30560.before.json) · [after](./experiments/runs/20261003-143255-full-5c501118/cc05a425/25d30560.after.json) · [agent-state](./experiments/runs/20261003-143255-full-5c501118/cc05a425/25d30560.agent-state.json)。

- **qwen / generation 1**：job `9fcd77fa-255e-4756-8877-12902a4eda15`，toolchain `92ba3216-b746-42d2-8b8b-20b486afe985`；[job](./experiments/runs/20261004-142512-full-aca112a7/cc05a425/25d30560.job.json) · [toolchain](./experiments/runs/20261004-142512-full-aca112a7/cc05a425/agent-traces/toolchains/20261004T162420301307_cc05a425_92ba3216-b746-42d2-8b8b-20b486afe985_completed.json) · [checks](./experiments/runs/20261004-142512-full-aca112a7/cc05a425/25d30560.checks.json) · [before](./experiments/runs/20261004-142512-full-aca112a7/cc05a425/25d30560.before.json) · [after](./experiments/runs/20261004-142512-full-aca112a7/cc05a425/25d30560.after.json) · [agent-state](./experiments/runs/20261004-142512-full-aca112a7/cc05a425/25d30560.agent-state.json)。

## 3. f30cc643：用户写1米，隐藏“已起飞”检查要求5米

**用户要求。** 起飞至1 m，访问(532,516,1)，短暂悬停，返航落地，验证完整飞行周期。

**允许观测与离线事实边界。** 原指令直接给定1 m；合法状态、命令收据可确认起飞至1 m、航点和落地。在线Agent没有看到min_altitude=5的隐藏阈值。

**模型方案。** 两模型按1 m执行；受障碍阻挡后改中间航点，仍保持1 m，并在家落地。

**实际工具/API。** take_off(altitude=1) success；导航绕行到指定点；hover、return/返航路径和land完成。工具与最后输出没有宣称曾达到5 m。

**首个可定位偏离。** 行动与原指令没有在高度上偏离。偏离发生于任务文本与check_drone_has_taken_off(min_altitude=5)之间。

**checker失分与完成表述。** 两模型均3/4，只丢“已起飞至少5 m”检查，其他航点/返航/落地检查通过。

**证据级别、竞争解释及日志限制。** A级规格不一致。不能据此断定需要更强Memory或把在线起飞统一偷偷抬到5 m；那将违背明确的1 m要求。

**最小待验证假设。** 先审计生成器的高度与checker阈值是否成对一致；本轮只列证据，不改隐藏条件、不重算旧成绩。

原始证据与唯一执行映射：

- **deepseek / generation 1**：job `c16eff9a-8e3e-4528-bb83-f5f47d160e4a`，toolchain `06cb8158-cb3f-463f-b6b1-d166a0c70e52`；[job](./experiments/runs/20261003-143255-full-5c501118/c350908c/f30cc643.job.json) · [toolchain](./experiments/runs/20261003-143255-full-5c501118/c350908c/agent-traces/toolchains/20261003T145143762075_c350908c_06cb8158-cb3f-463f-b6b1-d166a0c70e52_completed.json) · [checks](./experiments/runs/20261003-143255-full-5c501118/c350908c/f30cc643.checks.json) · [before](./experiments/runs/20261003-143255-full-5c501118/c350908c/f30cc643.before.json) · [after](./experiments/runs/20261003-143255-full-5c501118/c350908c/f30cc643.after.json) · [agent-state](./experiments/runs/20261003-143255-full-5c501118/c350908c/f30cc643.agent-state.json)。

- **qwen / generation 1**：job `e2994271-509a-440c-ae33-9449fc859ba1`，toolchain `b031f8d5-4134-4a41-9d3b-23b074101f18`；[job](./experiments/runs/20261004-142512-full-aca112a7/c350908c/f30cc643.job.json) · [toolchain](./experiments/runs/20261004-142512-full-aca112a7/c350908c/agent-traces/toolchains/20261004T151711756955_c350908c_b031f8d5-4134-4a41-9d3b-23b074101f18_completed.json) · [checks](./experiments/runs/20261004-142512-full-aca112a7/c350908c/f30cc643.checks.json) · [before](./experiments/runs/20261004-142512-full-aca112a7/c350908c/f30cc643.before.json) · [after](./experiments/runs/20261004-142512-full-aca112a7/c350908c/f30cc643.after.json) · [agent-state](./experiments/runs/20261004-142512-full-aca112a7/c350908c/f30cc643.agent-state.json)。

## 4. 55b61526：未发现真实目标，却从局部搜索推出“不存在”

**用户要求。** Drone14按顺序访问Polygon Target1、Circle Target4、Waypoint1，各处悬停拍照。

**允许观测与离线事实边界。** 前两目标通过合法感知可绑定；两模型整份API response中均没有真实Waypoint1的ID 7c7a6f6d。离线完整after确认该目标存在于(844,79)，但这些隐藏坐标不能反向交给在线Agent。

**模型方案。** DeepSeek完成前两目标后做27次navigate_to_and_sense等探索，另有全机感知；Qwen也在前两目标后继续寻找，最终超时。

**实际工具/API。** 两模型各有两次take_photo success，前两目标到达通过。DeepSeek共201次API，末尾返航并宣称“无此实体/无法定位”；Qwen共73次API后窗口取消。

**首个可定位偏离。** 最早可确定的推理偏离是DeepSeek把“黑板及采样区域没有返回该实体”提升为“环境不存在Waypoint1”。此前搜索选择是否最优不能仅凭未发现结果判定。

**checker失分与完成表述。** 两模型2/3，第三目标reached=0。DeepSeek自然语言明确承认仅完成2/3，虽然runtime success=True；不能记成自然语言全成功谎报。

**证据级别、竞争解释及日志限制。** A级：目标存在、未进入API可见实体响应、未到达与文本推断；B级：搜索未覆盖/未在合适点感知的具体几何原因仍需恢复实际观测覆盖，不是Memory忘了已经看到的目标。

**最小待验证假设。** 先离线重建成功移动后的感知足迹与尚未观察区域，检验“未知不等于不存在”的终止规则。不要直接把随机探索次数或调用上限增加视作已验证方案。

原始证据与唯一执行映射：

- **deepseek / generation 1**：job `13542562-84d6-4f8f-a100-5ae199440479`，toolchain `80eca5ee-83be-4d89-8bf2-aeb70011bc54`；[job](./experiments/runs/20261003-143255-full-5c501118/76802002/55b61526.job.json) · [toolchain](./experiments/runs/20261003-143255-full-5c501118/76802002/agent-traces/toolchains/20261003T150622792025_76802002_80eca5ee-83be-4d89-8bf2-aeb70011bc54_completed.json) · [checks](./experiments/runs/20261003-143255-full-5c501118/76802002/55b61526.checks.json) · [before](./experiments/runs/20261003-143255-full-5c501118/76802002/55b61526.before.json) · [after](./experiments/runs/20261003-143255-full-5c501118/76802002/55b61526.after.json) · [agent-state](./experiments/runs/20261003-143255-full-5c501118/76802002/55b61526.agent-state.json)。

- **qwen / generation 1**：job `811e8aa4-b2d3-4cdd-bc15-32a69408757f`，toolchain `f7f29ecb-6548-4a5f-a26f-4a01b7571f00`；[job](./experiments/runs/20261004-142512-full-aca112a7/76802002/55b61526.job.json) · [toolchain](./experiments/runs/20261004-142512-full-aca112a7/76802002/agent-traces/toolchains/20261004T153438687092_76802002_f7f29ecb-6548-4a5f-a26f-4a01b7571f00_exception.json) · [checks](./experiments/runs/20261004-142512-full-aca112a7/76802002/55b61526.checks.json) · [before](./experiments/runs/20261004-142512-full-aca112a7/76802002/55b61526.before.json) · [after](./experiments/runs/20261004-142512-full-aca112a7/76802002/55b61526.after.json) · [agent-state](./experiments/runs/20261004-142512-full-aca112a7/76802002/55b61526.agent-state.json)。

## 5. 7af66f50：到达历史提示不等于拍照瞬间包含目标

**用户要求。** Drone10起飞17 m，先到Circle Target1拍照，再到Waypoint1拍照。

**允许观测与离线事实边界。** Qwen最初把目标名当target_id查询得到404；之后航线响应出现“Reached target(s): Circle Target1”，但它当时并没有通过实体详情得到该目标中心。状态查询明确当前为(150,480,17)。DeepSeek搜索后仍未发现两个目标。

**模型方案。** Qwen将航线经过目标的历史reached提示当作当前已经位于目标处，立即拍照；之后成功发现并到达Waypoint1再拍第二张。DeepSeek做多机搜索后明确报告被阻塞。

**实际工具/API。** Qwen照片8053ea90在(150,480,17)拍摄，Circle Target1真实中心(209,650)，平面距约179.947 m；感知半径50。该次长线移动沿途曾触发目标reached。第二张c290daa2在Waypoint1(673,489)处拍摄。

**首个可定位偏离。** 第一张照片前，历史事件“沿途到达过”被误当作“此刻可见”；快门成功又被当作照片包含目标。它不是没有拍照。

**checker失分与完成表述。** Qwen3/4，Circle Target1 reached通过但target_in_photo失败，Waypoint1两项通过。DeepSeek0/4，未定位、无照片，明确写Mission blocked；两模型同题不同机制，不能归为单一照片问题。

**证据级别、竞争解释及日志限制。** A级：照片时位置、目标中心、半径、API提示及checker代码一致。目标中心由离线证据核实，不声称它已提供给在线Agent。无法仅凭此例判断Memory结构是否不足。

**最小待验证假设。** 先核验工具“沿途reached”和“当前可感知”的返回语义，并在拍摄目标前建立当前目标/当前位置的窄范围检查；与更改全部计划架构分开验证。

原始证据与唯一执行映射：

- **deepseek / generation 1**：job `140529ce-b5d1-460b-97ee-2cb5056b4416`，toolchain `2ab6ba85-e04f-4b86-914f-fd27dcfcffb3`；[job](./experiments/runs/20261003-143255-full-5c501118/93831d6d/7af66f50.job.json) · [toolchain](./experiments/runs/20261003-143255-full-5c501118/93831d6d/agent-traces/toolchains/20261003T160741692722_93831d6d_2ab6ba85-e04f-4b86-914f-fd27dcfcffb3_completed.json) · [checks](./experiments/runs/20261003-143255-full-5c501118/93831d6d/7af66f50.checks.json) · [before](./experiments/runs/20261003-143255-full-5c501118/93831d6d/7af66f50.before.json) · [after](./experiments/runs/20261003-143255-full-5c501118/93831d6d/7af66f50.after.json) · [agent-state](./experiments/runs/20261003-143255-full-5c501118/93831d6d/7af66f50.agent-state.json)。

- **qwen / generation 1**：job `aa88839e-d57d-4bb3-bf4b-8523831be909`，toolchain `e679d1b7-d02e-4624-875b-987ad1d2eb7e`；[job](./experiments/runs/20261004-142512-full-aca112a7/93831d6d/7af66f50.job.json) · [toolchain](./experiments/runs/20261004-142512-full-aca112a7/93831d6d/agent-traces/toolchains/20261004T174950827678_93831d6d_e679d1b7-d02e-4624-875b-987ad1d2eb7e_completed.json) · [checks](./experiments/runs/20261004-142512-full-aca112a7/93831d6d/7af66f50.checks.json) · [before](./experiments/runs/20261004-142512-full-aca112a7/93831d6d/7af66f50.before.json) · [after](./experiments/runs/20261004-142512-full-aca112a7/93831d6d/7af66f50.after.json) · [agent-state](./experiments/runs/20261004-142512-full-aca112a7/93831d6d/7af66f50.agent-state.json)。

## 6. 9c0b52a0：将已发现目标当全部目标；同时存在历史进度继承

**用户要求。** 起飞必要无人机并分配到所有目标，使session进度超过95%。

**允许观测与离线事实边界。** Qwen当前黑板只有Fixed Target1，多个局部搜索仍未使其他实体进入黑板；它不能通过一次全机感知证明全场只有一个。离线after有3个目标，before已有2个reached。

**模型方案。** Qwen自称系统搜索了全场，断言只有1个目标，随后把5架机全部分配到这个目标，用1/1推断100%。DeepSeek同题观察并实际飞向三目标，但其任务开始前checker进度已100%。

**实际工具/API。** Qwen134次API，多条搜索/绕行、5架起飞，最终全部围绕Fixed Target1；after真实进度仍66%，与before相同。DeepSeek也有合法导航和查询，但before100/after100限制对照解释。

**首个可定位偏离。** 把观测集合当封闭全集，再自行重定义进度分母。不是证明黑板遗忘了已出现的目标，也不是实际进度由0升到33%。

**checker失分与完成表述。** Qwen0/1，task_progress=0.66；DeepSeek1/1且deployment_pass/worker/无providererror，但历史已达100%，只作辅助对照，不能算本题独立产生了全部通过。

**证据级别、竞争解释及日志限制。** A级：局部黑板、文本分母、前后真实进度；B级：怎样以合法信息证明搜索充分仍待设计。本例从隐藏检查和完整快照取得的进度仅作离线诊断。Server 另有 AGENT 可访问的当前任务进度接口，但未暴露在原版29个模型工具中；接入该公开反馈属于方法变更，不能混为原运行已拥有的信息。

**最小待验证假设。** 先区分已知目标完成率、已探索空间和官方任务进度，不在未知全集下声明全局百分比。检验这些语义提示是否足够，再决定需要怎样的搜索状态。

原始证据与唯一执行映射：

- **deepseek / generation 1**：job `3d909d1a-19b0-4755-90d2-7e31eb69d539`，toolchain `a7eb8c6e-78cf-4a81-b4fd-a1b9b903d7fc`；[job](./experiments/runs/20261003-143255-full-5c501118/3973d0d6/9c0b52a0.job.json) · [toolchain](./experiments/runs/20261003-143255-full-5c501118/3973d0d6/agent-traces/toolchains/20261003T181151586693_3973d0d6_a7eb8c6e-78cf-4a81-b4fd-a1b9b903d7fc_completed.json) · [checks](./experiments/runs/20261003-143255-full-5c501118/3973d0d6/9c0b52a0.checks.json) · [before](./experiments/runs/20261003-143255-full-5c501118/3973d0d6/9c0b52a0.before.json) · [after](./experiments/runs/20261003-143255-full-5c501118/3973d0d6/9c0b52a0.after.json) · [agent-state](./experiments/runs/20261003-143255-full-5c501118/3973d0d6/9c0b52a0.agent-state.json)。

- **qwen / generation 1**：job `e232dfc2-3c03-44e5-81a2-f886b10ff01c`，toolchain `f98e2ab0-29a0-4fef-b1a6-20161323bae3`；[job](./experiments/runs/20261004-142512-full-aca112a7/3973d0d6/9c0b52a0.job.json) · [toolchain](./experiments/runs/20261004-142512-full-aca112a7/3973d0d6/agent-traces/toolchains/20261004T222725787812_3973d0d6_f98e2ab0-29a0-4fef-b1a6-20161323bae3_completed.json) · [checks](./experiments/runs/20261004-142512-full-aca112a7/3973d0d6/9c0b52a0.checks.json) · [before](./experiments/runs/20261004-142512-full-aca112a7/3973d0d6/9c0b52a0.before.json) · [after](./experiments/runs/20261004-142512-full-aca112a7/3973d0d6/9c0b52a0.after.json) · [agent-state](./experiments/runs/20261004-142512-full-aca112a7/3973d0d6/9c0b52a0.agent-state.json)。

## 7. 4ccc9027：首次执行也有明确实体替换和覆盖计划句柄误用

**用户要求。** 指定Drone4覆盖Circle Target2至少95%，高度20 m。

**允许观测与离线事实边界。** Qwen初始全机局部感知只返回Circle Target1；真实Circle Target2未发现。调用generate_coverage_path对错误Target1返回了有效coverage-dc4cb7a467cd43608d3a09b076bb658f。

**模型方案。** Qwen把“未发现Target2”变成“没有Target2”，主动改做Target1；又将Target1实体ID当coverage_plan_id，报错后不用返回的有效句柄，改画手动路线。

**实际工具/API。** move_along_path(coverage_plan_id=4b43e7e9)报not found；后续20个手工航点全部完成，但在Target1区域。原Target2覆盖before0/after0。

**首个可定位偏离。** 首个实质偏离是generate_coverage_path的target_id就绑定了错误实体；句柄错误是第二个独立可见问题。只修句柄仍会认真执行错误目标。

**checker失分与完成表述。** Qwen0/1，指定Target2覆盖为0。DeepSeek首次deployment_pass、worker=true、无providererror；探索发现Target2中心(169,144)，使用正确生成句柄执行7点路径，before0→after100%，为有效操作成功对照。

**证据级别、竞争解释及日志限制。** A级：这是generation1首次，不是旧Drone20的generation2案例；原输出主动解释了替换行为。无需推断内部Memory组件故障。

**最小待验证假设。** 先离线检查名字-ID绑定与计划句柄类型混用的最小约束，验证拒绝错误参数能否保留正确探索和执行路径。不要把未绑定目标直接变成禁止行动。

原始证据与唯一执行映射：

- **deepseek / generation 1**：job `0e07253b-6d09-4a4b-a8eb-e703430f003f`，toolchain `b534a7d3-1dc3-475f-b611-510fdf33bddb`；[job](./experiments/runs/20261003-143255-full-5c501118/ef36e27a/4ccc9027.job.json) · [toolchain](./experiments/runs/20261003-143255-full-5c501118/ef36e27a/agent-traces/toolchains/20261003T164339650355_ef36e27a_b534a7d3-1dc3-475f-b611-510fdf33bddb_completed.json) · [checks](./experiments/runs/20261003-143255-full-5c501118/ef36e27a/4ccc9027.checks.json) · [before](./experiments/runs/20261003-143255-full-5c501118/ef36e27a/4ccc9027.before.json) · [after](./experiments/runs/20261003-143255-full-5c501118/ef36e27a/4ccc9027.after.json) · [agent-state](./experiments/runs/20261003-143255-full-5c501118/ef36e27a/4ccc9027.agent-state.json)。

- **qwen / generation 1**：job `282b3954-17e0-444d-a5bb-c0e06b7f2bb2`，toolchain `fc55bb83-280f-49ba-8370-0cfe140fb695`；[job](./experiments/runs/20261004-142512-full-aca112a7/ef36e27a/4ccc9027.job.json) · [toolchain](./experiments/runs/20261004-142512-full-aca112a7/ef36e27a/agent-traces/toolchains/20261004T185049382407_ef36e27a_fc55bb83-280f-49ba-8370-0cfe140fb695_completed.json) · [checks](./experiments/runs/20261004-142512-full-aca112a7/ef36e27a/4ccc9027.checks.json) · [before](./experiments/runs/20261004-142512-full-aca112a7/ef36e27a/4ccc9027.before.json) · [after](./experiments/runs/20261004-142512-full-aca112a7/ef36e27a/4ccc9027.after.json) · [agent-state](./experiments/runs/20261004-142512-full-aca112a7/ef36e27a/4ccc9027.agent-state.json)。

## 8. bfc721cc：三条队形直线和九张照片不等于足够面积覆盖

**用户要求。** 三机间距21.4 m成平行队形扫过Circle Target3并拍照；用户文本没有明示85%的隐藏面积阈值。

**允许观测与离线事实边界。** 合法目标详情/黑板可提供圆中心(1891,1304)、半径66及无人机task_radius20；但checker的85%属于离线规则。

**模型方案。** Qwen布置x=1869.6/1891/1912.4三条平行线，由y1370经过1304到1238，每段拍照；DeepSeek使用generate_coverage_path分配更全面的路径。

**实际工具/API。** Qwen三条扫描线、9次快门均执行，API总148次；目标面积覆盖从19.384%升到76.745%。DeepSeek两次生成覆盖计划并执行，22.375%升至100%，3张照片。

**首个可定位偏离。** Qwen把“按队形扫过圆域”报告为完成，但三条线无法囊括整个圆域两侧；不能把照片数当面积代理。同时必须承认用户文本与隐藏量化阈值的差别。

**checker失分与完成表述。** Qwen3/4，唯一失分coverage=0.7674486803519062<0.85；DeepSeek4/4、deployment/worker真、无providererror。起始覆盖不同，因此是方法可行性对照，不是纯净单变量消融。

**证据级别、竞争解释及日志限制。** A级轨迹与面积结果；B级最少再补几条线可达何覆盖需几何重算，不能直接声称某个固定动作必过。

**最小待验证假设。** 先在已知目标形状上比较轨迹覆盖几何与“扫过一次”的口语语义，并检查任务文本应否明确量化覆盖需求。不要用隐藏checker阈值反向提示模型。

原始证据与唯一执行映射：

- **deepseek / generation 1**：job `f9c672c7-9857-4d0e-bca0-4f69c1647a26`，toolchain `681aab96-8309-4c52-a28f-b8052d754060`；[job](./experiments/runs/20261003-143255-full-5c501118/ad34350a/bfc721cc.job.json) · [toolchain](./experiments/runs/20261003-143255-full-5c501118/ad34350a/agent-traces/toolchains/20261003T152556666362_ad34350a_681aab96-8309-4c52-a28f-b8052d754060_completed.json) · [checks](./experiments/runs/20261003-143255-full-5c501118/ad34350a/bfc721cc.checks.json) · [before](./experiments/runs/20261003-143255-full-5c501118/ad34350a/bfc721cc.before.json) · [after](./experiments/runs/20261003-143255-full-5c501118/ad34350a/bfc721cc.after.json) · [agent-state](./experiments/runs/20261003-143255-full-5c501118/ad34350a/bfc721cc.agent-state.json)。

- **qwen / generation 1**：job `fbc24563-9fc6-4a09-885a-56a28fc39d7e`，toolchain `615c3813-0fef-45e1-922a-0f6f4769aa5b`；[job](./experiments/runs/20261004-142512-full-aca112a7/ad34350a/bfc721cc.job.json) · [toolchain](./experiments/runs/20261004-142512-full-aca112a7/ad34350a/agent-traces/toolchains/20261004T161602952885_ad34350a_615c3813-0fef-45e1-922a-0f6f4769aa5b_completed.json) · [checks](./experiments/runs/20261004-142512-full-aca112a7/ad34350a/bfc721cc.checks.json) · [before](./experiments/runs/20261004-142512-full-aca112a7/ad34350a/bfc721cc.before.json) · [after](./experiments/runs/20261004-142512-full-aca112a7/ad34350a/bfc721cc.after.json) · [agent-state](./experiments/runs/20261004-142512-full-aca112a7/ad34350a/bfc721cc.agent-state.json)。

## 9. f817698f：80米命令成功却因二进制浮点比较失分

**用户要求。** 三机到指定目标后各西44 m、南40 m、北80 m。

**允许观测与离线事实边界。** 两模型都合法获得/执行目标导航和定向move_towards；所有最终9次定向动作的距离参数、heading和成功回执均明确。

**模型方案。** DeepSeek用较多斜向小段接近目标，起始点带小数；Qwen多数先到整数坐标目标，再执行同样三段方向距离。

**实际工具/API。** DeepSeek Drone8最后北移起止y=61.68378564061352→141.6837856406135，离线双精度相减79.99999999999999；Qwen同机起止61.999999999999986→142.0，差80.00000000000001。两边参数都是distance=80,heading=0，API success。

**首个可定位偏离。** 首个可证偏离在checker数值边界，而非模型少发了动作。冻结checker由历史起止点累计距离，用total>=80无距离epsilon；显示值另round到2位。

**checker失分与完成表述。** DeepSeek11/12，失分项显示value=80.0却result=false；Qwen12/12、deployment/worker真、无providererror。角度tolerance=5°不是距离容差。

**证据级别、竞争解释及日志限制。** A级：原始未舍入坐标、参数与冻结比较代码相互印证。不能将此例归入Agent完成证据不足，也不能把恰好80.0显示值当内部真实数。

**最小待验证假设。** 先离线对checker加入有数值意义的比较容差做敏感性审计，明确版本，不偷偷重写历史分数；不要求Agent为适应浮点问题额外飞行。

原始证据与唯一执行映射：

- **deepseek / generation 1**：job `72534bce-2e31-448f-b91b-693851c8f5a6`，toolchain `e9e4c809-93eb-4076-8b2e-8a4af70d9ea7`；[job](./experiments/runs/20261003-143255-full-5c501118/793e7745/f817698f.job.json) · [toolchain](./experiments/runs/20261003-143255-full-5c501118/793e7745/agent-traces/toolchains/20261003T171410182271_793e7745_e9e4c809-93eb-4076-8b2e-8a4af70d9ea7_completed.json) · [checks](./experiments/runs/20261003-143255-full-5c501118/793e7745/f817698f.checks.json) · [before](./experiments/runs/20261003-143255-full-5c501118/793e7745/f817698f.before.json) · [after](./experiments/runs/20261003-143255-full-5c501118/793e7745/f817698f.after.json) · [agent-state](./experiments/runs/20261003-143255-full-5c501118/793e7745/f817698f.agent-state.json)。

- **qwen / generation 1**：job `050c6eb2-1fa2-40c6-b730-9ee68280b9bc`，toolchain `77509517-825e-4ee3-9037-ebf79189b9d4`；[job](./experiments/runs/20261004-142512-full-aca112a7/793e7745/f817698f.job.json) · [toolchain](./experiments/runs/20261004-142512-full-aca112a7/793e7745/agent-traces/toolchains/20261004T194842862434_793e7745_77509517-825e-4ee3-9037-ebf79189b9d4_completed.json) · [checks](./experiments/runs/20261004-142512-full-aca112a7/793e7745/f817698f.checks.json) · [before](./experiments/runs/20261004-142512-full-aca112a7/793e7745/f817698f.before.json) · [after](./experiments/runs/20261004-142512-full-aca112a7/793e7745/f817698f.after.json) · [agent-state](./experiments/runs/20261004-142512-full-aca112a7/793e7745/f817698f.agent-state.json)。

## 10. cf8ee51f：38/39的末尾落地缺失；另一模型首次是402，重跑成功不能混入

**用户要求。** 三机各完成长马拉松路径，并在各自终点落地。

**允许观测与离线事实边界。** Qwen掌握任务明确路线及合法障碍信息，路径反馈包含成功航点和剩余电量；时间窗口来自实验运行协议。DeepSeek首次模型请求返回402余额不足，不能比较规划能力。

**模型方案。** Qwen依次处理三机，分别规划27、36、38个含绕行的长路径；前两机走完即落地，第三机路径之后尚未落地。

**实际工具/API。** Qwen API20、24两次land success；API26第三机38点路径success，末端高度仍27 m，电量约25.1%；窗口结束取消，未记录第三机land。DeepSeek首次无mutation、402，3个on_ground可由初始状态满足。

**首个可定位偏离。** Qwen可确定的是最后落地在窗口内未发生；不能根据“缺少land”直接证明模型忘记，因正在被超时取消。也没有电量耗尽证据。

**checker失分与完成表述。** Qwen38/39，唯一第三机on_ground失败，agent_timed_out=true。DeepSeek首次3/39且provider_errors含402；旧案例的39/39是generation2，排除。

**证据级别、竞争解释及日志限制。** A级时序/超时/终态；B级“分机顺序执行及大模型延迟耗掉收尾预算”的具体贡献要看逐调用时长，不能由终态唯一推断。

**最小待验证假设。** 先离线分析首次请求、每段路径和取消时刻是否存在可留给收尾的时间，比较有限重排的可行性；不要未经证据就加无限重试或改总体超时。

原始证据与唯一执行映射：

- **deepseek / generation 1**：job `af552b5b-1b07-420f-831f-08756038b8f1`，toolchain `0f471a36-9e2a-452d-be16-42619123a26b`；[job](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/cf8ee51f.job.json) · [toolchain](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/agent-traces/toolchains/20261003T202953549326_35ce64d8_0f471a36-9e2a-452d-be16-42619123a26b_exception.json) · [checks](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/cf8ee51f.checks.json) · [before](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/cf8ee51f.before.json) · [after](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/cf8ee51f.after.json) · [agent-state](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/cf8ee51f.agent-state.json)。

- **qwen / generation 1**：job `94f2e037-802e-4f3b-a93d-04e74a875d53`，toolchain `1c254bc8-2832-4478-b557-68afd91f3073`；[job](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cf8ee51f.job.json) · [toolchain](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/agent-traces/toolchains/20261005T013623403961_35ce64d8_1c254bc8-2832-4478-b557-68afd91f3073_exception.json) · [checks](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cf8ee51f.checks.json) · [before](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cf8ee51f.before.json) · [after](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cf8ee51f.after.json) · [agent-state](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cf8ee51f.agent-state.json)。

## 11. d46027e4：真实电量不足与起点前超时是两种失败

**用户要求。** Drone22在30 m高度完成20个中间航点，再到终点降落。

**允许观测与离线事实边界。** 合法动作回执持续给出remaining battery、走过距离、失败原因；可读状态有电量。初始并非低电，后续绕行消耗来自实际轨迹。

**模型方案。** DeepSeek绕障到起点并推进前14个中间点，随后尝试继续、就地降落充电、再起飞重试；Qwen在通往起点的障碍区反复尝试不同路径。

**实际工具/API。** DeepSeek API37 partial因后续航点电量不足；39再次insufficient battery；40降落，41 charge失败“not at a valid charging station”；42起飞再消耗电，43仍insufficient，44落地。Qwen仍在起点前，最后可见位置约(175,1035,30)，电量约92.6%，随后超时。

**首个可定位偏离。** DeepSeek任务完成的直接物理障碍是电量预算不足；就地charge在非充电站失败是后续无效恢复。Qwen的首要事实是导航进展慢且超时，不能归为电量。

**checker失分与完成表述。** DeepSeek16/23，末6点和终点位置未完成，但落地通过；输出明确“PARTIALLY COMPLETED — battery exhausted”，runtime success=True不等于自然语言谎称全成。Qwen0/23且timeout。

**证据级别、竞争解释及日志限制。** A级动作/回执；“整条任务无论如何都不可行”尚未证明，可能存在更短路径或合法充电方案，必须保留竞争解释。

**最小待验证假设。** 先用现有轨迹与电量模型核算任务下界/绕行损耗/充电点可达性，确定是计划低效还是实例本身能源不可行，再决定是否需要行程预算。

原始证据与唯一执行映射：

- **deepseek / generation 1**：job `e38d9490-8753-4ebd-8f8c-3a2e18fa07a7`，toolchain `51227b0b-d8f6-4144-9c60-d34f3bf0f5f7`；[job](./experiments/runs/20261003-143255-full-5c501118/640b66d4/d46027e4.job.json) · [toolchain](./experiments/runs/20261003-143255-full-5c501118/640b66d4/agent-traces/toolchains/20261003T155154454494_640b66d4_51227b0b-d8f6-4144-9c60-d34f3bf0f5f7_completed.json) · [checks](./experiments/runs/20261003-143255-full-5c501118/640b66d4/d46027e4.checks.json) · [before](./experiments/runs/20261003-143255-full-5c501118/640b66d4/d46027e4.before.json) · [after](./experiments/runs/20261003-143255-full-5c501118/640b66d4/d46027e4.after.json) · [agent-state](./experiments/runs/20261003-143255-full-5c501118/640b66d4/d46027e4.agent-state.json)。

- **qwen / generation 1**：job `37ac643a-57c2-4442-beaa-b5cf5999319e`，toolchain `db8acd4f-1f9b-4319-a927-15fec7c14728`；[job](./experiments/runs/20261004-142512-full-aca112a7/640b66d4/d46027e4.job.json) · [toolchain](./experiments/runs/20261004-142512-full-aca112a7/640b66d4/agent-traces/toolchains/20261004T164728192237_640b66d4_db8acd4f-1f9b-4319-a927-15fec7c14728_exception.json) · [checks](./experiments/runs/20261004-142512-full-aca112a7/640b66d4/d46027e4.checks.json) · [before](./experiments/runs/20261004-142512-full-aca112a7/640b66d4/d46027e4.before.json) · [after](./experiments/runs/20261004-142512-full-aca112a7/640b66d4/d46027e4.after.json) · [agent-state](./experiments/runs/20261004-142512-full-aca112a7/640b66d4/d46027e4.agent-state.json)。

## 12. cba90ef3：一次模型返回后零工具退出：日志能证明停止，不能证明内在理由

**用户要求。** Drone4起飞21 m，到(495,543,21)，广播至少5条。

**允许观测与离线事实边界。** Qwen只有/sessions/current管理状态读取，没有list_drones或其他工具返回；无鉴权、余额或provider错误。模型输出106 completion tokens，但现存job/trace用统一失败字符串替换了原自然语言。

**模型方案。** Qwen原始生成文本未保存在本次可用任务产物中，因此不能描述它实际提出的方案或停止理由。不能把零工具直接写成“Planner不会规划”。

**实际工具/API。** 一次模型请求约2.45 s，工具链空，API仅GET /sessions/current；runtime以缺少[TASK DONE]且非空响应退出。DeepSeek同题真实起飞、到点，连续5次broadcast success。

**首个可定位偏离。** 最早能定位的系统行为是将一次非空、无完成标记且无工具的模型返回作为最终终止，没有进一步执行。内部为什么返回该文本不可从这些日志恢复。

**checker失分与完成表述。** Qwen0/2，agent_success=false，未超时；这是诚实未确认完成，不是虚假成功。DeepSeek首次2/2、deployment_pass=true、worker_confirmed=true、provider_errors=[]，为有效成功对照。

**证据级别、竞争解释及日志限制。** A级停止路径；未知级模型动机/推理内容。HTTP200及token计数只能排除显式provider终止，不能证明完整响应语义。

**最小待验证假设。** 先恢复或补齐未来运行的原始非空终止文本记录，再离线设计“继续执行/澄清/拒绝”的分类；尚不确定应否自动续问，不能现在武断重试。

原始证据与唯一执行映射：

- **deepseek / generation 1**：job `e06a9309-5a59-4380-b2ba-4cbb33128a99`，toolchain `db7b8b2e-5aa1-402f-ad4b-8011fc9de5f6`；[job](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/cba90ef3.job.json) · [toolchain](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/agent-traces/toolchains/20261003T202108588339_35ce64d8_db7b8b2e-5aa1-402f-ad4b-8011fc9de5f6_completed.json) · [checks](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/cba90ef3.checks.json) · [before](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/cba90ef3.before.json) · [after](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/cba90ef3.after.json) · [agent-state](./experiments/runs/20261003-143255-full-5c501118/35ce64d8/cba90ef3.agent-state.json)。

- **qwen / generation 1**：job `1eec96ea-e95d-4e1f-85ed-c8f82e88069f`，toolchain `eeee78e0-6657-4542-8dbe-eb9d381f0e9a`；[job](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cba90ef3.job.json) · [toolchain](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/agent-traces/toolchains/20261005T004708271536_35ce64d8_eeee78e0-6657-4542-8dbe-eb9d381f0e9a_failed.json) · [checks](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cba90ef3.checks.json) · [before](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cba90ef3.before.json) · [after](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cba90ef3.after.json) · [agent-state](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cba90ef3.agent-state.json)。

## 冻结实现的核验位置

源码引用均来自2026-10-03首次DeepSeek运行的 `source.zip`（Qwen运行也有独立归档），不是当前工作目录代码：

- `server/models/obstacle.py:305–314`与`server/controllers/obstacle_controller.py:77–96`：主碰撞语义中height=0不可跨越。结合25d30560实际返回和圆内17 m几何距离判断该要求不可达；不泛化到所有辅助几何接口。
- `server/api/server.py:4824–4856,6358–6366`：从历史起止点累计指定角度内的距离，直接`total_distance >= min_distance`，返回值另做两位舍入。用于f817698f的数值边界复核。
- `server/api/server.py:5285–5347`：照片检查使用拍照事件位置到目标中心的距离是否不超过perceived_radius，与路径历史reached不同。用于7af66f50。

完整源码归档链接：[DeepSeek B0 source.zip](./experiments/runs/20261003-143255-full-5c501118/source.zip)；[Qwen B0 source.zip](./experiments/runs/20261004-142512-full-aca112a7/source.zip)。

## 本轮能支持和不能支持的判断

目前可以直接区分：不可满足或不一致的任务规格、checker数值边界、关系语义误读、未发现与不存在混淆、历史事件与当前状态混淆、目标/句柄类型错误、覆盖几何不足、真实能源约束、超时未收尾，以及非空模型终止没有继续执行。它们需要的验证方法不同，不能合并为“缺少完整Harness”。

这12题不证明这些是全量的主要原因，不证明新组件能提高成功率，也不支持“只要所有动作执行完再判分就能解决问题”。下一步应逐一离线复核最小假设及其成功对照，再决定哪些需修代码、哪些需修评测、哪些才值得改变架构。
