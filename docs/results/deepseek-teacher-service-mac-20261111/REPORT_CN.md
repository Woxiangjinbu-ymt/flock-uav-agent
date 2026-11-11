# 教师连续会话的真实 HTTP 入口与断线恢复

接收模型请求的是监听 `127.0.0.1` 的本地 HTTP 服务。它先把收到的每个 POST 摘要写盘，再返回响应或制造故障。把接收端记录与客户端预留逐笔对齐，可以检查一次请求是否在重启后被重复发出。本次真实 DeepSeek 调用、实际支出、GPU 卡时及新增正式 SFT 样本均为 0。[独立接收端与重启验收脚本](acceptance.py)

## 实测结果

| 情形 | 模拟服务端记录 | 恢复与对账结果 |
| --- | ---: | --- |
| 正常两场、回复可变轮数 | 60 次 POST，40/40 题独立评分 | — |
| 对端已接收请求，返回响应前断线 | 1 次 POST | — |
| 对端已接收请求，强制终止工作进程 | 1 次 POST | — |
| 协调进程退出，原工作进程仍在等待响应 | 1 次 POST | 再次启动时，存活的工作进程持有排他锁，账本字节未变。工作进程退出后核对，仍只有 1 笔预留。 |
| 工作进程正常封存，独立评分尚未启动时中断 | 恢复前后均为 1 次 POST | — |

服务端回传的模型名与请求别名不同；有的回复包含 fingerprint，有的省略该字段。两种回复均可核对原始字节、工具回执和用量。测试使用人民币模拟账本。该实验验证了客户端在这些中断下不会重复派发；供应商的实际扣款须在真实试采后对账。[核验摘要及证据摘要值](verification.json) · [本地证据归档](../../../outputs/teacher-service-mac-20261111/loopback-recovery.tar.gz)

## 入口与恢复规则

`prepare` 为冻结的 40 个题位建立一份按账户币种计价的账本。`advance` 明确指定一个场景。已执行并评分的场景从封存快照、评分和账本校验后只读复用。开始第二场前先核验第一场。收齐返回后将原始响应、服务实际返回的模型名、可选的 fingerprint、usage 和结算金额关联到同一条预留。[双场运行入口](../../../scripts/run_teacher_service.py) · [逐场审计](../../../scripts/flockcue_session_cpu/teacher_service_audit.py)

真实服务模式只准执行进程的教师传输连接注册的 HTTPS 地址。API 密钥从本机环境提供，评分子进程隔离外网与凭据。SDK 与底层 HTTP 自动重试均设为 0。单题及整批账本、并发锁、独立评分共用于真实模式与本地模式。场内执行若无法证明接续状态，余题保留为未启动。[传输与恢复实现](../../../scripts/flockcue_session_cpu/teacher_service.py) · [网络边界](../../../scripts/flockcue_session_cpu/teacher_network.py)

已注册的教师清单沿用 B 版感知工具说明。本次 A100 的无参感知是学生接口 C 的对照实验。实际试采的工具版本应在发出第一笔真实请求前与要蒸馏的学生接口对齐并登记。[试采方案](../../specs/04-deepseek-pilot40/plan.md)

## 本地复现

在项目根目录运行下列命令，输出目录中的各案例子目录须为新的目录。验收器创建本地 HTTP 模式，使用模拟凭据。

```sh
agent4drone/.venv/bin/python docs/results/deepseek-teacher-service-mac-20261111/acceptance.py --output /tmp/flockcue-teacher-http-audit
```
