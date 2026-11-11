# v5 输入说明与只读并行完成契约的本地验收

固定 20 槽，`provider_calls_authorized=0`。v5 使用 schema 5、`hover_completion_v2` 与 `json_object_or_string_v2`；其他输入、Harness、隔离和恢复配置继续采用 compact/v9。v4 与 v3 profile 只用于指定旧行为对照。

沿用前轮 16 种验收场景，增加 v5 只读并行正例、v4 相同脚本对照，以及普通/unknown 起飞动作与只读查询重叠的两个负例。`parallel_tools` 在同一模型响应发出两条真实工具调用；显式 `two_public_calls_v1` 本地 barrier 使两 handler 的首个公开 API 请求在 world lock 前会合。区间和 overlaps 来自原始运行 ledger，不能修改或注入。barrier 私有记录随 worker 原件封存。

20 槽必须全部准备后再执行，每槽全新 worker 和独立 scorer。相同只读并行脚本的 v4/v5 对照另核对初态、动作和 native schema 结构一致，以及工具 schema 字节缩减；

原 c74d7020 保留 development 身份。明确动作和持续时间仅派生公开指令，原 snapshot、timeline、task ID 和 checker 不改；旧 checker 只作高度/离地状态辅助。duration 仍未验证，`success` 正常返回与 `task_completed` 分离。

```bash
agent4drone/.venv/bin/python scripts/validate_hover_compact_contract.py prepare --output <新目录>
agent4drone/.venv/bin/python scripts/validate_hover_compact_contract.py run --output <同目录>
agent4drone/.venv/bin/python scripts/validate_hover_compact_contract.py verify --output <同目录>
```

已有目录或执行批不覆盖，不自动续跑。协议和元数据反事实审计脚本一并冻结到 selection-source。真实模型调用、Teacher 数据生产、训练和第五项均不启动。
