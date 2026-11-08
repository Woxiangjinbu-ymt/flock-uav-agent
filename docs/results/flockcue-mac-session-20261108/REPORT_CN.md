# Mac 已跑通原基准的一场 20 题

2026-11-08，在 Mac arm64、macOS 15.7.4、CPython 3.11.16 上，从冻结的 600 题中取清单第一场 `04cd6269`（区域分配与巡逻，Easy，5 架无人机）。一个 FlockCue Agent 在同一个仿真世界里按原顺序做完 20 题。另一个进程从原始场景重新开始，也做完了 20 题。两次逐题结果相同，79 个原始检查项的结果也逐项一致。[冻结题单](../../../experiments/flockcue-benchmark-600-v1/) · [逐题验收记录](verification.json) · [证据摘要](verification-seal.json)

## 在同一场内，下一题能接着上一题做

- 第 1 题要求 1 号机升到 9 米并悬停。题末高度为 9 米，电量为 99.4%，黑板记录了目标 `Polygon Target 1`。第 2 题准备结束时，1 号机已落地、电量为 100%，黑板仍有这个目标，会话里仍有前题的两条指令记录。[第 1 题终态](../../../outputs/flockcue-mac-session-20261108/normal/tasks/01-a05a871a/boundary.json) · [第 2 题起点](../../../outputs/flockcue-mac-session-20261108/normal/tasks/02-528d6d24/before.json)
- 第 2 题中，4 号机的水平坐标从 `(584, 274)` 变到 `(585, 274)`。第 3 题开始时，它仍在 `(585, 274)`，高度归零、电量为 100%。两次正常运行各有 19 次题间交接，19 次都继承了非空黑板，18 次继承了偏离原场起点的水平位置。[第 3 题起点](../../../outputs/flockcue-mac-session-20261108/normal/tasks/03-a377d715/before.json)
- 重开整场时，5 架飞机回到原始位置，黑板为空，仿真时钟归零，初始仿真状态摘要与首次运行一致。飞行状态、场景级指令历史和黑板各按原协议更新；题间重新装载无人机对象后，单机临时历史可以清空。[重启账本](../../../outputs/flockcue-mac-session-20261108/restart/session.json)

## 四次运行的结果

| 运行 | 计划题数 | 有独立评分 | 原检查通过 | 本地脚本模型请求数 | 观察到的行为 |
| --- | ---: | ---: | ---: | ---: | --- |
| 正常运行 | 20 | 20 | 1 | 100 | 40 条实际飞行动作事件，5 次 Agent 自报成功，独立检查确认第 1 题通过。 |
| 重开整场 | 20 | 20 | 1 | 100 | 换新进程，从同一初态出发，逐题逐项评分与首次相同。 |
| 第 2 题超时 | 20 | 20 | 1 | 97 | 将此题的截止缩短到 1.5 秒。模型迟到的起飞指令没有改变世界，另一次迟到的落地请求也被拒绝。第 2 题按冻结终态评分，其余 18 题继续。 |
| 第 2 题服务中断 | 20 | 1 | 1 | 6 | 模拟服务经本地传输向 SDK 返回一次 HTTP 503。第 2 题记录为服务失败，飞行动作提交为零。第 3 至 20 题逐一登记为未启动。 |

脚本只按公开指令和工具回执做几种固定动作，覆盖列出无人机、起飞、悬停或短距离移动、感知。**1/20 是这套脚本在该场景的任务成绩**。本次交付验证的是以后让真实模型来做题时，环境可以连续运行、独立评分和记录故障。303 次模型请求全部经过本地模拟传输，逐条核验了请求正文及摘要，未发现私有评分接口、检查定义或预置动作字段。GPU 卡时和付费模型调用均为零。该场 20 题登记为本轮工程开发已见，脚本轨迹不作教师示范，也不计入 A/B/C/D 的正式成绩。[原始四场归档](../../../outputs/flockcue-mac-session-20261108/)

## 执行和评分口径

每场共用一个 Agent 和一份会话黑板。v5 的 93 份冻结源码已通过基线核验，本次运行按原连续会话显式打开共享黑板。每题使用原准备函数切换到当前场景、落地、充电，各等 2 秒，题间等待 1 秒。准备与执行时按真实流逝时间推进本地仿真，冻结评分期间暂停。普通题保持 500 秒窗口。[本次运行器](../../../scripts/flockcue_session_cpu/runtime.py) · [四场共用的源码与配置摘要](../../../outputs/flockcue-mac-session-20261108/normal/run-input.json)

