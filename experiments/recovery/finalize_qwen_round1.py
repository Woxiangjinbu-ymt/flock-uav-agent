"""Verify and publish this Qwen full attempt, with no model calls or replay."""
import collections
import csv
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import reproduction as r
import reproduction_parallel as rp
from reproduction_metrics import terminal_provider_errors, METRICS_FILENAME
from finalize_round1 import metrics

ATTEMPT = ROOT / "experiments/runs/20261004-142512-full-aca112a7"
OUT = ROOT / "docs/results/qwen38-full-round1"
STATE = ROOT / "experiments/recovery/qwen38-overnight-state.json"
DEEPSEEK = ROOT / "experiments/runs/20261003-143255-full-5c501118"


def publish():
    manifest = r.read_json(ATTEMPT / "manifest.json")
    if manifest["status"] != "completed":
        raise ValueError("Qwen full is not complete; refusing to publish final results")
    with rp.runner_lock(r), rp.attempt_lock(ATTEMPT, exclusive=False):
        manifest = r.read_json(ATTEMPT / "manifest.json")
        if manifest["status"] != "completed":
            raise ValueError("Run changed while acquiring lock")
        plan = r.read_json(ATTEMPT / "plan.json")
        settings, config, _ = r.selected_config()
        if settings["selected_provider"] != "bailian_qwen_flash" or config["default_model"] != "qwen3.8-flash":
            raise ValueError("Qwen configuration is not selected")
        rp.verify_provenance(r, ATTEMPT, plan, settings)
        if (len(plan["sessions"]), plan["expected_tasks"], plan["expected_checks"]) != (75, 1500, 9396):
            raise ValueError("Unexpected full benchmark denominator")
        rows, scene_rows = [], []
        difficulties, families = collections.defaultdict(list), collections.defaultdict(list)
        recovery = []
        for scene in plan["sessions"]:
            rec = manifest["scenes"][scene["id"]]
            directory = rp.safe_directory(ATTEMPT, rec["directory"])
            rp.ensure_stopped(r, directory)
            part = rp.verify_scene(r, ATTEMPT, plan, scene, rec)
            for row in part:
                if terminal_provider_errors(directory / "agent-traces" / METRICS_FILENAME, row["job_id"]):
                    raise ValueError("Selected scene contains a terminal model service failure")
            difficulty = re.search(r" (Easy|Intermediate|Moderate|Hard|Extreme) \d+$", scene["name"]).group(1)
            rows.extend(part)
            difficulties[difficulty].extend(part)
            families[scene["family"]].extend(part)
            scene_rows.append({"session_id": scene["id"], "name": scene["name"], "family": scene["family"],
                               "difficulty": difficulty, "generation": rec["generation"],
                               "directory": rec["directory"], **metrics(part)})
            if rec["generation"] > 1:
                recovery.append({"session_id": scene["id"], "name": scene["name"],
                                 "selected_generation": rec["generation"], "history": rec.get("history", [])})
        summary = r.summarize(plan, rows, "completed")
        if not summary["valid_complete_run"] or rows != r.read_json(ATTEMPT / "results.json") or summary != r.read_json(ATTEMPT / "summary.json"):
            raise ValueError("Final aggregate differs from sealed scene evidence")
        performance = rp._performance(r, ATTEMPT, manifest)
        if performance != r.read_json(ATTEMPT / "performance.json"):
            raise ValueError("Performance aggregate differs from the final telemetry")
        state = r.read_json(STATE) if STATE.exists() else {}
        preserved = state.get("preserved_completed_scenes", {})
        for sid, before in preserved.items():
            after = manifest["scenes"][sid]
            if any(after[k] != before[k] for k in ("directory", "seal_sha256")):
                raise ValueError("An already sealed scene was replaced during overnight recovery")
        rp.verify_archive(r, DEEPSEEK)
        deep_rows = r.read_json(DEEPSEEK / "results.json")
        deep = {(x["session_id"], x["task_id"]): x for x in deep_rows}
        if set(deep) != {(x["session_id"], x["task_id"]) for x in rows}:
            raise ValueError("Qwen and DeepSeek task sets differ")
        paired = collections.Counter()
        for row in rows:
            q, d = row["passed"], deep[(row["session_id"], row["task_id"])]["passed"]
            paired["both_pass" if q and d else "qwen_only" if q else "deepseek_only" if d else "both_fail"] += 1
        report = {"attempt": str(ATTEMPT.relative_to(ROOT)), "verified_at": rp.timestamp(),
                  "status": "complete_verified", "identity_sha256": manifest["identity_sha256"],
                  "configuration": r.read_json(ATTEMPT / "configuration.json"), "protocol": manifest["protocol"],
                  "executor": manifest["executor"], "summary": summary, "metrics": metrics(rows),
                  "scene_difficulty": {k: metrics(v) for k, v in difficulties.items()},
                  "family": {k: metrics(v) for k, v in families.items()}, "scenes": scene_rows,
                  "verification": {"sealed_scenes": 75, "original_checker_replayed_tasks": 1500,
                      "checks": 9396, "summary_and_performance_match": True,
                      "selected_terminal_provider_failures": 0, "preserved_completed_scenes": len(preserved)},
                  "recovery": {"replayed_scenes": recovery, "invocations": manifest["invocations"]},
                  "active_wall_seconds_including_repair": sum(v.get("wall_seconds", 0) for v in manifest["invocations"]),
                  "invocations_without_wall_time": sum("wall_seconds" not in v for v in manifest["invocations"]),
                  "performance": {k: ({a: b for a, b in v.items() if a != "by_job"}
                      if k in ("selected_generations", "all_executed_generations") else v) for k, v in performance.items()},
                  "deepseek_comparison": {"attempt": DEEPSEEK.name, "metrics": metrics(deep_rows), "paired_outcomes": dict(paired)},
                  "failed_checks_by_endpoint": dict(collections.Counter(leaf["definition"]["endpoint"]
                      for row in rows for leaf in row["leaves"] if leaf["result"] is False).most_common())}
        if (OUT / "results.json").exists() and r.read_json(OUT / "results.json")["identity_sha256"] != report["identity_sha256"]:
            raise ValueError("Output directory belongs to another experiment")
        OUT.mkdir(parents=True, exist_ok=True)
        r.save_json(OUT / "results.json", report)
        scene_map = {s["session_id"]: s for s in scene_rows}
        fields = ["session_id", "scene_name", "scene_difficulty", "family", "generation", "task_id", "name",
                  "passed", "passed_checks", "expected_checks", "agent_timed_out"]
        with (OUT / "tasks.csv").open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                scene = scene_map[row["session_id"]]
                writer.writerow({**{k: row[k] for k in ["session_id", "task_id", "name", "family", "passed", "expected_checks", "agent_timed_out"]},
                    "scene_name": scene["name"], "scene_difficulty": scene["difficulty"], "generation": scene["generation"],
                    "passed_checks": sum(leaf["result"] for leaf in row["leaves"])})
        with (OUT / "scenes.csv").open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(scene_rows[0]))
            writer.writeheader()
            writer.writerows(scene_rows)
        pct = lambda n: f"{n * 100:.2f}%"
        q, d = report["metrics"], report["deepseek_comparison"]["metrics"]
        lines = ["# Qwen3.8 Flash 第一轮全量结果（已核验）", "", f"运行：`{ATTEMPT.name}`；核验：{report['verified_at']}。", "",
                 "75 场景、1500 任务、9396 项检查全部有效完成。Agent4Drone，8 workers，每题 500 秒。", "",
                 "| 指标 | Qwen3.8 Flash | deepseek-flash |", "|---|---:|---:|"]
        for label, key in [("Task Pass", "task_pass"), ("Avg. Check", "avg_check"), ("Global Check", "global_check"), ("Total Failed", "total_failed")]:
            lines.append(f"| {label} | {pct(q[key])} | {pct(d[key])} |")
        lines += ["", f"Qwen 通过 {q['passed_tasks']}/1500；Agent 超时 {q['agent_timed_out_tasks']} 条，其中 {summary['timed_out_but_passed_tasks']} 条仍通过。", "",
                  "| 场景难度 | 通过 / 任务 | Task Pass | Global Check |", "|---|---:|---:|---:|"]
        for level in ("Easy", "Intermediate", "Moderate", "Hard", "Extreme"):
            v = report["scene_difficulty"][level]
            lines.append(f"| {level} | {v['passed_tasks']}/{v['tasks']} | {pct(v['task_pass'])} | {pct(v['global_check'])} |")
        lines += ["", "| 任务族 | 通过 / 任务 | Task Pass | Global Check |", "|---|---:|---:|---:|"]
        for name, v in report["family"].items():
            lines.append(f"| {name} | {v['passed_tasks']}/{v['tasks']} | {pct(v['task_pass'])} | {pct(v['global_check'])} |")
        lines += ["", f"累计活跃运行时间（含补跑）：{report['active_wall_seconds_including_repair']/3600:.2f} 小时；调用间空档另见 invocations。",
                  f"发生整场恢复的场景数：{len(recovery)}。全部旧版本和失败证据保留；完整场景没有为了提升成绩重跑。", "",
                  "正式性能统计采用最终选定场景版本，实际开销采用所有执行版本。JSON 同时保留这两个口径的调用量、token、HTTP 错误、重试和资源指标。", "",
                  "Qwen 默认思考开启，接口适配器原样回传 reasoning_content。客户端请求温度为 0.1；百炼文档说明思考模式下低于 0.6 会调整为 0.6。不能声称两个服务的有效采样参数完全相同或本结果等同历史论文复现。", "",
                  "[任务明细](tasks.csv) · [场景明细](scenes.csv) · [完整结果与恢复证据](results.json)"]
        if report["invocations_without_wall_time"]:
            lines += ["", "部分调用因异常退出未记录 wall_seconds；以上累计活跃时间是已记录部分的下界，不能作为完整耗时。"]
        (OUT / "REPORT_CN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        r.save_json(OUT / "SHA256SUMS.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(OUT.iterdir()) if p.is_file() and p.name != "SHA256SUMS.json"})
        state.update(status="complete_verified", completed_at=rp.timestamp(), output=str(OUT.relative_to(ROOT)))
        r.save_json(STATE, state)
        print(json.dumps({"output": str(OUT), "summary": summary, "deepseek_paired_outcomes": dict(paired)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        publish()
    except (ValueError, OSError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(2)
