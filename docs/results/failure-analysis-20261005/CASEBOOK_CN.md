# 失败案例阅读索引

快照时间和统计口径见 [主报告](REPORT_CN.md)。每项链接指向本轮选定场景代次。下面是代表性深读案例，不是按主因穷尽分类。

## fb8971ec/82077301 — 最终距离与动作距离；严格阈值

场景：Area Assignment and Patrol Easy 1。任务序号：15/20。

> Drones Drone 3 and Drone 5 form a line formation: both take off to 17 meters. Drone 3 positions at (454, 416, 17) and Drone 5 flys to (545, 609, 17) first, and then move to 19 meters at most to Drone 3. Both maintain formation for monitoring.

- **deepseek：2/3，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/fb8971ec/82077301.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/fb8971ec/82077301.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/fb8971ec/82077301.agent-state.json)。
  API 调用 28 次；[完整工具/API轨迹](./experiments/runs/20261003-143255-full-5c501118/fb8971ec/agent-traces/toolchains/20261003T144810075586_fb8971ec_3dd090f6-06cf-4617-a381-0fe3e2a6a206_completed.json)。
  - 未通过 `/check/two_drones_distance`：参数 `{"drone_1_id": "a1f8089f", "drone_2_id": "530b5dfa", "max_distance": 19.0}`；响应值 `19.012`。
- **qwen：2/3，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/fb8971ec/82077301.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/fb8971ec/82077301.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/fb8971ec/82077301.agent-state.json)。
  API 调用 27 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/fb8971ec/agent-traces/toolchains/20261004T150359524051_fb8971ec_674f3ca1-20f3-4e76-9ae4-59eeb206ff7e_completed.json)。
  - 未通过 `/check/two_drones_distance`：参数 `{"drone_1_id": "a1f8089f", "drone_2_id": "530b5dfa", "max_distance": 19.0}`；响应值 `194.378`。

## cc05a425/25d30560 — 不可穿越障碍内的指定航点

场景：Area Assignment and Patrol Hard 1。任务序号：20/20。

> Drones Drone 7 and Drone 1 execute coordinated patrol. Both take off to 16 meters. Drone 7 patrols the route through waypoints (154,590,16), (792,483,16) while Drone 1 patrols the route through waypoints (288,205,16), (771,156,16).

- **deepseek：3/4，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/cc05a425/25d30560.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/cc05a425/25d30560.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/cc05a425/25d30560.agent-state.json)。
  API 调用 24 次；[完整工具/API轨迹](./experiments/runs/20261003-143255-full-5c501118/cc05a425/agent-traces/toolchains/20261003T152218375177_cc05a425_fdcde96b-1763-4325-a079-11ce1dffb6e6_completed.json)。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "e7de5308", "x": 288.0, "y": 205.0, "z": 16.0, "tolerance": 3.0}`；响应值 `0`。
- **qwen：3/4，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/cc05a425/25d30560.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/cc05a425/25d30560.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/cc05a425/25d30560.agent-state.json)。
  API 调用 23 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/cc05a425/agent-traces/toolchains/20261004T162420301307_cc05a425_92ba3216-b746-42d2-8b8b-20b486afe985_completed.json)。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "e7de5308", "x": 288.0, "y": 205.0, "z": 16.0, "tolerance": 3.0}`；响应值 `0`。

## c350908c/f30cc643 — 指令高度与起飞检查阈值不一致

场景：Area Assignment and Patrol Hard 2。任务序号：1/20。

> Drone Drone 17 complete flight cycle test: verify full operational capability by taking off to 1 meters (must achieve takeoff), fly to waypoint (532, 516, 1), hover briefly, then return home and land properly. This validates complete flight cycle.

- **deepseek：3/4，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/c350908c/f30cc643.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/c350908c/f30cc643.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/c350908c/f30cc643.agent-state.json)。
  API 调用 46 次；[完整工具/API轨迹](./experiments/runs/20261003-143255-full-5c501118/c350908c/agent-traces/toolchains/20261003T145143762075_c350908c_06cb8158-cb3f-463f-b6b1-d166a0c70e52_completed.json)。
  - 未通过 `/check/drone_has_taken_off`：参数 `{"drone_id": "fa625df4", "min_altitude": 5.0}`；响应值 `0`。
