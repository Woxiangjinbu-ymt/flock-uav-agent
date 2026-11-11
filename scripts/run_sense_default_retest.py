"""Run the registered 29 pairs with the public all-drones sensing default."""
import argparse
from collections import Counter
import importlib.util
import math
from pathlib import Path
import platform
import sys
import tempfile
import time
from unittest.mock import patch

import run_sense_retest as previous
from thinking_events import paired_batch
from thinking_events.campaign import verify_run
from thinking_events.campaign_storage import RentalBudget, SlotLedger, atomic_write, digest, json_bytes, locked, read_jsonl
from thinking_events.paired_admission import read_admission


def load_default(example_path, default_path):
    example = previous.load_example(example_path)
    sys.modules["thinking_events.sense_example_prepare"] = example
    spec = importlib.util.spec_from_file_location("thinking_events.sense_default", default_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return example, module


def prepare(run, args, example):
    bindings = {key: digest(getattr(args, key).read_bytes()) for key in ("helper", "default_helper", "mac_proof", "protocol")}
    bindings["coordinator"] = digest(Path(__file__).read_bytes())
    bindings["previous_coordinator"] = digest(Path(previous.__file__).read_bytes())
    authorized = previous.read(args.mac_proof)
    if (authorized["status"] != "pass" or authorized["default_helper_sha256"] != bindings["default_helper"]
            or authorized["protocol_sha256"] != bindings["protocol"]):
        raise ValueError("Mac evidence did not qualify this specific sensing contract and protocol")
    for kind in ("parse", "simulate"):
        evidence = args.mac_proof.parent / f"mac-{kind}.json"
        data = previous.read(evidence)
        if (digest(evidence.read_bytes()) != authorized[kind + "_sha256"]
                or data["helper_sha256"] != bindings["default_helper"]):
            raise ValueError("Inconsistent Mac qualification files")
        if kind == "simulate" and (not data["compared"] or data["network_attempts"]):
            raise ValueError("Missing offline simulator equivalence")
    if not run.exists():
        with tempfile.TemporaryDirectory(dir=run.parent) as temporary:
            stage = Path(temporary) / "run"
            previous.prepare(stage, args.original, args.single, example, args.helper)
            protocol = previous.read(stage / "retest-protocol.json")
            protocol.update(intervention="registered B + explicit optional all-drones sense default (condition C)",
                            effective_request="unchanged messages and other tools; optional envelope and C description for sense",
                            execution_message="empty argument object for sense materialized as input_json empty object",
                            condition="C", bindings=bindings)
            atomic_write(stage / "retest-protocol.json", json_bytes(protocol))
            stage.rename(run)
    verify_run(run)
    saved = previous.read(run / "retest-protocol.json")
    if (saved["condition"] != "C" or saved["bindings"] != bindings or
            saved["original_slots_sha256"] != digest((args.original / "slots.jsonl").read_bytes())):
        raise ValueError("Different evidence, code, baseline or protocol on resume")
    return saved


class DefaultSenseService(previous.RetestService):
    def request(self, **request):
        if request["slot"] not in self.slots:
            raise ValueError("Unexpected slot")
        tools = request["tools"]
        changed = self.example.effective_tools(tools)
        folder = self.run / "intervention" / request["slot"]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"turn-{request['turn']:02d}.json"
        if path.exists():
            raise ValueError("Duplicate intervention position")
        fields = {"slot": request["slot"], "turn": request["turn"], "seed": request["seed"], "thinking": request["thinking"],
                  "messages_sha256": digest(json_bytes(request["messages"])), "upstream_tools_sha256": digest(json_bytes(tools)),
                  "effective_tools_sha256": digest(json_bytes(changed))}
        if any(t["function"]["name"] == self.example.SENSE for t in changed):
            fields["effective_sense_sha256"] = digest(json_bytes(self.example.sense_function({"tools": changed})))
        response = self.original.request(**{**request, "tools": changed})
        raw = response.get("message")
        execution = self.example.execution_message(raw)
        previous.freeze(path, {**fields, "generated_message": raw, "execution_message": execution})
        return {**response, "message": execution}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("out", "original", "single", "helper", "default-helper", "mac-proof", "protocol", "g3", "models", "prior-retest"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    for key in vars(args):
        setattr(args, key, getattr(args, key).resolve())
    example, default = load_default(args.helper, args.default_helper)
    args.out.mkdir(exist_ok=True, parents=True)
    run = args.out / "run"
    with locked(args.out, ".retest.lock"):
        proto = prepare(run, args, example)
        plan = read_jsonl(run / "plan-slots.jsonl")
        eligible = set(proto["eligible_slot_ids"])
        if len(eligible) != 58 or eligible != {r["slot_id"] for r in plan if r["model"] == previous.MODEL}:
            raise ValueError("Unexpected 2B paired population")
        admission, admission_sha = read_admission(run, previous.CAMPAIGN, "transformers")
        if admission["host"]["node"] != platform.node() or admission["lease"]["billing_mode"] != "prepaid_week":
            raise ValueError("Unexpected qualified host or lease")
        metadata = previous.read(run / "t37" / admission["artifacts"]["metadata_" + previous.MODEL]["path"])
        baseline_open = paired_batch._open_service
        def open_service(run_dir, rows, model, *options):
            if model != previous.MODEL:
                raise ValueError("Unexpected model")
            service = baseline_open(run_dir, rows, model, *options)
            try:
                data = dict(service.metadata)
                source = Path(data.pop("source"))
                expected = dict(metadata)
                original = Path(expected.pop("source"))
                if data != expected or digest(source.read_bytes()) != digest(original.read_bytes()):
                    raise ValueError("Model metadata differs from T37")
                return DefaultSenseService(service, run, default, eligible)
            except BaseException:
                service.close()
                raise
        lease_path = run / "retest-lease.json"
        if not lease_path.exists():
            previous.freeze(lease_path, {**admission["lease"], "stage_started_unix": time.time(),
                                         "prior_stage_seconds": 0, "id": "sense-default-prepaid"})
        terms = previous.read(lease_path)
        budget = RentalBudget(run)
        for path in (args.original, args.prior_retest):
            budget.max_seconds -= previous.read(path / "cost-ledger.json")["gpu_billed_seconds"]
        budget.max_seconds -= math.ceil(previous.read(args.single / "ledger.json")["gpu_seconds"])
        def reserve(_run, _admission, seconds):
            return budget.reserve_prepaid(terms, seconds)
        ledger = SlotLedger(run, plan)
        remaining = sum(r["status"] == "unstarted" for r in ledger.read() if r["slot_id"] in eligible)
        if any(r["status"] != "unstarted" for r in ledger.read() if r["slot_id"] not in eligible):
            raise ValueError("Reference slots cannot enter the default-sense experiment")
        if remaining:
            with patch.object(paired_batch, "_open_service", open_service), patch.object(paired_batch, "_reserve", reserve):
                print(paired_batch.execute_batch(run, args.g3, backend="transformers",
                    student_python=admission["interpreters"]["student"], model_root=args.models, max_new=remaining), flush=True)
        for row in ledger.read():
            if row["status"] == "started":
                receipt = paired_batch._terminal(run, row, "formal")
                atomic_write(run / "terminal-results" / (row["slot_id"] + ".json"), json_bytes(receipt))
                ledger.finish(row["slot_id"], receipt)
        statuses = Counter(r["status"] for r in ledger.read() if r["slot_id"] in eligible)
        if statuses["unstarted"] or statuses["started"]:
            raise RuntimeError(f"Unfinished default-sense retest: {statuses}")
        previous.freeze(run / "retest-complete.json", {"targeted": dict(statuses), "other_model_excluded": 58,
                        "t37_admission_sha256": admission_sha, "lease": budget.settle_prepaid()})
        print(previous.read(run / "retest-complete.json"), flush=True)


if __name__ == "__main__":
    main()
