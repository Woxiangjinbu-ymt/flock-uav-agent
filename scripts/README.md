# 脚本导航

**FlockCue 当前研究入口**位于 [agent_harness/](../agent_harness/README.md)。从仓库根目录运行 `agent4drone/.venv/bin/python -m agent_harness check` 核对本地 v5 基线。用 `smoke --output experiments/runs/flockcue-smoke-01` 在新目录中重跑两个悬停正反例，完整命令见[复现手册](../docs/REPRODUCTION_RUNBOOK_CN.md)。

**第一篇论文（选择性思考）**使用 [thinking_events](thinking_events/) 从封存轨迹复算 G3。教师推理用量可用 [probe.py](../docs/audit/teacher-reasoning-usage-20261108/probe.py) 复算。第 1 步规格、技术方案及预算上限已确认。G3 已通过，G1、G2 待测。[G3 复算报告](../docs/results/thinking-events-g3-20261108/REPORT_CN.md) · [规格](../docs/specs/01-thinking-events/spec.md) · [技术方案](../docs/specs/01-thinking-events/plan.md) · [任务清单](../docs/specs/01-thinking-events/task.md) · [验收清单](../docs/specs/01-thinking-events/checklist.md)

学生接入和迁移使用固定 Python 3.11.16。在仓库根目录用 `agent4drone/.venv/bin/python scripts/package_thinking_student.py --study <已物化的完整-dev-目录> --output <新文件.tar.gz>` 生成包含冻结源码、初态、校验清单和无卡脚本的交付包。接收方按包内 `LINUX_CPU_CN.md` 建环境并运行验收。[Linux 无卡步骤](../docs/results/thinking-student-cpu-20261108/LINUX_CPU_CN.md)

11-07 验证器方案的开发前缀可用 [extract_examples.py](../docs/audit/trajectory-verifier-plan-20261107/extract_examples.py) 与 [audit_queries.py](../docs/audit/trajectory-verifier-plan-20261107/audit_queries.py) 复算。下表的旧脚本多数被封存批次的 `verify` 或运行器导入，名称与位置保持不变。

## 底层组件与历史实验脚本

下表按脚本最初适用的版本列出。各批次的 `prepare`、`run`、`verify` 参数以所链接的协议为准。

