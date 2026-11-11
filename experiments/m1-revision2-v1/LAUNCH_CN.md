# M1-R2 fast100 单组启动记录

用户授权后于北京时间 2026 年 10 月 7 日 **21:10:50** 启动。唯一运行组为 `m1r2`；M0 沿用封存的历史 80/100，其他旧方法沿用现有结果，不重新执行。

| 配置 | 本次取值 |
| --- | --- |
| 方法 | `69dd34c067ec97b746db5180684594c546a35172`，标签 `m1-evidence-r2` |
| 模式 | `UAV_TASK_PROGRESS_MODE=evidence_r2`；实际请求审计开启 |
| 数据 | 原 fast100，5 场、100 题、605 检查，原场内顺序 |
| 预算 | 2 workers，每题原 500 秒，原温度与模型配置 |
| 模型 | 官方 DeepSeek `deepseek-flash`，与历史组配置一致 |
| Job | `jobs/20261007-211050-guard-a754c424` |
| Attempt | `experiments/runs/20261007-211050-m1r2-fast100-f5985403` |
| 重试 | 不补跑、不恢复、不创建第二次 attempt |

## 早停规则

沿用此前至少 **40 道已计分题**的观察起点，阈值改为**相同题目少成功 5 题及以上**：

`已计分同题数 ≥ 40 且 同题 M0 成功数 − M1-R2 成功数 ≥ 5`

这里比较的是成功题数净差，等价于同题 `L − W ≥ 5`；不是累计 L 达到 5，也不是中途低 5 个百分点。没有拿当前部分成绩与完整 80/100 混比。例如计分 40 题，M0 在这 40 题成功 34 题、R2 成功 29 题，便触发；成功 30 题则不触发。

外层 `experiments/analysis/m1_revision2_guard.py` 每 5 秒检查完整账本，只计入原评分已最终落盘的题。触发时先保存 `stop-trigger.json`，再向其直接持有的 job worker 发送一次 SIGTERM，由原冻结执行器停止派发并安全收尾。运行中的任务可能在收尾期间额外计分，最终报告需保留触发与最终快照的差异。已经全部 100 题计分时完成验收，不追溯早停。

监控故障也会请求安全停止，避免失去预算保护后继续运行。故障停止与性能退化分别记录。未计分题保持空白，不补零为算法失败，不将 partial 成绩外推为全 100 题结果。这是经费保护规则，不是统计显著性判据。

## 冻结与启动验收

开跑前通过 94 项启动/监控/旧入口回归测试；实际冻结包解压后禁止 socket 网络连接，通过 84 项方法、图执行、service 配置和资源封存测试。三套依赖环境与历史 M0 相同。准备阶段付费模型调用为 0。

新 agent 提交仅包含已通过 M1-R2 离线验收的四个 agent 文件，未切换工作区 HEAD 或改动原暂存区。共享源码冻结为 119 文件：原执行代码只纳入之前验证过的资源 writer 关闭修复，另有封存说明、相关测试和本研究独立入口；数据、评分和任务预算不变。允许差异的确切哈希保存在 `frozen/study.json`，其他共享源码变动会拒绝启动。

旧 M1-R 终态快照 1,155 个文件和既有报告哈希再次验证不变；其原场景封存异常仍保留。新修复不将旧 81/100 追认为完整验收成功。

`jobs/20261007-211050-guard-a754c424/startup-verification.json` 记录首批实际完成轨迹：`evidence_r2` active、revision=`m1-r2`、observer/audit errors 为空且有实际请求正文哈希。它是启动时快照，不代表整轮最终验收。

## 监控和结果验收

自动化 `b1-m1` 已更新为「M1-R2 fast100 五题退化早停与验收」，每 10 分钟检查。正常运行保持静默，仅在完成、早停、失败或需要处理时通知；结束验收后暂停。早停由独立本地监控执行，不等待自动化提醒。

终态为 `completed_and_verified` 后，使用新冻结入口核验：

```bash
controller/.venv/bin/python -B scripts/m1_revision2_pilot.py verify \
  experiments/runs/20261007-211050-m1r2-fast100-f5985403
```

验收报告保存至新的 `docs/results/m1-revision2-fast100-20261007`。主指标为原任务成功率、历史同题 W/L、净增和逐场差异，并跟踪旧四个 W、三个 L 的变化；成本报告时间、token、缓存与真实调用数。来源绑定、公开反馈状态和内部标签是方法诊断，不替代任务收益。

fast100 只有五个开发场景，世界和黑板承接；M0 为历史参照，旧 M1-R 存在封存问题，R2 新增公开进度工具，且没有同期提示/观测对照。结果用于后续决策，不宣称随机因果、统计显著或全量提升。进一步付费实验由用户决定，不自动进入 dev240、M2 或 M3。

## 文件位置

- `frozen/study.json`、`frozen/m1r2/source.zip`：本轮方法和公共源码冻结包。
- `frozen/historical-m0.json`：100 题历史 M0 账本。
- `preflight.json`、`preparation-verification.json`：冻结包、依赖与旧证据核验。
- `launch-tests.log/xml`、`preflight-tests.log`：离线测试记录。
- Job 内 `guard-policy.json`、`request.json`：固定停止规则和源码哈希。
- Job 内 `guard-status.json`、`status.json`、`observations.jsonl`：实时状态和新增已计分题快照。
- Job 内 `stop-trigger.json`、`final-paired-snapshot.json`：触发时及收尾后结果，分别在对应事件发生后生成。
