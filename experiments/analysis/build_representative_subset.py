"""Build/verify a fixed whole-scene development benchmark; no model/service calls.

Run with controller/.venv/bin/python. The selection is a deterministic, capped
coverage heuristic, not a population estimator or a globally optimal solution.
"""
import argparse
import ast
import collections
import csv
import hashlib
import itertools
import json
import random
import re
import statistics
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "docs/results/baseline-deployment-20261005"
AUDIT = ROOT / "docs/results/failure-analysis-20261005"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "server"))
import reproduction as harness
from models.obstacle import Obstacle
from shapely.geometry import Point

MODELS = ("deepseek", "qwen")
DIFFICULTIES = ("Easy", "Intermediate", "Moderate", "Hard", "Extreme")
FAMILIES = ("area_assignment_and_patrol", "area_search", "target_assignment")
SEED = 20261005
COHORT = "first_attempt_no_replay"
REVIEWED_CASES = {
    ("fb8971ec", "82077301"): "final_distance_vs_movement",
    ("76802002", "55b61526"): "undiscovered_target_not_nonexistent",
    ("93831d6d", "7af66f50"): "photo_action_vs_target_in_frame",
    ("35ce64d8", "cf8ee51f"): "missing_final_landing_after_long_route",
    ("4e425a5b", "4cb91745"): "directed_segment_vs_net_displacement",
    ("3973d0d6", "9c0b52a0"): "observed_targets_vs_global_progress",
    ("ef36e27a", "4ccc9027"): "target_binding_and_coverage_handle",
    ("9ddae16c", "649bca01"): "early_exit_without_action",
    ("8b7866a0", "87072bfa"): "visited_targets_vs_completion_threshold",
    ("ad34350a", "bfc721cc"): "coverage_below_required_threshold",
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def csv_save(path, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                         for k, v in row.items()} for row in rows)


def bucket(row):
    if row["accounting_reason"] != "observed_checker":
        return row["accounting_reason"]
    if row["task_pass"]:
        return "pass"
    passed = row["observed_passed_checks"]
    if passed == 0:
        return "zero_single" if row["expected_checks"] == 1 else "zero_multi"
    return "near_miss" if passed == row["expected_checks"] - 1 else "other_partial"


def distribution(values):
    if not values:
        return {"observed_tasks": 0, "sum": 0, "mean": None, "p95": None}
    values = sorted(values)
    p = (len(values) - 1) * 0.95
    lo = int(p)
    return {"observed_tasks": len(values), "sum": sum(values), "mean": statistics.mean(values),
            "p95": values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (p - lo)}


