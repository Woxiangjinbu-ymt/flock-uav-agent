# 扩充前后覆盖

题目按场景 ID 和任务 ID 去重；决策按实际训练行去重。分母为 A 折 12 场的 240 道源题。题型按原任务名称归类，仅合并末尾的实例序号，保留机队规模、任务进度阈值等区别。

## 场景任务族

| 类别 | 源题 | 入训题（前 → 后） | 去重决策（前 → 后） | 新增决策 |
|---|---:|---:|---:|---:|
| area_assignment_and_patrol | 80 | 30 → 32 | 141 → 150 | +9 |
| area_search | 80 | 39 → 39 | 117 → 135 | +18 |
| target_assignment | 80 | 41 → 42 | 129 → 146 | +17 |

## 场景

| 类别 | 源题 | 入训题（前 → 后） | 去重决策（前 → 后） | 新增决策 |
|---|---:|---:|---:|---:|
| 04cd6269 | 20 | 16 → 16 | 118 → 120 | +2 |
| 0567e470 | 20 | 8 → 10 | 15 → 22 | +7 |
| 193e179a | 20 | 4 → 4 | 6 → 8 | +2 |
| 25239af9 | 20 | 13 → 13 | 57 → 65 | +8 |
| 35ce64d8 | 20 | 9 → 10 | 19 → 24 | +5 |
| 58fe4605 | 20 | 15 → 15 | 55 → 59 | +4 |
| 5b8dd44e | 20 | 12 → 12 | 33 → 38 | +5 |
| 5be2589b | 20 | 4 → 4 | 6 → 6 | +0 |
| 640b66d4 | 20 | 2 → 2 | 2 → 2 | +0 |
| 793e7745 | 20 | 6 → 6 | 9 → 11 | +2 |
| 7f1a1456 | 20 | 13 → 13 | 49 → 55 | +6 |
| ef36e27a | 20 | 8 → 8 | 18 → 21 | +3 |

## 题型

| 类别 | 源题 | 入训题（前 → 后） | 去重决策（前 → 后） | 新增决策 |
|---|---:|---:|---:|---:|
| Any Drone Area Coverage Search | 1 | 0 → 0 | 0 → 0 | +0 |
| Any Drone Two-Target Area Coverage Search | 3 | 0 → 0 | 0 → 0 | +0 |
| Area Search Task Completion | 6 | 0 → 0 | 0 → 0 | +0 |
| Basic Takeoff | 3 | 2 → 2 | 7 → 7 | +0 |
| Communication Relay with Messages | 4 | 3 → 3 | 16 → 18 | +2 |
| Complete Area Coverage Search | 13 | 3 → 3 | 5 → 6 | +1 |
| Complete Flight Cycle Verification | 2 | 2 → 2 | 15 → 15 | +0 |
| Complex Obstacle Course Navigation | 7 | 6 → 6 | 9 → 11 | +2 |
| Coordinated Search Sweep - 3 Drones | 2 | 1 → 1 | 1 → 1 | +0 |
| Delivery to Specific Location | 7 | 6 → 6 | 21 → 24 | +3 |
| Dual Drone Area Coverage Search | 8 | 1 → 1 | 6 → 6 | +0 |
| Dual Drone Patrol | 4 | 1 → 1 | 1 → 4 | +3 |
| Dual Flight Marathon Navigation | 7 | 0 → 0 | 0 → 0 | +0 |
| Extended Distance Flight Test | 7 | 6 → 7 | 19 → 24 | +5 |
| Extended Hover Observation | 3 | 0 → 0 | 0 → 0 | +0 |
| Extreme Flight Marathon Navigation | 4 | 0 → 0 | 0 → 0 | +0 |
| Fleet Coordinated Takeoff and Formation (3 Drones) | 8 | 4 → 4 | 13 → 14 | +1 |
| Fleet Coordinated Takeoff and Formation (4 Drones) | 1 | 0 → 0 | 0 → 0 | +0 |
| Fleet Coordinated Takeoff and Formation (5 Drones) | 3 | 0 → 0 | 0 → 0 | +0 |
| Fleet Target Sweep (3 Drones) | 5 | 2 → 2 | 2 → 2 | +0 |
| Fleet Target Sweep (4 Drones) | 4 | 1 → 1 | 1 → 1 | +0 |
| Fleet Target Sweep (5 Drones) | 2 | 1 → 1 | 1 → 1 | +0 |
| Flight Marathon Navigation | 5 | 2 → 2 | 6 → 6 | +0 |
| Fly Several Waypoints | 3 | 3 → 3 | 22 → 25 | +3 |
| Line Formation - 2 Drones | 11 | 7 → 7 | 25 → 28 | +3 |
| Multi-Target Reach Verification | 6 | 4 → 4 | 14 → 17 | +3 |
| Multi-Target Survey Mission | 5 | 2 → 2 | 8 → 8 | +0 |
| Multi-Waypoint Delivery Route | 6 | 5 → 5 | 50 → 55 | +5 |
| Multiple Target Inspection | 8 | 3 → 3 | 11 → 12 | +1 |
| Multiple Target Inspection - Advanced | 8 | 3 → 3 | 10 → 12 | +2 |
| Obstacle Avoidance Navigation | 6 | 5 → 6 | 12 → 14 | +2 |
| Patrol Mission - Quadrangle | 6 | 2 → 3 | 9 → 12 | +3 |
| Perimeter Search - 4 Drones | 5 | 4 → 4 | 7 → 7 | +0 |
| Photo Documentation Mission | 3 | 2 → 2 | 14 → 14 | +0 |
| Quick Checkpoint Visit | 4 | 3 → 3 | 8 → 10 | +2 |
| Randomized Loiter with Photo | 4 | 1 → 1 | 1 → 3 | +2 |
| Search Task with Photo | 5 | 5 → 5 | 29 → 29 | +0 |
| Sequential Multi-Target Visit | 12 | 0 → 0 | 0 → 0 | +0 |
| Simple Altitude Change | 4 | 3 → 3 | 3 → 3 | +0 |
| Simple Rotation Test | 6 | 5 → 5 | 12 → 12 | +0 |
| Target Area Complete Coverage and Photography Mission | 4 | 2 → 2 | 4 → 5 | +1 |
| Target Assignment 50% Progress With 3 Targets | 4 | 0 → 0 | 0 → 0 | +0 |
| Target Assignment 70% Progress With 5 Targets | 2 | 0 → 0 | 0 → 0 | +0 |
| Target Assignment Task Completion | 2 | 1 → 1 | 1 → 1 | +0 |
| Target Inspection | 7 | 5 → 5 | 11 → 11 | +0 |
| Triangle Formation - 3 Drones | 7 | 3 → 3 | 12 → 12 | +0 |
| Triple Drone Area Coverage Search | 1 | 0 → 0 | 0 → 0 | +0 |
| Triple Flight Marathon Navigation | 1 | 0 → 0 | 0 → 0 | +0 |
| Two Find Two Support | 1 | 1 → 1 | 1 → 1 | +0 |

## 决策类型

沿用训练索引的 kind 标签，通用工具决策可能也包含纠错或核验行为。

| 类型 | 去重决策（前 → 后） | 覆盖题（前 → 后） |
|---|---:|---:|
| action | 4 → 4 | 4 → 4 |
| closing | 11 → 21 | 11 → 21 |
| correction | 3 → 30 | 3 → 23 |
| tool_decision | 364 → 364 | 106 → 106 |
| verification | 5 → 12 | 5 → 12 |
