"""Synthetic offline preflight tests: no real settings, SDK, provider or trial.

Only the old plan verifier is replaced. The new documents/prepare/verify path
reads and hashes inert temporary files, including synthetic credential canaries.
"""
import ast
import copy
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import pickle
import runpy
import socket
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import harness_provider_preflight as preflight
import prepare_harness_natural_agent as preparation

SECRET = "SYNTHETIC_ONLY_SECRET_DO_NOT_OUTPUT_91f4"
ROTATED_SECRET = "SYNTHETIC_ONLY_ROTATED_SECRET_7a6d"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else preflight.encoded(value))
    return path


def get(path):
    return json.loads(path.read_bytes())


def settings():
    return {"selected_provider": "qwen_local", "private_note": SECRET,
            "provider_configs": {
                "qwen_local": {"api_key": SECRET + "_QWEN", "base_url": "https://qwen.invalid"},
                "deepseek_official": {"type": "openai-compatible", "base_url": "https://api.deepseek.com",
                    "api_key": SECRET, "default_model": "deepseek-flash",
                    "default_models": ["deepseek-flash"], "extra_private": SECRET}}}


def facts():
    return {"retrieved_date_local": "2026-10-25", "documented_models": [{
        "id": "deepseek-flash", "context_window_tokens_in_documentation_example": "1048576",
        "maximum_output_tokens": "393216", "current_pricing_page_version": "synthetic-version"}],
        "official_direct_pricing": {"unit_tokens": "1000000", "rows": [
            {"model": "deepseek-flash", "currency": "USD", "peak": {
                "input_cache_hit": "0.006", "input_cache_miss": "0.30", "output": "1.20"},
             "off_peak": {"input_cache_miss": "0.15", "output": "0.60"}},
            {"model": "deepseek-flash", "currency": "CNY", "peak": {
                "input_cache_hit": "0.04", "input_cache_miss": "2", "output": "8"},
             "off_peak": {"input_cache_miss": "1", "output": "4"}}]}}


def proposal(**changes):
    value = dict(preflight.DEFAULT_PROPOSAL)
    value.update(changes)
    return value


def complete_proposal(**changes):
    value = proposal(thinking_mode="disabled", max_output_tokens=4096,
        max_http_attempts_per_trial=8, per_request_timeout_seconds="45", drain_seconds="10",
        worker_watchdog_seconds="511", currency="USD", trial_cost_limit="3", batch_cost_limit="6")
    value.update(changes)
    return value


def report(value=None, profile=None):
    return preflight.report(complete_proposal() if value is None else value,
                            preflight.public_profile(settings()) if profile is None else profile, facts())


@pytest.fixture(autouse=True)
def no_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Preflight tests forbid network, process launch and pickle deserialization")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(pickle, "load", forbidden)
    monkeypatch.setattr(pickle, "loads", forbidden)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    put(root / preflight.PLAN / "manifest.json", {"synthetic": True, "authorized_executions": 0})
    runner = root / preflight.RUNNER
    accepted = put(root / "scripts/accepted.py", b"# inert accepted source\n")
    sealed = put(runner / "inert.txt", b"synthetic accepted artifact\n")
    put(runner / "artifact-seal.json", {"files": {"inert.txt": {
        "sha256": digest(sealed.read_bytes()), "bytes": sealed.stat().st_size}}})
    put(runner / "revalidation-summary.json", {"passed": True, "check_count": 336})
    put(runner / "delivery-manifest.json", {"files": {"scripts/accepted.py": digest(accepted.read_bytes())}})
    put(root / preflight.FACTS, facts())
    put(root / "agent4drone/llm_settings.json", settings())
    for name in ("harness_provider_preflight.py", "harness_cost_admission.py", "harness_deepseek_adapter.py"):
        put(root / "scripts" / name, ("# inert source reference: " + name + "\n").encode())
    def old_verify(actual_root, actual_output):
        assert Path(actual_root) == root and actual_output == preflight.PLAN
        assert get(root / preflight.PLAN / "manifest.json")["synthetic"] is True
        return {"online_ready": False}
    monkeypatch.setattr(preflight, "verify", old_verify)
    monkeypatch.setattr(preparation, "verify", old_verify)
    return root


