# FlockCue

FlockCue 是多无人机任务的执行反馈 Agent Harness。教师和学生在同一仿真环境中接收任务、调用工具、读取执行回执，最终由独立检查器给出整题通过率。

| 目录 | 负责的工作 |
| --- | --- |
| [runtime/](runtime/) | 启动服务、按原顺序执行场景、冻结终态、重放评分 |
| [simulation/](simulation/) | 多无人机仿真、角色权限和原题独立检查器 |
| [flockcue/](flockcue/) | 正式运行的策略服务、工具、执行反馈、恢复和逐轮记录 |
| [datasets/](datasets/) | 原始基准、冻结的 600 题、教师数据、正式评测结果 |
| [posttraining/](posttraining/) | 从实发请求和回执重建教师轨迹，复核 2B 学生的 SFT 输入与监督，衔接后续 RL |

## 运行与复核

使用 Python 3.11.16。调度器、仿真和策略服务分别使用自己的依赖环境。下列命令从仓库根目录执行，使用本地数据。首次安装见[运行环境](runtime/README.md)和[学生分词环境](posttraining/sft/README.md)。

```sh
flockcue/.venv/bin/python -m flockcue check
runtime/.venv/bin/python -m runtime prepare --stage ../flockcue-runs/benchmark-600
runtime/.venv/bin/python -m runtime verify --stage ../flockcue-runs/benchmark-600
python3 datasets/teacher/2026-11-11-fold-a/verify.py
posttraining/.venv/bin/python -m posttraining.sft
```

正式评测沿用 30 场 × 20 题的原顺序、8 个 worker 和 500 秒动作窗口。FlockCue + DeepSeek 首遍通过 544/600。同日同指纹的 200 题配对中，FlockCue 通过 182 题，原版通过 168 题。[设置、完整分母及逐题对照](datasets/results/README.md)

项目同时保存 1,500 道原题及十月原版首遍记录、600 道核心基准及十一月 FlockCue 首遍记录。后训练以 240 题为首版训练来源，60 题用于内部验证，B 折 300 题固定为独立测试集。若学生能力仍不足，可从核心基准外 900 题中选取完整场景补充训练。已筛出 110 题的 386 个可监督决策轮。[数据与划分](datasets/README.md) · [正式运行记录](datasets/results/README.md) · [后训练进度](posttraining/README.md)

## 来源与归档

仿真平台、原始题库、独立检查器及策略服务复用的工具组件来自 [MultiUAV-Plat](https://github.com/zhangsheng93/MultiUAV-Plat)。FlockCue 的执行反馈、预定义执行子程序、真实轨迹审阅与学生输入适配属于本研究的实现。[许可证](LICENSE)

完整旧工作区保存在同级目录 `../MultiUAV-Plat-archive-20261111/`。其中包含原版 Agent4Drone、历史实验和报告、可视化工具、旧执行环境，以及未纳入 Git 的原始运行记录。提交 `744388f4` 对应归档时的受版本管理源码和证据；从 Git 检出历史版本不会自动取得本机的大型原始归档。部分旧报告和虚拟环境记录了原路径，在新位置执行旧代码时按归档中的锁文件重建环境。
