# DeepSeek 行为 pilot 证据分析

运行目录：`./docs/results/deepseek-behavior-pilot-20261102/run-01`

真实模型 API＋本地隔离仿真的已知开发行为 pilot。

3 个任务、1 个场景、每题每臂 1 次；这是整合包对照，不支持单机制归因、显著性或泛化主张。


| task / arm | checker | 已派发 HTTP | 已知 token | 动作 | start / resume / cancel | 真实可恢复返回 | hover效果 |
| --- | --- | ---: | ---: | ---: | --- | ---: | --- |
| c00d8b78 / baseline | True | 7 | 77236 | 3 | 0 / 0 / 0 | 0 | True |
| c00d8b78 / integrated | True | 4 | 48296 | 3 | 1 / 0 / 0 | 0 | True |
| b4714028 / integrated | True | 8 | 102887 | 3 | 0 / 0 / 0 | 0 | True |
| b4714028 / baseline | True | 6 | 66426 | 3 | 0 / 0 / 0 | 0 | True |
| 7eba7390 / baseline | False | 12 | 212597 | 31 | 0 / 0 / 0 | 0 | 未记录/不可估计 |
| 7eba7390 / integrated | 未记录/不可估计 | 未记录/不可估计 | 未记录/不可估计 | 未记录/不可估计 | 未运行 | 未记录/不可估计 | 未记录/不可估计 |

锁定预留：0。

## c00d8b78-baseline

原指令：Drone Drone 1 takes off to 15 meters altitude, then rotates to face heading 96 degrees and hovers.

运行/评分：scored；worker耗时 8.744014042000344s；停止原因 无（正常返回）。

| 决策 | logical call / 原 tool call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_M5yDNSrVYuLqoFz9ompb9464 | list_drones | {} |
| 2 | model-2 / call_00_5unew08kYoDCCZAPNXeL5028 | get_weather | {} |
| 3 | model-2 / call_01_4fCsLer72uSNVyczz97E8006 | sense_nearby_entities | {"drone_ids": ["492c9b8c"], "detail": "summary"} |
| 4 | model-3 / call_00_RIOc2qsle9GBFrGTQ8q23572 | take_off | {"drone_id": "492c9b8c", "altitude": 15.0} |
| 5 | model-4 / call_00_ET_EG79gC9HCyhBjVehvHWx6440 | rotate | {"drone_id": "492c9b8c", "heading": 96.0} |
| 6 | model-5 / call_00_jVOyKxGRkBMJ15CObFul2450 | hover | {"drone_id": "492c9b8c"} |
| 7 | model-6 / call_00_kjhhn1p9MuGuwfXWFncL9106 | get_drone_status | {"drone_id": "492c9b8c"} |

子程序终态：[]。
可恢复机会 0；恢复率：not_estimable_zero_opportunities。终态后关联的原工具动作决策数 0（时间关联本身不证明剩余义务完成）。

已派发 HTTP 7；归档请求尝试 7（包含准入前拒绝，不能全部算作供应商调用）。

模型可见最终文本（不含 reasoning）：All commands completed successfully. Here's the summary:  **Drone 1 (`492c9b8c`)** — task complete: - ✅ **Took off** to 15 m altitude (now at z=15.0) - ✅ **Rotated** to face heading 96° - ✅ **Hovering** at current position (battery 99.2%)  Current state confirmed: - Position: (834.0, 126.0, 15.0) - Heading: 96.0° - Status: hovering - Battery: 99.2%  No obstacles were present in the vicinity, and weather (windy, ~6 m/s) was within safe limits for the operation.  [TASK DONE]

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

## c00d8b78-integrated

原指令：Drone Drone 1 takes off to 15 meters altitude, then rotates to face heading 96 degrees and hovers.

运行/评分：scored；worker耗时 4.987688041001093s；停止原因 无（正常返回）。

