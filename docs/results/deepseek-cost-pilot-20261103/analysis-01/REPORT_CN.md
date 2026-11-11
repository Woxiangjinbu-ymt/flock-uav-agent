# DeepSeek 输入与搜索成本同期对照：证据分析

运行目录：`./docs/results/deepseek-cost-pilot-20261103/run-01`

真实 DeepSeek API + 本地隔离仿真。

4 个旧开发任务、4 个场景、5 条件，每条件每臂一次；两版整合配置对照，不支持显著性、单机制因果或泛化结论。

计划 10 槽；启动 10；评分 10；未运行 0。

| 条件 / 臂 | checker | 附加动作审计 | HTTP | 已知 token | 动作 | start/resume/cancel | 程序机会 | unknown剩余机会 |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: |
| short_chain / control | True | True | 6 | 74550 | 2 | 0/0/0 | 0 | 0 |
| short_chain / candidate | True | False | 5 | 44097 | 1 | 0/0/0 | 0 | 0 |
| navigation / candidate | True | True | 6 | 55037 | 3 | 0/0/0 | 0 | 0 |
| navigation / control | True | True | 6 | 74735 | 3 | 0/0/0 | 0 | 0 |
| search / control | False | False | 10 | 228978 | 99 | 1/0/0 | 0 | 0 |
| search / candidate | True | True | 8 | 155817 | 97 | 1/0/0 | 0 | 0 |
| unknown_takeoff / candidate | True | True | 9 | 87619 | 2 | 1/0/0 | 0 | 1 |
| unknown_takeoff / control | True | True | 7 | 90688 | 2 | 1/0/0 | 0 | 1 |
| search_complete / control | True | True | 5 | 93195 | 135 | 1/0/0 | 0 | 0 |
| search_complete / candidate | True | True | 6 | 99012 | 135 | 1/0/0 | 0 | 0 |

control 为 full_v1 / observed_target_v7；candidate 为 compact_tools_v1 / search_confirm_v9。两臂均启用相同执行状态与恢复机制，未设置同期原工具臂。

| 臂 | 成功 / 已评分 | 实际输入 token | 实际输出 token | 实际总 token | HTTP | 环境动作 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| control | 4/5 | 545485 | 16661 | 562146 | 34 | 241 |
| candidate | 5/5 | 431965 | 9617 | 441582 | 34 | 238 |

锁定预留 0。

## short_chain-control

Command drone Drone 4 to take off to an altitude of 12 meters and hover in place.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_OKB7NK6nLqXSETd8GjLG8024 | list_drones | {} |
| 2 | model-1 / call_01_8EWHOgfxy58xo2ySsVUG0869 | get_weather | {} |
| 3 | model-2 / call_00_QrwSIzQvi1EZ7wL8hOMW1302 | sense_nearby_entities | {"drone_ids": ["3d229034"], "detail": "summary"} |
| 4 | model-3 / call_00_vxlhNTqIlgYEHaoj72C83747 | take_off | {"drone_id": "3d229034", "altitude": 12.0} |
| 5 | model-4 / call_00_ET_fMtOYIhzwHbzccUDpWeY8318 | hover | {"drone_id": "3d229034"} |
| 6 | model-5 / call_00_ET_vOFgIFRT8dwpQ3bfWlfM5368 | get_drone_status | {"drone_id": "3d229034"} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": "3d229034", "hover_request_ids": [14], "takeoff_request_ids": [13], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [13], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

最终可见文本（不含 reasoning）：Done. Drone 4 (id `3d229034`) took off from (660, 734, 0) to 12 m altitude and is now hovering in place at (660, 734, 12). Weather was clear with light wind (6.3 m/s), no obstacles or other drones within its perception radius, and battery remains at 99.4%.  [TASK DONE]

## short_chain-candidate