def test_imports_are_stdlib_and_local_only():
    tree = ast.parse(Path(preflight.__file__).read_text())
    modules = {node.module.split(".")[0] for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) and node.module}
    modules |= {alias.name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.Import) for alias in node.names}
    assert modules <= set(sys.stdlib_module_names) | {"prepare_harness_natural_agent", "harness_cost_admission"}
    assert not modules & {"openai", "httpx", "langchain", "requests", "uav_agent"}


def test_current_qwen_selection_is_preserved_and_secret_never_projected():
    original = settings()
    before = copy.deepcopy(original)
    value = preflight.public_profile(original)
    assert original == before and original["selected_provider"] == "qwen_local"
    assert value["provider_id"] == "deepseek_official" and value["model"] == "deepseek-flash"
    assert value["selected_in_application"] is False and value["credential_present"] is True
    assert value["credential_validation"] == "not_attempted"
    assert value["settings_or_secret_hash"] == "not_recorded"
    assert SECRET not in json.dumps(value) and digest(preflight.encoded(original)) not in json.dumps(value)


@pytest.mark.parametrize("url,canonical", [
    ("https://api.deepseek.com", "https://api.deepseek.com"),
    ("https://api.deepseek.com/", "https://api.deepseek.com"),
    ("https://api.deepseek.com/v1/", "https://api.deepseek.com/v1"),
    ("https://api.deepseek.com:443/v1", "https://api.deepseek.com/v1")])
def test_official_urls(url, canonical):
    value = settings()
    value["provider_configs"]["deepseek_official"]["base_url"] = url
    assert preflight.public_profile(value)["base_url"] == canonical


@pytest.mark.parametrize("url", [
    "http://api.deepseek.com", "https://api.deepseek.com.evil.invalid",
    "https://evil.invalid/api.deepseek.com", "https://api.deepseek.com@evil.invalid",
    "https://user:secret@api.deepseek.com", "https://user@api.deepseek.com",
    "https://api.deepseek.com?api_key=" + SECRET, "https://api.deepseek.com/#" + SECRET,
    "https://api.deepseek.com:444", "https://api.deepseek.com/beta",
    "https://api.deepseek.com/v1/../beta", "https://api.deepseek.com/%76%31",
    "https://api.deepseek.com./", "https://127.0.0.1", "file:///api.deepseek.com",
    "https://api.deepseek.com\n", "\thttps://api.deepseek.com", "https://api.deep\tseek.com"])
def test_dangerous_or_outside_profile_url_rejected(url):
    value = settings()
    value["provider_configs"]["deepseek_official"]["base_url"] = url
    with pytest.raises(ValueError, match="^invalid_official_profile_metadata$"):
        preflight.public_profile(value)


@pytest.mark.parametrize("mutation", ["missing", "wrong_type", "profile_list", "url_not_string"])
def test_invalid_profile_rejected(mutation):
    value = settings()
    if mutation == "missing":
        del value["provider_configs"]["deepseek_official"]
    elif mutation == "wrong_type":
        value["provider_configs"]["deepseek_official"]["type"] = "gateway"
    elif mutation == "profile_list":
        value["provider_configs"] = []
    else:
        value["provider_configs"]["deepseek_official"]["base_url"] = 42
    with pytest.raises(ValueError, match="invalid_official_profile_metadata"):
        preflight.public_profile(value)


@pytest.mark.parametrize("secret,present", [(SECRET, True), ("", False), ("  ", False), (None, False), (123, False)])
def test_credential_presence_only(secret, present):
    value = settings()
    value["provider_configs"]["deepseek_official"]["api_key"] = secret
    assert preflight.public_profile(value)["credential_present"] is present


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"schema_version": 1.0}, {"profile_id": "qwen_local"},
    {"model": "deepseek-v4-pro"}, {"historical_temperature": 0.1},
    {"thinking_mode": True}, {"thinking_mode": "auto"}, {"reasoning_effort": "none"},
    {"currency": "EUR"}, {"max_output_tokens": True}, {"max_output_tokens": 4096.0},
    {"max_output_tokens": 0}, {"max_output_tokens": 393217}, {"max_http_attempts_per_trial": True},
    {"max_http_attempts_per_trial": 1.5}, {"max_http_attempts_per_trial": 0},
    {"max_http_attempts_per_trial": 100001}, {"per_request_timeout_seconds": 45},
    {"per_request_timeout_seconds": "500.000000000001"}, {"drain_seconds": "0"},
    {"drain_seconds": True}, {"trial_cost_limit": float("nan")},
    {"trial_cost_limit": "NaN"}, {"batch_cost_limit": "Infinity"},
    {"batch_cost_limit": "1e3"}, {"batch_cost_limit": "-1"},
    {"batch_cost_limit": " 1"}, {"batch_cost_limit": "01"},
    {"authorization": {"executions": 14}}, {"authorized_executions": 14},
    {"online_ready": True}, {"temperature": "0.5"}, {"arm_A": {"max_output_tokens": 8192}}])
