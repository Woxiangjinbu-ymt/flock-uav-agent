"""Durable, single-writer journals for the planned slots and rental budget."""

from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
import fcntl
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time
import math

from prepare_task_dataset import parse_json


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def atomic_write(path, data):
    """The rename is the commit point; fsync also persists a claim across restarts."""
    path = Path(path)
    with NamedTemporaryFile(dir=path.parent, prefix=".writing-", delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(tmp, path)
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            tmp.unlink(missing_ok=True)


@contextmanager
def locked(run, name):
    with (Path(run) / name).open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_jsonl(path):
    return [parse_json(line) for line in Path(path).read_bytes().splitlines()]


def save_rows(path, rows):
    atomic_write(path, b"".join(json_bytes(row) for row in rows))


class SlotLedger:
    """A slot may leave unstarted exactly once, even after a lost coordinator."""

    def __init__(self, run, plan):
        self.run = Path(run)
        self.plan = list(plan)
        self.planned = {row["slot_id"]: row for row in self.plan}
        if len(self.planned) != len(self.plan):
            raise ValueError("Duplicate planned slot IDs")
        self.path = self.run / "slots.jsonl"
        if any(not key or Path(key).name != key or key in {".", ".."} for key in self.planned):
            raise ValueError("Unsafe slot ID")

    def initialize(self):
        with locked(self.run, ".slots.lock"):
            if self.path.exists():
                return self.read()
            rows = [{**row, "status": "unstarted"} for row in self.plan]
            save_rows(self.path, rows)
            return rows

    def read(self):
        rows = read_jsonl(self.path)
        if [r["slot_id"] for r in rows] != list(self.planned):
            raise ValueError("Slot journal differs from frozen plan")
        for row in rows:
            original = self.planned[row["slot_id"]]
            if any(row.get(k) != v for k, v in original.items()) or row["status"] not in {
                    "unstarted", "started", "scored", "unscored"}:
                raise ValueError(f"Invalid slot journal entry: {row['slot_id']}")
        return rows

    def claim(self, slot_id, backend_sha256):
        with locked(self.run, ".slots.lock"):
            rows = self.read()
            if slot_id not in self.planned:
                raise ValueError("Slot ID is outside this plan")
            fingerprint = self.run / "selected-backend.sha256"
            if not isinstance(backend_sha256, str) or len(backend_sha256) != 64 or any(
                    c not in "0123456789abcdef" for c in backend_sha256):
                raise ValueError("Backend fingerprint must be a SHA-256 digest")
            if fingerprint.exists() and fingerprint.read_text().strip() != backend_sha256:
                raise ValueError("Cannot switch the selected backend after first claim")
            if not fingerprint.exists():
                atomic_write(fingerprint, (backend_sha256 + "\n").encode())
            row = next(r for r in rows if r["slot_id"] == slot_id)
            if row["status"] != "unstarted":
                return False
            row.update(status="started", backend_sha256=backend_sha256)
            save_rows(self.path, rows)
            return True

    def finish(self, slot_id, result):
        if result.get("status") not in {"scored", "unscored"}:
            raise ValueError("A terminal worker result is required")
        if result.get("trial_id") != slot_id:
            raise ValueError("Result belongs to another slot")
        if (result["status"] == "scored" and type(result.get("passed")) is not bool
                or result["status"] == "unscored" and result.get("passed") is not None):
            raise ValueError("Score disagrees with worker terminal state")
        with locked(self.run, ".slots.lock"):
            rows = self.read()
            row = next(r for r in rows if r["slot_id"] == slot_id)
            if row.get("result") == result:
                return
            if row["status"] != "started":
                raise ValueError("Only the owner of a started slot may finalize it once")
            row.update(status=result["status"], result=result)
            save_rows(self.path, rows)


def run_local_slots(run, plan, *, backend_sha256, execute):
    """Crash-safe exercise of the worker protocol on isolated CPU smoke slots."""
    run = Path(run)
    if parse_json((run / "manifest.json").read_bytes()).get("scope") != "student_cpu_regression":
        raise ValueError("Scripted execution requires a separate CPU fixture run")
    with locked(run, ".coordinator.lock"):
        ledger = SlotLedger(run, plan)
        rows = ledger.initialize()
        seals = run / "terminal-results"
        seals.mkdir(exist_ok=True)
        chosen = run / "selected-backend.sha256"
        if chosen.exists() and chosen.read_text().strip() != backend_sha256:
            raise ValueError("Cannot resume a different backend")
        for row in rows:
            slot = row["slot_id"]
            terminal = seals / (slot + ".json")
            if row["status"] in {"scored", "unscored"}:
                if not terminal.exists() or parse_json(terminal.read_bytes()) != row["result"]:
                    raise ValueError("Terminal evidence differs from slot journal")
                continue
            if row["status"] == "started":
                # Never replay an interrupted action. If publication was interrupted
                # after writing the terminal receipt, finish just its journal entry.
                if terminal.exists():
                    result = parse_json(terminal.read_bytes())
                else:
                    result = {"trial_id": slot, "task_id": row["task_id"], "status": "unscored",
                              "passed": None, "unscored_reason": "interrupted_before_terminal_receipt"}
            else:
                if terminal.exists() or not ledger.claim(slot, backend_sha256):
                    raise ValueError("Unclaimed terminal receipt or competing slot owner")
                result = execute(row)
            atomic_write(terminal, json_bytes(result))
            ledger.finish(slot, result)
        return [r["result"] for r in ledger.read()]


def _seconds(value):
    if type(value) is not int or value < 0:
        raise ValueError("Expected nonnegative whole seconds")
    return value


def _money(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Expected a finite nonnegative price")
    return amount


class RentalBudget:
    """Round total rental time up to the provider quantum; reserve shutdown first."""

    max_seconds = 20 * 3600
    max_cny = Decimal("200")

    def __init__(self, run):
        self.run = Path(run)
        self.path = self.run / "cost-ledger.json"

    def initialize(self):
        with locked(self.run, ".budget.lock"):
            if not self.path.exists():
                atomic_write(self.path, json_bytes({"gpu_billed_seconds": 0, "paid_cny": "0",
                             "entries": [], "reservation": None}))
            return parse_json(self.path.read_bytes())

    def reserve(self, name, *, elapsed_seconds, work_seconds, shutdown_seconds,
                billing_seconds, hourly_cny, extra_cny=0, prepaid_terms=None):
        """elapsed_seconds is actual uptime since this lease began, incl. idle time.

        Each entry settled below closes one lease. While a lease remains active,
        revise its outstanding reservation for the next operation.
        """
        elapsed, work, shutdown, quantum = map(_seconds, (
            elapsed_seconds, work_seconds, shutdown_seconds, billing_seconds))
        if not name or work == 0 or quantum == 0:
            raise ValueError("Need a workload and a positive billing quantum")
        price, extra = _money(hourly_cny), _money(extra_cny)
        rounded = ((elapsed + work + shutdown + quantum - 1) // quantum) * quantum
        cost = (price * rounded / 3600 + extra).quantize(Decimal(".01"), rounding=ROUND_CEILING)
        with locked(self.run, ".budget.lock"):
            log = parse_json(self.path.read_bytes())
            current = log["reservation"]
            if any(row["lease"] == name for row in log["entries"]):
                raise ValueError("Duplicate lease ID")
            if current and (current["lease"] != name or elapsed < current["elapsed_seconds"]
                            or current.get("prepaid_terms") != prepaid_terms
                            or price != _money(current.get("hourly_cny", 0)) or quantum != current["billing_seconds"]
                            or extra < _money(current["extra_cny"])):
                raise ValueError("Unsettled lease or inconsistent quote")
            if log["gpu_billed_seconds"] + rounded > self.max_seconds:
                return "gpu_hours_cap"
            if _money(log["paid_cny"]) + cost > self.max_cny:
                return "total_cost_cap"
            log["reservation"] = {"lease": name, "elapsed_seconds": elapsed, "work_seconds": work,
                                  "shutdown_seconds": shutdown, "billing_seconds": quantum,
                                  "hourly_cny": str(price), "extra_cny": str(extra),
                                  "reserved_billed_seconds": rounded, "reserved_cny": str(cost)}
            if prepaid_terms is not None:
                del log["reservation"]["hourly_cny"]
                log["reservation"]["prepaid_terms"] = prepaid_terms
            atomic_write(self.path, json_bytes(log))
            return "admitted"

    def reserve_prepaid(self, quote, work_seconds):
        """Attribute this stage's occupied card time and incremental fee on a prepaid order."""
        started = quote["stage_started_unix"]
        if (quote["billing_mode"] != "prepaid_week" or type(started) not in (int, float)
                or not math.isfinite(started) or started <= 0 or started > time.time()
                or _money(quote["incremental_cny"]) != 0 or _seconds(quote["shutdown_seconds"]) <= 0
                or any(key in quote for key in ("hourly_cny", "started_unix", "extra_cny"))):
            raise ValueError("Invalid prepaid weekly stage terms")
        prior = _seconds(quote["prior_stage_seconds"])
        elapsed = prior + math.ceil(time.time() - started)
        return self.reserve(quote["id"], elapsed_seconds=elapsed, work_seconds=work_seconds,
                            shutdown_seconds=quote["shutdown_seconds"], billing_seconds=1, hourly_cny=0,
                            prepaid_terms=dict(quote))

    def settle_prepaid(self):
        """Finalize one prepaid stage after both calibration and formal work have stopped."""
        with locked(self.run, ".budget.lock"):
            log = parse_json(self.path.read_bytes())
            reserved = log["reservation"]
            if reserved is None:
                if log["entries"] and "prepaid_terms" in log["entries"][-1]:
                    return log["entries"][-1]
                raise ValueError("No prepaid stage to settle")
            terms = reserved.get("prepaid_terms")
            if not terms:
                raise ValueError("Settlement requires a prepaid stage")
            seconds = terms["prior_stage_seconds"] + math.ceil(time.time() - terms["stage_started_unix"])
            if seconds < reserved["elapsed_seconds"]:
                raise ValueError("Prepaid stage clock moved backwards")
            entry = {"lease": reserved["lease"], "billed_seconds": seconds, "charged_cny": "0",
                     "prepaid_terms": terms}
            log["entries"].append(entry)
            log.update(gpu_billed_seconds=log["gpu_billed_seconds"] + seconds, reservation=None)
            atomic_write(self.path, json_bytes(log))
            return entry

    def settle(self, name, *, billed_seconds, charged_cny):
        duration, charge = _seconds(billed_seconds), _money(charged_cny)
        with locked(self.run, ".budget.lock"):
            log = parse_json(self.path.read_bytes())
            entry = {"lease": name, "billed_seconds": duration, "charged_cny": str(charge)}
            past = [v for v in log["entries"] if v["lease"] == name]
            if past:
                if past == [entry]:
                    return
                raise ValueError("Conflicting settlement")
            reserved = log["reservation"]
            if reserved is None or reserved["lease"] != name:
                raise ValueError("Unknown lease")
            if "prepaid_terms" in reserved:
                raise ValueError("Use prepaid stage settlement")
            log["entries"].append(entry)
            log.update(gpu_billed_seconds=log["gpu_billed_seconds"] + duration,
                       paid_cny=str(_money(log["paid_cny"]) + charge), reservation=None)
            atomic_write(self.path, json_bytes(log))


    def record_ancillary(self, fee_id, cny):
        """Record quoted storage/transfer fees before activating a GPU lease."""
        fee = _money(cny)
        if not fee_id:
            raise ValueError("Fee identity required")
        with locked(self.run, ".budget.lock"):
            log = parse_json(self.path.read_bytes())
            fees = log.setdefault("ancillary_fees", {})
            if fee_id in fees:
                if _money(fees[fee_id]) != fee:
                    raise ValueError("Conflicting ancillary fee")
                return "recorded"
            reserve = _money((log["reservation"] or {}).get("reserved_cny", 0))
            if _money(log["paid_cny"]) + reserve + fee > self.max_cny:
                return "total_cost_cap"
            fees[fee_id] = str(fee)
            log["paid_cny"] = str(_money(log["paid_cny"]) + fee)
            atomic_write(self.path, json_bytes(log))
            return "recorded"
