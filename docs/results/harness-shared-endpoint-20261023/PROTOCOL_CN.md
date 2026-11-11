# Harness 跨actor目的点证据：配对验证协议

日期：2026-10-23。**执行就绪，执行前冻结本协议与源码。** 候选静态环境契约、专项测试和独立runner审查已完成；根代理授权按原定14次执行。基准为 `blocked_candidate_v4`，候选为 `shared_endpoint_v5`。本协议不把既有重复事件自动视为跨actor共享安全或有效的证明。

## 问题与进入条件

上一批v4已消除固定轨迹中的同actor目的点重复。封存后的[公开证据审计](../harness-blocked-candidate-20261023/independent-public-evidence-audit.json)仍发现：已有其他actor在相同XYZ失败后，再次出现目的点冲突的事件，`24ac1838`为1次/1组，`b1ad4f3e`为7次/4组。它们是固定轨迹的相关事件，不是统计独立样本，也不能直接证明共享后整题会成功。

现有[313项回归元数据](regression-existing.json)及[新增63项专项测试](SHARED_ENDPOINT_TESTS.json)均通过，共376个不重复测试；测试初期8项断言修正保留在专项metadata，未改生产实现。[静态契约审计](CONTRACT_AUDIT_CN.md)与[输入来源](CONTRACT_AUDIT_SOURCES.json)明确共享前提及其边界。[runner静态与诊断自检](runner-diagnostics-check.log)检查语法、合成helper及旧7题v4的1,448条公开task请求：现有请求均通过allowlist，地图/admin请求被拒；这些不是新仿真实验。独立审查确认主指标为严格来源/后续回执inclusive且无阻断；最终runner SHA256为`f9c202a4b8f25f407fa52e4e8a1101e31e22162b9a76815594f2361dfd867600`。冻结前再核对生产源码与两类测试记录hash。

本分支只研究已确认的跨actor目的点证据使用，不同时加入目标利用、搜索范围/格网/排名改造、预算扩张、动态地图推断或私有几何。不改server、原任务、checker、fixture或既有默认revision；v4必须作为本批真实基准完整保留。

## 候选语义与静态障碍契约

`shared_endpoint_v5`继承v4入口、终态、飞行状态与目的点回执核验。仅接受command=`move_to`、status=`error`、drone_id与当前actor一致、command_outcome=`blocked`，且message全文为 `Target position conflicts with obstacle: Position conflicts with (circle|ellipse|polygon) obstacle '<name>'` 的原始公开回执；后读公开XYZ确认该actor位置未变后，记录精确目标XYZ及原actor、move evidence_ref与回执。路径冲突、actor状态或电量错误、部分/未知回执不共享。

共享仅存在于当前generator/plan、scene/epoch及固定z=20的搜索中。后续任意actor只排除完全相同的候选XYZ，不推断区域或相邻点、不给路径冲突改判。保留原候选集合、排名、范围、回合及预算。暂停/换起点、空nearby与新障碍ID不清除已知失败；无TTL。plan/epoch/external action等原失效边界仍退出计划；同ID公开几何变化（type、position/coordinates、radius、width、length、vertices、height、dimensions）或黑板中已观察实体消失，退出整个计划，不自动恢复或重启。仅改变名称/观测元数据不算几何改变。

跨actor共享依赖显式静态障碍且无外部writer契约：`HarnessRuntime(..., revision="shared_endpoint_v5", static_obstacle_contract=True)`。参数默认False，v5搜索未获True在首个公开API前退出；集成端必须显式设置 `UAV_HARNESS_STATIC_OBSTACLES=1`，其他值不生效。这不是空观测可以推出的事实，也不是动态地图支持。不可见地图变更不能靠公开黑板比较可靠检测，须由调用端隔离writer保证；公开变化检测只是补充保守退出。

本runner先验证自身本机新进程TestClient使用`_TestClientTransport`直接绑定ASGI app，未启动lifespan/background simulator，且socket连接被阻断。两臂施加相同task阶段请求allowlist：只读drone、nearby、current session，以及原take_off/change_altitude/rotate/hover/move_to命令；所有task admin及地图写入在server前拒绝。结束时evaluator的公开current-session读仍允许，隐藏checker随后在独立`scoring`阶段执行。原冻结时间线须逐项只有second/targets/environments/rng，`World.advance`仅推进targets、environment和时钟/RNG；其源码随manifest封存并留来源hash。

满足这些验证后，v5传True，v4传False保持其runtime state/log行为。评估端在策略开始前与结束后对私有障碍ID及几何字段做digest，验证同原fixture及配对两臂一致；只把契约布尔值给runtime，不把私有坐标/几何/hash传入policy、黑板或账本。每题保存`static-obstacle-contract.json`，含契约来源、输入条件、前后digest、writer拒绝记录。digest变化或writer违规为基础设施/契约失败，停止后续派发；不会用最终digest反向修正策略决策。以上范围只支持本次隔离静态fixture条件下的离线功能验证。

## 固定样本与执行方式

| 类型 | 任务ID | 固定用途 |
|---|---|---|
| 三个原搜索问题簇 | `7eba7390`、`24ac1838`、`b1ad4f3e` | 整题搜索与跨actor失败事件 |
| 简单链正对照 | `b1eb0270`、`898cfdca` | 原完整动作链 |
| 不支持语法负对照 | `784d71f8`、`1905e0ad` | 保留指定目标和50%/70%附加义务 |

