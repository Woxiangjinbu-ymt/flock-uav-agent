# 从执行原件重建教师轨迹

在仓库根目录使用 Python 3.11。命令默认读取本地教师发布目录的 `raw/`。`--run` 可指定持有同一批 `manifest.json` 和 15 个场景子目录的其他位置。输出使用新的目录。

```sh
python3 -m posttraining.trajectories.index_wire --output /tmp/flockcue-wire-index
python3 -m posttraining.trajectories.rebuild --package all --output /tmp/flockcue-public
```

第一条核对实发请求、教师响应、调用 ID、执行结果和场景封存摘要，重建 300 道题的索引、4,431 个逻辑轮及一个公开样例，与原发布文件逐字节对照。保留的来源坐标仍使用发布时的逻辑路径。第二条按[审阅清单](../../datasets/teacher/2026-11-11-fold-a/audit/)重建训练、内部验证和筛选后的三个公开包，同时核对原始行和压缩输出的 SHA-256。`--package reviewed` 可单独重建经审阅的 386 个决策轮。