| 决策 | logical call / 原 tool call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_JmedDlsN5iWFzG7al6zo0525 | list_drones | {} |
| 2 | model-2 / call_00_SWMCVBHYWcTNPZ79b6Vh8675 | run_harness | {"action": "start", "skill": "simple_chain"} |
| 3 | model-3 / call_00_tsVC5PODKBSmqJ88T8Ye9029 | get_drone_status | {"drone_id": "492c9b8c"} |

子程序终态：[{"plan_id": "e801ec99-f33c-4f53-8f27-ac27974f61dc:p1", "skill": "simple_chain", "status": "completed", "reason": "skill_public_stop_condition", "cursor": 3, "actor_bindings": {"Drone 1": "492c9b8c"}, "api_calls": 10, "repairs": 0, "units": 2}]。
可恢复机会 0；恢复率：not_estimable_zero_opportunities。终态后关联的原工具动作决策数 0（时间关联本身不证明剩余义务完成）。

已派发 HTTP 4；归档请求尝试 4（包含准入前拒绝，不能全部算作供应商调用）。

模型可见最终文本（不含 reasoning）：Task complete — Drone 1 (492c9b8c) executed the full chain:  1. **Takeoff** to 15 m altitude 2. **Rotate** to heading 96° 3. **Hover** in place  Verified final state: position (834.0, 126.0, z = 15.0), heading 96.0°, speed 0.0, status "hovering", battery 99.2%.  [TASK DONE]

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

## b4714028-integrated

原指令：Drone Drone 3 takes off to 15 meters, flies to checkpoint at (544, 393, 15), and hovers briefly.

运行/评分：scored；worker耗时 10.609388040997146s；停止原因 无（正常返回）。

| 决策 | logical call / 原 tool call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_AIaZeo5ENyy9QaRg988X2751 | list_drones | {} |
| 2 | model-1 / call_01_UaGH9vPQyDpUmsYcnNzY5252 | get_weather | {} |
| 3 | model-1 / call_02_EDRgHpTN2Uv728R2kwba2276 | sense_nearby_entities | {} |
| 4 | model-2 / call_00_tuFCoSGi7OIOr79LTKeg3729 | sense_nearby_entities | {"drone_ids": ["d99f1904"], "detail": "summary"} |
| 5 | model-3 / call_00_jCDdz70N6NwE2xdze2yw7360 | take_off | {"drone_id": "d99f1904", "altitude": 15.0} |
| 6 | model-4 / call_00_ET_2GGW7ymm5s1v5MepHtKP8669 | get_drone_status | {"drone_id": "d99f1904"} |
| 7 | model-5 / call_00_sZsDo6AxGfkiKKNjQhHa4277 | navigate_to_and_sense | {"drone_id": "d99f1904", "x": 544.0, "y": 393.0, "z": 15.0} |
| 8 | model-6 / call_00_9i2g0W5lqmOdDWhn5ODs8382 | hover | {"drone_id": "d99f1904", "duration": 5.0} |
| 9 | model-7 / call_00_fwxaafCwEePiNz31m63s0645 | get_drone_status | {"drone_id": "d99f1904"} |

子程序终态：[]。
可恢复机会 0；恢复率：not_estimable_zero_opportunities。终态后关联的原工具动作决策数 0（时间关联本身不证明剩余义务完成）。

工具错误回执 `call_02_EDRgHpTN2Uv728R2kwba2276`（sense_nearby_entities）：[{"kind": "text", "text": "Error invoking tool 'sense_nearby_entities' with kwargs {} with error:\n input_json: Field required\n Please fix the error and try again.", "truncated": false, "characters": 135, "sha256": "6783737effdd584e0a14f9e83503d6aaf8eca9a54a9c26533cf8e6c52ccdad95"}]

已派发 HTTP 8；归档请求尝试 8（包含准入前拒绝，不能全部算作供应商调用）。

