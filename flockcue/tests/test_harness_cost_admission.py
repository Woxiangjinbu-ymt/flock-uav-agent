"""Synthetic money and authorization only; this does not exercise a provider."""
import ast
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from harness_cost_admission import CostAdmissionError, CostAdmissionLedger, CostPolicy, decimal_amount


@pytest.fixture
def policy():
    # Synthetic tariffs, deliberately unrelated to any supplier's real prices.
    return CostPolicy("2.00", "4.00", 1000, 100, True, True, True)


def amount(snapshot, key):
    return Decimal(snapshot[key])


def ledger(policy, budget="0.1", *, attempts=2, trial_budget="0.01"):
    book = CostAdmissionLedger(policy, budget)
    book.reserve_pair("pair-1", max_http_attempts=attempts, trial_budget=trial_budget)
    return book


def settle(book, ident, **overrides):
    args = {"input_tokens": 100, "output_tokens": 10, "archive_complete": True}
    args.update(overrides)
    return book.settle_attempt(ident, **args)


@pytest.mark.parametrize("value", [0, 0.1, True, Decimal("1"), "NaN", "Infinity", "-Infinity", "-1", "-0", "+1", "1e-3", " 1", "1 ", "", "1_000", "1."])
def test_money_rejects_non_decimal_nonfinite_signed_or_ambiguous_inputs(value):
    with pytest.raises(ValueError):
        decimal_amount(value)


@pytest.mark.parametrize("value,expected", [("0", Decimal(0)), ("0.00", Decimal(0)), (".125", Decimal("0.125")), ("12.3400", Decimal("12.34"))])
def test_money_accepts_exact_nonnegative_decimal_text(value, expected):
    assert decimal_amount(value) == expected


def test_request_bound_uses_full_provider_context_plus_explicit_output_cap(policy):
    assert policy.request_upper_bound == Decimal("0.0024")
    assert policy.usage_upper_bound(100, 10) == Decimal("0.00024")
    assert policy.snapshot()["hard_cap_claimed"] is False
    assert policy.snapshot()["price_scope"] == "worst_cache_miss_no_discount_no_additional_charge"


@pytest.mark.parametrize("flag", ["pricing_verified", "context_verified", "no_additional_charges_verified"])
def test_unverified_cost_context_or_extra_charges_prevent_admission(policy, flag):
    candidate = replace(policy, **{flag: False})
    book = CostAdmissionLedger(candidate, "10")
    before = book.snapshot()
    with pytest.raises(CostAdmissionError, match="cost_policy_unverified"):
        book.reserve_pair("pair", max_http_attempts=2, trial_budget="1")
    assert book.snapshot() == before
    assert candidate.snapshot()["unverified_reasons"] == [flag]
    assert candidate.snapshot()["hard_cap_claimed"] is False
    assert candidate.snapshot()["bound_supported_by_caller_attestations"] is False


@pytest.mark.parametrize("field,value", [("max_context_tokens", 0), ("max_output_tokens", 0),
    ("max_output_tokens", 1001), ("max_context_tokens", True), ("max_context_tokens", 1000.0),
    ("pricing_verified", 1), ("context_verified", "true"), ("no_additional_charges_verified", None),
    ("input_per_million", "nan"), ("currency", "USD/CNY")])
def test_policy_rejects_invalid_bounds_or_attestation_types(policy, field, value):
    with pytest.raises(ValueError):
        replace(policy, **{field: value})


def test_pair_is_fully_reserved_before_either_arm_and_both_have_equal_resources(policy):
    book = ledger(policy, "0.0096", trial_budget="0.0048")
    snapshot = book.snapshot()
    assert amount(snapshot, "reserved") == Decimal("0.0096")
    assert amount(snapshot, "unallocated_budget") == 0
    a, f = [snapshot["pairs"]["pair-1"]["trials"][arm] for arm in ("A", "F")]
    assert a == f and Decimal(a["initial_reservation"]) == Decimal("0.0048")
    # F may run first: fairness is equal reservation, not a forced execution order.
    book.reserve_attempt("pair-1", "F", "f-1")
    settle(book, "f-1")
    book.finish_trial("pair-1", "F", outcome="success")
    assert Decimal(book.snapshot()["pairs"]["pair-1"]["trials"]["A"]["remaining_reservation"]) == Decimal("0.0048")
    book.reserve_attempt("pair-1", "A", "a-1")
    settle(book, "a-1", outcome="failed")
    book.finish_trial("pair-1", "A", outcome="failed")
    assert book.snapshot()["active_pair"] is None


@pytest.mark.parametrize("batch,trial,reason", [("0.009599", "0.0048", "batch_budget_insufficient"),
                                                ("10", "0.004799", "trial_budget_insufficient")])
