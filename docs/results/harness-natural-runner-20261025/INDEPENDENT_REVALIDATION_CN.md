# 本地自然入口 runner 独立复核

只读复核 `run-20261025-01`，没有启动新任务、模型、模拟器或 checker。原始 `summary.json` 的 **335/336** 和封存完整保留。

唯一修正是验收辅助函数的断言：`hung_drain` 的主线程已关闭不完整归档，然后等待父进程终止；其未交付请求应有 `complete=false` 的 response-end。`watchdog_hang` 则在返回前被终止，归档仍开放。该修正不改变运行时行为，也不是追加实验。

- [合成测试记录](oracle-tests-attempt01.json)：33 个 unittest 方法全部通过，其中一个方法检查 12 种固定场景形状；不把子例或日志重采集重复计数。覆盖正文、关联、迟到回执、强杀阶段和错误完成标记的反例。
- [只读复核结果](independent-revalidation.json)：243/243 个 oracle 检查通过；1096/1096 个独立文件哈希、进程顺序和证据检查通过。原来的 93 条非 oracle 结果保持原值，重新合并后为 336/336。计数是验收项，不是任务成功率或泛化样本。
- 12 个 worker、10 个隔离 scorer 使用不同 PID，并在进入后续进程前退出。两个强杀案例均有真实起飞、第三次实际 dispatch，第三请求正文已包含对应公开起飞回执；没有对应第三响应，也没有评分。
- 迟到及缺 usage 的 2xx 完整回执仅在私有诊断中，正文/哈希与独立 transport 日志对应；HTTP 500 错误响应按协议交给 SDK 并归档，其 usage 仍标记未知。所有策略文件未出现 canary 或私有 checker 定义。完整回合的隔离评分有 canary 正对照。原始 fixture、冻结源码、当前及上一批封存哈希均匹配。
- `post_kill_B` 与成功参考首次模型请求的 46064 字节完全相同，无须替换任何 command ID；同初态，三条动作和任务检查通过。初版额外审计误要求请求必须含 command ID，已据实际零出现修正，初版输出另存 `independent-revalidation-attempt01.json`。

复核脚本为 [independent_revalidation.py](independent_revalidation.py)，仅读取已有文件。该批验证的是脚本化 SDK + 本地 transport 的运行、准入与隔离边界；不代表自然 LLM 选技能的成绩。1 秒任务截止为显式测试窗口；正常对照仍使用 500 秒预算。同步非流式范围、ASGI 请求锁内的截止/取消语义，以及远端请求无法撤销的限制仍然保留。
