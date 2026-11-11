# 自然 Agent 验证协议与本地准备记录

日期：2026-10-25。接续完整回合验收，本轮完成复用接口和下一批设计的本地准备，**没有新增模型、模拟器或任务执行，没有增加在线预算，也没有启动训练**。

## 实际交付

1. [复用接口与验证协议](../../HARNESS_NATURAL_AGENT_VALIDATION_CN.md)：明确新进程、初态、模型输入/行动、终止、退出后独立评分和封存；区分本地脚本验收与真实provider运行器。
2. [准备器](../../../scripts/prepare_harness_natural_agent.py)：只有 `prepare` / `verify`，无run/launch入口；纯标准库，不读provider配置、不导入Agent/模拟器、不连接网络、不反序列化pickle。
3. [独立计划目录](../../../experiments/harness-natural-agent-plan-v1/manifest.json)：8份准备文件，已在真实仓库执行prepare并只读verify通过；
4. [准备器专项测试](../../../scripts/tests/test_harness_natural_agent_prepare.py)：首轮 **67项全部通过**。测试中的410/302/80文件及96检查摘要均为临时合成夹具，不是再次跑原实验。见[日志](tests-attempt01.log)、[源码及执行记录](tests-attempt01.json)。
5. [既有检查点审计](CHECKPOINT_EVIDENCE_CN.md)：从封存公开轨迹找到可用于设计自然恢复验证的候选分支，保留逐事件回执、状态、游标和hash关联。

研究入口已同步[研究状态](../../RESEARCH_STATUS_CN.md)与[Roadmap](../../CSCWD2027_INCREMENTAL_RESEARCH_ROADMAP_CN.md)。AGENTS的稳定主线和生产代码没有变化；短期证据留在本目录，不追加到Agent指令。

## 设计依据与关键发现

旧40次自然入口校准的真实paused机会为0；终止态上的resume不能算恢复。旧v2有恢复证据，但初始skill是夹具预提交，且其中block_move/takeoff故障通过临时增删障碍实现，不能直接混入当前v7静态障碍契约。

原v7的静态开发任务本身已有公开失败依据。本次只读核对三题 **2 / 16 / 9，共27个** 移动受阻后仍在修复预算内的检查点，逐项关联真实错误回执和前后公开位置；对应原轨迹共28次错误。24ac的第17次错误在yield前耗尽repair，所以不是可暂停点。

这些旧轨迹由脚本选择skill、调度器为applicability，实际没有自然模型paused。源码表明failure到达同一检查点时会暂停；一旦模型介入，后续轨迹不能从旧A轨迹推算。因此27是**候选事件数**，不是新自然机会、恢复成功数或独立样本。无须用动态障碍注入制造机会；未来机会为零时如实报告，不追加预算补样本。

## 拟议对照与统计边界

沿用7个开发任务、7个问题组、4个场景：3道搜索、2道短链、2道完整语法不支持的对照。两臂同为 `evidence_harness/observed_target_v7`，静态障碍写入隔离；只改变applicability/failure调度。单次每题每臂一槽，共14个拟议slot，固定A先4对/F先3对，不宣称完全平衡。详见[拟议顺序](../../../experiments/harness-natural-agent-plan-v1/schedule.proposed.json)。

模型必须从原任务自行观察、选技能；不预提交计划、不把候选点或旧脚本skill标签放入输入。[公开任务目录](../../../experiments/harness-natural-agent-plan-v1/tasks.public.json)仅含task_id、scene_id、command及hash；问题组、开发标签、适用性和旧脚本选型留在[evaluation-annotations](../../../experiments/harness-natural-agent-plan-v1/evaluation-annotations.json)。这些文件尚未被任何模型消费。

按实际证据分别统计：自主选择、真正ready/paused机会、同计划resume、终止或主动放弃后raw接手、已生效动作重复以及整题checker。未选skill但raw工具完成任务仍算任务成功；技能completed或部分叶子通过不是整题成功。条件恢复率与全任务结果分开，不能排除未选技能的回合制造收益。

这两臂只能回答调度差异，不能宣称Harness总体优于raw-only，也不能证明v7相对legacy的全部改进收益。当前观察粒度“选择/恢复/接手”只是潜在学生职责，约2B模型最终职责及论文核心问题仍未冻结。

## 本地验证与仍需完成的实现

实际准备时核对原410个输入、7题JSON/快照/timeline/源文件引用，及上一批302文件封存、80份冻结源码与当前源码一致。仅hash读取二进制，未复制或加载世界。准备器通过确定性重建校验整个8文件包；67项测试覆盖零授权、AF4/FA3、四字段目录、私有字段隔离、输入篡改、重新签hash后的语义修改、路径越界、符号链接和误用执行入口。实际命令及产物hash见[prepare/verify记录](prepare-verify.json)。

另由独立审计核对计划内容与原证据，**61项只读检查通过**，没有再运行准备器、测试或任何任务。见[独立审计说明](PREPARATION_AUDIT_CN.md)与[机器记录](preparation-audit.json)。这61项是产物复核，不增加样本或任务成绩。

上一批受控回合使用虚拟时钟、MockTransport固定应答、60秒父watchdog及固定动作/调用数量，不能直接替换模型名字后上线。旧full100 `run_trial`又在执行进程中评分，不能原样满足新隔离约定。下一项应实现**真实墙钟的自然入口worker和独立scorer适配，先用本地MockTransport验证**，不改变生产默认或启动模型。

原500秒任务窗、每计划2000API、16次repair/16轮、600步/1800递归作为拟议执行约束保留；

旧自然校准中所选7题已有数据的5对消耗 **18,011,979 token**；另外2题没有该批自然成本。[精确分项及来源hash](checkpoint-evidence.json)已保留。

本轮没有新增Benchmark分母、Teacher样本或训练成果。准备器测试、27个旧事件审计及7题/14槽拟议设计分别计数，均不与上一批9回合或既有线上成绩合并。
