# 数据集

| 数据 | 用途 |
| --- | --- |
| [原版题库](original-benchmark/benchmark.zip) | 75 个原场景、1,500 题，包含场景初态和私有评分规则。 |
| [FlockCue 600](flockcue-600/README_CN.md) | 冻结的 30 场、600 题，每场保留原始顺序，构成论文主基准。 |
| [DeepSeek 教师轨迹](teacher/2026-11-11-fold-a/README.md) | 正式首遍的 A 折。12 个训练场景、3 个内部验证场景。首位学生将 B 折用于折外评测。 |
| [Qwen3.5-2B 首版 SFT 数据](sft/qwen35-2b-first-pass/) | 训练侧 110 题的 386 个已审阅决策轮，含分词后的输入、损失掩码、逐行来源和发布清单。[使用与重建](../posttraining/sft/README.md) |
| [系统评测结果](results/README.md) | 十月原版 1,500 题首遍、十一月 FlockCue 600 题首遍及同日 200 题配对对照。 |

## 训练与评测划分

[75 场完整角色表](original-benchmark/scene-roles.csv)可按场景 ID 与原题、逐题成绩或轨迹索引关联。同一场内 20 题按原序连续执行，整场使用同一角色。

| 角色 | 场景 | 题数 | 使用范围 |
| --- | ---: | ---: | --- |
| 基线训练 | 12 | 240 | A 折，供教师示范、SFT 与 GRPO 使用。 |
| 内部验证 | 3 | 60 | A 折，用于比较训练方案、选择 checkpoint。 |
| 固定独立测试 | 15 | 300 | B 折，只用于最终学生评测。SFT、GRPO、示范筛选和选模均排除这 15 场。 |
| 扩充候选 | 45 | 900 | 位于核心 600 题之外。目前均为候选；如需扩充，记录实际选入的整场 ID 和首个使用它的训练版本。 |

在仓库根目录以 Python 3.11 运行。

```sh
python3 datasets/flockcue-600/verify.py --historical-csv
python3 datasets/flockcue-600/roles.py
python3 datasets/results/benchmark-1500/verify.py
python3 datasets/teacher/2026-11-11-fold-a/verify.py
```

拟扩充时，可用 `python3 datasets/flockcue-600/roles.py --check-training <场景ID> ...` 检查候选名单。校验器拒绝含内部验证、固定测试、未知或重复场景的名单，输出区分已登记训练场景与扩充候选。独立评分、初态判定、题级取舍和轮级审阅存放在对应审计目录。教师与学生的当轮输入只取公开工具、指令和已经返回的执行反馈。
