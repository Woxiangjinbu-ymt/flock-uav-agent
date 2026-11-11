"""Independent comparison assertions and real local ASGI boundary examples."""
import copy
import json
from pathlib import Path
import socket
import sys

import pytest

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "scripts")))
import validate_harness_method as method


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network forbidden in method tests")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture(scope="module")
def package():
    return json.loads(method.DEFAULT_CASES.read_bytes())


@pytest.fixture(scope="module")
def results(package):
    # Each production campaign uses fresh subprocesses; unit tests additionally
    # exercise the functions directly and verify explicit world reset equality.
    return {c["case_id"]: {a: method.execute_case(c, a) for a in method.ARMS}
            for c in package["cases"]}


def test_fixture_denominator_and_fixed_public_scope(package):
    method.validate_case_package(package)
    assert package["case_count"] == 12 and package["planned_trials"] == 36
    assert {c["category"] for c in package["cases"]} == {
        "normal", "locally_repairable", "must_exit", "entry_rejected"}
    assert all(c["revision"] == "observed_target_v7" for c in package["cases"])


@pytest.mark.parametrize("damage", ["duplicate", "count", "trials", "arms", "revision"])
def test_case_package_cannot_hide_or_duplicate_denominator(package, damage):
    bad = copy.deepcopy(package)
    if damage == "duplicate":
        bad["cases"][-1]["case_id"] = bad["cases"][0]["case_id"]
    elif damage == "count":
        bad["case_count"] -= 1
    elif damage == "trials":
        bad["planned_trials"] -= 1
    elif damage == "arms":
        bad["arms"].remove("guarded_macro")
    else:
        bad["revision"] = "legacy"
    with pytest.raises(ValueError):
        method.validate_case_package(bad)


@pytest.mark.parametrize("index", range(12))
def test_declared_three_arm_control_semantics(package, results, index):
    case = package["cases"][index]
    checks = method.compare_results(case, results[case["case_id"]])
    assert all(c["passed"] for c in checks), [c for c in checks if not c["passed"]]


def test_first_failure_handoff_differs_but_identical_resume_trace(package, results):
    for case in package["cases"]:
        if case["category"] != "locally_repairable":
            continue
        rows = results[case["case_id"]]
        assert rows["failure"]["first_return"]["status"] == "paused"
        assert rows["applicability"]["first_return"]["status"] == "completed"
        assert rows["failure"]["first_return"]["after_action"] < len(rows["failure"]["actions"])
        assert rows["failure"]["calls"] == rows["guarded_macro"]["calls"]
        assert rows["failure"]["opportunities"]["scripted_resumes"] == 1


def test_static_obstacle_present_before_policy_observation(package, results):
    case = next(c for c in package["cases"] if c["case_id"].startswith("static-blocked"))
    assert any(o["id"] == "fixed-endpoint-obstacle" for o in case["scene"]["obstacles"])
    row = results[case["case_id"]]["applicability"]
    assert not any(o["id"] == "fixed-endpoint-obstacle" for v in row["public_setup"]["nearby"].values() for o in v["obstacles"])
    blocked = [a for a in row["actions"] if a["public_result"].get("outcome") == "blocked"]
    assert len(blocked) == 1 and blocked[0]["tool"] == "move_to"
    assert "Target position conflicts with obstacle" in blocked[0]["public_result"]["message"]
    assert row["events"] == []  # no transient fault injection


def test_unknown_applied_and_unapplied_both_stop_without_history_rewrite(package, results):
    for applied in (True, False):
        case = next(c for c in package["cases"] if c["event"] == {"kind": "receipt_loss", "applied": applied})
        row = results[case["case_id"]]["applicability"]
        assert len(row["actions"]) == 1 and row["frame"]["cursor"] == 0
        assert row["opportunities"]["unknown_action_receipts"] == 1
        assert row["frame"]["state"]["unknown_effect_observation"]["position"]["z"] == (25 if applied else 0)
        assert row["checker"]["passed"] is False


