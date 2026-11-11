# DeepSeek 输入与搜索成本同期对照：证据分析

运行目录：`./docs/results/deepseek-cost-pilot-20261103/local-smoke-01`

**本地脚本 smoke；不能作为真实模型行为。**

4 个旧开发任务、4 个场景、5 条件，每条件每臂一次；两版整合配置对照，不支持显著性、单机制因果或泛化结论。

计划 10 槽；启动 10；评分 10；未运行 0。

| 条件 / 臂 | checker | 附加动作审计 | HTTP | 已知 token | 动作 | start/resume/cancel | 程序机会 | unknown剩余机会 |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: |
| short_chain / control | True | True | 3 | 78 | 2 | 1/0/0 | 0 | 0 |
| short_chain / candidate | True | True | 3 | 78 | 2 | 1/0/0 | 0 | 0 |
| navigation / candidate | True | True | 5 | 130 | 3 | 0/0/0 | 0 | 0 |
| navigation / control | True | True | 5 | 130 | 3 | 0/0/0 | 0 | 0 |
| search / control | False | False | 3 | 78 | 85 | 1/0/0 | 0 | 0 |
| search / candidate | False | False | 3 | 78 | 85 | 1/0/0 | 0 | 0 |
| unknown_takeoff / candidate | True | True | 5 | 130 | 2 | 1/0/0 | 0 | 1 |
| unknown_takeoff / control | True | True | 5 | 130 | 2 | 1/0/0 | 0 | 1 |
| search_complete / control | True | True | 3 | 78 | 125 | 1/0/0 | 0 | 0 |
| search_complete / candidate | True | True | 3 | 78 | 116 | 1/0/0 | 0 | 0 |

control 为 full_v1 / observed_target_v7；candidate 为 compact_tools_v1 / search_confirm_v9。两臂均启用相同执行状态与恢复机制，未设置同期原工具臂。

| 臂 | 成功 / 已评分 | 实际输入 token | 实际输出 token | 实际总 token | HTTP | 环境动作 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| control | 4/5 | 323 | 171 | 494 | 19 | 217 |
| candidate | 4/5 | 323 | 171 | 494 | 19 | 208 |

锁定预留 0。

## short_chain-control

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

## short_chain-candidate

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

## navigation-candidate

Drone Drone 3 takes off to 14 meters, flies to checkpoint at (454, 380, 14), and hovers briefly.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / smoke-0 | list_drones | {} |
| 2 | model-2 / smoke-1 | take_off | {"drone_id": "87c81cad", "altitude": 14} |
| 3 | model-3 / smoke-2 | navigate_to | {"drone_id": "87c81cad", "x": 454, "y": 380, "z": 14} |
| 4 | model-4 / smoke-3 | hover | {"drone_id": "87c81cad"} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": "87c81cad", "hover_request_ids": [14], "takeoff_request_ids": [9], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [12, 14], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：[]。

最终可见文本（不含 reasoning）：Local protocol stop. [TASK DONE]

## navigation-control

Drone Drone 3 takes off to 14 meters, flies to checkpoint at (454, 380, 14), and hovers briefly.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / smoke-0 | list_drones | {} |
| 2 | model-2 / smoke-1 | take_off | {"drone_id": "87c81cad", "altitude": 14} |
| 3 | model-3 / smoke-2 | navigate_to | {"drone_id": "87c81cad", "x": 454, "y": 380, "z": 14} |
| 4 | model-4 / smoke-3 | hover | {"drone_id": "87c81cad"} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": "87c81cad", "hover_request_ids": [14], "takeoff_request_ids": [9], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [12, 14], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：[]。

最终可见文本（不含 reasoning）：Local protocol stop. [TASK DONE]

## search-control

take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / smoke-0 | list_drones | {} |
| 2 | model-2 / smoke-1 | run_harness | {"action": "start", "skill": "public_search"} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": null, "hover_request_ids": [], "takeoff_request_ids": [], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "public_progress_observations": [{"request_id": 7, "progress_percentage": 0}, {"request_id": 8, "progress_percentage": 0}, {"request_id": 9, "progress_percentage": 0}, {"request_id": 11, "progress_percentage": 0}, {"request_id": 68, "progress_percentage": 0}, {"request_id": 109, "progress_percentage": 33}, {"request_id": 150, "progress_percentage": 33}, {"request_id": 191, "progress_percentage": 66}, {"request_id": 232, "progress_percentage": 66}, {"request_id": 273, "progress_percentage": 66}, {"request_id": 314, "progress_percentage": 66}, {"request_id": 355, "progress_percentage": 66}], "strict_progress_over_95": false, "additional_obligation_check": false, "checker_and_additional_obligation_passed": false}。

