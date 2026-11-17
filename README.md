# FlockCue

FlockCue 是面向多无人机协同任务的 Agent Harness。模型接收自然语言指令，在 MultiUAV-Plat 仿真环境中查询机队、分配目标、控制飞行，并根据工具回执决定下一步动作。FlockCue 跟踪每架无人机的执行状态，支持分步执行与中断恢复；任务结束后，由独立检查器对仿真终态评分。

## Harness 实验结果

在 10 个完整场景、200 道题的配对实验中，FlockCue 与原版 Agent4Drone 均使用 `deepseek-flash`。两套系统在同一天的不同时段运行，成功请求记录到相同的模型服务指纹。**FlockCue 的整题通过率为 91.0%，原版为 84.0%，相差 7.0 个百分点。**

| 任务类型 | Agent4Drone | FlockCue | 净增通过题数 |
| --- | ---: | ---: | ---: |
| 区域分配与巡逻 | 54/60 | 56/60 | +2 |
| 区域搜索 | 55/60 | 56/60 | +1 |
| 目标分配 | 59/80 | 70/80 | +11 |
| **合计** | **168/200** | **182/200** | **+14** |

逐题比较，两套系统共同通过 162 题，仅 FlockCue 通过 20 题，仅原版通过 6 题，另有 12 题均失败。净增的 14 题中有 11 题来自目标分配。[200 题逐题对照](datasets/results/paired-200/pairs.csv) · [分场结果与模型指纹](datasets/results/paired-200/summary.json)

**完整 600 题基准。** 主基准覆盖 3 类任务、5 档难度，共 30 个场景。每场 20 题按原始顺序连续执行，单题动作窗口为 500 秒。FlockCue + DeepSeek 首遍通过 **544/600（90.7%）**。原版 Agent4Drone 在相同题目上的历史首遍通过 **506/600（84.3%）**；两次完整运行的 DeepSeek 服务指纹不同。[基准构成](datasets/flockcue-600/README_CN.md) · [600 题逐题成绩](datasets/results/benchmark-600/tasks.csv) · [运行设置与评分汇总](datasets/results/benchmark-600/summary.json)

## Harness 如何执行任务

- **按回执决定下一步。** 模型可以直接调用无人机工具，也可以启动预定义的执行子程序。例如执行“起飞到 9 米并悬停”时，子程序在动作后读取指定无人机的位置和飞行状态；高度偏离预期或悬停状态未获确认时，它会返回原因，供模型重新决策。[执行子程序](flockcue/harness/skills.py) · [真实任务的六轮教师记录](datasets/teacher/2026-11-11-fold-a/audit/public-example.json)
- **保留跨步骤的执行证据。** 同一场景通过公开黑板共享观测，每道题单独记录指令、动作、回执和剩余步骤。子程序可在检查点暂停或恢复；恢复后会重新核对相关状态，避免继续使用已过时的位置。[Harness 运行时](flockcue/harness/core.py) · [任务证据记录](flockcue/evidence/ledger.py)
- **独立评分与逐轮回放。** 策略只使用当轮可见的任务、工具和观测。运行器在评分前冻结仿真状态，由原题检查树判定整题是否通过；模型请求、工具调用和回执可按调用 ID 回溯。[运行协议](runtime/README.md) · [独立评分器](simulation/checker.py) · [轨迹记录](flockcue/recording/)

## Qwen3.5-2B 学生模型

项目从 DeepSeek 的真实执行记录中提取教师决策，保留当轮可见的历史，只对当前教师回复和结束符计算训练损失。首轮 LoRA 使用 110 道题的 386 个决策；随后加入经复审的纠错、核验与收尾示范，继续训练五轮。[教师轨迹](datasets/teacher/2026-11-11-fold-a/README.md) · [SFT 方法与数据](posttraining/sft/README.md)

在 A 折 60 题内部验证上，原推理预算下，基座与首轮 SFT 的正式整题通过数均为 **0/60**，续训一轮的检查点以 **6/60** 入选。这个检查点在取消单题累计 token 上限后达到 **11/60**，再将上下文从 32,768 扩至 65,536 后达到 **12/60**。A 折内部验证用于失败诊断与选模；B 折 300 题保留作学生独立测试。[训练与验证主表](datasets/results/sft/README.md) · [场景划分](datasets/original-benchmark/scene-roles.csv) · [六份 LoRA 权重](artifacts/README.md)

## 项目结构

| 目录 | 内容 |
| --- | --- |
| [flockcue/](flockcue/README.md) | Agent 服务、无人机工具、执行子程序、证据记录与恢复 |
| [simulation/](simulation/README.md) | 多无人机仿真环境、角色权限与原题检查器 |
| [runtime/](runtime/README.md) | 场景调度、任务动作窗口、现场封存与评分重放 |
| [datasets/](datasets/README.md) | 原始题库、冻结基准、教师轨迹、SFT 数据和逐题成绩 |
| [posttraining/](posttraining/README.md) | 教师轨迹重建、学生输入适配、LoRA 训练与推理 |
| [artifacts/](artifacts/README.md) | 模型权重、检查点索引与运行归档校验值 |

## 快速复核

在仓库根目录使用 Python 3.11 运行以下命令，可离线核验 600 题基准与历史成绩，并从已发布的训练日志、逐题评分和适配器复算学生实验主表。

```sh
python3 datasets/flockcue-600/verify.py --historical-csv
python3 datasets/results/sft/verify.py
```

运行本地仿真回放或重新执行基准时，按组件安装锁定依赖（实验使用 Python 3.11.16）。本地回放通过正式策略服务与仿真器执行一次悬停任务；完整基准运行使用 DeepSeek 服务。[Harness 安装与回放](flockcue/README.md) · [基准运行指南](runtime/README.md)

## 来源与许可

FlockCue 基于 MultiUAV-Plat 的仿真平台、原始题库和独立检查器，复用了 Agent4Drone 的无人机工具、规划器和基础服务协议。本仓库实现了基于执行反馈的 Harness、真实教师轨迹的重建与审阅，以及学生训练和配对评测流程。[上游项目](https://github.com/zhangsheng93/MultiUAV-Plat) · [项目许可证](LICENSE) · [仿真服务许可证](simulation/server/LICENSE) · [检查器许可证](simulation/CHECKER_LICENSE)
