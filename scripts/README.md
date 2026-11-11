# 共享运行工具

`reproduction.py` 及同组模块负责仿真服务、场景调度和原版部署流程。`setup_python_envs.sh` 安装 Agent、控制器与仿真服务的独立环境。[环境安装](../docs/DEPENDENCY_SETUP_CN.md) · [部署手册](../docs/guides/reproduction.md)

项目开发从组件目录进入。

| 工作 | 主入口 |
| --- | --- |
| FlockCue 运行时与系统评测 | [flockcue/](../flockcue/README.md) · [evaluation/](../flockcue/evaluation/) |
| 教师轨迹构建和核查 | [posttraining/trajectories/](../posttraining/trajectories/) |
| 学生格式化和训练 | [posttraining/](../posttraining/README.md) |
| 数据集发布与划分工具 | [datasets/](../datasets/README.md) · [处理工具](../datasets/tools/) |

本层有 9 个共享 Python 模块，并为冻结运行器保留 18 个 Python 文件名。早期验证、试采与诊断脚本的兼容链接已移至 [compat/scripts/](../compat/scripts/)。开发时直接修改所属组件中的源文件。同一实验的历史实现通过 Git 提交查询。迁移前的脚本说明可用 `git show project-layout-before-20261111:scripts/README.md` 查看。[迁移记录](../docs/maintenance/project-layout-20261111.md)
