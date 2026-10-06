# B1 局部补救试验：冻结历史 B0 对照

此目录的 `baseline.json` 固定保存原版 B0 在旧 pilot 同一组 60 题上的首次执行结果。只取官方 DeepSeek `deepseek-flash`、B0 `1794e45`、`first_attempt_no_replay`、generation 1；不重跑 B0，不混入 Qwen、后续重试、B1 或 Harness 成绩。

| 固定顺序 | 场景 ID | 原场景名称 | 通过/总题数 |
|---|---|---|---:|
| 1 | `ad34350a` | Area Assignment and Patrol Extreme 5 | 12/20 |
| 2 | `ef36e27a` | Area Search Hard 1 | 18/20 |
| 3 | `3973d0d6` | Target Assignment Easy 5 | 17/20 |
| 合计 | | | **47/60（78.33%）** |

每场完整保留原 JSON 中的 20 题及顺序。原始外部检查点通过 293/320，仅为次要统计。60 题均有 worker 退出确认，无服务错误、无超时；本组原判题通过、任务记账通过和部署通过一致。失败也完整保留在分母中。

## 文件与核验

- `baseline.json`：来源、固定协议、汇总、逐题 ID/名称/类别/分数、题前和题后进度、原证据相对路径。所有证据路径相对仓库根目录。
- `baseline-audit.json`：真实只读核验结果、三场历史 manifest 的 generation 1 记录、场景封存校验、完整输入 SHA-256、生成 baseline 文件的 SHA-256。
- `prepare_baseline.py`：可复现的离线构建和核验脚本，仅写本目录的两个 JSON 文件。

核验从历史 `experiments/runs/20261003-143255-full-5c501118/source.zip` 载入原 `reproduction_parallel.verify_scene`，传入历史运行自己的完整 `plan.json`、场景及 manifest 记录。没有拿新的 60 题计划验证旧场景封存。

三场全部封存文件逐项校验。原 checker 的 `evaluate_execution_check_node` 从同一归档提取，保持函数不变，仅对保存的 API 响应做回放；共核对 60 题、320 个检查点、60 组 worker/冻结世界/lease 证据，并交叉核对原始 results、首次账本及服务错误遥测。归档源文件也逐项与归档源哈希清单核对。

不访问 `agent-settings.json`、`llm_settings.json`、`.env` 或环境凭据，不启动 GUI、服务或模型。实验身份只校验 manifest/identity 摘要及实际使用的计划、公开脱敏配置、源归档和源哈希；没有调用会读取凭据配置文件的完整 `verify_archive`。此处 `ready=true` 仅表示这份历史对照证据核验通过，不表示新试验已就绪或已运行。

在仓库根目录复现：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 experiments/b1-remedies-pilot-v1/prepare_baseline.py
PYTHONDONTWRITEBYTECODE=1 python3 experiments/b1-remedies-pilot-v1/prepare_baseline.py --check
```

第二条命令重新核验并比较已保存内容，除忽略核验时间外完全比对，不写文件。若输入变化或分数不再是已核验的 47/60，脚本报错，不静默刷新分母。

## 比较边界

历史任务执行于 2026-10-03，官方模型名 `deepseek-flash`，当时场景并发 8。新版本数日后运行，即使模型名称相同，服务时点、采样和并发条件也不完全一致。应称为“当前版本与历史 B0 的同题比较”，不能把全部差值都归因于代码。

世界仅在每场开始时初始化；题间降落、充电，但会保留先前任务产生的目标到达、覆盖及 session 黑板状态。同题顺序一致不代表每题的动态初始状态一致。例如 `3973d0d6/9c0b52a0` 在 B0 题前快照中 `task_progress` 已为 100%，其原协议通过仍保留，但不能据此宣称本题独立从零完成 95%。逐题快照进度已收录，便于后续区分继承状态与本题新动作。

本组覆盖 6 类、38 种任务模板，但只有 3 个完整场景，不是 60 个独立同分布样本。部分题已有失败诊断并用于开发；这不是未见过的独立测试集，也不用于单独证明架构创新。不得依据本轮新成绩替换题目或删去困难、冲突、未执行任务。

## 本次单臂执行

用户明确只运行当前 B1 改进版的 60 题，B0 不重跑。方法代码为 `cb80fde8269a09795782a6ef046d04c381b3f3c9`（`b1-targeted-remedies-v1`），不使用失败的 evidence-repair Harness。研究标识为 `b1-remedies-historical-b0-60-v1`，底层保持原 `agent4drone-v3-gui500-timeout-scoring` 协议与判题门槛。

- 官方 `deepseek-flash`，temperature=0.1，500 秒动作窗口，150 次迭代配置。
- 2 个场景 worker；三场按冻结计划顺序派发，每场内部 20 题严格原序。场景之间无世界或黑板共享。
- 每题仍有原协议的降落、充电、lease、排空与冻结判题，以及最多 120 秒 worker 退出等待。
- 不另跑模型冒烟题，不增加 B0 调用。首个实际模型任务计入这 60 题。模型 SDK 原有行为保持，试验入口不提供任务重跑/恢复命令。
- 服务、worker 或证据故障停止后续派发，保留首次记录；
- 代码归档后，server、agent 和 checker 从本次 `runtime/` 副本导入；依赖使用现有虚拟环境并保存 freeze 校验。运行过程中不修改依赖。
- Agent Python 必须逐字匹配上述方法提交；共同 server/controller 运行源码必须与历史 B0 一致。其他工作区改动不被回退或一并提交。

入口及检查：

```sh
controller/.venv/bin/python scripts/b1_remedies_pilot.py prepare
controller/.venv/bin/python scripts/b1_remedies_pilot.py run
controller/.venv/bin/python scripts/b1_remedies_pilot.py verify <本次attempt目录>
```

`prepare` 仅做离线数据、源码、配置及依赖核验。入口新增 21 项离线测试通过；Agent 方法此前 299 项测试通过。它们都不是成功率成绩。

完成后先验证三场 seal、60 题全部原判题/worker证据及最终 suite-seal，再在 `docs/results/b1-remedies-pilot-deepseek-20261006/` 保存中文结论、逐题配对 CSV、汇总 JSON、输入哈希。固定 60 分母，分别报告共同成功、共同失败、新增成功、回归失败；成功率使用外部原始判题，内部自判及部署可靠性单列。

是否保留以本轮实际净收益及轨迹为依据：新增成功超过回归失败、且轨迹支持补救机制发挥作用时，才支持暂时保留；净退步则不支持整体保留，需定位是否能拆开保留个别有效修改；持平不足以证明提升。任何结论都保留历史对照、三场选样和运行时点的限制；本轮不自动再改架构或启动新模型实验。
