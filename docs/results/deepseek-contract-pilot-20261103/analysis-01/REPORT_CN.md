# 新版 DeepSeek 执行契约 pilot：证据分析

运行目录：`./docs/results/deepseek-contract-pilot-20261103/run-01`

真实 DeepSeek API + 本地隔离仿真。

3 个旧开发任务、3 个场景、4 条件，每条件每臂一次；整合包对照，不支持显著性、单机制因果或泛化结论。

计划 8 槽；启动 8；评分 8；未运行 0。

| 条件 / 臂 | checker | 附加动作审计 | HTTP | 已知 token | 动作 | start/resume/cancel | 程序机会 | unknown剩余机会 |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: |
| short_chain / baseline | True | True | 6 | 65143 | 2 | 0/0/0 | 0 | 0 |
| short_chain / integrated | True | True | 4 | 49130 | 2 | 1/0/0 | 0 | 0 |
| navigation / integrated | True | True | 6 | 74680 | 3 | 0/0/0 | 0 | 0 |
| navigation / baseline | True | True | 6 | 66234 | 3 | 0/0/0 | 0 | 0 |
| search / baseline | False | False | 12 | 197356 | 19 | 0/0/0 | 0 | 0 |
| search / integrated | False | False | 10 | 239897 | 110 | 1/0/0 | 0 | 0 |
| unknown_takeoff / integrated | True | True | 6 | 76139 | 2 | 1/0/0 | 0 | 1 |
| unknown_takeoff / baseline | True | True | 6 | 65345 | 2 | 0/0/0 | 0 | 1 |

锁定预留 0。

## short_chain-baseline

Command drone Drone 4 to take off to an altitude of 12 meters and hover in place.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_ET_6jOi32YiaqC9wzA2CznR2068 | list_drones | {} |
| 2 | model-2 / call_00_rlyson1Md5ueySo5xM7c3117 | get_weather | {} |
| 3 | model-2 / call_01_481XFxZACxSI0RsJd1mL2600 | sense_nearby_entities | {"drone_ids": ["3d229034"], "detail": "summary"} |
| 4 | model-3 / call_00_Y57n23fXIqC07QT4ux0Y2232 | take_off | {"drone_id": "3d229034", "altitude": 12.0} |
| 5 | model-4 / call_00_ET_caeu9js83TLDS4uH2eLZ1737 | hover | {"drone_id": "3d229034"} |
| 6 | model-5 / call_00_ET_XD14nMF44LsuEpsEXT7O3962 | get_drone_status | {"drone_id": "3d229034"} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": "3d229034", "hover_request_ids": [14], "takeoff_request_ids": [13], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [13], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

最终可见文本（不含 reasoning）：Drone 4 (`3d229034`) is now airborne and hovering at 12 m as requested.  - Pre-flight checks: weather clear, wind ~6.4 m/s, visibility ~8.1 km, no obstacles within sensing radius - Takeoff to 12 m altitude: completed - Hover command: active; status confirmed as `hovering` at (660.0, 734.0, 12.0) - Battery: 99.4% (ample margin)  [TASK DONE]

## short_chain-integrated

Command drone Drone 4 to take off to an altitude of 12 meters and hover in place.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_DOjSNEzuFu0q4bBTktZs7741 | list_drones | {} |
| 2 | model-2 / call_00_0q09cIMpAolk4fKs4pPM2604 | get_weather | {} |
| 3 | model-2 / call_01_WkVe8t5NyTOGjqqZrOFF7336 | sense_nearby_entities | {"drone_ids": ["3d229034"], "detail": "summary"} |
| 4 | model-3 / call_00_YAczNs0hJluEwgXc7OuP5873 | run_harness | {"action": "start", "skill": "simple_chain"} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": "3d229034", "hover_request_ids": [18], "takeoff_request_ids": [15], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [15], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

最终可见文本（不含 reasoning）：Drone 4 has completed the requested action.  - **Drone:** Drone 4 (`3d229034`) - **Takeoff:** climbed from z=0 m to **12.0 m** - **Final status:** `hovering` at (660, 734, 12), speed 0, heading 0 - **Battery:** 99.4% - **Weather:** clear, wind ~6.4 m/s, visibility ~8.1 km — safe conditions - **Obstacles:** none detected within Drone 4's perception radius  [TASK DONE]

## navigation-integrated