def summarize(rows):
    n = len(rows)
    checks = sum(r["expected_checks"] for r in rows)
    return {
        "tasks": n, "checks": checks, "passed": sum(r["task_pass"] for r in rows),
        "task_pass_rate": sum(r["task_pass"] for r in rows) / n,
        "deployment_passed": sum(r["deployment_pass"] for r in rows),
        "avg_check": sum(r["credited_checks"] / r["expected_checks"] for r in rows) / n,
        "global_check": sum(r["credited_checks"] for r in rows) / checks,
        "buckets": dict(collections.Counter(bucket(r) for r in rows)),
        "timeouts": sum(r["agent_timed_out"] is True for r in rows),
        "worker_not_confirmed_scored": sum(r["original_status"] == "scored" and not r["worker_confirmed"] for r in rows),
        "scoring_ready_seconds": distribution([r["scoring_ready_seconds"] for r in rows if r["scoring_ready_seconds"] is not None]),
        "observed_token_usage": {
            k: {**distribution([r["observed_usage"][k] for r in rows if r["observed_usage"].get(k) is not None]),
                "observed_sum_per_planned_task": sum(r["observed_usage"].get(k) or 0 for r in rows) / n}
            for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cache_hit_tokens")},
    }


def weight_cap(feature):
    kind = feature.split(":", 1)[0]
    return {
        "reviewed": (14, 1), "signal": (7, 4), "conflict": (4, 2),
        "failure_endpoint": (5, 4), "failure_category": (7, 5),
        "failure_template": (1, 2), "category": (2, 8), "template": (0.4, 1),
        "bucket": (8, 8), "pair": (6, 8), "control": (4, 5),
        "runtime": (2, 2),
    }[kind]


def load_inputs():
    input_hashes = {}
    for directory, names in ((BASE, ("tasks.json", "results.json", "verification.json", "evidence-sha256.json")),
                             (AUDIT, ("mechanisms.json", "evidence-sha256.json"))):
        sums = read(directory / "SHA256SUMS.json")
        for name in names:
            p = directory / name
            assert sha(p) == sums[name], p
            input_hashes[str(p.relative_to(ROOT))] = sha(p)
    baseline = read(BASE / "results.json")
    assert baseline["accounting_complete"] and baseline["planned_tasks"] == 1500
    assert baseline["planned_checks"] == 9396
    rows = [r for r in read(BASE / "tasks.json") if r["cohort"] == COHORT]
    assert len(rows) == 3000 and all(r["generation"] == 1 for r in rows)
    lookup = {(r["model"], r["session_id"], r["task_id"]): r for r in rows}
    assert len(lookup) == 3000
    plan = read(ROOT / baseline["attempts"]["deepseek"] / "plan.json")
    assert sha(ROOT / "benchmark/benchmark.zip") == plan["benchmark_sha256"]
    input_hashes["benchmark/benchmark.zip"] = plan["benchmark_sha256"]
    input_hashes["controller/default_templates.py"] = sha(ROOT / "controller/default_templates.py")
    tree = ast.parse((ROOT / "controller/default_templates.py").read_text())
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign) and
                      any(isinstance(t, ast.Name) and t.id == "DEFAULT_TEMPLATES" for t in n.targets))
    templates = ast.literal_eval(assignment.value)
    categories = {t["name"]: t.get("category", "Unknown") for t in templates.values()}
    mechanisms = {}
    for m in read(AUDIT / "mechanisms.json"):
        k = (m["model"], m["session_id"], m["task_id"])
        # The earlier snapshot included later generations: do not misattribute them to first attempts.
        if Path(m["trace_path"]).is_relative_to(Path(lookup[k]["evidence_dir"])):
            mechanisms[k] = m
    sources, raw_sources = {}, {}
    with zipfile.ZipFile(ROOT / "benchmark/benchmark.zip") as z:
        for scene in plan["sessions"]:
            raw = z.read(scene["file"])
            assert hashlib.sha256(raw).hexdigest() == scene["sha256"]
            raw_sources[scene["id"]] = raw
            sources[scene["id"]] = json.loads(raw)
    return baseline, rows, lookup, plan, categories, mechanisms, sources, raw_sources, input_hashes


