# FlockCue 原基准 600 题 v1

2026-11-08 用户确认该题目组合。正式身份 `flockcue-benchmark-600-v1` 见 [manifest.json](manifest.json)。其引用的 [candidate-600.json](candidate-600.json) 逐字节保留了当时展示的候选清单，包含 600 道题的任务 ID、原始顺序、所属场景、难度、任务族和来源摘要。文件名及其中的 `proposal` 是原提案记录。

| 范围 | 内容 |
| --- | --- |
| 母集 | 75 个原场景，每场 20 题。3 个任务族 × 5 档难度，每层 5 个场景。 |
| 冻结组合 | 每层按预定哈希排序取前 2 个完整场景，共 30 场、600 题。各任务族 200 题，每档难度 120 题。 |
| 原始场景 | [sessions-600.zip](sessions-600.zip) 含 30 个原始 JSON，场景初态、任务指令、执行顺序和原 checker 都随场景原件保存。 |
| 执行单位 | 每场从原初态开始，按场景内原顺序连续执行 20 题，沿用原会话的位置、累计状态和黑板继承规则。对照各组采用同一套规则。 |
| 历史参照 | [历史结果](historical-reference-600.json) 来自旧协议下原 Agent4Drone + DeepSeek 首次运行。在这些题上历史分为 506/600（84.33%）。历史结果保留独立来源，选题只读基准元数据。 |
| 分折提案 | [proposed-folds.json](proposed-folds.json) 依每层两场的既定顺序分到 A、B，各含 15 场、300 题。本轮训练、内部验证和留出的具体角色见[教师数据](../teacher/2026-11-11-fold-a/README.md)。 |

在仓库根目录执行。校验器使用 Python 标准库，核验选中清单身份、全量源 ZIP 的 15 个分层及排序、原始场景字节、任务顺序、子集压缩包和建议分折。`--historical-csv` 从正式 600 题配对表逐题复算十月参照成绩。也可在该参数后显式提供归档中的十月完整 CSV，以原 SHA-256 核对来源。

```sh
python3 datasets/flockcue-600/verify.py
python3 datasets/flockcue-600/verify.py --historical-csv
```

向服务器复制整个 `datasets/flockcue-600/` 目录后，可在目录内运行 `python3 verify.py --standalone` 检查交付数据。需要独立复算全库选题规则时，另外提供 `--benchmark /路径/benchmark.zip`。仿真与独立评分器使用原始场景；给教师或学生的输入只取任务原文、公开工具和观测回执。
