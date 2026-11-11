# 正式评测结果

2026-11-11，FlockCue + DeepSeek 在冻结的 30 场、600 道题中独立通过 544 题。十月 Agent4Drone 在同题通过 506 题，两次 DeepSeek 服务指纹不同。逐题表保留全部失败题、终态评分、进程状态与计分原因。[600 题逐题结果](benchmark-600/tasks.csv) · [设置、复算和分组统计](benchmark-600/summary.json)

同日 10 场、200 道配对题中，两臂的成功请求均观察到相同的 DeepSeek 服务指纹。FlockCue 通过 182 题，原版 Agent4Drone 通过 168 题。两者都过 162 题，仅 FlockCue 过 20 题，仅原版过 6 题，两者都失 12 题。两次运行的服务时段不同，比较范围限于这 10 个完整场景。[200 题逐题对照](paired-200/pairs.csv) · [指纹、设置与分组统计](paired-200/summary.json)

表中的原始证据路径沿用发布时的目录。完整报告、原版实现与运行现场在仓库外的 `MultiUAV-Plat-archive-20261111/`，可在其 `docs/results/`、`outputs/` 和 `experiments/` 查阅。Git 提交 `744388f4` 保存当时已纳入版本管理的部分。