- **qwen：3/4，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/c350908c/f30cc643.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/c350908c/f30cc643.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/c350908c/f30cc643.agent-state.json)。
  API 调用 60 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/c350908c/agent-traces/toolchains/20261004T151711756955_c350908c_b031f8d5-4134-4a41-9d3b-23b074101f18_completed.json)。
  - 未通过 `/check/drone_has_taken_off`：参数 `{"drone_id": "fa625df4", "min_altitude": 5.0}`；响应值 `0`。

## 76802002/55b61526 — 真实目标未被发现

场景：Area Assignment and Patrol Hard 3。任务序号：7/20。

> Drone Drone 14 performs sequential multi-target mission: take off to 24 meters, visit targets Polygon Target 1, Circle Target 4, and Waypoint 1 in sequence. At each target, get within the target radius, hover for observation, and take a photo. Plan optimal route to minimize flight distance.

- **deepseek：2/3，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/76802002/55b61526.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/76802002/55b61526.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/76802002/55b61526.agent-state.json)。
  API 调用 201 次；[完整工具/API轨迹](./experiments/runs/20261003-143255-full-5c501118/76802002/agent-traces/toolchains/20261003T150622792025_76802002_80eca5ee-83be-4d89-8bf2-aeb70011bc54_completed.json)。
  - 未通过 `/check/target_is_reached`：参数 `{"target_id": "7c7a6f6d"}`；响应值 `0`。
- **qwen：2/3，超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/76802002/55b61526.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/76802002/55b61526.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/76802002/55b61526.agent-state.json)。
  API 调用 73 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/76802002/agent-traces/toolchains/20261004T153438687092_76802002_f7f29ecb-6548-4a5f-a26f-4a01b7571f00_exception.json)。
  - 未通过 `/check/target_is_reached`：参数 `{"target_id": "7c7a6f6d"}`；响应值 `0`。

## 35ce64d8/cf8ee51f — 38/39：长航线后的降落收尾

场景：Target Assignment Moderate 1。任务序号：20/20。

> Drones Drone 4, Drone 9, and Drone 7 perform a triple marathon flight at 27 meters. Drone 4 route: fly to and start at  (65, 120, 27), then waypoints (169, 144, 27), (829, 235, 27), (181, 320, 27), (806, 354, 27), (155, 432, 27), (924, 566, 27), (123, 623, 27), (816, 725, 27), (316, 781, 27), (635, 872, 27), then end (944, 891, 27) and land. Drone 9 route: fly to and start at  (486, 51, 27), then waypoints (448, 211, 27), (886, 268, 27), (174, 367, 27), (858, 397, 27), (114, 507, 27), (866, 577, 27), (116, 683, 27), (807, 776, 27), (320, 814, 27), (635, 899, 27), then end (560, 901, 27) and land. Drone 7 route: fly to and start at  (905, 80, 27), then waypoints (830, 207, 27), (171, 180, 27), (920, 299, 27), (162, 400, 27), (843, 468, 27), (113, 568, 27), (818, 604, 27), (240, 739, 27), (832, 853, 27), (292, 920, 27), then end (67, 885, 27) and land.

- **deepseek：39/39，未超时，generation 2**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/retries/35ce64d8/0002/cf8ee51f.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/retries/35ce64d8/0002/cf8ee51f.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/retries/35ce64d8/0002/cf8ee51f.agent-state.json)。
- **qwen：38/39，超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cf8ee51f.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cf8ee51f.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cf8ee51f.agent-state.json)。
  API 调用 27 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/agent-traces/toolchains/20261005T013623403961_35ce64d8_1c254bc8-2832-4478-b557-68afd91f3073_exception.json)。
  - 未通过 `/check/drone_on_ground`：参数 `{"drone_id": "6126210f", "tolerance": 0.5}`；响应值 `27.0`。