def collect_tasks(lookup, plan, categories, mechanisms, sources):
    tasks, scene_features = [], {}
    for scene in plan["sessions"]:
        sid = scene["id"]
        source = sources[sid]
        geometries = [(o, Obstacle.from_dict(o).to_geometry()) for o in source["obstacles"] if o.get("height") == 0]
        for index, task in enumerate(source["tasks"], 1):
            matched = re.fullmatch(r"Task Template (.+) created by .+", task.get("originated_from", ""))
            template = matched.group(1) if matched else "Unknown"
            category = categories.get(template, "Unknown")
            features = {f"category:{category}", f"template:{template}"}
            conflicts, evidence_links = [], []
            for leaf in harness.leaves(task["execution_check_apis"]):
                node = leaf["definition"]
                p = node.get("parameters", {})
                if node.get("endpoint") in ("/check/drone_position", "/check/drone_has_visited_position") and "x" in p and "y" in p:
                    point = Point(p["x"], p["y"])
                    for obstacle, shape in geometries:
                        if shape.contains(point) and shape.boundary.distance(point) > p.get("tolerance", 5) + 0.01:
                            conflicts.append({"kind": "geometric_check_conflict", "leaf_path": leaf["path"],
                                              "obstacle_id": obstacle["id"], "boundary_distance": shape.boundary.distance(point)})
                if node.get("endpoint") == "/check/drone_has_taken_off":
                    commanded = re.search(r"taking off to ([0-9.]+) meters", task["content"])
                    if commanded and float(commanded.group(1)) < p.get("min_altitude", 0):
                        conflicts.append({"kind": "takeoff_instruction_check_conflict", "leaf_path": leaf["path"],
                                          "commanded_altitude": float(commanded.group(1)), "checked_min_altitude": p["min_altitude"]})
            features.update(f"conflict:{c['kind']}" for c in conflicts)
            pair = [lookup[m, sid, task["id"]] for m in MODELS]
            if all(r["accounting_reason"] == "observed_checker" for r in pair):
                d, q = (r["task_pass"] for r in pair)
                paired = "both_pass" if d and q else "deepseek_only" if d else "qwen_only" if q else "both_fail"
                features.add(f"pair:{paired}")
                if paired == "both_pass":
                    features.add(f"control:{category}")
            else:
                paired = "infrastructure_affected"
            for model, row in zip(MODELS, pair):
                b = bucket(row)
                features.add(f"bucket:{model}:{b}") if b not in ("pass", "terminal_provider_error", "unscored_execution_stop") else None
                if row["accounting_reason"] != "observed_checker":
                    features.add(f"runtime:{row['accounting_reason']}")
                    continue
                if not row["worker_confirmed"]:
                    features.add("runtime:worker_not_confirmed")
                if row["agent_timed_out"]:
                    features.add("signal:timeout_with_progress" if row["observed_passed_checks"] else "signal:timeout_zero_checks")
                if not row["task_pass"]:
                    features.add(f"failure_category:{category}")
                    features.add(f"failure_template:{template}")
                    features.update(f"failure_endpoint:{l['definition']['endpoint']}" for l in row["original_leaves"] if l["result"] is False)
                    if row["agent_success"] is True:
                        features.add("signal:agent_success_flag_but_checks_fail")
                audit = mechanisms.get((model, sid, task["id"]))
                if audit:
                    evidence_links.append(audit["trace_path"])
                    if audit["failed_target_ids_absent_from_blackboard"]:
                        features.add("signal:failed_target_absent_from_blackboard")
                    if audit["contradictory_navigation"]:
                        features.add("signal:navigation_false_at_zero_distance")
                    if audit["api_counts"].get("nearby", 0) >= 20 and not row["task_pass"]:
                        features.add("signal:repeated_local_sensing")
                    if (sid, task["id"]) in REVIEWED_CASES:
                        features.add("reviewed:" + REVIEWED_CASES[sid, task["id"]])
            record = {"session_id": sid, "scene_name": scene["name"], "family": scene["family"],
                      "scene_difficulty": re.search(r" (Easy|Intermediate|Moderate|Hard|Extreme) \d+$", scene["name"]).group(1),
                      "task_id": task["id"], "ordinal": index, "name": task["name"], "command": task["content"],
                      "task_difficulty": task["difficulty"], "template": template, "generator_category": category,
                      "expected_checks": len(harness.leaves(task["execution_check_apis"])),
                      "paired_outcome_observed": paired, "conflicts": conflicts, "mechanism_trace_paths": evidence_links,
                      "features": sorted(features)}
            for model, row in zip(MODELS, pair):
                record[model] = {"bucket": bucket(row), "task_pass": row["task_pass"],
                                 "observed_passed_checks": row["observed_passed_checks"],
                                 "accounting_reason": row["accounting_reason"], "worker_confirmed": row["worker_confirmed"],
                                 "timeout": row["agent_timed_out"], "scoring_ready_seconds": row["scoring_ready_seconds"],
                                 "observed_token_usage": row["observed_usage"], "evidence_dir": row["evidence_dir"],
                                 "failed_check_endpoints": sorted({l["definition"]["endpoint"] for l in row["original_leaves"] if l["result"] is False})}
            tasks.append(record)
        scene_features[sid] = collections.Counter(f for t in tasks if t["session_id"] == sid for f in t["features"])
    return tasks, scene_features


