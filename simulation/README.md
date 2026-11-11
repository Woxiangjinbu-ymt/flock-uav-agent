# 多无人机仿真与独立评分

[server/](server/) 保留正式部署实际使用的 MultiUAV-Plat 仿真 API、模型和控制器。运行器按一秒周期更新移动目标、当前场景和天气，并在评分前暂停更新。[checker.py](checker.py) 提取自原控制器的执行检查树方法，递归处理 AND、OR、NOT 和叶节点的预期值。Agent 只接收公开工具与执行回执。

从项目根目录单独启动受任务窗口控制的本地仿真服务。

```bash
simulation/server/.venv/bin/python -m runtime.server --port 8000
```

正式运行由[执行器](../runtime/README.md)创建场景，打开动作窗口、回收请求并用独立权限读取检查结果。仿真和检查器原件各自的版权声明见 [server/LICENSE](server/LICENSE) 与 [CHECKER_LICENSE](CHECKER_LICENSE)。
