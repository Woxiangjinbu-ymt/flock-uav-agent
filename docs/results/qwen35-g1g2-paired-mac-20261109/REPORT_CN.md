# 29 题开关思考计分的 Mac 模拟验收

10 组预设用例全部得到预期判定。每组使用相同的 5 个父场景、29 道开发题、116 个计划槽。Qwen3.5-2B 的开关组决定 G1/G2，Qwen3-4B 结果按臂单列。

| 模拟用例 | G1 | G2 | 净改善 | 改善父场景 | 缺对 |
| --- | --- | --- | ---: | ---: | ---: |
| [跨场景净增 3 题](cases/pass/REPORT_CN.md) | 通过 | 通过 | +3 | 2 | 0 |
| [同样净增 3 题，但全部来自一个父场景](cases/single_parent/REPORT_CN.md) | 通过 | 未通过 | +3 | 1 | 0 |
| [主模型开臂只有 2 题成功，参照模型仍有净改善](cases/two_successes/REPORT_CN.md) | 未通过 | 未通过 | -4 | 0 | 0 |
| [仅开臂触及回合墙钟](cases/deadline/REPORT_CN.md) | 通过 | 待判 | +3 | 2 | 0 |
| [仅开臂触及请求时限](cases/request_timeout/REPORT_CN.md) | 通过 | 待判 | +3 | 2 | 0 |
| [仅开臂超过上下文长度](cases/context_length/REPORT_CN.md) | 通过 | 待判 | +3 | 2 | 0 |
| [同题开臂墙钟截止、关臂请求超时，合并数相同](cases/balanced_union/REPORT_CN.md) | 通过 | 待判 | +3 | 2 | 0 |
| [关臂一题服务出错、一题未启动](cases/missing/REPORT_CN.md) | 通过 | 待判 | +3 | 2 | 2 |
| [开臂分别耗尽累计 token、请求次数、单轮生成额度](cases/budget/REPORT_CN.md) | 通过 | 通过 | +3 | 2 | 0 |
| [全部 116 槽未启动](cases/unstarted/REPORT_CN.md) | 待判 | 待判 | +0 | 0 | 29 |

各用例目录包含可独立回放的 `results.json` 和逐槽、逐场景的 `gates.json`。净改善只统计可评分配对；全部未启动时为 0 个观测到的净改善，配对数为 0。模拟通过仅验收计分程序。

## 在 Mac 上复算

在项目根目录复用旧交付中已物化的 dev 初态，生成新版计划，再独立回放第一组模拟结果。

```sh
export PYTHONPATH="$PWD/scripts:$PWD"
py=agent4drone/.venv/bin/python
study=outputs/flockcue-g1g2-mac-acceptance-20261108/dev-run/study
work=$(mktemp -d)
"$py" -m thinking_events prepare-dev --study "$study" --output "$work/run"
"$py" -m thinking_events simulate-paired --run "$work/run" --output "$work/sim"
"$py" -m thinking_events report --run "$work/run" --results "$work/sim/cases/pass/results.json" --output "$work/check"
cmp "$work/sim/cases/pass/gates.json" "$work/check/gates.json"
```

[机器可读摘要](summary.json)