模型可见最终文本（不含 reasoning）：Mission complete for Drone 3 (id `d99f1904`):  1. **Pre-flight checks** – 5 drones available, weather windy (6.1 m/s, visibility ~10.5 km), Drone 3 on ground at (506, 627, 0) with 100% battery, no nearby obstacles sensed. 2. **Takeoff** – Climbed to 15 m (status: hovering, battery 99.5%). 3. **Flight to checkpoint** – Navigated to (544, 393, 15); arrival verified, distance remaining 0.0 m, direct move strategy used. No obstacles blocked the path; one static obstacle (Polygon Obstacle 1, id `2eb7caed`, footprint 392–538 × 280.78–426.78 at ground level) was sensed nearby and noted — it does not intersect the checkpoint at 15 m altitude. 4. **Hover** – Hovered ~5 s at the checkpoint; final position confirmed (544, 393, 15), heading ~171°, battery 98.0%.  Drone 3 remains hovering at the checkpoint, ready for further instructions.  [TASK DONE]

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

## b4714028-baseline

原指令：Drone Drone 3 takes off to 15 meters, flies to checkpoint at (544, 393, 15), and hovers briefly.

运行/评分：scored；worker耗时 8.11744483300572s；停止原因 无（正常返回）。

| 决策 | logical call / 原 tool call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_jbQODixLvu8tjXmHKHpa8588 | list_drones | {} |
| 2 | model-1 / call_01_Ukvv84yF2zIuKzDqZFTX2166 | get_weather | {} |
| 3 | model-2 / call_00_0fyosiV37XGxXQbFZrDj5012 | sense_nearby_entities | {"drone_ids": ["d99f1904"], "detail": "summary"} |
| 4 | model-3 / call_00_MegJvWZggPIIHCiZStB09684 | take_off | {"drone_id": "d99f1904", "altitude": 15.0} |
| 5 | model-4 / call_00_ET_PEwvfWETKvEK7mcJWnP56723 | navigate_to_and_sense | {"drone_id": "d99f1904", "x": 544.0, "y": 393.0, "z": 15.0} |
| 6 | model-5 / call_00_FhjZ45IrI1iFmVoX6mET3729 | hover | {"drone_id": "d99f1904", "duration": 5.0} |

子程序终态：[]。
可恢复机会 0；恢复率：not_estimable_zero_opportunities。终态后关联的原工具动作决策数 0（时间关联本身不证明剩余义务完成）。

已派发 HTTP 6；归档请求尝试 6（包含准入前拒绝，不能全部算作供应商调用）。

模型可见最终文本（不含 reasoning）：Drone 3 (id d99f1904) task complete:  - **Weather check:** windy (wind ~6.2 m/s from NE), visibility ~10.5 km — safe to fly. - **Takeoff:** Ascended to 15 m (battery 99.5%). - **Navigate:** Flew from (506, 627) to checkpoint (544, 393, 15). Arrival verified — final position exactly (544.0, 393.0, 15.0), distance remaining 0.0 m (battery 98.1%). - **Hover:** Brief 5 s hover completed at the checkpoint (battery 98.0%).  Note: A "Polygon Obstacle 1" (vertices spanning x≈392–538, y≈281–427) was detected near the route around (465, 354); the path stayed clear and the checkpoint was reached without issue.  [TASK DONE]

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

## 7eba7390-baseline

原指令：take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%.

运行/评分：scored；worker耗时 96.94679520800128s；停止原因 token_limit。

