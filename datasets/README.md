# 项目数据集

| 目录 | 内容与用途 |
| --- | --- |
| [original-benchmark](original-benchmark/) | 上游原始 75 场、1,500 题的 `benchmark.zip`。场景中的初态和私有检查供环境执行和独立评测使用。 |
| [flockcue-600](flockcue-600/README_CN.md) | 正式评测冻结的 30 场、600 题，包括原始题序、场景文件、分折与校验器。 |
| [teacher/2026-11-11-fold-a](teacher/2026-11-11-fold-a/README.md) | FlockCue 正式首遍重建的 DeepSeek 教师轨迹。按场景分开保存训练侧和内部验证侧的逐轮输入、教师回复与后置工具回执。 |
| [sft](sft/README.md) | SFT 试采数据、历史格式回归样例和各自的适用范围。 |
| [development/task-split-pilot](development/task-split-pilot/) | 2026-11-03 的旧开发划分，30 场、174 题。用于复核与历史生成集的样本重合。 |
| [tools](tools/) | 数据集生成、划分、覆盖审计与质量检查。该目录下的 `tests/` 存放配套测试。 |

从仓库根目录核查冻结题库和首批教师数据。

```sh
python3 datasets/flockcue-600/verify.py --historical-csv
python3 datasets/teacher/2026-11-11-fold-a/verify.py
```

教师集清单和经审阅的 386 个决策轮随 Git 分发。全量逐轮文件、运行器原始归档和试采训练载荷按产物保存。恢复可选的全量逐轮文件后，可在第二条命令后加 `--full` 校验整批文件。

实验报告保留登记时的相对路径，旧入口可继续读取相同内容。开发用的独立划分及其来源快照见[历史生成集](../experiments/task-splits-v1/)和[开发覆盖样本](../experiments/task-coverage-v1/)。
