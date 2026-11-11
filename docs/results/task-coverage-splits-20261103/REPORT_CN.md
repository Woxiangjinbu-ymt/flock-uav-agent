# 第三项：任务覆盖与独立数据划分

日期：2026-11-03。对应 W2；协议见[任务覆盖与划分](../../TASK_COVERAGE_AND_SPLITS_CN.md)。本轮没有调用模型 provider，也没有启动 Teacher 采集、SFT 或 RL。

## Confirmed Decisions — 数据身份

旧 75 场景 / 1500 题全部保守登记为已暴露开发池。历史 `validation60` 的选样已使用基线结果，不能重新命名为独立测试；旧场景没有可核实的生成 seed/version，不能从文件名补造。历史成绩、失败和未执行分母均保留。

新数据按**完整场景**划分，同场景所有任务和派生别名留在同一 split。目标为已知模板下的新场景实例，不宣称新模板、任务族或语言泛化。生成/审计脚本先在独立 pilot 上调试，正式包在质量审计和任何策略运行前冻结；不按后续结果重新抽 seed 或换题。

## Verified Results — 开发覆盖集与初态

[开发清单](../../../experiments/task-coverage-v1/README_CN.md)按预定模板槽及原文件/任务顺序选择，不以模型成功率或用量排名：18 题、9 个原场景、91 个原 checker 叶子，F1–F6 各 3 题。明确参与主体覆盖 1–5 架，实际可用机队为 3/5 架；旧池没有 1/2/4 机队场景，不通过删机制造这些规模。15 题为原工具候选，2 题具有 `simple_chain` 候选、1 题具有 `public_search` 候选；匹配不代表模型实际选择或成功。

[初态包](../../../experiments/task-coverage-v1-study/manifest.json)已完成 18 题物化：所有初始 checker 均不通过、无检查器执行错误，污染后恢复的完整状态、公开观测和评分一致。保留原场景所有实体及原指令/checker；生成 501 秒 timeline。这里记录的 simulator seed 控制本地时钟环境/ID，不冒充历史场景生成 seed。

[维护入口 smoke](entry-smoke-run-01/summary.json)使用一题 `c74d7020`，分别走原工具和 `simple_chain`：2 worker、2 独立 scorer，均通过原 checker，158 文件封存核验通过。这是脚本模型验证新初态包与第二项运行链兼容，不是 18 题自然成功率，也不是新模型行为证据。

旧 checker 局限另列，未改题或删除分母：6 题提到 hover 但没有直接 hover 叶子；部分多目标任务只查 generic reached，没有绑定所要求主体/顺序/照片；一个 12 叶任务保留 4 条重复定义；三条 F6 原文使用严格 `exceeds`，原 checker 实为 `>=`；编队同步措辞没有显式时间同步判定。新数据不能用这些弱 checker 作为完整语义保证。

## 新场景与冻结划分

生成器预定 30 场景 / 174 题：train 20 场景 / 116 题，dev 5 场景 / 29 题，test 5 场景 / 29 题；各集合覆盖 1–5 架可用机队，单机场景不生成 F5。每题从独立干净初态执行；6 个模板共享在三个集合中。目标初始可见，静态障碍在必要路径外，因此是受控可行性起点，不代表复杂搜索、碰撞恢复或广泛难度。

F4 的文字和 checker 均针对机队聚合覆盖；F6 针对累计访问全部非 waypoint 目标、公开进度达到 100%，不要求同时占位，也不匹配当前 `public_search` 的严格语法。F2 明确任意顺序访问并指定最终点；不宣称 checker 核验未实现的时序。hover 使用动作历史，拍照明确只要求目标处于感知范围，不等于真实视觉质量。

正式生成及审计结果见下方机器记录；数据质量脚本通过公开接口取得目标坐标，私有世界和 checker 只在隔离准备/评分侧使用。正例、无动作初态和预定缺少必要步骤的负例分别记录，质量轨迹不进入 Teacher 数据。

