# 自然 Agent 本地准备包独立审计

2026-10-25。**61 / 61 条只读产物检查通过，无阻断；该结论只表示准备包一致，不表示可以在线启动。** 详见含逐项结果及全部八文件 SHA-256 的 [preparation-audit.json](preparation-audit.json)。本次没有执行 prepare、verify、pytest、模拟器、Agent、provider 或 checker，没有改动准备包及生产代码。

核对对象为 [harness-natural-agent-plan-v1](../../../experiments/harness-natural-agent-plan-v1/)，准备器 SHA-256 为 `179834ec820b4cb7efdfc53cae127f07098b32fad0eb3a66cac29af235a2485e`。

- 八文件集合及 manifest 的七个文件摘要一致；协议是当前版本的逐字节副本。准备器只导入标准库，CLI 仅 prepare / verify，没有任务启动或网络调用路径；verify 按受核输入和固定语义重建内容，不能仅改授权字段再重算 hash 蒙混通过。
- 固定 7 题对应 **7 个 problem_cluster、4 个 scene**，不是 7 个独立场景；18 个选中 fixture/timeline 引用与原 manifest 一致。原 **410** 个文件、已验收完整回合的 **302** 个封存文件及 **80** 个冻结/当前源码摘要全部通过。旧完整回合及 observed-target seal 与此前独立审计所记录摘要一致。
- 公开任务目录仅有 `task_id`、`scene_id`、`command`、`command_hash` 四字段，逐题等同原文；开发集标签、problem_cluster、适用性和旧脚本请求的 skill 均在评估注释中。没有把私有 checker、初态正文或本次检查点诊断放进公开目录。
- 拟议 14 个 slot 是原 7 题各 A/F 一次；除 slot/标识元数据外，成对方案只改变 scheduler。顺序 **AF 4 对 / FA 3 对**，明确 `fully_balanced=false`。这是协议检查，尚没有自然 worker 可用于证明运行时控制变量完全一致。
7 个未完成在线条件明确列出。旧预算只作参考，不自动转为新额度。

此前两项建议已落实：公开目录去掉评估元数据；有效 paused 被模型主动放弃/取消后转 raw 工具，按接手原因单列，不冒充同 plan 恢复。旧 5 对自然轨迹的 **18,011,979 token** 来源与精确求和也已复核，详见 [检查点证据](CHECKPOINT_EVIDENCE_CN.md)及其 JSON。

本审计的 61 条文件/语义检查不是代码测试、在线 trial 或自然恢复次数；不与其他分母相加。后续仍须实现并本地验收自然 worker、独立 scorer、真实时钟及硬准入，并另行确定资源和在线授权。
