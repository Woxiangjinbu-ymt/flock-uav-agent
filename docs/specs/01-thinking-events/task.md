# FlockCue 第 1 步 · 执行事件与思考标签任务清单

**状态：G3 已通过，原 1.7B/4B 学生接入与 Mac CPU 链路已验收。** 2026-11-09 用户确认用 Qwen3.5-2B 作 G1/G2 主模型，Qwen3-4B 作参照。此次在 Mac 修订槽表、运行模拟计分和报告；真实四臂共 116 个槽待启动。[历史无卡验收](../../results/thinking-student-cpu-20261108/REPORT_CN.md) · [G3 交付报告](../../results/thinking-events-g3-20261108/REPORT_CN.md)

第 1 步交付逐轮事件与思考标签、学生开发集配对结果，以及是否进入教师采数的门槛报告。CPU 开发可并行。T01–T14、T36 产出真实 G3 结论；T15–T31 负责学生接入与开发集。付费工作只在 G3 通过后启动，顺序为 T36a Linux 主机准备、T37 测速与后端选择、T38 正式配对。T32–T35 定义报告，T39 汇合实测证据。实验判据以[已批准规格](spec.md)为准。

## 文件与运行环境

表中列出第 1 步的实现位置。已接入 `replay`、`prepare-dev`、无卡 `run-dev` 与独立学生冒烟入口。11 月 10 日，T38/T39 的批次领取、服务调用、独立评分读取和报告生成已用四臂固定回复在 Mac 跑通全部 116 槽。Linux 实测与结算待 T36a/T37 后进行。[本地复跑与上机交接](../../results/qwen35-g1g2-execution-mac-20261110/README_CN.md)下表给出目录，后续任务使用文件名定位。每一行任务都有直接依赖、改动对象和可观察的验证结果；“无”表示可在实施开始时独立进行。

| 新建文件（相对仓库根目录） | 职责 | 对应的新测试（均在 `scripts/tests/`） |
| --- | --- | --- |
| `scripts/thinking_events/__init__.py`、`__main__.py` | 包入口，接入 `replay`、`prepare-dev`、`run-dev`、`report` | 在对应子命令的测试文件中验证 |
| `scripts/thinking_events/archive.py` | 封存认证、请求尝试、公开前缀和独立的回复元数据 | `test_thinking_archive.py` |
| `scripts/thinking_events/events.py` | E0–E6、证据缺口及义务覆盖 | `test_thinking_events.py` |
| `scripts/thinking_events/labels.py` | 原始标签、有效监督、固定间隔和逐轮导出 | `test_thinking_labels.py` |
| `scripts/thinking_events/student.py`、`student_process.py`、`student_service.py`、`student_timing.py` | 带历史推理的模板、生成与解码、模型进程和按请求计时 | `test_thinking_student.py` |
| `scripts/thinking_events/campaign.py`、`campaign_storage.py` | 开发初态、槽表、路径绑定、预算、恢复与封存 | `test_thinking_campaign.py` |
| `scripts/thinking_events/worker.py`、`worker_child.py`、`worker_smoke.py`、`worker_verify.py` | 冻结 worker、独立 scorer、跨进程夹具和原始请求复验 | `test_thinking_worker.py` |
| `scripts/thinking_events/report.py`、`paired_scores.py`、`paired_simulation.py` | G3 复算、G1/G2 配对计分、模拟输入与报告重放 | 按门槛边界和错配场景验收 |

原 1.7B/4B 的 Linux 推理锁和迁移包保留冻结。T36a 为 2B/4B 另存已验证的 Linux 后端清单与兼容性证据。

统一以 `PYTHONPATH="$PWD/scripts" python -m thinking_events` 作为入口。新包内部采用相对导入；旧脚本采用顶层名（如 `harness_request_limits`）。测试与子进程沿用该规则，冻结 worker/scorer/GPU 进程将搜索根指向该批冻结的 `source/scripts`，其余运行模块按组件从冻结源码加载。

CPU 归档、仿真和报告测试使用 Agent 环境。学生模板测试使用既有 tokenizer 环境，GPU 生成使用方案指定的独立推理环境。每组实现同时补齐上表中的对应测试。例如，归档组执行 `agent4drone/.venv/bin/python -m pytest -q scripts/tests/test_thinking_archive.py`，模板组执行 `training/.venv-input/bin/python -m pytest -q scripts/tests/test_thinking_student.py`。文末列出了交付时的入口和回归命令。

