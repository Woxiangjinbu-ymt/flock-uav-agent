# 教师轨迹

[正式首遍的 A 折数据](../../datasets/teacher/2026-11-11-fold-a/)包含逐题执行索引、逐轮取舍、训练和内部验证的公开轮，以及筛选后交给 SFT 阶段的 386 轮。首批重建以评测时的 HTTP 请求和模型实际返回的回复为来源，使用工具调用 ID 对上执行回执。

在仓库根目录使用项目 Python 3.11 运行。下面的命令只读封存的原始执行数据，在一个新目录复算训练、内部验证和审阅三个压缩包，并与原发布的 SHA-256 逐一比对。

```sh
agent4drone/.venv/bin/python -m posttraining.trajectories.rebuild \
  --package all --output /tmp/flockcue-rebuilt-release
```

要从同一批原始 HTTP、模型处理器和工具事件重新生成证据索引，可运行以下命令。`--workspace` 与公开包重建器接受相同的归档位置。该步骤读取已登记的 A 折场景和角色，核对每个场景的封存摘要、每题命令、请求、响应和回执，生成题级、轮级索引及一道完整公开样例。程序逐文件对照发布清单的 SHA-256，通过后才交付新目录。[索引重建代码](index_wire.py)

```sh
agent4drone/.venv/bin/python -m posttraining.trajectories.index_wire \
  --output /tmp/flockcue-rebuilt-wire-index
```

[重建入口](rebuild.py)默认只导出经审阅的训练轮。可用 `--audit` 指定发布索引目录、`--workspace` 指定原始归档所在工作区。原始文件按索引中的路径存放在该工作区的 `outputs/` 下。所有输出必须写入新的目录。该命令复用已登记的场景、工具证据索引和审阅判定，具体重建范围及审阅标准见[首批构建报告](../../docs/results/flockcue-teacher-trajectories-a-20261111/REPORT_CN.md)。

每一行的 `messages` 和 `tools` 为当轮输入，`target` 为实际教师回复，`post_action_receipts` 为回复后才返回的工具结果。下一轮才能观察到这些回执。

[pilot](pilot/) 包含早期两场 40 题的采集与审阅入口。同级的 `runtime.py`、`teacher_*.py`、`score.py` 和 `verify.py` 为连续会话执行、教师通信、故障恢复和独立终态核验所用模块。[试采协议](../../docs/results/deepseek-teacher-pilot40-live-20261111/REVIEW_PROTOCOL_CN.md)
