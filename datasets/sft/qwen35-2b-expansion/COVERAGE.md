# 431 轮基线与扩充版覆盖

分母为原训练侧 240 题及本批新增整场 60 题。每行按独立任务与分词后决策去重。

## 重点任务

| 类别 | 原源题 | 新源题 | 入训题（前 → 后） | 决策（前 → 后） | 净增决策 |
|---|---:|---:|---:|---:|---:|
| 4/5 机编队 | 4 | 4 | 0 → 2 | 0 → 6 | +6 |
| 其他 | 195 | 42 | 103 → 113 | 410 → 424 | +14 |
| 区域覆盖 | 30 | 9 | 6 → 14 | 17 → 31 | +14 |
| 机队目标扫掠 | 11 | 5 | 4 → 7 | 4 → 8 | +4 |

## 场景任务族

| 类别 | 原源题 | 新源题 | 入训题（前 → 后） | 决策（前 → 后） | 净增决策 |
|---|---:|---:|---:|---:|---:|
| area_assignment_and_patrol | 80 | 20 | 32 → 41 | 150 → 167 | +17 |
| area_search | 80 | 20 | 39 → 49 | 135 → 151 | +16 |
| target_assignment | 80 | 20 | 42 → 46 | 146 → 151 | +5 |

## 场景

| 类别 | 原源题 | 新源题 | 入训题（前 → 后） | 决策（前 → 后） | 净增决策 |
|---|---:|---:|---:|---:|---:|
| 04cd6269 | 20 | 0 | 16 → 16 | 120 → 120 | +0 |
| 0567e470 | 20 | 0 | 10 → 10 | 22 → 22 | +0 |
| 193e179a | 20 | 0 | 4 → 4 | 8 → 8 | +0 |
| 25239af9 | 20 | 0 | 13 → 13 | 65 → 65 | +0 |
| 35ce64d8 | 20 | 0 | 10 → 10 | 24 → 24 | +0 |
| 36cbddd1 | 0 | 20 | 0 → 9 | 0 → 17 | +17 |
| 58fe4605 | 20 | 0 | 15 → 15 | 59 → 59 | +0 |
| 5b8dd44e | 20 | 0 | 12 → 12 | 38 → 38 | +0 |
| 5be2589b | 20 | 0 | 4 → 4 | 6 → 6 | +0 |
| 640b66d4 | 20 | 0 | 2 → 2 | 2 → 2 | +0 |
| 6e1a4ad2 | 0 | 20 | 0 → 10 | 0 → 16 | +16 |
| 793e7745 | 20 | 0 | 6 → 6 | 11 → 11 | +0 |
| 79b207ff | 0 | 20 | 0 → 4 | 0 → 5 | +5 |
| 7f1a1456 | 20 | 0 | 13 → 13 | 55 → 55 | +0 |
| ef36e27a | 20 | 0 | 8 → 8 | 21 → 21 | +0 |

## 题型