Command drone Drone 4 to take off to an altitude of 12 meters and hover in place.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_ET_qoNIgWemrIv8GWonrqeM9605 | list_drones | {} |
| 2 | model-2 / call_00_sgiWidJAE8ZTpEqPq0UX2993 | get_weather | {} |
| 3 | model-2 / call_01_z5DzS6Qb5RtdvzGRZfJ79599 | sense_nearby_entities | {"drone_ids": ["3d229034"], "detail": "summary"} |
| 4 | model-3 / call_00_WtvOzcqgx5aIF26UgG8x0138 | take_off | {"drone_id": "3d229034", "altitude": 12.0} |
| 5 | model-4 / call_00_ET_hjzYUXte1k6DSmcwxxCe5992 | get_drone_status | {"drone_id": "3d229034"} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": "3d229034", "hover_request_ids": [], "takeoff_request_ids": [13], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": false, "required_action_order_confirmed": false, "position_evidence_request_ids": [13], "additional_obligation_check": false, "checker_and_additional_obligation_passed": false}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

最终可见文本（不含 reasoning）：Drone 4 has completed the maneuver successfully:  - **Drone:** Drone 4 (`3d229034`) - **Takeoff:** Success — climbed from 0 m to **12.0 m** - **Current status:** `hovering` in place at (660, 734, 12.0), heading 0°, speed 0 - **Battery:** 99.5% (ample reserve) - **Conditions:** Clear weather, ~6.4 m/s wind, no obstacles or other drones within its 50 m perception radius  Drone 4 is now hovering at the requested 12-meter altitude.  [TASK DONE]

## navigation-candidate

Drone Drone 3 takes off to 14 meters, flies to checkpoint at (454, 380, 14), and hovers briefly.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_d0u1URLnJRVYF42h18pA3213 | list_drones | {} |
| 2 | model-1 / call_01_CpmjXzpcBZGBm83vQn5K5118 | get_weather | {} |
| 3 | model-2 / call_00_ianoHDLxb0yksz7RShsx4596 | sense_nearby_entities | {"drone_ids": ["87c81cad"], "detail": "summary"} |
| 4 | model-3 / call_00_IjGvzzrV8l57nIn56xvU4032 | take_off | {"drone_id": "87c81cad", "altitude": 14.0} |
| 5 | model-4 / call_00_ET_jrKgo51VZT0EY5qD0dt06661 | navigate_to_and_sense | {"drone_id": "87c81cad", "x": 454.0, "y": 380.0, "z": 14.0, "detail": "summary"} |
| 6 | model-5 / call_00_ET_HnzPo6RYnUHQ9ngIZHer8762 | hover | {"drone_id": "87c81cad", "duration": 5.0} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": "87c81cad", "hover_request_ids": [17], "takeoff_request_ids": [11], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [14, 17], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

最终可见文本（不含 reasoning）：Drone 3 (id `87c81cad`) completed the requested sequence:  1. **Takeoff** — rose to 14 m (battery 99.5%). 2. **Navigation** — flew to checkpoint (454, 380, 14); arrival verified, final position exactly (454.0, 380.0, 14.0), distance remaining 0.0 m (battery 97.8%). 3. **Hover** — hovering briefly (5 s) at the checkpoint.  Environment notes: no obstacles were detected near Drone 3 before or after the flight; Polygon Target 1 (id `247da170`, center 465, 193) was the only nearby target sensed. Weather is partly cloudy, wind ~1.6 m/s — well within safe limits.  [TASK DONE]

## navigation-control

Drone Drone 3 takes off to 14 meters, flies to checkpoint at (454, 380, 14), and hovers briefly.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_ET_O8rE9Ix9dkaNMkRHup333115 | list_drones | {} |
| 2 | model-1 / call_01_ET_XGjkDSzxU2F9rY0Ky3c23972 | get_weather | {} |
| 3 | model-2 / call_00_aQC2HjErAMDXOr6tO9d50200 | sense_nearby_entities | {"drone_ids": ["87c81cad"], "detail": "summary"} |
| 4 | model-3 / call_00_grRxO8BaFv8FYWQWWwhj8302 | take_off | {"drone_id": "87c81cad", "altitude": 14.0} |
| 5 | model-4 / call_00_ET_diOrEGLGFppOEl9M64QO9778 | navigate_to_and_sense | {"drone_id": "87c81cad", "x": 454.0, "y": 380.0, "z": 14.0, "detail": "summary"} |
| 6 | model-5 / call_00_ET_JQANu1H9gj3YU12arD3x0639 | hover | {"drone_id": "87c81cad", "duration": 5.0} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": "87c81cad", "hover_request_ids": [17], "takeoff_request_ids": [11], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [14, 17], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