## 4e425a5b/4cb91745 — 定向移动与净位移的区别

场景：Area Assignment and Patrol Moderate 1。任务序号：17/20。

> Four-drone target sweep: Drone 10, Drone 5, Drone 8, and Drone 3 take off together to 22 meters altitude. Drone 10 flies to target Polygon Target 1, Drone 5 flies to target Polygon Target 2, Drone 8 flies to target Polygon Target 3, and Drone 3 flies to target Waypoint 1. After reaching the targets, all four drones move west for 69m, then south for 55m, then north for 61m together. Each drone must complete the specified directional movements (west 69m, south 55m, north 61m) regardless of starting position - if obstacles block the path, drones should adjust position as needed to complete the full directed distance in each heading.

- **deepseek：8/16，超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/4e425a5b/4cb91745.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/4e425a5b/4cb91745.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/4e425a5b/4cb91745.agent-state.json)。
  API 调用 205 次；[完整工具/API轨迹](./experiments/runs/20261003-143255-full-5c501118/4e425a5b/agent-traces/toolchains/20261003T161932247926_4e425a5b_e58b3135-fa3c-498d-8e5d-a3968c84cc7e_exception.json)。
  - 未通过 `/check/target_is_reached_by_drone`：参数 `{"target_id": "01add8d8", "drone_id": "f6ffb4b5"}`；响应值 `0`。
  - 未通过 `/check/drone_has_moved_directed_distance`：参数 `{"drone_id": "70786039", "min_distance": 69.0, "heading": 270, "tolerance": 5.0}`；响应值 `0.0`。
  - 未通过 `/check/drone_has_moved_directed_distance`：参数 `{"drone_id": "63bde6ec", "min_distance": 69.0, "heading": 270, "tolerance": 5.0}`；响应值 `0.0`。
  - 未通过 `/check/drone_has_moved_directed_distance`：参数 `{"drone_id": "f6ffb4b5", "min_distance": 69.0, "heading": 270, "tolerance": 5.0}`；响应值 `0.0`。
  - 未通过 `/check/drone_has_moved_directed_distance`：参数 `{"drone_id": "db61ce50", "min_distance": 55.0, "heading": 180, "tolerance": 5.0}`；响应值 `0.0`。
  - 未通过 `/check/drone_has_moved_directed_distance`：参数 `{"drone_id": "db61ce50", "min_distance": 61.0, "heading": 0, "tolerance": 5.0}`；响应值 `0.0`。
  - 未通过 `/check/drone_has_moved_directed_distance`：参数 `{"drone_id": "70786039", "min_distance": 61.0, "heading": 0, "tolerance": 5.0}`；响应值 `0.0`。
  - 未通过 `/check/drone_has_moved_directed_distance`：参数 `{"drone_id": "63bde6ec", "min_distance": 61.0, "heading": 0, "tolerance": 5.0}`；响应值 `0.0`。
- **qwen：15/16，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/4e425a5b/4cb91745.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/4e425a5b/4cb91745.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/4e425a5b/4cb91745.agent-state.json)。
  API 调用 47 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/4e425a5b/agent-traces/toolchains/20261004T174836420933_4e425a5b_2aac7ab7-c32b-41c1-b9f6-1328385c1740_completed.json)。
  - 未通过 `/check/drone_has_moved_directed_distance`：参数 `{"drone_id": "f6ffb4b5", "min_distance": 55.0, "heading": 180, "tolerance": 5.0}`；响应值 `0.0`。

## 93831d6d/7af66f50 — 搜索失败与照片有效性：同题不同机制

场景：Area Assignment and Patrol Moderate 5。任务序号：6/20。

> Drone Drone 10 must take off to 17 meters, navigate to target Circle Target 1, take a photo, then navigate to target Waypoint 1 and take another photo.

