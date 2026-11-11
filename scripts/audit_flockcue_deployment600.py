"""Join all discordant deployment scores to the two first-attempt trajectory archives."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import os
from pathlib import Path

from report_flockcue_deployment600 import ROOT, check_plan, events, history_by_task, read


DIRECTIONS = {"historical_only": "旧过新失", "flockcue_only": "旧失新过"}
LABELS = {
    "provider": "终止性模型服务故障",
    "unscored": "无独立终态评分",
    "timeout": "500 秒截止",
    "step_limit": "到达 Agent 工具步上限",
    "claim_missed_checks": "最终回复带完成标记，原检查未过",
    "no_claim": "最终回复缺完成标记，原检查未过",
    "other": "未能定位最终声明或停因",
}
KIND = {
    "drone_has_visited_position": "到访指定位置",
    "drone_has_moved_directed_distance": "指定方向移动距离",
    "target_is_reached_by_drone": "指定机抵达目标",
    "drone_hovering": "终态悬停",
    "target_in_photo_taken_by_drone": "指定机拍到指定目标",
    "target_is_reached": "抵达目标",
    "drone_on_ground": "地面状态",
    "drone_has_taken_photo": "拍照记录",
    "drone_position": "终态位置",
    "target_is_fully_searched": "目标覆盖阈值",
    "drone_has_taken_off": "起飞记录",
    "drone_altitude": "高度",
    "task_progress": "整场累计进度",
    "two_drones_distance": "双机距离",
    "drone_has_moved_distance": "累计移动距离",
    "target_searched_area_percentage": "目标搜索覆盖率",
    "drone_has_hovered": "悬停历史",
    "drone_heading": "航向",
    "drone_has_sent_message": "通信记录",
    "drone_has_landed": "降落记录",
    "drone_at_home": "返航位置",
}
OBSERVATIONS = {
    "obstacle_receipt_and_unmet_route": "指定机移动时收到障碍回执，同机的位置或路程要求未达",
    "no_successful_photo": "指定机的拍照要求未过，本题增量无该机成功拍照记录",
    "photo_without_target": "指定机成功拍照，目标入镜检查仍未过",
    "no_successful_hover": "指定机悬停历史要求未过，本题增量无该机成功悬停记录",
    "no_successful_landing": "指定机降落历史要求未过，本题增量无该机成功降落记录",
}
ROUTE_LEAVES = {"drone_has_visited_position", "drone_has_moved_distance", "drone_has_moved_directed_distance",
                "drone_position", "target_is_reached_by_drone", "drone_at_home"}


def optional(path):
    return read(path) if path.is_file() else None


def short(value, size=320):
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:size] + ("…" if len(text) > size else "")


def trace_index(directory):
    commands = defaultdict(set)
    for event in events(directory / "agent-traces/reproduction-metrics.jsonl"):
        if event["event"] == "llm_start" and event.get("job_id") and event.get("command_id"):
            commands[event["job_id"]].add(event["command_id"])
    return commands


def trace_for_job(directory, commands, job, tid):
    if not job:
        return None, None
    ids = commands.get(job["job_id"], set())
    if not ids:
        return None, None
    if len(ids) != 1:
        raise ValueError(f"Ambiguous command identity for {directory.name}/{tid}")
    command_id, = ids
    paths = list((directory / "agent-traces/toolchains").glob(f"*_{command_id}_*.json"))
    if not paths:
        return None, None
    if len(paths) != 1:
        raise ValueError(f"Multiple toolchain records for {directory.name}/{tid}")
    trace = read(paths[0])
    if trace["command_id"] != command_id or trace["command"] != job["command"]:
        raise ValueError(f"Toolchain mismatched to original command: {paths[0]}")
    return trace, paths[0]


def hash_xy(snapshot):
    if snapshot is None:
        return None
    data = {d["id"]: [d["position"]["x"], d["position"]["y"]] for d in snapshot["drones"]}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def command_delta(before, after):
    if before is None or after is None:
        return None
    prior = {row["command_id"] for row in before["history"]["command_history"]}
    out = []
    seen = set()
    for row in after["history"]["command_history"]:
        ident = row["command_id"]
        if ident in prior:
            continue
        if ident in seen:
            raise ValueError(f"Ambiguous physics command ID {ident}")
        seen.add(ident)
        out.append({"actor": row.get("drone_id"), "action": row.get("command"), "status": row.get("status"),
                    "message": short(row.get("message")), "command_id": ident})
    return out


def leaf_evidence(path):
    checks = optional(path)
    if checks is None:
        return None
    leaves = []
    for row in checks:
        if row["type"] != "leaf":
            continue
        response = row.get("response") or {}
        # The raw endpoint reply may contain all historical images/positions. Keep
        # scalar measurements plus the check identity; full arrays remain in situ.
        measured = {k: v for k, v in response.items() if isinstance(v, (bool, int, float, str))
                    and k not in {"session_id", "drone_id", "target_id", "result"}}
        leaves.append({"endpoint": row["endpoint"], "parameters": row.get("parameters"),
                       "expect": row.get("expect", True), "actual": row.get("actual"),
                       "passed": row.get("result"), "measured": measured})
    return leaves


def control_calls(trace):
    controls = []
    for step in trace.get("tool_chain") or []:
        if step["tool"] != "run_harness":
            continue
        receipt = step.get("observation")
        if isinstance(receipt, str):
            try:
                receipt = json.loads(receipt)
            except ValueError:
                pass
        record = {"step": step["index"], "input": step["tool_input"]}
        if isinstance(receipt, dict):
            record.update({k: receipt.get(k) for k in ("status", "reason", "plan_id", "next_action") if k in receipt})
        else:
            record["receipt_preview"] = short(receipt)
        controls.append(record)
    return controls


def trace_details(trace):
    steps = trace.get("tool_chain") or []
    errors = []
    for call in trace.get("api_calls") or []:
        resp = call.get("response")
        if not isinstance(resp, dict):
            resp = {}
        # An action can return HTTP 200 and still be rejected by the simulator.
        if (call.get("success") is False or call.get("error") or resp.get("status") in {"error", "failed"}
                or resp.get("success") is False):
            errors.append({"index": call.get("index"), "endpoint": call["endpoint"], "http": call.get("status_code"),
                           "status": resp.get("status"), "message": short(resp.get("message") or call.get("error"))})
    harness = trace.get("harness") or {}
    frames = [{key: frame.get(key) for key in ("plan_id", "skill", "status", "reason", "cursor", "repairs", "api_calls")}
              for frame in harness.get("frames", [])]
    assessment = trace.get("completion_assessment") or {}
    model_output = trace.get("model_output", trace.get("output")) or ""
    # The model may quote the completion marker to explain why it cannot claim
    # success. Record literal marker presence and the software's own status.
    return {"trace_status": trace.get("status"), "success": trace.get("success"),
            "literal_task_done": "[TASK DONE]" in model_output, "final_text_suffix": short(model_output[-650:], 650),
            "hover_assessment": {key: assessment.get(key) for key in ("status", "completion_claim_accepted",
                                 "completion_marker_present", "kind", "actor_id", "reasons") if key in assessment},
            "tool_steps": len(steps), "tool_counts": dict(Counter(x["tool"] for x in steps)),
            "exceptional_api_receipts": errors, "subprogram_controls": control_calls(trace),
            "subprogram_frames": frames}


def arm(directory, tid, commands, raw, reason, base):
    job = optional(directory / f"{tid}.job.json")
    trace, trace_path = trace_for_job(directory, commands, job, tid)
    before = optional(directory / f"{tid}.before.json")
    after = optional(directory / f"{tid}.after.json")
    result = {"directory": os.path.relpath(directory, base), "accounting_reason": reason,
              "checker_passed": raw.get("passed"), "timed_out": raw.get("agent_timed_out"),
              "worker_confirmed": raw.get("continuation_safe"), "job_status": raw.get("agent_status"),
              "agent_success": raw.get("agent_success"), "initial_xy_sha256": hash_xy(before),
              "physical_command_delta": command_delta(before, after),
              "leaves": leaf_evidence(directory / f"{tid}.checks.json")}
    if trace is not None:
        result["trace"] = {"file": os.path.relpath(trace_path, base), **trace_details(trace)}
    else:
        result["trace"] = None
    return result


def mismatch_checks(old, new):
    a, b = old["leaves"], new["leaves"]
    if a is None or b is None:
        return None
    differences = []
    for i, (o, n) in enumerate(zip(a, b, strict=True), 1):
        if any(o[k] != n[k] for k in ("endpoint", "parameters", "expect")):
            raise ValueError(f"Original leaf definitions differ at index {i}")
        if o["passed"] != n["passed"]:
            differences.append({"leaf_index": i, "endpoint": n["endpoint"], "historical_pass": o["passed"],
                                "flockcue_pass": n["passed"]})
    return differences


def classify(failed):
    if failed["accounting_reason"] == "terminal_provider_error":
        return "provider"
    if failed["leaves"] is None or failed["checker_passed"] is None:
        return "unscored"
    if failed["timed_out"] is True:
        return "timeout"
    t = failed["trace"]
    if not t:
        return "other"
    if t["trace_status"] == "step_limit":
        return "step_limit"
    return "claim_missed_checks" if t["literal_task_done"] else "no_claim"


def linked_receipts(failed):
    commands = failed["physical_command_delta"]
    if commands is None or failed["leaves"] is None:
        return None
    evidence = []
    for idx, leaf in enumerate(failed["leaves"], 1):
        actor = (leaf["parameters"] or {}).get("drone_id")
        if leaf["passed"] is not False or leaf["expect"] is not True or not actor:
            continue
        rows = [r for r in commands if r["actor"] == actor]
        kind = leaf["endpoint"].removeprefix("/check/")
        label, matched = None, []
        if kind in ROUTE_LEAVES:
            matched = [r for r in rows if r["action"] in {"move_to", "move_along_path", "move_towards"}
                       and r["status"] in {"error", "partial_success"}
                       and any(word in (r["message"] or "").lower() for word in ("obstacle", "collision", "blocked"))]
            if matched:
                label = "obstacle_receipt_and_unmet_route"
        elif kind in {"target_in_photo_taken_by_drone", "drone_has_taken_photo", "drone_has_hovered", "drone_has_landed"}:
            action = {"drone_has_hovered": "hover", "drone_has_landed": "land"}.get(kind, "take_photo")
            matched = [r for r in rows if r["action"] == action]
            if not any(r["status"] == "success" for r in matched):
                label = {"hover": "no_successful_hover", "land": "no_successful_landing",
                         "take_photo": "no_successful_photo"}[action]
            elif kind == "target_in_photo_taken_by_drone":
                label = "photo_without_target"
        if label:
            evidence.append({"observation": label, "leaf_index": idx, "actor": actor,
                             "command_ids": [r["command_id"] for r in matched]})
    return evidence


def observed_regressions(records, direction):
    subset = [r for r in records if r["pair"] == direction]
    fail_key = "flockcue" if direction == "historical_only" else "historical"
    categories = Counter(r["termination_category"] for r in subset)
    failed_endpoints = Counter()
    controls, trace_known, movements, known_xy = 0, 0, 0, 0
    frames = Counter()
    observed = Counter()
    rejected_tasks = rejected_calls = frame_tasks = linked_coverage = 0
    bad_receipts = 0
    for r in subset:
        fail = r[fail_key]
        if fail["leaves"] is not None:
            failed_endpoints.update({l["endpoint"].removeprefix("/check/") for l in fail["leaves"] if l["passed"] is False})
        t = r["flockcue"]["trace"]
        if t is not None:
            trace_known += 1
            controls += bool(t["subprogram_controls"])
            frames.update((f["skill"], f["status"]) for f in t["subprogram_frames"])
            frame_tasks += bool(t["subprogram_frames"])
            refused = sum(c.get("status") in {"rejected", "error", "failed", "invalid"} for c in t["subprogram_controls"])
            rejected_tasks += bool(refused)
            rejected_calls += refused
        if r["failed_actor_receipt_links"] is not None:
            linked_coverage += 1
            observed.update({v["observation"] for v in r["failed_actor_receipt_links"]})
        if fail["trace"] and fail["trace"]["exceptional_api_receipts"]:
            bad_receipts += 1
        xy = r["same_fleet_xy_at_start"]
        if xy is not None:
            known_xy += 1
            movements += not xy
    return {"total": len(subset), "termination": dict(categories), "failed_endpoint_tasks": dict(failed_endpoints),
            "new_trace_present": trace_known, "new_explicit_control_tasks": controls,
            "new_rejected_control_calls": rejected_calls, "new_control_rejected_tasks": rejected_tasks,
            "new_recorded_frame_tasks": frame_tasks, "linked_evidence_available": linked_coverage,
            "failed_actor_observations": dict(observed),
            "new_frame_outcomes": [{"skill": skill, "status": status, "frames": n}
                                   for (skill, status), n in sorted(frames.items())],
            "failed_trace_has_error_receipt_tasks": bad_receipts,
            "xy_known": known_xy, "xy_different": movements}


def format_report(items, summary):
    lines = ["# 原版与 FlockCue 成绩不一致题的轨迹核对", "",
             "按场景、任务双键核查十月原版和本次 FlockCue 首遍。表中只含两期部署计分不一致的题位，"
             "两套策略沿各自轨迹连续执行。完整分母与逐题成绩见[配对表](tasks.csv)。", "",
             "| 失败一侧的直接停因或最终状态 | 旧过新失 | 旧失新过 |", "|---|---:|---:|"]
    for key, label in LABELS.items():
        vals = [summary[d]["termination"].get(key, 0) for d in DIRECTIONS]
        lines.append(f"| {label} | {vals[0]} | {vals[1]} |")
    lines.extend(["", "归档最终回复可能引用 `[TASK DONE]` 解释未完成，标记列只统计字面出现；JSON 另列 Agent 成功状态、终局原文末段与悬停声明验收。"
                  "同题有多个现象时，停因表依次优先记服务故障、缺测、超时、步数上限和标记。", "",
                  "## 未满足的检查", "",
                  "| 失败侧原检查 | 旧过新失（题） | 旧失新过（题） |", "|---|---:|---:|"])
    endpoints = set().union(*(v["failed_endpoint_tasks"] for v in summary.values()))
    for key in sorted(endpoints, key=lambda k: (-sum(v["failed_endpoint_tasks"].get(k, 0) for v in summary.values()), k)):
        vals = [summary[d]["failed_endpoint_tasks"].get(key, 0) for d in DIRECTIONS]
        lines.append(f"| {KIND.get(key, key)} | {vals[0]} | {vals[1]} |")
    lines.extend(["", "同一题内相同检查类型只计一次。一道题可能缺多个条件；OR 和反向要求仍按原树整题计分。"
                  "服务故障可按规定计零，即使继承状态恰好通过某些检查。", "", "## 失败侧同机检查与动作回执", "",
                  "| 同一架指定机在该题的记录 | 旧过新失（题） | 旧失新过（题） |", "|---|---:|---:|"])
    for key, label in OBSERVATIONS.items():
        vals = [summary[d]["failed_actor_observations"].get(key, 0) for d in DIRECTIONS]
        lines.append(f"| {label} | {vals[0]} | {vals[1]} |")
    counts = [summary[d]["linked_evidence_available"] for d in DIRECTIONS]
    lines.extend(["", f"旧过新失有 {counts[0]} 题、旧失新过有 {counts[1]} 题同时封存了前后动作历史与叶检查。"
                  "只核查明确指定无人机、期望为真的失败叶。动作增量取题后相对题前新增的物理指令 ID；同题多项可重叠。"
                  "命中同机障碍回执与终局未达只说明两者共现。", "", "## 预定义子程序、回执与起点", ""])
    for g, label in DIRECTIONS.items():
        s = summary[g]
        lines.append(f"- {label} {s['total']} 题中，{s['new_trace_present']} 题有可归属的 FlockCue 工具链，"
                     f"其中 {s['new_explicit_control_tasks']} 题记录了 `run_harness` 工具步骤。"
                     f"{s['new_control_rejected_tasks']} 题收到拒绝或错误回执（共 {s['new_rejected_control_calls']} 次），"
                     f"{s['new_recorded_frame_tasks']} 题记录了程序实例。"
                     f"两期题初机队 XY 可比的 {s['xy_known']} 题中，有 {s['xy_different']} 题至少一架机位置不同。"
                     f"失败侧有 {s['failed_trace_has_error_receipt_tasks']} 题在工具链中留有明确的 API 错误回执。")
        if s["new_frame_outcomes"]:
            frames = "、".join(f"{f['skill']} {f['status']} {f['frames']} 次" for f in s["new_frame_outcomes"])
            lines.append(f"  记录的程序实例终态为 {frames}。")
    lines.extend(["", "程序控制请求可以被拒绝，实例完成也只代表该程序达到了公开退出条件。"
                  "工具链缺失时显式调用数记为未知；错误回执可能已在后续轮恢复。"
                  "每个不一致题保留两期逐叶实测值、物理动作增量、工具链路径、程序终态及原始终局声明的末段。"
                  "[逐题证据](mechanism-pairs.json)", "",
                  "历史差异同时涉及服务时间窗、模型输出和题间继承。模块作用需从同一题初状态、同一服务设置下做单因素对照。"])
    return "\n".join(lines) + "\n"


def examples(items):
    lines = ["", "## 逐题对照示例", "",
             "按原题序，各方向先列一个有子程序控制记录的题，再列一个带不同首个差异检查的题。完整不一致题均在 JSON 中。", ""]
    for direction, label in DIRECTIONS.items():
        candidates = [r for r in items if r["pair"] == direction and r["leaf_disagreements"]]
        candidates.sort(key=lambda r: not bool((r["flockcue"]["trace"] or {}).get("subprogram_controls")))
        seen = set()
        for row in candidates:
            differences = [m for m in row["leaf_disagreements"]
                           if not m["flockcue_pass" if direction == "historical_only" else "historical_pass"]]
            if not differences:
                continue
            leaf = differences[0]
            if leaf["endpoint"] in seen:
                continue
            seen.add(leaf["endpoint"])
            li = leaf["leaf_index"] - 1
            old, new = row["historical"], row["flockcue"]
            o, n = old["leaves"][li], new["leaves"][li]
            topic = KIND.get(leaf["endpoint"].removeprefix("/check/"), leaf["endpoint"])
            vals = [v["measured"].get("value", v["actual"]) for v in (o, n)]
            counts = [f"{len(v['physical_command_delta'])} 次" if v["physical_command_delta"] is not None else "缺测"
                      for v in (old, new)]
            lines.append(f"- {label}，`{row['session_id']}/{row['task_id']}`（场内第 {row['index']} 题）。"
                         f"两期世界的本题新增物理指令分别为 {counts[0]}、{counts[1]}。"
                         f"叶检查 {li+1} 为「{topic}」，期望布尔值 {o['expect']}，端点实测量按十月／本次为 "
                         f"{vals[0]}／{vals[1]}。失败侧记录为「{LABELS[row['termination_category']]}」。"
                         f"[十月原检查]({old['directory']}/{row['task_id']}.checks.json) · "
                         f"[本次原检查]({new['directory']}/{row['task_id']}.checks.json)")
            if len(seen) == 2:
                break
    return "\n".join(lines) + "\n"


def audit(attempt, report_dir):
    if report_dir.is_relative_to(attempt):
        raise ValueError("Keep analysis artifacts outside the first-pass archive")
    plan = check_plan(attempt)
    historical = history_by_task(plan)
    manifest = read(attempt / "manifest.json")
    if not manifest.get("finished_at"):
        raise ValueError("Mechanism audit requires a finished deployment")
    pairs_file = report_dir / "tasks.csv"
    with pairs_file.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    expected = [(s["id"], t["id"]) for s in plan["sessions"] for t in s["tasks"]]
    if [(p["session_id"], p["task_id"]) for p in rows] != expected:
        raise ValueError("Scored table differs from frozen task order")
    by_scene = defaultdict(list)
    for r in rows:
        key = (r["session_id"], r["task_id"])
        old, new = r["historical_pass"], r["task_pass"]
        expected_pair = {("True", "True"): "both_pass", ("False", "False"): "both_fail",
                         ("True", "False"): "historical_only", ("False", "True"): "flockcue_only"}[old, new]
        if old != historical[key]["task_pass"] or expected_pair != r["pair"]:
            raise ValueError(f"Pair class is inconsistent: {key}")
        if r["pair"] in DIRECTIONS:
            by_scene[key[0]].append(r)
    items = []
    for scene in plan["sessions"]:
        sid = scene["id"]
        subset = by_scene[sid]
        if not subset:
            continue
        record = manifest["scenes"][sid]
        if (record["generation"] not in (0, 1) or record.get("directory") not in (None, sid)):
            raise ValueError(f"First-pass scene identity changed: {sid}")
        directories = [ROOT / historical[(sid, subset[0]["task_id"])]["evidence_dir"], attempt / sid]
        originals = [{r["task_id"]: r for r in optional(p / "results.json") or []} for p in directories]
        commands = [trace_index(p) for p in directories]
        for r in subset:
            tid = r["task_id"]
            key = sid, tid
            old = historical[key]
            if (ROOT / old["evidence_dir"]) != directories[0]:
                raise ValueError(f"Mixed historical executions in scene {sid}")
            data = [arm(directories[i], tid, commands[i], originals[i].get(tid, {}), reason, report_dir)
                    for i, reason in enumerate((old["accounting_reason"], r["accounting_reason"]))]
            if data[1]["checker_passed"] is not None and str(data[1]["checker_passed"]) != r["checker_passed"]:
                raise ValueError(f"Checker outcome differs from scoring report: {key}")
            xy = [a["initial_xy_sha256"] for a in data]
            fail = 1 if r["pair"] == "historical_only" else 0
            items.append({"session_id": sid, "task_id": tid, "index": int(r["index"]), "name": r["name"],
                          "pair": r["pair"], "family": r["family"], "termination_category": classify(data[fail]),
                          "failed_actor_receipt_links": linked_receipts(data[fail]),
                          "same_fleet_xy_at_start": (xy[0] == xy[1]) if all(xy) else None,
                          "leaf_disagreements": mismatch_checks(*data), "historical": data[0], "flockcue": data[1]})
    summary = {d: observed_regressions(items, d) for d in DIRECTIONS}
    output = {"frozen_tasks": len(rows), "scoring_table_sha256": hashlib.sha256(pairs_file.read_bytes()).hexdigest(),
              "attempt": os.path.relpath(attempt, report_dir), "by_direction": summary, "cases": items}
    (report_dir / "mechanism-pairs.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    (report_dir / "MECHANISMS_CN.md").write_text(format_report(items, summary) + examples(items))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Completed scoring report directory with tasks.csv")
    args = parser.parse_args()
    print(json.dumps(audit(args.run.resolve(), args.output.resolve()), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