| 正式产物 / 验收 | 结果 |
| --- | --- |
| [新划分 manifest](../../../experiments/task-splits-v1/manifest.json) | 30 场景、174 题、588 个 checker 叶子；20/5/5 场景，116/29/29 题；SHA256 `4d2da10b0a6ea753a924ec22f6f16ad29d59711a94ecef6fbd013cbadf226f4e` |
| [独立划分审计](split-audit.json) | 跨 split 的 ID/seed/lineage、去标识初态、XY 平移及 ≤1 米几何筛查通过；与旧 75 场景无 ID/相同平移几何复用，与已暴露 pilot 无 ≤1 米几何复用；F1 30 个任务指纹无跨集重复，与 pilot 无同指纹 |
| 模板和命令重叠 | 6 个模板全部跨 split 共享；9 类数字/名字归一骨架共享，7 类保留数字但归一名字的命令共享。不同场景中的目标访问/覆盖指令常相同，不能宣称指令或模板独立 |
| [正式质量验收](quality-run-01/summary.json) | 30 个场景进程、174/174 题通过预定质量规则：174 个初态均未完成，174 个公开动作正例均通过，174 个缺必要步骤负例均不通过；共 522 次 checker 评价，不是 522 次模型任务 |
| [可复现性](reproducibility.json) | 从封存源码重建正式包，全部 44 个文件逐字节一致；未重新抽样或替换任务 |
| [代码测试](pytest.xml) | 138 项不重复测试通过，覆盖选样、生成、划分、数据用途准入、公开动作来源、恢复隔离和审计期间版本绑定 |

开发调试批 `tmp/task-splits-pilot-01` 另有 30 场景 / 174 题及一次完整质量验证，全部保留开发身份，不并入正式集分母。其名为 train/dev/test 的字段仅用于调试分组逻辑，不具有独立 holdout 身份。一次测试命令文件名拼写错误未收集测试，保留 [调用记录](pytest-invocation-error.xml)，不是测试断言失败；最终 138 项以正式 JUnit 为准。

初态包使用自身封存源码物化，随后为维护版 materializer 增加执行期间的数据版本绑定检查；原物化包和原数据的哈希独立复核一致，没有改写初态或重跑择优。每个使用事件与用途见[暴露记录](exposure-ledger.jsonl)，封存与最终复核见[最终验收](final-verification.json)。

## 复核与下一次使用

以下只读命令不调用 provider：

```bash
agent4drone/.venv/bin/python scripts/build_development_coverage.py verify --dataset experiments/task-coverage-v1
agent4drone/.venv/bin/python scripts/audit_task_splits.py --dataset experiments/task-splits-v1 --exposed-dataset tmp/task-splits-pilot-01
agent4drone/.venv/bin/python scripts/general_agent.py verify --run docs/results/task-coverage-splits-20261103/entry-smoke-run-01
```

下一次为采集准备 train 初态时，使用明确的用途门禁；输出必须是不存在的新目录：

```bash
agent4drone/.venv/bin/python scripts/prepare_task_dataset.py --dataset experiments/task-splits-v1 --split train --purpose teacher_training --output tmp/task-splits-train-study-01
```

该命令仅物化初态，既不调用模型也不导出 Teacher 样本。此入口拒绝 test 的开发/训练物化，训练用途只允许 train；dev 用于开发时须显式选择 `--split dev --purpose development`。后续评价入口及其权限要单独冻结。manifest、原 scene、checker、quality recipe 均为准备/评估资料，不能直接拼进学生 prompt 或当训练行。

## 限制与后续

本轮没有自然模型运行，不能报告当前整合 Agent 的跨任务成功率或独立泛化收益。test 的预定自动质量检查属于明确登记的暴露，不用于修改 Agent/prompt/奖励；文件在本地可读，不宣称具有系统级保密隔离。后续任何人工查看或策略运行都需要追加用途记录。

[最终独立复核](final-review.json)按原始回执重算全部分母与来源：3150 次公开请求、1350 次动作，目标详情读取均有先前公开发现依据。质量脚本与 evaluator 在同一场景进程内通过阶段/权限分离，**不是174个独立scorer进程**；30场的fresh进程由冻结`subprocess.run`源码与逐场退出记录支撑，没有逐场PID时间遥测。另行2回合smoke才具有worker/scorer不同进程的完整记录。

全场几何筛查覆盖去 ID/改名、整体平移及 1 米近重复，F1 另检查与无关场景几何无关的任务参数/初始状态等价；F2–F6 尚无完整任务语义等价器，旋转/缩放和复杂等价也没有完全排除。共享命令/模板在审计中公开，不能把“无指定指纹碰撞”写成“完全没有语义泄漏”。

下一项可进入 W3 样本转换、标签/mask、学生输入预算；先用 train 采集、dev 开发，test 留待评价协议冻结后使用。
