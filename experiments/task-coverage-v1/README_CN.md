# 已知开发覆盖集 v1

18 题，F1–F6 各 3 题；按预先声明的结构条件及原文件顺序选取，不按历史成绩或成本排名。
全部来自已暴露的旧 1500 题，不属于独立 train/dev/test。这里没有新模型执行结果。

| 族 | Task ID | Scene ID | 模板 | 可用机队 | 明确参与主体 | 可选子程序候选 |
| --- | --- | --- | --- | ---: | ---: | --- |
| F1 | c74d7020 | fb8971ec | Basic Takeoff | 5 | 1 | simple_chain |
| F1 | fed7d291 | 90e931b1 | Simple Rotation Test | 5 | 1 | simple_chain |
| F1 | 528d6d24 | 04cd6269 | Complete Flight Cycle Verification | 5 | 1 | 原工具 |
| F2 | 6c3f98ce | 5aabad33 | Quick Checkpoint Visit | 3 | 1 | 原工具 |
| F2 | 592bbf82 | 90e931b1 | Fly Several Waypoints | 5 | 1 | 原工具 |
| F2 | ba7cf7aa | 90e931b1 | Obstacle Avoidance Navigation | 5 | 1 | 原工具 |
| F3 | d8a9fc1d | 4aa67fd7 | Target Inspection | 5 | 1 | 原工具 |
| F3 | bcaf22cf | fb8971ec | Sequential Multi-Target Visit | 5 | 1 | 原工具 |
| F3 | afa92e74 | fb8971ec | Multiple Target Inspection - Advanced | 5 | 3 | 原工具 |
| F4 | 6012c8ca | fb8971ec | Complete Area Coverage Search | 5 | 1 | 原工具 |
| F4 | 368b8113 | fb8971ec | Dual Drone Area Coverage Search | 5 | 2 | 原工具 |
| F4 | 0c07d7db | fb8971ec | Target Area Complete Coverage and Photography Mission | 5 | 1 | 原工具 |
| F5 | 51a76de4 | fb8971ec | Multi-Target Survey Mission | 5 | 2 | 原工具 |
| F5 | f6b47310 | ff4fbc38 | Fleet Coordinated Takeoff and Formation (4 Drones) | 5 | 4 | 原工具 |
| F5 | fb68238f | 0f23ff1e | Fleet Target Sweep (5 Drones) | 5 | 5 | 原工具 |
| F6 | fdd21809 | be5d648b | Target Assignment Task Completion | 5 | 由模型选择 | public_search |
| F6 | 0883d483 | 7f1a1456 | Target Assignment 50% Progress With 3 Targets | 5 | 由模型选择 | 原工具 |
| F6 | 8b784ef3 | 90e931b1 | Area Search Task Completion | 5 | 由模型选择 | 原工具 |

原始场景 JSON 逐字节复制，原任务与 checker 原样保留。manifest 中的 original_task、checker 与场景全量状态仅供私有准备/隔离评分，不能放入模型提示。

本包尚未物化 snapshot/timeline，初态评分、恢复一致性及几何可解性待单独的本地 materialization 验收；不得运行后按成绩换题。

已明确的检查弱点记录在每题 checker_review：悬停、照片、指定主体、次序或同步义务未必被原 checker 完整检查。保留原分母，需要另记行为证据。

参与主体覆盖 1–5；所选场景可用机队是 3/5。旧池没有 1/2/4 机队场景，不删机制造这些规模。新生成划分另行覆盖。

开发暴露依据是两模型首次部署清单的身份与证据路径字段；额外人工/模块暴露未逐项穷尽，因此整池保守标为 known_development。

## 复核

```bash
python3 scripts/build_development_coverage.py verify --dataset experiments/task-coverage-v1
```

重建必须指定不存在的新目录。此清单不授权 provider、Teacher 采数或模型训练。
