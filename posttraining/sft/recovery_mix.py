"""Release the 44 reviewed recovery, verification and closing decisions with the unique baseline."""
import argparse
from collections import defaultdict
import csv
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from .adapter import load_tokenizer
from .export import DATASETS, MANIFEST_SHA, RELEASE, ROLES_SHA, checked_bytes, json_line
from .mix import FIRST, FIRST_SHA, TOKEN_FIELDS, load_rows, summarize
from .supervision import supervise


SELECTION = RELEASE / "audit/recovery-verification-44.jsonl"
SELECTION_SHA = "af0bc5b1ec8759022868940b24eacd1eeb8045f1b5ca127f082b3c42b74a040b"
BASE_SHA = "03841132ce00ce44108bf9b2194643b20e2dcefe98dad4a532bc4a7b877253ab"
KINDS = {
    "遇错后改动作": "correction",
    "逐条件终答": "closing",
    "与上述终答配对的末态核验调用": "verification",
}


def recovered_rows(policy):
    teacher = json.loads(checked_bytes(RELEASE / "audit/manifest.json", MANIFEST_SHA))
    seals = {Path(f["path"]).name: f["sha256"] for f in teacher["files"]}
    projection = {
        r["source_training_line"]: r for r in map(json.loads, checked_bytes(
            RELEASE / "audit/training-candidate-qwen-audit.jsonl", seals["training-candidate-qwen-audit.jsonl"]
        ).splitlines())
    }
    selected = list(map(json.loads, checked_bytes(SELECTION, SELECTION_SHA).splitlines()))
    by_line = {r["source_line"]: r for r in selected}
    tokenizer = load_tokenizer()
    rendered = {}
    public = "training-public-turns.jsonl.gz"
    with gzip.GzipFile(fileobj=io.BytesIO(checked_bytes(RELEASE / "public" / public, seals[public]))) as stream:
        for n, wire in enumerate(stream, 1):
            if n not in by_line:
                continue
            review, record = by_line[n], projection[n]
            key = (review["scene_id"], review["task_id"], f"model-{review['turn']}")
            if (key != (record["session_id"], record["task_id"], record["logical_call_id"])
                    or sha256(wire).hexdigest() != record["source_row_sha256"]
                    or review["source_sha256"] != record["source_row_sha256"] or record["status"] != "compatible"):
                raise ValueError(f"Unmatched review or source at training line {n}")
            row = json.loads(wire)
            ids, labels, audit = supervise(tokenizer, row["messages"], row["tools"], row["target"])
            if (any(audit[k] != record[k] for k in ("prompt_ids_sha256", "target_ids_sha256"))
                    or audit["masked_prompt_tokens"] != review["prompt_tokens"]
                    or audit["supervised_tokens"] != review["target_tokens"]
                    or audit["supervised_reasoning_tokens"] != record["target_reasoning_tokens"]
                    or audit["masked_prompt_tokens"] + policy["max_target_tokens"] > policy["max_sequence_tokens"]):
                raise ValueError(f"Native token projection changed at training line {n}")
            line = json_line({"input_ids": ids, "labels": labels})
            rendered[n] = (line, {
                "session_id": key[0], "task_id": key[1], "logical_call_id": key[2],
                "kind": KINDS[review["类型"]], "source_training_line": n,
                "source_row_sha256": review["source_sha256"], "train_row_sha256": sha256(line).hexdigest(),
                **{k: audit[k] for k in TOKEN_FIELDS},
            })
    if len(rendered) != len(selected) or len(selected) != 44:
        raise ValueError("Expected 44 distinct audited decisions")
    for i, r in enumerate(selected, 1):
        line, ref = rendered[r["source_line"]]
        yield line, {**ref, "source_pack_rows": {"recovery_verification_44": i}}


