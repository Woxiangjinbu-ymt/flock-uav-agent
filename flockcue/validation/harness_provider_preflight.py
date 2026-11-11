"""Offline-only DeepSeek configuration preflight. No SDK, network or launcher.

The existing plan remains immutable and unauthorized. Secrets are read only to
report presence; neither their values nor a settings-file hash are persisted.
"""
from __future__ import annotations
import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import argparse
from decimal import Decimal, localcontext
import json
import re
from urllib.parse import urlsplit

from prepare_harness_natural_agent import absolute, local, output_path, encoded, file_sha, verify
from harness_cost_admission import CostPolicy

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())
PLAN = "experiments/harness-natural-agent-plan-v1"
RUNNER = "docs/results/harness-natural-runner-20261025"
FACTS = "docs/results/harness-provider-preflight-20261025/public-provider-facts.json"
DEFAULT_OUTPUT = "experiments/harness-provider-preflight-v1"
PROFILE = "deepseek_official"
MODEL = "deepseek-flash"
ZERO_AUTHORIZATION = {"executions": 0, "http_attempts": 0, "tokens": 0, "cost": "0"}
DEFAULT_PROPOSAL = {
    "schema_version": 1, "profile_id": PROFILE, "model": MODEL,
    "historical_temperature": "0.1", "thinking_mode": None, "reasoning_effort": None,
    "max_output_tokens": None, "max_http_attempts_per_trial": None,
    "per_request_timeout_seconds": None, "drain_seconds": None,
    "worker_watchdog_seconds": None, "currency": None,
    "trial_cost_limit": None, "batch_cost_limit": None,
}


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result
    try:
        return json.loads(Path(path).read_bytes(), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (OSError, UnicodeError, ValueError):
        raise ValueError("unreadable_or_invalid_json") from None


def decimal_value(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,12})?", value):
        raise ValueError("invalid_decimal_string")
    return Decimal(value)


def validate_proposal(proposal):
    if not isinstance(proposal, dict) or set(proposal) != set(DEFAULT_PROPOSAL):
        raise ValueError("unexpected_proposal_fields")
    for key in ("schema_version", "profile_id", "model", "historical_temperature"):
        if type(proposal[key]) is not type(DEFAULT_PROPOSAL[key]) or proposal[key] != DEFAULT_PROPOSAL[key]:
            raise ValueError("proposal_outside_fixed_candidate")
    if proposal["thinking_mode"] not in (None, "enabled", "disabled"):
        raise ValueError("invalid_thinking_mode")
    if proposal["reasoning_effort"] not in (None, "low", "high", "max"):
        raise ValueError("invalid_reasoning_effort")
    if proposal["thinking_mode"] != "enabled" and proposal["reasoning_effort"] is not None:
        raise ValueError("effort_requires_enabled_thinking")
    if proposal["currency"] not in (None, "USD", "CNY"):
        raise ValueError("unsupported_currency")
    for key in ("max_output_tokens", "max_http_attempts_per_trial"):
        value = proposal[key]
        maximum = 393216 if key == "max_output_tokens" else 100000
        if value is not None and (type(value) is not int or not 1 <= value <= maximum):
            raise ValueError("invalid_positive_integer_limit")
    for key in ("per_request_timeout_seconds", "drain_seconds", "worker_watchdog_seconds", "trial_cost_limit", "batch_cost_limit"):
        if proposal[key] is not None and decimal_value(proposal[key]) <= 0:
            raise ValueError("nonpositive_proposed_limit")
    if proposal["per_request_timeout_seconds"] is not None and decimal_value(proposal["per_request_timeout_seconds"]) > 500:
        raise ValueError("request_timeout_exceeds_task_window")
    if all(proposal[k] is not None for k in ("drain_seconds", "worker_watchdog_seconds")):
        with localcontext() as context:
            context.prec = 64
            if decimal_value(proposal["worker_watchdog_seconds"]) <= 500 + decimal_value(proposal["drain_seconds"]):
                raise ValueError("watchdog_does_not_cover_task_and_drain")
    return proposal


def public_profile(settings):
    """Strict allowlist, never copy arbitrary profile/configuration fields."""
    try:
        candidate = settings["provider_configs"][PROFILE]
        raw_url = candidate["base_url"]
        if not isinstance(raw_url, str) or raw_url != raw_url.strip() or any(ord(c) < 33 or ord(c) == 127 for c in raw_url):
            raise ValueError()
        parts = urlsplit(raw_url)
        if (parts.scheme != "https" or parts.hostname != "api.deepseek.com"
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or parts.port not in (None, 443)
                or parts.path not in ("", "/", "/v1", "/v1/")
                or candidate["type"] != "openai-compatible"):
            raise ValueError()
        default = candidate.get("default_model") == MODEL
        models = candidate.get("default_models", [])
        advertised = isinstance(models, list) and MODEL in models
        secret = candidate.get("api_key")
        return {"provider_id": PROFILE, "type": "openai-compatible", "model": MODEL,
                "base_url": "https://api.deepseek.com" + parts.path.rstrip("/"),
                "configured_default_matches_candidate": default,
                "candidate_in_configured_models": default or advertised,
                "selected_in_application": settings.get("selected_provider") == PROFILE,
                "credential_present": isinstance(secret, str) and bool(secret.strip()),
                "credential_reference": {"path": "agent4drone/llm_settings.json", "profile_id": PROFILE, "field": "api_key"},
                "credential_validation": "not_attempted", "settings_or_secret_hash": "not_recorded"}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("invalid_official_profile_metadata") from None


