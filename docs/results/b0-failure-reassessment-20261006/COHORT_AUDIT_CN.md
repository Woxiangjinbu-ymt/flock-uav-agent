# B0 首次执行失败：分母与证据边界复核

本报告只分析原版 B0 的首次执行，不采用恢复重跑成绩，不运行模型或仿真，也不修改运行架构。

输入固定为 baseline-deployment-20261005/tasks.json 中 cohort=first_attempt_no_replay：DeepSeek Flash 与 Qwen 各 75 场、1500 题、9396 个检查点；全部 generation=1。

| 互斥结果分类 | DeepSeek | Qwen |
|---|---:|---:|
| 首次部署成功 | 1243 | 1150 |
| 可观察任务失败，非超时 | 169 | 89 |
| 可观察任务失败，超时 | 34 | 192 |
| 终止服务错误 | 37 | 0 |
| 已计分但 worker 退出未确认 | 0 | 13 |
| 执行中断且未计分 | 1 | 0 |
| 停止后未执行 | 16 | 56 |

部署失败仍计入固定 1500 题分母；但是未执行、未计分、服务错误和退出未确认不能全部解释为 Planner 失败。

DeepSeek 部署失败 257 题中，203 题属于外判失败、worker 已确认且无终止服务错误（169 非超时、34 超时）。37 题服务错误中 25 题外判失败、12 题外判通过；状态码为 36 次 402、1 次 429。另有 1 题未计分中断和 16 题未执行。

Qwen 部署失败 350 题中，281 题满足同一可观察失败条件（89 非超时、192 超时）。另有 13 题已冻结计分但 worker 退出未确认（12 题外判失败、1 题外判通过，全部超时），以及 56 题未执行。没有已记录终止服务错误。

超时与失败不是同一集合：DeepSeek 共 35 题超时，其中 1 题外判通过；Qwen 共 220 题超时，其中 16 题外判通过。203/281 是后续行为分析的候选集合，不是已经证明由规划导致的失败数。

## 旧机制诊断可复用到哪里

旧报告是中期快照：DeepSeek 为选择恢复后的 1500 题，Qwen 为 1280 题，不能把原来的 429 条机制记录直接当作首次执行的全量机制统计。

逐条检查旧 trace 的文件哈希、所在 evidence generation、command、开始时间与 job 的对应关系：DeepSeek 217 条旧记录中仅 190 条属于相同首次执行，27 条来自重跑；Qwen 212 条中仅 200 条相同，12 条来自重跑。不同代际的 39 条不得用于解释本次首次执行结果，即使 task_id 相同。

同代匹配共 390 条，全部属于可观察任务失败。仍有 DeepSeek 13 题、Qwen 81 题可观察失败没有同代旧诊断；按全部部署失败口径则分别有 67、150 题没有覆盖。旧 bucket 仅保留为旧标签，本次没有重新证明其因果解释。

未覆盖任务逐条见 cohort-summary.json 的 uncovered_eligible_tasks；39 条不适用的旧重跑记录见 legacy_diagnosis_reconciliation.excluded_other_generation。failure-ledger.csv 保留全部 607 条首次部署失败及每条诊断覆盖状态，没有丢弃未执行项。

## 核验范围与复现

核对 3000 条原始 results、2927 条已计分任务的 checks 响应/叶节点值及 barrier/window、2914 条 worker 退出确认、全部首次场景终止服务事件；消费的 13267 个文件在生成前再次计算 SHA-256，其中 13264 个与既有归档哈希匹配。

本轮未重跑模型、未重放仿真，也未重新执行完整原 checker 检查树；原 checker 回放由既有 baseline-deployment/verification.json 记录，本轮核对其消费证据、原响应和计分字段。输入清单见 input-sha256.json。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 experiments/analysis/reassess_b0_failures.py
```
