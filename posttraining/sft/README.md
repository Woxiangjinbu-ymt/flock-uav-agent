# Qwen3.5-2B 监督格式

[原生适配器](adapter.py)按调用 ID 绑定历史回执并渲染学生原生模板。[监督区间](supervision.py)只给当前教师回复及结束标记分配标签，提示词标签为 `-100`。每个样本进行工具参数和顺序回放，整串上限为 32,768 token，目标上限为 2,048 token。模型来源、固定 revision 和文件 SHA-256 见[权重清单](model.json)。所需四个分词器文件随项目保存。

在仓库根目录运行。该命令读取已审阅的 386 个真实决策轮，将当前提示和目标 token 与当时登记的 SHA-256 逐条对照。

```sh
posttraining/.venv/bin/python -m posttraining.sft
```

首次配置可使用 `python3.11 -m venv posttraining/.venv` 和 `posttraining/.venv/bin/python -m pip install -r posttraining/requirements.txt`。本目录提供学生格式化与监督核验。训练样本权重及首个 SFT 权重仍待定稿。[数据边界](../../datasets/teacher/2026-11-11-fold-a/README.md)
