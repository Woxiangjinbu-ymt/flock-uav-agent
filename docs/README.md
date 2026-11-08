# FlockCue 文档导航

截至 2026-11-08。第一篇论文研究多无人机小模型 Agent 的选择性思考，当前在第 0 阶段。第 1 步规格、技术方案及预算上限已确认。G3 已通过，G1、G2 待测。[G3 复算报告](results/thinking-events-g3-20261108/REPORT_CN.md)

## 目录分工

| 目录 | 放什么 | 规则 |
| --- | --- | --- |
| `docs/*.md` | 现行文档：状态、路线、方案、指南、运行手册、证据索引，以及仍被代码按路径读取的冻结协议 | 现行文档随决策更新。冻结协议不改名不移动（见下表）。 |
| `docs/specs/` | 各步骤的 spec / plan / task / checklist | 每步一个目录，编号与路线一致。 |
| `docs/results/` | 各批次实验的原件、封存与报告 | 只读。改动会使封存校验失败。 |
| `docs/audit/` | 只读审计与调研，每项一个带日期的目录 | 根目录下 10 月初的零散文件被历史脚本按路径引用，保留原位。 |
| `docs/history/` | 被取代的规格、预算、研究笔记和旧报告 | 只查阅，不作为当前计划。 |
| `docs/maintenance/` | 清理与维护清单 | 每次整理新增一份，不改旧清单。 |
| `docs/upstream/`、`docs/related-paper/`、`docs/assets/` | 上游说明、论文 PDF、报告配图 | `related-paper/` 中的 PDF 被封存哈希引用，不改名。 |

## 现行文档

| 要查的问题 | 文档 |
| --- | --- |
| 接下来做什么，门槛是什么？ | [研究路线](RESEARCH_ROADMAP_CN.md) |
| 什么已经验证，什么还是假设？ | [研究状态](RESEARCH_STATUS_CN.md) |
| 学生学什么，怎样对照，要多少资源？ | [后训练方案](POSTTRAINING_PLAN_CN.md) |
| 哪 600 道题用于构建 SFT 数据和最终系统比较？ | [冻结题目清单](../experiments/flockcue-benchmark-600-v1/) · [采数、训练与评测规格草案](specs/02-benchmark600-sft/spec.md) |
| 第 1 步的执行事件与思考标签如何定义、如何验证？ | [规格](specs/01-thinking-events/spec.md) · [技术方案](specs/01-thinking-events/plan.md) · [任务清单](specs/01-thinking-events/task.md) · [验收清单](specs/01-thinking-events/checklist.md) |
| 与已有工作的差异、贡献标准、展示与归属 | [项目指南](PROJECT_GUIDE_CN.md) |
| FlockCue 如何启动与复核 | [Harness 入口](../agent_harness/README.md) · [运行手册](REPRODUCTION_RUNBOOK_CN.md) · [依赖安装](DEPENDENCY_SETUP_CN.md) |
| 各批实验的结果和边界 | [实验依据](EXPERIMENT_EVIDENCE_CN.md) |
| 脚本和实验目录 | [脚本导航](../scripts/README.md) · [实验目录](../experiments/README.md) |

## 冻结协议（代码按路径读取或封存哈希，不改名不移动）

| 文档 | 读取方 |
| --- | --- |
| [通用 Agent 范围 v1](GENERAL_AGENT_SCOPE_CN.md)、[执行契约](AGENT_EXECUTION_CONTRACT_CN.md) | `scripts/general_agent.py` prepare 时写入每个 run 的 `PROTOCOL_CN.md` |
| [任务覆盖与划分](TASK_COVERAGE_AND_SPLITS_CN.md) | `scripts/build_development_coverage.py` 与划分 manifest |
| [旧样本契约](AGENT_SAMPLE_CONTRACT_CN.md) | `scripts/convert_agent_samples.py`；新样本另建版本 |
| [工具 ID 适配](STUDENT_TOOL_ID_ADAPTER_CN.md) | `scripts/validate_student_tool_ids.py` |
| [Harness 方法](HARNESS_METHOD_CN.md)、[方法验证协议](HARNESS_VALIDATION_PROTOCOL_CN.md) | `scripts/freeze_harness_validation.py` |
| [自然 Agent 验证协议](HARNESS_NATURAL_AGENT_VALIDATION_CN.md) | `scripts/prepare_harness_natural_agent.py` |
| [执行记忆 v7](EXECUTION_MEMORY_V7_CN.md)、[旧投稿路线入口](CSCWD2027_INCREMENTAL_RESEARCH_ROADMAP_CN.md) | 封存报告与交付校验按原路径链接 |

## 历史材料

- [验证器草案与预算（2026-11-07）](history/verifier-plan-20261107/)：原第 1 步判断契约与 300 条样本预算，第二篇论文再启用。研究依据见[验证器方案调研](audit/trajectory-verifier-plan-20261107/REPORT_CN.md)。
- [B1/M1 研究笔记（2026-10-06）](history/agent4drone-notes-20261006/)：原放在 `agent4drone/docs/`。
- 11 月 1 日的[合并报告](RESEARCH_COMBINED_REPORT_CN.html)、[Harness 阶段报告](HARNESS_STAGE_REPORT_CN.html)、[后训练阶段报告](POSTTRAINING_STAGE_REPORT_CN.html)和 10 月 17 日的[后训练调研](SMALL_MODEL_POSTTRAINING_SURVEY_CN.html)保留当时的方案与用词，留在原位：后训练调研被封存完整性校验引用，三份阶段报告互相链接，且 `assets/posttraining-stage-20261101/build_route.py` 按固定路径写回报告。
- 上游论文：[精读](MULTIUAV_PLAT_READING_CN.md) · [中文翻译](MULTIUAV_PLAT_FULL_TRANSLATION_CN.md)。
- 清理记录：[2026-11-08](maintenance/cleanup-20261108.json) · [2026-10-28](maintenance/cleanup-20261028.json)。
