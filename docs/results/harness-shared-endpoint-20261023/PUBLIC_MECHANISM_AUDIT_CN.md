# v5 共享目的点负证据：独立公开轨迹审计

2026-10-23；对象为封存的 `run-20261023-01`（7 题 × v4/v5）。只读取公开 API 轨迹、Harness 记录与封存源码，原 checker 的布尔结果只作结果引用。不运行模型/仿真，不修改封存内容。逐条来源、原始请求号/JSONL 行号、检查结果与 29 个输入文件 SHA-256 见 [机器记录](PUBLIC_MECHANISM_AUDIT.json)。输入均与 campaign seal 匹配，读取结束再次核对未变。

**结论：21 条共享 mask（24ac 14 条、b1ad 7 条）全部有一致的原 actor、原 move 回执与未改变的后读 XYZ；两题首次动作分歧都发生在另一 actor 已公开证实阻塞的精确点。** 未发现误把路径错误共享为目的点错误、来源引用错位、先用后记录或候选轨迹中在登记后再次发送同 XYZ 的情况。功能修复与任务结果须分别报告：24ac 仍 38%/检查失败，b1ad 80%→100%/检查通过，但后者运行状态仍因旧进度缓存报 `no_public_candidate`，且执行成本增加。

## 1. 共享 mask 的逐条核验

| 项目 | 24ac1838 | b1ad4f3e |
| --- | ---: | ---: |
| 最终共享 mask | 14 | 7 |
| 原 move evidence_ref、调用参数、回执字段与回执摘要一致 | 14/14 | 7/7 |
| 明确支持的目的点全文错误、command/actor 一致 | 14/14 | 7/7 |
| 源请求前后公开 XYZ 完全相同 | 14/14 | 7/7 |
| 后状态回执已确认，再发布 mask | 14/14 | 7/7 |
| 同计划、同场景/epoch、完整 XYZ 且 z=20 | 14/14 | 7/7 |
| 登记后任意 actor 再发该精确 XYZ 的 move 数 | 0 | 0 |

每条记录从 `evidence_ref` 找到 Harness `public_call_start/end`，再用 command_id 唯一定位公开 move；保存原 actor、candidate index、完整目标 XYZ。原回执字段逐项匹配，Harness 回执 SHA 对上归档动作回执；后读状态也通过独立 call-end 摘要匹配，并确认发生在首次含该 mask 的 `plan_update` 之前。前后状态为同一 actor、同一 plan，期间没有别的该机动作。其余五个任务没有共享 mask。

本批所有实际搜索 move 的请求高度均为 20。轨迹证明本批没有跨计划或扩大高度的执行现象；候选未发送的点不是可直接观察的反事实。精确匹配、不扩大到邻点/区域及新计划隔离，还须结合[契约审计](CONTRACT_AUDIT_CN.md)、[专项测试](SHARED_ENDPOINT_TESTS.json)与冻结源码解释；这不是多高度运行或动态地图验证。

## 2. 两题首次分歧

| 任务 | 前缀 | 已有公开来源 | 首次不同动作 | 实际替代与回执 |
| --- | --- | --- | --- | --- |
| 24ac1838 | 前 137 个动作相同 | actor `869a8e16`，#313，ref `e563`，目的点 `(1715,1155,20)`，Polygon Obstacle 7 | 第 138 动作，两臂 #565；actor `92e6a7b8`、公开起点均 `(1505,1225,20)` | v4 再发原点并发生目的点错误；v5 改去 `(1505,1295,20)`，成功 |
| b1ad4f3e | 前 36 个动作相同 | actor `2a266de6`，#142，ref `e251`，目的点 `(175,665,20)`，Polygon Obstacle 9 | 第 37 动作，两臂 #151；actor `60892444`、公开起点均 `(105,525,20)` | v4 再发原点并发生目的点错误；v5 改去 `(175,595,20)`，成功 |

