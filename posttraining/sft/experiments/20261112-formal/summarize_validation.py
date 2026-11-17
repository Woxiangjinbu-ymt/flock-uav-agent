"""Select from the first SFT and all five additional epochs on the frozen A60 tasks."""
import argparse
from collections import Counter, defaultdict
import csv
from hashlib import sha256
import json
from pathlib import Path
import math
import os
from tempfile import NamedTemporaryFile

FIRST_PAIRS_SHA = "c3dc528c4296539b18bfdac4c631fc23a6e803ea9ab263c7b4fa27fa555d3746"
FIRST_ADAPTER_SHA = "8735ff75258991c9781625f19a9802d93c4c47bc16065e6ab65bdcca5913007e"
SCENES = ("72ccacd0", "0293952c", "cc14d231")
GROUPS = {"original_training_probes": 12, "reviewed_closings": 9}


def load(path):
    return json.loads(path.read_bytes())


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def rows_at(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def task_order(rows):
    return [(r["session_id"], int(r["ordinal"]), r["task_id"], int(r["checks"])) for r in rows]


def count_arm(rows, prefix, name, epoch, weights):
    formal, checker, leaves = 0, 0, 0
    stopped = Counter()
    by_scene = {}
    stop_checks = defaultdict(int)
    for r in rows:
        field = lambda k: r[f"{prefix}_{k}"]
        ok = {"True": True, "False": False}[field("pass")]
        sim = {"True": True, "False": False}[field("checker")]
        passed = int(field("passed_checks"))
        checks = int(r["checks"])
        status = field("terminal_http_status")
        if not (0 <= passed <= checks) or (ok != (sim and not status)):
            raise ValueError(f"Inconsistent formal/checker/terminal result for {r['task_id']}")
        reason = field("stop_reason") if prefix == "candidate" else field("failure_reason") or "completed"
        stopped[reason] += 1
        if field("failure_reason") == "token_budget":
            stop_checks["already_passed" if sim else "incomplete"] += 1
        scene = by_scene.setdefault(r["session_id"], {"family": r["family"], "tasks": 0, "formal_passes": 0,
                                                      "original_checker_passes": 0, "passed_leaf_checks": 0, "leaf_checks": 0})
        scene["tasks"] += 1
        scene["formal_passes"] += ok
        scene["original_checker_passes"] += sim
        scene["passed_leaf_checks"] += passed
        scene["leaf_checks"] += checks
        formal += ok
        checker += sim
        leaves += passed
    return {"name": name, "extra_epochs": epoch, "adapter_sha256": weights, "formal_passes": formal,
            "original_checker_passes": checker, "passed_leaf_checks": leaves, "stop_counts": dict(stopped),
            "token_budget": sum(stop_checks.values()), "token_budget_with_checker_pass": stop_checks["already_passed"],
            "token_budget_with_checker_fail": stop_checks["incomplete"], "by_scene": by_scene}


def attach_probes(path, entry, signatures):
    probe = load(path)
    if probe["adapter"]["weights_sha256"] != entry["adapter_sha256"]:
        raise ValueError(f"Probe adapter differs from {entry['name']} in {path}")
    losses = {}
    for group, expected_count in GROUPS.items():
        values = probe["groups"][group]
        rows = values["rows"]
        signature = sorted((r["sha256"], r["supervised_tokens"]) for r in rows)
        if len(rows) != expected_count or len({r["sha256"] for r in rows}) != expected_count:
            raise ValueError(f"Unexpected probe rows in {path}")
        if signatures.setdefault(group, signature) != signature:
            raise ValueError(f"The training probes changed in {path}")
        n = sum(r["supervised_tokens"] for r in rows)
        loss = sum(r["loss"] * r["supervised_tokens"] for r in rows) / n
        if n != values["supervised_tokens"] or not math.isfinite(loss) or not math.isclose(loss, values["loss"]):
            raise ValueError(f"Invalid weighted loss in {path}")
        losses[group] = {"supervised_tokens": n, "rows": len(rows), "loss": loss}
    entry["training_probes"] = losses
    entry["probe_sha256"] = digest(path)


def build(run, stages, archived, probe_files):
    if digest(archived) != FIRST_PAIRS_SHA:
        raise ValueError("The archived A60 pairs differ from the frozen comparison")
    history = rows_at(archived)
    expected_order = task_order(history)
    if len(set(k[2] for k in expected_order)) != 60 or sum(k[-1] for k in expected_order) != 485:
        raise ValueError("Historical task or leaf denominator differs")
    baseline = count_arm(history, "base", "基座", None, None)
    entries = [count_arm(history, "sft", "首轮 SFT", 0, FIRST_ADAPTER_SHA)]
    for epoch, stage in enumerate(stages, 1):
        summary = load(stage / "summary.json")
        pairs = rows_at(stage / "pairs.csv")
        if task_order(pairs) != expected_order:
            raise ValueError(f"Task identity/order differs at epoch {epoch}")
        item = count_arm(pairs, "candidate", f"追加 {epoch} epoch", epoch, summary["adapter_sha256"])
        for key in ("formal_passes", "original_checker_passes", "passed_leaf_checks", "by_scene"):
            if item[key] != summary[key]:
                raise ValueError(f"A60 summary and rows disagree on {key} at epoch {epoch}")
        if (summary["tasks"], summary["leaf_checks_per_arm"], summary["stop_reasons"]) != (60, 485, item["stop_counts"]):
            raise ValueError(f"A60 denominator or stops differ at epoch {epoch}")
        for path in (run / "output" / f"epoch-{epoch}" / "adapter_config.json", stage / "adapter/adapter_config.json"):
            if path.is_file() and load(path)["weights_sha256"] != item["adapter_sha256"]:
                raise ValueError(f"Validation uses another checkpoint at {path}")
        item["pairs_sha256"] = digest(stage / "pairs.csv")
        item["stage_summary_sha256"] = digest(stage / "summary.json")
        item["paired_comparison"] = summary["paired_comparison"]
        entries.append(item)
    signatures = {}
    for path, entry in zip(probe_files, entries, strict=True):
        if path is not None:
            attach_probes(path, entry, signatures)
    best = max(entries, key=lambda e: e["formal_passes"])
    tie = [e["extra_epochs"] for e in entries if e["formal_passes"] == best["formal_passes"]]
    return {"tasks": 60, "leaf_checks": 485, "historical_pairs_sha256": FIRST_PAIRS_SHA,
            "protocol_sha256": digest(run / "protocol.json"), "base": baseline, "checkpoints": entries,
            "selected": {"name": best["name"], "extra_epochs": best["extra_epochs"], "adapter_sha256": best["adapter_sha256"],
                         "formal_passes": best["formal_passes"], "tied_extra_epochs": tie,
                         "rule": "highest formal pass count; earliest checkpoint on ties, archived first SFT eligible"}}


def report(result):
    entries = result["checkpoints"]
    best = entries[result["selected"]["extra_epochs"]]
    tied = result["selected"]["tied_extra_epochs"]
    table = [f"| {e['name']} | {e['formal_passes']}/60 | {e['original_checker_passes']}/60 | "
             f"{e['passed_leaf_checks']}/485 | {e['token_budget']} | {e['token_budget_with_checker_pass']} |"
             for e in [result["base"], *entries]]
    lines = ["# 优化版 SFT 的 A 折内部验证", "",
             f"按正式整题通过数选择 **{best['name']}**，通过 **{best['formal_passes']}/60"
             f"（{100 * best['formal_passes']/60:.1f}%）**。六个候选中有 {len(tied)} 个达到这一分数。"
             + ("已按预登记规则选择最早的权重。" if len(tied) > 1 else ""),
             "", f"选中适配器 SHA-256 为 `{best['adapter_sha256']}`。", "",
             "| 权重 | 正式整题通过 | 仿真终态通过 | 叶检查通过 | 累计预算终止题 | 其中终态已过 |",
             "| --- | ---: | ---: | ---: | ---: | ---: |", *table, "",
             "正式通过要求原仿真器整题检查达标，终止性模型 HTTP 错误计败。基座为历史对照；入选范围包括首轮 SFT 和其后五份权重。", "",
             "## 选中权重的分场结果", "", "| 场景 | 正式通过 | 仿真终态通过 | 叶检查通过 |",
             "| --- | ---: | ---: | ---: |"]
    for s in SCENES:
        d = best["by_scene"][s]
        lines.append(f"| {d['family']}（{s}） | {d['formal_passes']}/{d['tasks']} | "
                     f"{d['original_checker_passes']}/{d['tasks']} | {d['passed_leaf_checks']}/{d['leaf_checks']} |")
    present = [e for e in entries if "training_probes" in e]
    if present:
        lines.extend(["", "## 固定训练样本损失", "", "以下为相同教师回复上按监督 token 加权的交叉熵，仅作拟合诊断。", "",
                      "| 权重 | 原训练探针 12 条 | 复审收尾 9 条 |", "| --- | ---: | ---: |"])
        lines.extend(f"| {e['name']} | {e['training_probes']['original_training_probes']['loss']:.4f} | "
                     f"{e['training_probes']['reviewed_closings']['loss']:.4f} |" for e in present)
    diff = best["formal_passes"] - entries[0]["formal_passes"]
    diagnostic = (f"五个追加检查点在这 60 题上均未超过首轮的正式通过数（{entries[0]['formal_passes']}/60）。"
                  if diff == 0 else f"选中权重较首轮多通过 {diff} 题（{100*diff/60:.1f} 个百分点）。")
    lines.extend(["", "## 是否补充轨迹", "", diagnostic, ""])
    if best["token_budget_with_checker_pass"]:
        lines.append(f"- {best['token_budget_with_checker_pass']} 题耗尽额度时仿真终态已经达标。逐轮核对当时可见证据和剩余额度；"
                     "若证据已齐且有生成机会，定向重采简短收尾；若最后一次请求连输入都容不下，采集更短的动作与核验路径。")
    remaining = 60 - best["original_checker_passes"]
    if remaining:
        lines.append(f"- {remaining} 题冻结时仍未满足全部检查，其中 {best['token_budget_with_checker_fail']} 题以累计预算终止。"
                     "先按失败回执与缺失动作归类，再从训练侧补实体 ID 发现、参数修正、关键前置状态核验或多机协同执行示范。"
                     "缺少必要证据时应如实说明能验证的范围。")
    if not remaining and not best["token_budget_with_checker_pass"]:
        lines.append("- 这组三场的终态全部达标，且无终态达标后因累计预算失败的题。此次结果没有显示需针对这两类缺口扩充数据。")
    lines.extend(["", "## 解释范围", "",
                  "三场各含 20 道连续任务，共 485 个叶检查。同一任务族只有一个验证场景，场内题目共享历史状态。"
                  "基座与首轮分数取自历史现场记录，各次动态天气可能有差异。A 折 60 题此前参与失败诊断，本轮又用于选模。"
                  "需要独立检验时使用冻结的 B 折 300 题。", "",
                  "本轮从首轮权重追加训练，同时调整示范配比、学习率日程及梯度累积。"
                  "同一混合训练内的后续检查点可比较继续训练的结果，单纯增加 epoch 的独立贡献需另设固定数据与优化器的对照。"
                  "可从 A 折训练场景采集补充轨迹，数量仍不足时，从基准外已登记的完整训练场景补充。"
                  "按完整分母复评，并保留出现退步的场景和任务。", ""])
    return "\n".join(lines)


def write_atomic(path, content):
    with NamedTemporaryFile("w", dir=path.parent, prefix=path.name, encoding="utf-8", delete=False) as stream:
        stream.write(content)
        temp = Path(stream.name)
    os.replace(temp, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--stages", type=Path, nargs=5, help="Additional epochs 1 through 5, in order")
    parser.add_argument("--historical-pairs", type=Path)
    parser.add_argument("--probes", nargs=6, help="First SFT, then five additional epochs. Use - for a missing probe.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    stages = args.stages or [args.run / "validation" / f"epoch-{i}" for i in range(1, 6)]
    stages = [s / "stage" if (s / "stage").is_dir() else s for s in stages]
    archived = args.historical_pairs or stages[0] / "historical-pairs.csv"
    if args.probes:
        probe_files = [Path(s) if s != "-" else None for s in args.probes]
    else:
        probe_files = []
        for i in range(6):
            p = args.run / "probes" / f"epoch-{i}.json"
            probe_files.append(p if p.exists() else None)
    if load(args.run / "protocol.json")["training"]["extra_epochs"] != 5:
        raise ValueError("Expected the registered five-epoch experiment")
    result = build(args.run, stages, archived, probe_files)
    markdown = report(result)
    notes = markdown.split("## 是否补充轨迹\n\n", 1)[1]
    advice, limits = notes.split("\n\n## 解释范围\n\n")
    result["data_guidance_cn"] = [line.removeprefix("- ") for line in advice.splitlines() if line]
    result["limitations_cn"] = [line for line in limits.splitlines() if line]
    destination = args.output or args.run / "selection"
    destination.mkdir(parents=True, exist_ok=True)
    write_atomic(destination / "REPORT_CN.md", markdown)
    write_atomic(destination / "summary.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"{result['selected']['name']} {result['selected']['formal_passes']}/60; {destination}/REPORT_CN.md")


if __name__ == "__main__":
    main()