## 四批归档与 G3

| 任务 | 直接依赖 | 文件与操作 | 验证 |
| --- | --- | --- | --- |
| T01 核验归档身份 | 无 | `archive.py` 按方案顺序检查四份 campaign seal 的合并摘要，再用 campaign 和 worker 两级封存核对归档路径、哈希与所属回合。按计划表登记未执行槽。 | 四批分别定位 5、8、10、8 份归档。行为批的未执行槽可查。修改任一级封存或调换归档归属时，复算指出对应错误。 |
| T02 重组一次模型回复 | T01 | `archive.py` 关联逻辑调用、HTTP 尝试和顺序化的 chunk，校验字节长度及 SHA-256；分别返回尝试状态、回复用量、正文是否可用。 | 夹具中同一逻辑调用重试后成功只生成一条决策；无回复尝试仍可查。显式零、缺 usage、损坏响应三者可区分。 |
| T03 截取决策前缀 | T02 | `archive.py` 以本轮已发请求为截止，按上轮调用 ID 核对原回执与 wire 内的 tool 消息，保留可见的实际顺序和原任务，输出 `read_turns(source)` 的前缀部分。 | 交换两个同名工具的返回顺序仍能找到各自飞机；删除一个回执或制造重复 ID，出现对应缺口。上下文已裁掉的信息不会重新进入前缀。 |
| T04 分离附加说明和标签输入 | T03 | `archive.py` 逐字核对已知 M1-R 附加段，保留原回执、证据位置与未知改写原因。采用字段白名单，当前回复和教师用量仅交给标签侧。 | 用一条带附加段的真实请求，回查到唯一原回执；重复显示同一附加段时事实只出现一次。改写未知段、替换当轮推理或终局评分，前者留缺口，后两者不改变前缀。 |
| T05 判断受阻与未知 | T03 | `events.py` 复用现有命令分类和执行效果语义；拆开复合移动的动作分量，核对内外层主体与调用身份，再判 E1、E2。 | 几何拒绝得到 E1；可归属的动作超时、传输异常或错主体得到 E2。参数校验、仅缺失回执各有反例；晚到的成功查询保留此前的未知。 |
| T06 判断部分完成与明确失败 | T03 | `events.py` 识别 `partial`、子程序公开证实的剩余航点和参数拒绝，按具体证据标 E3、E4。 | `partial` 即使没有剩余数量也触发 E3。单独的 `needs_replan` 不触发；公开参数拒绝为 E4，带 error 字样的动作超时不重复计 E4。 |
| T07 判断新实体和进度 | T03 | `events.py` 用批前已知的类型、公开 ID 及同会话进度作对照，处理各机、组合工具、黑板和子程序公开返回的新目标；整批判断后才合并事实。 | 并行调用发现同一新目标时保留两条引用；之后回显无 E5。进度首次读取建立基线，数值改变触发 E5，并行矛盾读数留下缺口。 |
| T08 判断具名悬停义务 | T03 | `events.py` 复用具名悬停解析，把原文条款位置、唯一飞机、公开状态与显式动作回执连接起来，记录首次证实的改变。 | 在公开证据能确定主体、时序及所需的高度和位置锚点时，状态查询可证实状态目标；要求执行悬停指令的条款还需对应动作回执。重复确认无新 E6，错机、时序不明、时长不可证均可追溯。 |
| T09 判断具名航点义务 | T05 | `events.py` 为 checkpoint 子条款核对飞机、原文目的坐标与阻断的航段。输出条款支持范围和证据位置。 | 同一飞机飞往原文坐标遇几何拒绝，得到 E1 和该原路径的 E6。更换飞机或目的坐标仅保留可证实的动作事件；模糊条款进入覆盖统计。 |
| T10 汇总轮次判定 | T04、T05、T06、T07、T08、T09 | `events.py` 实现 `extract_events(prefix)`，保留多个事件与同轮缺口，生成 `start/event/E0/indeterminate`。 | 覆盖 E0–E6 各至少一个正例与反例。纯文字上一轮单独检查待回执及新信息。对前缀与事件组件监测打开私有状态、评分及终局 ledger 的行为，访问次数为零。替换当前回复、后续回执和隔离评分后，该轮事件逐字节一致。 |
| T11 生成原始标签与有效监督 | T02、T10 | `labels.py` 计算事件、教师自选与两个端点。保留缺正文的动作行，分开记录 `requested_think`、`effective_think`、冲突原因。LoGiC 登记固定版本 Qwen3-1.7B 和 ε=0.001 nat/token，两项标签留空并标 `deferred`。 | 在规格的两个真实例中，动作超时后 24 个推理 token 得到两种原始标签皆真；参数失败后零 token 得到事件真、教师假，事件有效监督为假。缺用量保留空值。 |
| T12 确定固定间隔 | T11 | `labels.py` 在四批合并的共同可判轮上按原轮号求 k，首轮纳入，每回合重置，并列取较小 k。用同一分母报告请求与有效保留率、保留 token 及偏差。 | 用含不可判中间轮的不同长度回合手算后对照；并列、残差正好 2 个百分点及超过 2 个百分点均按方案处理。全留与教师有效监督逐行相同时报告重合。 |
| T13 导出复算产物 | T01、T11、T12 | `labels.py` 完成 `label_turns(turns, schedule)`；`__main__.py` 接入 `replay`。写开发用途、版本、归档/调用/截止引用、条款覆盖、`turns.jsonl`、`attempts.jsonl` 与来源清单。 | 同一夹具在两个新目录生成相同的确定性文件；每行能回查原请求，输出不含推理正文；已有输出不会被覆盖，失败尝试之前的有效前缀仍在。 |
| T14 判定 G3 | T13 | `report.py` 按非首轮计算事件轮 A、E0 轮 B、不可判轮 U，报告 A/(A+B) 及 [A/183, (A+U)/183]。按批次、实验臂和自然/注入条件列事件频次、调用/回合覆盖、E6 条款覆盖和标签交叉表；分别给出事件轮与 E0 轮的推理 token 样本数、零值比例及分位数。 | — |

