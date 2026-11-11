# 悬停完成与参数表示的本地验收

本目录冻结 16 个已知开发场景上的脚本化案例，由 `scripts/validate_hover_input_contract.py` 一次准备四组，然后依次执行。每例使用全新 Agent worker 与独立 scorer；provider 调用授权为 0。这是接口、证据、隔离和恢复的验收，不评价真实模型成功率、自然能力、token 收益或泛化，也不产生 Teacher 数据。

新 profile 明确启用 `hover_completion_v1` 和 `json_object_or_string_v1`，保持 `compact_tools_v1 + search_confirm_v9`。legacy profile 是原 v3 候选，保留 `off + string_v1` 的既有行为。

| 组 | 数量 | 覆盖 |
| --- | ---: | --- |
| main | 12 | string/object 同义动作、缺失/错误/过期状态、raw/skill unknown 后观测确认且不重放、有效 object 子程序、array/scalar/malformed 拒绝及修正 |
| action | 2 | 明确要求执行 hover 时，缺动作不能宣称完成；成功动作之后的新状态可验证 |
| duration | 1 | 当前同步 API 不测量实际持续时间，传入 duration=5 仍不能证明悬停持续 5 秒 |
| legacy | 1 | 旧 schema 继续拒绝 object，不偷偷启用新契约 |

main 使用原 c74d7020 指令。action/duration 仅派生公开指令和指令 hash，增加来源说明；原场景快照、timeline、task ID、original_task 和 checker 保持不变。旧 checker 只作为原高度/离地状态的辅助评分，不能验证新增动作或时长。所有派生内容保留 development 身份。

`plan.json` 预先列出每槽的声明采纳、旧 checker 与物理动作数预期。正常运行返回 `success=True` 与任务完成是不同字段；未能验证任务时 `task_completed=None`、`completion_claim_accepted=False`，不能把 None 改写成任务失败或假设完成。原模型声明与消息存入归档，公开最终输出不保留被拒绝的完成标记。

```bash
agent4drone/.venv/bin/python scripts/validate_hover_input_contract.py prepare --output <新的本地证据目录>
agent4drone/.venv/bin/python scripts/validate_hover_input_contract.py run --output <同一目录>
agent4drone/.venv/bin/python scripts/validate_hover_input_contract.py verify --output <同一目录>
```

`prepare` 必须先完成全部 16 槽；已有目录、已开始的批次不能覆盖或自动重跑。运行使用 `general_agent` 冻结的源代码、trace 和 fixture。顶层 manifest、每组独立 seal、最终 validation 与完整 inventory 一并留存；后续校验只读重算。完整协议见 `docs/results/hover-input-contract-20261103/PROTOCOL_CN.md`。
