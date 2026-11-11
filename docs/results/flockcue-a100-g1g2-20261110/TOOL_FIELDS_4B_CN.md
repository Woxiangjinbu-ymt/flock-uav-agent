# Qwen3-4B 的 58 个正式回合为何停止

2026 年 11 月 11 日在 Mac 上复核了 11 月 10 日同一批 29 题的 Qwen3-4B 开关配对。开思考 29 回合、69 次请求，关思考 29 回合、78 次请求。147 次请求均从实际 SDK 请求体重建，冻结分词器重新生成的输入 ID 与服务器记录逐个相同。使用冻结解析器重新解码全部生成 ID，147 次回复的解析消息、异常和 EOS 状态均与原结果一致。[逐槽证据索引](tool-fields-4b.jsonl) · [正式评分](REPORT_CN.md)

| 最终停止原因 | 开思考 / 29 | 关思考 / 29 | 最后一轮的原始证据 |
| --- | ---: | ---: | --- |
| 工具字段格式错误 `generated_tool_fields` | 27 | 23 | 共 73 个完整工具调用，每个都有 `name`、对象类型的 `arguments`，以及模型自行添加的顶层 `id`。50 个回合均正常输出 EOS。 |
| 工具 JSON 不完整 `generated_json` | 0 | 2 | 两次均调用 `sense_nearby_entities`，正常输出 EOS，外层 JSON 对象缺少最后一个右花括号。 |
| 达到单次生成上限 `generation_truncated` | 2 | 0 | 两次均在首轮恰好生成 2,048 个 ID，思考段未闭合，没有 EOS。 |
| 正常返回文本 | 0 | 4 | 最后一条回复均通过解析，原 checker 对这四题分别判定 0/4 项检查通过。 |
| **获得独立评分** | **29** | **29** | **两臂均成功 0/29。** |

50 个字段错误都发生在带有先前工具调用与回执的轮次。模板允许历史消息携带运行时分配的 ID，并要求新调用只生成 `name` 与 `arguments`。模型给新调用写入了 `call_2` 等 ID。43 个回合各提交一个调用，另 7 个回合各提交 3、6 或 9 个调用；这 7 组内的每个调用均自行写了 ID。题 `4d8bf293b0eb6bb9` 开思考时生成了带 `call_5`、`call_6`、`call_7` 的起飞、旋转、悬停调用，整组在执行前被拒绝。[冻结模板](../../../outputs/flockcue-stage0-20261110/server/run/source/training/templates/qwen3-tool-ids-v2.jinja#L11) · [原始生成 ID](../../../outputs/flockcue-stage0-20261110/server/run/trials/qwen3-4b-on-4d8bf293b0eb6bb9/private/student-requests.jsonl) · [ID 解析规则](../../../outputs/flockcue-stage0-20261110/server/run/source/scripts/student_input_v2.py#L334)

在内存中仅移除 73 个顶层 `id`，把同一批回复交还原解析器，50/50 组均通过格式检查，所有调用名也都在当轮公开工具清单中。这项离线检查定位了原解析器拒绝这些调用的直接原因。执行效果、机体状态变化和整题成功率需要另做在线对照。对原评分的核查覆盖 58 份独立评分、对应的原检查树、worker 与 scorer 进程 ID，以及两个进程退出时封存的全部文件摘要。58 题次共通过 5/196 个叶检查，全部整题评分为失败。[原始评分及运行轨迹](../../../outputs/flockcue-stage0-20261110/server/run/trials/) · [worker 文件摘要](../../../outputs/flockcue-stage0-20261110/server/run/worker-exit-seals/) · [scorer 文件摘要](../../../outputs/flockcue-stage0-20261110/server/run/scorer-exit-seals/)

逐槽 JSONL 按原计划顺序列出每个回合及其最终停止原因、生成字段、EOS、原检查计数、请求体和两个退出封存的 SHA-256。相对路径以归档的 `server/run/` 为根。该索引的 SHA-256 为 `2f6b00f3df20a9725f07408be85ed599380d67cfd3855567dde291bcaf7353ee`。[逐槽 JSONL](tool-fields-4b.jsonl)
