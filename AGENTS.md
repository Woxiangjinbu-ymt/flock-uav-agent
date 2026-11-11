# FlockCue 项目协作约定

进入本仓库时先读[研究决策与证据维护规则](docs/research/project-context.md)，并查阅当前步骤的规格。该文档承接原 AGENTS.md 的完整研究约定。

- FlockCue 代码放在 `flockcue/`，轨迹构建、SFT、RL 放在 `posttraining/`，数据和训练划分放在 `datasets/`。代码按用途命名，用 Git 提交记录迭代。
- 以同一 Harness 下独立评分的整题通过率为主指标，记录模型、场景归属、输入、预算和评分设置。策略输入只包含决策时可见的信息。
- 历史实验按发布清单校验。保留完整分母、负结果和原始后训练调研文档。具体证据及当前工作见[研究状态](docs/RESEARCH_STATUS_CN.md)。
