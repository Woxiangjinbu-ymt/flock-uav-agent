"""Offline paired-score rehearsal on the frozen development roster. No inference backend."""
from collections import defaultdict
from contextlib import contextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from prepare_task_dataset import parse_json
from .campaign import ROOT, g3_evidence
from .campaign_storage import digest, json_bytes
from .paired_scores import CELLS, PRIMARY, REFERENCE, score_pairs

SCHEMA = "synthetic-paired-results-v1"
G3_FILE = Path("docs/results/thinking-events-g3-20261108/run-01/g3.json")
# Expected gates are defined independently of the scoring implementation.
CASES = {
    "pass": ("跨场景净增 3 题", "pass", "pass"),
    "single_parent": ("同样净增 3 题，但全部来自一个父场景", "pass", "fail"),
    "two_successes": ("主模型开臂只有 2 题成功，参照模型仍有净改善", "fail", "fail"),
    "deadline": ("仅开臂触及回合墙钟", "pass", "indeterminate"),
    "request_timeout": ("仅开臂触及请求时限", "pass", "indeterminate"),
    "context_length": ("仅开臂超过上下文长度", "pass", "indeterminate"),
    "balanced_union": ("同题开臂墙钟截止、关臂请求超时，合并数相同", "pass", "indeterminate"),
    "missing": ("关臂一题服务出错、一题未启动", "pass", "indeterminate"),
    "budget": ("开臂分别耗尽累计 token、请求次数、单轮生成额度", "pass", "pass"),
    "unstarted": ("全部 116 槽未启动", "indeterminate", "indeterminate"),
}
STATUS_CN = {"pass": "通过", "fail": "未通过", "indeterminate": "待判"}
STOP_CODES = ("episode_deadline", "request_timeout", "context_length", "any")
STOP_CN = ("500 秒回合截止", "120 秒请求超时", "上下文超长", "前三类合并去重")


