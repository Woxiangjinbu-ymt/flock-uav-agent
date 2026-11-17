# Qwen3.5-2B 纠错、核验与收尾 SFT 数据

将本目录的 [train.jsonl.gz](train.jsonl.gz) 传给现有训练器的 `--dataset` 参数，保留同目录的 [manifest.json](manifest.json) 与 [index.jsonl](index.jsonl)。每行是一条已分词的真实 deepseek-flash 决策，训练器只对本轮助手回复与 EOS 计算损失。学生输入使用当时可见的公开历史和工具定义，后置回执与取舍理由保存在数据旁表。

## 合并口径

基线为 11 月 12 日实际续训混合包，每个 epoch 有 423 行，其中 18 个不同决策各有 2 次额外重放。新包按完整训练行去重，每个决策每个 epoch 出现一次。第 1–387 行保留基线全部不同决策的原始训练字节，第 388–431 行按[审阅清单](../../teacher/2026-11-11-fold-a/audit/recovery-verification-44.jsonl)顺序追加。清单中的来源行号与哈希可定位训练侧原始教师记录；新增索引中的 `source_pack_rows.recovery_verification_44` 指向审阅清单的 1 起始行号。

| 指标 | 现有续训包 | 扩充包 |
|---|---:|---:|
| A 折训练场景 | 12 | 12 |
| 入训题 / 训练源题 | 110/240 | 113/240 |
| 已覆盖题型 / 原题型 | 36/49 | 36/49 |
| 去重决策 | 387 | 431 |
| 去重决策的监督 token | 128,057 | 154,946 |
| 每 epoch 样本行 | 423 | 431 |
| 每 epoch 监督 token | 134,795 | 154,946 |

新增的 44 轮分布在 10 场、29 题、18 个既有题型，包括 27 轮遇障后修正动作、10 个逐项核对的终答和 7 轮末态核验。首次有示范的 3 道题分别是长距离飞行、避障导航和四边形巡逻。10 个新终答中有 4 个涉及多机。

[完整覆盖表](COVERAGE.md)列出全部 3 个任务族、12 个场景、49 个题型和 5 类索引标签。原索引只有 3 轮显式标为 `correction`。逐回执行为审计在旧包中实际识别了 11 轮遇错后改动作；加上本次 27 轮，按同一行为口径可确认 38 轮。部分此类行为在原索引沿用通用工具决策标签。新包的通过率需另行实测。

## 重建与核验

在仓库根目录执行。构建器需要本机封存的 A 折训练侧公开轨迹 `datasets/teacher/2026-11-11-fold-a/public/training-public-turns.jsonl.gz`；直接使用发布包只需训练数据、索引与清单。以下命令在临时目录重建基线和本版数据。

```sh
workdir=$(mktemp -d)
posttraining/.venv/bin/python -m posttraining.sft.mix --output "$workdir/base"
posttraining/.venv/bin/python -m posttraining.sft.recovery_mix \
  --baseline "$workdir/base" --output "$workdir/release"
```

发布核验使用训练器的 `read_release` 在 CPU 上读取全部 431 行，核对样本索引、逐行哈希、掩码与 token 汇总。另按完整 token ID 及监督起点比对，44 条新增样本均与首版和实际续训包互斥。44 轮的输入、目标 token 哈希均与历史投影一致。所有样本均来自 A 折训练角色，输入最多 30,720 token、单轮目标最多 2,048 token。此次仅构建和读取数据。