| 类别 | 原源题 | 新源题 | 入训题（前 → 后） | 决策（前 → 后） | 净增决策 |
|---|---:|---:|---:|---:|---:|
| Any Drone Area Coverage Search | 1 | 1 | 0 → 0 | 0 → 0 | +0 |
| Any Drone Two-Target Area Coverage Search | 3 | 0 | 0 → 0 | 0 → 0 | +0 |
| Area Search Task Completion | 6 | 0 | 0 → 0 | 0 → 0 | +0 |
| Basic Takeoff | 3 | 0 | 2 → 2 | 7 → 7 | +0 |
| Communication Relay with Messages | 4 | 3 | 3 → 3 | 18 → 18 | +0 |
| Complete Area Coverage Search | 13 | 3 | 3 → 6 | 6 → 11 | +5 |
| Complete Flight Cycle Verification | 2 | 2 | 2 → 2 | 15 → 15 | +0 |
| Complex Obstacle Course Navigation | 7 | 2 | 6 → 6 | 11 → 11 | +0 |
| Coordinated Search Sweep - 3 Drones | 2 | 0 | 1 → 1 | 1 → 1 | +0 |
| Delivery to Specific Location | 7 | 1 | 6 → 6 | 24 → 24 | +0 |
| Dual Drone Area Coverage Search | 8 | 2 | 1 → 3 | 6 → 9 | +3 |
| Dual Drone Patrol | 4 | 2 | 1 → 1 | 4 → 4 | +0 |
| Dual Flight Marathon Navigation | 7 | 1 | 0 → 0 | 0 → 0 | +0 |
| Extended Distance Flight Test | 7 | 0 | 7 → 7 | 24 → 24 | +0 |
| Extended Hover Observation | 3 | 0 | 0 → 0 | 0 → 0 | +0 |
| Extreme Flight Marathon Navigation | 4 | 1 | 0 → 0 | 0 → 0 | +0 |
| Fleet Coordinated Takeoff and Formation (3 Drones) | 8 | 0 | 4 → 4 | 14 → 14 | +0 |
| Fleet Coordinated Takeoff and Formation (4 Drones) | 1 | 2 | 0 → 0 | 0 → 0 | +0 |
| Fleet Coordinated Takeoff and Formation (5 Drones) | 3 | 2 | 0 → 2 | 0 → 6 | +6 |
| Fleet Target Sweep (3 Drones) | 5 | 2 | 2 → 3 | 2 → 4 | +2 |
| Fleet Target Sweep (4 Drones) | 4 | 2 | 1 → 3 | 1 → 3 | +2 |
| Fleet Target Sweep (5 Drones) | 2 | 1 | 1 → 1 | 1 → 1 | +0 |
| Flight Marathon Navigation | 5 | 2 | 2 → 2 | 6 → 6 | +0 |
| Fly Several Waypoints | 3 | 1 | 3 → 4 | 25 → 26 | +1 |
| Line Formation - 2 Drones | 11 | 3 | 7 → 8 | 28 → 31 | +3 |
| Multi-Target Reach Verification | 6 | 0 | 4 → 4 | 17 → 17 | +0 |
| Multi-Target Survey Mission | 5 | 3 | 2 → 4 | 8 → 10 | +2 |
| Multi-Waypoint Delivery Route | 6 | 1 | 5 → 5 | 55 → 55 | +0 |
| Multiple Target Inspection | 8 | 2 | 3 → 3 | 12 → 12 | +0 |
| Multiple Target Inspection - Advanced | 8 | 2 | 3 → 4 | 12 → 13 | +1 |
| Obstacle Avoidance Navigation | 6 | 0 | 6 → 6 | 14 → 14 | +0 |
| Patrol Mission - Quadrangle | 6 | 4 | 3 → 4 | 12 → 14 | +2 |
| Perimeter Search - 4 Drones | 5 | 1 | 4 → 4 | 7 → 7 | +0 |
| Photo Documentation Mission | 3 | 1 | 2 → 2 | 14 → 14 | +0 |
| Quick Checkpoint Visit | 4 | 1 | 3 → 4 | 10 → 11 | +1 |
| Randomized Loiter with Photo | 4 | 0 | 1 → 1 | 3 → 3 | +0 |
| Search Task with Photo | 5 | 1 | 5 → 5 | 29 → 29 | +0 |
| Sequential Multi-Target Visit | 12 | 2 | 0 → 0 | 0 → 0 | +0 |
| Simple Altitude Change | 4 | 0 | 3 → 3 | 3 → 3 | +0 |
| Simple Rotation Test | 6 | 0 | 5 → 5 | 12 → 12 | +0 |
| Target Area Complete Coverage and Photography Mission | 4 | 2 | 2 → 4 | 5 → 9 | +4 |
| Target Assignment 50% Progress With 3 Targets | 4 | 1 | 0 → 0 | 0 → 0 | +0 |
| Target Assignment 70% Progress With 5 Targets | 2 | 1 | 0 → 1 | 0 → 2 | +2 |
| Target Assignment Task Completion | 2 | 0 | 1 → 1 | 1 → 1 | +0 |
| Target Inspection | 7 | 2 | 5 → 5 | 11 → 11 | +0 |
| Triangle Formation - 3 Drones | 7 | 2 | 3 → 5 | 12 → 14 | +2 |
| Triple Drone Area Coverage Search | 1 | 1 | 0 → 1 | 0 → 2 | +2 |
| Triple Flight Marathon Navigation | 1 | 0 | 0 → 0 | 0 → 0 | +0 |
| Two Find Two Support | 1 | 0 | 1 → 1 | 1 → 1 | +0 |

## 决策类型

| 类型 | 决策（前 → 后） | 净增 |
|---|---:|---:|
| action | 4 → 21 | +17 |
| closing | 21 → 21 | +0 |
| correction | 30 → 32 | +2 |
| tool_decision | 364 → 377 | +13 |
| verification | 12 → 18 | +6 |