复用依据：[命令分类](../../../flockcue/evidence/command_results.py)、[执行状态](../../../flockcue/runtime/execution_state.py)、[悬停契约](../../../flockcue/runtime/hover.py)、[旧归档解码](../../../posttraining/sft/formatting/wire_samples.py)。

## 学生推理与调用边界

| 任务 | 直接依赖 | 文件与操作 | 验证 |
| --- | --- | --- | --- |
| T15 规范化学生历史 | 无 | `student.py` 复用 v2 的调用 ID、工具名与回执校验，为新版历史保留学生实际生成的推理。 | 两个同名并行调用倒序返回，均关联到原 ID；后一轮含前一轮实际推理。重复或未知 ID 的输入被识别。 |
| T16 渲染开、关两臂 | T15 | `student.py` 使用固定 ID 模板、完整工具列表、`add_generation_prompt=True` 和本臂的开关。存储实际 prompt token ID 与模板身份。 | 同一输入的工具、调用及回执一致；关臂空思考前缀只计入 prompt。2B 使用自身原生 tokenizer 和公开回执投影，4B 采用原有 Qwen3 ID 模板；两者都检查成组调用和归属。原 1.7B 夹具只回归旧版本。 |
| T17 拆分生成 token | T16 | `student.py` 只解析新增 token，按各模型的思考分隔符、合法 EOS 划分推理、正文与总量，记录格式异常。 | 空白推理计零；分隔符及 EOS 计入生成总量。测试非空推理、不同合法 EOS、未闭合、嵌套、关臂异常及长度截断，均可回查原始 ID。 |
| T18 完整解析工具调用 | T15、T17 | `student.py` 把真实合法终止符映射到既有解析边界，完整解析调用组后分配运行时 ID，形成 SDK 可归档的回复与 usage。 | 同批第二个调用损坏时整个调用组均无派发；缺 EOS 的截断回复保留原样。有效调用执行后下一轮仍可依据 ID 匹配回执。 |
| T19 计算请求准入 | T16 | `student.py` 用实际 prompt 长度按方案扣除已耗 token，计算本次可生成上限；`campaign.py` 检查请求次数、单请求与回合截止。 | 测试累计剩余 0 和 1、上下文 32,768 和 32,769、尝试第 13 次请求，以及回合仅余 5 秒时的单请求截止。记录具体停因与实测长度；在超长或超时夹具中验证自动截断、SDK 重试和云端回退次数均为零。 |
| T20 接入单卡模型 | T17、T18、T19 | 在目标 Linux 上为 Qwen3.5-2B 与 Qwen3-4B 加载计划中锁定的权重、原生或 ID 模板和解析器，验证同一后端能运行这两个模型。使用独立推理环境、BF16、batch=1 与相同采样参数，归档权重/tokenizer/模板哈希、采样种子及实际 PyTorch/CUDA 版本，并实现备选 vLLM 的 token ID 适配。 | 用 CPU 替身捕获两臂的生成实参，核对核准输出上限、temperature=0.6、top_p=0.95、top_k=20、重复惩罚中性。用替身核对独立 vLLM 后备进程接口：接受相同模板产生的 prompt ID，返回实际生成 ID，复用同一解码器。夹具验证权重修订、采样、EOS、上下文、取消和无跨槽缓存契约；真实权重在 T37 验证。 |
| T21 绑定进程管道与取消 | T18、T20 | `student.py` 与 `campaign.py` 经本机预接通管道发送白名单内的公开数据，校验槽、轮次和截止；取消后确认生成已停止，必要时回收该进程再开下槽。 | 注入迟到、挂起、错槽三种回复。下一槽的输入和 KV 状态独立；私有场景与 checker 不进入模型进程，请求超时后没有迟到动作。 |

