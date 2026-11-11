# FlockCue 在 A100 租用机上的迁移验收

2026-11-10 已将项目迁至用户指定的 `/uav/flockcue-20261110/flockcue-a100`。机器为 Linux x86_64，单张 NVIDIA A100 PCIe 40 GB，驱动 570.211.01。`/uav` 位于 200 GB 的实例根文件系统，平台的跨实例保留期限未核实。本次 CPU 验收、环境锁和权重摘要已回传 Mac。

| 已验证项目 | 目标机结果 |
| --- | --- |
| 运行材料 | 新版配对包 522 项、会话基础包 490 项、会话补充包 24 项通过摘要核验，分为两套运行目录。600 题包在 Linux 通过独立包校验，含 30 场、15 个分层。 |
| 学生链路 | Linux 3.11.16，Agent 117 项、学生 29 项测试全部通过，均无跳过。v5 基线 93 个源码文件通过核验。G3 从开发归档复算通过，29 题四臂的 116 个正式槽全部未启动。[学生验收](student-cpu.json) · [目标机槽表摘要](plan-on-server.json) |
| 连续会话 | 场景 `04cd6269` 的正常与重启运行均获得 20/20 题独立评分，每遍 79 项原检查、19 次题间交接，与 Mac 逐题对照一致。超时和服务中断两种用例也与各自的 Mac 记录一致。[Linux 原始核验](session-cpu.json) |
| 原始权重 | Qwen3.5-2B 的 6 个文件、Qwen3-4B 的 11 个文件按固定修订逐文件核验，共 12,622,029,501 字节。项目校验脚本已在 Linux 复核。[2B](Qwen3.5-2B.project-check.json) · [4B](Qwen3-4B.project-check.json) |
| 首选 GPU 推理环境 | Python 3.11.16、PyTorch 2.8.0+cu128、Transformers 5.22.2、tokenizers 0.23.3。53 个安装包核验 SHA-256，依赖一致性检查通过。两种模型、两种思考模式的固定回复往返与 Mac 一致，真实设备上的 BF16 矩阵乘法通过。目标机权重加载次数与模型生成次数均为 0。[硬件与运行时](runtime.json) · [环境锁](requirements-linux-cu128.lock) · [安装包来源与摘要](wheels.lock.json) |

## 在服务器上接手

```bash
cd /uav/flockcue-20261110/flockcue-a100
source operations/env.sh
source operations/venv-inference/bin/activate
```

| 服务器项目内的路径 | 用途 |
| --- | --- |
| `paired/flockcue-g1g2/prepared-dev/` | 已封存的 29 题初态、原 checker、四臂计划和源码。为真实批次新建运行目录。 |
| `session/flockcue-g1g2/experiments/flockcue-benchmark-600-v1/` | 后续教师采数与训练使用的题单、场景与建议折表。 |
| `models/Qwen3.5-2B/`、`models/Qwen3-4B/` | 已校验的本地原始模型。 |
| `operations/venv-inference/`、`operations/gpu-candidate/` | 首选 GPU 推理解释器、安装包、锁和硬件核验。训练依赖待训练方案确定。 |
| `evidence/paired-cpu-GLT58hkf/run/`、`evidence/session-cpu-33qaF5b3/run/` | 两项 Linux 复验的逐项记录和原始归档。 |
| `reference/docs/` | 训练方案草案和开发材料；16 条开发样例的批次标记为 `eligible_for_training=false`。 |

## 下一步

先接好 T37 实测采集与封存入口，在已验证的硬件和环境上对两个模型做真实加载、生成冒烟和测速；现有批次程序可读取已封存的 T37 证据。当前尚无 T37 实测结果，备选 vLLM 尚未在该机验收。通过吞吐和时限准入后运行原始模型 29 题开关对照。G1、G2 实测完成前，正式教师轨迹、SFT 训练样本和 LoRA 权重仍待产出。[T37 测量与判据](../../specs/01-thinking-events/plan.md#t37-测速与-t38-准入) · [后续采数与训练草案](../../specs/02-benchmark600-sft/spec.md)

用户确认服务器包周预付，无额外服务器费。本次未调用教师 API、未训练权重。