- **deepseek：0/4，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/93831d6d/7af66f50.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/93831d6d/7af66f50.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/93831d6d/7af66f50.agent-state.json)。
  API 调用 160 次；[完整工具/API轨迹](./experiments/runs/20261003-143255-full-5c501118/93831d6d/agent-traces/toolchains/20261003T160741692722_93831d6d_2ab6ba85-e04f-4b86-914f-fd27dcfcffb3_completed.json)。
  - 未通过 `/check/target_is_reached_by_drone`：参数 `{"target_id": "544d5224", "drone_id": "153c7566"}`；响应值 `0`。
  - 未通过 `/check/target_in_photo_taken_by_drone`：参数 `{"target_id": "544d5224", "drone_id": "153c7566"}`；响应值 `False`。
  - 未通过 `/check/target_is_reached_by_drone`：参数 `{"target_id": "0b66d055", "drone_id": "153c7566"}`；响应值 `0`。
  - 未通过 `/check/target_in_photo_taken_by_drone`：参数 `{"target_id": "0b66d055", "drone_id": "153c7566"}`；响应值 `False`。
- **qwen：3/4，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/93831d6d/7af66f50.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/93831d6d/7af66f50.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/93831d6d/7af66f50.agent-state.json)。
  API 调用 72 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/93831d6d/agent-traces/toolchains/20261004T174950827678_93831d6d_e679d1b7-d02e-4624-875b-987ad1d2eb7e_completed.json)。
  - 未通过 `/check/target_in_photo_taken_by_drone`：参数 `{"target_id": "544d5224", "drone_id": "153c7566"}`；响应值 `False`。

## 8b7866a0/87072bfa — 零通过单检查，但实际进度77%/81%

场景：Area Assignment and Patrol Extreme 2。任务序号：20/20。

> Take off necessary drones and search the assigned area targets with systematic coverage patterns until the session task progress exceeds 95%.

- **deepseek：0/1，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/8b7866a0/87072bfa.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/8b7866a0/87072bfa.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/8b7866a0/87072bfa.agent-state.json)。
  API 调用 87 次；[完整工具/API轨迹](./experiments/runs/20261003-143255-full-5c501118/8b7866a0/agent-traces/toolchains/20261003T153238945500_8b7866a0_df883ac7-fe9a-4052-865f-3939034a37a7_completed.json)。
  - 未通过 `/check/task_progress`：参数 `{"expected_progress": 0.95}`；响应值 `0.77`。
- **qwen：0/1，超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/8b7866a0/87072bfa.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/8b7866a0/87072bfa.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/8b7866a0/87072bfa.agent-state.json)。
  API 调用 124 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/8b7866a0/agent-traces/toolchains/20261004T163121011987_8b7866a0_4b53f7a4-6a77-4d2d-bbe3-f41f8481dc4c_exception.json)。
  - 未通过 `/check/task_progress`：参数 `{"expected_progress": 0.95}`；响应值 `0.81`。

## 3973d0d6/9c0b52a0 — 把已发现目标数量当作全部目标

场景：Target Assignment Easy 5。任务序号：14/20。

> take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%.

- **deepseek：1/1，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/3973d0d6/9c0b52a0.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/3973d0d6/9c0b52a0.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/3973d0d6/9c0b52a0.agent-state.json)。
- **qwen：0/1，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/3973d0d6/9c0b52a0.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/3973d0d6/9c0b52a0.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/3973d0d6/9c0b52a0.agent-state.json)。
  API 调用 134 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/3973d0d6/agent-traces/toolchains/20261004T222725787812_3973d0d6_f98e2ab0-29a0-4fef-b1a6-20161323bae3_completed.json)。
  - 未通过 `/check/task_progress`：参数 `{"expected_progress": 0.95}`；响应值 `0.66`。

## 228ef1f0/8ec127f6 — 感知与搜索消耗预算

场景：Area Assignment and Patrol Extreme 1。任务序号：7/20。

> Drone Drone 12 must take off to 17 meters, navigate to target Circle Target 4, and take a photo.

