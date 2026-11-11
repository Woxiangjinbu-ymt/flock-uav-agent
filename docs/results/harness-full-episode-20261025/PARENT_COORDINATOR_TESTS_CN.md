# 父协调器纯测试

2026-10-25。新增 [test_harness_episode.py](../../../scripts/tests/test_harness_episode.py)：**21 个测试函数、71 个参数化用例，首次执行 71 passed / 0 failed**，pytest 0.33 秒。六个输入源码/测试文件的运行前后 SHA-256 一致，未修改生产协调器或 worker。

原始[日志](testing-attempt01.log)和[运行元数据](testing-attempt01.json)保留；[带计数的稳定元数据](PARENT_COORDINATOR_TESTS.json)引用同次执行，不是新运行。测试只使用临时合成文件、不可反序列化的占位快照字节、假 launcher/Popen 与受控线程。socket 和真实子进程入口被禁用；没有启动 Agent、SDK、worker、scorer、FullWorld、provider 或旧 campaign。

| 检查 | 实际覆盖 |
| --- | --- |
| 冻结与不可覆盖 | 固定 reference-B 与四组 A→B，共九行；原 500 秒与配置字段。已有输出拒绝覆盖；manifest、协议、源码、输入的 hash 变化拒绝 |
| 路径 | 普通子目录可用；绝对路径、父目录逃逸、叶子及目录符号链接拒绝 |
| 隐藏信息隔离 | 明文、base64 请求和跨响应 chunk 的 canary 检测；嵌套工具内容中的 checker 定义检测；公开进度和工具 ID 可保留；缺失或损坏字节不能通过检查 |
| Worker 材料 | 未退出、非零退出、watchdog 超时不能评分；Harness/归档未闭合、无效归档、完整性标记冲突、网络尝试、snapshot 篡改、未列出文件、错误回合身份或私有泄漏拒绝 |
| 独立评分引用 | scorer 必须绑定已退出 worker 的结果 hash 与 snapshot hash；canary 阳性、原 checker 树一致、未导入 Agent 和 checker 正常执行分别检查；worker 退出后的 console 变更也会被 seal 拒绝 |
| 正文 | 独立 transport oracle 对照正确字节；缺 request 正文、漏 response chunk、响应 hash 不符、缺结束或 partial 响应拒绝 |
| 终止边界 | 四种预声明条件的最小合成公共动作记录可通过；虚假 timeout trigger、非原截止、错误触发请求、未提交动作、无起飞物理效果、终止后多余动作及评估侧 API 混入被拒绝 |
| 协调顺序 | 只依冻结顺序启动，私有分数真假不改变 B 配置/顺序；校验失败立即停止、不评分不续跑；第二次调用拒绝恢复失败轮次；wait 返回前不启动 scorer/B，watchdog 路径调用 kill 后继续 wait |

Worker/scorer 元数据单元测试显式替换 `validate_wire` 和 `validate_boundary`，避免虚构完整仿真产物；这两个函数另用最小字节记录及公共动作记录直接测试。协调顺序测试替换产物校验和汇总，只检验父流程；不把这些合成例称为完整 episode。合法的受控 failure/timeout/cancel 可在进程正常退出、材料闭合后评分，这与进程/材料无效而拒绝评分是两种情况。

**71 项是代码测试分母，与九回合验收、其独立不变量检查及上一批 825 项均不相加。** 它不证明自然模型选择、泛化、Teacher 数据质量、进程池复用或真实远端动作撤销；真实 worker/scorer 的证据以本批主报告和封存结果为准。

复现命令（仓库根目录；不会启动实际 campaign）：

```sh
agent4drone/.venv/bin/python -m pytest scripts/tests/test_harness_episode.py -q
```

测试中的 workspace、协议和 snapshot 均为 pytest 新临时目录中的合成数据，不会改写已封存 run。