def selector(rows, plan, scene_features):
    cells, metadata = {}, {}
    medians = {}
    for m in MODELS:
        for d in DIFFICULTIES:
            valid = [r for r in rows if r["model"] == m and r["scene_difficulty"] == d and r["accounting_reason"] == "observed_checker"]
            medians[m, d] = (statistics.median(r["scoring_ready_seconds"] for r in valid),
                             statistics.median(r["observed_usage"].get("total_tokens", 0) for r in valid))
    for scene in plan["sessions"]:
        sid = scene["id"]
        sr = [r for r in rows if r["session_id"] == sid]
        d = sr[0]["scene_difficulty"]
        cells.setdefault((scene["family"], d), []).append(sid)
        seconds, tokens = 0, 0
        for r in sr:
            fallback = medians[r["model"], d]
            seconds += r["scoring_ready_seconds"] if r["accounting_reason"] == "observed_checker" else fallback[0]
            tokens += r["observed_usage"].get("total_tokens", fallback[1]) if r["accounting_reason"] == "observed_checker" else fallback[1]
        metadata[sid] = {"family": scene["family"], "difficulty": d,
                         "cost_proxy": seconds / 10000 + tokens / 100000000,
                         "unscored_tasks": sum(r["original_status"] != "scored" for r in sr),
                         "infrastructure_affected": sum(r["accounting_reason"] != "observed_checker" for r in sr)}

    # Prefer observed execution evidence within every stratum. All 15 strata
    # have first-attempt candidates with all 40 model-task checks observed.
    # A later worker failure or provider-error score remains separately labeled.
    for cell, candidates in cells.items():
        fewest_missing = min(metadata[s]["unscored_tasks"] for s in candidates)
        for sid in candidates:
            metadata[sid]["eligible_by_observation_coverage"] = metadata[sid]["unscored_tasks"] == fewest_missing
        cells[cell] = [s for s in candidates if metadata[s]["eligible_by_observation_coverage"]]

    def objective(ids):
        counts = collections.Counter()
        for sid in ids:
            counts.update(scene_features[sid])
        coverage = sum(weight_cap(f)[0] * min(count, weight_cap(f)[1]) / weight_cap(f)[1] for f, count in counts.items())
        # Only modest tie pressure on cost; unavailable observations cannot appear artificially cheap.
        penalty = sum(metadata[s]["cost_proxy"] * 0.35 + metadata[s]["infrastructure_affected"] * 0.35 for s in ids)
        return round(coverage - penalty, 9)

    ordered_cells = [(f, d) for f in FAMILIES for d in DIFFICULTIES]
    rng = random.Random(SEED)
    best = None
    for restart in range(32):
        chosen = [sorted(cells[c])[0] if restart == 0 else rng.choice(sorted(cells[c])) for c in ordered_cells]
        for _ in range(20):
            old = list(chosen)
            for i, c in enumerate(ordered_cells):
                candidates = [(objective(chosen[:i] + [sid] + chosen[i + 1:]), sid) for sid in sorted(cells[c])]
                chosen[i] = min(candidates, key=lambda x: (-x[0], x[1]))[1]
            if old == chosen:
                break
        option = (objective(chosen), tuple(chosen))
        if best is None or (-option[0], option[1]) < (-best[0], best[1]):
            best = option
    return list(best[1]), objective, metadata, best[0]


def choose_splits(main, objective, metadata, scene_features):
    # Preserve high-value reviewed mechanisms in development. Lock one scene per family;
    # moderate/hard validation scenes test nontrivial tasks without consuming the extreme exemplars.
    choices = [[s for s in main if metadata[s]["family"] == f and metadata[s]["difficulty"] in ("Moderate", "Hard")]
               for f in FAMILIES]
    options = []
    for val in itertools.product(*choices):
        dev = [s for s in main if s not in val]
        options.append((objective(dev) + 0.10 * objective(val), tuple(val)))
    val = min(options, key=lambda x: (-x[0], x[1]))[1]
    dev = [s for s in main if s not in val]
    candidates = []
    for fast in itertools.combinations(dev, 5):
        if {metadata[s]["family"] for s in fast} != set(FAMILIES):
            continue
        if len({metadata[s]["difficulty"] for s in fast}) < 3:
            continue
        if not any(metadata[s]["family"] == "target_assignment" and metadata[s]["difficulty"] in ("Hard", "Extreme") for s in fast):
            continue
        candidates.append((objective(fast), fast))
    fast = min(candidates, key=lambda x: (-x[0], x[1]))[1]
    return {"main300": main, "dev240": dev, "validation60": list(val), "fast100": list(fast)}