| 用途 | 入口 |
| --- | --- |
| 安装组件环境 | `setup_python_envs.sh` |
| Harness方法与本地验证 | `validate_harness_method.py`对照真实runtime与guarded macro；`validate_harness_partial_recovery.py`验收真实部分路径与剩余恢复；`freeze_harness_validation.py prepare/verify`封存方法/对照清单及代码数据指纹。见[协议](../docs/HARNESS_VALIDATION_PROTOCOL_CN.md)，均不发送provider请求 |
| 通用 Agent v1 本地维护入口 | `general_agent.py prepare/run/verify`；`audit_general_agent.py` 检验固定8案例；范围、配置及复现见[规格](../docs/GENERAL_AGENT_SCOPE_CN.md) |
| DeepSeek有限自然行为pilot | `deepseek_behavior_pilot.py`、`harness_live_cost.py`、`analyze_deepseek_behavior_pilot.py`；本批已按协议停线，后续不自动重跑，见[报告](../docs/results/deepseek-behavior-pilot-20261102/REPORT_CN.md) |
| DeepSeek新版执行契约小批 | `deepseek_contract_pilot.py prepare/local-smoke/run/verify`；`analyze_deepseek_contract_pilot.py`只读分析。固定8槽的授权范围与停止规则见[协议](../docs/results/deepseek-contract-pilot-20261103/PROTOCOL_CN.md)，用法见下文 |
| DeepSeek compact/v9成本同期对照 | `deepseek_cost_pilot.py prepare/local-smoke/run/verify`；`analyze_deepseek_cost_pilot.py`只读分析。固定10槽，旧整合full/v7与新整合compact/v9，详见[协议](../docs/results/deepseek-cost-pilot-20261103/PROTOCOL_CN.md)。该批已封存 |
| 统一状态与执行契约v2 | `general_agent.py`使用显式v2本地profile，`episode_termination.py`分开回合/批次/归档/评分，`audit_agent_workflow.py`只读独立验收；见[报告](../docs/results/agent-execution-contract-20261102/REPORT_CN.md) |
| 搜索与输入成本候选（显式启用） | `validate_harness_cost_reduction.py`验收14槽真实ASGI脚本对照；`validate_search_progress.py`单列16槽搜索防回归；`audit_harness_input_layout.py`只读历史wire字节审计。见[报告](../docs/results/harness-cost-reduction-20261103/REPORT_CN.md)及下文用法，均不调用provider |
| 同名并行学生输入v2 | `student_input_v2.py`统一历史canonical ID与推理解析/追加，`student_parallel_corpus.py`只读固定98prompt/61样本；`validate_student_tool_ids.py`比较v1/v2。见[协议](../docs/STUDENT_TOOL_ID_ADAPTER_CN.md)和[验收](../docs/results/student-tool-ids-v2-20261103/REPORT_CN.md)，未执行真实学生推理 |
| 历史主 Agent 动作样本转换与学生输入 | `convert_agent_samples.py`按真实wire与独立评分生成development决策样本，`audit_agent_samples.py`独立审计；`student_input.py`使用固定Qwen tokenizer编码，`student_sample_loader.py`核验loss/padding，`audit_student_context.py`保留失败轨迹的prompt诊断。见[报告](../docs/results/agent-samples-student-input-20261103/REPORT_CN.md)，不启动训练或provider |
| 开发覆盖与独立划分 | `build_development_coverage.py`固定旧题开发清单；`task_split_generator.py`生成新场景；`audit_task_splits.py`只读审计；`prepare_task_dataset.py`物化获准split初态；`validate_task_dataset.py`仅作公开API脚本质量验收。见[协议](../docs/TASK_COVERAGE_AND_SPLITS_CN.md)及[报告](../docs/results/task-coverage-splits-20261103/REPORT_CN.md)，不调用provider、不生产Teacher |
| 自然 Agent 协议准备/校验 | `prepare_harness_natural_agent.py` |
| Provider 候选配置预检 | `harness_provider_preflight.py`，仅 prepare/verify |
| — | `harness_deepseek_adapter.py`、`harness_cost_admission.py`。后续固定真实小批的运行与结算见[执行契约报告](../docs/results/deepseek-contract-pilot-20261103/REPORT_CN.md) |
| 回合与自然入口 | `harness_episode_local.py`、`harness_natural_runner.py`、`harness_natural_local.py`、`harness_request_limits.py` |
| 独立本地验收 | `validate_harness_episode.py`、`validate_harness_natural_runner.py`、`audit_harness_natural_runner.py` |
| Harness 机制回归 | `validate_harness_entry_contract.py` 至 `validate_harness_observed_target.py` 各独立批次入口 |
| 历史部署基线 | `reproduction.py` 与 `reproduction_*` |

## 2026-11-03 搜索与输入成本对照的本地用法

重现当时的本地候选需显式选择 [recommended-profile.json](../experiments/harness-cost-v1/recommended-profile.json)：schema v3 使用 `compact_tools_v1` 输入布局和 `search_confirm_v9` 搜索版本。现有通用 Agent profile 的 `full_v1 / observed_target_v7` 路径不会自动切换。输入变更只缩短 system 中重复的工具目录，native工具schema保留完整描述；v9保留v7探索，只在最近公开进度已≥95%且出现新可信reach时再次公开核验，严格>95%才完成。原始任务、工具回执与历史、执行状态及 unknown/partial 恢复链保留。

旧 [candidate-profile.json](../experiments/harness-cost-v1/candidate-profile.json) 仍是 `search_progress_v8`，仅保留作复现：其停滞交回在已知搜索题把进度从66%降到33%，**不推荐使用**。原批次和负结果保留；v9的收缩依据见[后续协议](../docs/results/harness-cost-reduction-20261103/FOLLOWUP_PROTOCOL_CN.md)。

下面通过通用入口运行预定的5个主要本地案例。目录必须尚不存在；`prepare` 冻结profile、输入与源码，`run` 执行一次，`verify`只读检查封存。模型响应来自公开脚本，环境使用真实ASGI和独立worker/scorer，没有provider调用。