def pricing_projection(facts):
    """Take only official Flash peak/cache-miss figures; no currency conversion."""
    try:
        model = next(row for row in facts["documented_models"] if row["id"] == MODEL)
        context = int(model["context_window_tokens_in_documentation_example"])
        if context != 1048576 or model["maximum_output_tokens"] != "393216":
            raise ValueError()
        prices = {}
        for currency in ("USD", "CNY"):
            rows = [r for r in facts["official_direct_pricing"]["rows"] if r["model"] == MODEL and r["currency"] == currency]
            if len(rows) != 1:
                raise ValueError()
            peak = rows[0]["peak"]
            for key in ("input_cache_miss", "output"):
                if decimal_value(peak[key]) <= 0:
                    raise ValueError()
            prices[currency] = {"input_per_million": peak["input_cache_miss"], "output_per_million": peak["output"]}
        if facts["official_direct_pricing"]["unit_tokens"] != "1000000":
            raise ValueError()
        return {"documented_version": model["current_pricing_page_version"],
                "checked_on": facts["retrieved_date_local"], "context_tokens_from_documentation_example": context,
                "context_verified_by_account": False, "maximum_output_tokens": 393216,
                "prices_peak_cache_miss_per_million": prices,
                "scope": "conditional documented official-route token cost; no invoice or hard-cap proof"}
    except (KeyError, TypeError, ValueError, StopIteration):
        raise ValueError("invalid_public_pricing_facts") from None


def report(proposal, profile, facts):
    p = validate_proposal(proposal)
    public = pricing_projection(facts)
    required = [k for k in DEFAULT_PROPOSAL if k not in {"schema_version", "profile_id", "model", "historical_temperature", "reasoning_effort"}]
    missing = [k for k in required if p[k] is None]
    if p["thinking_mode"] == "enabled" and p["reasoning_effort"] is None:
        missing.append("reasoning_effort")
    quotes, budget_issues = {}, []
    if p["max_output_tokens"] is not None:
        for currency, rates in public["prices_peak_cache_miss_per_million"].items():
            # The context ceiling deliberately over-reserves; no local tokenizer
            # estimate, cache hit or off-peak saving is spent in advance.
            policy = CostPolicy(**rates, max_context_tokens=public["context_tokens_from_documentation_example"],
                                max_output_tokens=p["max_output_tokens"], currency=currency,
                                pricing_verified=False, context_verified=False, no_additional_charges_verified=False)
            cost = policy.request_upper_bound
            item = {"request_upper_estimate": str(cost), "currency": currency, "conditional_on_documented_limits": True}
            if p["max_http_attempts_per_trial"] is not None:
                with localcontext() as context:
                    context.prec = 64
                    trial_cost = cost * p["max_http_attempts_per_trial"]
                    pair_cost = 2 * trial_cost
                    full_cost = 14 * trial_cost
                item.update(trial_upper_estimate=str(trial_cost), pair_reservation_estimate=str(pair_cost),
                            fourteen_slot_upper_estimate=str(full_cost))
                if p["currency"] == currency:
                    if p["trial_cost_limit"] is not None and decimal_value(p["trial_cost_limit"]) < trial_cost:
                        budget_issues.append("trial_proposal_cannot_cover_reserved_requests")
                    if p["batch_cost_limit"] is not None and decimal_value(p["batch_cost_limit"]) < pair_cost:
                        budget_issues.append("batch_proposal_cannot_cover_one_full_pair")
            quotes[currency] = item
    else:
        budget_issues.append("output_limit_required_for_cost_estimate")
    wire = {"model": MODEL, "stream": False, "max_retries": 0, "use_responses_api": False,
            "trust_env": False, "max_tokens": p["max_output_tokens"], "thinking": {"type": p["thinking_mode"]}}
    if p["thinking_mode"] == "enabled":
        wire["reasoning_effort"] = p["reasoning_effort"]
    elif p["thinking_mode"] == "disabled":
        wire["temperature"] = p["historical_temperature"]
    gates = ["explicit_online_authorization_absent", "provider_credentials_and_actual_acceptance_unverified",
             "rolling_alias_identity_policy_not_frozen", "online_transport_and_cost_ledger_not_integrated",
             "context_and_billing_assumptions_not_account_verified", "new_execution_manifest_absent"]
    gates.extend("missing_configuration:" + key for key in missing)
    gates.extend(budget_issues)
    if not profile["credential_present"]:
        gates.append("candidate_credential_missing")
    if not profile["candidate_in_configured_models"]:
        gates.append("candidate_not_in_configured_models")
    return {"status": "offline_preflight_only", "online_ready": False,
            "authorization": ZERO_AUTHORIZATION.copy(), "provider_calls": 0, "training_started": False,
            "candidate_configuration_complete": not missing and not budget_issues,
            "missing_configuration": missing, "uncompleted_gates": gates,
            "public_provider_facts": public, "proposed_client_parameters_not_a_sendable_payload": wire,
            "conditional_cost_estimates": quotes, "hard_cost_cap_claimed": False,
            "application_selection_changed": False, "runtime_source_changed": False,
            "pair_policy": "reserve equal full A/F ceilings before either arm; refuse next pair if funds insufficient",
            "stop_policy": {"unknown_usage_timeout_cancel_error": "retain unresolved reservation and stop",
                "archive_partial_unjoined_or_unscored": "stop and preserve partial pair",
                "known_usage_complete_archive_normal_task_failure": "retain result; may finish reserved pair under future frozen rules",
                "automatic_retry_or_resume": False}}


