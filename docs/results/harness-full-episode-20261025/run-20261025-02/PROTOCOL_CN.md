# 完整回合本地验收协议

日期：2026-10-25。只验证进程隔离的回合链，不启动真实模型、旧360次队列、训练或新增搜索机制。

## 固定范围

沿用原 full100 中 b1eb0270 短链任务与原检查器：Drone 3 起飞14米、转向353度、悬停。原fixture、输入清单、状态与timeline只读且加载前校验hash。全部回合使用同一初态；派生状态只在隐藏检查树根节点加入不参与评分的canary元数据，去掉该键后检查树须与原件完全相同，公开初态hash不变。原件与派生状态hash分别保存。

每trial新解释器进程、新FullWorld、新Agent，固定显式evidence_harness/observed_target_v7/applicability。沿用500秒、每计划2000 API、16 repair、16 search rounds，600工具步/1800递归与SDK零重试，不提高任何限额。模型是实际ChatOpenAI/SDK经MockTransport接本地脚本，公开UAV API经ASGI TestClient执行真实模拟器；拒绝socket网络连接，使用合成key，不读provider配置。

固定9回合：独立成功基准reference-B；随后success-A→success-B、failure-A→success-B、timeout-A→success-B、cancel-A→success-B。这是故障边界测试，不是9道独立Benchmark或自然模型能力样本。父进程等待每个worker真实退出并核验其封存，之后另启动scorer进程读取只读评分快照，再进入下一个回合。不因某个checker分数而挑选任务、重试或增加预算；基础设施失败则停批、保留失败，后续未执行明确列出。

## 四种终态

- success：公开list_drones后由脚本模型调用simple_chain，再返回完成标志，实际三动作与原checker均独立核对。
- failure：公开list_drones后执行一次真实take_off，再由本地模型端点返回完整HTTP500，SDK不重试。明确为注入的provider failure，不能称未知环境中的自发失败或技能修复效果。
- timeout：真实take_off已提交之后，将验证时钟推进至原500秒截止，后续动作必须被拒绝；不实际等待500秒，也不把deadline调小。固定正常虚拟时间以便比较B，真实墙钟另记，不据此估计吞吐。
- cancel：真实take_off已提交之后设置取消信号，保留第一动作并阻止后续动作；必须等待execute收尾及worker退出，取消flag不代表排空。

脚本模型不访问完整fixture/checker，仅消费真实模型请求与公开工具回执；模型选择是预写脚本，不是Teacher智能决策。

## 核验项目

1. 每个初态恢复的原结构hash、公开startup hash、派生hash、空黑板与命令账本、无旧runtime/score/timer/日志；相同session与task ID不构成复用理由。
2. 原始模型HTTP正文、结构化调用/回执、UAV公开API、物理事件及技能动作归属可连接。模型交互归档按本轮显式启用，项目默认仍关闭。
3. execution终态、Agent自报success、原checker.passed分别保存。500秒或取消后不再派发新动作，评分快照不可变，晚到响应不改闭合归档。
4. 私有评分在worker退出后另进程运行，评分API日志放evaluation目录；Agent轨迹没有admin/scoring请求。检查树canary须在scorer回执中出现作阳性对照，但不得出现在模型输入/输出、公开工具回执及policy日志中；Base64正文解码后检查，且禁止非空execution_check_apis。公开聚合进度仍合法。
5. 四个B分别与独立B比较完整初态、公开首观测、脚本/配置、模型输入、动作及checker；只对明确的每回合随机身份做限定映射，不宽泛删除字段掩盖污染。A闭合档案在scorer与B结束后hash不变。
6. 准入/哈希/评分异常不得被记作任务成绩；日志缺失或不完整明确失败，不自动补跑。单独保存代码测试与本地回合执行分母。

## 不外推的范围

不验证进程池复用、同进程多World并行、任意恶意Python代码的操作系统沙箱或真实远端动作撤销。本地ASGI模拟器的事务边界、脚本模型和虚拟时钟不能证明真实provider/实体无人机时序、泛化、奖励可靠性或训练环境吞吐。此次只在当前支持路径验收完整回合链；不自动授权Teacher生产或SFT/RL。