def test_macro_never_calls_runtime_drive(monkeypatch, package):
    from flockcue.runtime.core import HarnessRuntime
    def forbidden(*args, **kwargs):
        raise AssertionError("macro must not delegate to HarnessRuntime")
    monkeypatch.setattr(HarnessRuntime, "_drive", forbidden)
    row = method.execute_case(package["cases"][0], "guarded_macro")
    assert row["terminal"]["status"] == "completed"


@pytest.mark.parametrize("field", ["calls", "frame", "final_state", "terminal", "opportunities", "checker"])
def test_comparator_detects_independent_behavior_damage(package, results, field):
    case = package["cases"][0]
    rows = copy.deepcopy(results[case["case_id"]])
    row = rows["applicability"]
    if field == "calls":
        row[field][2]["args"]["altitude"] += 1
    elif field == "frame":
        row[field]["cursor"] -= 1
    elif field == "final_state":
        row[field]["tick"] += 1
    elif field == "terminal":
        row[field]["reason"] = "incorrect"
    elif field == "opportunities":
        row[field]["scheduling_pause_returns"] += 1
    else:
        row[field]["passed"] = False
    assert not all(c["passed"] for c in method.compare_results(case, rows))


def test_missing_or_failed_arm_retained_as_failed_comparison(package, results):
    case = package["cases"][0]
    for missing in (True, False):
        rows = copy.deepcopy(results[case["case_id"]])
        if missing:
            rows.pop("failure")
        else:
            rows["failure"] = {"worker_error": "timeout"}
        assert not all(c["passed"] for c in method.compare_results(case, rows))


def test_existing_output_rejected_before_any_worker(tmp_path):
    with pytest.raises(ValueError, match="already exists"):
        method.run_campaign(method.DEFAULT_CASES, tmp_path)


@pytest.mark.parametrize("damage", ["call_boundary", "action_boundary", "first_return"])
def test_wrong_pause_position_rejected_even_with_same_count_and_final_trace(package, results, damage):
    case = next(c for c in package["cases"] if c["case_id"].startswith("static-blocked"))
    rows = copy.deepcopy(results[case["case_id"]])
    failure = rows["failure"]
    if damage == "first_return":
        failure["first_return"]["after_public_call"] += 1
    else:
        field = "after_public_call" if damage == "call_boundary" else "after_action"
        failure["controls"][0][field] += 1
        failure["first_return"][field] += 1
    assert failure["opportunities"]["scheduling_pause_returns"] == 1
    assert failure["calls"] == rows["guarded_macro"]["calls"]
    assert failure["final_state"] == results[case["case_id"]]["failure"]["final_state"]
    assert not all(c["passed"] for c in method.compare_results(case, rows))


def test_caught_network_attempt_is_still_counted(monkeypatch):
    monkeypatch.setattr(method, "NETWORK_ATTEMPTS", [])
    with monkeypatch.context() as patch:
        original = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
        # Ensure restoration after installing the worker's actual blocker.
        patch.setattr(socket.socket, "connect", original[0])
        patch.setattr(socket.socket, "connect_ex", original[1])
        patch.setattr(socket, "create_connection", original[2])
        method.forbid_network()
        try:
            socket.create_connection(("unreachable.invalid", 443))
        except RuntimeError:
            pass
        assert len(method.NETWORK_ATTEMPTS) == 1


def test_semantic_state_normalization_only_removes_declared_request_metadata(package, results):
    raw = copy.deepcopy(results[package["cases"][0]["case_id"]]["guarded_macro"]["final_state"])
    normalized = method.semantic_world_state(raw)
    session = raw["controllers"]["session_controller"]["sessions"][package["cases"][0]["case_id"]]
    assert session["request_history"][0]["request_id"]
    assert session["request_history"][0]["timestamp"]
    before = copy.deepcopy(normalized)
    session["request_history"][0]["request_id"] = "different"
    session["request_history"][0]["timestamp"] = "different"
    assert method.semantic_world_state(raw) == before
    raw["controllers"]["drone_controller"]["drones"]["drone-1"]["position"]["x"] += 1
    assert method.semantic_world_state(raw) != before