def write_coverage(path, episodes, before, after):
    lines = ["# 扩充前后覆盖", "",
             "题目按场景 ID 和任务 ID 去重；决策按实际训练行去重。分母为 A 折 12 场的 240 道源题。"
             "题型按原任务名称归类，仅合并末尾的实例序号，保留机队规模、任务进度阈值等区别。", ""]
    for title, field in [("场景任务族", "family"), ("场景", "session_id"), ("题型", "name")]:
        groups = defaultdict(set)
        for key, ep in episodes.items():
            label = re.sub(r" \d+$", "", ep[field]) if field == "name" else ep[field]
            groups[label].add(key)
        lines += [f"## {title}", "", "| 类别 | 源题 | 入训题（前 → 后） | 去重决策（前 → 后） | 新增决策 |",
                  "|---|---:|---:|---:|---:|"]
        for label, keys in sorted(groups.items()):
            counts = []
            for refs in (before, after):
                tasks = [(r["session_id"], r["task_id"]) for r in refs if (r["session_id"], r["task_id"]) in keys]
                counts.append((len(set(tasks)), len(tasks)))
            (t0, d0), (t1, d1) = counts
            lines.append(f"| {label} | {len(keys)} | {t0} → {t1} | {d0} → {d1} | +{d1-d0} |")
        lines.append("")
    lines += ["## 决策类型", "", "沿用训练索引的 kind 标签，通用工具决策可能也包含纠错或核验行为。", "",
              "| 类型 | 去重决策（前 → 后） | 覆盖题（前 → 后） |", "|---|---:|---:|"]
    for kind in sorted({r["kind"] for r in after}):
        old, new = ([r for r in refs if r["kind"] == kind] for refs in (before, after))
        lines.append(f"| {kind} | {len(old)} → {len(new)} | "
                     f"{len({r['task_id'] for r in old})} → {len({r['task_id'] for r in new})} |")
    path.write_text("\n".join(lines) + "\n")


def write_release(stage, baseline):
    base = json.loads(checked_bytes(baseline / "manifest.json", BASE_SHA))
    roles = csv.DictReader(checked_bytes(DATASETS / "original-benchmark/scene-roles.csv", ROLES_SHA).decode().splitlines())
    scenes = {r["session_id"] for r in roles if r["role"] == "baseline_train"}
    teacher = json.loads(checked_bytes(RELEASE / "audit/manifest.json", MANIFEST_SHA))
    seal = next(f["sha256"] for f in teacher["files"] if Path(f["path"]).name == "episodes.jsonl")
    episodes = {(r["session_id"], r["task_id"]): r for r in map(json.loads, checked_bytes(
        RELEASE / "audit/episodes.jsonl", seal).splitlines()) if r["role"] == "train" and r["session_id"] in scenes}
    entries = {}
    for line, ref in load_rows(baseline, base):
        entries.setdefault(ref["train_row_sha256"], {"line": line, "ref": ref})
    before = [e["ref"] for e in entries.values()]
    if summarize(list(entries.values())) != base["unique_counts"]:
        raise ValueError("Unique baseline counts changed")
    first = json.loads(checked_bytes(FIRST / "manifest.json", FIRST_SHA))
    historically_trained = {r["train_row_sha256"] for _, r in load_rows(FIRST, first)}
    for line, ref in recovered_rows(base["policy"]):
        digest = ref["train_row_sha256"]
        if digest in entries or digest in historically_trained:
            raise ValueError(f"Reviewed addition already present: {digest}")
        entries[digest] = {"line": line, "ref": ref}
    after = [e["ref"] for e in entries.values()]
    identities = {(r["session_id"], r["task_id"], r["logical_call_id"]) for r in after}
    if len(identities) != len(after) or any(episodes[(s, t)]["semantic_status"] != "verified" for s, t, _ in identities):
        raise ValueError("Duplicate decision identity or ineligible episode")
    with (stage / "train.jsonl.gz").open("xb") as raw, gzip.GzipFile(
            filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=6) as train, (stage / "index.jsonl").open("xb") as index:
        for number, entry in enumerate(entries.values(), 1):
            train.write(entry["line"])
            index.write(json_line({**entry["ref"], "row": number, "exposure_in_epoch": 1}))
    write_coverage(stage / "COVERAGE.md", episodes, before, after)
    manifest = {key: base[key] for key in ("schema", "split", "tokenizer", "scene_roles_sha256", "teacher_release",
                                           "teacher_manifest_sha256", "policy", "scene_ids")}
    counts = summarize(list(entries.values()))
    files = {p.name: {"bytes": p.stat().st_size, "sha256": sha256(p.read_bytes()).hexdigest()} for p in sorted(stage.iterdir())}
    manifest.update(pack="recovery_verification_mix", baseline_manifest_sha256=BASE_SHA,
                    selection_file=str(SELECTION.relative_to(DATASETS)), selection_sha256=SELECTION_SHA,
                    mixture={"exposures_per_unique_decision": 1, "baseline_exposures": base["counts"]["decisions"],
                             "baseline_unique_decisions": len(before), "added_decisions": len(after)-len(before)},
                    counts=counts, unique_counts=counts, files=files)
    (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--baseline", type=Path, required=True, help="Original formal SFT mix built by posttraining.sft.mix")
    cli.add_argument("--output", type=Path, required=True, help="New release directory")
    args = cli.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output.parent) as temp:
        stage = Path(temp) / "release"
        stage.mkdir()
        write_release(stage, args.baseline)
        stage.rename(args.output)