def documents(workspace, proposal):
    # Reverify existing frozen evidence; never edit its historical pending flags.
    verify(workspace, PLAN)
    runner = local(workspace, RUNNER)
    seal = read_json(runner / "artifact-seal.json")
    for name, value in seal["files"].items():
        path = local(runner, name)
        if file_sha(path) != value["sha256"] or path.stat().st_size != value["bytes"]:
            raise ValueError("runner_evidence_changed")
    revalidation = read_json(runner / "revalidation-summary.json")
    if revalidation["passed"] is not True or revalidation["check_count"] != 336:
        raise ValueError("runner_acceptance_not_passed")
    sources = read_json(runner / "delivery-manifest.json")["files"]
    for name, checksum in sources.items():
        if name.startswith("scripts/") and file_sha(local(workspace, name)) != checksum:
            raise ValueError("accepted_runtime_source_changed")
    facts_path = local(workspace, FACTS)
    facts = read_json(facts_path)
    profile = public_profile(read_json(local(workspace, "agent4drone/llm_settings.json")))
    payload = {"proposal.json": proposal, "provider.public.json": profile,
               "preflight.json": report(proposal, profile, facts), "official-facts.json": facts,
               "references.json": {"original_plan_manifest_sha256": file_sha(local(workspace, PLAN + "/manifest.json")),
                   "runner_artifact_seal_sha256": file_sha(runner / "artifact-seal.json"),
                   "runner_revalidation_sha256": file_sha(runner / "revalidation-summary.json"),
                   "public_facts_sha256": file_sha(facts_path),
                   "preflight_sources": {name: file_sha(local(workspace, name)) for name in
                       ("scripts/harness_provider_preflight.py", "scripts/harness_cost_admission.py", "scripts/harness_deepseek_adapter.py")},
                   "settings_hash": "never_recorded"}}
    contents = {name: encoded(value) for name, value in payload.items()}
    import hashlib
    manifest = {"schema_version": 1, "status": "offline_preflight_only", "online_ready": False,
                "authorization": ZERO_AUTHORIZATION.copy(), "provider_calls": 0, "proposed_slots": 14,
                "scope": "configuration and conditional budget checks; no launch entry point",
                "files": {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()}}
    contents["manifest.json"] = encoded(manifest)
    return contents, manifest


def prepare(workspace, output, proposal=None):
    workspace = absolute(workspace)
    target = output_path(workspace, output)
    if target.exists():
        raise FileExistsError("existing_preflight_must_not_be_overwritten")
    data, manifest = documents(workspace, validate_proposal(DEFAULT_PROPOSAL.copy() if proposal is None else proposal))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()
    for name, value in data.items():
        (target / name).write_bytes(value)
    return manifest


def verify_preflight(workspace, output):
    workspace = absolute(workspace)
    target = output_path(workspace, output)
    proposal = validate_proposal(read_json(local(target, "proposal.json")))
    expected, manifest = documents(workspace, proposal)
    if {p.name for p in target.iterdir()} != set(expected):
        raise ValueError("preflight_inventory_changed")
    for name, value in expected.items():
        if local(target, name).read_bytes() != value:
            raise ValueError("preflight_content_or_references_changed")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=Path(DEFAULT_OUTPUT))
    parser.add_argument("--proposal", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "verify" and args.proposal is not None:
            raise ValueError("verify_uses_sealed_proposal")
        value = (prepare(args.workspace, args.output, read_json(args.proposal) if args.proposal else None)
                 if args.action == "prepare" else verify_preflight(args.workspace, args.output))
        print(json.dumps({k: value[k] for k in ("status", "online_ready", "authorization", "provider_calls")}))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Do not print arbitrary settings bytes, URLs, filenames or credentials.
        print(json.dumps({"status": "preflight_rejected", "error_type": type(exc).__name__}))
        raise SystemExit(2)
