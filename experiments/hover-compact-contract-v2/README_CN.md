# v5 本地候选与固定20槽

`profile.json` 为显式 schema5 / general-agent-v5-local，使用 compact_tools_v1、search_confirm_v9、hover_completion_v2、json_object_or_string_v2；在线授权0。v4-profile及legacy-profile只用于预定对照，不切换应用默认。

plan.json固定20个脚本槽：前轮16种兼容/恢复/动作/时长语义，加上v5/v4只读并行对照和两种动作重叠负例。脚本使用真实公开工具回执解析主体；parallel_tools只在本地SDK夹具使用，一次性barrier让两个handler生命周期确实交叠，不能当作自然模型行为。

运行入口为 scripts/validate_hover_compact_contract.py，先prepare冻结所有输入/源码，再run；不覆盖旧尝试。完整协议与已完成原件见[报告](../../docs/results/hover-compact-contract-20261103/REPORT_CN.md)。本轮没有Teacher、训练或第五项。
