# DeepSeek 悬停轨迹的 16 条 SFT 开发样例

2026-11-09 在 Mac 上离线复核了 **4 个回合、26 个教师决策轮，保留 16 轮，剔除 10 轮**。每条样例练习一次决策：给定教师当时已看到的任务、对话和工具回执，学生要生成教师这一轮的公开答复或工具调用，以及教师实际写下的思考。评分、筛选理由和本轮动作执行后的结果另存。此次新增教师调用、GPU 卡时和训练步数均为 0。[16 条样例](samples.jsonl) · [全部 26 条审阅记录](decisions.jsonl) · [批次清单](batch.json)

选取 11 月 3 日悬停开发批次中工具参数契约相同的整个 `candidate` 组，包含原题 `c74d7020` 的四种要求或故障条件。另一组使用旧参数契约。四个入选回合的原检查都是 2/2，因而还需分别问三个问题：整项任务能否独立证实，本轮动作收到什么回执，教师最后的说法有没有公开依据。这里采用保守的开发样例筛法，时长无法核验的回合整体留作诊断。这 16 条都是已见开发轮，正式 600 题训练集和评测成绩单独核算。[原批次协议](../deepseek-hover-pilot-20261103/run-01/PROTOCOL_CN.md) · [来源封存清单](../deepseek-hover-pilot-20261103/run-01/campaign-seal.json)

## 每一轮为什么保留或剔除

表中“请求 → 回复”是原始 JSONL 的序号。样例与审阅记录用同一个 `sample_id` 对应，例如 `unknown_takeoff-candidate/turn-05`。点击每组的原始记录即可定位，文末命令可直接解码完整内容。工具调用之后取得的回执用于本轮筛选，从下一轮起才可能出现在学生输入中。

### 明确要求先起飞再执行悬停 · 保留 7/7

目标是让 Drone 4 起飞到 12 米，单独执行悬停，再确认结果。原始机号为 `3d229034`。[原始记录](../deepseek-hover-pilot-20261103/run-01/trials/explicit_hover-candidate/policy/modelio/model-io-26aec846c00949b3b3046547f1292e96.jsonl) · [独立评分](../deepseek-hover-pilot-20261103/run-01/trials/explicit_hover-candidate/evaluation/result.json)

| 轮 | 请求 → 回复 | 取舍 | 本轮依据 |
| --- | --- | --- | --- |
| 1 | 3 → 5 | 保留 | 查机队，用返回的名称和 ID 确认哪架是 Drone 4。 |
| 2 | 11 → 13 | 保留 | 确认机号后，并行查天气和目标机周边，两份回执均可归属到原调用。 |
| 3 | 21 → 23 | 保留 | 发出飞行指令前单独查询状态，记下起点 (660, 734, 0)，可与终态核对。 |
| 4 | 29 → 31 | 保留 | 向已确认的目标机发出 12 米起飞指令，收到成功回执。 |
| 5 | 37 → 39 | 保留 | 起飞后单独调用 `hover`，参数为 `{"input_json":{"drone_id":"3d229034"}}`，序号 43 有该指令的成功回执。 |
| 6 | 45 → 47 | 保留 | 在悬停动作之后再次查询同一架飞机，得到位置和飞行状态。 |
| 7 | 53 → 55 | 保留 | 起点、依次发出的两次动作、终点均可由之前的公开记录举证，完成报告也通过独立评分。 |

### 要求达到原地悬停状态 · 保留 5/6

独立评分认定实际完成。在线验收要求能明确定位起飞前的水平位置，当轮并行查询没有让验收程序确认这一点。[原始记录](../deepseek-hover-pilot-20261103/run-01/trials/state_hover-candidate/policy/modelio/model-io-78b8997baf094b4c8ba4b10e67ae336a.jsonl) · [公开验收结果](../deepseek-hover-pilot-20261103/run-01/trials/state_hover-candidate/policy/agent-result.json) · [独立评分](../deepseek-hover-pilot-20261103/run-01/trials/state_hover-candidate/evaluation/result.json)

| 轮 | 请求 → 回复 | 取舍 | 本轮依据 |
| --- | --- | --- | --- |
| 1 | 3 → 5 | 保留 | 并行读取机队和天气，两项只读动作都有回执，可用于选择飞机和了解环境。 |
| 2 | 13 → 15 | 保留 | 用机队中查到的目标 ID 探测周边，取得起飞前的局部观测。 |
| 3 | 21 → 23 | 保留 | 向该机发出 12 米起飞指令，指令参数与回执一致。 |
| 4 | 29 → 31 | 保留 | 起飞后单独发出悬停指令，有同一架飞机的成功回执。此题只要求达到悬停状态，该额外指令可执行，未评估它能否省略。 |
| 5 | 37 → 39 | 保留 | 最后一个飞行动作之后查状态，取得 12 米处正在悬停的观测。 |
| 6 | 45 → 47 | 剔除 | 教师宣布整题完成。公开验收仍要求补充起飞前的位置依据，因此保留前五个有证据的动作，剔除这条完成声明。 |

### 起飞回执超时 · 保留 4/6

这里能看到“指令有没有执行”和“模型当时知不知道结果”的区别。独立记录确认飞机已起飞，但教师在第 4 轮收到的只有超时消息。[原始记录](../deepseek-hover-pilot-20261103/run-01/trials/unknown_takeoff-candidate/policy/modelio/model-io-84a81a9f166b4eefb6b893c886570a8b.jsonl) · [独立评分](../deepseek-hover-pilot-20261103/run-01/trials/unknown_takeoff-candidate/evaluation/result.json)