def test_insufficient_budget_rejects_whole_pair_atomically_without_topup(policy, batch, trial, reason):
    book = CostAdmissionLedger(policy, batch)
    before = book.snapshot()
    with pytest.raises(CostAdmissionError, match=reason):
        book.reserve_pair("pair", max_http_attempts=2, trial_budget=trial)
    assert book.snapshot() == before


def test_partner_reservation_cannot_be_spent_by_extra_attempt_or_new_pair(policy):
    book = ledger(policy, attempts=1)
    book.reserve_attempt("pair-1", "A", "a")
    settle(book, "a")
    before = book.snapshot()
    with pytest.raises(CostAdmissionError, match="http_attempt_limit"):
        book.reserve_attempt("pair-1", "A", "extra")
    with pytest.raises(CostAdmissionError, match="previous_pair_unfinished"):
        book.reserve_pair("pair-2", max_http_attempts=1, trial_budget="1")
    assert book.snapshot() == before
    book.reserve_attempt("pair-1", "F", "f")
    settle(book, "f")


def test_single_worker_allows_only_one_unsettled_attempt_even_across_arms(policy):
    book = ledger(policy)
    book.reserve_attempt("pair-1", "F", "f")
    before = book.snapshot()
    with pytest.raises(CostAdmissionError, match="serial_attempt_still_unsettled"):
        book.reserve_attempt("pair-1", "A", "a")
    with pytest.raises(CostAdmissionError, match="serial_attempt_still_unsettled"):
        book.finish_trial("pair-1", "F", outcome="success")
    assert book.snapshot() == before


def test_attempt_requires_unique_reservation_and_cannot_settle_twice(policy):
    book = ledger(policy)
    with pytest.raises(CostAdmissionError, match="not_reserved"):
        settle(book, "never-issued")
    book.reserve_attempt("pair-1", "A", "unique")
    settle(book, "unique")
    before = book.snapshot()
    with pytest.raises(CostAdmissionError, match="already_settled"):
        settle(book, "unique")
    with pytest.raises(CostAdmissionError, match="duplicate_attempt_id"):
        book.reserve_attempt("pair-1", "F", "unique")
    assert book.snapshot() == before


def test_complete_known_usage_failure_is_not_an_unknown_cost_or_batch_stop(policy):
    book = ledger(policy)
    book.reserve_attempt("pair-1", "A", "http-failure-known-usage")
    result = settle(book, "http-failure-known-usage", outcome="failed")
    assert result["bill_is_actual"] is False
    assert Decimal(result["settled_conservative_charge"]) == Decimal("0.00024")
    book.finish_trial("pair-1", "A", outcome="failed")
    assert book.snapshot()["stopped"] is False
    book.reserve_attempt("pair-1", "F", "f")
    settle(book, "f")
    book.finish_trial("pair-1", "F", outcome="success")
    assert amount(book.snapshot(), "reserved") == 0
    assert amount(book.snapshot(), "settled_conservative_charge") == Decimal("0.00048")


@pytest.mark.parametrize("overrides,reason", [
    ({"input_tokens": None}, "usage_missing"), ({"output_tokens": None}, "usage_missing"),
    ({"outcome": "timeout"}, "abnormal_outcome:timeout"), ({"outcome": "cancelled"}, "abnormal_outcome:cancelled"),
    ({"outcome": "exception"}, "abnormal_outcome:exception"), ({"outcome": []}, "abnormal_outcome"),
    ({"archive_complete": False}, "partial_archive"), ({"archive_complete": 1}, "partial_archive"),
    ({"input_tokens": -1}, "invalid_or_out_of_bounds_usage"), ({"input_tokens": 1001}, "invalid_or_out_of_bounds_usage"),
    ({"output_tokens": 101}, "invalid_or_out_of_bounds_usage"), ({"input_tokens": True}, "invalid_or_out_of_bounds_usage"),
    ({"input_tokens": 1.0}, "invalid_or_out_of_bounds_usage"), ({"output_tokens": "1"}, "invalid_or_out_of_bounds_usage"),
    ({"observed_charge": "0.00240001"}, "charge_exceeds_reservation"),
    ({"observed_charge": "NaN"}, "invalid_observed_charge"), ({"observed_charge": 0.01}, "invalid_observed_charge")])
def test_unknown_invalid_or_partial_evidence_locks_reservation_and_stops_entire_batch(policy, overrides, reason):
    book = ledger(policy)
    book.reserve_attempt("pair-1", "A", "known")
    settle(book, "known")
    book.reserve_attempt("pair-1", "F", "unsafe")
    before = book.snapshot()
    with pytest.raises(CostAdmissionError, match=reason):
        settle(book, "unsafe", **overrides)
    after = book.snapshot()
    assert after["stopped"] is True and after["attempts"]["unsafe"]["status"] == "locked"
    assert after["reserved"] == before["reserved"] == after["locked_reservation"]
    assert after["settled_conservative_charge"] == before["settled_conservative_charge"]
    for operation in (lambda: book.reserve_attempt("pair-1", "A", "next"),
                      lambda: settle(book, "unsafe"),
                      lambda: book.finish_trial("pair-1", "A", outcome="success"),
                      lambda: book.reserve_pair("pair-2", max_http_attempts=1, trial_budget="1")):
        with pytest.raises(CostAdmissionError, match="batch_stopped"):
            operation()
    assert book.snapshot() == after


