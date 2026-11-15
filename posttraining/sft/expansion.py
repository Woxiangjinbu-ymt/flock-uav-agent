"""Build the expanded student release from its 431-row parent and audited public decisions."""
import argparse
from collections import Counter
import csv
import gzip
from hashlib import sha256
import io
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
import zipfile

from ..inference import MAX_CONTEXT, MAX_GENERATION
from .adapter import load_tokenizer
from .export import DATASETS, ROLES_SHA, checked_bytes, json_line
from .mix import TOKEN_FIELDS, load_rows, summarize
from .supervision import supervise


PARENT = DATASETS / "sft/qwen35-2b-recovery-verification"
PARENT_SHA = "a981a874e405c4924bae5d0e57a5acf530ba8d1cf61f4d3cef19888fbb2c83ad"
TEACHER = DATASETS / "teacher/2026-11-15-expansion"


def focus(name):
    if name.startswith("Fleet Coordinated Takeoff and Formation") and any(n in name for n in ("4 Drones", "5 Drones")):
        return "4/5 机编队"
    if name.startswith("Fleet Target Sweep"):
        return "机队目标扫掠"
    if "Coverage" in name:
        return "区域覆盖"
    return "其他"


def write_release(stage, teacher=TEACHER):
    base = json.loads(checked_bytes(PARENT / "manifest.json", PARENT_SHA))
    registration = json.loads((teacher / "registration.json").read_bytes())
    report = json.loads((teacher / "audit.json").read_bytes())
    scenes = set(registration["session_ids"])
    inventory = list(csv.DictReader(checked_bytes(DATASETS / "original-benchmark/scene-roles.csv", ROLES_SHA).decode().splitlines()))
    candidates = {r["session_id"] for r in inventory if r["role"] == "expansion_pool"}
    if (len(scenes) != 3 or not scenes <= candidates
            or registration["role_inventory_sha256"] != ROLES_SHA
            or registration["plan_sha256"] != report["plan_sha256"]):
        raise ValueError("Expansion includes an unregistered scene")
    tasks = {}
    source = checked_bytes(DATASETS / "original-benchmark/benchmark.zip", report["benchmark_sha256"])
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        for r in inventory:
            if r["role"] == "baseline_train" or r["session_id"] in scenes:
                for t in json.loads(archive.read(r["source_file"]))["tasks"]:
                    tasks[(r["session_id"], t["id"])] = (r["family"], t["name"])
    episodes = {(e["scene_id"], e["task_id"]): e for e in report["episodes"]}
    if set(episodes) != {k for k in tasks if k[0] in scenes} or len(report["episodes"]) != 60:
        raise ValueError("Incomplete expansion episode inventory")
    decisions = checked_bytes(teacher / "decisions.jsonl", report["decisions_sha256"]).splitlines()
    reviews = [r for r in map(json.loads, decisions) if r["verdict"] == "retain"]
    entries = [{"line": line, "ref": ref} for line, ref in load_rows(PARENT, base)]
    seen = {e["ref"]["train_row_sha256"] for e in entries}
    identities = {(r["session_id"], r["task_id"], r["logical_call_id"]) for r in (e["ref"] for e in entries)}
    tokenizer = load_tokenizer()
    public = "reviewed-public-turns.jsonl.gz"
    with gzip.GzipFile(fileobj=io.BytesIO(checked_bytes(teacher / public, report["public_sha256"]))) as source:
        for index, (wire, review) in enumerate(zip(source, reviews, strict=True), 1):
            key = review["scene_id"], review["task_id"]
            identity = (*key, review["logical_call_id"])
            if (sha256(wire).hexdigest() != review["public_sha256"] or review["public_row"] != index
                    or identity in identities or not episodes[key]["eligible"]):
                raise ValueError(f"Unmatched or duplicate reviewed record {identity}")
            row = json.loads(wire)
            ids, labels, audit = supervise(tokenizer, row["messages"], row["tools"], row["target"])
            if (audit["masked_prompt_tokens"] > MAX_CONTEXT - MAX_GENERATION or any(audit[k] != review["projection"][k]
                    for k in (*TOKEN_FIELDS, "prompt_ids_sha256", "target_ids_sha256"))):
                raise ValueError(f"Changed student projection {identity}")
            line = json_line({"input_ids": ids, "labels": labels})
            digest = sha256(line).hexdigest()
            if digest in seen:
                raise ValueError(f"Duplicate tokenized row {identity}")
            seen.add(digest)
            identities.add(identity)
            ref = {"row": len(entries)+1, "session_id": key[0], "task_id": key[1], "logical_call_id": identity[2],
                   "kind": review["kind"], "exposure_in_epoch": 1, "source_pack_rows": {registration["name"]: index},
                   "source_row_sha256": review["public_sha256"], "train_row_sha256": digest,
                   **{k: audit[k] for k in TOKEN_FIELDS}}
            entries.append({"line": line, "ref": ref})
    if not reviews:
        raise ValueError("No expansion decisions admitted")
    with (stage / "train.jsonl.gz").open("xb") as raw, gzip.GzipFile(
            filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as train, (stage / "index.jsonl").open("xb") as index:
        for entry in entries:
            train.write(entry["line"])
            index.write(json_line(entry["ref"]))
    before = [e["ref"] for e in entries[:base["counts"]["decisions"]]]
    after = [e["ref"] for e in entries]
    write_coverage(stage / "COVERAGE.md", tasks, scenes, before, after)
    manifest = {k: base[k] for k in ("schema", "tokenizer", "scene_roles_sha256", "policy")}
    manifest.update(split="expanded_train", pack=registration["first_sft_release"], parent_manifest_sha256=PARENT_SHA,
                    teacher_release=teacher.name, teacher_audit_sha256=sha256((teacher/"audit.json").read_bytes()).hexdigest(),
                    registration_sha256=sha256((teacher/"registration.json").read_bytes()).hexdigest(),
                    registered_expansion_scenes=sorted(scenes), scene_ids=sorted({r["session_id"] for r in after}),
                    counts=summarize(entries), unique_counts=summarize(entries),
                    files={p.name: {"bytes": p.stat().st_size, "sha256": sha256(p.read_bytes()).hexdigest()}
                           for p in sorted(stage.iterdir())})
    (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def write_coverage(path, tasks, scenes, before, after):
    lines = ["# 431 轮基线与扩充版覆盖", "",
             "分母为原训练侧 240 题及本批新增整场 60 题。每行按独立任务与分词后决策去重。", ""]
    for title, labels in [
        ("重点任务", {k: focus(n) for k, (_, n) in tasks.items()}),
        ("场景任务族", {k: f for k, (f, _) in tasks.items()}),
        ("场景", {k: k[0] for k in tasks}),
        ("题型", {k: re.sub(r" \d+$", "", n) for k, (_, n) in tasks.items()}),
    ]:
        lines += [f"## {title}", "", "| 类别 | 原源题 | 新源题 | 入训题（前 → 后） | 决策（前 → 后） | 净增决策 |",
                  "|---|---:|---:|---:|---:|---:|"]
        for label in sorted(set(labels.values())):
            keys = {k for k, v in labels.items() if v == label}
            orig = sum(k[0] not in scenes for k in keys)
            counts = []
            for refs in (before, after):
                selected = [(r["session_id"], r["task_id"]) for r in refs if (r["session_id"], r["task_id"]) in keys]
                counts.append((len(set(selected)), len(selected)))
            (t0, d0), (t1, d1) = counts
            lines.append(f"| {label} | {orig} | {len(keys)-orig} | {t0} → {t1} | {d0} → {d1} | +{d1-d0} |")
        lines.append("")
    lines += ["## 决策类型", "", "| 类型 | 决策（前 → 后） | 净增 |", "|---|---:|---:|"]
    old, new = (Counter(r["kind"] for r in refs) for refs in (before, after))
    for k in sorted(new):
        lines.append(f"| {k} | {old[k]} → {new[k]} | +{new[k]-old[k]} |")
    path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", required=True, type=Path)
    output = cli.parse_args().output
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    output.parent.mkdir(exist_ok=True, parents=True)
    with TemporaryDirectory(dir=output.parent) as temp:
        stage = Path(temp) / "release"
        stage.mkdir()
        write_release(stage)
        stage.rename(output)
