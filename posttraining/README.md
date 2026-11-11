# FlockCue 后训练

| 目录 | 用途 |
| --- | --- |
| [trajectories](trajectories/README.md) | 用正式首跑的模型请求、响应和工具回执重建可公开的教师数据。历史 40 题试采另放在 pilot。 |
| [sft](sft/README.md) | Qwen3.5-2B 的原生模板、工具调用解析、监督 token 和首版样本审阅。 |
| [rl](rl/README.md) | 后续从 SFT 权重开展在线 GRPO。 |
| [resources](resources/) | 学生模型与 tokenizer 清单、模板、锁定依赖及 CPU 交接命令。 |
| [thinking_events](thinking_events/) | 早期选择性思考实验，包括事件标注、受限工作进程及已结束的同题对照。 |
| [tests](tests/) | 教师采集、轨迹验证、样本处理和学生执行的回归测试。 |

从[数据集目录](../datasets/)查阅实际用题、训练与留出划分、已发布的教师数据。从[研究状态](../docs/RESEARCH_STATUS_CN.md)查阅已有实验和待验证的问题。

[历史开发样本转换器](sft/formatting/wire_samples.py) 位于 SFT 格式目录。重放封存的并行工具实验时，诊断程序按完整源文件 SHA-256 选用当时随数据发布的转换器。后续修改使用新提交，数据发布时记录代码提交和文件哈希。
