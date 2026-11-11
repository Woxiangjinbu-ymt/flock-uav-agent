"""Independently replay sealed 2B end states, then apply the frozen G1/G2 rules."""
from __future__ import annotations


import argparse
from collections import Counter
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

_workspace = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts").is_dir())
if __name__ == "__main__" and not __package__:
    sys.path[:0] = [str(_workspace / "scripts"), str(_workspace / "compat/scripts"), str(_workspace)]



def check_worlds(run, targets, output):
    """Run inside the frozen simulator in a separate, offline CPU process."""
    from harness_episode_local import block_network, setup_paths
    from harness_full100_world import FullWorld
    from thinking_events.worker_child import _frozen_modules

    attempts = block_network()
    for name in ("models", "controllers"):
        sys.modules[name + ".__init__"] = importlib.import_module(name)
    setup_paths(Path.cwd())
    world = FullWorld()
    answers = {}
    for row in targets:
        folder = run / "trials" / row["slot_id"]
        blob = (folder / "private/score-world.pkl").read_bytes()
        world.restore(blob, row["snapshot_sha256"])
        result = world.evaluate(row["task_id"], blob)
        archived = json.loads((folder / "evaluation/result.json").read_bytes())["checker"]
        if result != archived:
            raise ValueError(f"Replayed checker differs from its sealed output: {row['slot_id']}")
        answers[row["slot_id"]] = {key: result[key] for key in ("passed", "checks_passed", "checks_expected")}
    required = {"api.server", "models.drone", "controllers.drone_controller"}
    if (attempts or "uav_agent" in sys.modules
            or not required.issubset(_frozen_modules(run / "source"))):
        raise ValueError("Unexpected module or network use in the offline scorer")
    output.write_text(json.dumps(answers), encoding="utf-8")


def cpu_replay(run, targets, world_python):
    # The existing Agent environment supplies the transport dependencies. The
    # matching server environment supplies pygame. All project code is frozen.
    root = world_python.absolute().parents[3]
    local = [run / "source" / name for name in ("scripts", "agent4drone", "server")]
    deps = sorted((root / "server/.venv/lib").glob("python*/site-packages"))
    env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "LC_ALL") if key in os.environ}
    env.update(PYTHONPATH=os.pathsep.join(map(str, local + deps)), PYTHONHASHSEED="0",
               PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PYGAME_HIDE_SUPPORT_PROMPT="1")
    with TemporaryDirectory(prefix="flockcue-checker-") as tmp:
        output = Path(tmp) / "replayed.json"
        cmd = [str(world_python.absolute()), "-P", str(Path(__file__).resolve()),
               "worlds", str(run), str(output)]
        process = subprocess.run(cmd, cwd=tmp, env=env, input=json.dumps(targets),
                                 capture_output=True, text=True)
        if process.returncode:
            raise RuntimeError(process.stderr[-2500:] or process.stdout[-2500:])
        return json.loads(output.read_bytes())


