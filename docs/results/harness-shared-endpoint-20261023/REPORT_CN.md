# Harness 跨无人机目的点证据 v5：离线配对结果

日期：2026-10-23。按[执行前协议](PROTOCOL_CN.md)，在独立[`run-20261023-01`](run-20261023-01/summary.json)一次完成原7题×2版本，共14次，没有改样本、加预算、补跑或择优重跑。

**在本次隔离静态环境中，v5将跨actor同XYZ目的点重复冲突从1次、7次降为0；三个搜索题的原整题checker由0/3变为1/3。** `b1ad4f3e`公开进度80%→100%、总move错误17→10，原checker通过；`24ac1838`仍38%、17个错误并耗尽修复预算。通过的搜索题仍返回`needs_replan/no_public_candidate`，不能写成Harness已经正确宣布完成。两项受影响任务的动作、API、路径与耗电都增加。

14次是脚本显式选择技能和start的真实本机模拟器代码路径验证，没有模型调用、自然Agent选技能或原工具接手。三个原搜索问题簇、两个短技能正对照、两个完整语法负对照分别统计；不把混合7题比例当作Benchmark成功率，也不据此宣称稳定泛化或模型能力提升。

## 问题、最小改动与适用前提

前一批v4只记住该actor的目的点错误；[冻结诊断](DIAGNOSIS_CN.md)保留了其他actor再次到同一XYZ冲突的1次和7次事件。[服务端契约审计](CONTRACT_AUDIT_CN.md)确认当前目的点碰撞谓词不读取actor尺寸、能力或起点，但读取完整XYZ、当前障碍几何与高度。路径、电量、状态、权限错误不能共享，普通在线场景也不能仅凭scene ID/本地epoch相同就推断地图未变。

`shared_endpoint_v5`继承v4入口、终态、飞行状态与严格回执核验，只在原move回执明确证明目的点冲突且随后公开XYZ未变时，保存精确目标XYZ、源actor和原move evidence_ref。当前计划后续actor排除同一XYZ；不扩展到邻点、区域、其他命令高度或路径冲突，不改变搜索候选、排名、范围、语法、API或修复预算。共享记录属于generator/plan、scene/epoch与固定z=20；空nearby、换起点和新ID不清除；已观察同ID几何改变则退出整个计划。

该功能默认关闭。standalone启用为`HarnessRuntime(..., revision="shared_endpoint_v5", static_obstacle_contract=True)`；集成须同时设置`UAV_HARNESS_REVISION=shared_endpoint_v5`及精确`UAV_HARNESS_STATIC_OBSTACLES=1`。没有显式静态声明，v5搜索在首API前退出。其他revision保留原行为；本轮只改3个Harness生产文件，`coordinated_search_r2.py`保持上一轮可选过滤实现不变。

**静态writer隔离是适用前提，公开变化守卫不能检测不可见的远处地图修改。** 本批两臂都使用新进程本机ASGI TestClient、未启动lifespan/background simulator、阻断socket，并对task请求施加相同allowlist，拒绝admin及地图写入。原冻结时间线只推进targets/environment与时钟/RNG；开始前验证结构，结束后验证评估端障碍几何digest。只给v5传True、v4传False，私有障碍内容及digest均不输入policy、黑板、账本或示范。完整来源、transport、时间线及前后digest见各trial的`static-obstacle-contract.json`。这不是动态障碍有效性、地图版本协议或实机安全验证。

## 固定协议与执行完整性