复用依据：[v2 输入与调用解析](../../../scripts/student_input_v2.py)、[工具 ID 模板](../../../training/templates/qwen3-tool-ids-v2.jinja)、[请求截止与排空](../../../scripts/harness_request_limits.py)。

## 初态、评分和运行预算

| 任务 | 直接依赖 | 文件与操作 | 验证 |
| --- | --- | --- | --- |
| T22 物化 dev 初态 | 无 | 用既有物化入口生成开发实验输入。`campaign.py` 校验冻结划分与 dev/development 身份，登记用途，绑定初态、指令、时间线和原 checker 哈希。 | 独立新目录含 5 个父场景的 29 个不同任务；发现混入其他 split 或哈希变更时指出对应任务。 |
| T23 冻结配对槽与种子 | T22 | `campaign.py` 建立 116 个唯一槽；新版四臂先 2B 后 4B，各模型内按父场景 ID、任务 ID 排序，逐题交替开关先后。由 `task_id/轮号` 的 SHA-256 前四字节按大端取种子。 | 每臂恰好 29 槽；同题两臂初态、原评分器和同轮种子一致，重建槽表哈希一致。 |
| T24 封存学生配置 | T23 | `campaign.py` 和 `__main__.py` 实现 `prepare-dev`。登记学生运行配置、新增源码与依赖、模型修订及 v5 的 compact 工具、权限、调度、执行和完成契约；沿用现行基线锁。封存两种候选后端和 T37 选择规则，首槽前另附唯一选定后端指纹。对初态、槽表和冻结源码使用相对路径与内容哈希；供现有 worker 读取的绝对 `study/workspace` 在目标机从清单生成，单列主机路径绑定。 | 比对四臂清单，实验条件中只有所选规模与思考开关随臂改变；修改任一冻结输入时停止。原来的 `agent_harness check` 仍通过。 |
| T25 启动独立 worker | T21、T24 | `campaign.py` 从冻结源码和指定初态为每槽创建新 worker，保持工具权限及 SDK 请求/回复归档。生成审计与仿真工具回执可按同一逻辑轮次关联。 | 两槽 CPU 夹具显示不同 worker 进程、相同初态哈希、初始状态无残留；模型看到的两轮公开历史与实际请求一致。 |
| T26 封存终态并独立评分 | T25 | `campaign.py` 关闭准入、冻结终态、排空并等待 worker 退出；核对退出封存后才从终态启动独立 scorer，调用原 checker。 | 使用已知正、反例各得一次独立评分，PID 与 worker 不同；排空失败、缺退出封存或终态哈希不符的槽保留未评分原因。 |
| — | 无 | — | — |
| T28 建立耐中断的槽记录 | T23、T26、T27 | `campaign.py` 把开始、终止、退出与评分凭据绑定到槽 ID，防止重复领取。恢复时只领取封存清单里的未启动槽，生成含全部计划槽的 `slots.jsonl`。 | 在领取后、评分前和封存后分别中断再恢复；每个槽最多启动一次，已评分结果不被覆盖。两个进程争抢同一槽时只有一个取得执行权。 |
| T29 接通批次入口 | T24、T28 | `__main__.py`、`campaign.py` 接入 `run-dev`，绑定经 T36 核验的 `--g3` 证据与逐轮哈希；允许在 CPU 上直接登记跳过付费批次。通过准入后，在统一账本内执行冒烟、测速、必要的 vLLM 复测、配对和结算；首次正式槽前封存唯一后端，恢复核对其哈希。 | 用冻结的 116 槽表验证 G3 未通过、待判或证据校验失败的分支，CPU 端写出原因及全部未启动槽，GPU 启动次数为 0。正常执行与中断恢复使用小型槽表和 CPU 替身验证。未通过测速时 dev 槽保持未启动，达到额度输出完整槽表，恢复时不得更换已封存的正式后端。 |
| T30 核对逐槽用量与时延 | T25、T26 | — | 对一条两轮夹具手算输入、输出和总 token；超时未取到的用量留空，迟到用量单列，空输出与未闭合推理可辨别。 |
| T31 回归 CPU 完整链 | T29、T30 | `test_thinking_campaign.py` 把模板、替身服务、工具执行、公开回执、冻结终态及独立 scorer 串成一条跨进程夹具。 | 连续两轮、同名并行、畸形调用与取消路径均通过；核对 raw wire、实际送入模型的序列、回执 ID 及策略/评分隔离。CLI、测试、冻结子进程分别记录复用模块的真实路径与模块名；同一业务源码只加载一次，子进程无工作树副本。 |