def recount(run, g3, world_python, *, baseline=False):
    from thinking_events.campaign import verify_run
    from thinking_events.campaign_storage import digest, read_jsonl
    from thinking_events.paired_actual_report import _cost, _terminal
    from thinking_events.paired_batch import verify_execution
    from thinking_events.paired_scores import PRIMARY, score_pairs

    run = run.resolve()
    campaign_sha = digest((run / "campaign.json").read_bytes())
    if campaign_sha != "ba8b8e2d10bdc54af7c5ff97c6919f5b2727a50de7e68db80efb9c7f67f5d2d6":
        raise ValueError("Unexpected 29-question campaign")
    campaign = verify_run(run)
    execution = verify_execution(run, campaign, g3)
    if execution["backend"] != "transformers" or execution["scope"] != "formal":
        raise ValueError("Expected independently scored real Transformers weights")
    plan = read_jsonl(run / "plan-slots.jsonl")
    journal = read_jsonl(run / "slots.jsonl")
    main = [r for r in journal if r["model"] == PRIMARY]
    original_ledger = "486eedc5d13750f151902289c19dc781d31f8dc8916101d268bb967068a31cb0"
    if len(main) != 58 or len({r["task_id"] for r in main}) != 29:
        raise ValueError("The full set of 58 primary slots is required")
    timing = None
    if baseline:
        if digest((run / "slots.jsonl").read_bytes()) != original_ledger:
            raise ValueError("Expected the archived November 10 baseline ledger")
    else:
        protocol = json.loads((run / "retest-protocol.json").read_bytes())
        if (any(r["status"] != "unstarted" for r in journal if r["model"] != PRIMARY)
                or protocol["model"] != PRIMARY or protocol["original_slots_sha256"] != original_ledger
                or protocol["original_campaign_sha256"] != campaign_sha
                or protocol["original_plan_sha256"] != campaign["plan_slots_sha256"]
                or protocol["eligible_slot_ids"] != [r["slot_id"] for r in plan if r["model"] == PRIMARY]
                or protocol["reference_pairs_matched"] != 50
                or protocol["example_descriptions"]["B"] != "d5b1e501ed100173ea316f45f819e757deefcec343af9302382ec60770ec09a1"):
            raise ValueError("Retest population or tool description differs from the registered design")
        receipt = json.loads((run / "retest-complete.json").read_bytes())
        ledger = _cost(run, "formal")
        if (receipt["targeted"] != Counter(r["status"] for r in main) or receipt["other_model_excluded"] != 58
                or receipt["t37_admission_sha256"] != execution["admission_sha256"]
                or ledger["outstanding_reservation"] or ledger["billing_mode"] != "prepaid_week"
                or receipt["lease"]["billed_seconds"] != ledger["gpu_billed_seconds"]):
            raise ValueError("Retest settlement disagrees with terminal receipts")
        timing = {"start_unix": json.loads((run / "retest-lease.json").read_bytes())["stage_started_unix"],
                  "billed_seconds": ledger["gpu_billed_seconds"], "incremental_cny": ledger["settled_cny"]}
    if any(r["status"] in {"unstarted", "started"} for r in main):
        raise ValueError("The 58-slot batch has not finished")
    evidence, targets = {}, []
    for row in main:
        sid = row["slot_id"]
        proof = _terminal(run, row, execution)
        evidence[sid] = proof
        if row["status"] == "scored":
            snapshot = json.loads((run / "trials" / sid / "worker-result.json").read_bytes())["snapshot"]
            targets.append({"slot_id": sid, "task_id": row["task_id"], "snapshot_sha256": snapshot["sha256"]})
    replay = cpu_replay(run, targets, world_python)
    for sid, score in replay.items():
        evidence[sid]["cpu_replay"] = score
    gates = score_pairs(plan, main)
    return {
        "schema": "flockcue-sense-retest-2b-report-v1",
        "condition": "old" if baseline else "new",
        "model": PRIMARY, "revision": campaign["model_revisions"][PRIMARY],
        "campaign_sha256": campaign_sha, "plan_sha256": campaign["plan_slots_sha256"],
        "backend": execution["backend"], "t37_sha256": execution["admission_sha256"],
        "retest_timing": timing,
        "G1": gates["G1"], "G2": gates["G2"], "G3": execution["g3"],
        "results": gates["by_model"][PRIMARY], "slot_evidence": evidence,
        "replayed_snapshots": len(replay),
        "replayed_leaf_checks": sum(s["checks_expected"] for s in replay.values()),
    }


