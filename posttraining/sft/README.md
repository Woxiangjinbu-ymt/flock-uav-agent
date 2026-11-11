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

## 如何解释首轮结果

386 个样本覆盖 129,010 个监督 token，其中 83,907 个为教师原生推理。381 轮包含工具调用；入选的 110 题中有 21 题覆盖首轮，5 题覆盖全部轮次。验收采用实发请求、真实回执、动作证据和学生格式回放；教师推理文本的逐句事实仍未经独立标注。

所有训练序列都在 32,768 token 内。其中 9 个提示超过 30,720 token，若在线为回复预留完整的 2,048 token，这些提示会触发当前学生的上下文限制。[学生推理预算](../inference/__init__.py)

后续在 A 折 60 道内部验证题上比较 2B 基座和 SFT 的同题整题通过率，并按执行记录核对首次分工、受阻后的调度与完成判断。B 折 300 题留到训练方案确定后独立评测。