- **deepseek：0/2，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/228ef1f0/8ec127f6.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/228ef1f0/8ec127f6.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/228ef1f0/8ec127f6.agent-state.json)。
  API 调用 116 次；[完整工具/API轨迹](./experiments/runs/20261003-143255-full-5c501118/228ef1f0/agent-traces/toolchains/20261003T145225498451_228ef1f0_5fe28242-39ce-40bf-84be-092bb71c4405_completed.json)。
  - 未通过 `/check/target_is_reached_by_drone`：参数 `{"target_id": "fc8ce0b8", "drone_id": "9e980cc4"}`；响应值 `0`。
  - 未通过 `/check/target_in_photo_taken_by_drone`：参数 `{"target_id": "fc8ce0b8", "drone_id": "9e980cc4"}`；响应值 `False`。
- **qwen：0/2，超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/228ef1f0/8ec127f6.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/228ef1f0/8ec127f6.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/228ef1f0/8ec127f6.agent-state.json)。
  API 调用 151 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/228ef1f0/agent-traces/toolchains/20261004T150500755216_228ef1f0_e3b6e45f-0a63-42f9-ad54-22cde66228f2_exception.json)。
  - 未通过 `/check/target_is_reached_by_drone`：参数 `{"target_id": "fc8ce0b8", "drone_id": "9e980cc4"}`；响应值 `0`。
  - 未通过 `/check/target_in_photo_taken_by_drone`：参数 `{"target_id": "fc8ce0b8", "drone_id": "9e980cc4"}`；响应值 `False`。

## 29ec452f/0dab4a45 — 错误无人机绑定

场景：Area Search Extreme 1。任务序号：3/20。

> Drone Drone 20 takes off to 41 meters, flies to (668, 695, 41), loiters for 7 seconds, and captures a photo.