@pytest.mark.parametrize("outcome", ["cancelled", "timeout", "exception"])
def test_abnormal_trial_end_never_refunds_unissued_partner_capacity(policy, outcome):
    book = ledger(policy)
    before = book.snapshot()
    with pytest.raises(CostAdmissionError, match="abnormal_trial_outcome"):
        book.finish_trial("pair-1", "A", outcome=outcome)
    after = book.snapshot()
    assert after["reserved"] == before["reserved"] == after["locked_reservation"]


def test_actual_lower_bill_does_not_apply_unverified_cache_discount(policy):
    book = ledger(policy)
    book.reserve_attempt("pair-1", "A", "cached")
    result = settle(book, "cached", observed_charge="0.000001")
    assert Decimal(result["settled_conservative_charge"]) == Decimal("0.00024")
    assert result["bill_is_actual"] is False


def test_observed_charge_within_reservation_is_conservatively_retained(policy):
    book = ledger(policy)
    book.reserve_attempt("pair-1", "A", "rounded")
    result = settle(book, "rounded", observed_charge="0.0003")
    assert Decimal(result["settled_conservative_charge"]) == Decimal("0.0003")


def test_boundary_usage_can_consume_entire_reservation_without_rounding(policy):
    book = ledger(policy, "0.0048", attempts=1, trial_budget="0.0024")
    for arm in ("F", "A"):
        book.reserve_attempt("pair-1", arm, arm)
        settle(book, arm, input_tokens=1000, output_tokens=100)
        book.finish_trial("pair-1", arm, outcome="success")
    snapshot = book.snapshot()
    assert amount(snapshot, "settled_conservative_charge") == Decimal("0.0048")
    assert amount(snapshot, "unallocated_budget") == amount(snapshot, "reserved") == 0
    with pytest.raises(CostAdmissionError, match="batch_budget_insufficient"):
        book.reserve_pair("pair-2", max_http_attempts=1, trial_budget="1")


def test_decimal_arithmetic_is_independent_of_callers_low_precision():
    candidate = CostPolicy("0.123456789123456789", "0.987654321987654321", 1_000_000, 1_000_000, True, True, True)
    expected = Decimal("1.111111111111111110")
    with localcontext() as context:
        context.prec = 3
        assert candidate.request_upper_bound == expected
        book = ledger(candidate, "4.444444444444444440", attempts=2, trial_budget="2.222222222222222220")
        assert amount(book.snapshot(), "reserved") == Decimal("4.444444444444444440")
        assert amount(book.snapshot(), "unallocated_budget") == 0


def test_zero_price_is_valid_synthetic_math_not_authorization():
    candidate = CostPolicy("0", "0.00", 1000, 100, True, True, True)
    book = ledger(candidate, "0", trial_budget="0")
    assert amount(book.snapshot(), "reserved") == 0
    assert book.snapshot()["hard_cap_claimed"] is False


def test_policy_and_snapshot_cannot_accidentally_mutate_live_budget(policy):
    book = ledger(policy)
    before = book.snapshot()
    snapshot = book.snapshot()
    snapshot["batch_budget"] = "999"
    snapshot["pairs"]["pair-1"]["trials"]["A"]["max_http_attempts"] = 999
    assert book.snapshot() == before
    with pytest.raises(FrozenInstanceError): policy.max_context_tokens = 999
    with pytest.raises(AttributeError): book.policy = replace(policy, input_per_million="0")


def test_finished_pair_ids_cannot_be_reused_and_spend_accumulates(policy):
    book = ledger(policy, attempts=1)
    for arm in ("A", "F"):
        book.reserve_attempt("pair-1", arm, arm)
        settle(book, arm)
        book.finish_trial("pair-1", arm, outcome="success")
    before = book.snapshot()
    with pytest.raises(CostAdmissionError, match="duplicate_pair_id"):
        book.reserve_pair("pair-1", max_http_attempts=1, trial_budget="1")
    assert book.snapshot() == before
    book.reserve_pair("pair-2", max_http_attempts=1, trial_budget="0.01")
    assert amount(book.snapshot(), "settled_conservative_charge") == Decimal("0.00048")


def test_module_has_no_sdk_transport_process_or_network_dependencies():
    tree = ast.parse((ROOT / "scripts/harness_cost_admission.py").read_bytes())
    imports = {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imports |= {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert imports <= {"__future__", "copy", "dataclasses", "decimal", "re"}
