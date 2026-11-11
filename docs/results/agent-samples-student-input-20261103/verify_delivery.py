"""Read-only W3 artifact verification. Writes only to stdout; no provider/model."""
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
REPORT = Path(__file__).resolve().parent
ROOT = REPORT.parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import convert_agent_samples as converter
import student_sample_loader as loader


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(name):
    return json.loads((REPORT / name).read_bytes())


def package(name, anchor):
    folder = REPORT / name
    assert sha(folder / "manifest.json") == anchor == (folder / "manifest.sha256").read_text().strip()
    manifest = read(name + "/manifest.json")
    inventory = {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()}
    assert inventory == set(manifest["files"]) | {"manifest.json", "manifest.sha256"}
    assert not any(p.is_symlink() for p in folder.rglob("*"))
    for file, expected in manifest["files"].items():
        assert sha(folder / file) == expected
    return manifest


def verify():
    conversion_anchor = "9377805432cdcd1041305d2e2e7d689f91686388867acb42da8c1a12bbabf2be"
    input_anchor = "5d548f56ccee87024f20af14720dbda2bbe426f7aaac7c4ceea5ef23f768ab6f"
    conversion = package("conversion-run-01", conversion_anchor)
    student = package("student-input-run-01", input_anchor)
    assert student["source_converter_manifest_sha256"] == conversion_anchor
    plan_file = ROOT / "experiments/agent-samples-v1/source-plan.json"
    assert sha(plan_file) == "a2e317a1261dcf0a86d5b3460219cee58104233b9d9a4874a3c669bd9eb9a689"
    plan = json.loads(plan_file.read_bytes())
    for run in plan["runs"]:
        converter._verify_run(ROOT / run["run"], run)
    snapshot = read("source-manifest.json")
    assert sha(REPORT / "source-manifest.json") == (REPORT / "source-manifest.sha256").read_text().strip()
    for file, expected in snapshot["files"].items():
        assert sha(ROOT / file) == expected == sha(REPORT / "source" / file)
    assert sha(ROOT / "experiments/task-splits-v1/manifest.json") == "4d2da10b0a6ea753a924ec22f6f16ad29d59711a94ecef6fbd013cbadf226f4e"
    _, encoded = loader.load_package(REPORT / "student-input-run-01")
    assert len(encoded) == 61 and all(row["source"]["split"] == "development" and row["training_eligible"] is False for row in encoded)
    audit, loading, context = (read(name) for name in ("sample-audit.json", "loading-smoke.json", "context-diagnostic.json"))
    assert audit["passed"] and audit["counts"] == conversion["counts"] and audit["manifest_sha256"] == conversion_anchor
    assert loading["passed"] and loading["samples"] == 61 and len(loading["batches"]) == 31
    assert loading["package_manifest_sha256"] == input_anchor and loading["loader_sha256"] == sha(ROOT / "scripts/student_sample_loader.py")
    assert context["logical_calls"] == context["http_request_records"] == 98
    assert context["measured_requests"] == 88 and context["unmeasurable_requests"] == 10
    assert context["unmeasurable_reasons"] == {"id_mapping/same_name_parallel_unsupported": 10}
    assert context["source_plan_sha256"] == sha(plan_file)
    for file, expected in context["source_sha256"].items():
        assert sha(ROOT / file) == expected
    assert context["tokenizer_manifest_sha256"] == student["tokenizer_manifest_sha256"]
    reproduction = read("conversion-reproducibility.json")
    assert reproduction["passed"] and not reproduction["changed_files"] and reproduction["original_manifest_sha256"] == reproduction["rebuilt_manifest_sha256"] == conversion_anchor
    gates = read("production-gates.json")
    assert gates["passed"] and len(gates["checks"]) == 2 and all(c["rejected"] for c in gates["checks"])
    review = read("final-review.json")
    assert review["passed"] is True
    tests = {}
    for name, count in (("pytest-core.xml", 150), ("pytest-student.xml", 53)):
        suites = ET.parse(REPORT / name).getroot().findall("testsuite")
        got = {key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
        assert got == {"tests": count, "failures": 0, "errors": 0, "skipped": 0}
        tests[name] = {**got, "sha256": sha(REPORT / name)}
    artifact_names = ["sample-audit.json", "loading-smoke.json", "context-diagnostic.json", "source-manifest.json",
                      "conversion-reproducibility.json", "production-gates.json", "final-review.json", "REPORT_CN.md", "verify_delivery.py"]
    return {"schema": "w3-final-verification-v1", "date": "2026-11-03", "passed": True,
            "scope": "development_sample_conversion_and_student_input_only",
            "conversion_manifest_sha256": conversion_anchor, "student_manifest_sha256": input_anchor,
            "conversion_counts": conversion["counts"], "source_runs_reverified": len(plan["runs"]),
            "source_snapshot_files_match_current": len(snapshot["files"]), "w2_split_manifest_unchanged": True,
            "encoded_samples": len(encoded), "loading_batches": len(loading["batches"]),
            "sequence_tokens": read("student-input-run-01/length-report.json")["sequence_tokens"],
            "context_requests": {k: context[k] for k in ("logical_calls", "http_request_records", "measured_requests", "unmeasurable_requests")},
            "tests": tests, "total_tests_passed": 203,
            "artifact_sha256": {name: sha(REPORT / name) for name in artifact_names},
            "provider_calls": 0, "teacher_training_examples": 0, "model_weights_loaded": False, "optimizer_steps": 0,
            "limitations": ["16k fit applies to 61 retained development sequences; 10 historical requests are template-incompatible",
                            "No framework/GPU training, quality optimality or student capability evaluation",
                            "Production training export/loading disabled; future collection provenance not implemented"]}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