| 轮 | 请求 → 回复 | 取舍 | 本轮依据 |
| --- | --- | --- | --- |
| 1 | 3 → 5 | 保留 | 查机队，确认 Drone 4 的编号、初始位置和状态。 |
| 2 | 11 → 13 | 保留 | 用目标机号探测周边，在控制前取得可观察到的障碍信息。 |
| 3 | 19 → 21 | 保留 | 起飞前查询天气，收到当时环境的回执。 |
| 4 | 27 → 29 | 剔除 | 起飞参数正确，但回执正文是超时，不能将这一轮作为收到成功确认的动作示范。原指令与超时消息完整保留在第 5 轮历史里。 |
| 5 | 35 → 37 | 保留 | 知道起飞效果未知，教师选择查同一架飞机的状态；没有重发起飞。第 41 条记录显示目标机在 12 米悬停。 |
| 6 | 43 → 45 | 剔除 | 终态有观测支持，但教师进一步断言超时由响应延迟造成。公开记录不足以确定原因，因此整条原文剔除。 |

### 要求连续悬停 5 秒 · 保留 0/7

该回合只记录了命令参数、回执和瞬时位置。独立评分把“实际连续悬停 5 秒”记为无法判定。本批按整题可核验性剔除七轮，以下仍逐条保留取舍依据。[原始记录](../deepseek-hover-pilot-20261103/run-01/trials/duration_hover-candidate/policy/modelio/model-io-6700089b83244bcc8c575d6fd51fb183.jsonl) · [独立评分](../deepseek-hover-pilot-20261103/run-01/trials/duration_hover-candidate/evaluation/result.json)

| 轮 | 请求 → 回复 | 取舍 | 本轮依据 |
| --- | --- | --- | --- |
| 1 | 3 → 5 | 剔除 | 机队和天气查询成功，属于时长无法验收回合的准备动作。 |
| 2 | 13 → 15 | 剔除 | 周边探测有回执，只反映飞行前的环境。 |
| 3 | 21 → 23 | 剔除 | 12 米起飞有成功回执，只证明执行了起飞。 |
| 4 | 29 → 31 | 剔除 | 查到起飞后的瞬时位置，此时尚未发出带时长的指令。 |
| 5 | 37 → 39 | 剔除 | 悬停指令带有 5 秒参数并被接收，但没有连续计时证据。 |
| 6 | 45 → 47 | 剔除 | 再次查到飞机在 12 米悬停，单个状态点无法补出连续 5 秒。 |
| 7 | 53 → 55 | 剔除 | 教师如实写明时长未经验证；保留这段原文供审阅，整题仍无法作为已完成示范。 |

## 学生会学到什么

16 条中，7 条有教师原始思考，9 条的思考为空。全部使用同一个允许思考的 Qwen3.5-2B 提示前缀，空思考的样例直接结束思考并输出原动作或答复。学生的损失只计算当前回复，历史消息、历史思考、既往回执和工具定义全部遮罩。原始 HTTP 消息和工具定义保存在样例中，公开调用与回执的配对在分词时按既有适配器重建。筛选使用的独立评分、后续回执和剔除理由只在审阅侧读取。[样例](samples.jsonl) · [监督转换与回放](../../../scripts/flockcue_qwen35/dev_sample_tokens.py)

16 条共有 200,948 个提示 token 和 1,501 个受监督 token，后者含 472 个思考 token。单条提示为 11,342–13,957 个 token，单条监督目标为 31–252 个 token。验收逐条从封存 HTTP 正文还原输入和回复，并以实际分词器复算损失遮罩、结束标记、工具名和有类型的参数。不同轮会重复读取同一段历史，因此 16 条样例仍只覆盖一道已知原题的四个变体。这些数字衡量整理质量，学生任务成功率须另行实测。[独立验收](verification.json) · [原有单条样例](../../audit/qwen35-2b-input-20261108/REPORT_CN.md)

## 在 Mac 上回查与复算

从项目根目录运行。以下三条查询分别显示第 4 轮的原始超时回执、第 5 轮教师看到的完整请求，以及教师给出的下一步。将回合名和轮号替换为上表的其他条目，即可查任意保留或剔除轮。省略 `--part` 会显示处理决定、原因代码和原始记录序号。

```sh
export PYTHONPATH=scripts:.
py=training/.venv-input/bin/python
"$py" -m flockcue_qwen35.inspect_dev_sample unknown_takeoff-candidate 4 --part receipts
"$py" -m flockcue_qwen35.inspect_dev_sample unknown_takeoff-candidate 5 --part request
"$py" -m flockcue_qwen35.inspect_dev_sample unknown_takeoff-candidate 5 --part response
```

下列命令使用本机保存的 11 月 2–3 日四份封存归档、冻结 W3 交付和 Qwen3.5-2B 分词器（修订 `15852e8c16360a2fea060d615a32b45270f8a8fc`）。迁移到其他机器复算时也须带上这些原件。单独阅读样例只需要本目录中的 JSONL。[分词器及模板校验](../../../scripts/flockcue_qwen35/adapter.py)

```sh
export PYTHONPATH=scripts:.
export HF_HUB_OFFLINE=1
py=training/.venv-input/bin/python
tmpdir=$(mktemp -d)
"$py" -m flockcue_qwen35.dev_samples --output "$tmpdir/rebuilt"
"$py" -m flockcue_qwen35.verify_dev_samples "$tmpdir/rebuilt"
for file in samples.jsonl decisions.jsonl batch.json; do
  cmp "$tmpdir/rebuilt/$file" "docs/results/deepseek-sft-dev-examples-20261109/$file" || exit 1
done
```
