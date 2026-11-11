"""Verify fixed v2 input artifacts, code and test records; stdout only."""
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
REPORT = Path(__file__).resolve().parent
ROOT = REPORT.parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import student_sample_loader as loader
import student_parallel_corpus as corpus


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(name):
    return json.loads((REPORT / name).read_bytes())


def verify():
    anchor = "e3c8c0a6f56f073014aa1812cb22326ec451f0163f0163201dfb187b0b022795"
    package = REPORT / "run-01"
    assert sha(package / "manifest.json") == anchor
    manifest, rows = loader.load_package(package)
    assert manifest["adapter"] == "qwen3-current-assistant-runtime-history-ids-v2"
    assert len(rows) == 61 and all(row["adapter"] == manifest["adapter"] and row["source"]["split"] == "development"
                                   and row["training_eligible"] is False for row in rows)
    frozen = {name[len("source/"):]: value for name, value in manifest["files"].items() if name.startswith("source/")}
    for name, expected in frozen.items():
        assert sha(ROOT/name) == expected
    new_tests = read("test-source-manifest.json")
    for name, expected in new_tests["files"].items():
        assert sha(ROOT/name) == expected == sha(REPORT/"test-source"/name)
    assert len(corpus.load_requests()) == 98 and len(corpus.load_samples()) == 61
    comparison = read("run-01/comparison.json")
    assert comparison["passed"] and comparison["v2_measured_prompts"] == 98 and comparison["previously_rejected_resolved"] == 10
    assert comparison["encoded_samples"] == 61 and comparison["synthetic_passed"] == comparison["synthetic_cases"] == 12
    assert comparison["paired_sequence_totals"] == {"v1": 736669, "v2": 749200}
    assert comparison["candidate_lengths"]["32768"]["prompt_plus_reserve_fit"] == 98
    assert comparison["candidate_lengths"]["16384"]["prompt_only_fit"] == 92
    loading = read("loading-smoke.json")
    assert loading["passed"] and loading["samples"] == 61 and len(loading["batches"]) == 31
    assert loading["package_manifest_sha256"] == anchor
    assert loading["loader_sha256"] == sha(ROOT/"scripts/student_sample_loader.py")
    old = read("v1-reverification.json")
    assert old["passed"] and old["teacher_training_examples"] == 0 and old["w2_split_manifest_unchanged"]
    review = read("final-review.json")
    assert review["passed"] is True
    suites = ET.parse(REPORT/"pytest.xml").getroot().findall("testsuite")
    tests = {name: sum(int(s.get(name, 0)) for s in suites) for name in ("tests", "failures", "errors", "skipped")}
    assert tests == {"tests": 265, "failures": 0, "errors": 0, "skipped": 0}
    evidence = ["run-01/manifest.json", "run-01/comparison.json", "pytest.xml", "pytest.log", "loading-smoke.json",
                "v1-reverification.json", "final-review.json", "test-source-manifest.json", "REPORT_CN.md", "verify_delivery.py"]
    return {"schema": "student-tool-id-v2-final-verification", "date": "2026-11-03", "passed": True,
            "scope": "local_input_association_not_model_capability", "manifest_sha256": anchor,
            "source_files_match_current": len(frozen), "new_test_sources_match_current": len(new_tests["files"]),
            "comparison": comparison, "tests": tests, "loading_samples": 61, "loading_batches": 31,
            "v1_reverification_passed": True, "teacher_training_examples": 0, "provider_calls": 0,
            "model_weights_loaded": False, "optimizer_steps": 0,
            "evidence_sha256": {name: sha(REPORT/name) for name in evidence},
            "limitations": ["Actual wire receipt order was not reversed; reversed coverage is synthetic",
                            "Parser validates structure/association, not complete business JSON Schema or real tool execution",
                            "No student model inference, production serving, training or GPU feasibility conclusion",
                            "All old failures/development split remain unchanged; 32k plus 2048 headroom is a finite-source diagnosis"]}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