上述源 mask 的发布都早于替代动作开始；源 actor 与执行替代的 actor 不同。来源见 [24ac 源失败 #313](run-20261023-01/trials/24ac1838-shared_endpoint_v5/public-api.jsonl#L313)、[v4 #565](run-20261023-01/trials/24ac1838-blocked_candidate_v4/public-api.jsonl#L565)、[v5 #565](run-20261023-01/trials/24ac1838-shared_endpoint_v5/public-api.jsonl#L565)；[b1ad 源失败 #142](run-20261023-01/trials/b1ad4f3e-shared_endpoint_v5/public-api.jsonl#L142)、[v4 #151](run-20261023-01/trials/b1ad4f3e-blocked_candidate_v4/public-api.jsonl#L151)、[v5 #151](run-20261023-01/trials/b1ad4f3e-shared_endpoint_v5/public-api.jsonl#L151)。

这支持首次分歧来自共享的精确目的点排除。后续路线和覆盖均随之变化，不能把整个后续任务分数或成本分别当成每条 mask 的独立因果收益。

## 3. 后续新增失败与残余问题

失败签名按明确目的点错误使用 actor+XYZ，路径错误另含公开起点 XYZ；“新增”指相对本批 v4 签名集合新增，不代表统计独立样本。障碍名称直接取回执，不据名字推断几何。

| 任务/请求 | actor 与公开起点 | 目的点 | 公开失败类型 |
| --- | --- | --- | --- |
| [24ac #640](run-20261023-01/trials/24ac1838-shared_endpoint_v5/public-api.jsonl#L640) | `318da56a`，`(945,1225,20)` | `(875,1155,20)` | 新目的点冲突，Circle Obstacle 7 |
| [b1ad #405](run-20261023-01/trials/b1ad4f3e-shared_endpoint_v5/public-api.jsonl#L405) | `63e09994`，`(665,35,20)` | `(945,525,20)` | 新路径冲突，Circle Obstacle 3；不得共享为点阻塞 |
| [b1ad #413](run-20261023-01/trials/b1ad4f3e-shared_endpoint_v5/public-api.jsonl#L413) | `b6654aad`，`(875,315,20)` | `(1015,525,20)` | 新路径冲突，Circle Obstacle 3；不得共享为点阻塞 |

24ac 两臂均为 14 次目的点错误+3 次路径错误；共享消除了已知跨机重复，但新点失败补上原 17 次错误上限，仍在 repairs=17、budget=16 时退出。公开进度都是 38/100，原 checker 都失败；因此不能称“障碍问题已解决”或“任务完成改善”。

b1ad 从 15 次目的点错误+2 次路径错误变成 7+3，总错误 17→10；不再耗尽修复预算，后续达到 20/20，原 checker 通过。全体三个搜索任务结果是 0/3→1/3；另一个搜索任务 `7eba7390` 仍为 1/3 目标、33%，16 轮后退出，两臂动作相同。两条短技能正对照及两个不支持语法对照也没有动作变化。

## 4. b1ad 的任务成功与技能终止不一致

1. 技能最后一次 progress 读取为 [#772](run-20261023-01/trials/b1ad4f3e-shared_endpoint_v5/public-api.jsonl#L772)：95%，19/20，剩余 1；当时 cursor=170。
2. 此后还有六次 move：#774、#779、#783、#787、#800、#807，最终 cursor=176。[#800](run-20261023-01/trials/b1ad4f3e-shared_endpoint_v5/public-api.jsonl#L800) 的 actor `04315908` 成功回执报告 `Fixed Target 7, Fixed Target 20`。`Fixed Target 20` 是成功 move 回执中第 20 个首次出现的目标名；此前公开 nearby [#161](run-20261023-01/trials/b1ad4f3e-shared_endpoint_v5/public-api.jsonl#L161)、[#272](run-20261023-01/trials/b1ad4f3e-shared_endpoint_v5/public-api.jsonl#L272) 显示该目标 ID `87e28d07`、未到访。
3. 结合 #772 的 19/20 和结束后的公开 [#834](run-20261023-01/trials/b1ad4f3e-shared_endpoint_v5/public-api.jsonl#L834) 的 20/20，可推定 #800 已完成最后目标；但 **#800 时没有同步的 progress=100 读取**，不得把结束后评估读取写成策略当时已知。
4. 技能每 10 次尝试刷新 progress。176 后出现没有动作的空轮，`if not made_attempt and progress <= 95` 直接抛 `no_public_candidate`，绕过循环后的余数刷新；frame 留下缓存 95。源码见 [冻结 public_search](run-20261023-01/source/agent4drone/harness_skills.py#L359)。这是已有终止刷新路径的缺口，本批未修改，也不据此篡改原 checker=true 或重跑。

后续最小可验证问题是：无候选/空轮退出前，在原 deadline/API 预算内是否应做一次公开进度确认，并分别覆盖已达阈值、未达阈值、读取失败和预算不足。这里只记录问题，不把候选修复或新成绩写成已完成。

## 5. 解释边界

本审计不重复主协议的全量成本/静态隔离审计。所读结果中，24ac 动作 153→156、API 600→612；b1ad 动作 120→191、API 478→818。任务完成提升伴随更长执行，不能写成效率提高。所有解释只使用公开动作、状态、感知和进度回执；没有用私有地图、目标真值或 checker 条件指导策略，也没有从一个受控实例推断稳定泛化或模型能力提升。