def test_invalid_or_authorizing_proposal_rejected(changes):
    with pytest.raises(ValueError):
        preflight.validate_proposal(complete_proposal(**changes))


@pytest.mark.parametrize("value", [{}, [], None])
def test_missing_proposal_schema_rejected(value):
    with pytest.raises(ValueError):
        preflight.validate_proposal(value)


def test_empty_prepare_proposal_does_not_fall_back_to_default(workspace):
    with pytest.raises(ValueError, match="unexpected_proposal_fields"):
        preflight.prepare(workspace, "out", {})
    assert not (workspace / "out").exists()


@pytest.mark.parametrize("mode,effort,complete", [
    (None, None, False), ("disabled", None, True), ("enabled", None, False),
    ("enabled", "low", True), ("enabled", "high", True), ("enabled", "max", True)])
def test_thinking_three_states_and_effective_parameters(mode, effort, complete):
    result = report(complete_proposal(thinking_mode=mode, reasoning_effort=effort))
    wire = result["proposed_client_parameters_not_a_sendable_payload"]
    assert wire["thinking"] == {"type": mode}
    assert result["candidate_configuration_complete"] is complete
    assert ("temperature" in wire) is (mode == "disabled")
    assert ("reasoning_effort" in wire) is (mode == "enabled")
    assert wire["stream"] is False and wire["max_retries"] == 0
    assert wire["trust_env"] is False and wire["use_responses_api"] is False
    assert result["online_ready"] is False and result["authorization"] == preflight.ZERO_AUTHORIZATION


@pytest.mark.parametrize("mode", [None, "disabled"])
def test_effort_cannot_enable_thinking_implicitly(mode):
    with pytest.raises(ValueError, match="effort_requires_enabled_thinking"):
        preflight.validate_proposal(complete_proposal(thinking_mode=mode, reasoning_effort="high"))


def test_output_upper_bound_is_exact():
    assert preflight.validate_proposal(complete_proposal(max_output_tokens=393216))["max_output_tokens"] == 393216


@pytest.mark.parametrize("watchdog", ["500", "509.999999999999", "510"])
def test_watchdog_requires_strict_task_plus_drain_margin(watchdog):
    with pytest.raises(ValueError, match="watchdog_does_not_cover_task_and_drain"):
        preflight.validate_proposal(complete_proposal(worker_watchdog_seconds=watchdog))


def test_decimal_precision_is_local_for_watchdog_and_quotes():
    value = complete_proposal(worker_watchdog_seconds="510.000000000001")
    expected = report(value)
    with localcontext() as context:
        context.prec = 2
        assert preflight.validate_proposal(value) == value
        assert report(value) == expected


def test_conditional_peak_cache_miss_quote_and_equal_pair():
    value = report()
    assert value["candidate_configuration_complete"] is True
    for currency, expected_request in (("USD", Decimal("0.319488")), ("CNY", Decimal("2.12992"))):
        quote = value["conditional_cost_estimates"][currency]
        assert Decimal(quote["request_upper_estimate"]) == expected_request
        assert Decimal(quote["trial_upper_estimate"]) == expected_request * 8
        assert Decimal(quote["pair_reservation_estimate"]) == expected_request * 16
        assert Decimal(quote["fourteen_slot_upper_estimate"]) == expected_request * 112
        assert quote["conditional_on_documented_limits"] is True
    assert value["hard_cost_cap_claimed"] is False
    assert value["public_provider_facts"]["context_verified_by_account"] is False
    assert value["pair_policy"].startswith("reserve equal full A/F ceilings")
    assert value["stop_policy"]["automatic_retry_or_resume"] is False


@pytest.mark.parametrize("changes,issue", [
    ({"trial_cost_limit": "2.555903999999"}, "trial_proposal_cannot_cover_reserved_requests"),
    ({"batch_cost_limit": "5.111807999999"}, "batch_proposal_cannot_cover_one_full_pair")])
def test_insufficient_budget_remains_incomplete(changes, issue):
    value = report(complete_proposal(**changes))
    assert value["candidate_configuration_complete"] is False
    assert issue in value["uncompleted_gates"] and value["online_ready"] is False


