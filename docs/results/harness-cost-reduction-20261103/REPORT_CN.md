# 搜索成本与输入冗余收敛

日期：2026-11-03。**本轮保留 `compact_tools_v1 + search_confirm_v9` 作为下一轮真实对照的本地候选。** 输入去重已实现；搜索只采纳接近完成时及时核验的优化，没有缩短探索预算。停滞提前交回的 v8 因部分进度退化而不推荐。隔离、归档、未知效果及部分完成恢复均通过回归和完整本地回合验收。

没有新增 provider 调用、Teacher 数据或训练，未进入第五项。[前批真实结果](../deepseek-contract-pilot-20261103/REPORT_CN.md)仍原样保留。

## 具体改动

| 变更 | 实现与约束 |
| --- | --- |
| 输入布局 `compact_tools_v1` | system 的 AVAILABLE TOOLS 目录只列名称，完整描述仍在原生 tools schema 中。操作/安全规则、参数 schema、原任务、全部历史回执、reasoning、工具关联、证据附注及恢复投影保留 |
| 搜索 `search_confirm_v9` | 继承 v7 全部探索与恢复规则；仅缓存的有效公开进度≥95%，且成功移动回执出现新的可信 reach 名称时，及时读取一次同会话公开进度。严格>95%才完成，与原周期读取重合时不重复调用 |
| 显式配置 | [本地候选 profile](../../../experiments/harness-cost-v1/recommended-profile.json)为 schema v3，选择 compact/v9；仍是脚本模型、在线授权0。默认输入仍full，原应用/旧profile不自动切换 |

`input_json` 原本要求字符串，前批模型传对象的两次错误没有被暗中纠正，也没有据此扩宽工具 schema。本轮先减少可精确识别的重复描述，保留错误原貌。提示布局变化仍可能改变模型使用工具的行为，需真实同期对照再测。

## 输入体积：同一历史的反事实与实际本地接线

对前批全部 **56 个实际已派发请求**逐条核对原生工具描述，排除2个预算拒绝请求，再仅替换重复目录。其余请求字段、非system消息、规则与恢复投影逐值相同；原始运行只读。详见[输入审计](input-audit/REPORT_CN.md)及[机器结果](input-audit/audit.json)。

| 口径 | 修改前 | 修改后 | 变化 |
| --- | ---: | ---: | ---: |
| 56条历史请求，同规范JSON序列化字节 | 3,211,644 | 2,457,582 | **−23.5%** |
| 其中原工具基线请求字节 | 1,528,243 | 1,153,573 | −24.5% |
| 其中整合臂请求字节 | 1,683,401 | 1,304,009 | −22.5% |
| 最终14槽本地完整链，每臂40次Mock请求的实际wire字节 | 1,987,722 | 1,427,985 | **−28.2%** |

56条历史是固定旧模型输出下的反事实；14槽是固定脚本决策下的实际新SDK请求，并且run_harness描述包含v9规则，因此两者不是同一分母。前批搜索I有84.5%的token花在子程序退出后的7次原工具交互，不能仅靠子程序动作减少就宣称这部分token已解决。

## 搜索：保留负结果后收缩改动

第一候选 v8 在保留原16轮/2,000 API/16 repair上限的同时，尝试在正进度后连续两次无进展时提前交回，并在新reach后立即确认进度。固定16槽实测暴露两个问题：

- `7eba7390` 动作85→35，但进度 **66%→33%**，丢失了旧版后续能完成的目标。两臂checker都失败不代表没有退化。
- `24ac1838` 动作167不变、进度51%不变，API **660→701**，多41次确认没有完成收益。

因此v8不作为推荐版本。[负结果审计](search-progress-audit.json)、[原16槽](search-comparison-01/validation.json)及[原14槽合同验收](contract-run-01/validation.json)全部保留。合同验收通过说明实现按规则运行，不能否定策略覆盖退化。

随后在新[跟进协议](FOLLOWUP_PROTOCOL_CN.md)中只允许一个收缩候选v9：取消停滞交回，95%之前不额外确认新reach。重新冻结相同原7题＋`fdd21809`，full输入、相同脚本/初态/预算，另跑16槽。结果见[独立评分与封存](search-confirm-01/validation.json)、[逐题动作及进度审计](search-confirm-audit.json)。

