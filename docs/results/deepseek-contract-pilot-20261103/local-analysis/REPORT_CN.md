# 新版 DeepSeek 执行契约 pilot：证据分析

运行目录：`./tmp/deepseek-contract-local-smoke-01`

**本地脚本 smoke；不能作为真实模型行为。**

3 个旧开发任务、3 个场景、4 条件，每条件每臂一次；整合包对照，不支持显著性、单机制因果或泛化结论。

计划 8 槽；启动 8；评分 8；未运行 0。

| 条件 / 臂 | checker | 附加动作审计 | HTTP | 已知 token | 动作 | start/resume/cancel | 程序机会 | unknown剩余机会 |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: |
| short_chain / baseline | True | True | 4 | 104 | 2 | 0/0/0 | 0 | 0 |
| short_chain / integrated | True | True | 3 | 78 | 2 | 1/0/0 | 0 | 0 |
| navigation / integrated | False | False | 2 | 52 | 0 | 0/0/0 | 0 | 0 |
| navigation / baseline | False | False | 12 | 312 | 0 | 0/0/0 | 0 | 0 |
| search / baseline | False | False | 2 | 200020 | 0 | 0/0/0 | 0 | 0 |
| search / integrated | False | False | 2 | 52 | 0 | 0/0/0 | 0 | 0 |
| unknown_takeoff / integrated | True | True | 5 | 130 | 2 | 1/0/0 | 0 | 1 |
| unknown_takeoff / baseline | True | True | 5 | 130 | 2 | 0/0/0 | 0 | 1 |

锁定预留 0。

## short_chain-baseline

Command drone Drone 4 to take off to an altitude of 12 meters and hover in place.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / smoke-0 | list_drones | {} |
| 2 | model-2 / smoke-1 | take_off | {"drone_id": "3d229034", "altitude": 12} |
| 3 | model-3 / smoke-2 | hover | {"drone_id": "3d229034"} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": "3d229034", "hover_request_ids": [12], "takeoff_request_ids": [11], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [11], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：[]。

最终可见文本（不含 reasoning）：Local protocol stop. [TASK DONE]

## short_chain-integrated

Command drone Drone 4 to take off to an altitude of 12 meters and hover in place.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / smoke-0 | list_drones | {} |
| 2 | model-2 / smoke-1 | run_harness | {"action": "start", "skill": "simple_chain"} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": "3d229034", "hover_request_ids": [16], "takeoff_request_ids": [13], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [13], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：[]。

最终可见文本（不含 reasoning）：Local protocol stop. [TASK DONE]

## navigation-integrated

Drone Drone 3 takes off to 14 meters, flies to checkpoint at (454, 380, 14), and hovers briefly.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / smoke-0 | list_drones | {} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": "87c81cad", "hover_request_ids": [], "takeoff_request_ids": [], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": false, "required_action_order_confirmed": false, "position_evidence_request_ids": [], "additional_obligation_check": false, "checker_and_additional_obligation_passed": false}。

实际响应 model：['deepseek-flash']；fingerprint：[]。

最终可见文本（不含 reasoning）：Local protocol stop. [TASK DONE]

## navigation-baseline

Drone Drone 3 takes off to 14 meters, flies to checkpoint at (454, 380, 14), and hovers briefly.

