# Episode guard 对照源码归档

本目录仅归档，**没有执行新的测试**。`original/` 内为已经执行的脚本与 66 项 / 0 失败记录，原字节不变；它们保留当时 `/tmp`、主干路径，不作为今后默认执行入口。

`replay_episode_guard.py` 是另存的、尚未执行的重放适配版，默认读取同目录下的 `source-before/`、`source-after/` 和 `shared/`。它保留原后端、线程调度、方法抽取和断言，只改输入/输出路径选择，并显式按路径加载冻结的 `harness_core.py`。此文件不可冒称已通过原 66 项。

归档输入：

- `source-before/`：修复前 `uav_agent.py` 与 `agent_api_service.py`，作为 AST 输入，不直接导入依赖模型框架的完整模块。
- `source-after/`：通过原 66 项的最终同名源码。
- `shared/agent4drone/harness_core.py`：实际使用的 runtime；其导入依赖均为 Python 标准库。
- `shared/` 其余 3 份文件：原记录的 hash 对照上下文，不导入、不执行；保留它们使原 `source_hashes` 可完整核对。

在 Python 3.10+ 环境中，可从任意工作目录调用以下命令（不会调用模型、服务或仿真）：

```sh
python3 /absolute/path/to/episode-guard-replay-01/replay_episode_guard.py --output /tmp/episode-guard-replay-result.json
```

不传 `--output` 时写同目录 `replay-result.json`；输出已存在则拒绝覆盖。可用 `--source-before`、`--source-after`、`--shared-root` 显式选择其他根；更换输入的结果必须独立解释，不能作为当前冻结版本的证据。不要覆盖 `original/` 或原报告。

`MANIFEST.json` 列出所有归档文件的 SHA-256、输入角色及原检查记录出处。仅复制并核对 hash，不代表又运行了 66 项检查；BaseException 清理与 World reset 的结论边界仍见原报告。
