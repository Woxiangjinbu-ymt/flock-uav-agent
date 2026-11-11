"""Conservative Decimal reservations for one serial A/F pair at a time.

Pure bookkeeping: no provider, transport, persistence, or online authorization.
Proof flags are caller-supplied attestations, not verification performed here.
Known usage is charged at the worst cache-miss tariff, never at a discount; the
settled amount is a conservative accounting bound, not the supplier's paid bill.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from decimal import Decimal, localcontext
import re


class CostAdmissionError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def decimal_amount(value):
    """Nonnegative finite decimal text; no float, sign, whitespace, or exponent."""
    if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", value):
        raise ValueError("Expected nonnegative finite decimal text")
    result = Decimal(value)
    if not result.is_finite() or result < 0:
        raise ValueError("Expected nonnegative finite decimal text")
    return result


def _integer(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
        raise ValueError("Expected a nonnegative integer" if not positive else "Expected a positive integer")
    return value


def _sum(*values):
    # Local precision prevents caller Decimal context from rounding reservations.
    with localcontext() as context:
        context.prec = max(50, sum(len(v.as_tuple().digits) + abs(v.as_tuple().exponent) for v in values) + 4)
        return sum(values, Decimal(0))


def _multiply(value, count, *, scale=0):
    with localcontext() as context:
        context.prec = max(50, len(value.as_tuple().digits) + abs(value.as_tuple().exponent) + len(str(count)) + 8)
        return (value * Decimal(count)).scaleb(scale)


def _text(value):
    return format(value, "f")


@dataclass(frozen=True)
class CostPolicy:
    input_per_million: str
    output_per_million: str
    max_context_tokens: int
    max_output_tokens: int
    pricing_verified: bool
    context_verified: bool
    no_additional_charges_verified: bool
    currency: str = "USD"

    def __post_init__(self):
        decimal_amount(self.input_per_million)
        decimal_amount(self.output_per_million)
        _integer(self.max_context_tokens, positive=True)
        _integer(self.max_output_tokens, positive=True)
        if self.max_output_tokens > self.max_context_tokens:
            raise ValueError("Output cap exceeds verified context capacity")
        for value in (self.pricing_verified, self.context_verified, self.no_additional_charges_verified):
            if type(value) is not bool:
                raise ValueError("Verification attestations must be booleans")
        if not isinstance(self.currency, str) or not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("Currency must be a three-letter code")

    @property
    def unverified_reasons(self):
        return [name for name in ("pricing_verified", "context_verified", "no_additional_charges_verified")
                if not getattr(self, name)]

    def usage_upper_bound(self, input_tokens, output_tokens):
        _integer(input_tokens); _integer(output_tokens)
        if input_tokens > self.max_context_tokens or output_tokens > self.max_output_tokens:
            raise ValueError("Usage exceeds verified input/output bounds")
        return _sum(_multiply(decimal_amount(self.input_per_million), input_tokens, scale=-6),
                    _multiply(decimal_amount(self.output_per_million), output_tokens, scale=-6))

    @property
    def request_upper_bound(self):
        # Use the entire provider context as the input bound. Even where context
        # includes outputs, adding the explicit output cap remains conservative.
        return self.usage_upper_bound(self.max_context_tokens, self.max_output_tokens)

    def snapshot(self):
        return {"currency": self.currency, "input_per_million": self.input_per_million,
            "output_per_million": self.output_per_million, "max_context_tokens": self.max_context_tokens,
            "max_output_tokens": self.max_output_tokens, "request_upper_bound": _text(self.request_upper_bound),
            "unverified_reasons": self.unverified_reasons,
            "bound_supported_by_caller_attestations": not self.unverified_reasons,
            "price_scope": "worst_cache_miss_no_discount_no_additional_charge",
            "hard_cap_claimed": False, "scope": "accounting_only_not_connected_to_transport"}


class CostAdmissionLedger:
    """Single-worker serial state machine; no automatic retry, top-up or resume."""
    def __init__(self, policy, batch_budget):
        if not isinstance(policy, CostPolicy):
            raise TypeError("Expected CostPolicy")
        self._policy = policy
        self._budget = decimal_amount(batch_budget)
        self._reserved = Decimal(0)
        self._settled = Decimal(0)
        self._pairs = {}
        self._attempts = {}
        self._active_pair = None
        self._active_attempt = None
        self._stop_reason = None

    @property
    def policy(self):
        return self._policy

    def _open(self):
        if self._stop_reason is not None:
            raise CostAdmissionError("batch_stopped:" + self._stop_reason)

    @staticmethod
    def _identifier(value):
        if not isinstance(value, str) or not value or value.strip() != value:
            raise ValueError("Expected a nonempty identifier without surrounding whitespace")

    def _trial(self, pair_id, arm):
        if pair_id != self._active_pair or arm not in {"A", "F"}:
            raise CostAdmissionError("unknown_or_inactive_trial")
        trial = self._pairs[pair_id]["trials"][arm]
        if trial["status"] != "reserved":
            raise CostAdmissionError("trial_finished")
        return trial

    def _stop(self, reason, attempt=None):
        self._stop_reason = reason
        if attempt is not None:
            attempt["status"] = "locked"
            attempt["stop_reason"] = reason
        # All remaining reservations stay locked, including the partner arm.
        # No refund can depend on an unknown, partial, or cancelled request.
        raise CostAdmissionError(reason)

    def reserve_pair(self, pair_id, *, max_http_attempts, trial_budget):
        self._open(); self._identifier(pair_id)
        _integer(max_http_attempts, positive=True)
        budget = decimal_amount(trial_budget)
        if pair_id in self._pairs:
            raise CostAdmissionError("duplicate_pair_id")
        if self._active_pair is not None:
            raise CostAdmissionError("previous_pair_unfinished")
        if self.policy.unverified_reasons:
            raise CostAdmissionError("cost_policy_unverified")
        one = _multiply(self.policy.request_upper_bound, max_http_attempts)
        both = _multiply(one, 2)
        if one > budget:
            raise CostAdmissionError("trial_budget_insufficient")
        available = _sum(self._budget, self._reserved.copy_negate(), self._settled.copy_negate())
        if both > available:
            raise CostAdmissionError("batch_budget_insufficient")
        self._pairs[pair_id] = {"status": "reserved", "trials": {arm: {
            "status": "reserved", "budget": budget, "max_http_attempts": max_http_attempts,
            "initial_reservation": one, "remaining_reservation": one, "attempt_ids": [],
            "settled_conservative_charge": Decimal(0)} for arm in ("A", "F")}}
        self._reserved = _sum(self._reserved, both)
        self._active_pair = pair_id
        return {"pair_id": pair_id, "per_trial_reservation": _text(one), "pair_reservation": _text(both)}

    def reserve_attempt(self, pair_id, arm, attempt_id):
        self._open(); self._identifier(attempt_id)
        trial = self._trial(pair_id, arm)
        if attempt_id in self._attempts:
            raise CostAdmissionError("duplicate_attempt_id")
        if self._active_attempt is not None:
            raise CostAdmissionError("serial_attempt_still_unsettled")
        if len(trial["attempt_ids"]) >= trial["max_http_attempts"]:
            raise CostAdmissionError("http_attempt_limit")
        amount = self.policy.request_upper_bound
        self._attempts[attempt_id] = {"pair_id": pair_id, "arm": arm, "status": "reserved", "reservation": amount}
        trial["attempt_ids"].append(attempt_id)
        self._active_attempt = attempt_id
        return {"attempt_id": attempt_id, "reserved_upper_bound": _text(amount)}

    def settle_attempt(self, attempt_id, *, input_tokens=None, output_tokens=None,
                       archive_complete=False, outcome="completed", observed_charge=None):
        self._open()
        if attempt_id != self._active_attempt or attempt_id not in self._attempts:
            raise CostAdmissionError("attempt_not_reserved_or_already_settled")
        attempt = self._attempts[attempt_id]
        if not isinstance(outcome, str) or outcome not in {"completed", "failed"}:
            self._stop("abnormal_outcome:" + str(outcome), attempt)
        if archive_complete is not True:
            self._stop("partial_archive", attempt)
        if input_tokens is None or output_tokens is None:
            self._stop("usage_missing", attempt)
        try:
            charge = self.policy.usage_upper_bound(input_tokens, output_tokens)
        except ValueError:
            self._stop("invalid_or_out_of_bounds_usage", attempt)
        if observed_charge is not None:
            try:
                observed = decimal_amount(observed_charge)
            except ValueError:
                self._stop("invalid_observed_charge", attempt)
            if observed > attempt["reservation"]:
                self._stop("charge_exceeds_reservation", attempt)
            # Lower reported bills do not introduce cache discounts. A larger
            # observed charge is conservatively retained within the reservation.
            charge = max(charge, observed)
            attempt["observed_charge"] = observed
        if charge > attempt["reservation"]:
            self._stop("charge_exceeds_reservation", attempt)
        trial = self._trial(attempt["pair_id"], attempt["arm"])
        trial["remaining_reservation"] = _sum(trial["remaining_reservation"], attempt["reservation"].copy_negate())
        trial["settled_conservative_charge"] = _sum(trial["settled_conservative_charge"], charge)
        self._reserved = _sum(self._reserved, attempt["reservation"].copy_negate())
        self._settled = _sum(self._settled, charge)
        attempt.update(status="settled", outcome=outcome, input_tokens=input_tokens, output_tokens=output_tokens,
                       settled_conservative_charge=charge, archive_complete=True)
        self._active_attempt = None
        return {"attempt_id": attempt_id, "settled_conservative_charge": _text(charge), "bill_is_actual": False}

    def finish_trial(self, pair_id, arm, *, outcome):
        self._open()
        trial = self._trial(pair_id, arm)
        if self._active_attempt is not None:
            raise CostAdmissionError("serial_attempt_still_unsettled")
        if not isinstance(outcome, str) or outcome not in {"success", "failed"}:
            self._stop("abnormal_trial_outcome:" + str(outcome))
        # Explicit closure may release only never-issued slots. All issued
        # attempts have already supplied valid usage and a complete archive.
        self._reserved = _sum(self._reserved, trial["remaining_reservation"].copy_negate())
        trial.update(status="finished", outcome=outcome, remaining_reservation=Decimal(0))
        pair = self._pairs[pair_id]
        if all(item["status"] == "finished" for item in pair["trials"].values()):
            pair["status"] = "finished"
            self._active_pair = None

    def snapshot(self):
        def encode(value):
            if isinstance(value, Decimal): return _text(value)
            if isinstance(value, dict): return {key: encode(item) for key, item in value.items()}
            if isinstance(value, list): return [encode(item) for item in value]
            return value
        result = {"scope": "single_worker_serial_accounting_only", "hard_cap_claimed": False,
            "policy": self.policy.snapshot(), "batch_budget": self._budget,
            "settled_conservative_charge": self._settled, "reserved": self._reserved,
            "locked_reservation": self._reserved if self._stop_reason is not None else Decimal(0),
            "unallocated_budget": _sum(self._budget, self._reserved.copy_negate(), self._settled.copy_negate()),
            "stopped": self._stop_reason is not None, "stop_reason": self._stop_reason,
            "active_pair": self._active_pair, "active_attempt": self._active_attempt,
            "automatic_retry": False, "automatic_resume": False,
            "pairs": self._pairs, "attempts": self._attempts}
        return encode(copy.deepcopy(result))