- 两臂同原full100完整对象图及时间线、空黑板、新R2账本、`applicability`调度；逐题交替版本先后，每次新进程。
- 500秒deadline、2,000 API、16repair、16round，外层90秒watchdog。实际任务0.0126–3.9764秒，未触发deadline、watchdog或API上限；这不是实机飞行耗时。
- 14/14执行完整性通过，7/7初始结构hash、公开初态与配对障碍digest一致；14个前后障碍digest不变、writer拒绝记录为空、网络连接尝试及模型调用均0。
- 冻结76份源码、19项fixture依赖和协议；原manifest全410项、冻结源码与历史v4封存均在运行后重验不变。315项[本批封存](run-20261023-01/campaign-seal.json)逐项重验，当前源码与冻结副本无差异。
- 28次重复start/终态resume探针没有改变世界、frames、API或物理事件。有plan的20次明确拒绝；另8次为unsupported/不存在plan的负对照。未自动重启或接手。
- 既有313项＋新增63项，共376个不重复测试通过，见[回归汇总](regression-validation.json)、[专项记录](SHARED_ENDPOINT_TESTS.json)与[冻结前检查](preflight-validation.json)。开发时修正测试断言的8项失败已保留在专项metadata；没有将重复运行叠加为新样本。
- [历史保护检查](preservation-validation.json)确认旧入口、飞行、v4三批256/273/287项封存及HTML保持，初始11,564个既有非Markdown文件无缺失，累积只改变4个预期Harness/policy生产文件；本轮新改其中3个Harness文件。
- [独立完整性与raw结果审计](INDEPENDENT_AUDIT_CN.md)及[322项静态核验](independent-verification.json)全部通过，包含独立重算障碍digest、错误/重复、动作/路径/耗电与原checker。322是封存后的审计断言数，不与376项pytest或14次trial相加。

## 原7题的完整结果

以下`v4→v5`均来自本批同时冻结的两臂，检查列为原checker子检查通过数。

| 任务 | 公开进度/访问数 | 原检查 | 动作尝试 | runtime停止原因 |
|---|---|---|---:|---|
| `7eba7390` | 33%→33%（1/3→1/3） | 0/1→0/1 | 85→85 | 均`round_limit_below_threshold` |
| `24ac1838` | 38%→38%（38/100→38/100） | 0/1→0/1 | 153→156 | 均`local_repair_budget_exhausted` |
| `b1ad4f3e` | 80%→100%（16/20→20/20） | 0/1→1/1 | 120→191 | `local_repair_budget_exhausted`→`no_public_candidate` |
| `b1eb0270` | 0%→0% | 2/2→2/2 | 3→3 | 均`skill_public_stop_condition` |
| `898cfdca` | 0%→0% | 2/2→2/2 | 3→3 | 均`skill_public_stop_condition` |
| `784d71f8` | 0%→0% | 0/4→0/4 | 0→0 | 均`unsupported_skill_or_full_task_grammar` |
| `1905e0ad` | 0%→0% | 0/6→0/6 | 0→0 | 均`unsupported_skill_or_full_task_grammar` |

搜索原整题checker为0/3→1/3。两个短技能两臂均2/2整题通过，其任务不要求搜索，session progress=0不是该技能失败。两个负对照均拒绝且零动作，保留指定目标及50%/70%附加义务。其余5对规范化动作序列完全相同。完整原始汇总见[`results.json`](run-20261023-01/results.json)。

## 重复、总错误及新增失败

主跨actor指标为严格资格inclusive：后续目的点冲突本身合格，且同plan存在request ID更早、不同actor、精确XYZ相同的合格来源，每个后续请求只计一次。独立parser核对完整message、command、actor、无矛盾/非几何错误字段及后读XYZ未变。路径冲突排除；若同时存在同actor前源，overlap另列但不扣主计数。宽前缀总数、资格失败与缺失值全部保留，不能静默排除以美化结果。

| 指标 | `24ac1838` v4→v5 | `b1ad4f3e` v4→v5 |
|---|---:|---:|
| 跨actor同XYZ重复事件 / 组 | 1/1→0/0 | 7/4→0/0 |
| 同actor目的点重复 | 0→0 | 0→0 |
| move总尝试 | 123→126 | 105→176 |
| move成功 | 106→109 | 88→166 |
| 全部move错误 | 17→17 | 17→10 |
| 目的点 / 路径冲突 | 14/3→14/3 | 15/2→7/3 |
| 去重失败签名 | 17→17 | 17→10 |
| 共同 / 新增 / 未再出现的签名 | 16 / 1 / 1 | 8 / 2 / 9 |