| 搜索任务 | v7→v9最终公开进度 | v7→v9动作尝试 | v7→v9公开API | 原checker |
| --- | --- | ---: | ---: | --- |
| `7eba7390` | 66%→66% | 85→85 | 349→349 | 均失败 |
| `24ac1838` | 51%→51% | 167→167 | 660→660 | 均失败 |
| `b1ad4f3e` | 100%→100% | **125→116** | **502→466** | 均成功 |
| `fdd21809` | 66%→66% | 85→85 | 349→349 | 均失败 |

另外两条短链与两条语法负例不变。全8题每臂均3/8通过（1搜索＋2短链；另含故意的语法负例），总动作468→459、API1,896→1,860。唯一变化来自成功搜索：最近公开进度95%后出现新reach，及时确认100%，省去已达到停止条件后的9个动作、净省36次API。其余7题的动作/参数/回执状态序列逐项相同。这里是开发场景内的局部节省，**三个困难搜索仍未解决**，不是新的自然模型成功率或广泛搜索效率收益。

## 隔离、归档与恢复保留情况

最终组合 compact/v9 与 full/v7 用相同公开脚本完成 **7条件×2配置=14槽**，14个新worker及14个独立scorer，无网络调用、无未运行槽。条件为短链、导航、搜索、raw未知效果、子程序未知效果、partial恢复正例及省略恢复负例。[完整验收](contract-confirm-01/validation.json)185项主检查及两臂各28项partial检查通过。

- 两臂各100个动作、430次公开API；这7个条件中的动作/执行成本相同。搜索 `fdd21809` 均66%、80个move、原checker失败，没有启用停滞交回。
- unknown原工具与子程序路径均真实起飞一次后丢回执，后续脚本新决策查询状态并悬停，无重放；ledger仍保留历史unknown，不根据查询追溯改成成功。这是本地脚本恢复，不新增自然恢复样本。
- partial正例从已到达A继续到剩余B，未重放A，原checker4/4；省略恢复负例正常返回但仅2/4。评分仍与正常返回分开。
- 实际wire保留原消息、回执与工具关联；两版本的参数schema一致，只有run_harness描述因搜索版本不同而追加对应说明。
UAV Agent既有方法中只改构造参数和prompt构建，其他方法AST相同。方法级比较不能替代测试，因此同时保留上述实际链路验收。

两轮合计 **60个本地回合**（每轮16搜索对照＋14合同验收），分别保留分母，不合成模型成功率。最终相关回归 **1,280项通过**：运行/搜索/输入/恢复等1,226项＋隔离SDK适配54项；[最终回归](regression-runtime.log)、[SDK回归](regression-adapter.log)、[前候选回归](regression-runtime-v8.log)均保留。

## 使用与当前结论

推荐用于下一轮受控对照的配置是[compact/v9本地profile](../../../experiments/harness-cost-v1/recommended-profile.json)，不是已证明优于原工具的论文效果版本。实现位置为[输入模板](../../../agent4drone/template/system_prompt.py)、[Agent接线](../../../agent4drone/uav_agent.py)、[搜索子程序](../../../agent4drone/harness_skills.py)及[配置校验](../../../agent4drone/agent_profile.py)。复现命令见[脚本导航](../../../scripts/README.md)；以下仅为本地固定验收命令形式，输出必须是新目录：

```bash
agent4drone/.venv/bin/python scripts/validate_harness_cost_reduction.py --candidate-revision search_confirm_v9 --output /新的绝对路径
agent4drone/.venv/bin/python scripts/validate_search_progress.py prepare --candidate search_confirm_v9 --output /另一个新的绝对路径
agent4drone/.venv/bin/python scripts/validate_search_progress.py run --output /另一个新的绝对路径
```

本轮完成的是**输入可证重复部分的精简、一个无探索提前退出的局部搜索优化，以及正确性回归**。没有减少全部困难搜索的动作成本，也没有实测真实token节省。下一步若继续真实模型验证，应以compact/v9与明确基线重新冻结同期协议，重点测输入变短后的工具错误率、真实token和成功率；不能沿用前批额度自动扩量。第五项仍未启动。
