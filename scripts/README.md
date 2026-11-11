# 共享运行工具

`reproduction.py` 及同组模块负责仿真服务、场景调度和原版部署流程。`setup_python_envs.sh` 安装 Agent、控制器与仿真服务的独立环境。[环境安装](../docs/DEPENDENCY_SETUP_CN.md) · [部署手册](../docs/REPRODUCTION_RUNBOOK_CN.md)

项目开发从组件目录进入。

| 工作 | 主入口 |
| --- | --- |
| FlockCue 运行时与系统评测 | [flockcue/](../flockcue/README.md) · [evaluation/](../flockcue/evaluation/) |
| 教师轨迹构建和核查 | [posttraining/trajectories/](../posttraining/trajectories/) |
| 学生格式化和训练 | [posttraining/](../posttraining/README.md) |
| 数据集发布与划分工具 | [datasets/](../datasets/README.md) · [处理工具](../datasets/tools/) |

部分旧命令与封存清单使用 `scripts/` 下的历史文件名。它们的路径通过兼容入口解析到所属组件，新的改动直接写入组件目录。同一实验的历史实现通过 Git 提交查询。迁移前的脚本说明可用 `git show project-layout-before-20261111:scripts/README.md` 查看。[迁移记录](../docs/maintenance/project-layout-20261111.md)