def test_exact_pair_budget_can_describe_one_pair_but_never_authorize_fourteen():
    value = report(complete_proposal(trial_cost_limit="2.555904", batch_cost_limit="5.111808"))
    assert value["candidate_configuration_complete"] is True
    assert Decimal(value["conditional_cost_estimates"]["USD"]["fourteen_slot_upper_estimate"]) > Decimal("5.111808")
    assert value["authorization"] == preflight.ZERO_AUTHORIZATION
    assert "explicit_online_authorization_absent" in value["uncompleted_gates"]


def test_missing_configuration_and_missing_candidate_are_explicit():
    value = settings()
    value["provider_configs"]["deepseek_official"].update(api_key="", default_model="old", default_models=[])
    result = report(proposal(), preflight.public_profile(value))
    assert result["candidate_configuration_complete"] is False and result["conditional_cost_estimates"] == {}
    assert {"thinking_mode", "max_output_tokens", "currency", "worker_watchdog_seconds"} <= set(result["missing_configuration"])
    assert {"candidate_credential_missing", "candidate_not_in_configured_models"} <= set(result["uncompleted_gates"])
    assert result["online_ready"] is False and result["provider_calls"] == 0 and result["training_started"] is False


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":Infinity}',
                                b'{"x":-Infinity}', b'not-json', b'\xff'])
def test_json_bad_or_ambiguous_values_rejected(tmp_path, raw):
    path = put(tmp_path / "bad.json", raw)
    with pytest.raises(ValueError, match="unreadable_or_invalid_json"):
        preflight.read_json(path)