实际响应 model：['deepseek-flash']；fingerprint：[]。

最终可见文本（不含 reasoning）：Local protocol stop. [TASK DONE]

## search-candidate

take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / smoke-0 | list_drones | {} |
| 2 | model-2 / smoke-1 | run_harness | {"action": "start", "skill": "public_search"} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": null, "hover_request_ids": [], "takeoff_request_ids": [], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "public_progress_observations": [{"request_id": 7, "progress_percentage": 0}, {"request_id": 8, "progress_percentage": 0}, {"request_id": 9, "progress_percentage": 0}, {"request_id": 11, "progress_percentage": 0}, {"request_id": 68, "progress_percentage": 0}, {"request_id": 109, "progress_percentage": 33}, {"request_id": 150, "progress_percentage": 33}, {"request_id": 191, "progress_percentage": 66}, {"request_id": 232, "progress_percentage": 66}, {"request_id": 273, "progress_percentage": 66}, {"request_id": 314, "progress_percentage": 66}, {"request_id": 355, "progress_percentage": 66}], "strict_progress_over_95": false, "additional_obligation_check": false, "checker_and_additional_obligation_passed": false}。

实际响应 model：['deepseek-flash']；fingerprint：[]。

最终可见文本（不含 reasoning）：Local protocol stop. [TASK DONE]

## unknown_takeoff-candidate

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

## unknown_takeoff-control

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

## search_complete-control

take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / smoke-0 | list_drones | {} |
| 2 | model-2 / smoke-1 | run_harness | {"action": "start", "skill": "public_search"} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": null, "hover_request_ids": [], "takeoff_request_ids": [], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "public_progress_observations": [{"request_id": 17, "progress_percentage": 0}, {"request_id": 18, "progress_percentage": 0}, {"request_id": 19, "progress_percentage": 0}, {"request_id": 21, "progress_percentage": 0}, {"request_id": 98, "progress_percentage": 15}, {"request_id": 149, "progress_percentage": 30}, {"request_id": 190, "progress_percentage": 50}, {"request_id": 231, "progress_percentage": 55}, {"request_id": 272, "progress_percentage": 70}, {"request_id": 313, "progress_percentage": 70}, {"request_id": 354, "progress_percentage": 80}, {"request_id": 395, "progress_percentage": 95}, {"request_id": 436, "progress_percentage": 95}, {"request_id": 477, "progress_percentage": 95}, {"request_id": 518, "progress_percentage": 100}], "strict_progress_over_95": true, "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：[]。

最终可见文本（不含 reasoning）：Local protocol stop. [TASK DONE]

## search_complete-candidate

take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / smoke-0 | list_drones | {} |
| 2 | model-2 / smoke-1 | run_harness | {"action": "start", "skill": "public_search"} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": null, "hover_request_ids": [], "takeoff_request_ids": [], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "public_progress_observations": [{"request_id": 17, "progress_percentage": 0}, {"request_id": 18, "progress_percentage": 0}, {"request_id": 19, "progress_percentage": 0}, {"request_id": 21, "progress_percentage": 0}, {"request_id": 98, "progress_percentage": 15}, {"request_id": 149, "progress_percentage": 30}, {"request_id": 190, "progress_percentage": 50}, {"request_id": 231, "progress_percentage": 55}, {"request_id": 272, "progress_percentage": 70}, {"request_id": 313, "progress_percentage": 70}, {"request_id": 354, "progress_percentage": 80}, {"request_id": 395, "progress_percentage": 95}, {"request_id": 436, "progress_percentage": 95}, {"request_id": 477, "progress_percentage": 95}, {"request_id": 482, "progress_percentage": 100}], "strict_progress_over_95": true, "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：[]。

最终可见文本（不含 reasoning）：Local protocol stop. [TASK DONE]

动作的时间先后不等于模型恢复。配对差值、完整 call/请求/事件关联及无机会原因见 analysis.json；同一回复预选动作、程序查询、后续新模型动作分别记录。
