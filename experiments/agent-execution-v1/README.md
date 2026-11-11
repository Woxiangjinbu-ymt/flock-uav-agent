# 统一执行契约：本地受控验收配置

日期：2026-11-02。对应主线第二项，不是新一轮在线能力实验。

8个`profile-*.json`分别配套同名`cases-*.json`；模型输出位于`traces/`。main使用applicability，resume使用every_step制造可复查暂停；budget使用合成usage上限52，其余分别检验未知usage、取消、请求超时、未排空和任务截止。所选开发任务、初态与评分不变，故障和回复在运行前显式冻结。

示例（会新建本地运行目录，不调用provider）：

```bash
agent4drone/.venv/bin/python scripts/general_agent.py prepare --profile experiments/agent-execution-v1/profile-main.json --cases experiments/agent-execution-v1/cases-main.json --output tmp/agent-execution-new-main
agent4drone/.venv/bin/python scripts/general_agent.py run --run tmp/agent-execution-new-main
agent4drone/.venv/bin/python scripts/general_agent.py verify --run tmp/agent-execution-new-main
```

契约见[规格](../../docs/AGENT_EXECUTION_CONTRACT_CN.md)，正式结果与完整限制见[验收报告](../../docs/results/agent-execution-contract-20261102/REPORT_CN.md)。已有运行目录拒绝覆盖或自动续跑。配置schema v2仍为`local_mock_only`，不授予付费采数权限。