- **deepseek：3/3，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/29ec452f/0dab4a45.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/29ec452f/0dab4a45.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/29ec452f/0dab4a45.agent-state.json)。
- **qwen：0/3，未超时，generation 2**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/retries/29ec452f/0002/0dab4a45.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/retries/29ec452f/0002/0dab4a45.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/retries/29ec452f/0002/0dab4a45.agent-state.json)。
  API 调用 11 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/retries/29ec452f/0002/agent-traces/toolchains/20261005T023910737470_29ec452f_db4370f0-4947-4dfc-bd9b-292bd5d0c308_completed.json)。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "39b9630c", "x": 668.0, "y": 695.0, "z": 41.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_taken_photo`：参数 `{"drone_id": "39b9630c", "min_count": 1}`；响应值 `0`。
  - 未通过 `/check/drone_hovering`：参数 `{"drone_id": "39b9630c", "tolerance": 1}`；响应值 `idle`。

## ef36e27a/4ccc9027 — 错误目标与错误coverage_plan_id

场景：Area Search Hard 1。任务序号：5/20。

> Drone Drone 4 executes comprehensive area search of target area Circle Target 2. Take off to 20 meters and perform a systematic coverage pattern to search at least 95% of the target area using an efficient serpentine or spiral pattern.

- **deepseek：1/1，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/ef36e27a/4ccc9027.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/ef36e27a/4ccc9027.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/ef36e27a/4ccc9027.agent-state.json)。
- **qwen：0/1，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/ef36e27a/4ccc9027.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/ef36e27a/4ccc9027.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/ef36e27a/4ccc9027.agent-state.json)。
  API 调用 20 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/ef36e27a/agent-traces/toolchains/20261004T185049382407_ef36e27a_fc55bb83-280f-49ba-8370-0cfe140fb695_completed.json)。
  - 未通过 `/check/target_is_fully_searched`：参数 `{"target_id": "588c92a7", "coverage_threshold": 0.95}`；响应值 `0.0`。

## 9ddae16c/649bca01 — 完成前提前终止

场景：Area Search Easy 3。任务序号：1/20。

> Drone Drone 1 should take off to 13 meters, then climb to 26 meters altitude and hover.

- **deepseek：2/2，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/9ddae16c/649bca01.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/9ddae16c/649bca01.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/9ddae16c/649bca01.agent-state.json)。
- **qwen：0/2，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/9ddae16c/649bca01.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/9ddae16c/649bca01.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/9ddae16c/649bca01.agent-state.json)。
  API 调用 4 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/9ddae16c/agent-traces/toolchains/20261004T174618889337_9ddae16c_8b368f8c-4575-4b67-b82d-ffa3901ad0a0_failed.json)。
  - 未通过 `/check/drone_altitude`：参数 `{"drone_id": "b5c873c5", "expected_altitude": 26.0, "tolerance": 2.0}`；响应值 `0.0`。
  - 未通过 `/check/drone_hovering`：参数 `{"drone_id": "b5c873c5", "tolerance": 0.5}`；响应值 `idle`。

## 35ce64d8/cba90ef3 — 未执行动作就终止

场景：Target Assignment Moderate 1。任务序号：10/20。

> Communication relay mission: Drone Drone 4 acts as communication relay. Take off to 21 meters, position at relay point (495, 543, 21). Send broadcast messages to coordinate with other drones. Send at least 5 messages total during the mission.

- **deepseek：2/2，未超时，generation 2**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/retries/35ce64d8/0002/cba90ef3.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/retries/35ce64d8/0002/cba90ef3.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/retries/35ce64d8/0002/cba90ef3.agent-state.json)。
- **qwen：0/2，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cba90ef3.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cba90ef3.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/cba90ef3.agent-state.json)。
  API 调用 1 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/35ce64d8/agent-traces/toolchains/20261005T004708271536_35ce64d8_eeee78e0-6657-4542-8dbe-eb9d381f0e9a_failed.json)。
  - 未通过 `/check/drone_has_sent_message`：参数 `{"drone_id": "be69a550", "min_count": 5.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "be69a550", "x": 495.0, "y": 543.0, "z": 21.0, "tolerance": 3.0}`；响应值 `0`。

## 640b66d4/d46027e4 — 长路线起点前超时与电量不足

场景：Area Assignment and Patrol Extreme 4。任务序号：20/20。

> Drone 22 performs an extreme marathon flight. Take off to 30 meters, fly to start point (53, 102, 30), then zigzag across the full map through 20 intermediate waypoints: (124, 150, 30), (866, 157, 30), (128, 268, 30), (826, 291, 30), (108, 457, 30), (915, 517, 30), (217, 551, 30), (811, 638, 30), (155, 743, 30), (847, 788, 30), (149, 864, 30), (803, 931, 30), (452, 785, 30), (955, 544, 30), (87, 581, 30), (922, 397, 30), (78, 301, 30), (772, 88, 30), (342, 912, 30), (633, 526, 30). Avoid obstacles Ellipse Obstacle 4, Polygon Obstacle 23, and Point Obstacle 8. Finally, fly to end point (910, 948, 30) and land.

- **deepseek：16/23，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/640b66d4/d46027e4.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/640b66d4/d46027e4.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/640b66d4/d46027e4.agent-state.json)。
  API 调用 44 次；[完整工具/API轨迹](./experiments/runs/20261003-143255-full-5c501118/640b66d4/agent-traces/toolchains/20261003T155154454494_640b66d4_51227b0b-d8f6-4144-9c60-d34f3bf0f5f7_completed.json)。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 87.0, "y": 581.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 922.0, "y": 397.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 78.0, "y": 301.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 772.0, "y": 88.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 342.0, "y": 912.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 633.0, "y": 526.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_position`：参数 `{"drone_id": "5799c486", "x": 910.0, "y": 948.0, "z": 0, "tolerance": 3.0}`；响应值 `1007.692`。