| 决策 | logical call / 原 tool call ID | 工具 | 参数 |
| ---: | --- | --- | --- |
| 1 | model-1 / call_00_ET_1mP3RAIA9i0KRKMcGGjF3412 | list_drones | {} |
| 2 | model-1 / call_01_ET_3r5UVq9BQO6g7pg6et650836 | get_weather | {} |
| 3 | model-2 / call_00_ZeiZY7MIupd55DfP2ucd2653 | sense_nearby_entities | {"drone_ids": ["492c9b8c", "1a4f0495", "d99f1904", "a3b868a8", "5ed7d6ab"], "detail": "full"} |
| 4 | model-3 / call_00_rVFDGGGum1RWPnuHa1Bi2539 | take_off | {"drone_id": "492c9b8c", "altitude": 10.0} |
| 5 | model-3 / call_01_TlNaasqldB9cYpbSRdfu9652 | take_off | {"drone_id": "1a4f0495", "altitude": 10.0} |
| 6 | model-3 / call_02_7324X2AIFseet6WZw1rv2691 | take_off | {"drone_id": "d99f1904", "altitude": 10.0} |
| 7 | model-3 / call_03_89d0LaAurYE8mnvxKz811263 | take_off | {"drone_id": "a3b868a8", "altitude": 10.0} |
| 8 | model-3 / call_04_ERYHW5M5O6oOpMN2jDRW9074 | take_off | {"drone_id": "5ed7d6ab", "altitude": 10.0} |
| 9 | model-4 / call_00_ET_Wgl4oIu23GG0rRGlw6Cg8221 | sense_nearby_entities | {"drone_ids": ["492c9b8c", "1a4f0495", "d99f1904", "a3b868a8", "5ed7d6ab"], "detail": "full"} |
| 10 | model-5 / call_00_rpMwcvgs3Tyj5rL3kxEB7256 | get_drone_status | {"drone_id": "492c9b8c"} |
| 11 | model-6 / call_00_XnRiqP9e1f8vmm8nIIwT1561 | navigate_to_and_sense | {"drone_id": "492c9b8c", "x": 850, "y": 280, "z": 10} |
| 12 | model-6 / call_01_AZF20TsRMv394m283drr9888 | navigate_to_and_sense | {"drone_id": "1a4f0495", "x": 680, "y": 400, "z": 10} |
| 13 | model-6 / call_02_DBnPj7Bgxt29fS4yvRPw1944 | navigate_to_and_sense | {"drone_id": "d99f1904", "x": 620, "y": 560, "z": 10} |
| 14 | model-6 / call_03_UBR9PpEHMNTtJj1eFfvr8750 | navigate_to_and_sense | {"drone_id": "a3b868a8", "x": 800, "y": 470, "z": 10} |
| 15 | model-6 / call_04_pBxzbnilpVCharu2NRxd3573 | navigate_to_and_sense | {"drone_id": "5ed7d6ab", "x": 950, "y": 180, "z": 10} |
| 16 | model-7 / call_00_MMvDuv3CId2ti25irhxF6142 | navigate_to_and_sense | {"drone_id": "492c9b8c", "x": 450, "y": 50, "z": 10} |
| 17 | model-7 / call_01_E9p1T1Au85r3xVehmiux9122 | navigate_to_and_sense | {"drone_id": "1a4f0495", "x": 1050, "y": 50, "z": 10} |
| 18 | model-7 / call_02_nI88aDCagsY8UdnfYOC53294 | navigate_to_and_sense | {"drone_id": "d99f1904", "x": 450, "y": 700, "z": 10} |
| 19 | model-7 / call_03_WgjkZp5X33SeWsYebgIQ5392 | navigate_to_and_sense | {"drone_id": "a3b868a8", "x": 1050, "y": 700, "z": 10} |
| 20 | model-7 / call_04_vWXut1S4gcp11tzX3HKc3888 | navigate_to_and_sense | {"drone_id": "5ed7d6ab", "x": 750, "y": 350, "z": 10} |
| 21 | model-8 / call_00_yYlkVmdQLHutjQlehLNn8139 | change_altitude | {"drone_id": "5ed7d6ab", "altitude": 60.0} |
| 22 | model-9 / call_00_ET_oTTmcoOCwQQQebq7zvwv6915 | sense_nearby_entities | {"drone_ids": ["5ed7d6ab"], "detail": "full"} |
| 23 | model-10 / call_00_4QP4qhMY2CPSHYA3ikct1175 | navigate_to_and_sense | {"drone_id": "492c9b8c", "x": 600, "y": 200, "z": 10} |
| 24 | model-10 / call_01_F3noTQrbTrV7x51vrnQA5289 | navigate_to_and_sense | {"drone_id": "1a4f0495", "x": 700, "y": 500, "z": 10} |
| 25 | model-10 / call_02_abxB4ViuIB8gxydFEoBz4940 | navigate_to_and_sense | {"drone_id": "d99f1904", "x": 900, "y": 350, "z": 10} |
| 26 | model-10 / call_03_V0SfAMnJNf6nLLUUt7zf2227 | navigate_to_and_sense | {"drone_id": "a3b868a8", "x": 550, "y": 400, "z": 10} |
| 27 | model-10 / call_04_f6XzjiBmaodbjKEXYt384392 | navigate_to_and_sense | {"drone_id": "5ed7d6ab", "x": 850, "y": 600, "z": 10} |
| 28 | model-11 / call_00_TnR2Ow2L1aIScoqesXbc3823 | navigate_to_and_sense | {"drone_id": "492c9b8c", "x": 450, "y": 360, "z": 10} |
| 29 | model-11 / call_01_WYL0TtcmDEEgiVyRjDuT3987 | navigate_to_and_sense | {"drone_id": "1a4f0495", "x": 580, "y": 360, "z": 10} |
| 30 | model-11 / call_02_FtZLjGrqcFg3BtdwuFN48845 | navigate_to_and_sense | {"drone_id": "d99f1904", "x": 710, "y": 360, "z": 10} |
| 31 | model-11 / call_03_ZkDEkmxZ3aL8M9ee8S1J7188 | navigate_to_and_sense | {"drone_id": "a3b868a8", "x": 840, "y": 360, "z": 10} |
| 32 | model-11 / call_04_vnrewLr9uUYSBugsCShc6353 | navigate_to_and_sense | {"drone_id": "5ed7d6ab", "x": 970, "y": 360, "z": 10} |
| 33 | model-12 / call_00_28u8bE8T20v8xfwGHeNh7066 | navigate_to_and_sense | {"drone_id": "492c9b8c", "x": 450, "y": 90, "z": 10} |
| 34 | model-12 / call_01_fLhwOEKhgBI1tNhduomI3628 | navigate_to_and_sense | {"drone_id": "1a4f0495", "x": 550, "y": 90, "z": 10} |
| 35 | model-12 / call_02_2T2wYClILuP8Gs55y8wU8971 | navigate_to_and_sense | {"drone_id": "d99f1904", "x": 650, "y": 90, "z": 10} |
| 36 | model-12 / call_03_roNrQatQc9meCEFL3Mop5713 | navigate_to_and_sense | {"drone_id": "a3b868a8", "x": 750, "y": 90, "z": 10} |
| 37 | model-12 / call_04_MxbnALhUiDOLVPnOwZ2I7430 | navigate_to_and_sense | {"drone_id": "5ed7d6ab", "x": 850, "y": 90, "z": 10} |

子程序终态：[]。
可恢复机会 0；恢复率：not_estimable_zero_opportunities。终态后关联的原工具动作决策数 0（时间关联本身不证明剩余义务完成）。

已派发 HTTP 12；归档请求尝试 13（包含准入前拒绝，不能全部算作供应商调用）。

实际响应 model：['deepseek-flash']；fingerprint：['bc19ba97cbe016479903222bb1578db6']。

## 7eba7390-integrated

原指令：take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%.

未运行；不计作任务失败。

恢复的有效效果、无动作重放、剩余义务完成必须结合 JSON 中 call ID、frame、公开 request ID 和物理事件审阅。没有事件按 0 记录，分母为 0 的比率保持不可估计。
