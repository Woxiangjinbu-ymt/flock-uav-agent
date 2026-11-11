# 同技能在线对照启动记录

启动时间：2026-10-15 15:40:32（Asia/Shanghai）。用户已在当前线程明确授权按冻结方案启动并比较完整 100 题的成功率、模型调用与耗时。

## 冻结条件

使用 `frozen/study.json` 和同一 `shared/source.zip`，研究清单 SHA-256 为 `9d3f025faff6d28f64a937be57c95f9a776d44724503ca4f06d4450a1d21a2cc`。不改模型、提示、技能、门控、场景、任务或 Checker。当前官方 DeepSeek `deepseek-flash` 配置通过与冻结配置比较后启用，密钥只在运行时传入，不写入启动参数和冻结配置。

顺序固定：

1. `same_skill_fixed4`
2. `r2_reference`
3. `same_skill_applicability`
4. `same_skill_every_step`
5. `same_skill_failure`

每臂 100 题、605 检查点、5 场景，2 个场景 worker；任务预算 500 秒，保留场景内原题序和状态继承。第一轮总计 500 次任务执行。每臂完成、封存并核验后才启动下一臂；终止性 provider 错误或其他臂失败会停止队列，不自动重试、跳过失败臂或挑选最好重复。

## 实际启动

- [campaign.json](campaign.json)：队列状态、当前臂、当前 attempt 和进程信息。
- [runs.json](runs.json)：已登记的实际运行；尚未轮到的臂不提前登记 attempt。
- [launch.json](launch.json)：启动身份；后台 owner 初始 PID 为 `12236`。
- [第一臂日志](logs/same_skill_fixed4.log)：已有真实任务提交、模型调用与评分结果。
- 第一臂目录：`experiments/runs/20261015-154032-harness-same_skill_fixed4-f078526b`。
- [结果报告](../../docs/results/minimal-harness-online-20261015/REPORT_CN.md)及 [comparison.json](../../docs/results/minimal-harness-online-20261015/comparison.json)：每臂结束后自动更新；可以只读重算。

后台 owner 已与本次终端调用分离，并使用 `caffeinate -i` 避免空闲睡眠；关闭程序、机器休眠／断电等外部事件仍可能中断运行。中断后按已冻结的无自动重跑规则保留证据。

## 启动编排器验收

新增的编排和统计代码单独保存在 `execution/`，未写入或改变已冻结的技能源码包。启动适配器清单 SHA-256：`638d3e1a8cc29e9200cc6309d7a414493bfc23547ca89b80b77fc7975c87dcfb`。

- 编排及统计语义测试：11 项通过，见 [launch-tests.log](launch-tests.log)。
- 五个实际运行目录的离线 provenance 验证通过，见 [execution/preflight.json](execution/preflight.json)。
- 既有冻结源码预检仍为 69 项通过。
- 冻结源码清单额外包含一个 `experiments/analysis/validate_minimal_harness.py`。既有 runner 只扫描四个源码根目录；编排器在校验时显式补入该文件并验证其哈希，保留其余完整源码比较，没有删减清单或跳过 provenance 检查。

每次 attempt 单独保存源码、配置、计划、执行环境、场景封存及总封存。运行日志放在 attempt 外，避免继续写日志破坏封存哈希。

## 判读约定

主比较使用完整且封存验证通过的 100 题结果，报告任务成功率、605 检查点、配对 W/L 和场景层结果。模型调用分为窗口内逻辑调用、完整轨迹逻辑调用和 HTTP 尝试；模型 handler 墙钟不能解释成内部思考时长。评分就绪时间包括轮询及世界冻结／排空。

按预先冻结的 13 个适用任务追踪选择、计划提交、暂停后恢复和退出后的原始动作。日志中的匹配语法、重新进入计划、有后续动作都是观察指标，必须结合原任务结果和具体轨迹判断正确性。`harness.events` 中的 `control_rejected` 计数只覆盖已记录为该事件的拒绝；工具 JSON 参数校验拒绝还需读取 `tool_chain` 的实际回执，不能把该事件计数 0 解释为完全没有接口错误。

早期校验样本：`b1eb0270` 和 `e59d948d` 均由模型选择简单链并完成。后者第一次 `start` 带入了不允许的 altitude / drone_id 等字段，工具返回 `Unexpected control fields`；模型下一次改用合法参数后成功。其两次 start 是一个参数纠错过程，只有一次计划提交和实际动作链，不能记成成功完成后重复执行。具体案例见 [EARLY_CASES_CN.md](../../docs/results/minimal-harness-online-20261015/EARLY_CASES_CN.md)。目前没有完整臂，不能作性能结论。

已在当前线程设置每 30 分钟跟进一次的验收任务（`automation-2`）：正常运行且无重要变化时保持安静；新臂完成或异常时核查；五臂结束后读真实轨迹并完成中文研究结论，随后停用跟进。跟进不能触发新的付费重复实验。

只读更新汇总：

```bash
controller/.venv/bin/python -B experiments/minimal-harness-v1/execution/summarize_minimal_harness_online.py --workspace .
```

不要再次执行 `start`；启动器也会拒绝已有 campaign 的重复启动。