```bash
candidate_run="tmp/harness-cost-candidate-example"
agent4drone/.venv/bin/python scripts/general_agent.py prepare \
  --profile experiments/harness-cost-v1/recommended-profile.json \
  --study experiments/task-coverage-v1-study \
  --cases experiments/harness-cost-v1/main-cases.json \
  --output "$candidate_run"
agent4drone/.venv/bin/python scripts/general_agent.py run --run "$candidate_run"
agent4drone/.venv/bin/python scripts/general_agent.py verify --run "$candidate_run"
```

完整验收入口另将旧配置与候选配置配对，固定7条件×2配置=14槽，包含短链、导航、搜索、原工具/技能回执丢失，以及partial恢复正反例。为兼容旧批，脚本默认revision仍为v8，执行v9必须显式指定。以下第一条会准备并执行新本地批；第二条根据包内冻结版本只读复核，不重跑仿真或修改原件。

```bash
agent4drone/.venv/bin/python scripts/validate_harness_cost_reduction.py --candidate-revision search_confirm_v9 --output tmp/harness-cost-validation-example
agent4drone/.venv/bin/python scripts/validate_harness_cost_reduction.py --verify --output tmp/harness-cost-validation-example
```

历史输入审计只读11-03真实小批的已派发wire，反事实替换可核实的重复目录，并检查其余请求内容保持不变。输出必须是原run之外的新目录；不会再次调用模型，也不输出修改后的请求供执行。

```bash
agent4drone/.venv/bin/python scripts/audit_harness_input_layout.py \
  --run docs/results/deepseek-contract-pilot-20261103/run-01 \
  --output tmp/harness-input-audit-example
```

这些入口测量本地执行行为和请求字节。搜索交回后的自然模型行为仍需另行验证。该历史候选的包和数据仅用于其原批次验证。完整结果与限制见[本轮报告](../docs/results/harness-cost-reduction-20261103/REPORT_CN.md)。

## 2026-11-03 真实 DeepSeek 执行契约小批

`deepseek_contract_pilot.py` 使用短链、导航、搜索和受控起飞回执丢失四个条件，各含原工具臂与整合臂。这 8 个回合已经封存。该脚本的在线 `run` 只适用于其预定历史条件。[该批报告](../docs/results/deepseek-contract-pilot-20261103/REPORT_CN.md)

```bash
agent4drone/.venv/bin/python scripts/deepseek_contract_pilot.py verify \
  --run docs/results/deepseek-contract-pilot-20261103/run-01
```

旧版本名如 `harness_full100_*`、`harness_v2_*` 仍可能被当前运行器导入或被源码封存引用。2026-10-28 已移除的一次性执行记忆试点入口记录于[清理清单](../docs/maintenance/cleanup-20261028.json)。

## compact/v9 固定10槽真实对照

`deepseek_cost_pilot.py` 用于已封存的 2026-11-03 限定对照，两臂均为整合版：control=`full_v1 + observed_target_v7`，candidate=`compact_tools_v1 + search_confirm_v9`。5条件为短链、导航、困难搜索、受控起飞回执丢失和已知可完成搜索；10槽顺序与源码/输入在prepare时冻结。每槽12 HTTP、200,000 usage token响应后停线、8,192输出、500s；整批保守USD2.00、每槽USD0.50，不继承旧队列或额度，不自动扩量。

只有 `local-smoke` 不调用真实provider；已执行目录拒绝覆盖和自动续跑。只读核验本批已有运行：

```bash
agent4drone/.venv/bin/python scripts/deepseek_cost_pilot.py verify \
  --run docs/results/deepseek-cost-pilot-20261103/run-01
agent4drone/.venv/bin/python scripts/analyze_deepseek_cost_pilot.py \
  --run docs/results/deepseek-cost-pilot-20261103/run-01 \
  --output /新的绝对路径/独立只读分析目录
```

原始reasoning不输出到分析报告；失败、未运行和无恢复机会分开。与前批原工具基线的结果不能跨批拼接为同期对照。