- **qwen：0/23，超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/640b66d4/d46027e4.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/640b66d4/d46027e4.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/640b66d4/d46027e4.agent-state.json)。
  API 调用 29 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/640b66d4/agent-traces/toolchains/20261004T164728192237_640b66d4_db8acd4f-1f9b-4319-a927-15fec7c14728_exception.json)。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 53.0, "y": 102.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 124.0, "y": 150.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 866.0, "y": 157.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 128.0, "y": 268.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 826.0, "y": 291.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 108.0, "y": 457.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 915.0, "y": 517.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 217.0, "y": 551.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 811.0, "y": 638.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 155.0, "y": 743.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 847.0, "y": 788.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 149.0, "y": 864.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 803.0, "y": 931.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 452.0, "y": 785.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 955.0, "y": 544.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 87.0, "y": 581.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 922.0, "y": 397.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 78.0, "y": 301.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 772.0, "y": 88.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 342.0, "y": 912.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_has_visited_position`：参数 `{"drone_id": "5799c486", "x": 633.0, "y": 526.0, "z": 30.0, "tolerance": 3.0}`；响应值 `0`。
  - 未通过 `/check/drone_position`：参数 `{"drone_id": "5799c486", "x": 910.0, "y": 948.0, "z": 0, "tolerance": 3.0}`；响应值 `740.131`。
  - 未通过 `/check/drone_on_ground`：参数 `{"drone_id": "5799c486", "tolerance": 0.5}`；响应值 `30.0`。

## ad34350a/bfc721cc — 覆盖任务局部完成：76.74%而非85%

场景：Area Assignment and Patrol Extreme 5。任务序号：14/20。

> Drones Drone 22, Drone 6, and Drone 30 execute coordinated search sweep of target area Circle Target 3. All take off to 24 meters. Drones maintain parallel formation 21.4 meters apart and sweep through the target area in synchronized pattern. Each drone takes photos during sweep.

- **deepseek：4/4，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/ad34350a/bfc721cc.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/ad34350a/bfc721cc.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/ad34350a/bfc721cc.agent-state.json)。
- **qwen：3/4，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/ad34350a/bfc721cc.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/ad34350a/bfc721cc.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/ad34350a/bfc721cc.agent-state.json)。
  API 调用 148 次；[完整工具/API轨迹](./experiments/runs/20261004-142512-full-aca112a7/ad34350a/agent-traces/toolchains/20261004T161602952885_ad34350a_615c3813-0fef-45e1-922a-0f6f4769aa5b_completed.json)。
  - 未通过 `/check/target_searched_area_percentage`：参数 `{"target_id": "e5336486", "expected_percentage": 0.85}`；响应值 `0.7674486803519062`。

## 793e7745/f817698f — 定向距离数值边界候选

场景：Area Search Extreme 4。任务序号：17/20。

> Three-drone target sweep: Drone 8, Drone 20, and Drone 13 take off together to 17 meters altitude. Drone 8 flies to target Polygon Target 3, Drone 20 flies to target Polygon Target 10, and Drone 13 flies to target Polygon Target 2. After reaching the targets, all three drones move west for 44m, then south for 40m, then north for 80m together. Each drone must complete the specified directional movements (west 44m, south 40m, north 80m) regardless of starting position - if obstacles block the path, drones should adjust position as needed to complete the full directed distance in each heading.

- **deepseek：11/12，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261003-143255-full-5c501118/793e7745/f817698f.job.json) · [原检查响应](./experiments/runs/20261003-143255-full-5c501118/793e7745/f817698f.checks.json) · [黑板与结束状态](./experiments/runs/20261003-143255-full-5c501118/793e7745/f817698f.agent-state.json)。
  API 调用 80 次；[完整工具/API轨迹](./experiments/runs/20261003-143255-full-5c501118/793e7745/agent-traces/toolchains/20261003T171410182271_793e7745_e9e4c809-93eb-4076-8b2e-8a4af70d9ea7_completed.json)。
  - 未通过 `/check/drone_has_moved_directed_distance`：参数 `{"drone_id": "015f5650", "min_distance": 80.0, "heading": 0, "tolerance": 5.0}`；响应值 `80.0`。
- **qwen：12/12，未超时，generation 1**。 [任务与工具步骤](./experiments/runs/20261004-142512-full-aca112a7/793e7745/f817698f.job.json) · [原检查响应](./experiments/runs/20261004-142512-full-aca112a7/793e7745/f817698f.checks.json) · [黑板与结束状态](./experiments/runs/20261004-142512-full-aca112a7/793e7745/f817698f.agent-state.json)。

