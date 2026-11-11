# 约 2B 学生输入兼容验收 v1

本目录仅实现 tokenizer、工具模板、标签 mask 与长度验收。Qwen3-1.7B 是 text-only 输入兼容候选，**不是已冻结训练基座**。没有下载权重、模型推理、训练或新增 provider 请求；没有 GPU/显存/吞吐结论。输出仅用于 development 输入 smoke，`training_eligible=false`。

## 固定来源和独立环境

- 官方候选：[Qwen/Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B)，固定 revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`。模型卡标注 1.7B total parameters、1.4B non-embedding parameters；不把参数规模当作任务能力证据。
- 小文件在 `tokenizers/qwen3-1.7b-input-v1/`：仅 LICENSE、README、config、generation_config、tokenizer_config、tokenizer.json、merges、vocab。manifest 记录每个官方 commit URL、字节数与 SHA-256；代码同时锚定 manifest hash。`chat_template` 来自 tokenizer_config，未改写官方模板。没有下载 safetensors、bin 或权重 index。
- 安装隔离在 `.venv-input/`，不更改 Agent 环境。Python 3.11.16；直接依赖见 `requirements-input.in`，实际完整依赖版本见 `requirements-input.lock`。固定 Transformers 4.57.3 / tokenizers 0.22.1 / huggingface_hub 0.36.0 / Jinja2 3.1.6。该版本满足[官方模型卡](https://huggingface.co/Qwen/Qwen3-1.7B)所述 Transformers >=4.51 的 Qwen3 支持下限；不宣称这是最新版本。
- 加载仅 `AutoTokenizer.from_pretrained(local_path, local_files_only=True, trust_remote_code=False, use_fast=True)`，不导入 AutoModel。环境未安装 PyTorch、TensorFlow 或 Flax。文件白名单和 hash 在加载前校验，新增 remote-code 文件亦拒绝。

```sh
python3.11 -m venv training/.venv-input
training/.venv-input/bin/python -m pip install -r training/requirements-input.lock
training/.venv-input/bin/python -m pytest -q scripts/tests/test_student_input.py
training/.venv-input/bin/python scripts/student_input.py --samples CONVERTER_BUNDLE/samples.jsonl --source-manifest-sha256 TRUSTED_CONVERTER_MANIFEST_SHA256 --output NEW_OUTPUT
```

## 输入与监督契约

转换器 `agent-decision-sample-v1` 的 `messages[-1]` 是唯一当前 assistant 目标，`supervision.target_message_index` 与 `message_loss_mask=[0,...,0,1]` 必须严格一致。历史 system/user/assistant/tool 和工具 schema 属于输入，只有当前 assistant continuation 进入 labels；监督目标包含文本、完整 tool-call JSON、EOS 与模板末尾换行。assistant 角色起始标记与空的 `<think>...</think>` non-thinking scaffold 属于 prompt，labels 为 -100。没有把确定性子程序内部动作扩成 assistant 决策。

`reasoning_content`/`reasoning` 的非空文本及 assistant 内嵌 thinking markup 被拒绝；原始教师推理应由转换器按明确策略剥离并仅记录 hash/字符数。学生输入和 labels 均不含教师推理。text-only v1 只接受 system/user/assistant/tool，不接受 multimodal 数组、developer 或其它会被官方模板忽略的字段。至少保留一条 user 任务，system 只能位于开头。

工具 schema 使用实际请求的完整列表，不删工具、不压缩说明。外层 OpenAI `function.arguments` 若为 JSON 字符串，仅严格解析一次为对象；内部 `input_json` 字符串保持原样。重复 JSON key、非有限数、未声明工具及失配的回执 ID 被拒绝。消息内容、工具 schema 与参数中的原始聊天角色/结束控制 token 也被拒绝，避免它们改变模板边界。

按照[官方工具模板说明](https://qwen.readthedocs.io/en/latest/getting_started/concepts.html)，Qwen3 用 `<tool_call>` 包裹工具名/参数，用 `<tool_response>` 包裹回执。固定模板不打印 OpenAI call ID，也不打印回执 name。因此 v1 验证调用 ID 唯一、每次历史调用有且只有一个回执、回执顺序与调用列表一致。原 ID、工具名、参数 hash 与回执索引进入审计 sidecar，**不能声称 ID 出现在 token 中**。有序且工具名不同的 parallel 调用可编码；乱序回执和同名 parallel 调用按模板兼容限制拒绝，单列为 `id_mapping/order_unsupported` / `id_mapping/same_name_parallel_unsupported`。它们不自动意味着源转换器样本错误。将来若需要 ID 保真的模板，应另立版本并重新验证；当前不悄悄重排回执。

## 实际 token 对齐与长度

分别用官方模板渲染完整历史的 generation prompt（`enable_thinking=False`）和加入目标后的完整文本；先证明文本前缀一致，再分别以 `add_special_tokens=False, truncation=False` 调用实际 tokenizer，证明 token ID 前缀一致。任一边界不一致立即拒绝，不能按字符数或估算 token 数分割 labels。官方固定模板没有 `{% generation %}` 标签，故不用 `return_assistant_tokens_mask` 的空掩码代替真实监督。官方文档说明渲染后另行 tokenize 时应[关闭再次添加特殊 token](https://huggingface.co/docs/transformers/v4.57.3/en/chat_templating)。

默认全量输入或拒绝，不截断任务、历史工具关联、最新反馈或目标。显式 right padding 时，attention 对实 token 为 1、pad 为 0，所有 pad label 为 -100；原始 `sequence_length` 与 `target_start` 保留。没有 packing、跨样本 attention、自动裁剪或重标 token。

候选长度测量为 4096 / 8192 / 16384 / 32768；选能容纳全部兼容且不超过 32768 的样本的最小一档，仅作为输入 smoke 长度。大于 32768 的样本仍拒绝并保留分母；全部不兼容时记录显式 32768 上限及无可加载样本，不声称某一长度适配成功。模型卡写 32768 context，而固定 revision 的 config 写 `max_position_embeddings=40960`；本 adapter 保守上限为 32768，不依这个差异扩大训练配置。长度不是生成预留预算：正式部署还要另计最大输出、总上下文和实际 GPU 资源。

输出 manifest 绑定输入转换包与 samples hash、tokenizer commit/manifest/template hash、脚本、输入协议、依赖锁和实际版本。`length-report.json` 的每档分母由可容纳、超长、模板兼容拒绝三类组成；拒绝样本不被隐藏。`encoded.jsonl` 仅含选定上限内样本，`rejections.jsonl` 单列原因；输出目录必须全新且位于原始转换包之外。

## 边界

已知开发轨迹和 scripted Mock 只能检验输入/标签/关联/加载实现，不能当作合格 Teacher 训练集、模型能力或泛化成绩。真实历史教师日志同样带既有开发暴露与批次限制。最终训练基座、工具 schema 简化策略、context、生成预算、LoRA/QLoRA、GPU、正式数据质量门槛与后续付费采样均待独立确定。whole-turn 裁剪如需实施应另版本、保留可审计关联与任务/最新反馈，再检验效果；当前没有实施。
