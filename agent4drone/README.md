# Agent4Drone

[main.py](main.py) 启动原平台的无人机 Agent，[agent_api_service.py](agent_api_service.py) 提供 HTTP 服务。[服务运行说明](README_SERVICE.md) · [API 用法](README_API.md)

[uav_agent.py](uav_agent.py) 管理模型、工具和上游 Agent 生命周期。配置启用 FlockCue 时，调用 [FlockCue 接线模块](../flockcue/runtime/integration.py)。执行记忆、工具回执和任务证据的当前实现位于 [flockcue/](../flockcue/README.md)。旧文件名的相对链接供已发布的运行器和代码清单读取。

重放论文中的原版基线时，按登记的十月源码和实验设置准备原版执行环境。同日 200 题比较的来源及可比范围见[配对评测报告](../docs/results/agent4drone-matched200-20261111/REPORT_CN.md)。