复用依据：[开发集物化入口](../../../scripts/prepare_task_dataset.py)、[现有 worker 与 scorer](../../../scripts/harness_natural_runner.py)、[终止分类](../../../scripts/episode_termination.py)、[脚本基线配置](../../../experiments/hover-compact-contract-v2/profile.json)。

## 门槛报告与实验交付

| 任务 | 直接依赖 | 文件与操作 | 验证 |
| --- | --- | --- | --- |
| T32 判定 G1 | T23、T28、T30 | `paired_scores.py` 按冻结槽表和独立评分计算各臂成功数 / 29。锁定 T28 槽状态和 T30 用量/停因结构后，可先用合成记录开发。用 2B 开臂判主门槛，逐题列明失败归因、峰值上下文和超长比例，4B 单列。 | 合成 3 题成功、3 题有证据的任务执行失败时通过。若证据排除达标则未通过。缺评分或缺归因仍可能达标则待判。已知纯预算截断不计任务失败。 |
| T33 判定 G2 | T23、T28、T30 | `paired_scores.py` 按任务连接开关两臂，输出成功/失败四格、净改善题数和逐父场景净差。按 T28/T30 的槽与用量结构先做合成配对测试。每臂以 29 为分母列 500 秒回合截止、120 秒请求超时、32,768 上下文超长，并给出合并去重题数。逐类和合并项都比较仅开停止与仅关停止。100,000 累计 token、单轮生成截断和 12 次限额分别报告。 | 2B 完整配对且净增 3 题、至少两个父场景净改善时通过；同样 3 题全在一个父场景时未通过；基础设施造成的缺对记待判。即使四格达到数值门槛，2B 任一指定停因或合并项的仅开停止数大于仅关，也判待判；仅有累计 token 停止时仍按原预算评分。4B 单列。 |
| T34 汇总指标与缺失量 | T14、T30、T32、T33 | — | 对已知用量/时延序列及含未评分槽的夹具复算；四臂各自的计划分母恒为 29，未知量记录原因。 |
| T35 接通报告入口 | T34 | `paired_scores.py` 汇总 G1/G2，`paired_simulation.py` 关联已封存 G3，`__main__.py` 接入模拟及重算，输出 `gates.json` 与中文报告。T39 单独验收正式数据读取与封存。按规格给出通过、未通过、待判及下一阶段建议。 | 用三门均通过、任一未通过、任一待判三类夹具检查决策；预算耗尽且部分槽未运行时仍能出报告，表内每项都能回查归档或槽 ID。 |
| T36 交付真实离线复算 | T13、T14 | 在两个新目录运行 `replay`，比较确定性产物。用既有用量探针、旧 W3 和模板封存工具做独立回归，封存 `g3.json`、逐轮证据和复算结果。用 G3 结论在本机决定是否进入任何付费主机操作；非通过或无法核验时，在 CPU 上将 116 槽登记为未启动并交 T39。 | 四批合计 31 回合、218 请求、214 回复、4 个末尾无回复尝试；31 首轮、183 后续轮；推理 53,319 token，69 行为零。两次表格逐字节一致，旧 W3 的 61 行和模板封存通过。 |
| T36a 准备 Linux 主机 | T27、T31、T36 | 按 plan 的主机准备节在无卡模式优先完成：转移完整冻结快照及原件，在目标目录新建 CPython 3.11.16 的 Agent、server 两个环境，分别安装各自仓库锁；另为两个模型选择可验证的 Linux Transformers / CUDA PyTorch 与备选 vLLM，封存新版推理锁、wheel 来源及哈希。新版 T37 才确认真实生成，旧版环境锁另存。按两个已锁 commit 预取模型（允许镜像），对照官方修订校验权重/分片、索引及 tokenizer/config。 | — |
| T37 校验真实权重并实测墙钟 | T14、T27、T29、T31、T36、T36a | — | 两个模型均通过功能冒烟、占用估算 ≤250 秒且满长 P95 可定，才封存正式后端。否则按 plan 的触发条件对四臂统一改用 vLLM 并同法复测；功能错误、两后端均不合格、测量不足且无可行补救或预算不足，给出停因，T38 零启动。用 250 秒、刚超过 250 秒、P95 右删失三组夹具验证分支。 |
| T38 运行 dev 配对批次 | T31、T37 | — | — |
| T39 封存门槛结论 | T35、T36、T38 | — | 从报告各抽查一项 G1 归因、G2 配对和 G3 事件，可定位独立评分或原回执。未运行项可定位停止记录。结论与三门状态一致，v5 的 93 个源码文件通过基线检查。 |