def verify_selected(rows, selected, tasks, evidence_hashes, audit_hashes):
    verified = {}
    task_by_id = {(t["session_id"], t["id"]): t for t in tasks}

    def evidence(relative, hashes):
        path = ROOT / relative
        actual = sha(path)
        assert hashes.get(relative) == actual, relative
        verified[relative] = actual
        return read(path)

    replayed = 0
    for row in rows:
        if row["session_id"] not in selected or row["original_status"] != "scored":
            continue
        prefix = f'{row["evidence_dir"]}/{row["task_id"]}'
        checks = evidence(prefix + ".checks.json", evidence_hashes)
        barrier = evidence(prefix + ".barrier.json", evidence_hashes)
        window = evidence(prefix + ".window.json", evidence_hashes)
        evidence(prefix + ".job.json", evidence_hashes)
        assert barrier["lease"] == window["lease"] and barrier["quiescent"] is True
        assert barrier["simulation_paused"] is True and barrier["inflight"] == 0
        leaves = iter(c for c in checks if c.get("type") == "leaf")

        class API:
            def api_check_execution(self, endpoint, parameters, show_error=False):
                leaf = next(leaves)
                assert leaf["endpoint"] == endpoint
                assert parameters == {**leaf["parameters"], "session_id": row["session_id"]}
                return leaf["response"]

        tree = task_by_id[row["session_id"], row["task_id"]]["execution_check_apis"]
        passed, checked = harness.upstream_evaluate(API(), tree, row["session_id"])
        assert next(leaves, None) is None and passed == row["original_passed"]
        assert not any(c.get("error") for c in checked)
        assert [(c["result"], c["actual"]) for c in checked if c.get("type") == "leaf"] == [(c["result"], c["actual"]) for c in row["original_leaves"]]
        replayed += 1
    return verified, replayed


