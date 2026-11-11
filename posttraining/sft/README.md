# Qwen3.5-2B 首版 SFT 数据

[原生适配器](adapter.py)按调用 ID 绑定历史回执并渲染学生原生模板。[监督区间](supervision.py)只给当前教师回复及结束标记分配标签，提示词标签为 `-100`。每个样本进行工具参数和顺序回放，整串上限为 32,768 token，目标上限为 2,048 token。模型来源、固定 revision 和文件 SHA-256 见[权重清单](model.json)。所需四个分词器文件随项目保存。

首版从 A 折训练侧 240 题中筛出 12 场、110 题的 386 个教师决策轮，各保留一次。保留 DeepSeek 实发的本轮推理、文本和工具调用，采用同一种学生输入格式。本轮动作发生后的回执仅用于核验，后续轮次才可看到它。60 道内部验证题和 B 折 300 道独立测试题均排除在训练文件之外。[轨迹审阅报告](../../datasets/teacher/2026-11-11-fold-a/audit/REPORT_CN.md)

## 使用与重建

[训练文件](../../datasets/sft/qwen35-2b-first-pass/train.jsonl.gz)使用 gzip 压缩的 JSON Lines。每行是一个未填充的因果语言模型样本，含完整的 `input_ids` 与等长的 `labels`。标签只覆盖当前教师回复及回合结束标记。读取时保留已有 `labels`，批内补齐时将新增位置的标签设为 `-100`，注意力掩码设为 0。[行号与原执行轮次](../../datasets/sft/qwen35-2b-first-pass/index.jsonl)用于审计。[发布清单](../../datasets/sft/qwen35-2b-first-pass/manifest.json)

在仓库根目录运行。第一条命令核验原生模板下的提示与目标 token，第二条从已审阅教师包在新目录重建训练数据。

```sh
posttraining/.venv/bin/python -m posttraining.sft
posttraining/.venv/bin/python -m posttraining.sft.export --output /tmp/flockcue-sft-first-pass
```

首次配置可使用 `python3.11 -m venv posttraining/.venv` 和 `posttraining/.venv/bin/python -m pip install -r posttraining/requirements.txt`。[数据边界](../../datasets/teacher/2026-11-11-fold-a/README.md)

## 单卡训练耗时

2026-11-11 在空闲的 A100 PCIe 40 GB 上测量 Qwen3.5-2B 单步前向、反向和 AdamW 更新。基座用 BF16，注意力投影用秩 16 的 LoRA，每步一个完整样本，开启梯度检查点。损失只在教师回复的标签位置计算。环境使用 PyTorch 2.8.0、Transformers 5.22.2 和这套环境自带的 PyTorch 线性注意力实现。[各步耗时、显存与环境记录](../../datasets/results/sft-a100-first-pass/measurement.json)

| 样本 token 数 | 计时步数 | 单步耗时 | 完整序列 token/s | 峰值已分配 / 预留显存（GiB） |
| ---: | ---: | ---: | ---: | ---: |
| 8,517 | 3 | 9.65 秒 | 883 | 6.85 / 7.20 |
| 12,971 | 1 | 19.23 秒 | 674 | 8.59 / 8.94 |
| 17,168 | 3 | 30.93 秒 | 555 | 10.27 / 11.24 |
| 24,920 | 1 | 60.07 秒 | 415 | 13.17 / 13.84 |
| 32,608 | 3 | 98.18 秒 | 332 | 16.02 / 16.79 |

每个长度先预热一步，表中耗时取后续计时步的中位数。最短、中位附近和最长三点分段线性插值，逐条累加 386 个样本得到 4 小时 10 分钟。加入两条区间中部样本复算为 **约 4 小时/epoch**，共处理 6,962,880 token，含 129,010 个监督 token。此估算覆盖纯训练时间，模型加载、存盘和仿真评测另计。测速使用临时适配器。

在配有同版本 CUDA 训练环境和固定权重的机器上，从项目根目录运行三档测速。输出目录使用新的仓库外路径。

```sh
python -m posttraining.sft.benchmark --model /path/to/Qwen3.5-2B \
  --dataset datasets/sft/qwen35-2b-first-pass/train.jsonl.gz \
  --output /tmp/flockcue-sft-benchmark --warmup 1 --steps 3
```

## 首轮训练与结果

从项目根目录，在上述 CUDA 环境运行一个 epoch。输出目录放在仓库外。程序会核验基座、训练集和逐行索引，用固定种子 123 打乱样本，每个样本训练一次。优化器采用恒定学习率 `2e-4` 的 AdamW。

```sh
python -u -m posttraining.sft.train --model /path/to/Qwen3.5-2B \
  --dataset datasets/sft/qwen35-2b-first-pass/train.jsonl.gz \
  --output /path/to/run/output
```

`progress.json` 显示当前步数和最近一次检查点，`steps.jsonl` 保留各步的原始样本行号、损失、耗时和显存。首步、每 10 步和末步原子保存 `checkpoint.pt`。发生中断时，给同一条命令追加 `--resume`，从最近的完整检查点继续。完成后，`summary.json` 记录整轮统计。`adapter.safetensors`、`adapter_config.json` 配合固定基座，通过 `TransformersModel(base_path, adapter_dir=output_dir)` 载入。

## 如何解释首轮结果

386 个样本覆盖 129,010 个监督 token，其中 83,907 个为教师原生推理。381 轮包含工具调用；入选的 110 题中有 21 题覆盖首轮，5 题覆盖全部轮次。验收采用实发请求、真实回执、动作证据和学生格式回放；教师推理文本的逐句事实仍未经独立标注。

所有训练序列都在 32,768 token 内。其中 9 个提示超过 30,720 token，若在线为回复预留完整的 2,048 token，这些提示会触发当前学生的上下文限制。[学生推理预算](../inference/__init__.py)

后续在 A 折 60 道内部验证题上比较 2B 基座和 SFT 的同题整题通过率，并按执行记录核对首次分工、受阻后的调度与完成判断。B 折 300 题留到训练方案确定后独立评测。