def test_underlying_api_trace_drift_is_detected(package, results):
    case = package["cases"][0]
    rows = copy.deepcopy(results[case["case_id"]])
    rows["applicability"]["public_api"][0]["json_body"] = {"unexpected": "data"}
    assert not all(c["passed"] for c in method.compare_results(case, rows))


def sealed_fixture(tmp_path, package, results):
    """Freeze the real direct-test observations without launching more workers."""
    case_bytes = method.canonical(package)
    relative = "experiments/harness-method-v1/method-cases.json"
    source_file = tmp_path / "source" / relative
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(case_bytes)
    sources = {relative: method.sha(case_bytes)}
    workers, checks = [], []
    for case in package["cases"]:
        rows = copy.deepcopy(results[case["case_id"]])
        for arm in method.ARMS:
            index = len(workers) + 1
            row = rows[arm]
            row["worker_pid"] = index
            name = f"trials/{case['case_id']}/{arm}.json"
            method.write_json(tmp_path / name, row)
            workers.append({"case_id": case["case_id"], "arm": arm, "pid": index,
                            "result": name, "returncode": 0, "started_ns": index * 2, "joined_ns": index * 2 + 1})
        checks.extend({"case_id": case["case_id"], **c} for c in method.compare_results(case, rows))
    method.write_json(tmp_path / "plan.json", {"sources": sources,
        "case_package_sha256": method.sha(case_bytes), "planned_trials": 36, "planned_cases": 12,
        "planned": [{"case_id": c["case_id"], "arm": a} for c in package["cases"] for a in method.ARMS]})
    method.write_json(tmp_path / "report.json", {"passed": True, "workers": workers, "checks": checks,
        "planned_cases": 12, "planned_trials": 36, "retained_trials": 36, "completed_workers": 36})
    reseal(tmp_path, sources)


def reseal(path, sources):
    files = {str(p.relative_to(path)): method.sha(p.read_bytes()) for p in path.rglob("*")
             if p.is_file() and p.name not in ("manifest.json", "manifest.sha256")}
    method.write_json(path / "manifest.json", {"schema": method.SCHEMA, "files": files,
                                               "sources": sources, "report_sha256": files["report.json"]})
    (path / "manifest.sha256").write_text(method.sha((path / "manifest.json").read_bytes()) + "\n")


def test_read_only_verify_recomputes_full_denominator(tmp_path, package, results):
    sealed_fixture(tmp_path, package, results)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    verified = method.verify_run(tmp_path)
    assert verified["passed"], [c for c in verified["checks"] if not c["passed"]]
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("damage", ["manifest", "file", "extra", "deleted", "resealed_wrong_pause"])
def test_verify_rejects_tamper_and_resealed_semantic_fault(tmp_path, package, results, damage):
    sealed_fixture(tmp_path, package, results)
    if damage == "manifest":
        (tmp_path / "manifest.sha256").write_text("0" * 64)
    elif damage == "file":
        (tmp_path / "plan.json").write_text("{}")
    elif damage == "extra":
        (tmp_path / "unexpected.txt").write_text("extra")
    elif damage == "deleted":
        (tmp_path / "trials/normal-simple-01/failure.json").unlink()
    else:
        path = tmp_path / "trials/static-blocked-candidate-01/failure.json"
        row = json.loads(path.read_bytes())
        row["controls"][0]["after_action"] += 1
        row["first_return"]["after_action"] += 1
        method.write_json(path, row)
        reseal(tmp_path, json.loads((tmp_path / "manifest.json").read_bytes())["sources"])
    assert method.verify_run(tmp_path)["passed"] is False