def build(out):
    if (out / "main300.json").exists():
        raise ValueError("Refusing to overwrite a frozen subset; use a new output/version")
    baseline, rows, lookup, plan, categories, mechanisms, sources, raw_sources, input_hashes = load_inputs()
    tasks, features = collect_tasks(lookup, plan, categories, mechanisms, sources)
    main, objective, metadata, score = selector(rows, plan, features)
    splits = choose_splits(main, objective, metadata, features)
    by_sid = {s["id"]: s for s in plan["sessions"]}
    splits = {name: sorted(ids, key=lambda s: by_sid[s]["file"]) for name, ids in splits.items()}
    selected_tasks = [t for t in tasks if t["session_id"] in main]
    source_tasks = [{**t, "session_id": sid} for sid in main for t in sources[sid]["tasks"]]
    evidence_hashes = read(BASE / "evidence-sha256.json")
    audit_hashes = read(AUDIT / "evidence-sha256.json")
    verified, replayed = verify_selected(rows, main, source_tasks, evidence_hashes, audit_hashes)
    for task in selected_tasks:
        for path in task["mechanism_trace_paths"]:
            assert sha(ROOT / path) == audit_hashes[path], path
            verified[path] = audit_hashes[path]
    out.mkdir(parents=True, exist_ok=True)
    (out / "scenes").mkdir(exist_ok=True)
    for sid in main:
        (out / "scenes" / by_sid[sid]["file"]).write_bytes(raw_sources[sid])
    for name, ids in splits.items():
        sessions = [{**by_sid[s], "source_file": by_sid[s]["file"], "file": "scenes/" + by_sid[s]["file"]} for s in ids]
        manifest = {"schema": 1, "dataset_id": "agent4drone-representative-v1", "split": name,
                    "status": "frozen", "benchmark_sha256": plan["benchmark_sha256"],
                    "selection_baseline": "deployment-no-scene-replay-v1:first_attempt_no_replay",
                    "execution_protocol": plan["protocol"], "scoring_protocol": baseline["protocol"],
                    "expected_tasks": sum(len(s["tasks"]) for s in sessions),
                    "expected_checks": sum(len(t["leaves"]) for s in sessions for t in s["tasks"]),
                    "original_task_order": True, "sessions": sessions}
        save(out / f"{name}.json", manifest)
    totals = {}
    for name, ids in splits.items():
        totals[name] = {m: summarize([r for r in rows if r["model"] == m and r["session_id"] in ids]) for m in MODELS}
        totals[name]["paired_outcomes_observed"] = dict(collections.Counter(t["paired_outcome_observed"] for t in selected_tasks if t["session_id"] in ids))
    save(out / "baseline-summary.json", {"cohort": COHORT, "baseline_protocol": baseline["protocol"],
        "cautions": ["Qwen is accounting-complete, not fully executed or valid_complete_run", "Do not mix generations or historical recovered scores",
                     "Token usage is observed only; unfinished requests may add unknown cost", "Scoring-ready time is not physical flight time or end-to-end job time"],
        "splits": totals})
    save(out / "tasks.json", selected_tasks)
    save(out / "baseline-tasks.json", [r for r in rows if r["session_id"] in main])
    task_csv = []
    for t in selected_tasks:
        item = {k: t[k] for k in ("session_id", "scene_name", "family", "scene_difficulty", "task_id", "ordinal", "name", "task_difficulty", "template", "generator_category", "expected_checks", "paired_outcome_observed")}
        item.update(split="validation60" if t["session_id"] in splits["validation60"] else "dev240", fast100=t["session_id"] in splits["fast100"],
                    conflicts=t["conflicts"], features=t["features"], mechanism_trace_paths=t["mechanism_trace_paths"])
        for m in MODELS:
            item.update({m + "_" + k: v for k, v in t[m].items()})
        task_csv.append(item)
    csv_save(out / "tasks.csv", task_csv)
    scene_csv = []
    for sid in splits["main300"]:
        part = [t for t in selected_tasks if t["session_id"] == sid]
        item = {"session_id": sid, "scene_name": by_sid[sid]["name"], "family": metadata[sid]["family"], "difficulty": metadata[sid]["difficulty"],
                "split": "validation60" if sid in splits["validation60"] else "dev240", "fast100": sid in splits["fast100"],
                "tasks": 20, "checks": sum(t["expected_checks"] for t in part),
                "drones": len(sources[sid]["drones"]), "targets": len(sources[sid]["targets"]), "obstacles": len(sources[sid]["obstacles"]),
                "reviewed_mechanisms": sorted(f for f in features[sid] if f.startswith("reviewed:")),
                "selection_features": dict(features[sid]), "infrastructure_affected_model_tasks": metadata[sid]["infrastructure_affected"]}
        for m in MODELS:
            summary = summarize([r for r in rows if r["session_id"] == sid and r["model"] == m])
            item[m + "_passed"] = summary["passed"]
            item[m + "_buckets"] = summary["buckets"]
            item[m + "_observed_total_tokens"] = summary["observed_token_usage"]["total_tokens"]["sum"]
            item[m + "_observed_scoring_ready_seconds"] = summary["scoring_ready_seconds"]["sum"]
        scene_csv.append(item)
    csv_save(out / "scenes.csv", scene_csv)
    population = collections.Counter(f for t in tasks for f in t["features"])
    selected = collections.Counter(f for t in selected_tasks for f in t["features"])
    coverage = {"features": {f: {"full_tasks": n, "subset_tasks": selected[f]} for f, n in sorted(population.items())},
                "missing_features": sorted(set(population) - set(selected)),
                "full_template_count": len({t["template"] for t in tasks}), "subset_template_count": len({t["template"] for t in selected_tasks}),
                "generator_categories": dict(collections.Counter(t["generator_category"] for t in selected_tasks)),
                "task_difficulties": dict(collections.Counter(t["task_difficulty"] for t in selected_tasks)),
                "conflict_task_count": sum(bool(t["conflicts"]) for t in selected_tasks),
                "near_miss_either_model_tasks": sum(any(t[m]["bucket"] == "near_miss" for m in MODELS) for t in selected_tasks),
                "zero_multi_either_model_tasks": sum(any(t[m]["bucket"] == "zero_multi" for m in MODELS) for t in selected_tasks),
                "zero_single_either_model_tasks": sum(any(t[m]["bucket"] == "zero_single" for m in MODELS) for t in selected_tasks),
                "infrastructure_affected_either_model_tasks": sum(t["paired_outcome_observed"] == "infrastructure_affected" for t in selected_tasks)}
    save(out / "coverage.json", coverage)
    candidate_records = []
    for scene in plan["sessions"]:
        sid = scene["id"]
        replacement = [sid if (metadata[s]["family"], metadata[s]["difficulty"]) == (metadata[sid]["family"], metadata[sid]["difficulty"]) else s for s in main]
        candidate_records.append({"session_id": sid, "scene_name": scene["name"], "selected": sid in main, **metadata[sid],
                                  "objective_if_replaced_in_cell": objective(replacement), "features": dict(features[sid])})
    save(out / "selection-audit.json", {"algorithm": "Within each cell retain candidates with minimum unscored model-task count; then 32 deterministic seeded starts and coordinate ascent, one scene per cell; capped coverage minus modest cost/infrastructure penalties",
         "seed": SEED, "objective": score, "objective_claim": "heuristic local optimum, not a proof of global optimality",
         "feature_weights_caps": {k: weight_cap(k + ":example") for k in ("reviewed", "signal", "conflict", "failure_endpoint", "failure_category", "failure_template", "category", "template", "bucket", "pair", "control", "runtime")},
         "cost_proxy_only": "Unobserved/provider-error rows use model x difficulty medians solely for selection cost, never for reported outcomes/cost totals",
         "reviewed_cases": [{"session_id": s, "task_id": t, "label": label} for (s, t), label in REVIEWED_CASES.items()],
         "candidates": candidate_records, "splits": splits})
    save(out / "input-sha256.json", {**input_hashes, str(Path(__file__).relative_to(ROOT)): sha(__file__)})
    save(out / "evidence-sha256.json", verified)
    save(out / "verification.json", {"observed_tasks_checker_replayed": replayed, "consumed_evidence_hashes_checked": len(verified),
         "selected_scene_source_bytes_match_benchmark": True, "no_modified_checks_or_task_order": True,
         "main_tasks": 300, "primary_source_accounting_complete": True, "qwen_original_valid_complete_run": False,
         "note": "Infrastructure policy zeros remain distinct from observed failures; no new experiments executed"})
    with zipfile.ZipFile(out / "scenes-main300.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for sid in splits["main300"]:
            info = zipfile.ZipInfo(by_sid[sid]["file"], (2026, 10, 5, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, raw_sources[sid])
    # Core checksum set intentionally excludes documentation, allowing explanatory notes without reselection.
    save(out / "DATA_SHA256SUMS.json", {str(f.relative_to(out)): sha(f) for f in sorted(out.rglob('*')) if f.is_file() and f.suffix != '.md' and f.name not in ('DATA_SHA256SUMS.json', 'selection-spec.json')})
    print(json.dumps({"output": str(out), "splits": splits, "summary": totals["main300"], "coverage": {k: v for k, v in coverage.items() if k not in ('features', 'missing_features')}, "missing_features": coverage["missing_features"]}, ensure_ascii=False, indent=2))


def verify(out):
    sums = read(out / "DATA_SHA256SUMS.json")
    for name, expected in sums.items():
        assert sha(out / name) == expected, name
    splits = {name: read(out / (name + '.json')) for name in ('main300', 'dev240', 'validation60', 'fast100')}
    ids = {}
    with zipfile.ZipFile(ROOT / 'benchmark/benchmark.zip') as z:
        for name, manifest in splits.items():
            ids[name] = {s['id'] for s in manifest['sessions']}
            tids = []
            for scene in manifest['sessions']:
                raw = (out / scene['file']).read_bytes()
                assert raw == z.read(scene['source_file'])
                original = json.loads(raw)
                assert [t['id'] for t in original['tasks']] == [t['id'] for t in scene['tasks']]
                for a, b in zip(original['tasks'], scene['tasks']):
                    assert harness.leaves(a['execution_check_apis']) == b['leaves']
                    assert hashlib.sha256(a['content'].encode()).hexdigest() == b['content_sha256']
                tids.extend(t['id'] for t in scene['tasks'])
            assert len(tids) == len(set(tids)) == manifest['expected_tasks'] == int(re.search(r'\d+', name).group())
    assert ids['dev240'].isdisjoint(ids['validation60'])
    assert ids['dev240'] | ids['validation60'] == ids['main300']
    assert ids['fast100'] <= ids['dev240']
    main = splits['main300']['sessions']
    assert len({(s['family'], re.search(r' (Easy|Intermediate|Moderate|Hard|Extreme) \d+$', s['name']).group(1)) for s in main}) == 15
    assert len(read(out / 'tasks.json')) == 300 and len(read(out / 'baseline-tasks.json')) == 600
    print(f'PASS: {len(sums)} artifact hashes, 300 unique tasks, 15 strata, unchanged scene bytes/checks/order, disjoint and nested splits')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    verify(args.output.resolve()) if args.verify else build(args.output.resolve())