## 悬停完成与 input_json 本地验收

`validate_hover_input_contract.py` 固定16槽，先准备全部四组再启动独立 worker/scorer；模型是脚本 MockTransport，网络禁止，provider授权0。新profile启用 hover_completion_v1 / json_object_or_string_v1 并保留 compact/v9；旧对照维持 off/string_v1。原始未知效果、object/string历史、call ID及失败尝试完整保留。复核最终封存：

```bash
agent4drone/.venv/bin/python scripts/validate_hover_input_contract.py verify \
  --output docs/results/hover-input-contract-20261103/local-03
```

新建本地复现依次执行 `prepare --output 新目录`、`run --output 新目录` 和 `verify --output 新目录`，不覆盖或续跑旧批。`verify`只读复算，负例预期是否满足与任务完成分开；初次local-02应返回验收失败，不能改其原件使之通过。状态目标、明确动作与持续时长的标准及完整分母见[报告](../docs/results/hover-input-contract-20261103/REPORT_CN.md)。该验收使用本地脚本模型。

## v4 悬停与输入契约同期 DeepSeek 小批

`deepseek_hover_pilot.py` 是独立限定8槽入口：两臂同compact/v9，control=off/string_v1、candidate=hover_completion_v1/json_object_or_string_v1；状态、明确动作、回执丢失和时长诊断各一对。统一独立真值与两臂共同公开assessment分列，原checker仅辅助；时长不进支持任务完成率。新派生指令保留初态与原checker。

本轮已执行并结束，保守USD1.50整批/0.50每槽授权不顺延。`prepare`不调用provider；仅 `local-smoke --output 新目录` 是网络阻断的合成回合。只读复核已有真实结果：

```bash
agent4drone/.venv/bin/python scripts/deepseek_hover_pilot.py verify \
  --run docs/results/deepseek-hover-pilot-20261103/run-01
agent4drone/.venv/bin/python scripts/analyze_deepseek_hover_pilot.py \
  --run docs/results/deepseek-hover-pilot-20261103/run-01 \
  --output /新的绝对路径/只读分析目录
```

分析器不调用SDK/模拟器/pickle，按原始响应位置统计input_json表示与错误，恢复要求见到unknown后新模型状态查询、历史保留和所有来源无起飞重放；8/6/2分组与同样成功子集分别报告token。当前实际结果及限制见[报告](../docs/results/deepseek-hover-pilot-20261103/REPORT_CN.md)。

## v5 输入说明压缩与只读并行本地验收

`validate_hover_compact_contract.py` 固定20槽，使用显式 schema5 本地 profile、真实 Agent/SDK MockTransport/ASGI，使用本地脚本回执。`parallel_tools` 与一次性 `local_api_barrier` 仅为本地脚本夹具，用于产生真实重叠，不能当作模型自主行为。旧串行脚本行为不变。

```sh
agent4drone/.venv/bin/python scripts/validate_hover_compact_contract.py prepare --output /tmp/hover-compact-local-new
agent4drone/.venv/bin/python scripts/validate_hover_compact_contract.py run --output /tmp/hover-compact-local-new
agent4drone/.venv/bin/python scripts/validate_hover_compact_contract.py verify --output /tmp/hover-compact-local-new
```

输出路径必须未存在；`run` 不续跑/覆盖。已完成原件为 `docs/results/hover-compact-contract-20261103/local-01`。`audit_hover_compact_contract.py --run docs/results/deepseek-hover-pilot-20261103/run-01 --output /tmp/hover-compact-audit-new.json` 只读检查旧26个N请求、27个C参考与8个ledger，不运行模型/动作，不改历史分数；

`audit_hover_compact_parallel.py` 另做并行调用ID→ledger→原始HTTP→API的独立严格关联，用于补充固定验收器的唯一映射检查。可运行 `agent4drone/.venv/bin/python scripts/audit_hover_compact_parallel.py --campaign docs/results/hover-compact-contract-20261103/local-01 --output /tmp/hover-compact-parallel-new.json`；输出必须是新文件。结果、限制与复现见[本轮报告](../docs/results/hover-compact-contract-20261103/REPORT_CN.md)。
