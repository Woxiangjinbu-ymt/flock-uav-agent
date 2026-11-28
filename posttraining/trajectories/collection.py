"""Merge registered teacher sources by task and export the 51 training scenes."""
import argparse
from collections import Counter
from dataclasses import replace
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from runtime import executor as rt, parallel
from posttraining.sft.adapter import load_tokenizer
from .context_window import PROMPT_LIMIT
from .pilot import SceneSource, export_sources, sealed_source
from .rebuild import file_digest


def verified_prefix(attempt, scene, entry, split):
    sid = scene["id"]
    rt.use_stage(attempt.parent.parent)
    parallel.verify_archive(rt, attempt)
    plan = rt.read_json(attempt / "plan.json")
    record = rt.read_json(attempt / "manifest.json")["scenes"][sid]
    folder = parallel.safe_directory(attempt, record["directory"])
    files = {str(p.relative_to(folder)): file_digest(p) for p in folder.rglob("*") if p.is_file()}
    if record["status"] != "aborted" or parallel.json_digest(rt, files) != entry["files_sha256"]:
        raise ValueError(f"Interrupted scene differs from the registered first pass: {sid}")
    rows = [row for row in rt.read_json(folder / "results.json") if row["status"] == "scored"]
    tasks = scene["tasks"][:len(rows)]
    if [t["id"] for t in tasks] != entry["scored_task_ids"]:
        raise ValueError(f"First-pass prefix differs from the original task order: {sid}")
    parallel.validate_task_evidence(rt, plan, {**scene, "tasks": tasks}, folder, rows)
    return SceneSource(attempt, scene, folder, files, rows, split, entry["files_sha256"])


def merged_scene(first, pilot, supplement, sid, registration, repair):
    if sid in registration["carried_seals"]:
        source = sealed_source(pilot, sid)
        if source.evidence_sha256 != registration["carried_seals"][sid]:
            raise ValueError(f"Carried pilot seal differs: {sid}")
    elif sid not in repair["prefixes"]:
        source = sealed_source(first, sid)
    else:
        replay = sealed_source(supplement, sid)
        prefix = verified_prefix(first, replay.scene, repair["prefixes"][sid], replay.split)
        sources = [prefix, replace(replay, rows=replay.rows[len(prefix.rows):])]
        origin = {"scene_id": sid, "settings_sha256": registration["settings_sha256"], "sources": []}
        for part, kind in zip(sources, ("first_pass_prefix", "supplement")):
            item = part.origin()
            if kind == "first_pass_prefix":
                item["files_sha256"] = item.pop("seal_sha256")
            origin["sources"].append({**item, "kind": kind, "task_ids": [r["task_id"] for r in part.rows]})
        return sources, origin
    return [source], source.origin()


def audit_l1(folder, sources, origin):
    if rt.read_json(folder / "origin.json") != origin:
        raise ValueError(f"L1 provenance differs: {folder.name}")
    episodes = rt.read_json(folder / "episodes.json")
    expected = [(part, row) for part in sources for row in part.rows]
    failed = {part.attempt: parallel.terminal_http_jobs(part.folder) for part in sources}
    scored = []
    for (part, row), ep in zip(expected, episodes, strict=True):
        passed = row["task_pass"] and row["job_id"] not in failed[part.attempt]
        if (ep["task_id"] != row["task_id"] or ep["job_id"] != row["job_id"] or ep["formal_pass"] != passed
                or ep.get("attempt", origin.get("attempt")) != part.attempt.name
                or ep["archive_sha256"] != part.files[str(Path(ep["archive_path"]).relative_to(part.scene["id"]))]):
            raise ValueError(f"L1 episode differs from its scored source: {row['task_id']}")
        scored.append({**row, "attempt": part.attempt.name, "formal_pass": passed})

    counts = Counter(tasks=len(episodes), complete_archives=sum(e["archive_complete"] for e in episodes))
    with gzip.open(folder / "public.jsonl.gz", "rb") as public, (folder / "decisions.jsonl").open() as index:
        for ep in episodes:
            candidates = 0
            for turn in range(1, ep["logical_turns"] + 1):
                row = json.loads(next(index))
                if (row["scene_id"], row["task_id"], row["turn"]) != (folder.name, ep["task_id"], turn):
                    raise ValueError("L1 decision order differs from episode order")
                if "public_sha256" in row and rt.digest(next(public)) != row["public_sha256"]:
                    raise ValueError("L1 public bytes differ from decision index")
                eligible = ep["formal_pass"] and ep["archive_complete"] and not row["wire_issues"] and row["projection"] == "ok"
                if bool(eligible) != row["raw_candidate"]:
                    raise ValueError("L1 eligibility disagrees with score or wire evidence")
                counts["logical_turns"] += 1
                if eligible:
                    candidates += 1
                    counts["candidate_turns"] += 1
                    counts["candidate_fallback_turns"] += row["window"].get("fallback_prompt_limit", PROMPT_LIMIT) > PROMPT_LIMIT
                    counts["candidate_supervised_tokens"] += row["target_tokens"]
                    counts["candidate_prompt_tokens"] += row["window"]["tokens"]
                    counts["candidate_turns_with_accepted_receipts"] += row["receipts_accepted"]
            counts["candidate_tasks"] += candidates > 0
            counts["fully_representable_candidate_tasks"] += candidates > 0 and candidates == ep["logical_turns"]
        if next(public, None) is not None or next(index, None) is not None:
            raise ValueError("Extra L1 rows after the last episode")
    return scored, counts


