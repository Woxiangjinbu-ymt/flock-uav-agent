# SFT 实验日志

## 关键节点（北京时间）

| 日期 | 实际完成的工作 | 证据 |
| --- | --- | --- |
| 11-11 | 冻结训练侧与验证侧场景，从教师首遍实发请求构建首批样本，完成五档 A100 长度测速 | [场景角色](../../original-benchmark/scene-roles.csv) · [测速](../sft-a100-first-pass/measurement.json) |
| 11-12 02:29 | 首轮 386 步完成；随后独立进程重载并短生成 | [训练、探针与重载](../sft-a100-first-pass/epoch-1/result.json) |
| 11-12 | 完成首轮 A60 配对；首轮仿真达标的 6 题最终均遇终止性服务错误。复审收尾、纠错和核验，20:32 启动混合续训 | [首轮评分](../student-validation-60/REPORT_CN.md) · [失败分析](../student-validation-60/stop-analysis/REPORT_CN.md) · [续训启动记录](continuation/run.json) |
| 11-13 16:42 至 11-14 01:51 | 五轮续训结束；五份检查点各评满 60 题，选中续训 1。其巡逻、搜索、分配场景分别通过 2/20、3/20、1/20 | [训练日志](continuation/train.log) · [五轮评测日志](continuation/post-training.log) · [选模结果](continuation/selection/summary.json) |
| 11-14 至 11-15 01:28 | 对同一权重完成三次配对预算实验，保留基座的零分及全部失败题 | [三批评分与服务配置](budget-validation/) |
| 11-15 | 发布 431 决策去重集；随后纳入基准外 3 个已登记的训练场景，增加 23 题、38 个决策，共 469 个决策、170,071 个监督 token。两包均未用于本表训练 | [431 决策](../../sft/qwen35-2b-recovery-verification/README.md) · [469 决策](../../sft/qwen35-2b-expansion/README.md) |
| 11-17 | A100 到期前将六份 LoRA、两份训练检查点、运行日志和 Harness 现场封存于本机，暂停后续训练 | [本地与 Git 保存范围](../../../artifacts/README.md) |
