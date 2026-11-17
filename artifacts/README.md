# 模型权重与 A100 现场归档

2026-11-17 在 A100 上清点两次训练和各次验证目录，共有六种正式 LoRA 权重。每份权重含 228 个张量、7,382,016 个 FP32 参数，约 28.2 MiB，配套的加载配置与权重一同纳入 Git。[训练与验证主表](../datasets/results/sft/README.md)

| 适配器目录 | 累计 epoch | SHA-256 前 12 位 |
| --- | ---: | --- |
| [first-pass](adapters/first-pass/) | 1 | `8735ff752589` |
| [continuation-1](adapters/continuation-1/)（原预算入选） | 2 | `516fa198a35b` |
| [continuation-2](adapters/continuation-2/) | 3 | `cf404ce1d927` |
| [continuation-3](adapters/continuation-3/) | 4 | `69102ed40bbe` |
| [continuation-4](adapters/continuation-4/) | 5 | `43e66c55d2cb` |
| [continuation-5](adapters/continuation-5/) | 6 | `a5a9f10c2f90` |

## 从 Git 检出后复核

从仓库根目录执行。第一条命令核对全部适配器及配置的原始字节。基座固定为 Qwen/Qwen3.5-2B revision `15852e8c16360a2fea060d615a32b45270f8a8fc`，本机已将六个基座文件核对到原始清单。新机器可用项目的下载入口恢复该修订。[基座文件清单](../posttraining/sft/model.json) · [推理环境](../posttraining/inference/README.md)

```sh
shasum -a 256 -c artifacts/adapters.sha256
python3 datasets/results/sft/verify.py
python3 -m posttraining.inference.weights artifacts/local/Qwen3.5-2B
```

最后一条使用本机基座；在新机器安装学生推理依赖后，追加 `--download` 可获取固定版本。加载入选 LoRA 时，将基座路径和 `artifacts/adapters/continuation-1` 传给项目的 CUDA 推理入口。

## 本机归档和迁移

| 相对仓库根目录的路径 | 内容 |
| --- | --- |
| `artifacts/local/Qwen3.5-2B/` | 已核对的 BF16 基座、索引、配置与分词器 |
| `artifacts/local/a100/checkpoints/{20261111-first-pass,20261112-formal}/checkpoint.pt` | 两次训练各自最后一次保存的 LoRA、优化器与随机数状态 |
| `artifacts/local/a100/sft-a100.tar.gz` | A100 两次训练、首轮在线验证、三次预算对照及测速现场。含逐轮请求、进程日志、冻结终态、原检查树、脚本、源快照和优化器检查点。归档省去独立保存的 safetensors、虚拟环境与环境脚本。归档内路径相对于原服务器的 `/uav/` |
| `artifacts/local/a100/harness-a100.tar.gz` | 早期 A100 Harness 交接包、配对和单场现场、stage0 与感知接口实验、参考与环境记录。归档内路径相对于原部署目录 `/uav/flockcue-20261110/flockcue-a100/` |
| `datasets/results/*/raw/`、`datasets/teacher/*/raw/` | 既有基准与教师现场，含本次归置的基准外三场教师原始运行。两份完整 A 折公开轨迹包位于 `datasets/teacher/2026-11-11-fold-a/public/` |

Git 保存代码、六份适配器、训练数据、主表和精选证据。跨机器搬迁完整现场时，另行复制上表中的本机数据，保持相对路径。A100 的两个归档与两份检查点已和远端 SHA-256 对照。仓库根目录执行 `shasum -a 256 -c artifacts/local-archives.sha256` 可复查这四项；两个大型基准的文件数及内容树校验方法见[基准现场索引](../datasets/results/run-archives.json)。

11 月 12 日续训代码为 `91a9ef8e`，选模评测代码为 `ea0d7c14`；三次预算变化分别为 `adc9a46e`、`e9f50314`、`3b7166ba`。每批 `sources.json` 列出实际源文件 SHA-256。九批学生评测中，涉及 FlockCue 与仿真的 71 个文件均与本次归档的代码一致。[Harness 入口与模块](../flockcue/README.md) · [学生验证协议](../runtime/student_validation.py) · [独立评分](../simulation/checker.py)

## 2026-11-17 归档复核记录

- 本机 600 题运行副本原缺 19 份模型通信日志。按场景封条从已有副本补回后，6,443 个文件、7,544,480,704 字节的整树 SHA-256 与发布值一致。1,500 题现场的 15,740 个文件也通过整树校验。[原始封存清单](../datasets/results/run-archives.json)
- A 折教师原始子集中另补回 3 份通信日志，并从旧工作区恢复训练侧及验证侧两个完整公开包，通过全部 25 项封存校验。基准外三场的 702 个场景封存文件逐一通过校验。[A 折校验入口](../datasets/teacher/2026-11-11-fold-a/verify.py) · [扩充场景审计](../datasets/teacher/2026-11-15-expansion/audit.json)
- 纯 Git 文件导出后可复算 6 行训练主表、13 行验证主表，并核验权重、续训输入及选模脚本。Harness 和调度测试分别通过 45、11 项，SFT 测试通过 46 项。本机 SFT 环境缺少训练测试所需的 Torch 与 safetensors 组合，两项相关测试跳过。

公开版保留研究分支的提交顺序与合并关系。实验记录中的原始提交号可在[原始与公开提交对照表](git-history.tsv)中查找。