所有trial的严格与宽跨actor指标一致，overlap、资格失败及缺失plan/actor/XYZ均0。没有其他move错误、partial/unknown、缺失公开起终点，也没有同actor同edge重复或同actor换起点后对同终点再次路径冲突。24ac两臂各有一组不同actor/起点的同终点路径冲突，未混入目的点共享。

去重签名只按协议键区分：目的点为actor＋XYZ，路径再加公开起点，其他错误加message。它们是相关轨迹事件，**不是统计独立样本**；新增1/2个签名不能作泛化分母。

| 任务 | v5请求号 | 新签名 |
|---|---:|---|
| `24ac1838` | 640 | actor`318da56a`到`(875,1155,20)`目的点冲突 |
| `b1ad4f3e` | 405 | actor`63e09994`从`(665,35,20)`到`(945,525,20)`路径冲突 |
| `b1ad4f3e` | 413 | actor`b6654aad`从`(875,315,20)`到`(1015,525,20)`路径冲突 |

24ac移除了原actor`92e6a7b8`到`(1715,1155,20)`的失败，却继续探索到新失败，最终总错误与访问数不变。b1ad减少目的点冲突但新增两条起点相关路径错误；路径错误保持原处理。两题轨迹改变后“未再出现”的签名不表示对应坐标已可达。逐move来源及配对列表见各trial的`blocked-candidate-diagnostics.json`、`shared-endpoint-diagnostics.json`与[配对摘要](run-20261023-01/summary.json)。

## 首次动作分歧与证据归属

`24ac1838`前137个动作请求相同；第138个动作、公开请求565首次分歧。请求564均观察actor`92e6a7b8`位于`(1505,1225,20)`且hovering。v4到`(1715,1155,20)`报Polygon7目的点冲突；v5改到`(1505,1295,20)`并成功。共享记录保留源actor`869a8e16`的请求313、原目标XYZ及`e563`，不是把其他actor的失败伪装为当前actor执行过。

`b1ad4f3e`前36个动作请求相同；第37个动作、请求151首次分歧。请求150均观察actor`60892444`位于`(105,525,20)`且hovering。v4到`(175,665,20)`报Polygon9冲突；v5改到`(175,595,20)`并成功。共享来源为actor`2a266de6`请求142到原目标的公开失败、`e251`。完整前置状态、请求、回执及源引用可在[首次分歧记录](run-20261023-01/summary.json)与各trial的`harness.json`追溯。

前缀含成功和失败尝试，不等于完成同样数量的动作；后续所有分数变化也不能拆成独立的单个mask效应。[独立公开机制审计](PUBLIC_MECHANISM_AUDIT_CN.md)已核对本批v5最终21条共享记录（24ac 14条、b1ad 7条）：原actor/参数/回执/ref及摘要均匹配，公开后状态XYZ未变且确认早于mask发布，登记后没有任意actor再次发送该精确XYZ，也没有伪造消费者已失败。29个输入对齐seal、315项机制审计检查无失败，详见[逐条来源](PUBLIC_MECHANISM_AUDIT.json)。这是另一组静态审计断言，不增加任务、pytest或独立样本分母，没有为缺失的反事实追加试验。

## 原checker通过但Harness没有宣布完成

b1ad v5的最终公开session为20/20、100%，隔离原checker也通过；runtime仍`needs_replan/no_public_candidate`。这两个事实同时保留。

[独立公开终止诊断](completion-reporting-diagnosis.json)显示：技能最后一次progress读取为请求772，缓存95%；随后还有6个move请求774/779/783/787/800/807，计划cursor最终176，而原逻辑每10次move才刷新progress。下一轮无可选候选时提前抛出`no_public_candidate`，跳过循环末尾的最终progress刷新；终态后的评估端请求834才读取到100%，原checker请求835通过。这是完成判定反馈与最终世界状态的差异，不能把评估端读数倒灌为技能已知信息或改写其终态，也不单凭最后一次动作断言剩余目标何时完成。