def collect(first, pilot, supplement, output):
    registration = rt.read_json(rt.COLLECTION_REGISTRATION)
    repair = rt.read_json(rt.REPAIR_REGISTRATION)
    verification = rt.read_json(rt.COLLECTION_REGISTRATION.with_name("supplement-verification.json"))
    if (first.name != repair["first_attempt"] or file_digest(first / "manifest.json") != repair["first_manifest_sha256"]
            or pilot.name != registration["carried_attempt"]
            or supplement.name != verification["attempt"]
            or file_digest(supplement / "manifest.json") != verification["manifest_sha256"]
            or file_digest(supplement / "plan.json") != repair["plan_sha256"]):
        raise ValueError("Attempts differ from the registered cohort")
    for path in (first, pilot, supplement):
        if file_digest(path / "agent-settings.json") != registration["settings_sha256"]:
            raise ValueError(f"Teacher settings changed: {path.name}")
    train = rt.collection_train_roles()
    plan = rt.make_plan(rt.COLLECTION_PLAN, [(n, s) for n, s in rt.canonical_scenes() if s["id"] in train])
    l1 = output / "l1"
    l1.mkdir(parents=True, exist_ok=True)
    totals, files, rows = Counter(), {}, []
    tokenizer = load_tokenizer()
    for scene in plan["sessions"]:
        sid = scene["id"]
        sources, origin = merged_scene(first, pilot, supplement, sid, registration, repair)
        folder = l1 / sid
        if not folder.exists():
            with TemporaryDirectory(dir=output) as temp:
                export_sources(sources, Path(temp), tokenizer, origin)
                Path(temp).rename(folder)
        scores, counts = audit_l1(folder, sources, origin)
        rows.extend(scores)
        totals.update(counts)
        files[sid] = {p.name: file_digest(p) for p in sorted(folder.iterdir())}
        print(sid, counts["candidate_tasks"], counts["candidate_turns"], flush=True)
    if {p.name for p in l1.iterdir()} != set(train):
        raise ValueError("L1 export contains unexpected entries")
    score = rt.summarize(plan, rows, "completed")
    if not score["valid_complete_run"]:
        raise ValueError("Combined scores do not cover the training plan")
    passed = sum(row["formal_pass"] for row in rows)
    strict = sum(row["formal_pass"] and row["continuation_safe"] for row in rows)
    score.update(passed_tasks=passed, pass_rate=passed / len(rows),
                 strict_deployment_passed=strict, strict_deployment_rate=strict / len(rows),
                 passed_checks=sum(leaf["result"] for row in rows for leaf in row["leaves"]),
                 sources=dict(Counter(row["attempt"] for row in rows)))
    rt.save_json(output / "score-rows.json", rows)
    rt.save_json(output / "score.json", score)
    rt.save_json(output / "l1-summary.json", {"scenes": len(files), "counts": totals, "files": files})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("first", "pilot", "supplement", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    collect(*(path.resolve() for path in (args.first, args.pilot, args.supplement, args.output)))


if __name__ == "__main__":
    main()