7题×2revision＝14次，顺序如表；偶数索引先v4后v5，奇数索引先v5后v4，每次新进程。原full100完整快照及时间线只读；先核验原manifest、任务、快照、时间线及源场景SHA，再反序列化，核验恢复结构hash与公开初态hash。公开准备检查后再恢复快照，不预载策略黑板；每次新R2账本、空黑板。

两臂同为 `applicability`，500秒deadline、2,000 API、16repair、16round；外层90秒watchdog。驱动器显式选择技能并start到terminal，不调用LLM、外部网络、raw接手或额外恢复策略。worker阻断socket，仅本机ASGI；隐藏checker在策略结束后隔离调用。

冻结并复制同一份源码后执行固定14次；不得边跑边改源码、换样本、加预算或择优重跑。基础设施/哈希/预算/网络/checker错误、静态契约/写入守卫违规、终态控制有副作用时停止剩余派发；普通needs_replan、unsupported、预算耗尽或整题不通过是有效结果，不触发自动重试。新输出目录必须不存在；旧v4封存及诊断材料不改。

## 主要指标与独立公开诊断

主结果为原整题checker及子检查，三个搜索题单列0–3分母；简单链和不支持语法分别报告，不能将混合7题比例包装成Benchmark成功率。保留公开progress/访问数、最终status/reason、cursor/units/repairs、API、动作成功/错误/总尝试、路径、耗电和实际用时。减少重复不等于减少总错误，也不等于整题完成。

诊断器只解析task阶段skill公开日志，不调用候选的错误分类器来证明自身正确。目的点冲突定义为error回执且message以 `Target position conflicts with obstacle:` 开头；路径冲突为error且含 `Flight path intersects with`；其他错误、部分和未知另列。

**跨actor同精确XYZ重复：** 主指标采用严格资格inclusive口径：对每条后续合格目的点冲突，要求同一plan中有request ID严格更早、actor不同、请求XYZ完全相同的合格目的点冲突来源；每个后续请求最多计一次。资格由独立诊断核对完整回执message、error、command=`move_to`、actor及已知请求XYZ，并查该actor下一次动作前的首次公开状态，核对actor匹配且前后XYZ未变。所有路径冲突及仅有同actor前源的事件排除。若当前actor自身也曾在该XYZ失败，保留overlap但不从主计数扣除；因此主计数与同actor重复不能相加当总失败数，另报无overlap子计数。按同plan精确XYZ另报至少两个合格actor的分组数，事件数不等于组数。

宽message前缀的跨actor事件/组及全部来源也完整保留，与严格指标并列；不合格回执、缺失后读或状态变化的原因逐条可查，不能悄悄排除以美化指标。缺失plan/actor/XYZ单列，不补私有坐标。既有v4的1/7事件与1/4组无overlap，独立资格检查全通过，严格与宽口径一致。

同actor同目的点重复仍按actor＋XYZ单列；路径冲突仍区分actor＋公开起点＋终点的同edge重复与换起点尝试。这样不能将所有“同终点报错”错误升级为目的点全局不可达。

去重失败签名继续沿用v4口径：目的点冲突为actor＋XYZ；路径冲突加公开起点；其他错误加message。同题配对仅忽略revision导致的planID差异，报告共同、新增和未再出现的签名及request IDs。这些只是键去重的相关事件，不是统计独立样本，不能把新增签名当泛化分母。

保留全部错误/去重错误与上述重复指标，避免把预算转移到新错误误写成总失败下降。按规范化 `method/endpoint/params/body` 比较首次动作分歧及共同前缀，附最近公开状态与回执；不比较随机请求元数据或时间戳作为策略差异。每次终态继续执行重复start/终态resume无副作用探针，不支持语法的无plan情形单列。

## 初态、模拟时间与历史v4复现

与上一批 `harness-blocked-candidate-20261023/run-20261023-01` 的v4作只读诊断：初态hash、动作序列、公开进度、停止原因、checker和新诊断口径下的错误/跨actor指标。历史不代替本批v4基准，不能为了追求一致重跑。

**同初始化、时间线和预算不保证每个动作处在相同模拟秒。** 既有 `advance(elapsed)` 按墙钟耗时推进环境；上一批已记录相同动作跨环境更新时间边界产生小幅能耗差。本轮不改变时间推进，因此保存历史/本批全部实际cost及elapsed、逐字段差额和精确相等布尔；能耗或elapsed逐字不同不是硬失败，不声称精细能耗差全部由策略造成。动作/路径等非能耗成本另行核对，异常如实披露。

## 冻结与交付

实现、专项、回归及runner审查已就绪；核验源码hash后复制源码与协议，再派发固定14次。输出完整manifest、14次公开API/物理事件/计划及门控、原checker、控制探针、三类诊断、静态契约审计、配对摘要、历史对照与campaign封存；全部成功、失败和负对照均保留。

独立runner：`scripts/validate_harness_shared_endpoint.py`。固定新结果目录 `docs/results/harness-shared-endpoint-20261023/run-20261023-01`。执行命令：

```sh
PYTHONDONTWRITEBYTECODE=1 agent4drone/.venv/bin/python scripts/validate_harness_shared_endpoint.py --output docs/results/harness-shared-endpoint-20261023/run-20261023-01
```

结果目录必须不存在；未来复现须使用另一个新目录，不得覆盖本批。依赖既有agent Python 3.11虚拟环境及server虚拟环境中的本地包；无pip安装、模型服务或网络请求。
