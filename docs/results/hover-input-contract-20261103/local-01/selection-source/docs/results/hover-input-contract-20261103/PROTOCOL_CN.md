# 悬停完成契约与工具输入类型：本地验证协议

日期：2026-11-03。用户要求先明确悬停完成标准，修复完成判定与input_json类型，保留compact输入及隔离、归档、恢复修复。本轮仅本地实现/脚本验证，**真实provider授权0，不进入第五项，不生产Teacher或训练**。此前真实10槽及其独立hover动作协议原样保留；新语义不追改旧分数。

## 语义与范围

源码表明take_off已将无人机置为hovering；hover额外设置状态、耗电并记事件。duration当前只是直接写入历史的参数，不能证明实际持续时长。新显式`hover_completion_v1`只完整匹配限定单机命令，不从复杂命令截取hover条款。

| 类型 | 本轮标准 |
| --- | --- |
| 无时长悬停目标 | 同主体、本回合新鲜公开get_drone_status，完整有限XYZ、空中hovering，speed如提供必须为0；不要求为记一次事件重复hover |
| 起飞到H并原地悬停 | 匹配主体/高度的本回合起飞尝试，之后上述新鲜状态确认H，并保持起飞前公开XY；成功动作或效果未知后查询可支持现态，但不回写历史unknown |
| 明确执行hover命令 | 额外需要正确主体成功hover回执及指令要求的顺序，再核验后续状态；unknownhover不能仅凭状态冒充已确认执行，不自动重放 |
| 指定/模糊悬停时长 | 当前不证明实际持续时间；返回未验证，不以duration回显、瞬时状态或一次同步返回声称完成 |
| 不支持的hover复合指令 | 保留完整原文，标记unsupported，不用部分匹配证明整题完成 |
| 其他任务 | not_applicable，保持未评估；不把悬停判定器扩为通用判题器 |

只读取本回合公开ledger，不调用API/LLM、私有checker或世界状态。主体绑定须唯一；缺字段、错高度/主体、无状态查询、动作后的旧状态、未知顺序/并行、异常观察均不能证明完成。位置公差由实现显式固定。公开状态是离散观测，不宣称连续物理保持或实机安全保证。

## 完成结果与输入版本

新v4本地profile显式启用`completion_contract=hover_completion_v1`与`tool_input_contract=json_object_or_string_v1`，保留`compact_tools_v1 + search_confirm_v9`、evidence_harness及执行状态v1。原profile默认`off/string_v1`，现有应用不自动切换。

完成结果单列`completion_assessment`、三值`task_completed`与`completion_claim_accepted`。`success`继续表示Agent正常返回，不作为整题完成标签。模型的`[TASK DONE]`不能替代证据：拒绝采纳时对外output明确未验证、不保留完成标记；正常返回时的原模型文本保存在model_output和未经改写的原始归档中；有界悬停任务可以诚实返回未验证而不写完成标记。没有自动补动作、隐藏查询或额外模型调用。独立checker仍在退出后隔离执行。

新工具schema明确接受`input_json`为JSON object（优先）或旧JSON-object string；对合法对象仅在工具执行边界编码给既有handler，不猜参数、不补ID、不修改模型历史或call ID。非法数组、标量、非有限数和非JSON值在handler前拒绝；旧内层JSON字符串还拒绝重复键。原生object的重复键可能已被上游解析折叠，本适配层不声称能事后发现，原始wire仍留档。所有实际参数工具含relative与run_harness统一适配，无参工具不动；旧string_v1保持原行为。原工具参数语义、权限与底层执行不改。

## 固定完整回合验收

预定16本地槽：新版主批12、派生显式动作2、派生时长1、旧string_v1对象拒绝对照1。全部子批在首次worker执行前冻结输入、源码、profile、脚本及协议。每槽新worker及退出后独立scorer，禁止外网；

主批覆盖string/object等效、状态已满足无需额外hover、缺查询、错主体、错高度、查询后land失效、raw/skill未知起飞效果后的核验和无重放、run_harness对象、数组/标量/畸形字符串拒绝及修正接续。显式动作覆盖缺动作不采纳与执行后再读状态通过；时长覆盖参数回显仍未验证；旧对照保持对象拒绝。机器清单固定在experiments/hover-input-contract-v1，运行前核对预定数量。

派生动作/时长指令复制原c74开发场景初态，只在新独立study中更改公开command/hash并记录来源；原study、snapshot和原checker不改。原checker只作高度/离地辅助，不证明派生动作或时长。另用公开事件与完成assessment交叉检查新增语义。合成单元测试补充模糊/额外条款、并发、重置、恶意类型和状态失效边界，不与16回合合成自然成功率。

## 通过条件及保留项

- 有效string/object在同初态与脚本意图下的实际动作/回执一致；provider wire和原始assistant历史仍保留实际object/string形式及call ID，完整schema与compact目录一致。
- 缺证据、错误或不支持任务不因完成标记获认证；正常返回、公开完成判断和原checker分列，不静默改旧评分。
- unknown原动作历史保留、无重复起飞；
- 所有计划槽、失败、未运行及基础设施错误完整保留；若实现缺陷导致失败，先保留该尝试，修复后新目录重新冻结，不覆盖原件。
- 不调用真实DeepSeek，不补跑前批、不追加任何线上样本，不宣称已改善自然成功率或论文效果。
