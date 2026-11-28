# Qwen3.5-2B 教师轨迹 v2 全量 SFT 训练包

训练参数 `--dataset datasets/sft/qwen35-2b-teacher-collection-v2/train.jsonl.gz` 指向主训练文件。训练脚本从同一目录读取逐行索引与清单。训练文件通过 Git LFS 分发，从新的检出目录获取数据时可运行 `git lfs pull --include="datasets/sft/qwen35-2b-teacher-collection-v2/train.jsonl.gz"`。[训练脚本](../../../posttraining/sft/train.py) · [数据清单](manifest.json)

| 指标 | 数量 |
| --- | ---: |
| 训练场景 | 51 |
| 独立题目 | 901 |
| 训练样本 | 11,060 |
| 掩码输入 token | 227,159,908 |
| 监督 token | 4,603,837 |

每行对应一个教师决策，包含已分词的 `input_ids` 和 `labels`。输入历史的标签为 `-100`，监督区间覆盖本轮助手回复及 EOS。训练包保留 L1 中全部 `raw_candidate` 轮次，包括 39 个使用 48K 回退窗口的样本。逐行索引记录场景、题目、逻辑调用以及来源和训练行的 SHA-256。[逐行索引](index.jsonl) · [固定版本的采集结果](https://github.com/Woxiangjinbu-ymt/flock-uav-agent/blob/8cc21e8a23673d0603491dee90facef897c1d2df/datasets/teacher/teacher-collection-v2/result.json)

已在 CPU 上用 PyTorch 2.8.0 调用训练器的 `read_release` 加载全部 11,060 行，逐行通过哈希和监督掩码校验。最长序列为 47,372 个 token。
