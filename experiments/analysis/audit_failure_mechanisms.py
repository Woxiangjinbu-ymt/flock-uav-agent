#!/usr/bin/env python3
"""Offline symptom audit; geometry flags are evidence, not inferred LLM causes.

Use server/.venv/bin/python after analyze_failure_snapshot.py. No service calls.
"""
import collections
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
from models.obstacle import Obstacle
from shapely.geometry import Point


def read(path):
    return json.loads(path.read_text())


def main():
    out = Path(sys.argv[1]).resolve()
    evidence = read(out / "evidence-sha256.json")
    plans = read(ROOT / "experiments/runs/20261003-143255-full-5c501118/plan.json")
    sources = {s["id"]: read(ROOT / "controller/UAVSessions" / s["file"]) for s in plans["sessions"]}
    geometries = {sid: [(o, Obstacle.from_dict(o).to_geometry()) for o in s["obstacles"]] for sid,s in sources.items()}
    records = []
    for model in ("deepseek", "qwen"):
        rows = [x for x in read(out / f"{model}-tasks.json") if not x["passed"]]
        by_dir = collections.defaultdict(list)
        for row in rows:
            by_dir[row["evidence_dir"]].append(row)
        for dirname, scene_rows in by_dir.items():
            directory = ROOT / dirname
            seal = read(directory / "complete.json")

            def verified(path):
                raw = path.read_bytes()
                sha = hashlib.sha256(raw).hexdigest()
                assert seal["files"][str(path.relative_to(directory))] == sha
                evidence[str(path.relative_to(ROOT))] = sha
                return json.loads(raw)

            traces = []
            for f in (directory / "agent-traces/toolchains").glob("*.json"):
                trace = read(f)
                if "command" in trace:
                    traces.append((f, trace))
            for row in scene_rows:
                source = sources[row["session_id"]]
                target_map = {t["id"]:t for t in source["targets"]}
                checks = [x for x in verified(directory / f"{row['task_id']}.checks.json") if x.get("type") == "leaf" and x["result"] is False]
                geometry_flags, missing_ids, target_evidence = [], [], []
                for leaf in checks:
                    params = leaf["parameters"]
                    if leaf["endpoint"] in ("/check/drone_has_visited_position", "/check/drone_position"):
                        if "x" in params and "y" in params:
                            point = Point(params["x"], params["y"])
                            tolerance = params.get("tolerance", 5)
                            for obstacle, shape in geometries[row["session_id"]]:
                                # Conservative certificate: tolerance disk wholly inside a full-height obstacle.
                                if obstacle.get("height") == 0 and shape.contains(point) and shape.boundary.distance(point) > tolerance + 0.01:
                                    geometry_flags.append({"endpoint": leaf["endpoint"], "parameters": params,
                                        "obstacle_id": obstacle["id"], "obstacle_name": obstacle["name"],
                                        "distance_to_boundary": shape.boundary.distance(point), "tolerance": tolerance})
                    if "target_id" in params:
                        target = target_map.get(params["target_id"])
                        if target is None:
                            missing_ids.append(params["target_id"])
                        else:
                            target_evidence.append({"id": target["id"], "name": target["name"], "type": target["type"], "position": target["position"]})
                candidates = [(f,t) for f,t in traces if t["command"] == row["command"]]
                assert candidates, (model,row["task_id"])
                created = datetime.fromisoformat(row["created_at"])
                f, trace = min(candidates, key=lambda ft: abs((datetime.fromisoformat(ft[1]["started_at"])-created).total_seconds()))
                time_delta = abs((datetime.fromisoformat(trace["started_at"])-created).total_seconds())
                assert time_delta < 10, (f,time_delta)
                trace = verified(f)
                calls = trace.get("api_calls", [])
                api_counts = collections.Counter()
                discovered = set()
                rejected = 0
                commands = []
                for call in calls:
                    ep = call["endpoint"]
                    if "/command/" in ep:
                        api_counts[ep.split("/command/")[-1]] += 1
                        commands.append({"endpoint": ep, "params": call.get("params"), "json": call.get("json"), "response": call.get("response")})
                    elif ep.endswith("/nearby"):
                        api_counts["nearby"] += 1
                        res = call.get("response") or {}
                        if isinstance(res,dict):
                            discovered.update(t["id"] for t in res.get("targets",[]))
                    else:
                        api_counts["other_reads"] += 1
                    res = call.get("response")
                    if isinstance(res,dict) and (res.get("status") == "error" or res.get("success") is False):
                        rejected += 1
                state = verified(directory / f"{row['task_id']}.agent-state.json")
                bb = state.get("blackboards",{}).get(row["session_id"],{})
                known = set(bb.get("targets",{}))
                contradictory_navigation = []
                for step in trace.get("tool_chain", []):
                    if not str(step.get("tool", "")).startswith("navigate_to"):
                        continue
                    try:
                        observation = json.loads(step["observation"])
                    except (ValueError, TypeError):
                        continue
                    nav = observation.get("navigation_result", observation)
                    if nav.get("success") is False and nav.get("distance_remaining") == 0:
                        contradictory_navigation.append({"index": step["index"], "tool": step["tool"],
                            "distance_remaining": 0, "last_move_status": nav.get("last_move_result",{}).get("status")})
                records.append({"model": model, "session_id": row["session_id"], "task_id": row["task_id"],
                    "bucket": row["bucket"], "agent_timed_out": row["agent_timed_out"],
                    "failed_check_responses": checks, "blocked_coordinate_certificates": geometry_flags,
                    "missing_target_ids_in_source": sorted(set(missing_ids)),
                    "failed_target_source_facts": target_evidence,
                    "source_target_count": len(target_map), "blackboard_target_count": len(known),
                    "failed_target_ids_absent_from_blackboard": sorted({t["id"] for t in target_evidence}-known),
                    "observed_target_ids_this_task": sorted(discovered),
                    "trace_path": str(f.relative_to(ROOT)), "trace_status": trace["status"],
                    "trace_time_match_seconds": time_delta, "api_call_count": len(calls), "api_counts": dict(api_counts),
                    "api_error_responses": rejected, "trace_tool_count": len(trace.get("tool_chain",[])),
                    "contradictory_navigation": contradictory_navigation,
                    "last_commands": commands[-6:]})
    (out / "mechanisms.json").write_text(json.dumps(records, ensure_ascii=False, indent=2)+"\n")
    (out / "evidence-sha256.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+"\n")
    for m in ("deepseek","qwen"):
        rs = [x for x in records if x["model"] == m]
        print(m, "failed",len(rs), "blocked coordinate tasks",sum(bool(x['blocked_coordinate_certificates']) for x in rs),
              "near blocked",sum(x['bucket']=='near_miss' and bool(x['blocked_coordinate_certificates']) for x in rs),
              "missing source ids",sum(bool(x['missing_target_ids_in_source']) for x in rs),
              "target absent from blackboard tasks",sum(bool(x['failed_target_ids_absent_from_blackboard']) for x in rs))
    (out / "SHA256SUMS.json").write_text(json.dumps({f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in out.iterdir() if f.is_file() and f.name != "SHA256SUMS.json"}, indent=2)+"\n")


if __name__ == "__main__":
    main()