Drone Drone 3 takes off to 14 meters, flies to checkpoint at (454, 380, 14), and hovers briefly.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_ET_kStk2gPHX5RTRGwWVrwS0552 | list_drones | {} |
| 2 | model-1 / call_01_ET_44lFjFBIaCl2L4y28g1T1442 | get_weather | {} |
| 3 | model-2 / call_00_AYEhydU9IDGfOWxs5It16777 | sense_nearby_entities | {"drone_ids": ["87c81cad"], "detail": "summary"} |
| 4 | model-3 / call_00_LeZB5yQNZClKwrzzGkw27820 | take_off | {"drone_id": "87c81cad", "altitude": 14.0} |
| 5 | model-4 / call_00_ET_r4ecZZTJAyzuEj4cJZWO3484 | navigate_to_and_sense | {"drone_id": "87c81cad", "x": 454.0, "y": 380.0, "z": 14.0, "detail": "summary"} |
| 6 | model-5 / call_00_ET_wMfVLnuYTGOUj1EzmGCF9910 | hover | {"drone_id": "87c81cad", "duration": 5.0} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": "87c81cad", "hover_request_ids": [17], "takeoff_request_ids": [11], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [14, 17], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

最终可见文本（不含 reasoning）：Mission complete for Drone 3 (id `87c81cad`):  - **Weather check:** partly_cloudy, wind 1.4 m/s, visibility ~14 km — safe for flight. - **Pre-flight sensing:** 1 target (Polygon Target 1) nearby, **no obstacles** in the path. - **Takeoff:** Successfully climbed to **14 m** (battery 99.5%). - **Navigation to checkpoint (454, 380, 14):** Completed via direct move, **arrival verified**, distance remaining 0.0 m — final position exactly (454.0, 380.0, 14.0). No obstacles encountered. - **Hover:** Hovering in place for 5 s (battery 97.8%).  All requested steps (takeoff to 14 m, fly to checkpoint, brief hover) were executed as specified.  [TASK DONE]

## navigation-baseline

Drone Drone 3 takes off to 14 meters, flies to checkpoint at (454, 380, 14), and hovers briefly.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_Igy9GKfI5Ni4cHdCI9Su4176 | list_drones | {} |
| 2 | model-1 / call_01_tf9b3teKSz1xHqxhcWyH1202 | get_weather | {} |
| 3 | model-2 / call_00_1Gi7XCU0hd03U2MYrETj2686 | sense_nearby_entities | {"drone_ids": ["87c81cad"], "detail": "summary"} |
| 4 | model-3 / call_00_EUpVsZGSW9tuaYzlzNyy8814 | take_off | {"drone_id": "87c81cad", "altitude": 14.0} |
| 5 | model-4 / call_00_ET_w9Rgw31CilJe1r53dcWo6560 | navigate_to_and_sense | {"drone_id": "87c81cad", "x": 454.0, "y": 380.0, "z": 14.0, "detail": "summary"} |
| 6 | model-5 / call_00_ET_MVyu7USsBfGDaJA5Km6L0142 | hover | {"drone_id": "87c81cad", "duration": 5.0} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": "87c81cad", "hover_request_ids": [17], "takeoff_request_ids": [11], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [14, 17], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

最终可见文本（不含 reasoning）：Drone 3 (id 87c81cad) mission complete:  - **Pre-flight checks**: Weather is safe (partly cloudy, wind ~1.6 m/s, good visibility); sensed surroundings — no obstacles detected, one nearby target (Polygon Target 1 at (465, 193)). - **Takeoff**: Ascended to 14 m from its start position (467, 75). - **Navigation**: Flew to the checkpoint (454, 380, 14) — arrival verified, distance remaining 0.0 m, current heading ~358°. - **Hover**: Hovering briefly at the checkpoint (~5 s).  Battery is at ~97.8% remaining. The drone is currently hovering at (454, 380, 14).  [TASK DONE]

## search-baseline

take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%.