最终可见文本（不含 reasoning）：All requested actions completed for Drone 3:  \| Step \| Result \| \|---\|---\| \| Pre-flight weather \| partly_cloudy, wind 1.57 m/s, visibility 14 km — safe \| \| Nearby sensing (pre-move) \| No obstacles detected \| \| Take off to 14 m \| ✅ completed (battery 99.5%) \| \| Navigate to (454, 380, 14) \| ✅ arrival verified, distance_remaining 0.0 m, moved directly (no blockage) \| \| Hover briefly (5 s) \| ✅ completed (battery 97.8%) \|  Drone 3 (id `87c81cad`) is now hovering at the checkpoint (454, 380, 14) with 97.8% battery.  [TASK DONE]

## search-control

take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%.

状态：scored；停止原因：token_limit；终止决策：{"schema_version": 1, "category": "budget_exhausted", "reason": "token_limit", "stop_reason": "token_limit", "score_eligible": true, "batch_action": "continue", "archive_training_eligible": false, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_ET_7L11fanzJ2TgjCGtvqNV0621 | list_drones | {} |
| 2 | model-1 / call_01_ET_IFHdwKvCJKUO4g9kNCKH7270 | get_weather | {} |
| 3 | model-1 / call_02_ET_8Mvx8LoNvzaiQmRVUsNg9892 | get_mission_progress | {} |
| 4 | model-2 / call_00_YNP0RIuIfIIW7gaJguYk6712 | sense_nearby_entities | {"detail": "full"} |
| 5 | model-3 / call_00_v8xZIHmkAwHVNJvIsR784629 | run_harness | {"action": "start", "skill": "public_search"} |
| 6 | model-4 / call_00_3TeWZ47zFu9c18yANRHw0025 | sense_nearby_entities | {"detail": "full"} |
| 7 | model-4 / call_01_DRF0ErpN5VxGN6MZRaHy5892 | get_drone_status | {"drone_id": "3ede1132"} |
| 8 | model-4 / call_02_a5Yv4Xu6AlYf6RjHtb5S2156 | get_drone_status | {"drone_id": "47230497"} |
| 9 | model-5 / call_00_Mhg070A8dqbuhdSmBTQ61494 | get_drone_status | {"drone_id": "25e62b42"} |
| 10 | model-5 / call_01_7q2zP59zvIFs8PiUcq6D8851 | get_drone_status | {"drone_id": "8975e019"} |
| 11 | model-5 / call_02_Qbb75HPsWLotHAgWU0748954 | get_drone_status | {"drone_id": "165247f7"} |
| 12 | model-6 / call_00_qwX82hhYlW8YUZgcaoBZ3907 | get_target_info | {"target_id": "5f846954"} |
| 13 | model-6 / call_01_0TqCC6lVbxcfUSYtd6t91666 | get_target_info | {"target_id": "9863aa68"} |
| 14 | model-7 / call_00_OBVXYnwS90X4bMwtcTIc4319 | move_to_and_sense | {"drone_id": "3ede1132", "x": 100.0, "y": 940.0, "z": 20.0} |
| 15 | model-8 / call_00_Qh2mKUJU5j3GF2IPzWX46486 | move_to_and_sense | {"drone_id": "8975e019", "x": 300.0, "y": 940.0, "z": 20.0} |
| 16 | model-8 / call_01_nTnZ05ng6jtDxCYb5AIw6582 | move_to_and_sense | {"drone_id": "47230497", "x": 500.0, "y": 940.0, "z": 20.0} |
| 17 | model-8 / call_02_rcZVHDLYS7r55k9D9Ymk9969 | move_to_and_sense | {"drone_id": "165247f7", "x": 700.0, "y": 940.0, "z": 20.0} |
| 18 | model-8 / call_03_VFgd27Uit37RGpnuQl1Y2625 | move_to_and_sense | {"drone_id": "25e62b42", "x": 900.0, "y": 940.0, "z": 20.0} |
| 19 | model-9 / call_00_UNP3w7PUHbWUWmcYAgvS3019 | move_to_and_sense | {"drone_id": "3ede1132", "x": 100.0, "y": 830.0, "z": 20.0} |
| 20 | model-9 / call_01_mYORDAck5bLaG6XnsLjH1709 | move_to_and_sense | {"drone_id": "8975e019", "x": 300.0, "y": 830.0, "z": 20.0} |
| 21 | model-9 / call_02_Hs8RchBbsCREjwcLDm6N8965 | move_to_and_sense | {"drone_id": "47230497", "x": 500.0, "y": 830.0, "z": 20.0} |
| 22 | model-9 / call_03_1GKch9mO6OALPItK4NzS0042 | move_to_and_sense | {"drone_id": "165247f7", "x": 800.0, "y": 830.0, "z": 20.0} |
| 23 | model-9 / call_04_dHsM7sYxyTPVxrClorNc9932 | move_to_and_sense | {"drone_id": "25e62b42", "x": 900.0, "y": 830.0, "z": 20.0} |
| 24 | model-10 / call_00_ZyYQMqDjBqIi9hzT2rp82906 | move_to_and_sense | {"drone_id": "3ede1132", "x": 100.0, "y": 720.0, "z": 20.0} |
| 25 | model-10 / call_01_r7IJFU8hn0Co8puML3im4793 | move_to_and_sense | {"drone_id": "8975e019", "x": 300.0, "y": 720.0, "z": 20.0} |
| 26 | model-10 / call_02_qL6v9A02nzbvpoGcIw727030 | move_to_and_sense | {"drone_id": "47230497", "x": 500.0, "y": 720.0, "z": 20.0} |
| 27 | model-10 / call_03_gMMaZSq845JEb4aHtDFb8589 | move_to_and_sense | {"drone_id": "25e62b42", "x": 900.0, "y": 720.0, "z": 20.0} |
| 28 | model-10 / call_04_DWsEKNChflVXoLUlDK8f6046 | move_to_and_sense | {"drone_id": "165247f7", "x": 810.0, "y": 420.0, "z": 20.0} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": null, "hover_request_ids": [], "takeoff_request_ids": [], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "public_progress_observations": [{"request_id": 7, "progress_percentage": 0}, {"request_id": 8, "progress_percentage": 0}, {"request_id": 9, "progress_percentage": 0}, {"request_id": 12, "progress_percentage": 0}, {"request_id": 19, "progress_percentage": 0}, {"request_id": 76, "progress_percentage": 0}, {"request_id": 117, "progress_percentage": 33}, {"request_id": 158, "progress_percentage": 33}, {"request_id": 199, "progress_percentage": 66}, {"request_id": 240, "progress_percentage": 66}, {"request_id": 281, "progress_percentage": 66}, {"request_id": 322, "progress_percentage": 66}, {"request_id": 363, "progress_percentage": 66}], "strict_progress_over_95": false, "additional_obligation_check": false, "checker_and_additional_obligation_passed": false}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

## search-candidate

take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_nkvDuJFb5uSfbHrJgjWi6096 | list_drones | {} |
| 2 | model-1 / call_01_EyQJPE3q2SDpWnGkJkYw6668 | get_mission_progress | {} |
| 3 | model-2 / call_00_NYh3zmUhvvZXpr7hAIZt8019 | run_harness | {"action": "start", "skill": "public_search"} |
| 4 | model-3 / call_00_NGA35MXDvRhTfs3SqQ3v9923 | list_drones | {} |
| 5 | model-3 / call_01_f7R3mN0HWt6gjKSOeedB1696 | sense_nearby_entities | {"detail": "full"} |
| 6 | model-4 / call_00_Wln3y83ucoCPbqFfhIQU5604 | navigate_to_and_sense | {"drone_id": "25e62b42", "x": 900.0, "y": 100.0, "z": 20.0} |
| 7 | model-4 / call_01_Sa6ODYJsAORcj39tzW2E0910 | navigate_to_and_sense | {"drone_id": "3ede1132", "x": 100.0, "y": 100.0, "z": 20.0} |
| 8 | model-4 / call_02_uzRD2CiRhwT6YIN88h0a9707 | navigate_to_and_sense | {"drone_id": "47230497", "x": 900.0, "y": 900.0, "z": 20.0} |
| 9 | model-4 / call_03_S1qeMpeYtk17rAuzEaMR1176 | navigate_to_and_sense | {"drone_id": "165247f7", "x": 100.0, "y": 900.0, "z": 20.0} |
| 10 | model-4 / call_04_IOT505n1X66G0NFl1dM90237 | navigate_to_and_sense | {"drone_id": "8975e019", "x": 500.0, "y": 300.0, "z": 20.0} |
| 11 | model-5 / call_00_582oovWcSAYrXck9RT4J5215 | get_mission_progress | {} |
| 12 | model-6 / call_00_V1WJahjMJ0gUPvbj31c74831 | move_along_path_and_sense | {"drone_id": "25e62b42", "waypoints": [{"x": 50, "y": 50}, {"x": 950, "y": 50}, {"x": 950, "y": 150}, {"x": 50, "y": 150}]} |
| 13 | model-6 / call_01_1ILl1hvql5lKsdrgKWcC6346 | move_along_path_and_sense | {"drone_id": "3ede1132", "waypoints": [{"x": 50, "y": 250}, {"x": 950, "y": 250}, {"x": 950, "y": 350}, {"x": 50, "y": 350}]} |
| 14 | model-6 / call_02_rLD82XQTY3oNKuNRIKcP0144 | move_along_path_and_sense | {"drone_id": "8975e019", "waypoints": [{"x": 50, "y": 450}, {"x": 950, "y": 450}, {"x": 950, "y": 550}, {"x": 50, "y": 550}]} |
| 15 | model-6 / call_03_GMP0fNh0geo37eT9jlJZ5933 | move_along_path_and_sense | {"drone_id": "47230497", "waypoints": [{"x": 50, "y": 650}, {"x": 950, "y": 650}, {"x": 950, "y": 750}, {"x": 50, "y": 750}]} |
| 16 | model-6 / call_04_LCwpxR3mQLqU76gvCgav7404 | move_along_path_and_sense | {"drone_id": "165247f7", "waypoints": [{"x": 50, "y": 850}, {"x": 950, "y": 850}, {"x": 950, "y": 950}, {"x": 50, "y": 950}]} |
| 17 | model-7 / call_00_CigkHOwmEP8irhd8OM683551 | get_mission_progress | {} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": null, "hover_request_ids": [], "takeoff_request_ids": [], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "public_progress_observations": [{"request_id": 7, "progress_percentage": 0}, {"request_id": 8, "progress_percentage": 0}, {"request_id": 9, "progress_percentage": 0}, {"request_id": 11, "progress_percentage": 0}, {"request_id": 12, "progress_percentage": 0}, {"request_id": 69, "progress_percentage": 0}, {"request_id": 110, "progress_percentage": 33}, {"request_id": 151, "progress_percentage": 33}, {"request_id": 192, "progress_percentage": 66}, {"request_id": 233, "progress_percentage": 66}, {"request_id": 274, "progress_percentage": 66}, {"request_id": 315, "progress_percentage": 66}, {"request_id": 356, "progress_percentage": 66}, {"request_id": 397, "progress_percentage": 66}, {"request_id": 408, "progress_percentage": 100}], "strict_progress_over_95": true, "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

最终可见文本（不含 reasoning）：Session task progress is now **100%** (3/3 targets visited), which exceeds the 95% threshold.  Summary of what was accomplished: - **Took off / activated all 5 drones** (they began idle on ground; Drone 5 was the first airborne and the sweep drones were flown at 20 m). - Initial harness search reached 66% (Fixed Target 2 & 3) but stalled one round below the threshold, so I replanned with raw tools. - Ran a systematic serpentine sweep across the operational area. This discovered and reached the previously-missing **Fixed Target 1** (via Drone 1), while Fixed Targets 2 and 3 were confirmed reached. - Final verification: **3/3 targets visited, session progress = 100% > 95%.**  Some sweep legs hit obstacle-conflict partial stops (Circle/Polygon obstacles), but they did not affect target coverage — all targets were reached and the threshold is satisfied.  [TASK DONE]

## unknown_takeoff-candidate

Command drone Drone 4 to take off to an altitude of 12 meters and hover in place.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_ET_VwS8kYI7CbfLBwoZp03w3954 | list_drones | {} |
| 2 | model-2 / call_00_OnftIY7XrPLnsH2IDteX0174 | sense_nearby_entities | {"drone_ids": ["3d229034"], "detail": "summary"} |
| 3 | model-2 / call_01_draDIVE6aVhwEc2ngApc4304 | get_weather | {} |
| 4 | model-3 / call_00_ooBZhObeUiMMxSYnM2zE7196 | sense_nearby_entities | {"drone_ids": ["3d229034"], "detail": "summary"} |
| 5 | model-4 / call_00_QBrNMIv4pP6toJ1LzhjA0691 | run_harness | {"action": "start", "skill": "simple_chain"} |
| 6 | model-5 / call_00_nfUvlfcnFtlr14nNxNG46448 | get_drone_status | {"drone_id": "3d229034"} |
| 7 | model-6 / call_00_ET_t1Ala3bAUiKs14QQQdlG8456 | hover | {"drone_id": "3d229034"} |
| 8 | model-7 / call_00_ET_9JUAwO1YdhcqJzPr6fMJ8617 | get_drone_status | {"drone_id": "3d229034"} |
| 9 | model-8 / call_00_ET_2DNVkHmPvQYAl10sZeDE1976 | get_drone_status | {"drone_id": "3d229034"} |

unknown 恢复：observed_remaining_hover_recovery；机会 1；完成且无重放尝试比率 1。

附加义务证据：{"actor_id": "3d229034", "hover_request_ids": [18], "takeoff_request_ids": [15], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [15], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

最终可见文本（不含 reasoning）：Mission complete:  - **Drone 4** (`3d229034`) resolved from the fleet list; pre-flight checks done (weather clear, no nearby obstacles, battery 100%). - **Takeoff to 12 m:** The bounded `simple_chain` plan reported a transport timeout on the takeoff receipt, so I did **not** replay it. Direct status verification showed the drone already at z = 12.0 m, status `hovering` — takeoff effect confirmed applied. - **Hover in place:** Issued `hover` → status `success`, `outcome: completed`. - **Final state verified:** Drone 4 hovering at (660.0, 734.0, 12.0), heading 0°, speed 0.0, battery 99.4% (well above the 2% threshold).  [TASK DONE]

## unknown_takeoff-control

Command drone Drone 4 to take off to an altitude of 12 meters and hover in place.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_O0OH17u1hf2xDUcEW8SX0025 | list_drones | {} |
| 2 | model-2 / call_00_2ft2nKkShTvU4PKTp8fH4492 | get_weather | {} |
| 3 | model-2 / call_01_jpIvVJhDvZoQYBGPOVHW8046 | sense_nearby_entities | {"drone_ids": ["3d229034"], "detail": "summary"} |
| 4 | model-3 / call_00_RSynus6JsAvGDQSK9Cyk3565 | run_harness | {"action": "start", "skill": "simple_chain"} |
| 5 | model-4 / call_00_Jvjd0TLAQy3meRX9xZjX1316 | get_drone_status | {"drone_id": "3d229034"} |
| 6 | model-5 / call_00_ylHKw506cyVuvyW67X543122 | hover | {"drone_id": "3d229034"} |
| 7 | model-6 / call_00_ek82Ik8jxezXNKdptaEY2981 | get_drone_status | {"drone_id": "3d229034"} |

unknown 恢复：observed_remaining_hover_recovery；机会 1；完成且无重放尝试比率 1。

附加义务证据：{"actor_id": "3d229034", "hover_request_ids": [18], "takeoff_request_ids": [15], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [15], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

最终可见文本（不含 reasoning）：Mission status complete.  - Drone 4 (id `3d229034`) took off from (660, 734, 0) and is now confirmed at **12.0 m** altitude, position (660, 734, 12). - The takeoff command receipt had a transport timeout, but fresh public state confirmed the climb succeeded (status `hovering`, z = 12.0), so I did not replay it. - Hover command executed successfully: status `hovering`, speed 0.0 m/s, battery 99.4%, no nearby obstacles/targets within its 50 m perception radius. - Weather was clear, wind ~6.5 m/s — safe for flight.  Drone 4 is now hovering in place at 12 m as requested.  [TASK DONE]

## search_complete-control

take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_4Bz48byDfEZVc2kUVGdK0785 | list_drones | {} |
| 2 | model-1 / call_01_BOpJdgxx9JpWbxDVlHkC6351 | get_mission_progress | {} |
| 3 | model-1 / call_02_Karyc0yOoG09WFoCSHLx4272 | get_weather | {} |
| 4 | model-2 / call_00_hfTF5V0z2umNQfdyQAeR6605 | sense_nearby_entities | {"detail": "summary"} |
| 5 | model-3 / call_00_3pQUe7WQIvWNuqzICPRv8879 | run_harness | {"action": "start", "skill": "public_search"} |
| 6 | model-4 / call_00_FQFs4uSmh5iHSr4G6ton6438 | get_mission_progress | {} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": null, "hover_request_ids": [], "takeoff_request_ids": [], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "public_progress_observations": [{"request_id": 17, "progress_percentage": 0}, {"request_id": 18, "progress_percentage": 0}, {"request_id": 19, "progress_percentage": 0}, {"request_id": 21, "progress_percentage": 0}, {"request_id": 39, "progress_percentage": 0}, {"request_id": 101, "progress_percentage": 10}, {"request_id": 152, "progress_percentage": 25}, {"request_id": 193, "progress_percentage": 50}, {"request_id": 234, "progress_percentage": 55}, {"request_id": 275, "progress_percentage": 65}, {"request_id": 316, "progress_percentage": 70}, {"request_id": 357, "progress_percentage": 70}, {"request_id": 398, "progress_percentage": 85}, {"request_id": 439, "progress_percentage": 85}, {"request_id": 480, "progress_percentage": 90}, {"request_id": 521, "progress_percentage": 90}, {"request_id": 562, "progress_percentage": 100}, {"request_id": 563, "progress_percentage": 100}], "strict_progress_over_95": true, "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

最终可见文本（不含 reasoning）：**Mission complete — session task progress is 100% (>95% threshold).**  Execution summary: - **Fleet:** 15 idle drones (all 100% battery), weather rain, wind 2.5 m/s, visibility ~12 km — safe to fly. Committed the bounded `public_search` plan (take off necessary drones + assign to all targets until progress > 95%). - **Coverage run:** 120 assignment attempts, 111 completed, 9 blocked mid-run by newly discovered static obstacles (Circle Obstacle 3/5, Polygon Obstacle 5/9). Each blocked endpoint was avoided via alternate candidate exploration — no target was left unreached. - **Targets visited (20/20):** Fixed Targets 1–20, all resolved by exact ID from local sensing, e.g.   - Fixed 1 → 661461cd, Fixed 2 → 4e5ee34b, Fixed 3 → 3599ce6e, Fixed 4 → da6c0477, Fixed 5 → 4bab8dfc   - Fixed 6 → fce5af36, Fixed 7 → 7a694a5a, Fixed 8 → 510c7bc7, Fixed 9 → f1049c7c, Fixed 10 → b1703776   - Fixed 11 → 7d84f52a, Fixed 12 → ae7ccfd2, Fixed 13 → 346a6dcc, Fixed 14 → 7813dcac, Fixed 15 → bea14a9b   - Fixed 16 → f93dcd4d, Fixed 17 → cfdb39fc, Fixed 18 → a1d4ac40, Fixed 19 → 4624f3c8, Fixed 20 → 87e28d07 - **Verified session state:** total_targets 20, visited 20, unvisited 0, progress 100% — read fresh after the run (threshold strictly >95% is met).  The plan is terminal (`resumable=false`), so I did not restart it; all its applied actions and obstacle-avoidance results stand. [TASK DONE]

## search_complete-candidate

take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_ET_fpQcx2lIGkOKISTkiMGe3387 | list_drones | {} |
| 2 | model-1 / call_01_ET_Wjvu1oek7j130Yo7Dt1R7753 | get_mission_progress | {} |
| 3 | model-1 / call_02_ET_y4wJazN149GuOWpjTbDD5799 | get_weather | {} |
| 4 | model-2 / call_00_WCb335i0VzZoLI3AJZp20191 | sense_nearby_entities | {"detail": "summary"} |
| 5 | model-3 / call_00_Pu4yX9YJvw0hlQa9Mslk1634 | run_harness | {"action": "start", "skill": "public_search"} |
| 6 | model-4 / call_00_nU3SXLi2DMyl9L1LbTKg0170 | get_mission_progress | {} |
| 7 | model-5 / call_00_UaS5fv0j5q9HGDaO3B9u9957 | list_drones | {} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": null, "hover_request_ids": [], "takeoff_request_ids": [], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "public_progress_observations": [{"request_id": 17, "progress_percentage": 0}, {"request_id": 18, "progress_percentage": 0}, {"request_id": 19, "progress_percentage": 0}, {"request_id": 21, "progress_percentage": 0}, {"request_id": 39, "progress_percentage": 0}, {"request_id": 101, "progress_percentage": 10}, {"request_id": 152, "progress_percentage": 25}, {"request_id": 193, "progress_percentage": 50}, {"request_id": 234, "progress_percentage": 55}, {"request_id": 275, "progress_percentage": 65}, {"request_id": 316, "progress_percentage": 70}, {"request_id": 357, "progress_percentage": 70}, {"request_id": 398, "progress_percentage": 85}, {"request_id": 439, "progress_percentage": 85}, {"request_id": 480, "progress_percentage": 90}, {"request_id": 521, "progress_percentage": 90}, {"request_id": 562, "progress_percentage": 100}, {"request_id": 563, "progress_percentage": 100}], "strict_progress_over_95": true, "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

最终可见文本（不含 reasoning）：Task complete.  **Result** - **Session task progress: 100%** (20/20 targets visited, 0 unvisited) — verified by a fresh `get_mission_progress` read *after* the coverage actions, and it is strictly above the required 95%. - **Drones committed:** all 15 fleet drones took off to 20 m and were assigned across the target field; each is now hovering at 20 m with battery between ~88% and ~95% (no charging needed).  **How it was executed** - Committed the bounded public-search skill (`public_search`), which: sensed each fleet drone once without moving it (empty-obstacle entry contract), took off the necessary drones, discovered targets 1–20 by local perception, and assigned an owner drone to each target coordinate. - 120 movement attempts: 111 completed, 9 blocked by previously unknown obstacles (Circle Obstacle 3 & 5, Polygon Obstacle 5 & 9). Those blocked endpoints were recorded and re-routed through alternate waypoints rather than repeated, so no skill step or waypoint was abandoned. - All 20 named targets resolved to distinct ids and were each reached by a real drone visit (e.g., Fixed Target 19 → 4624f3c8 by Drone 12, Fixed Target 15 → bea14a9b by Drone 1, Fixed Target 4 → da6c0477, Fixed Target 9 → f1049c7c, etc.).  **Notes for you** - Novel obstacles discovered beyond the initial blackboard (14 new public obstacles) were avoided; the blackboard now holds the obstacle geometry for future routing. - All drones remain airborne hovering at 20 m — say the word if you want them to return home or land. - Weather was light rain, wind ~2.6 m/s, visibility ~12 km — within safe limits, but keep an eye on it if you extend the mission.  [TASK DONE]

动作的时间先后不等于模型恢复。配对差值、完整 call/请求/事件关联及无机会原因见 analysis.json；同一回复预选动作、程序查询、后续新模型动作分别记录。
