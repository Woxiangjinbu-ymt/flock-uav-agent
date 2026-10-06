# M1 任务进度提示：fast100 同期开发对照

本入口只负责冻结、运行和核验两个方法，复用原 `reproduction_parallel` 场景执行器、原 checker、500 秒截止与 worker 生命周期。它不是旧 B1 中断试验的恢复，也不重写原研究记录。

| 方法 | Agent Python 来源 | 运行模式 |
|---|---|---|
| M0 | `cb80fde8269a09795782a6ef046d04c381b3f3c9` 原 B1 定向补救代码 | `UAV_TASK_PROGRESS_MODE=off` |
| M1 | `prepare --method-commit` 显式提供的已提交版本 | `UAV_TASK_PROGRESS_MODE=summary` |

每臂全新执行 fast100 的 5 个完整场景、100 题、605 个原检查，固定顺序 `ad34350a, ef36e27a, 3973d0d6, 823c15d5, 35ce64d8`。各场保留原20题顺序、世界与session黑板承接，2个场景worker。官方DeepSeek `deepseek-flash`、temperature=0.1，500秒动作窗口，原排空与退出等待不变。

M0与M1顺序运行，不绕过全局runner锁。M0完整封存并核验后才允许M1启动。两臂各有独立attempt，不混代次、不自动重试、不另做付费smoke。任何服务、worker或基础设施失败停止派发；需要新实验时另建研究目录，不恢复本轮或删除旧attempt。

## 先离线冻结

在仓库根目录执行，替换尖括号参数为真实M1提交。此步骤不启动Agent服务、不访问模型；检查配置、key存在和依赖，但不输出或归档key。

```sh
PYTHONDONTWRITEBYTECODE=1 controller/.venv/bin/python -B scripts/m1_progress_pilot.py prepare \
  --method-commit <M1完整提交SHA>
```

准备目录默认为本目录。仅在准备新研究时可显式使用 `--study-dir <新目录>`，同一路径在后续run中保持一致。已存在的 `frozen/` 不覆盖。`prepare` 从Git提取两臂全部Agent Python，包括M0不含的M1新文件；共同server/controller/scripts和requirements以准备时快照保存。实际依赖使用现有三个虚拟环境，保存freeze并在启动/结束核对，期间不要安装依赖。

`frozen/study.json` 固定两臂commit、模式、数据、公共源码和依赖身份。每臂source.zip与runtime独立，不从活动工作区读取Agent执行源码。prepare后共同runner/server/checker文件变化会拒绝启动；应在公共启动器全部就绪后再prepare。Agent工作区后续改动不会改变已冻结的两臂源码。

## 显式在线运行

以下命令会调用模型，先M0，再M1；不是离线检查：

```sh
PYTHONDONTWRITEBYTECODE=1 controller/.venv/bin/python -B scripts/m1_progress_pilot.py run --arm m0 --method-commit <M1完整提交SHA>
PYTHONDONTWRITEBYTECODE=1 controller/.venv/bin/python -B scripts/m1_progress_pilot.py run --arm m1 --method-commit <M1完整提交SHA>
```

run创建attempt后，使用 `exec` 替换当前进程，进入该attempt的冻结入口及runner。PID不因这个替换改变。外层应使用已验证的持久启动器：独立session、stdin关闭、stdout/stderr写到attempt之外的启动日志，记录PID及启动身份、退出码与终止信号。先用本地假Agent验证调用端断开时runner仍能存活。单写nohup不是完成了可靠性验证。

现有服务supervisor的父管道保护保持启用，runner消失时它应回收所属服务。SIGKILL无法执行finally；外层观察器应记录中断事实，不能把没有suite seal的残留running状态当成仍运行，也不能擅自把未计分题改成超时。启动日志不要放进运行中将封存的attempt内部，否则最后打印输出会破坏seal。

注册、运行状态保存在研究目录的 `runs.json`；方法身份同时进入attempt的 `method-freeze.json`、plan和manifest的 `metadata.freeze`，由现有identity连同数据和配置约束。进程环境强制设置每臂模式，避免外部summary变量污染M0。

## 核验与指标

```sh
PYTHONDONTWRITEBYTECODE=1 controller/.venv/bin/python -B scripts/m1_progress_pilot.py verify <attempt目录>
```

verify进入冻结解释器环境，检查suite文件哈希、方法身份、原数据、generation1、各场seal与原checker回放。只有完整100题才给最终通过率；partial记录保留固定100分母和未计分状态，不补造分数。此入口不生成带因果判断的比较报告。

两臂通过后，用同题配对账本报告共同成功/共同失败/新增W/回归L及 `(W-L)/100`，逐场解释差异。原B1的50/57仅作历史证据，不代替此次M0，也不能只补40题后拼接。fast100属于开发难例集，不是独立泛化测试；五场有状态承接，不能把100题当独立样本。

本轮固定 M0 后 M1 的运行顺序，仍可能存在时间与服务负载影响；这是开发筛查，正式主实验再预先均衡方法顺序。M1 先以语义功能成立、无系统性回归作为继续研发的必要条件；持平只能暂准，不能声称已证明无退化或有效。若性能或开销暴露问题，分析本轮全部记录后再决定 dev240，不自动扩展实验规模。

两臂总计划200题，不再额外跑模型smoke；资源统计保留所有真实尝试。

离线入口测试：

```sh
PYTHONDONTWRITEBYTECODE=1 agent4drone/.venv/bin/python -B -m pytest scripts/tests/test_m1_progress_pilot.py -q
```

这些测试核对冻结、数据与模式身份和登记/收尾路径，使用假返回；不证明在线成功率，也不代替本地真实HTTP假Agent与持久启动器的生命周期测试。
