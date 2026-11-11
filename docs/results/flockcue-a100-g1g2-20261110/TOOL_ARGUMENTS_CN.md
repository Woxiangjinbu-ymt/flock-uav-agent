# Qwen3.5-2B 工具参数错误核查

## 首批三个回合

抽查 29 题正式开发筛查中最先完成的三个 2B 回合。逐回合校验了归档 SDK 请求体的 SHA-256，用固定修订的分词器和模板复算输入 ID，再解码实际生成 ID。三次出错的调用均为 `sense_nearby_entities`，均以 EOS 248046 正常结束。下表的“原检查”指通过项数 / 该题原 checker 的总项数。

| 冻结运行槽 | 真实生成的调用 | 独立评分与证据 |
| --- | --- | --- |
| `qwen3p5-2b-on-07b3442204748182` | 开思考，第 2 轮，函数块中没有参数。 | 原检查 0/2，目标抵达和拍照均失败。[生成 ID](../../../outputs/flockcue-stage0-20261110/server/run/trials/qwen3p5-2b-on-07b3442204748182/private/student-requests.jsonl#L2) · [原评分](../../../outputs/flockcue-stage0-20261110/server/run/trials/qwen3p5-2b-on-07b3442204748182/evaluation/result.json) |
| `qwen3p5-2b-off-07b3442204748182` | 关思考，第 2 轮，同样生成无参数的函数块。 | 原检查 0/2，目标抵达和拍照均失败。[生成 ID](../../../outputs/flockcue-stage0-20261110/server/run/trials/qwen3p5-2b-off-07b3442204748182/private/student-requests.jsonl#L2) · [原评分](../../../outputs/flockcue-stage0-20261110/server/run/trials/qwen3p5-2b-off-07b3442204748182/evaluation/result.json) |
| `qwen3p5-2b-off-0fe7f9e9a60a943c` | 关思考，第 3 轮，直接生成顶层 `<parameter=drone_ids>`，值为单机 ID 列表。 | 原检查 0/1，目标搜索覆盖率为 0，要求为 0.95。[生成 ID](../../../outputs/flockcue-stage0-20261110/server/run/trials/qwen3p5-2b-off-0fe7f9e9a60a943c/private/student-requests.jsonl#L3) · [原评分](../../../outputs/flockcue-stage0-20261110/server/run/trials/qwen3p5-2b-off-0fe7f9e9a60a943c/evaluation/result.json) |

实际发给模型的工具定义只声明一个顶层参数 `input_json`，并将它列为必填。按冻结协议，三次调用均应报 `tool_arguments`。同一工具的自然语言说明将内部字段写为可选，示例只展示含 `drone_ids` 和 `detail` 的内层对象。该说明与外层调用格式之间的歧义可能影响模型行为；这次抽查无法量化其影响。[发给模型的原始请求](../../../outputs/flockcue-stage0-20261110/server/run/trials/qwen3p5-2b-off-0fe7f9e9a60a943c/policy/modelio/model-io-6d04829f6ff2413a9f3917d255cf13b8.jsonl) · [冻结解析器](../../../outputs/flockcue-stage0-20261110/server/run/source/scripts/flockcue_qwen35/adapter.py)

三个回合的 worker、scorer 退出封存及其逐文件哈希均吻合。各回合的评分进程与运行进程 PID 不同，原检查树匹配，评分器记录的错误数均为零。上述核查只覆盖表中三个回合。[worker 封存](../../../outputs/flockcue-stage0-20261110/server/run/worker-exit-seals/) · [scorer 封存](../../../outputs/flockcue-stage0-20261110/server/run/scorer-exit-seals/)

## 2B 全部 58 个回合

开、关思考各有 28/29 个回合以 `tool_arguments` 停止，这 56 个回合均未通过整题原检查。逐一校验最后一轮 SDK 请求体的哈希，用冻结模板从公开消息重建输入 ID，并用实际生成 ID 重放解析。56/56 个输入序列与归档逐项相同，输出均有正常 EOS，重放均在同一工具组复现参数错误。表中每个回合只计一次。[58 槽逐项核查索引](tool-arguments-2b.jsonl)

| 触发终止的生成内容 | 思考开 | 思考关 | 两臂合计 |
| --- | ---: | ---: | ---: |
| `sense_nearby_entities` 无任何参数，缺必填的 `input_json` | 21 | 21 | 42 |
| `sense_nearby_entities` 将 `drone_ids` 或 `detail` 写成顶层参数 | 2 | 2 | 4 |
| `rotate` 将 `drone_id`、`heading` 写成顶层参数 | 2 | 5 | 7 |
| `change_altitude`、`return_home`、`navigate_to` 分别直接写入内部字段 | 3（各 1） | 0 | 3 |
| 合计 | 28 | 28 | 56 |

这五个工具在相应请求中都只声明一个顶层必填参数 `input_json`，描述中的示例都直接展示内层字段。14 个直接生成内层字段的回合，其顶层参数名均无重复；它们触发的是冻结解析器“未知或重复参数”分支中的未知参数。该批可以确认公开工具格式、模型生成和解析结果之间的对应关系，示例措辞造成多大影响仍需另做对照。

37 个回合在最后一轮只生成一次工具调用，19 个生成同轮多工具调用（开思考 15 个，关思考 4 个）。逐个拆解后，19 组均只有一个调用的参数出错，均至少含一个靠前且可单独解析的调用。冻结协议对整组原子验收，因此这些本可解析的调用在该轮也没有发出。错误数按 19 个回合计，不能把同组内每个工具都归为参数生成错误。[冻结的整组解析](../../../outputs/flockcue-stage0-20261110/server/run/source/scripts/flockcue_qwen35/adapter.py) · [逐组调用与首个错误位置](tool-arguments-2b.jsonl)

另两槽逐一核对原检查。开思考的 `d944f2e70749dec9` 在第 9 轮达到累计 token 上限；此前已经起飞到 20 米，终态高度 20 米、航向 98°，并成功悬停 5 秒。独立评分 4/4，整题成功。关思考的 `ad9ce155b30d7ab8` 在第 8 轮输出完成说明；起飞和三个访问点通过，最终位置和悬停未通过，独立评分 4/6，整题失败。两臂据此分别成功 1/29、0/29。58 个回合的原检查树、worker 与 scorer 的进程分离、两个退出封存及其逐文件摘要均已复核，评分器均无错误记录。[开思考的 4/4](../../../outputs/flockcue-stage0-20261110/server/run/trials/qwen3p5-2b-on-d944f2e70749dec9/evaluation/result.json) · [关思考的 4/6](../../../outputs/flockcue-stage0-20261110/server/run/trials/qwen3p5-2b-off-ad9ce155b30d7ab8/evaluation/result.json)
