# FlockCue 多无人机协作执行反馈框架

FlockCue 的主 Agent 根据任务指令和机队回执选择下一步工具调用。研究目标是在同一仿真环境中，用真实 DeepSeek 执行轨迹训练约 2B 的指挥模型，并用整题通过率比较基座、SFT 与 GRPO。[English](docs/en/README.md) · [研究路线](docs/RESEARCH_ROADMAP_CN.md)

## 从哪里开始

| 我想做什么 | 位置 |
| --- | --- |
| 改 FlockCue Agent 的执行流程、记忆、证据或工具 | [flockcue/](flockcue/README.md) |
| 查看 Agent4Drone 服务和上游组件 | [agent4drone/](agent4drone/README.md) |
| 运行正式系统评测或复核结果 | [flockcue/evaluation/](flockcue/evaluation/) |
| 根据实发请求、工具调用与回执重建教师轨迹 | [posttraining/trajectories/](posttraining/trajectories/) |
| 制作学生 SFT 样本、检查模板与监督边界 | [posttraining/sft/](posttraining/sft/) |
| 开展在线 RL 或 GRPO | [posttraining/rl/](posttraining/rl/) |
| 查原始题库、冻结 600 题、教师轨迹及 SFT 数据 | [datasets/](datasets/README.md) |
| 查实验配置、结果和研究决策 | [experiments/](experiments/README.md) · [docs/](docs/README.md) |

仿真 API 与评分器位于 [server/](server/README.md)，场景管理与执行控制位于 [controller/](controller/README.md)，三维界面位于 [view3d/](view3d/README.md)。共享的部署工具和环境安装在 [scripts/](scripts/README.md)。

## 本地复核

按[依赖安装](docs/DEPENDENCY_SETUP_CN.md)准备各组件环境。从仓库根目录执行以下离线检查。

```bash
agent4drone/.venv/bin/python -m flockcue check
python3 datasets/flockcue-600/verify.py --historical-csv
```

首遍 FlockCue + DeepSeek 通过 544/600。同日、相同服务指纹的 200 题对照中，FlockCue 通过 182 题，原版通过 168 题。目前已从 A 折筛出 110 道训练题的 386 个教师决策轮，学生 SFT 和 GRPO 尚未启动。[正式评测](docs/results/flockcue-deployment600-20261111/REPORT_CN.md) · [同日对照](docs/results/agent4drone-matched200-20261111/REPORT_CN.md) · [教师轨迹构建报告](docs/results/flockcue-teacher-trajectories-a-20261111/REPORT_CN.md)

## 代码与数据的版本

在所属目录的稳定文件名上修改代码，以 Git 提交或标签标识实现版本。迁移前的源码保存在标签 `project-layout-before-20261111`。要追溯单个文件可使用 `git log --follow -- <路径>`。已发布的实验 ID、数据协议和校验清单沿用原名，旧运行器所用的兼容路径见[目录迁移说明](docs/maintenance/project-layout-20261111.md)。

Git 保存源码、依赖锁、冻结题单和已审阅的 386 轮数据包。完整教师公开轮和原始请求、仿真快照留存在本地，按发布清单校验。平台、数据集、Agent4Drone 与可视化组件的来源见[上游说明](docs/upstream/README_CN.md)和[许可证](LICENSE)。