@contextmanager
def new_output(path):
    """Refuse existing destinations and publish only fully generated files."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Provide a new output directory: {path}")
    with TemporaryDirectory(dir=path.parent) as tmp:
        stage = Path(tmp) / "bundle"
        stage.mkdir()
        yield stage
        # Acquire the destination exclusively, then replace the empty reservation
        # with the entire prepared directory in one same-filesystem rename.
        path.mkdir()
        try:
            stage.replace(path)
        except OSError:
            path.rmdir()
            raise


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


def _plan_digest(plan):
    return digest(b"".join(json_bytes(slot) for slot in plan))


def _task_positions(plan):
    scenes = defaultdict(set)
    for slot in plan:
        scenes[slot["scene_id"]].add(slot["task_id"])
    return {task: (si, ti) for si, scene in enumerate(sorted(scenes))
            for ti, task in enumerate(sorted(scenes[scene]))}


def _success(case, slot, scene, task):
    on = slot["thinking"]
    if slot["model"] == PRIMARY:
        if case == "single_parent":
            return task == 0 or (on and scene == 0 and 1 <= task <= 3)
        if case == "two_successes" and on:
            return task == 0 and scene < 2
    if task == 0:
        return True
    if on:
        return scene < 2 and task in (1, 2)
    return scene == 2 and task == 1


def _stop(case, on, scene, task):
    if (scene, task) == (3, 2):
        if case == "balanced_union":
            return "episode_deadline" if on else "request_timeout"
        if on and case in ("deadline", "request_timeout", "context_length"):
            return "episode_deadline" if case == "deadline" else case
    if case == "budget" and on and scene == 3:
        return {2: "token_budget", 3: "request_limit", 4: "generation_truncated"}.get(task)
    return None


def _usage(on, stop, *, completed_requests=2):
    n = {"token_budget": 5, "request_limit": 12}.get(stop, completed_requests)
    output = 2048 if stop == "generation_truncated" else 112
    prompt = (100000 // n - output) if stop == "token_budget" else 8000
    thought = 48 if on else 0
    latencies = [4.0 if on else 3.0] * n
    elapsed = sum(latencies) + 1
    if stop in ("request_timeout", "episode_deadline"):
        elapsed = 120 + sum(latencies) if stop == "request_timeout" else 500.0
    usage = {
        "prompt_tokens": prompt * n, "completion_tokens": output * n,
        "reasoning_tokens": thought * n, "total_tokens": (prompt + output) * n,
        "history_reasoning_tokens_by_request": [0] + [thought] * (n - 1),
        "elapsed_seconds": elapsed, "peak_prompt": prompt, "peak_context": prompt + output,
        "request_timings": [{"turn": i, "round_trip_seconds": t} for i, t in enumerate(latencies, 1)],
        "requests": n, "remaining_wall_seconds": 500.0 - elapsed, "stop_reason": stop,
        "remaining_requests": 12-n, "remaining_tokens": 100000 - n * (prompt + output),
        "peak_vram_bytes": None, "cost_cny": 0, "vram_measurement": "synthetic_no_gpu",
    }
    if stop in ("request_timeout", "episode_deadline"):
        # Two complete requests followed by one unanswered attempt, censored at cutoff.
        usage["requests"] += 1
        usage["remaining_requests"] -= 1
        usage["unknown_at_cutoff_turns"] = [n + 1]
        usage["unknown_usage_turns"] = [n + 1]
        usage["remaining_tokens"] = None
        for field in ("prompt_tokens", "reasoning_tokens", "completion_tokens", "total_tokens"):
            usage["known_" + field] = usage[field]
            usage[field] = None
    if stop == "context_length":
        # The third prompt is tokenized for admission but never sent to the model.
        usage["rejected_request"] = {"turn": n + 1, "prompt_tokens": 32000,
                                     "reserved_output_tokens": 2048, "context_limit": 32768}
    return usage


def _checker(slot, passed):
    return {"scope": "synthetic_checker", "slot_id": slot["slot_id"], "task_id": slot["task_id"],
            "checker_sha256": slot["checker_sha256"], "passed": passed}


def make_case(plan, case):
    if case not in CASES:
        raise ValueError(f"Unknown synthetic scenario: {case}")
    positions = _task_positions(plan)
    observations = []
    for slot in plan:
        scene, task = positions[slot["task_id"]]
        on = slot["thinking"]
        primary = slot["model"] == PRIMARY
        sid = slot["slot_id"]
        if case == "unstarted" or (case == "missing" and primary and not on and (scene, task) == (4, 2)):
            observations.append({"slot_id": sid, "status": "unstarted"})
            continue
        if case == "missing" and primary and not on and (scene, task) == (3, 2):
            result = {"trial_id": sid, "task_id": slot["task_id"], "status": "unscored",
                      "passed": None, "service_ok": False, "stop_reason": "infra_error",
                      "unscored_reason": "synthetic_scorer_unavailable"}
        else:
            passed = _success(case, slot, scene, task)
            stop = _stop(case, on, scene, task) if primary else None
            mock = _checker(slot, passed)
            evidence = None
            if not passed and stop is None:
                evidence = {"kind": "decision_error", "detail": "模拟轨迹遗漏题面要求的一项动作",
                            "reference": f"results.json#{sid}"}
            trace_action_order = primary and scene == task == 0
            result = {"trial_id": sid, "task_id": slot["task_id"], "status": "scored",
                      "synthetic_checker": mock, "passed": passed, "scorer_seal_sha256": digest(json_bytes(mock)),
                      "service_ok": True, "context_fits": stop != "context_length",
                      "stop_reason": stop, "failure_evidence": evidence,
                      "usage": _usage(on, stop, completed_requests=4 if trace_action_order else 2),
                      "agent_claimed_done": passed or evidence is not None, "harness_completed": passed}
            # A matched checker score can conceal an action-order difference.
            if trace_action_order:
                result["harness_completed"] = on
                result["action_diagnostic"] = {"source": "synthetic", "first_completion_turn": 4,
                                               "required_action_receipt_turn": 3 if on else None}
        observations.append({"slot_id": sid, "status": result["status"], "result": result})
    return {"schema": SCHEMA, "scenario": case, "plan_sha256": _plan_digest(plan),
            "observations": observations}


def _report(plan, bundle, g3):
    if (not isinstance(bundle, dict) or bundle.get("schema") != SCHEMA
            or bundle.get("plan_sha256") != _plan_digest(plan)):
        raise ValueError("Expected synthetic results bound to this frozen plan")
    if not isinstance(bundle.get("scenario"), str) or not isinstance(bundle.get("observations"), list):
        raise ValueError("Expected a scenario name and a list of observations")
    slots = {s["slot_id"]: s for s in plan}
    records, seen = [], set()
    for obs in bundle["observations"]:
        if not isinstance(obs, dict) or not isinstance(obs.get("slot_id"), str) or "status" not in obs:
            raise ValueError("Observation lacks a valid slot identity or status")
        sid = obs["slot_id"]
        if sid not in slots or sid in seen or set(obs) - {"slot_id", "status", "result"}:
            raise ValueError("Mixed, duplicate, or invalid synthetic slot")
        seen.add(sid)
        slot = slots[sid]
        if obs["status"] == "scored":
            result = obs.get("result")
            if not isinstance(result, dict) or type(result.get("passed")) is not bool:
                raise ValueError(f"Scored slot has no checker outcome: {sid}")
            mock = _checker(slot, result["passed"])
            if result.get("synthetic_checker") != mock or result.get("scorer_seal_sha256") != digest(json_bytes(mock)):
                raise ValueError(f"Synthetic checker seal differs: {sid}")
        records.append({**slot, **obs})
    gates = score_pairs(plan, records)
    diagnostics = []
    for row in records:
        result = row.get("result") or {}
        action = result.get("action_diagnostic")
        rejected = (result.get("usage") or {}).get("rejected_request")
        if result.get("stop_reason") == "context_length":
            if (not isinstance(rejected, dict) or rejected.get("context_limit") != 32768
                    or not all(type(rejected.get(k)) is int and rejected[k] > 0
                               for k in ("turn", "prompt_tokens", "reserved_output_tokens"))
                    or rejected["prompt_tokens"] + rejected["reserved_output_tokens"] <= rejected["context_limit"]):
                raise ValueError(f"Missing overlength admission evidence: {row['slot_id']}")
        if action is not None or rejected is not None:
            diagnostics.append({"slot_id": row["slot_id"], "task_id": row["task_id"], "model": row["model"],
                                "arm": row["arm"], "checker_passed": result.get("passed"),
                                "action_order": action, "rejected_request": rejected})
    all_pass = all(gates[gate]["status"] == "pass" for gate in ("G1", "G2"))
    return {"schema": "synthetic-paired-report-v1", "scenario": bundle["scenario"],
            "plan_sha256": bundle["plan_sha256"], "input_sha256": digest(json_bytes(bundle)),
            "G3": {**g3, "source": G3_FILE.as_posix(), "scope": "archived_development"},
            **gates, "diagnostics": diagnostics, "scope": "synthetic",
            "simulated_gate_decision": "all_pass" if all_pass else "defer",
            "formal_G1": "unmeasured", "formal_G2": "unmeasured",
            "eligible_for_collection": False, "formal_launches": 0, "rental_cny": 0}


def _g3():
    g3 = g3_evidence(ROOT / G3_FILE)
    if not g3["verified"] or g3["status"] != "pass":
        raise ValueError("The archived G3 certificate or its input hashes did not verify")
    return g3


def _fmt(x):
    return "—" if x is None else f"{x:g}"


def _cells(row):
    return " / ".join(str(row[k]) for k in CELLS)


def render_case(report):
    gate1, gate2 = (report[k]["status"] for k in ("G1", "G2"))
    lines = [f"# 模拟配对报告 · {report['scenario']}", "",
             f"主模型 G1 **{STATUS_CN[gate1]}**，G2 **{STATUS_CN[gate2]}**。所有任务结果及用量均为合成输入。"
             "原 29 个开发任务、父场景和槽位来自冻结清单。真实 G1/G2 待测，正式启动 0 槽，租卡费用 0 元。",
             "", "G3 使用已封存开发回合的真实证书，摘要为 `" + report["G3"]["sha256"] + "`。",
             "", "四格顺序为“两臂均成功 / 仅开成功 / 仅关成功 / 两臂均失败”。缺对单列，每臂计划分母均为 29。"]
    for model, result in report["by_model"].items():
        lines += ["", f"## {model}" + ("（主模型）" if model == PRIMARY else "（描述性参照）"),
                  "", f"四格 {_cells(result['four_cells'])}，缺 {result['missing_pairs']} 对，"
                  f"净改善 {result['net_gain']:+d} 题，{result['improved_scenes']} 个父场景净改善。",
                  "", "| 指标 | 思考开 | 思考关 |", "| --- | ---: | ---: |"]
        arms = [result["arms"][a] for a in ("on", "off")]
        for label, key in (("成功 / 29", "success"), ("已评分 / 29", "scored"),
                           ("有证据的任务失败 / 29", "attributable_failures"),
                           ("Agent 自报完成 / 29", "agent_claimed_done"), ("Harness 核验完成 / 29", "harness_completed")):
            lines.append(f"| {label} | {arms[0][key]} | {arms[1][key]} |")
        for label, key in (("推理 token", "reasoning_tokens"), ("全部 token", "total_tokens")):
            values = [arm["usage"][key] for arm in arms]
            counts = [f"{v['known_sum']}（有值 {v['measured_slots']}/29）" if v["measured_slots"] else "无观测（0/29）"
                      for v in values]
            lines.append(f"| {label} | {counts[0]} | {counts[1]} |")
        for label, key in (("请求", "request_seconds"), ("回合", "episode_seconds")):
            values = [arm["usage"][key] for arm in arms]
            times = [f"{_fmt(v['p50'])} / {_fmt(v['p95'])}（n={v['n']}）" for v in values]
            lines.append(f"| {label} P50 / P95 秒 | {times[0]} | {times[1]} |")
        lines += ["", "| 停止原因 | 开臂 | 关臂 | 仅开 | 仅关 |", "| --- | ---: | ---: | ---: | ---: |"]
        for label, key in zip(STOP_CN, STOP_CODES):
            stop = result["stop_pairs"][key]
            lines.append(f"| {label} | {stop['both'] + stop['only_on']} | {stop['both'] + stop['only_off']} | "
                         f"{stop['only_on']} | {stop['only_off']} |")
        for label, key in (("累计 token 用尽", "token_budget"), ("请求次数用尽", "request_limit"),
                           ("生成截断", "generation_truncated")):
            lines.append(f"| {label} | {arms[0]['stop_counts'].get(key, 0)} | "
                         f"{arms[1]['stop_counts'].get(key, 0)} | — | — |")
        lines += ["", "| 父场景 | 四格 | 净改善 |", "| --- | --- | ---: |"]
        for scene in result["by_scene"]:
            lines.append(f"| `{scene['scene_id']}` | {_cells(scene['four_cells'])} | {scene['net_gain']:+d} |")
    if report["diagnostics"]:
        lines += ["", "## 逐槽模拟旁证", ""]
    for diag in report["diagnostics"]:
        action, rejected = diag["action_order"], diag["rejected_request"]
        label = "开思考" if diag["arm"] == "on" else "关思考"
        if action:
            receipt = action["required_action_receipt_turn"]
            history = f"第 {receipt} 轮收到要求动作的回执" if receipt is not None else "没有要求动作的回执"
            score = "成功" if diag["checker_passed"] else "失败"
            lines.append(f"- `{diag['slot_id']}` {label}，第 {action['first_completion_turn']} 轮首次宣布完成，"
                         f"{history}。模拟 checker 计为{score}。")
        if rejected:
            lines.append(f"- `{diag['slot_id']}` 第 {rejected['turn']} 次请求因输入 "
                         f"{rejected['prompt_tokens']} 加预留输出 {rejected['reserved_output_tokens']} 超过 "
                         f"{rejected['context_limit']} token 被拒。该次未派发，用量表只累计此前已完成请求。")
    lines += ["", "独立评分字段决定四格。动作回执与首次完成声明的轮号、模型自报和 Harness 核验状态仅供旁证。"
              "可用 `gates.json` 中的槽 ID 回查输入观察记录。", ""]
    return "\n".join(lines)


def report_paired(plan, results_path, output):
    bundle = parse_json(Path(results_path).read_bytes())
    report = _report(plan, bundle, _g3())
    with new_output(output) as stage:
        _write(stage / "gates.json", report)
        (stage / "REPORT_CN.md").write_text(render_case(report), encoding="utf-8")
    return {k: report[k] for k in ("scenario", "scope", "formal_launches", "eligible_for_collection")}


def simulate_paired(plan, output):
    g3 = _g3()
    outcomes = []
    with new_output(output) as stage:
        for case, (label, expected1, expected2) in CASES.items():
            bundle = make_case(plan, case)
            report = _report(plan, bundle, g3)
            actual = tuple(report[gate]["status"] for gate in ("G1", "G2"))
            if actual != (expected1, expected2):
                raise ValueError(f"Synthetic case {case} expected {(expected1, expected2)}, got {actual}")
            model = report["by_model"][PRIMARY]
            target = stage / "cases" / case
            target.mkdir(parents=True)
            (target / "results.json").write_bytes(json_bytes(bundle))
            _write(target / "gates.json", report)
            (target / "REPORT_CN.md").write_text(render_case(report), encoding="utf-8")
            outcomes.append({"case": case, "description": label, "G1": actual[0], "G2": actual[1],
                             "four_cells": model["four_cells"], "net_gain": model["net_gain"],
                             "improved_scenes": model["improved_scenes"], "missing_pairs": model["missing_pairs"],
                             "input_sha256": report["input_sha256"]})
        summary = {"scope": "synthetic", "plan_sha256": _plan_digest(plan), "primary_model": PRIMARY,
                   "reference_model": REFERENCE, "expected_cases_passed": len(outcomes), "cases": outcomes,
                   "G3": g3, "formal_G1": "unmeasured", "formal_G2": "unmeasured", "eligible_for_collection": False,
                   "formal_launches": 0, "teacher_calls": 0, "training_steps": 0, "rental_cny": 0}
        _write(stage / "summary.json", summary)
        lines = ["# 29 题开关思考计分的 Mac 模拟验收", "",
                 f"{len(outcomes)} 组预设用例全部得到预期判定。每组使用相同的 5 个父场景、29 道开发题、"
                 "116 个计划槽。Qwen3.5-2B 的开关组决定 G1/G2，Qwen3-4B 结果按臂单列。",
                 "", "| 模拟用例 | G1 | G2 | 净改善 | 改善父场景 | 缺对 |",
                 "| --- | --- | --- | ---: | ---: | ---: |"]
        for outcome in outcomes:
            lines.append(f"| [{outcome['description']}](cases/{outcome['case']}/REPORT_CN.md) | "
                         f"{STATUS_CN[outcome['G1']]} | {STATUS_CN[outcome['G2']]} | "
                         f"{outcome['net_gain']:+d} | {outcome['improved_scenes']} | {outcome['missing_pairs']} |")
        lines += ["", "各用例目录包含可独立回放的 `results.json` 和逐槽、逐场景的 `gates.json`。"
                  "净改善只统计可评分配对；全部未启动时为 0 个观测到的净改善，配对数为 0。"
                  "模拟通过仅验收计分程序。正式 G1/G2 仍待测，新增教师调用、GPU 费用及训练步数均为 0。",
                  "", "## 在 Mac 上复算", "",
                  "在项目根目录复用旧交付中已物化的 dev 初态，生成新版计划，再独立回放第一组模拟结果。", "",
                  "```sh", 'export PYTHONPATH="$PWD/scripts:$PWD"', 'py=agent4drone/.venv/bin/python',
                  'study=outputs/flockcue-g1g2-mac-acceptance-20261108/dev-run/study', 'work=$(mktemp -d)',
                  '"$py" -m thinking_events prepare-dev --study "$study" --output "$work/run"',
                  '"$py" -m thinking_events simulate-paired --run "$work/run" --output "$work/sim"',
                  '"$py" -m thinking_events report --run "$work/run" --results "$work/sim/cases/pass/results.json" --output "$work/check"',
                  'cmp "$work/sim/cases/pass/gates.json" "$work/check/gates.json"', "```", "",
                  "[机器可读摘要](summary.json)", ""]
        (stage / "REPORT_CN.md").write_text("\n".join(lines), encoding="utf-8")
    return {k: summary[k] for k in ("scope", "expected_cases_passed", "formal_launches", "eligible_for_collection")}
