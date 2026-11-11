# 每 trial 新进程的完整回合本地验收

日期：2026-10-25。本批只验收初态恢复 → Agent 输入与行动 → 终止 → 独立评分 → 下一回合。没有调用真实模型 provider、增加原任务预算、续跑旧 360 次队列或启动训练；不与之前的搜索优化或模型 I/O 批次合并成绩。

## 结论与范围

固定的 9 个本地回合全部完成预定生命周期，父协调器的 **96 项链路检查通过**。每个回合一个全新 worker，worker 真实退出并封存之后，再启动一个独立 scorer，共 18 个进程。成功、注入 provider 失败、超时、取消之后的四个 B 回合，其完整初态、限定身份映射后的实际模型请求、公开动作与原检查器结果均与独立成功基准相同。没有发现前回合状态或日志污染下一回合。

这是一个原任务上的受控生命周期验收，**不是 9 道 Benchmark，也不是自然模型的成功率或收益**。模型是本地脚本响应；实际 ChatOpenAI/SDK、Agent、Harness、公开 UAV API 和模拟器参与执行。6 个成功情形与 3 个预定失败情形不能写成“任务成功率 6/9”。

生产 Agent、Harness、FullWorld 与模型 I/O 归档源码均未因本轮改动。新增的是验收协调器、worker/scorer 和测试；归档仍默认关闭，本次仅在子进程中显式启用。

## 固定协议与证据

执行前冻结的 [协议](PROTOCOL_CN.md) 使用原 full100 fixture `b1eb0270`、场景 `ad34350a`：Drone 3 起飞至 14 米、转向 353 度并悬停。检查器原有两个叶子分别检查高度与朝向；不把 Agent 自报完成当作评分。

- 原 410 文件输入清单加载前核验；80 份运行源码冻结。原 fixture、timeline、任务与检查树保持只读。
- 派生初态仅在私有检查树根节点增加不参与评分的 canary 元数据。去掉该键后与原树一致，公开 startup hash 与原件一致；所有回合使用相同派生初态。
- 预算仍为 500 秒、每计划 2,000 API、16 次 repair、16 轮 search、600 工具步、1,800 递归，SDK 零重试。正常虚拟时间固定；超时情形在真实起飞提交后推进到原 500 秒截止。真实墙钟另记，不能用虚拟时间推算吞吐。
- 9 个回合顺序预先固定，不按私有分数选任务或重试。基础设施失败停批，保留原输出。
- 父进程在 `wait()` 确认退出后封存全部 worker 文件，含最终 console；scorer 结束后再封存评分目录。后续回合结束后重新核验所有封存。

完整材料见 [run-02 清单](run-20261025-02/manifest.json)、[逐进程账本](run-20261025-02/process-ledger.jsonl)、[逐回合结果](run-20261025-02/results.json)、[96 项汇总](run-20261025-02/summary.json) 与 [302 文件封存](run-20261025-02/campaign-seal.json)。

另由独立只读脚本对已保存产物作 **297 项核验，全部通过**：重算302文件封存、80份冻结源、410份原输入、进程退出顺序、四种边界、策略正文和B对照。不调用父协调器断言、不启动模拟器/模型、不反序列化pickle或重跑评分。见 [独立审计](independent-audit.json) 与 [复核脚本](independent_audit.py)。297项是这些同批产物的证据复核，不是新增样本或成功率分母。

## 实际结果

| 回合 | 实际终态 | 模型 HTTP 次数 | task API / 动作 | 原检查通过叶子 | 模型归档 |
| --- | --- | --- | --- | --- | --- |
| reference-B | completed | 3 | 14 / 3 | 2/2，任务通过 | valid、complete |
| success-A | completed | 3 | 14 / 3 | 2/2，任务通过 | valid、complete |
| success-B | completed | 3 | 14 / 3 | 2/2，任务通过 | valid、complete |
| failure-A | provider_failure | 3 | 5 / 1 | 1/2，任务未完成 | valid、partial：model_error |
| failure-B | completed | 3 | 14 / 3 | 2/2，任务通过 | valid、complete |
| timeout-A | timeout | 2 | 7 / 1 | 1/2，任务未完成 | valid、partial：tool_error |
| timeout-B | completed | 3 | 14 / 3 | 2/2，任务通过 | valid、complete |
| cancel-A | cancelled | 2 | 7 / 1 | 1/2，任务未完成 | valid、partial：tool_error |
| cancel-B | completed | 3 | 14 / 3 | 2/2，任务通过 | valid、complete |

三种非成功 A 都已经完成真实起飞并产生 14 米垂直位移，不是空回合早退。failure-A 在公开读取并执行起飞后收到完整 HTTP 500；这是注入的 provider 错误，不是自然技能失败。timeout-A 的 runtime 实际进入 `interrupted/deadline`，取消回合进入 `interrupted/cancelled`，触发 ID 均对应已经提交成功的起飞；后续转向和悬停没有执行。三个情形都保留部分完成状态并正确判为整题失败。

所有归档已闭合且结构校验有效；实际 request bytes 和拼接 response bytes 与独立 MockTransport oracle 的长度、SHA 和状态码一致，工具调用/回执关联由归档校验器检查。3 个 partial 文件保留真实正文和错误边界，但不冒充完整成功示范。关闭后的迟到响应探针没有改写旧归档，关闭后的动作请求被拒绝且未改变状态、动作日志或评分快照。

