# 实验目录导航

当前研究从 [FlockCue](../agent_harness/README.md) 启动。本地回归使用 [v5 配置与开发案例](hover-compact-contract-v2/README_CN.md)。教师示范、学生 SFT 和最终系统比较将使用同一份 600 题清单。既有 `task-splits-v1/` 的 29 道 dev 题继续用于第 0 阶段门槛筛查。[600 题清单](flockcue-benchmark-600-v1/) · [采数和训练规格草案](../docs/specs/02-benchmark600-sft/spec.md) · [第 1 步规格](../docs/specs/01-thinking-events/spec.md) · [研究路线](../docs/RESEARCH_ROADMAP_CN.md)

## 既有实验准备包与冻结输入

- `harness-method-v1/`：方法/公平对照定义、12组控制语义fixture与真实部分路径正负fixture；[协议](../docs/HARNESS_VALIDATION_PROTOCOL_CN.md)和[本地报告](../docs/results/harness-method-validation-20261103/REPORT_CN.md)。72个自然槽仅拟议开发设计，未授权/未运行；本轮不进入Teacher采集。
- `student-tool-ids-v2/plan.json`：固定98prompt/61原样本与v1外部hash，验证同名并行ID表示、标签不变和上下文；[结果](../docs/results/student-tool-ids-v2-20261103/REPORT_CN.md)。不新增Teacher标签或provider调用。
- `agent-samples-v1/source-plan.json`：第四项预先固定10个历史run/22个计划槽，全部为development。转换、学生输入和过滤分母见[报告](../docs/results/agent-samples-student-input-20261103/REPORT_CN.md)，不含新train/test或Teacher采集。
- [task-coverage-v1/](task-coverage-v1/README_CN.md)：18题/9场景已知开发覆盖；`task-coverage-v1-study/`为已核验的snapshot/timeline；`task-coverage-smoke-v1/`为两条执行路径的本地脚本验收配置。
- `task-splits-v1/`：30个新场景、174题，train/dev/test=116/29/29；同模板新实例，test只做预定质量审计。包内源文件及数据不可覆盖，质量动作不是Teacher数据。[报告与复核命令](../docs/results/task-coverage-splits-20261103/REPORT_CN.md)、[暴露登记](../docs/results/task-coverage-splits-20261103/exposure-ledger.jsonl)。
- `harness-natural-agent-plan-v1/`：7 题/14 槽的开发验证协议，未在线执行。
- `harness-provider-preflight-v2/`：已选择 DeepSeek thinking enabled / high；
- `harness-provider-preflight-v1/`：前版预检，保留版本关系，不作为当前模式选择。

## 当前仍依赖的历史输入

- `minimal-harness-full100-v1/`：当前回合/自然验证所需的冻结 fixture 和源码依据；旧队列未恢复执行。
- `minimal-harness-v1/`：部分 fixture 的原始场景来源，预检按哈希核对。
- `execution-memory-mvp-v4/`、`execution-memory-mvp-v5/`：当前记忆回归测试仍使用其中 fixture。

其余 M1/R2/R3、execution-memory、minimal-harness 目录为历史尝试，不自动进入当前任务。旧执行记忆顶层启动与汇总脚本已退役，原实验内封存材料仍可审计；所需原入口见[清理清单](../docs/maintenance/cleanup-20261028.json)。

`runs/` 是被 Git 忽略的大体积历史原始运行数据，`*.local.json` 是本地配置，不提交。当前清理保留其证据内容，未将它们包装为训练数据。新实验使用独立目录与版本，不覆盖原批次、不拼接最优结果。结果导航见[实验依据](../docs/EXPERIMENT_EVIDENCE_CN.md)，新数据准备见[后训练计划](../docs/POSTTRAINING_PLAN_CN.md)。