[公开机制审计](PUBLIC_MECHANISM_AUDIT_CN.md)进一步定位请求800：成功move首次报告`Fixed Target 20`，此前请求161/272的公开nearby确认该目标未到访，累计成功move回执中的目标名达到20个。结合772的19/20和834的20/20，可推定800已完成最后目标；但800时没有同步progress=100读取，这一事后推断不能写成策略当时已知完成，也不把此前6个动作全部称为冗余。

本批没有修改终止逻辑或追加运行。后续可另立一项窄验证：在候选耗尽等停止边界，是否应先用已有公开接口刷新并验证完成状态，同时保持API预算、终态控制及失败证据约束。该问题尚未修复，不把本次原checker通过写成runtime完成语义已正确。

## 成本与历史v4复现

| 指标 | `24ac1838` v4→v5 | `b1ad4f3e` v4→v5 |
|---|---:|---:|
| 技能API / 含最终公开评估读取API | 599/600→611/612 | 477/478→817/818 |
| 全部动作尝试 | 153→156 | 120→191 |
| 动作成功 / 错误 | 136/17→139/17 | 103/17→181/10 |
| XY累计路径（m） | 12,002.681→12,424.065 | 13,544.762→30,809.960 |
| XYZ累计路径（m） | 12,619.681→13,041.065 | 13,844.762→31,109.960 |
| 累计垂直位移（m） | 617→617 | 300→300 |
| 累计耗电（mAh，实测） | 86.738774→89.853839 | 79.088633→171.635680 |
| 任务实际墙钟用时（s） | 3.687575→3.976386 | 1.444032→2.824257 |
| 最终repairs / budget | 17/16→17/16 | 17/16→10/16 |

repair预算16的原语义为第17次已确认blocked后停止，不是保证总错误最多16。24ac最终cursor126、units125，因最后错误在checkpoint前退出；b1ad为cursor/units176。没有扩大预算来获得100%，但减少重复后执行了更多动作，路径与耗电明显增加，因此不能把整题收益同时写成成本下降。

本批7个v4与上一批封存v4的初态、动作序列、公开进度、停止原因、checker、错误/跨actor指标以及全部cost字段逐项相等；实际elapsed不同，逐项值保留在[历史对照](run-20261023-01/historical-baseline-comparison.json)。本轮恰好cost一致不证明固定耗电完全可复现。初始化与时间线一致不保证每个动作处于同一模拟秒：`advance(elapsed)`仍受墙钟影响，上一批已发生相同几何动作跨环境更新时间边界而耗电不同。保留本批实测数，不将精细能耗差全部解释为共享策略效应。

## 可保留结论与下一步边界

可保留：在已声明并验证writer隔离的静态fixture上，公开严格目的点负证据可按精确XYZ跨actor复用；本批重复消除，且b1ad原整题checker有一个有成本代价的正结果。24ac仍受失败预算限制，7eba仍为1/3访问并耗尽轮数，b1ad终态语义仍与最终完成事实不一致。

这些结果不足以支持无条件上线、动态地图、其他高度、真实网络/实机协作、自然Agent选技能、稳定泛化或论文创新成立。模型后训练仍为规划。本次结束后不自动扩搜索范围、改目标利用、增加预算或继续跑在线Agent；先独立核验本批证据，再决定是否做停止边界的单一窄修复。

复现使用[`scripts/validate_harness_shared_endpoint.py`](../../../scripts/validate_harness_shared_endpoint.py)，最终SHA256 `f9c202a4b8f25f407fa52e4e8a1101e31e22162b9a76815594f2361dfd867600`。Python及依赖、原任务/快照/时间线SHA见[manifest](run-20261023-01/manifest.json)；请另选不存在的新输出目录，不覆盖本批：

```sh
PYTHONDONTWRITEBYTECODE=1 agent4drone/.venv/bin/python scripts/validate_harness_shared_endpoint.py --output /absolute/path/to/a-new-shared-endpoint-run
```

封存后的[完整性与计分审计](INDEPENDENT_AUDIT_CN.md)、[公开机制审计](PUBLIC_MECHANISM_AUDIT_CN.md)均保存在本目录外层；本批run内源码、结果与seal保持不变。