状态：scored；停止原因：http_attempt_limit；终止决策：{"schema_version": 1, "category": "budget_exhausted", "reason": "http_attempt_limit", "stop_reason": "http_attempt_limit", "score_eligible": true, "batch_action": "continue", "archive_training_eligible": false, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_ET_lKmClCktrRq0mCI92ucO1311 | list_drones | {} |
| 2 | model-2 / call_00_1cafEP0ujRpCJtut9gyJ3295 | sense_nearby_entities | {"detail": "summary"} |
| 3 | model-2 / call_01_hEVbV1kC5zydEhoyT1u52502 | get_weather | {} |
| 4 | model-3 / call_00_QcIp2VG0llM15yfxkKp65197 | get_drone_status | {"drone_id": "25e62b42"} |
| 5 | model-3 / call_01_XdQnCOGWSBLF4t1bBcTY5182 | get_drone_status | {"drone_id": "3ede1132"} |
| 6 | model-4 / call_00_VMui08BoKWOcfwVcBZ076079 | take_off | {"drone_id": "25e62b42", "altitude": 20.0} |
| 7 | model-4 / call_01_eiYD1OAzSRtyJNEDcmoG8184 | take_off | {"drone_id": "3ede1132", "altitude": 20.0} |
| 8 | model-4 / call_02_WTVNL19dQboKirh2TtEX9531 | take_off | {"drone_id": "8975e019", "altitude": 20.0} |
| 9 | model-4 / call_03_PeYMn2XEWAHxgDpJBzVH3258 | take_off | {"drone_id": "47230497", "altitude": 20.0} |
| 10 | model-4 / call_04_CWgmopk2aYZqRBov6slz1155 | take_off | {"drone_id": "165247f7", "altitude": 20.0} |
| 11 | model-5 / call_00_P7jN83HCxakDO8q0N7rH2758 | sense_nearby_entities | {"detail": "full", "include_blackboard": true} |
| 12 | model-6 / call_00_2qKGjIDSSArjCS4GjCRV6523 | get_nearby_entities | {"drone_id": "8975e019"} |
| 13 | model-6 / call_01_0kEJoAOegiM53KdnP0hc8903 | get_drone_status | {"drone_id": "8975e019"} |
| 14 | model-7 / call_00_ETkP22N39G9XcN3NiCtP7730 | navigate_to_and_sense | {"drone_id": "25e62b42", "x": 700.0, "y": 740.0, "z": 20.0} |
| 15 | model-7 / call_01_6091sh3ObxIqAHSoI9w62940 | navigate_to_and_sense | {"drone_id": "3ede1132", "x": 181.0, "y": 350.0, "z": 20.0} |
| 16 | model-7 / call_02_qVpT9T4dAdmqOZRYv2br7613 | navigate_to_and_sense | {"drone_id": "8975e019", "x": 401.0, "y": 180.0, "z": 20.0} |
| 17 | model-7 / call_03_uPuLBQla6GBbczxDYDiv3054 | navigate_to_and_sense | {"drone_id": "47230497", "x": 444.0, "y": 640.0, "z": 20.0} |
| 18 | model-7 / call_04_cWgnFaXgfF0xwTnhYGAs4656 | navigate_to_and_sense | {"drone_id": "165247f7", "x": 660.0, "y": 482.0, "z": 20.0} |
| 19 | model-8 / call_00_CJ9YetyrQcwZQYIpG9kf4660 | move_along_path_and_sense | {"drone_id": "25e62b42", "waypoints": [{"x": 850, "y": 880, "z": 20}, {"x": 950, "y": 600, "z": 20}], "allow_partial_move": false} |
| 20 | model-8 / call_01_VnwPbpIe8gePS0nYB2gp3965 | move_along_path_and_sense | {"drone_id": "3ede1132", "waypoints": [{"x": 100, "y": 500, "z": 20}, {"x": 50, "y": 300, "z": 20}], "allow_partial_move": false} |
| 21 | model-8 / call_02_XVGlkSSIfisgq574uSwC9860 | move_along_path_and_sense | {"drone_id": "8975e019", "waypoints": [{"x": 450, "y": 50, "z": 20}, {"x": 620, "y": 160, "z": 20}], "allow_partial_move": false} |
| 22 | model-8 / call_03_7xXwHWw5kaE5aZ8lYGUb3379 | move_along_path_and_sense | {"drone_id": "47230497", "waypoints": [{"x": 444, "y": 250, "z": 20}, {"x": 300, "y": 300, "z": 20}], "allow_partial_move": false} |
| 23 | model-8 / call_04_0U9xHLH2TNgiLNsgcYXp4320 | move_along_path_and_sense | {"drone_id": "165247f7", "waypoints": [{"x": 800, "y": 400, "z": 20}, {"x": 950, "y": 300, "z": 20}], "allow_partial_move": false} |
| 24 | model-9 / call_00_BroWgVatv7ASFFx6pP425648 | get_drone_status | {"drone_id": "3ede1132"} |
| 25 | model-10 / call_00_EHmibPRT1t198XxMfopL7931 | navigate_to_and_sense | {"drone_id": "3ede1132", "detail": "full", "x": 110.0, "y": 410.0, "z": 20.0} |
| 26 | model-11 / call_00_zrA5bjrMD086gmcgUylQ1692 | navigate_to_and_sense | {"detail": "full", "drone_id": "3ede1132", "x": 129.0, "y": 439.0, "z": 20.0} |
| 27 | model-12 / call_00_WEhxSIMgV1PEA6CucMmR9900 | change_altitude | {"drone_id": "3ede1132", "altitude": 8.0} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": null, "hover_request_ids": [], "takeoff_request_ids": [], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "public_progress_observations": [{"request_id": 7, "progress_percentage": 0}, {"request_id": 8, "progress_percentage": 0}, {"request_id": 9, "progress_percentage": 0}], "strict_progress_over_95": false, "additional_obligation_check": false, "checker_and_additional_obligation_passed": false}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

## search-integrated

take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%.

状态：scored；停止原因：token_limit；终止决策：{"schema_version": 1, "category": "budget_exhausted", "reason": "token_limit", "stop_reason": "token_limit", "score_eligible": true, "batch_action": "continue", "archive_training_eligible": false, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_ET_CeHka935ObVavJxWuWxt9398 | list_drones | {} |
| 2 | model-1 / call_01_ET_1kANsKZZ1pGytzpQRrD40451 | get_mission_progress | {} |
| 3 | model-2 / call_00_u80IekqUV5JWwUZWSK4q3645 | get_weather | {} |
| 4 | model-2 / call_01_BBMXPl6xfmjKUabSfZNI2924 | sense_nearby_entities | {"drone_ids": ["25e62b42", "3ede1132", "8975e019", "47230497", "165247f7"], "detail": "full"} |
| 5 | model-3 / call_00_Mtg596oYMidhdBXuNvRY9955 | run_harness | {"action": "start", "skill": "public_search"} |
| 6 | model-4 / call_00_dxoMXMgbiKAnGojcY3qq5842 | sense_nearby_entities | {"detail": "full"} |
| 7 | model-4 / call_01_ZDrxW789KD3aweH7T3Pc0651 | get_mission_progress | {} |
| 8 | model-5 / call_00_ET_VR3OWoHaZ0iQXtJqdddx0418 | sense_nearby_entities | {"detail": "full"} |
| 9 | model-6 / call_00_SdodgZZYvoMvktPTRhUZ5173 | list_drones | {} |
| 10 | model-6 / call_01_uQ8Q16559QjtUGJxpC3R2143 | get_drone_status | {"drone_id": "8975e019"} |
| 11 | model-7 / call_00_0AS1dq3Jf3PIC9yY2qj23940 | navigate_to_and_sense | {"drone_id": "25e62b42", "x": 500.0, "y": 500.0, "z": 20.0} |
| 12 | model-7 / call_01_rdoltBawgw2PGu4Av5L95337 | navigate_to_and_sense | {"drone_id": "3ede1132", "x": 0.0, "y": 0.0, "z": 20.0} |
| 13 | model-7 / call_02_aRFHuMpZd3t9apyQ7mPB0866 | navigate_to_and_sense | {"drone_id": "8975e019", "x": 1000.0, "y": 1000.0, "z": 20.0} |
| 14 | model-7 / call_03_zB3zQKeH3MkUa51w74Ue5681 | navigate_to_and_sense | {"drone_id": "47230497", "x": 0.0, "y": 1000.0, "z": 20.0} |
| 15 | model-7 / call_04_KRt5q4h6dvanXc6hEPNN2713 | navigate_to_and_sense | {"drone_id": "165247f7", "x": 1000.0, "y": 0.0, "z": 20.0} |
| 16 | model-8 / call_00_GMB06cQWppFRiRxba2H48022 | navigate_to_and_sense | {"drone_id": "25e62b42", "x": 0.0, "y": 0.0, "z": 20.0, "detail": "summary"} |
| 17 | model-8 / call_01_Nk94ZXgpMJzxcj3NNdUK4009 | navigate_to_and_sense | {"drone_id": "3ede1132", "x": 200.0, "y": 160.0, "z": 20.0, "detail": "summary"} |
| 18 | model-8 / call_02_C0hCRjgNArOWECcWqdr02799 | navigate_to_and_sense | {"drone_id": "8975e019", "x": 400.0, "y": 320.0, "z": 20.0, "detail": "summary"} |
| 19 | model-8 / call_03_liipaoRXxT8UHsqR4Z8w2703 | navigate_to_and_sense | {"drone_id": "47230497", "x": 600.0, "y": 480.0, "z": 20.0, "detail": "summary"} |
| 20 | model-8 / call_04_McSRDJQ06u3KdhvUM0zV3295 | navigate_to_and_sense | {"drone_id": "165247f7", "x": 800.0, "y": 640.0, "z": 20.0, "detail": "summary"} |
| 21 | model-9 / call_00_TNAuMJEXX9ZtKIj7lwcA0278 | navigate_to_and_sense | {"detail": "summary", "drone_id": "25e62b42", "x": 100.0, "y": 0.0, "z": 20.0} |
| 22 | model-9 / call_01_fN7po1BfGiSD5X2SEjBF7549 | navigate_to_and_sense | {"detail": "summary", "drone_id": "3ede1132", "x": 50.0, "y": 100.0, "z": 20.0} |
| 23 | model-9 / call_02_sY4w4K8UdF2K0CIHR60J1161 | navigate_to_and_sense | {"detail": "summary", "drone_id": "8975e019", "x": 150.0, "y": 300.0, "z": 20.0} |
| 24 | model-9 / call_03_FMfo1dLc9ZuQsNYDQIPT7354 | navigate_to_and_sense | {"detail": "summary", "drone_id": "47230497", "x": 250.0, "y": 500.0, "z": 20.0} |
| 25 | model-9 / call_04_qnbzoHAJ8XnlmV8OOKlo0155 | navigate_to_and_sense | {"detail": "summary", "drone_id": "165247f7", "x": 750.0, "y": 700.0, "z": 20.0} |
| 26 | model-10 / call_00_XnvLgbfNfVkhNMKSc7b52769 | navigate_to_and_sense | {"detail": "summary", "drone_id": "25e62b42", "x": 200.0, "y": 0.0, "z": 20.0} |
| 27 | model-10 / call_01_yhMQki2gf1R0G9qdBa2W8643 | navigate_to_and_sense | {"detail": "summary", "drone_id": "3ede1132", "x": 0.0, "y": 200.0, "z": 20.0} |
| 28 | model-10 / call_02_jHTavkVn8kFAF080lWYU0237 | navigate_to_and_sense | {"detail": "summary", "drone_id": "8975e019", "x": 150.0, "y": 300.0, "z": 20.0} |
| 29 | model-10 / call_03_mxXYoLydU8EDMf70Uf4w8059 | navigate_to_and_sense | {"detail": "summary", "drone_id": "47230497", "x": 150.0, "y": 500.0, "z": 20.0} |
| 30 | model-10 / call_04_U8s4BS6YKdrS6L6JGbNG6822 | navigate_to_and_sense | {"detail": "summary", "drone_id": "165247f7", "x": 450.0, "y": 700.0, "z": 20.0} |

unknown 恢复：not_estimable_no_trigger；机会 0；完成且无重放尝试比率 未取得/不可估计。

附加义务证据：{"actor_id": null, "hover_request_ids": [], "takeoff_request_ids": [], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "public_progress_observations": [{"request_id": 7, "progress_percentage": 0}, {"request_id": 8, "progress_percentage": 0}, {"request_id": 9, "progress_percentage": 0}, {"request_id": 11, "progress_percentage": 0}, {"request_id": 18, "progress_percentage": 0}, {"request_id": 75, "progress_percentage": 0}, {"request_id": 116, "progress_percentage": 33}, {"request_id": 157, "progress_percentage": 33}, {"request_id": 198, "progress_percentage": 66}, {"request_id": 239, "progress_percentage": 66}, {"request_id": 280, "progress_percentage": 66}, {"request_id": 321, "progress_percentage": 66}, {"request_id": 362, "progress_percentage": 66}, {"request_id": 363, "progress_percentage": 66}], "strict_progress_over_95": false, "additional_obligation_check": false, "checker_and_additional_obligation_passed": false}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

## unknown_takeoff-integrated

Command drone Drone 4 to take off to an altitude of 12 meters and hover in place.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_6ucQ2nWL0KUpXRKwPCoo8170 | list_drones | {} |
| 2 | model-1 / call_01_QWe4UtG9QaY1aX3FUuwu7353 | get_weather | {} |
| 3 | model-2 / call_00_txg15eypcFZoYdHsmMKc0379 | sense_nearby_entities | {"drone_ids": ["3d229034"], "detail": "summary"} |
| 4 | model-3 / call_00_aIzA17NfzDScR13sNd3C5079 | run_harness | {"action": "start", "skill": "simple_chain"} |
| 5 | model-4 / call_00_cgOLGDKk1AIekPqtxYl07591 | get_drone_status | {"drone_id": "3d229034"} |
| 6 | model-5 / call_00_2X86YQVrIpbCgX32GoEG7954 | hover | {"drone_id": "3d229034"} |

unknown 恢复：observed_remaining_hover_recovery；机会 1；完成且无重放尝试比率 1。

附加义务证据：{"actor_id": "3d229034", "hover_request_ids": [18], "takeoff_request_ids": [15], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [15], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

最终可见文本（不含 reasoning）：Done. Drone 4 (id `3d229034`) took off and is now hovering in place at 12 m altitude (x=660, y=734, z=12), heading 0°, speed 0, battery ~99.4%. No obstacles were detected near it, weather is clear.  Note: the harness's takeoff receipt hit a transport timeout, but the telemetry confirms the takeoff actually took effect (status hovering at z=12), so no replay was needed; hover was then confirmed explicitly.  [TASK DONE]

## unknown_takeoff-baseline

Command drone Drone 4 to take off to an altitude of 12 meters and hover in place.

状态：scored；停止原因：无（正常返回）；终止决策：{"schema_version": 1, "category": "normal_return", "reason": "normal_return", "stop_reason": null, "score_eligible": true, "batch_action": "continue", "archive_training_eligible": true, "archive_training_eligibility_scope": "archive_integrity_candidate_for_review_only_not_teacher_approval", "task_success": "not_evaluated", "cost_context": "durable_cost_snapshot", "continuation_scope": "remaining_pre_authorized_manifest_slots_only_no_retry_topup_or_new_work", "requires_parent_exit_seal": true}。

| 决策 | logical / 原 call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_ET_618yJs306r0odmXbciue2651 | list_drones | {} |
| 2 | model-2 / call_00_x6biUI97zpXwHwTZkF0s0517 | get_weather | {} |
| 3 | model-2 / call_01_Lrvwm8YCpfNBk2l01OWz4370 | sense_nearby_entities | {"drone_ids": ["3d229034"], "detail": "summary"} |
| 4 | model-3 / call_00_O18gMdg0cZqR2tI5lnDT8755 | take_off | {"drone_id": "3d229034", "altitude": 12.0} |
| 5 | model-4 / call_00_xkKmAMCyPtqCB8Z2moq28105 | get_drone_status | {"drone_id": "3d229034"} |
| 6 | model-5 / call_00_oprUxE8OUTnZKIuOPejE7281 | hover | {"drone_id": "3d229034"} |

unknown 恢复：observed_remaining_hover_recovery；机会 1；完成且无重放尝试比率 1。

附加义务证据：{"actor_id": "3d229034", "hover_request_ids": [15], "takeoff_request_ids": [13], "logs_present": true, "hover_duration_verified": false, "scope": "fixed_action_and_order_audit_not_general_language_verifier", "hover_effect_confirmed": true, "required_action_order_confirmed": true, "position_evidence_request_ids": [13], "additional_obligation_check": true, "checker_and_additional_obligation_passed": true}。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

最终可见文本（不含 reasoning）：Task complete. Summary:  \| Item \| Detail \| \|---\|---\| \| Drone \| Drone 4 (`3d229034`) \| \| Weather check \| Clear, 23 °C, wind 6.4 m/s (north), visibility 8104 m — safe \| \| Nearby obstacles \| None detected in perception radius \| \| Takeoff \| Command response timed out, but verification showed the drone reached 12 m \| \| Current state \| Hovering at (660, 734, 12), heading north, speed 0 \| \| Battery \| ~99.4% \|  Drone 4 is now holding a stable hover at an altitude of 12 meters above its home position (660, 734). Note: the takeoff call returned a timeout error, but Drone 4 did in fact execute it — always verify status after an ambiguous response rather than blindly resending, which could have caused a duplicate command.  [TASK DONE]

动作的时间先后不等于模型恢复。配对差值、完整 call/请求/事件关联及无机会原因见 analysis.json；同一回复预选动作、程序查询、后续新模型动作分别记录。