T37–T39 是实施阶段的实验任务。T39 使用该表给出待判项与阶段报告。训练卡时和新增教师调用额度均为零。**

## 执行入口与验收对应

从仓库根目录运行。G3 的真实复算与 29 题的模拟分属独立输出目录。默认 `prepare-dev` 生成 2B/4B 协议的四臂计划；旧 1.7B 材料保留旧协议身份。Mac 用 `simulate-paired` 产生输入和模拟报告，`report --run <计划目录> --results <一组模拟输入> --output <新目录>` 可单独重算。正式实验留待 Linux T36a、T37 后启动；每个槽从冻结初态恢复，失败和未启动槽均留在计划分母中。

```sh
run_root=$(mktemp -d "$PWD/experiments/runs/thinking-events-XXXXXXXX")
agent_python=agent4drone/.venv/bin/python
export PYTHONPATH="$PWD/scripts"
"$agent_python" -m thinking_events replay --output "$run_root/replay-a"
"$agent_python" -m thinking_events replay --output "$run_root/replay-b"
cmp "$run_root/replay-a/turns.jsonl" "$run_root/replay-b/turns.jsonl"
cmp "$run_root/replay-a/attempts.jsonl" "$run_root/replay-b/attempts.jsonl"
"$agent_python" scripts/prepare_task_dataset.py --dataset experiments/task-splits-v1 --split dev --purpose development --output "$run_root/dev-study"
"$agent_python" -m thinking_events prepare-dev --study "$run_root/dev-study" --output "$run_root/dev-run"
"$agent_python" -m thinking_events run-dev --run "$run_root/dev-run" --g3 "$run_root/replay-a/g3.json"
"$agent_python" -m thinking_events simulate-paired --run "$run_root/dev-run" --output "$run_root/simulated"
"$agent_python" -m thinking_events report --run "$run_root/dev-run" --results "$run_root/simulated/cases/pass/results.json" --output "$run_root/pass-replay"
cmp "$run_root/simulated/cases/pass/gates.json" "$run_root/pass-replay/gates.json"
cmp "$run_root/simulated/cases/pass/REPORT_CN.md" "$run_root/pass-replay/REPORT_CN.md"
```

现有保护性回归可独立运行：

```sh
agent4drone/.venv/bin/python -m agent_harness check
agent4drone/.venv/bin/python docs/results/agent-samples-student-input-20261103/verify_delivery.py
training/.venv-input/bin/python docs/results/student-tool-ids-v2-20261103/verify_delivery.py
python3 -I docs/audit/teacher-reasoning-usage-20261108/probe.py | diff - docs/audit/teacher-reasoning-usage-20261108/summary.json
```

| 规格 | 实施任务 | 验收清单核查的证据 |
| --- | --- | --- |
| AC1 确定性与公开输入 | T01–T10、T13、T36 | 前缀、哈希、正反例、泄漏检查及字节比较 |
| AC2 四批复算 | T01–T04、T11–T14、T36 | 计划槽、尝试和逐轮总账、教师用量对账 |
| AC3 标签格式 | T10–T13、T36 | 版本、证据截止位置、各标签、缺正文、固定间隔残差及推迟原因 |
| AC4 三道门槛 | T14–T39 | — |
