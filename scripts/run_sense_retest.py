"""Run only the original 58 Qwen3.5-2B episodes with the registered sense description.

Use the same agent interpreter and frozen coordinator checkout as the original A100 run.
The worker retains the pre-intervention SDK body. The public service boundary records
the effective tools for replay before passing the response back to the original worker.
"""
from collections import Counter
import argparse
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import time
import tempfile
from unittest.mock import patch

from thinking_events import paired_batch as batch
from thinking_events.campaign import verify_run
from thinking_events.campaign_storage import SlotLedger, RentalBudget, atomic_write, digest, json_bytes, locked, read_jsonl
from thinking_events.paired_admission import read_admission

MODEL = "Qwen3.5-2B"
CAMPAIGN = "ba8b8e2d10bdc54af7c5ff97c6919f5b2727a50de7e68db80efb9c7f67f5d2d6"
EXAMPLE_PLAN = "5eb2e7cfb9f5fa1e681cb15c8fd04e99044680e1d076df2f03f8f741b56c3029"


def read(path):
    return json.loads(path.read_bytes())


def freeze(path, value):
    raw = json_bytes(value)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError(f"Retest binding changed: {path}")
    if not path.exists():
        atomic_write(path, raw)


def load_example(path):
    spec = importlib.util.spec_from_file_location("thinking_events.sense_example_prepare", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(run, original, single, example, helper_path):
    if run == original or run.is_relative_to(original):
        raise ValueError("The retest must use a separate directory")
    if (original / "campaign.sha256").read_text().strip() != CAMPAIGN:
        raise ValueError("Unexpected original campaign")
    checked = read(single / "plan.json")
    if digest((single / "plan.json").read_bytes()) != EXAMPLE_PLAN:
        raise ValueError("Unexpected single-turn comparison")
    pairs = {}
    for row in checked["slots"]:
        path = single / row["request_path"]
        if digest(path.read_bytes()) != row["request_sha256"]:
            raise ValueError("Damaged reference request")
        pairs.setdefault(row["pair_index"], {})[row["condition"]] = read(path)
    for pair in pairs.values():
        if example.ordered_bytes(example.variant(pair["A"])) != example.ordered_bytes(pair["B"]):
            raise ValueError("The effective intervention differs from the B reference")
    if len(pairs) != 50:
        raise ValueError("Expected all 50 approved A/B examples")
    if not run.exists():
        with tempfile.TemporaryDirectory(dir=run.parent) as temporary:
            stage = Path(temporary) / "run"
            stage.mkdir()
            for name in ("study", "source", "t37"):
                shutil.copytree(original / name, stage / name)
            for name in ("campaign.json", "campaign.sha256", "plan-slots.jsonl"):
                shutil.copy2(original / name, stage / name)
            SlotLedger(stage, read_jsonl(stage / "plan-slots.jsonl")).initialize()
            RentalBudget(stage).initialize()
            verify_run(stage)
            stage.rename(run)
    verify_run(run)
    plan = read_jsonl(run / "plan-slots.jsonl")
    eligible = [row for row in plan if row["model"] == MODEL]
    if eligible != plan[:58] or len(eligible) != 58 or Counter(r["thinking"] for r in eligible) != {True:29, False:29}:
        raise ValueError("Expected the first 58 original paired slots")
    protocol = {"model": MODEL, "original_campaign_sha256": CAMPAIGN,
                "original_slots_sha256": digest((original / "slots.jsonl").read_bytes()),
                "original_plan_sha256": digest((original / "plan-slots.jsonl").read_bytes()),
                "example_plan_sha256": EXAMPLE_PLAN, "example_descriptions": example.DESCRIPTIONS,
                "description_helper_sha256": digest(helper_path.read_bytes()),
                "coordinator_sha256": digest(Path(__file__).read_bytes()),
                "eligible_slot_ids": [row["slot_id"] for row in eligible], "reference_pairs_matched": len(pairs),
                "intervention": "change only public sense tool description at the model request boundary",
                "original_sdk_body": "trials/<slot>/policy/modelio; before description change",
                "effective_request": "same public messages and tools, with registered B description"}
    freeze(run / "retest-protocol.json", protocol)
    return plan


class RetestService:
    def __init__(self, original, run, example, slots):
        self.original = original
        self.run = run
        self.example = example
        self.slots = slots
        self.metadata = original.metadata
        self.process = original.process

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.original.close()

    def cancel(self):
        self.original.cancel()

    def request(self, **request):
        if request["slot"] not in self.slots:
            raise ValueError("A slot outside the B retest attempted generation")
        example = self.example
        tools = request["tools"]
        present = any(t["function"]["name"] == example.SENSE for t in tools)
        changed = example.variant({"tools": tools})["tools"] if present else tools
        row = {"slot": request["slot"], "turn": request["turn"], "seed": request["seed"],
               "thinking": request["thinking"], "sense_offered": present,
               "messages_sha256": digest(example.ordered_bytes(request["messages"])),
               "upstream_tools_sha256": digest(example.ordered_bytes(tools)),
               "effective_tools_sha256": digest(example.ordered_bytes(changed))}
        folder = self.run / "intervention" / request["slot"]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"turn-{request['turn']:02d}.json"
        if path.exists():
            raise ValueError("Duplicate intervention position")
        freeze(path, row)
        response = self.original.request(**{**request, "tools": changed})
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("out", "original", "single", "helper", "g3", "models"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    op, original, single, helper, g3, models = (getattr(args, n).resolve() for n in
                                               ("out", "original", "single", "helper", "g3", "models"))
    op.mkdir(parents=True, exist_ok=True)
    run = op / "run"
    example = load_example(helper)
    with locked(op, ".retest.lock"):
        plan = prepare(run, original, single, example, helper)
        admission, admission_sha = read_admission(run, CAMPAIGN, "transformers")
        if admission["host"]["node"] != platform.node() or admission["lease"]["billing_mode"] != "prepaid_week":
            raise ValueError("Unexpected host or order")
        original_metadata = read(run / "t37" / admission["artifacts"]["metadata_" + MODEL]["path"])
        proto = read(run / "retest-protocol.json")
        eligible = set(proto["eligible_slot_ids"])
        rows = SlotLedger(run, plan).read()
        if any(r["status"] != "unstarted" for r in rows if r["slot_id"] not in eligible):
            raise ValueError("Reference-model slots must stay outside this retest")
        remaining = sum(r["status"] == "unstarted" for r in rows if r["slot_id"] in eligible)
        start_file = run / "retest-lease.json"
        if not start_file.exists():
            terms = {**admission["lease"], "stage_started_unix": time.time(), "prior_stage_seconds": 0,
                     "id": "sense-retest-prepaid"}
            freeze(start_file, terms)
        terms = read(start_file)
        prior = read(original / "cost-ledger.json")["gpu_billed_seconds"]
        budget = RentalBudget(run)
        budget.max_seconds -= prior
        open_original = batch._open_service

        def open_service(run_dir, rows, model, *args):
            if model != MODEL:
                raise ValueError("Unexpected model in sense retest")
            service = open_original(run_dir, rows, model, *args)
            try:
                metadata = dict(service.metadata)
                source = Path(metadata.pop("source"))
                previous = dict(original_metadata)
                previous_source = Path(previous.pop("source"))
                if metadata != previous or digest(source.read_bytes()) != digest(previous_source.read_bytes()):
                    raise ValueError("Live model metadata differs from T37")
                return RetestService(service, run, example, eligible)
            except BaseException:
                service.close()
                raise

        def reserve(_run, _admission, seconds):
            return budget.reserve_prepaid(terms, seconds)

        if remaining:
            with patch.object(batch, "_open_service", open_service), patch.object(batch, "_reserve", reserve):
                status = batch.execute_batch(run, g3, backend="transformers",
                    student_python=admission["interpreters"]["student"], model_root=models, max_new=remaining)
            print(json.dumps(status), flush=True)
        else:
            ledger = SlotLedger(run, plan)
            for row in rows:
                if row["status"] == "started":
                    result = batch._terminal(run, row, "formal")
                    atomic_write(run / "terminal-results" / (row["slot_id"] + ".json"), json_bytes(result))
                    ledger.finish(row["slot_id"], result)
        rows = SlotLedger(run, plan).read()
        targeted = Counter(r["status"] for r in rows if r["slot_id"] in eligible)
        if targeted["unstarted"] or targeted["started"]:
            raise RuntimeError(f"Retest incomplete: {targeted}")
        settled = budget.settle_prepaid()
        freeze(run / "retest-complete.json", {"targeted": dict(targeted), "other_model_excluded": 58,
               "t37_admission_sha256": admission_sha, "lease": settled})
        print(json.dumps(read(run / "retest-complete.json")), flush=True)


if __name__ == "__main__":
    main()
