# 修订后 M1 的单组 fast100 实验

本研究只执行 M1-R 的 100 题，使用现有 M0 的 **80/100** 作为固定历史参照。不会重新执行 M0，也不执行固定提醒对照。`prepare` 和 `preflight` 只作离线准备；在线运行由独立的 `start` 或 `run` 命令启动。

## 固定方法与协议

- 唯一运行组：`m1r`；方法模式 `UAV_TASK_PROGRESS_MODE=evidence`；请求哈希记录 `UAV_MODEL_REQUEST_AUDIT=1`。
- 每组原 fast100：5 个场景，每场 20 题，共 100 题、605 项原检查。保留场景列表和题内原顺序。
- 原方法预算：每题 500 秒，2 workers，温度 0.1，官方 DeepSeek `deepseek-flash`。原任务评分、超时后的排空与判分流程沿用既有公共 runner。
- 每个场景新建隔离服务和 agent；场景内世界和黑板随原任务顺序承接。同 ID 的外部重载不会在这一运行中复用原 agent。
- 一次 first attempt；不自动重启、补跑、恢复或追加其他组。服务/worker/最终模型请求故障按原流程停止后续分发，保留退出证据。
- 只接受新提交的 M1-R 源码，包含动作证据、追加上下文、请求哈希模块及其测试；运行时加载冻结包，忽略之后对工作区 Agent 源码的改动。
- 历史实验已有的公共源码、评分、声明依赖和三套实际环境须一致。新增公共文件只允许本研究的独立启动与离线测试文件。

冻结提交、归档哈希和配置在 `frozen/study.json`，历史 M0 逐题账本在 `frozen/historical-m0.json`。M0 原提交为 `cb80fde8269a09795782a6ef046d04c381b3f3c9`，原 attempt 为 `experiments/runs/20261006-152635-m1-fast100-m0-723f8ec6`。

## 离线准备与预检

在仓库根目录，以明确的 M1-R Git 提交运行：

```bash
controller/.venv/bin/python -B scripts/m1_revision_pilot.py prepare --method-commit <M1-R提交>
controller/.venv/bin/python -B scripts/m1_revision_pilot.py preflight
```

`prepare` 核验历史参照及原 benchmark 清单，复制原 5 场、原数据和服务资源，封存新 Agent 源码、公共代码和依赖。不联系模型服务，不创建 attempt。

`preflight` 解压真实冻结包，用实际 Agent 环境执行方法、原集成与服务配置入口测试，并禁止 socket 网络连接；模型和仿真输出均为本地测试替身。它验证 `evidence` 模式经真实服务设置入口生效，检查原回执、逐次注入与请求哈希能力。预检记录保存在 `preflight.json`，日志在 `preflight-tests.log`。

## 后续在线启动入口

完成预检并决定开始付费测试后，使用持久任务入口：

```bash
controller/.venv/bin/python -B scripts/m1_revision_job.py start --method-commit <M1-R提交>
```

该入口只有一个 `m1r` 阶段，会先核对冻结方法、预检和既有 attempt 登记，再由独立进程持有任务。不要使用旧 `m1_progress_job.py`，旧入口仍是原 M0/M1 双组研究。

任务状态在新研究的 `jobs/<job>/status.json`；运行登记在 `runs.json`。实验自身的状态、逐题账本和遥测位于登记中的新 attempt。正常执行不追加模型探针、不恢复旧任务、不跨阶段启动 M2/dev240。

## 验收与比较

完成后调用新研究的冻结验证入口：

```bash
controller/.venv/bin/python -B scripts/m1_revision_pilot.py verify <attempt目录>
```

验证同时检查原任务账本/评分证据、仅一次场景执行，以及各题实际 `task_progress_mode=evidence`、`task_progress_active=true`、`revision=m1-r1`、观察器错误与模型请求记录。方法遥测有效性和任务得分分别报告，不把更严格的内部标签记作成功率收益。

主指标是 M1-R 成功数 / 100，与固定历史 M0 80/100 的净增；保留同题 W/L、共同成功/失败及逐场分布。时间、token、cache hit/miss 和超时为成本诊断。所有 100 题都必须留在账本内；若未完成，标出未计分和退出原因，只作 partial 分析，不能把未运行任务补零成算法失败。

这是单次新运行对历史 M0 的开发集比较，不是同期随机对照；5 场世界与黑板承接，任务不是独立样本，远端模型别名和采样也未被固定。超过 80/100 可作为下一阶段决策依据，不能直接证明统计显著、全量提升，或单独归因于某个新增模块。后续付费实验和 dev240/M2 仍需另行决定。