def test_prepare_verify_no_secret_no_settings_hash_and_input_immutable(workspace, capsys):
    settings_path = workspace / "agent4drone/llm_settings.json"
    initial = {p.relative_to(workspace).as_posix(): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    result = preflight.prepare(workspace, "out", complete_proposal())
    assert preflight.verify_preflight(workspace, "out") == result
    assert result["online_ready"] is False and result["provider_calls"] == 0
    assert result["authorization"] == {"executions": 0, "http_attempts": 0, "tokens": 0, "cost": "0"}
    assert result["proposed_slots"] == 14
    for name, body in initial.items():
        assert (workspace / name).read_bytes() == body
    output = b"".join(p.read_bytes() for p in (workspace / "out").iterdir())
    assert SECRET.encode() not in output and digest(settings_path.read_bytes()).encode() not in output
    assert digest(SECRET.encode()).encode() not in output
    assert set(p.name for p in (workspace / "out").iterdir()) == set(result["files"]) | {"manifest.json"}
    for name, checksum in result["files"].items():
        assert digest((workspace / "out" / name).read_bytes()) == checksum
    assert capsys.readouterr().out == ""


def test_rotation_same_presence_does_not_invalidate_or_leak(workspace):
    preflight.prepare(workspace, "out")
    old_bytes = {p.name: p.read_bytes() for p in (workspace / "out").iterdir()}
    value = settings()
    value["provider_configs"]["deepseek_official"]["api_key"] = ROTATED_SECRET
    put(workspace / "agent4drone/llm_settings.json", value)
    preflight.verify_preflight(workspace, "out")
    assert {p.name: p.read_bytes() for p in (workspace / "out").iterdir()} == old_bytes
    value["provider_configs"]["deepseek_official"]["api_key"] = ""
    put(workspace / "agent4drone/llm_settings.json", value)
    with pytest.raises(ValueError, match="preflight_content_or_references_changed"):
        preflight.verify_preflight(workspace, "out")
    preflight.prepare(workspace, "absent")
    assert get(workspace / "absent/provider.public.json")["credential_present"] is False
    assert ROTATED_SECRET.encode() not in b"".join(p.read_bytes() for p in (workspace / "absent").iterdir())


def test_prepare_refuses_existing_output_without_touching_it(workspace):
    put(workspace / "out/keep.txt", b"keep")
    with pytest.raises(FileExistsError):
        preflight.prepare(workspace, "out")
    assert list((workspace / "out").iterdir()) == [workspace / "out/keep.txt"]


@pytest.mark.parametrize("target", ["..", "../outside", ".", "nested/../out"])
def test_output_path_traversal_or_workspace_rejected(workspace, target):
    with pytest.raises(ValueError):
        preflight.prepare(workspace, target)


def test_absolute_outside_and_symlink_output_rejected(workspace, tmp_path):
    with pytest.raises(ValueError):
        preflight.prepare(workspace, tmp_path / "outside")
    (workspace / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        preflight.prepare(workspace, "link/out")


@pytest.mark.parametrize("mutation", ["extra", "missing", "changed", "symlink", "reauthorized", "resigned_report", "resigned_proposal"])
def test_inventory_hash_and_resigned_authorization_tamper_rejected(workspace, mutation):
    preflight.prepare(workspace, "out")
    target = workspace / "out"
    if mutation == "extra":
        put(target / "extra.txt", b"extra")
    elif mutation == "missing":
        (target / "official-facts.json").unlink()
    elif mutation == "changed":
        (target / "provider.public.json").write_bytes(b"{}")
    elif mutation == "symlink":
        value = (target / "provider.public.json").read_bytes()
        (target / "provider.public.json").unlink()
        put(workspace / "replacement.json", value)
        (target / "provider.public.json").symlink_to(workspace / "replacement.json")
    elif mutation == "reauthorized":
        value = get(target / "manifest.json")
        value.update(online_ready=True, provider_calls=14, authorization={"executions": 14})
        put(target / "manifest.json", value)
    else:
        name = "preflight.json" if mutation == "resigned_report" else "proposal.json"
        value = get(target / name)
        value["online_ready"] = True
        put(target / name, value)
        manifest = get(target / "manifest.json")
        manifest["files"][name] = digest((target / name).read_bytes())
        put(target / "manifest.json", manifest)
    with pytest.raises(ValueError):
        preflight.verify_preflight(workspace, "out")


@pytest.mark.parametrize("mutation", ["sealed", "source", "acceptance", "new_source", "facts"])
def test_frozen_dependencies_are_rechecked(workspace, mutation):
    preflight.prepare(workspace, "out")
    if mutation == "sealed":
        put(workspace / preflight.RUNNER / "inert.txt", b"changed")
    elif mutation == "source":
        put(workspace / "scripts/accepted.py", b"changed")
    elif mutation == "acceptance":
        put(workspace / preflight.RUNNER / "revalidation-summary.json", {"passed": False, "check_count": 336})
    elif mutation == "new_source":
        put(workspace / "scripts/harness_deepseek_adapter.py", b"changed")
    else:
        value = facts()
        value["retrieved_date_local"] = "2026-10-26"
        put(workspace / preflight.FACTS, value)
    with pytest.raises(ValueError):
        preflight.verify_preflight(workspace, "out")


def test_invalid_inputs_fail_before_output_creation(workspace):
    put(workspace / "agent4drone/llm_settings.json", {"provider_configs": {"unsafe": SECRET}})
    with pytest.raises(ValueError, match="invalid_official_profile_metadata"):
        preflight.prepare(workspace, "not-created")
    assert not (workspace / "not-created").exists()


@pytest.mark.parametrize("action", ["prepare", "verify"])
def test_cli_only_reports_zero_authorization(workspace, monkeypatch, capsys, action):
    if action == "verify":
        preflight.prepare(workspace, "out")
    monkeypatch.setattr(sys, "argv", [preflight.__file__, action, "--workspace", str(workspace), "--output", "out"])
    runpy.run_path(preflight.__file__, run_name="__main__")
    captured = capsys.readouterr()
    value = json.loads(captured.out)
    assert value == {"status": "offline_preflight_only", "online_ready": False,
                     "authorization": preflight.ZERO_AUTHORIZATION, "provider_calls": 0}
    assert SECRET not in captured.out + captured.err


def test_cli_rejection_does_not_echo_credential_or_dangerous_url(workspace, monkeypatch, capsys):
    value = settings()
    value["provider_configs"]["deepseek_official"]["base_url"] += "?api_key=" + SECRET
    put(workspace / "agent4drone/llm_settings.json", value)
    monkeypatch.setattr(sys, "argv", [preflight.__file__, "prepare", "--workspace", str(workspace), "--output", "out"])
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(preflight.__file__, run_name="__main__")
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "preflight_rejected", "error_type": "ValueError"}
    assert SECRET not in captured.out + captured.err and "api.deepseek.com" not in captured.out + captured.err
    assert not (workspace / "out").exists()


def test_cli_has_no_launch_action(workspace, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [preflight.__file__, "run", "--workspace", str(workspace)])
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(preflight.__file__, run_name="__main__")
    assert caught.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
