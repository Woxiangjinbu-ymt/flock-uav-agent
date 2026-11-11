# FlockCue Agent Harness

FlockCue 通过无人机的观测和执行回执维护任务进度，供主 Agent 继续执行、恢复或重规划。真实 DeepSeek 的正式首遍在冻结的 600 题上通过 544 题。相同教师服务指纹的 200 题同期对照中，FlockCue 通过 182 题，原版 Agent4Drone 通过 168 题。[600 题报告](../docs/results/flockcue-deployment600-20261111/REPORT_CN.md) · [200 题配对结果](../docs/results/agent4drone-matched200-20261111/REPORT_CN.md)

| 目录 | 内容 |
| --- | --- |
| [runtime](runtime/) | Agent 配置、工具输入契约、子程序、执行状态、悬停完成判定和模型 I/O 归档。[调度](runtime/core.py) · [Agent 接线](runtime/integration.py) · [子程序](runtime/skills.py) |
| [evidence](evidence/) | 单机与机群任务进度、命令结果、接触与路线证据、搜索和观测。[任务证据](evidence/ledger.py) |
| [memory](memory/) | 近期感知、查询决策、已观察状态变化、待完成义务与路线投影。后面的策略复用前面若干能力，训练与部署 profile 可选择对应的策略。[待完成义务](memory/remaining_work.py) · [路线投影](memory/route_projection.py) |
| [execution](execution/) | 本地脚本模型的回合准备、执行、终止和独立评分。[运行器](execution/general_agent.py) |
| [evaluation](evaluation/) | 正式部署的教师策略服务、600 题运行与报告、200 题原版配对测评。 |
| [experiments](experiments/) · [validation](validation/) | 机制实验与本地行为验收。 |
| [tests](tests/) | Harness、Agent 接线、任务证据、部署协议的回归测试。 |

## 在本地验证

在仓库根目录运行。Python 版本、模拟器依赖和工具环境见[安装说明](../docs/DEPENDENCY_SETUP_CN.md)。

```bash
agent4drone/.venv/bin/python -m flockcue check
agent4drone/.venv/bin/python -m flockcue smoke --output experiments/runs/flockcue-smoke-example
agent4drone/.venv/bin/python -m flockcue.evaluation.run_flockcue_deployment600 --help
agent4drone/.venv/bin/python -m flockcue.evaluation.run_agent4drone_matched_subset verify
```

`check` 核验工作区准备出的 93 个实际源码输入、工具环境依赖清单、原任务及协议。锁定 profile 为 `general-agent-v5-local`。重排前与当前入口的摘要都登记在锁文件中，工作区源码出现其他改动会报告路径。`smoke` 使用当前代码实际执行两个脚本模型回合，同时核对公开完成证据和独立评分。一个回合缺少显式悬停回执，另一个有回执。两者在原评分器均通过，只有后者能确认履行了悬停要求。输出目录必须是新目录。[原始锁](baseline.lock.json) · [迁移前后逐文件摘要](layout-source-lock.json)

旧的 `python -m agent_harness check` / `smoke` 按原批次准备完全相同的源码字节。它先核对现有源文件，再从本地已封存的行动组快照或 Git 提交 `a1e85a5557fe5abf794bfeeaded419dfe7596718` 读取指定旧文件。结果字段 `source_layout` 分别标识当前源码 `current` 与原版平铺快照 `published_flat`。检出历史较浅且缺少本地实验快照时，可运行 `git fetch origin tag project-layout-before-20261111` 后重放。历史实验原件仍需按各自封存清单提供。[历史源码读取](history.py)

## 修改源码

在本目录的功能模块上迭代，使用 Git 分支、提交和标签保存版本。研究协议、配置 ID、轨迹和学生分词器有各自的冻结标识。现有 `agent4drone/` 与 `scripts/` 下指向本目录的相对链接，用于已有上游入口、独立分发包及封存清单读取旧路径。新代码可直接导入 `flockcue.runtime.core`，或通过 `from flockcue.memory import RouteProjectionLedger` 使用按职责命名的记忆策略，与旧入口共享同一组 Python 对象。新增模块按职责命名。

教师轨迹、学生 SFT 和 GRPO 的工作目录见[后训练](../posttraining/)，实验数据入口见[数据集](../datasets/)。