def publish(old, new, directory, run_dir):
    """Publish a comparison of like-for-like old/new arms and the within-retest gates."""
    if ((old["condition"], new["condition"]) != ("old", "new")
            or any(old[k] != new[k] for k in ("campaign_sha256", "plan_sha256", "revision", "t37_sha256"))):
        raise ValueError("Reports must be from the two conditions with the same plan and model")
    names = {"pass": "通过", "fail": "未通过", "indeterminate": "待判"}
    results = new["results"]
    g1, g2, g3 = (new[k] for k in ("G1", "G2", "G3"))
    old_pairs = {p["task_id"]: p for p in old["results"]["pairs"]}
    task_rows = []
    changed = {arm: Counter() for arm in ("on", "off")}
    raw = os.path.relpath(run_dir, directory).replace(os.sep, "/")
    for pair in results["pairs"]:
        previous = old_pairs[pair["task_id"]]
        cells = [f"`{pair['scene_id']}`", f"`{pair['task_id']}`"]
        for arm in ("on", "off"):
            before = previous[arm]["checker_passed"]
            after = pair[arm]["checker_passed"]
            changed[arm][before, after] += 1
            label = {True: "成功", False: "失败", None: "缺测"}
            score = f"{raw}/trials/{pair[arm + '_slot']}/evaluation/result.json"
            cells.extend((label[before], f"[{label[after]}]({score})" if after is not None else label[after]))
        task_rows.append("| " + " | ".join(cells) + " |")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "PAIRS_CN.md").write_text("\n".join([
        "# 29 题独立评分对照", "", "新说明两列链接到各自独立评分进程的原始结果。[旧说明原始 116 回合](../flockcue-a100-g1g2-20261110/README_CN.md)", "",
        "| 父场景 | 开发题 | 旧说明·开 | 新说明·开 | 旧说明·关 | 新说明·关 |",
        "| --- | --- | --- | --- | --- | --- |", *task_rows, ""]), encoding="utf-8")
    headline = (f"原始 Qwen3.5-2B 使用新感知工具说明，29 题开思考成功 **{g1['success']}/29**，"
                f"关思考成功 **{results['arms']['off']['success']}/29**。"
                f"本次 G1 **{names[g1['status']]}**，G2 **{names[g2['status']]}**。")
    cards = new["retest_timing"]
    seconds = cards["billed_seconds"]
    def latency(metric):
        if not metric["n"]:
            return "缺测（0）"
        return f"{metric['p50']:.2f} / {metric['p95']:.2f}（{metric['n']}）"

    measurements = []
    for report in (old, new):
        for arm in ("on", "off"):
            obs = report["results"]["arms"][arm]
            usage = obs["usage"]
            token = [f"{usage[k]['known_sum']:,}（{usage[k]['measured_slots']}/29）"
                     for k in ("reasoning_tokens", "total_tokens")]
            req, eps = usage["request_seconds"], usage["episode_seconds"]
            cells = ["新说明" if report is new else "旧说明", "开" if arm == "on" else "关",
                     f"{obs['success']} / {obs['scored']} / 29", *token, latency(req), latency(eps)]
            measurements.append("| " + " | ".join(cells) + " |")
    scenes = []
    for row in results["by_scene"]:
        count = row["four_cells"]
        scenes.append(f"| `{row['scene_id']}` | {count['both_success']} | {count['only_on']} | "
                      f"{count['only_off']} | {count['both_failure']} | {row['missing_pairs']} | {row['net_gain']:+d} |")
    stop_table = []
    for reason in sorted(set(results["arms"]["on"]["stop_counts"]) | set(results["arms"]["off"]["stop_counts"])):
        counts = [results["arms"][arm]["stop_counts"].get(reason, 0) for arm in ("on", "off")]
        if any(counts) and reason != "any":
            stop_table.append(f"| `{reason}` | {counts[0]} | {counts[1]} |")
    if not stop_table:
        stop_table.append("| 无记录停因 | 0 | 0 |")
    guard = ("开臂超限偏向涉及 " + "、".join(g2["open_arm_excess"]) + "。"
             if g2["open_arm_excess"] else "未触发预登记的开臂超时或上下文超长偏向条件。")
    a, b = changed["on"], changed["off"]
    audit = json.loads((directory / "intervention-audit.json").read_bytes())
    new_errors = audit["terminal_tool_argument_errors"]
    n_errors = sum(new_errors.values())
    if n_errors != sum(arm["stop_counts"].get("tool_arguments", 0) for arm in results["arms"].values()):
        raise ValueError("Tool attribution count differs from the sealed terminal stop counts")
    original_errors = sum(arm["stop_counts"].get("tool_arguments", 0) for arm in old["results"]["arms"].values())
    sense_errors = new_errors.get("sense_nearby_entities", 0)
    collection = ("三道研究门槛均已通过。教师轨迹试采可在采集入口验收和费用上限确认后启动。"
                  if all(g["status"] == "pass" for g in (g1, g2, g3))
                  else "正式教师采数的三道门槛尚未全部通过。依当前协议，40 题试采的启动日期待定。")
    lines = ["# Qwen3.5-2B 新感知工具说明 · 29 题整题复测", "", headline, "",
             f"单张 A100 40 GB，BF16，batch=1。共 {sum(x['scored'] for x in results['arms'].values())}/58 个 2B 回合获独立评分。"
             f"本次装载、执行、评分与收尾累计 {seconds // 60} 分 {seconds % 60:02d} 秒（{seconds / 3600:.4f} 卡时，向上取整至秒），"
             f"包周内新增费用 {cards['incremental_cny']} 元。[已登记复测口径](PROTOCOL_CN.md) · [逐回合计时与用量](gates.json)", "",
             "## G1、G2 的判定依据", "",
             f"- G1 要求开思考至少成功 3 题，并有至少 3 题可归因的执行失败。实际成功 {g1['success']} 题、"
             f"可归因失败 {g1['attributable_failures']} 题、未评分 {g1['unscored']} 题。",
             f"- G2 要求开、关思考的配对净增至少 3 题，且至少两个父场景净改善。实际净增 {g2['net_gain']:+d} 题，"
             f"{g2['improved_scenes']} 个父场景净改善，缺对 {g2['missing_pairs']}。{guard}",
             f"- G3 沿用已核验的事件分析，状态为{names[g3['status']]}。[历史 G3 复算](../thinking-events-g3-20261108/REPORT_CN.md)", "",
             collection + "[40 题方案及预算](../../specs/04-deepseek-pilot40/plan.md)", "",
             "| 父场景 | 均成功 | 仅开成功 | 仅关成功 | 均失败 | 缺对 | 净增 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |", *scenes, "",
             f"按同题、同模式比较旧新说明，开思考有 {a[False, True]} 题由失败转成功、{a[True, False]} 题由成功转失败，"
             f"关思考分别为 {b[False, True]} 题和 {b[True, False]} 题。[29 题逐项对照](PAIRS_CN.md) · [旧说明基线](../flockcue-a100-g1g2-20261110/REPORT_CN.md)", "",
             "## 用量、时延与停因", "",
             "| 说明 | 思考 | 成功 / 评分 / 计划 | 已知思考 token（完整回合） | 已知总 token（完整回合） | 请求 P50 / P95 秒（笔数） | 回合 P50 / P95 秒（个数） |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: |", *measurements, "",
             "| 新说明下记录的停因 | 开思考 / 29 | 关思考 / 29 |", "| --- | ---: | ---: |", *stop_table, "",
             f"工具参数错误导致的回合终止由旧说明的 {original_errors}/58 变为 {n_errors}/58。其中感知工具由 46 次降至 {sense_errors} 次，"
             f"其他工具由 {original_errors - 46} 次增至 {n_errors - sense_errors} 次。[原感知工具审阅](../flockcue-a100-interface-20261111/README_CN.md) · "
             "[本次逐请求与末轮错误核验](intervention-audit.json)", "",
             f"两端从 {new['replayed_snapshots']} 份终态快照用冻结 checker 重新执行 {new['replayed_leaf_checks']} 项叶检查，逐回合核对完整检查树。"
             "任务成功按检查树根节点结果计。表中停因独立记录，因此达到预算上限的回合仍可能已满足检查条件。"
             f"两臂使用同一批已暴露开发题与一组冻结种子。{audit['inputs_verified']}/{audit['requests']} 笔请求实际输入 ID 完整复现。"
             f"与旧批的首轮 SDK 请求逐字段比较，{audit['initial_sdk_matching_except_command_uuid']}/58 个回合仅有新分配的公开 command_id 不同，"
             "后续输入历史由各臂的实际动作和回执形成。整题差异描述这些运行条件下的配对结果。", "",
             "[CPU 复算入口](../../../scripts/report_sense_retest.py) · [逐请求干预核验](intervention-audit.json)", ""]
    (directory / "REPORT_CN.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "publish":
        parser = argparse.ArgumentParser(description="Compare CPU replay reports and publish the Chinese tables")
        for name in ("old", "new", "server", "directory", "run"):
            parser.add_argument("--" + name, type=Path, required=True)
        args = parser.parse_args(sys.argv[2:])
        if args.server.read_bytes() != args.new.read_bytes():
            raise ValueError("Mac and server reports differ")
        if args.new.resolve() != (args.directory / "gates.json").resolve():
            raise ValueError("Publish gates.json beside the markdown tables")
        publish(json.loads(args.old.read_bytes()), json.loads(args.new.read_bytes()), args.directory, args.run)
        return
    if len(sys.argv) == 4 and sys.argv[1] == "worlds":
        check_worlds(Path(sys.argv[2]), json.load(sys.stdin), Path(sys.argv[3]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--g3", type=Path, required=True, help="frozen G3 certificate g3.json")
    parser.add_argument("--world-python", type=Path, required=True, help="paired project's agent4drone/.venv/bin/python")
    parser.add_argument("--baseline", action="store_true", help="replay the old 2B subset of the completed four-arm campaign")
    parser.add_argument("--output", type=Path, required=True, help="destination JSON, outside run")
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.run.resolve()):
        parser.error("Write independent results outside the archived campaign")
    report = recount(args.run, args.g3, args.world_python, baseline=args.baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n",
                           encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("condition", "replayed_snapshots", "replayed_leaf_checks", "G1", "G2")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