四个 B 的黑板、账本和技能入口均为新回合状态，原始模型请求仅对已知 command ID/plan ID 做限定替换后比较，未删除状态、工具 schema、反馈或预算内容。动作比较保留参数与规范化回执，只去掉明确的回执身份/时间字段。B 的输入、动作和检查结果均与独立 reference-B 一致；A 文件在评分和 B 执行后 hash 不变。

## 隐藏评分隔离

worker 只能通过公开 API 给 Agent 提供观测；私有检查端点探针被拒绝。用于初态核对的准备性读取放在 `private/preparation-*`，不伪称 Agent 观测；`policy/worldlogs` 只含 task 阶段的非 admin 请求。

评分进程在 worker 退出后才读取 hash 已校验的私有快照，不导入 `uav_agent`，admin 请求和完整检查树仅写 `evaluation/`。每个 scorer 的私有回执均含 canary，构成阳性对照；策略目录的 JSON/JSONL、模型正文 Base64 解码及响应分块重组均未出现 canary 或非空私有检查定义。公开聚合任务进度仍允许进入策略。

上述证据支持当前可信本地脚本和进程边界的隔离，**不是操作系统安全沙箱**。不能据 canary 未出现推断任意恶意工具代码均无法读取文件；也不能将私有目录直接拼入 Teacher 文本。

## 首次失败与代码验证

[run-01](run-20261025-01/incomplete.json) 在首个 reference worker 初始化时因 Server 环境的旧 `typing_extensions` 优先加载而失败；尚未创建世界、调用模型或执行动作，后续 worker/scorer 未启动。修正验收 launcher 的依赖优先级，让 Agent 环境优先、Server 环境补充后，另建 run-02；没有覆盖、续跑或把首次失败混入 9 回合分母。[原始日志](run-20261025-01/process-logs/reference-B-worker.log) 保留。

父协调器专项 **71 项通过**，均使用合成文件、fake launcher/Popen，不增加真实回合。覆盖路径越界/符号链接、冻结 hash、正文缺失和跨分块 canary、传输 oracle、真实终态条件、退出前禁止评分、错误停批、不重跑，以及固定顺序不受评分反馈影响。见 [测试范围](PARENT_COORDINATOR_TESTS_CN.md)、[测试记录](testing-attempt01.json) 和 [日志](testing-attempt01.log)。

既有回归另列：世界截止/跨截止原子动作/入口预算与原工具路径 **6 项通过**；单 Agent 隔离 **32 项通过**。归档回归首次组合执行暴露一个旧测试的并发次序假设：两个独立只读 `get_drone_status` 的完成先后不同，而模型正文、工具 ID、步骤、结果和 usage 均一致。只修正该测试对这一组并行只读调用的比较，不改生产逻辑；旧失败日志保留。修正后整份归档测试只运行一次，**49 项全部通过**，见 [attempt03日志](regression-agent-archive-attempt03.log) 与 [改动/hash记录](regression-agent-archive-attempt03.json)。代码测试与9回合、96项链路检查、297项只读审计分别记录，不合并为任务成绩，也不与上批825项累计宣称新增测试。

最初从仓库根目录执行既有 Agent 测试时还出现过缺少 `PYTHONPATH` 的 collection error；补齐项目模块路径后才进入测试。这是测试启动问题，不计作回合或产品行为结论，见 `regression-agent-archive.log` 与后续 attempt 日志。

## 本次文件改动

- 新增 [父协调器](../../../scripts/validate_harness_episode.py)、[本地worker与scorer](../../../scripts/harness_episode_local.py)、[协调器测试](../../../scripts/tests/test_harness_episode.py)。
- 最小修正 [既有归档测试](../../../agent4drone/tests/test_model_io_archive_integration.py) 的并行只读顺序断言。
- 本目录新增协议、两次尝试的完整记录、测试及审计材料；研究入口同步到 [研究状态](../../RESEARCH_STATUS_CN.md) 与 [Roadmap](../../CSCWD2027_INCREMENTAL_RESEARCH_ROADMAP_CN.md)。AGENTS的稳定背景无需堆入逐回合细节，保持原样。
- [旧封存回读](historical-seals-validation.json)确认前两批94份与47份文件均无变化；原HTML hash保持 `ec01c042e0d5345205965ebbb825b6b5aedcc448d02d84858442c83c7c21e88b`。运行依赖版本见 [runtime-versions.json](runtime-versions.json)。

## 可复用边界与下一步

当前可以复用的是：**每 trial 全新进程 + 固定初态/权限/预算 + 实际模型归档 + 真实退出后独立评分 + 全文件封存**。脚本模型仅用于本地验收，不是 Teacher 数据，当前没有产出训练模型或新 Benchmark 成绩。

尚未覆盖进程池/同进程多 World 复用、真实 provider 时序、跨进程外部无人机动作撤销、多任务族自然选择/接手、真实环境吞吐以及检查器作为 RL 奖励的可靠性。现有关闭后探针验证的是本地模拟器拒绝新动作与归档拒绝迟到写入，不外推到真实远端已发动作。

下一步应先整理并冻结上述复用接口与限制，再设计小规模自然 Agent 选择/接手的独立协议；开始 Teacher 数据前，明确一个可训练问题和学生决策职责，并冻结场景级训练/开发/测试划分。本次不自动启动这些实验或训练。
