# 2026-11-11 项目目录迁移

迁移前的工作树有尚未纳入 Git 的 Harness、脚本和正式实验记录。分支 `codex/project-layout` 先登记源码、依赖、基准题单与教师审阅结果，再整理目录。标签 `project-layout-before-20261111` 指向源码检查点 `a1e85a55`，之后的 `b8c67fee` 补录了已有科研协议、报告、配置与小型复现输入。源码版本可以从这两个提交继续追溯。

| 之前 | 今后从哪里开发或读取 |
| --- | --- |
| `agent_harness/`、`agent4drone/` 中的 Harness 扩展 | [flockcue/runtime](../../flockcue/runtime/) 负责接线、工具和执行状态，[memory](../../flockcue/memory/) 和 [evidence](../../flockcue/evidence/) 负责记忆与证据。Agent4Drone 服务继续在 [agent4drone](../../agent4drone/) 下启动。 |
| `scripts/` 中的本地 Harness、正式评测和试验脚本 | [flockcue/execution](../../flockcue/execution/)、[evaluation](../../flockcue/evaluation/)、[validation](../../flockcue/validation/) 与 [experiments](../../flockcue/experiments/)。共享的仿真部署工具在 [scripts](../../scripts/README.md)。 |
| `scripts/flockcue_session_cpu`、教师运行和验证脚本 | [posttraining/trajectories](../../posttraining/trajectories/README.md)，含真实归档重建入口和早期试采。 |
| `scripts/flockcue_qwen35`、学生输入转换与训练资源 | [posttraining/sft](../../posttraining/sft/README.md)、[posttraining/resources](../../posttraining/resources/)。[posttraining/rl](../../posttraining/rl/README.md) 记录在线 GRPO 的后续入口。 |
| `benchmark/`、冻结 600 题、教师公开包与 SFT 样本 | [datasets](../../datasets/README.md) 按原始基准、冻结题集、教师数据、SFT 和开发集组织；[tools](../../datasets/tools/) 放置生成与检查代码。 |

## 以 Git 定位实现

在组件目录中按能力命名源文件。例如路线投影的实现位于 [route_projection.py](../../flockcue/memory/route_projection.py)，工具历史与学生模板在 [SFT](../../posttraining/sft/) 中维护。对同一能力的后续修订提交到原文件，实验记录代码 SHA 与输入数据摘要。若实验确实比较不同能力，使用可解释的配置项和功能名称。已发布的数据集 ID、模型版本、实验名称及源码校验表沿用原来的拼写。

在根目录查看迁移前的实现和迁移后的改动。

```bash
git show project-layout-before-20261111:agent4drone/execution_memory_v7.py
git log --follow -- flockcue/memory/route_projection.py
git diff --stat project-layout-before-20261111 HEAD -- flockcue posttraining datasets
```

旧平铺文件名曾被打包器、导入语句、测试和冻结清单直接读取。兼容路径使用 Git 跟踪的相对符号链接，指向组件内的同一份实现或数据。源码字节已经变动的锁定运行器通过[迁移登记](../../flockcue/layout-source-lock.json)核对迁移前后的摘要。正式发布的封存材料仍按最初的字节与路径核验，工作区行为可以用现行 [FlockCue 命令](../../flockcue/README.md) 回放。

## 版本化范围与复核

Git 保存源码、冻结基准、审阅表、已筛选的 386 轮教师数据包及必要的验证输入。本地原始教师请求、回执归档、完整公开轮以及凭据和虚拟环境各有独立的存储位置。新检出可核对教师审阅发布的 23 个文件，加载两个本地完整公开包后可核对全部 25 个文件。从原始轨迹重新生成三个包需要登记的正式首跑归档。[数据清单与恢复范围](../../datasets/teacher/2026-11-11-fold-a/README.md) · [重建命令](../../posttraining/trajectories/README.md)

```bash
agent4drone/.venv/bin/python -m flockcue check
agent4drone/.venv/bin/python -m flockcue smoke --output experiments/runs/layout-smoke
python3 datasets/flockcue-600/verify.py --historical-csv
python3 datasets/teacher/2026-11-11-fold-a/verify.py
```

`smoke` 使用新输出目录和本地脚本模型，覆盖两个悬停正反例，并独立评分。600 题校验器读取原题库、分折和历史评分。教师发布校验器检查 SHA-256、训练场景与逐轮映射。在线评测和训练有各自的运行入口。

本次在现有开发环境运行了三个组件的回归套件，FlockCue 3,275 项、后训练 522 项、数据工具 138 项均通过。共享部署工具在 controller 环境通过标准库测试，39 项通过、25 项因可选运行条件跳过。独立 Git 导出的目录完成了现行与历史入口的源码校验、现行两题模拟回放、600 题名单与历史评分复算、386 个已审阅轮的发布校验。导出目录只包含 Git 跟踪的教师子集。原始归档可用时，三个教师包的压缩前后 SHA-256 均已重新核对。