每道按时返回的题先封存终态，再由隔离进程运行原始检查树。通过的题按原协议标为已完成，供下一题承接。场景 worker 退出后，另一进程从各题快照逐一复评，核对全部叶子结果及完成标记。超时题也保留冻结评分，服务故障和未启动题保留原始状态与分母。评分进程不加载 Agent。[评分实现](../../../scripts/flockcue_session_cpu/score.py) · [独立验收器](../../../scripts/flockcue_session_cpu/verify.py)

本次用进程内仿真接口运行，迟到用例覆盖截止后才返回的动作。原部署服务对截止前已入队的请求还要等待排空，本地动作封存器对跨截止才完成的动作采用回滚。另用原 HTTP 执行器的两个本机用例核验，已达成目标的超时题在 worker 安全退出后可以计分并继续；无法确认退出时保留已得分，下一题不启动。[原 HTTP 用例记录](../../../outputs/flockcue-mac-session-20261108/original-live-regressions.log)

## 在 Mac 重跑

从项目根目录执行。Agent、server、controller 三个环境均使用 Python 3.11.16 和各自锁定的依赖。原评分器导入 controller 环境中的 Tk 和 tksheet，运行时无需打开图形窗口。每次选一个新的输出目录。

```sh
PYTHONPATH=scripts agent4drone/.venv/bin/python -m flockcue_session_cpu accept \
  --output outputs/flockcue-session-repeat-01
```

一次执行会生成四份账本、每题终态、工具与模型记录、原检查结果，以及总验收文件。只想做一场时，将 `accept` 换为 `run`，可用 `--session` 指定冻结场景 ID。本次只对上述一场做了完整验收。[运行入口](../../../scripts/flockcue_session_cpu/__main__.py)

## 下一步在 Linux 上无卡复验

复制原 CPU 交付包、这次的会话补充包及各自的 `.sha256` 文件，到目标 Linux 主机上的同一空目录。两个包解开后共用 `flockcue-g1g2/`。原包提供 Agent 和仿真，补充包提供这次的会话入口、原检查器、controller 依赖锁和冻结题单。[原 CPU 包](../../../outputs/flockcue-g1g2-cpu-20261108.tar.gz) · [原包摘要](../../../outputs/flockcue-g1g2-cpu-20261108.tar.gz.sha256) · [会话补充包](../../../outputs/flockcue-session-cpu-addon-20261108.tar.gz) · [补充包摘要](../../../outputs/flockcue-session-cpu-addon-20261108.tar.gz.sha256)

```sh
sha256sum -c flockcue-g1g2-cpu-20261108.tar.gz.sha256
sha256sum -c flockcue-session-cpu-addon-20261108.tar.gz.sha256
tar -xzf flockcue-g1g2-cpu-20261108.tar.gz
tar -xzf flockcue-session-cpu-addon-20261108.tar.gz
cd flockcue-g1g2
sha256sum -c SHA256SUMS > /dev/null
sha256sum -c SHA256SUMS.session-cpu > /dev/null
uv python install 3.11.16
bash scripts/setup_student_cpu.sh
uv venv --python 3.11.16 controller/.venv
uv pip sync --python controller/.venv/bin/python controller/requirements.txt
uv pip check --python controller/.venv/bin/python
controller/.venv/bin/python -c 'import tkinter, tksheet'
PYTHONPATH=scripts agent4drone/.venv/bin/python -m flockcue_session_cpu accept \
  --output tmp/session-linux-01
```

移交前已在 Mac 新目录解开两个包，校验原包 490 个文件和补充包 24 个文件，并用本机已有的三个环境重跑服务中断用例。独立评分得到 1 题通过、1 题服务失败、18 题未启动。补充包为 12,168,759 字节，独立重打包的 SHA-256 同为 `ab0238b6f6b6aecdd73e25de5d857ee7fd70eea42045a8c97fe520c4a8028a4f`。[打包入口](../../../scripts/package_flockcue_session_cpu.py)

Linux 的环境重建和四场验收待在目标机实际执行，以该机的 `verification.json` 为准。真实学生的测速、开关思考对照、教师采数及训练仍有各自的准入和预算。[原学生接入无卡步骤](../thinking-student-cpu-20261108/LINUX_CPU_CN.md)
