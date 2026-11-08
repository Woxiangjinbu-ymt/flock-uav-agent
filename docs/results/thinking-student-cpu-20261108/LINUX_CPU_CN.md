# FlockCue G1/G2 · Linux 无卡验收

目标是确认同一份冻结实验能在新的 Linux 路径上恢复，学生可以根据真实工具回执继续下一轮，独立评分器能读取终态。CPU 回放使用固定的 1.7B tokenizer 和预置生成回复。116 个开发槽保留为未启动，实际学生成绩待 T37/T38。

## 1. 准备主机与交付文件

建议使用 Linux x86_64、glibc 2.35 或兼容更新版本，以及 CPython 3.11.16。无卡阶段可用普通 CPU 主机；如有持久盘，把压缩包、运行目录和后续模型缓存放在该盘。复制发布的 `.tar.gz` 和同名 `.sha256` 到服务器，在新目录内执行：

```sh
sha256sum -c flockcue-g1g2-cpu-20261108.tar.gz.sha256
tar -xzf flockcue-g1g2-cpu-20261108.tar.gz
cd flockcue-g1g2
sha256sum -c SHA256SUMS > /dev/null
```

包内有版本锁定的 Agent/仿真源码、原 checker、G3 封存及可重算的四批归档、固定模板和 tokenizer，以及已准备好的 29 题开发初态与 116 槽清单。`prepared-dev/source` 与 `prepared-dev/study` 用相对路径和内容哈希封存。正式模型权重在目标主机按固定修订单独预取。实验数据仅供项目研究使用。

## 2. 在目标机创建 CPU 环境

准备可用的 `uv`。安装项目使用的解释器后，脚本在当前目录分别创建 Agent、仿真服务和 tokenizer 的虚拟环境，按各自锁文件安装并检查依赖。已有该版本解释器时可以设置 `FLOCKCUE_PYTHON=/绝对路径/python3.11`。环境首次安装需要访问软件包源，后续验收离线运行。

```sh
uv python install 3.11.16
bash scripts/setup_student_cpu.sh
```

## 3. 运行无卡验收

从解包后的根目录执行。每次验收给出新的输出路径；在包内使用 `tmp/<新名称>`，也可使用包外持久盘的绝对路径。

```sh
bash scripts/accept_student_cpu.sh "$PWD/tmp/accept-linux-01"
```

| 检查 | 观察结果 |
| --- | --- |
| `host.json` | 目标机系统、架构、CPython 3.11.16、三个环境路径、依赖检查与实际锁哈希 |
| `baseline-check.json`、`baseline-smoke.json` | v5 核验 93 个源码文件；两次脚本回合独立评分，provider 调用数为 0 |
| `replay/g3.json` | 四个确定性产物与封存件逐字节相同，A=77、B=106、U=0 |
| `dev-run/host-binding.json` | 工作区、初态和解释器全部指向当前主机；移交的冻结内容哈希通过 |
| `dev-readiness.json` | G3 通过，四臂共 116 槽保持未启动，G1/G2 待判，GPU 启动数与收费为 0 |
| `student-smoke/`、`student-smoke.json` | 预置回复经过真实 tokenizer、模型进程管道、仿真工具和独立评分。检查请求用量、调用 ID、停止原因与真实进程 PID |
| `agent-tests.xml`、`student-tests.xml` | 两组回归均无失败，pytest 文本输出保存在对应的 `.log` 文件中 |

脚本先核验包内 SHA-256，再创建工作副本。断点或失败时保留原输出用于定位，重新验收选择新目录。T37 的真实权重测速与 T38 的正式推理需要独立的实机记录。

## 4. 按需在持久盘预置真实后端

仅当已安排目标机环境时做此节。CPU 验收可独立完成。两个推理环境的 Linux x86_64 哈希锁均已提供；默认 Transformers 4.57.3，备选 vLLM 0.11.2，两环境的 PyTorch 为 2.9.0，Linux CUDA 运行库为 12.8。目标机需额外核对驱动兼容性和 BF16 可用性。

```sh
uv venv --python 3.11.16 training/.venv-inference
uv pip sync --python training/.venv-inference/bin/python --only-binary :all: --require-hashes training/requirements-inference-linux.lock
uv pip check --python training/.venv-inference/bin/python
# T37 需要测试备选后端时使用独立解释器。
uv venv --python 3.11.16 training/.venv-vllm
uv pip sync --python training/.venv-vllm/bin/python --only-binary :all: --require-hashes training/requirements-vllm-linux.lock
uv pip check --python training/.venv-vllm/bin/python
```

权重校验基于官方固定 commit 的文件清单。大文件使用官方 LFS SHA-256，其他文件从固定修订下载并核对 Git blob 身份后计算 SHA-256。可使用官方源或通过 `HF_ENDPOINT` 指定可信镜像，校验基准始终来自包内官方清单。将下例目录换成实际持久盘路径。

```sh
weights=/mnt/persistent/flockcue-models
for model in 1.7B 4B; do
  training/.venv-input/bin/python scripts/verify_student_weights.py \
    --model "$model" --directory "$weights/$model" --download
done
```

模型已预取时省略 `--download` 即可离线复核。保存两份核验结果、主机平台信息与验收目录，供 T36a/T37 使用。[Qwen3-1.7B 固定修订](https://huggingface.co/Qwen/Qwen3-1.7B/tree/70d244cc86ccca08cf5af4e1e306ecf908b1ad5e) · [Qwen3-4B 固定修订](https://huggingface.co/Qwen/Qwen3-4B/tree/1cfa9a7208912126459214e8b04321603b3df60c) · [uv 的跨平台解析](https://docs.astral.sh/uv/concepts/resolution/#platform-specific-resolution)
