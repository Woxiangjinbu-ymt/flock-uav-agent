# 教师轨迹 v2 全量采集

2026-11-27 批准使用精简回执的条件窗口策略采集 51 个训练场景、1,020 题。三场试采首遍实得 47/60，原登记门槛为 48/60。固定长题的后段示范覆盖为 6/15，原生参照为 0/15。此次放量决定接受这个实测结果。[试采报告](../teacher-collection-v2-pilot/compact-48k/README.md) · [全量登记](registration.json)

| 来源 | 场景 | 题位 | 独立检查 |
| --- | ---: | ---: | ---: |
| 已封存的精简回执试采 | 3 | 60 | 408 |
| 本次新增首遍 | 48 | 960 | 6,043 |
| 全量训练侧 | 51 | 1,020 | 6,451 |

角色表从原始场景表继承，采用试采时登记的六个新增内部验证场。训练由 A 折 12 场和扩充池 39 场组成。内部验证 9 场和独立测试 15 场按整场隔离。保留原基准既有开发暴露记录。采集、逐轮提取和训练行构建使用同一份场景名单。[角色表](scene-roles.csv) · [父表](../../original-benchmark/scene-roles.csv)

教师使用官方 `deepseek-flash`，温度 0.1。Qwen3.5-2B 的常规输入上限为 30,720 token；只有最新完整调用和回执仍装不下时才使用 47,104 的输入上限。单轮学生输出预留 2,048 个 token，整题无累计 token 上限。精简感知和移动回执，保存教师实发请求、原始工具回执与独立评分。每题 500 秒，8 个场景并行，场内沿用原题序和共享状态。

全量分母包括失败、超时和资料不完整的题。三个复用场景按登记的 attempt 与封存摘要核验，首次采集的 60 个题位各计一次。训练行以完整通过题的可表示教师轮为候选，公开执行证据的审阅理由、后置回执和私有评分单独保存。

新增 48 场的首遍于 2026-11-27 22:30（新加坡时间）启动，运行提交为 `4a98aa52`，编号为 `20261127-223047-flockcue-teacher-v2-training-5f598d84`。运行日志在仓库旁的 `MultiUAV-Plat-runs/teacher-collection-v2/full/run.log`。结束后合并评分写入同目录的 `score.json`，逐场训练侧轮次保存在 `l1/`。

`python -m runtime prepare --teacher-collection --stage <stage>` 生成剩余 48 场计划。沿用 `preflight` 和 `run` 入口。使用 `score --teacher-collection --stage <stage> --attempt <new-attempt> --carried-attempt <pilot-attempt>` 对两个来源合并重评。原始运行文件写在仓库旁的 `MultiUAV-Plat-runs/teacher-collection-v2/`。
