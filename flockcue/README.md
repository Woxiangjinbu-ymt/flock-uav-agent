# FlockCue Agent Harness

本目录包含正式 600 题部署实际使用的策略。服务按会话保存公开黑板，每道题单独建立执行证据账本和动作预算。模型可直接使用无人机工具，也可提交、恢复或取消一个适用的执行子程序。策略从公开观测和工具回执判断是否需要继续或重规划。终态通过率由仿真评分器独立计算。

| 位置 | 用途 |
| --- | --- |
| [service.py](service.py)、[api.py](api.py)、[agent.py](agent.py) | DeepSeek 请求、HTTP 服务、命令执行与停止 |
| [prompt.py](prompt.py)、[provider.py](provider.py)、[tools/](tools/) | 实际系统提示、感知参数规范、规划及无人机工具 |
| [harness/](harness/)、[evidence/](evidence/) | 执行子程序、预算、按作用域记录的证据与完成判定 |
| [recording/](recording/) | 工具链、SDK 实际请求和响应的审计记录 |
| [profile.json](profile.json) | 正式运行采用的策略开关。服务的模型和温度由部署入口绑定，配置中的本地脚本模型字段保留在原始摘要中 |

复用平台的 HTTP 工具客户端、无人机工具、规划器和基础 Agent 服务协议。FlockCue 的阶段执行、证据中间件、任务恢复、教师调用适配及记录位于上表对应模块。正式评测入口见[执行 runtime](../runtime/)。

## 离线验证

使用 Python 3.11。安装在仓库根目录执行，依赖锁与正式评测的 Agent 环境一致。

```sh
uv venv --python 3.11.16 flockcue/.venv
uv pip sync --python flockcue/.venv/bin/python flockcue/requirements.txt
flockcue/.venv/bin/python -m flockcue check
flockcue/.venv/bin/python -m pytest -q flockcue/tests
```

准备好仿真服务的独立环境后，可以用闭环回放检验一次真实工具执行。

```sh
flockcue/.venv/bin/python -m flockcue smoke --output ../flockcue-hover-smoke
```

使用新目录保存回放。回放在本机启动模型替身、正式策略 HTTP 服务和仿真服务，执行场景 `04cd6269` 的首题 `a05a871a`。它检查工具回执、飞行终态与原题两个评分条件，并核对首个 SDK 请求。比对只统一每次运行新建的命令 UUID。需要独立服务时使用 `python -m flockcue.service --help`。完整部署由 [runtime](../runtime/) 设置仿真地址、任务租约和控制令牌。