状态：scored；停止原因：http_attempt_limit；终止决策：{"schema_version": 1, "category": "budget_exhausted", "reason": "http_attempt_limit", "stop_reason": "http_attempt_limit", "score_eligible": true, "batch_action": "continue", "archive_training_eligible": false, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / smoke-0 | list_drones | {} |
| 2 | model-2 / smoke-1 | list_drones | {} |
| 3 | model-3 / smoke-2 | list_drones | {} |
| 4 | model-4 / smoke-3 | list_drones | {} |
| 5 | model-5 / smoke-4 | list_drones | {} |
| 6 | model-6 / smoke-5 | list_drones | {} |
| 7 | model-7 / smoke-6 | list_drones | {} |
| 8 | model-8 / smoke-7 | list_drones | {} |
| 9 | model-9 / smoke-8 | list_drones | {} |
| 10 | model-10 / smoke-9 | list_drones | {} |
| 11 | model-11 / smoke-10 | list_drones | {} |
| 12 | model-12 / smoke-11 | list_drones | {} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": "87c81cad", "hover_request_ids": [], "takeoff_request_ids": [], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": false, "required_action_order_confirmed": false, "position_evidence_request_ids": [], "additional_obligation_check": false, "checker_and_additional_obligation_passed": false}。

实际响应 model：['deepseek-flash']；fingerprint：[]。

## search-baseline

take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%.

状态：scored；停止原因：token_limit；终止决策：{"schema_version": 1, "category": "budget_exhausted", "reason": "token_limit", "stop_reason": "token_limit", "score_eligible": true, "batch_action": "continue", "archive_training_eligible": false, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / smoke-0 | list_drones | {} |
| 2 | model-2 / smoke-1 | list_drones | {} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": null, "hover_request_ids": [], "takeoff_request_ids": [], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "public_progress_observations": [{"request_id": 7, "progress_percentage": 0}, {"request_id": 8, "progress_percentage": 0}, {"request_id": 9, "progress_percentage": 0}], "strict_progress_over_95": false, "additional_obligation_check": false, "checker_and_additional_obligation_passed": false}。

实际响应 model：['deepseek-flash']；fingerprint：[]。

## search-integrated

take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / smoke-0 | list_drones | {} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": null, "hover_request_ids": [], "takeoff_request_ids": [], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "public_progress_observations": [{"request_id": 7, "progress_percentage": 0}, {"request_id": 8, "progress_percentage": 0}, {"request_id": 9, "progress_percentage": 0}], "strict_progress_over_95": false, "additional_obligation_check": false, "checker_and_additional_obligation_passed": false}。

实际响应 model：['deepseek-flash']；fingerprint：[]。

最终可见文本（不含 reasoning）：Local protocol stop. [TASK DONE]

## unknown_takeoff-integrated

Command drone Drone 4 to take off to an altitude of 12 meters and hover in place.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / smoke-0 | list_drones | {} |
| 2 | model-2 / smoke-1 | run_harness | {"action": "start", "skill": "simple_chain"} |
| 3 | model-3 / smoke-2 | get_drone_status | {"drone_id": "3d229034"} |
| 4 | model-4 / smoke-3 | hover | {"drone_id": "3d229034"} |

unknown 恢复：observed_remaining_hover_recovery；机会 1；完成且无重放尝试比率 1。

附加义务证据：{"actor_id": "3d229034", "hover_request_ids": [16], "takeoff_request_ids": [13], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [13], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：[]。

最终可见文本（不含 reasoning）：Local protocol stop. [TASK DONE]

## unknown_takeoff-baseline

Command drone Drone 4 to take off to an altitude of 12 meters and hover in place.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / smoke-0 | list_drones | {} |
| 2 | model-2 / smoke-1 | take_off | {"drone_id": "3d229034", "altitude": 12} |
| 3 | model-3 / smoke-2 | get_drone_status | {"drone_id": "3d229034"} |
| 4 | model-4 / smoke-3 | hover | {"drone_id": "3d229034"} |

unknown 恢复：observed_remaining_hover_recovery；机会 1；完成且无重放尝试比率 1。

附加义务证据：{"actor_id": "3d229034", "hover_request_ids": [13], "takeoff_request_ids": [11], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [11], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：[]。

最终可见文本（不含 reasoning）：Local protocol stop. [TASK DONE]

动作的时间先后不等于模型恢复。配对差值、完整 call/请求/事件关联及无机会原因见 analysis.json；同一回复预选动作、程序查询、后续新模型动作分别记录。
