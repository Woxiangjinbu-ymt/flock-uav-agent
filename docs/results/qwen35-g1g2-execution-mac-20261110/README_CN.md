# 29 题 G1/G2 执行、独立评分与报告的 Mac 验收

2026-11-10。使用原 5 个父场景的 29 份冻结初态，Qwen3.5-2B 和 Qwen3-4B 各设开、关思考两臂，每臂 29 槽。四臂均使用各自的真实分词器、模板、解析器和固定回复。worker 恢复初态，执行公开工具，排空后封存终态；独立 scorer 在另一个进程运行该题的原始 checker。[逐臂计数与时延](REPORT_CN.md) · [逐槽证据索引](gates.json)

| 验收项 | 本次观察 |
| --- | --- |
| 冻结批次 | 首次领取 2 槽后退出，报告保留 114 个未启动槽。恢复后新增 114 槽，四臂各有 29 份独立评分。 |
| 工具与回执 | 每槽第 1 轮查询机队，第 2 轮收到对应公开回执后返回完成标记。232 次请求逐条从 SDK 归档重新分词，并核对固定输出的 token ID、思考开关和种子。 |
| 评分 | 固定回复没有执行任务要求的飞行动作。四臂均为 0/29。即使模型输出完成标记，逐槽结果仍取独立 checker。 |
| — | — |
| 移机准备 | 在 Mac 另一目录解包，校验 522 个文件的摘要，复用本机环境运行交付包验收。Agent 组 117 项、学生组 29 项全部通过。Linux 目标机需重建环境后独立复验。 |

[逐请求复算](verification.json) · [本地运行目录](../../../outputs/flockcue-g1g2-execution-mac-20261110/) · [冻结输入迁移包](../../../outputs/flockcue-g1g2-execution-mac-20261110/linux-cpu.tar.gz)

## 在 Mac 复跑

从仓库根目录执行。运行目录必须新建。2B 默认读取已按修订校验的本机 tokenizer 缓存，可用 `--qwen35-tokenizer` 指定目录。4B 使用与其固定修订逐文件哈希相同的本地 tokenizer。固定生成内容不读取题目、私有状态、原检查或模型权重。

```sh
export PYTHONPATH="$PWD/scripts" HF_HUB_OFFLINE=1
py="$PWD/agent4drone/.venv/bin/python"
student="$PWD/training/.venv-input/bin/python"
study="$PWD/outputs/flockcue-g1g2-mac-acceptance-20261108/dev-run/study"
g3="$PWD/docs/results/thinking-events-g3-20261108/run-01/g3.json"
out=$(mktemp -d)
"$py" -m thinking_events prepare-dev --study "$study" --output "$out/dev"
"$py" -m thinking_events run-dev --run "$out/dev" --g3 "$g3" --backend fixture --student-python "$student" --max-new 2
"$py" -m thinking_events report --run "$out/dev" --output "$out/partial"
"$py" -m thinking_events run-dev --run "$out/dev" --g3 "$g3" --backend fixture --student-python "$student"
"$py" -m thinking_events report --run "$out/dev" --output "$out/report"
```

本地和正式批次各用一份新目录。单写者锁和持久领取记录保证同槽最多启动一次。若中断时终态回条已落盘，恢复过程补写对应账目；已领取且缺终态回条的槽记为未评分，保留中断原因。直接传入合成成绩的历史入口仍需显式指定 `report --results`。原始本次证据可运行以下命令复核；它会重建两种报告、逐槽复核评分封存，并复算全部 232 次请求的输入与生成 ID。

```sh
training/.venv-input/bin/python docs/results/qwen35-g1g2-execution-mac-20261110/verify_delivery.py
```

## GPU 交接

使用新的 [2B/4B CPU 迁移包](../../../outputs/flockcue-g1g2-execution-mac-20261110/linux-cpu.tar.gz)，核对[压缩包 SHA-256](../../../outputs/flockcue-g1g2-execution-mac-20261110/linux-cpu.tar.gz.sha256)。包内 `prepared-dev` 是四臂全部未启动的计划，含 29 题的冻结初态和源码。另带 2B tokenizer；两个模型的真实权重需在目标机按固定修订校验。打包命令为 `scripts/package_thinking_student.py --paired --study <物化目录> --output <新文件.tar.gz>`。

1. 按既有流程在同一 Linux 主机复验学生接入和 20 题连续会话。学生迁移包中运行 `bash scripts/setup_student_cpu.sh` 和 `bash scripts/accept_student_cpu.sh <新的绝对输出目录>`。后者写出 `verification.json`、原始测试与冒烟记录。会话复验沿用独立交付。[Linux 学生复验步骤](../thinking-student-cpu-20261108/LINUX_CPU_CN.md) · [会话移交与原检查](../flockcue-mac-session-20261108/REPORT_CN.md)
2. 在目标机按已批准的 T37 方案核对两个固定修订的权重、后端锁、模板、CUDA 设备和 BF16，完成四臂冒烟、三档自然测速、每模型 20 次开臂满长测速。本次 Mac 验收覆盖 T38 的准入读取，目标机的 T37 测量及其封存单独执行。[T37 测速与预算](../../specs/01-thinking-events/plan.md#t37-测速与-t38-准入)
3. 给一份全新正式运行目录传入同批次的 T37 证据后，运行 `run-dev --run <正式目录> --g3 <g3.json> --backend transformers --student-python <已测速的解释器> --model-root <离线权重父目录>`。父目录下的两个目录名为 `Qwen3.5-2B`、`Qwen3-4B`。仅在符合已定切换条件且 vLLM 已通过同样测速时使用 `--backend vllm --backend-python <已测速的引擎解释器>`。正式报告使用 `report --run <正式目录> --output <新目录>`；

### T37 向批次执行器移交的文件

在正式运行目录的 `t37/admission.json` 记录 `schema=paired-t37-admission-v1`、`campaign_sha256`、`backend` 以及下列字段。报告可以离线复核已封存的准入材料；启动真实服务时另外核对当前主机、解释器、软件版本、设备、模型清单和模板。首槽前封存批次身份，恢复时继续检查相同指纹。

| 字段 | 值或来源 |
| --- | --- |
| `host` | `node` 取目标机的 `platform.node()`，`system=Linux`，`card=A100 40GB`，`cards=1`，`bf16=true`，`batch_size=1`。 |
| `interpreters`、`versions` | `student`、`backend` 为测速时两解释器的绝对路径。Transformers 路径两者相同。版本包括 `transformers`、`tokenizers`、`torch`、`cuda`，适用时另含 `vllm`。 |
| `artifacts` | 各项均提供相对 `t37/` 的 `path` 和 `sha256`。包括 `linux_cpu`（学生验收 verification.json）、`session_cpu`（连续会话 verification.json）、`backend_lock`（实际环境锁）、`measurement_archive`（下述测量索引）。vLLM 还需 `transformers_fallback_reason` 指向切换依据。 |
| `lease` | — |
| `measurement_archive` 内容 | `schema=paired-t37-measurements-v1`、相同的 `backend`、`campaign_sha256`，以及 `models` 按两个正式模型名索引。每模型含 `revision`、`functional_smoke`、`natural_seconds`、`full_length`。`natural_seconds` 按 `on/off` 和 `4096/8192/13000` 两层索引，每格 3 次请求耗时；超时记 null。`full_length` 包含 20 条 `{seconds, generated_tokens}`，其中 null 秒表示达到 120 秒的右删失。由实测值重新判定开臂两种工作负载是否均在 250 秒以内，以及满长 P95 是否可确定。 |

