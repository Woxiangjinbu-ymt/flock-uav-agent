# FlockCue 多无人机协作执行反馈框架

**FlockCue** 是基于执行反馈的多无人机协作智能体框架。Agent 选择工具并指挥机群，FlockCue 记录每架机执行了什么、回执证实了什么、哪些任务要求还待核实。Harness 评测定型后，以可信 DeepSeek 教师轨迹训练约 2B 的指挥模型，用整题 pass rate 衡量 SFT、RL 的效果。[English](README.md) · [运行 FlockCue](agent_harness/README.md)

## 当前工作 · 2026-11-11

按十月原 1,500 题全量实验设置，在冻结的 30 个场景、600 题上正式评测 FlockCue + DeepSeek。原版 Agent4Drone + DeepSeek 的同题历史成绩为 506/600。根据 FlockCue 的实测结果决定模块取舍。[本批规格](docs/specs/05-deepseek-harness-ab600/spec.md) · [冻结题单与历史成绩](experiments/flockcue-benchmark-600-v1/README_CN.md)

| 要了解什么 | 从哪里开始 |
| --- | --- |
| 研究问题与阶段 | 先评测并定型 Harness，再训练、评测学生。[研究路线](docs/RESEARCH_ROADMAP_CN.md) |
| 学生学什么、怎样对照 | 核验教师轨迹，在同一 Harness 上比较原始、SFT 和 RL 学生的整题成绩。[后训练方案](docs/POSTTRAINING_PLAN_CN.md) |
| 哪些已经跑通 | 本地 v5 基线可重放；4 个封存真实批次的教师推理用量已复算。学生权重尚未产生。[研究状态](docs/RESEARCH_STATUS_CN.md) · [实验依据](docs/EXPERIMENT_EVIDENCE_CN.md) |
| 论文、大论文与展示 | 与 RACE、PenguinHarness 的差异和贡献标准。[项目指南](docs/PROJECT_GUIDE_CN.md) |

## 仓库结构

```text
agent_harness/ FlockCue 研究入口：版本锁、check 与 smoke
agent4drone/   无人机 Agent、Harness 运行时、回执解析、模型 I/O 归档
server/        仿真服务、公开 API 与隔离检查器
controller/    场景管理与执行控制 GUI
view3d/        三维可视化
benchmark/     上游任务与场景资源
scripts/       运行器、批次入口、验证与审计脚本（见 scripts/README.md）
training/      固定 tokenizer 与输入检查
experiments/   冻结的数据划分、配置、fixture 与历史实验（见 experiments/README.md）
docs/          现行文档、规格、结果原件与历史材料（见 docs/README.md）
tmp/           开发临时目录；只保留划分审计所需的 task-splits-pilot-01
tutorial/      上游平台操作教程
```

## 开始使用

按[依赖安装](docs/DEPENDENCY_SETUP_CN.md)准备各组件的独立环境，平台启动见[运行入口](docs/REPRODUCTION_RUNBOOK_CN.md)，操作见[中文教程](tutorial/tutorial_zh.md)。模型凭据放在被忽略的本地配置中，不提交。

在仓库根目录核验研究基线：

```bash
agent4drone/.venv/bin/python -m agent_harness check
```

## 上游与历史材料

仿真、Benchmark、Agent4Drone 底座及可视化来自上游 MultiUAV-Plat，本地增量与证据单独记录。[上游说明与引用](docs/upstream/README_CN.md) · [许可证](LICENSE)

2026-11-08 的整理把开发临时输出移出仓库，把被取代的验证器草案和预算移入 `docs/history/`。清单与恢复位置见[清理记录](docs/maintenance/cleanup-20261108.json)，更早的一次见[10-28 清理记录](docs/maintenance/cleanup-20261028.json)。
