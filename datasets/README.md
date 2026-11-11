# 数据集

| 数据 | 用途 |
| --- | --- |
| [原版题库](original-benchmark/benchmark.zip) | 75 个原场景、1,500 题，包含场景初态和私有评分规则。 |
| [FlockCue 600](flockcue-600/README_CN.md) | 冻结的 30 场、600 题，每场保留原始顺序。A、B 各占 15 个完整场景。 |
| [DeepSeek 教师轨迹](teacher/2026-11-11-fold-a/README.md) | 正式首遍的 A 折。12 个训练场景、3 个内部验证场景。首位学生将 B 折用于折外评测。 |
| [系统评测结果](results/) | 600 题正式首遍及同日 200 题配对对照。 |

在仓库根目录以 Python 3.11 运行。

```sh
python3 datasets/flockcue-600/verify.py --historical-csv
python3 datasets/teacher/2026-11-11-fold-a/verify.py
```

独立评分、初态判定、题级取舍和轮级审阅存放在对应审计目录。教师与学生的当轮输入只取公开工具、指令和已经返回的执行反馈。
