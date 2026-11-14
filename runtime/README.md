# 正式任务执行

运行器从原始 1,500 题生成冻结的 30 场、600 题计划，核验计划的 SHA-256。正式首遍用 8 个并发场景，每场 20 题保持原顺序。场景内共享黑板，每题执行前按原设置降落和充电。500 秒动作窗口关闭后，等待已接收请求完成、冻结仿真时钟，再从独立的检查接口对终态评分。每个新建 stage 最多发起一轮正式首遍。

## 环境

在项目根目录使用 Python 3.11.16 和 uv。仿真、Agent 和调度进程按原正式部署分别锁定依赖。

```bash
uv venv --python 3.11.16 simulation/server/.venv
uv pip sync --python simulation/server/.venv/bin/python simulation/requirements.txt
uv venv --python 3.11.16 flockcue/.venv
uv pip sync --python flockcue/.venv/bin/python flockcue/requirements.txt
uv venv --python 3.11.16 runtime/.venv
uv pip sync --python runtime/.venv/bin/python runtime/requirements.txt
```

已有环境也可分别通过 `FLOCKCUE_SERVER_PYTHON`、`FLOCKCUE_AGENT_PYTHON` 和 `FLOCKCUE_CONTROLLER_PYTHON` 指定对应的 Python 可执行文件。在线预检会核对三个环境的依赖版本。

## 准备、执行与复核

```bash
runtime/.venv/bin/python -m runtime prepare
runtime/.venv/bin/python -m runtime verify
```

默认输出位于仓库旁的 `<仓库名>-runs/flockcue-600-first-pass/`。每条命令都接受 `--stage PATH`。准备与校验均在本地执行，分别核对计划、题目原件和运行源码。

```bash
runtime/.venv/bin/python -m runtime preflight
runtime/.venv/bin/python -m runtime run
runtime/.venv/bin/python -m runtime status --attempt /absolute/path/to/stage/runs/ATTEMPT
runtime/.venv/bin/python -m runtime score --attempt /absolute/path/to/stage/runs/ATTEMPT
```

`preflight` 使用官方 DeepSeek 服务验证模型和一次两轮工具交互，`run` 启动正式 600 题。凭据从 `DEEPSEEK_API_KEY` 或本地 `runtime/llm_settings.local.json` 的 `provider_configs.deepseek_official.api_key` 读取，也可用 `--settings PATH` 指定同格式的本地文件。任务源码快照和配置归档均排除本地凭据。

`score` 读取已封存的任务原始回执并复算原树，逐场核验所有文件。它分别给出原检查通过数、按正式发布规则计入终止性服务故障后的整题通过数，以及确认工作进程退出的严格部署通过数。现行运行和已有的 600 题原始归档都可复算。对外置原始归档使用本项目新准备的 stage 和该归档的绝对 `--attempt` 路径。

## 学生检查点的 A 折内部验证

每个候选 LoRA 使用独立的仓库外 stage。准备时校验并复制权重和配置，冻结三场 60 题、485 个底层检查、推理预算和首轮逐题结果。历史对照来自同一 A 折此前执行的基座与首轮 SFT。原运行亦记录了仿真天气的自然波动。[首轮验证报告](../datasets/results/student-validation-60/REPORT_CN.md)

```sh
"$FLOCKCUE_CONTROLLER_PYTHON" -m runtime.student_validation prepare --stage "$stage" --adapter "$checkpoint"
```

依次执行 `72ccacd0`、`0293952c`、`cc14d231`。每场启动独立的本地学生服务，载入同一个 stage 的冻结权重，待 `/health` 就绪后执行对应场景，再关闭该服务。控制器会核对学生推理设置、权重 SHA-256、模板、仿真与 Harness 源码和三个运行环境的包版本。种子从题目 ID 和轮次确定。

```sh
"$student_python" -m posttraining.inference.server --model "$base" --adapter "$stage/adapter" \
  --port 21769 --log "$logs/$scene.events.jsonl"
# 从另一终端执行当前场景
"$FLOCKCUE_CONTROLLER_PYTHON" -m runtime.student_validation run --stage "$stage" \
  --arm sft --scene "$scene" --endpoint http://127.0.0.1:21769/v1
```

三场封存后运行 `"$FLOCKCUE_CONTROLLER_PYTHON" -m runtime.student_validation report --stage "$stage"`，复核各题检查树、场内原题顺序与完整分母。`summary.json` 分别统计正式整题通过数、独立检查树达标题数、叶检查和停止原因。`pairs.csv` 逐题列出新权重与两个历史对照的增减。省略 `prepare --adapter` 中的适配器参数可重建首轮基座与首轮 SFT 的双臂计划。

对基座和指定 SFT 权重重新做无累计 token 上限的配对评测时，在 `prepare --adapter` 和两组的推理服务启动命令中均加 `--no-episode-token-limit`。基座服务省略 `--adapter`，SFT 服务使用 `--adapter "$stage/adapter"`。对每组实跑三场后执行 `report`。计划与服务健康记录中的 `episode_tokens` 为 `null`；每题 500 秒、上下文 32,768、单轮最多生成 2,048 token，实耗仍逐轮记账。
