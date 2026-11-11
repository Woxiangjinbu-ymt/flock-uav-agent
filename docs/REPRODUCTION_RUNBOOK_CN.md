# FlockCue 运行与验证入口

更新于 2026-11-08。以下命令从仓库根目录运行，组件使用各自的 Python 环境。[环境安装](DEPENDENCY_SETUP_CN.md) · [研究路线](RESEARCH_ROADMAP_CN.md)

## 复现当前 Harness 基线

```bash
agent4drone/.venv/bin/python -m agent_harness check
agent4drone/.venv/bin/python -m agent_harness smoke --output experiments/runs/flockcue-smoke-01
```

`check` 核对 `general-agent-v5-local` 的配置、源码、固定输入和依赖清单。`smoke` 用脚本模型和本地仿真重新运行两个回合，各自独立评分。输出需要一个新目录，再次运行时更换名称。打开该目录的 `summary.json` 对照两条轨迹：有显式悬停回执与只有悬停状态时，原评分器均通过，Harness 的公开完成判断分别为已验证与未验证。两条命令均在本地完成。[基线锁、代码地图与复现范围](../agent_harness/README.md)

## 从封存的真实回合复核开发样例

以下命令只读历史 DeepSeek 交互并对照已归档摘录。两次 `diff` 退出码为 0 表示复算一致。最后一条用本地 tokenizer 检查四例的公开输入和监督目标分界，输出每例的目标起点、监督 token 数和总长度。[复核来源及局限](audit/trajectory-verifier-plan-20261107/REPORT_CN.md) · [tokenizer 环境](../training/README_INPUT_CN.md)

```bash
python3 docs/audit/trajectory-verifier-plan-20261107/extract_examples.py | diff - docs/audit/trajectory-verifier-plan-20261107/evidence-examples.json
python3 docs/audit/trajectory-verifier-plan-20261107/audit_queries.py | diff - docs/audit/trajectory-verifier-plan-20261107/query-repeats.json
training/.venv-input/bin/python docs/audit/trajectory-verifier-plan-20261107/check_input.py
```

四个开发摘录来自 11-07 的验证器方案，第二篇论文再使用。第一篇论文的教师推理用量可只读复算，`diff` 退出码为 0 表示一致：

```bash
python3 -I docs/audit/teacher-reasoning-usage-20261108/probe.py | diff - docs/audit/teacher-reasoning-usage-20261108/summary.json
```

教师采集、SFT、导出和学生回接的运行入口仍待开发。[第 1 步规格](specs/01-thinking-events/spec.md) · [后训练方案](POSTTRAINING_PLAN_CN.md)

## 启动平台应用

需要操作 GUI 或 Agent 服务时，在独立终端分别启动以下组件。GUI 和服务沿用各自的配置和凭据。研究用 v5 配置由上面的 FlockCue 命令显式选择。

```bash
cd server
.venv/bin/python main.py --api-only
```

```bash
cd controller
.venv/bin/python main.py
```

```bash
cd agent4drone
.venv/bin/python agent_api_service.py
```

首次配置可参考示例，已有配置保存在本地 `llm_settings.json`。详细说明见 [Server](../server/README.md)、[三维可视化](../view3d/README.md)和[中文教程](../tutorial/tutorial_zh.md)。

## 核验历史批次

以下分别只读核验当时的 v4 真实小批和 v5 本地回归。其他版本的参数、结果及已有输入样本的开发用途见[脚本导航](../scripts/README.md)和[实验依据](EXPERIMENT_EVIDENCE_CN.md)。

```bash
agent4drone/.venv/bin/python scripts/deepseek_hover_pilot.py verify --run docs/results/deepseek-hover-pilot-20261103/run-01
agent4drone/.venv/bin/python scripts/validate_hover_compact_contract.py verify --output docs/results/hover-compact-contract-20261103/local-01
```

11-03 方法评审使用的四份文档和当时的源码、数据指纹保存在[原始清单](results/harness-method-validation-20261103/review-packet/manifest.json)。工作区方法说明后来增加了 v4/v5 证据，旧 `freeze_harness_validation.py verify` 会将整个当前工作区与原封存作比较并报告版本差异。核对当时的方法定义请读[当批原件](results/harness-method-validation-20261103/review-packet/documents/docs/HARNESS_METHOD_CN.md)。

旧部署入口 `scripts/reproduction.py` 的 `status` 与 `summarize` 可读历史结果。执行记忆 v1–v7 的一次性 pilot 和汇总脚本已退役，恢复位置见[历史清理清单](maintenance/cleanup-20261028.json)。
