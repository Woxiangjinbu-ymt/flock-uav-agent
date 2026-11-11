"""Read-only supplementary audit of four frozen local parallel fixtures.

Closes the original validator's call-ID/API binding gap without changing its
frozen source, results, or scorer. Uses JSON and archived HTTP bytes only.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import argparse
import base64
import hashlib
import json


CASES = (("main", "parallel-readonly", ("list_drones", "get_weather")),
         ("main", "parallel-action", ("list_drones", "take_off")),
         ("main", "parallel-unknown", ("list_drones", "take_off")),
         ("v4_reference", "v4-parallel-readonly", ("list_drones", "get_weather")))
ENDPOINTS = {"list_drones": ("GET", "/drones"),
             "get_weather": ("GET", "/environments/current"),
             "take_off": ("POST", "/drones/3d229034/command/take_off")}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _raw_calls(archive):
    chunks, ended, calls = {}, set(), []
    for row in archive:
        kind, data = row.get("kind"), row.get("data", {})
        if kind == "http_response_chunk":
            ident, body = data["attempt_id"], data["body"]
            require(ident not in ended, "HTTP chunk follows a completed response")
            raw = base64.b64decode(body["base64"], validate=True)
            require(len(raw) == body["length"] and sha(raw) == body["sha256"], "HTTP chunk integrity mismatch")
            chunks.setdefault(ident, []).append((data["chunk_index"], raw, data["logical_call_id"]))
        elif kind == "http_response_end":
            ident = data["attempt_id"]
            require(ident not in ended and data.get("complete") is True and data.get("status") == "complete"
                    and data.get("error_type") is None, "Incomplete or duplicate HTTP response")
            parts = chunks.get(ident, [])
            require(bool(parts) and [p[0] for p in parts] == list(range(1, len(parts) + 1))
                    and len(parts) == data["chunk_count"]
                    and all(p[2] == data["logical_call_id"] for p in parts), "HTTP chunk identity/order mismatch")
            raw = b"".join(p[1] for p in parts)
            require(len(raw) == data["length"] and sha(raw) == data["sha256"], "HTTP response integrity mismatch")
            payload = json.loads(raw)
            require(isinstance(payload.get("choices"), list) and len(payload["choices"]) == 1,
                    "Expected one archived assistant choice")
            message = payload["choices"][0]["message"]
            for call in message.get("tool_calls", []):
                calls.append({"call": call, "attempt_id": ident, "logical_call_id": data["logical_call_id"],
                              "response_sha256": data["sha256"]})
            ended.add(ident)
    require(set(chunks) == ended, "Unclosed archived HTTP response")
    return calls


def audit_parallel_records(state, barriers, archive, api, expected_tools):
    """Pure strict linkage check; malformed/missing evidence raises ValueError."""
    require(len(expected_tools) == 2 and set(expected_tools) <= set(ENDPOINTS), "Unsupported fixture tools")
    entries = [r for r in barriers if r.get("event") == "entered"]
    releases = [r for r in barriers if r.get("event") == "released"]
    ids = [r.get("tool_call_id") for r in entries]
    require(len(barriers) == 3 and len(entries) == 2 and len(releases) == 1
            and barriers[-1] is releases[0], "Expected two barrier entries then one release")
    require(all(isinstance(i, str) and i for i in ids) and len(set(ids)) == 2, "Barrier IDs must be distinct")
    released = releases[0].get("tool_call_ids")
    require(isinstance(released, list) and len(released) == 2 and set(released) == set(ids),
            "Release must name exactly both barrier IDs")
    events = [e for e in state["events"] if e.get("tool_call_id") in ids]
    require(len(events) == 2 and {e.get("tool_call_id") for e in events} == set(ids),
            "Ledger must contain exactly one event for each barrier ID")
    require(sorted(e.get("tool_name") for e in events) == sorted(expected_tools), "Parallel tools differ from fixture")
    evidence_ids = [e.get("evidence_id") for e in events]
    require(all(isinstance(i, str) and i for i in evidence_ids) and len(set(evidence_ids)) == 2,
            "Parallel ledger evidence IDs must be distinct")
    boundaries = [e.get(k) for e in events for k in ("start_sequence", "end_sequence")]
    require(all(type(n) is int and n > 0 for n in boundaries) and len(set(boundaries)) == 4,
            "Invalid parallel sequence boundaries")
    a, b = events
    require(all(e["start_sequence"] < e["end_sequence"] and e.get("ordering") == "uncertain" for e in events)
            and max(a["start_sequence"], b["start_sequence"]) < min(a["end_sequence"], b["end_sequence"])
            and a.get("overlaps") == [b["evidence_id"]] and b.get("overlaps") == [a["evidence_id"]],
            "Ledger does not establish mutual invocation overlap")
    raw = [r for r in _raw_calls(archive) if r["call"].get("id") in ids]
    require(len(raw) == 2 and {r["call"].get("id") for r in raw} == set(ids),
            "Raw HTTP must contain exactly one tool call for each barrier ID")
    require(len({(r["attempt_id"], r["logical_call_id"]) for r in raw}) == 1,
            "Parallel calls must originate in the same assistant response")
    links = []
    for entry in entries:
        ident = entry["tool_call_id"]
        event = next(e for e in events if e["tool_call_id"] == ident)
        original = next(r for r in raw if r["call"]["id"] == ident)
        call, tool = original["call"], event["tool_name"]
        require(call.get("type") == "function" and call.get("function", {}).get("name") == tool,
                "Raw HTTP tool name differs from ledger")
        arguments = json.loads(call["function"]["arguments"])
        require(isinstance(arguments, dict), "Raw tool arguments must be an object")
        arguments = arguments.get("input_json", arguments)
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        require(arguments == event.get("args"), "Raw arguments differ from ledger")
        backend = [r for r in api if (r.get("origin") or {}).get("tool_call_id") == ident]
        require(len(backend) == 1, "Each barrier ID must bind exactly one public API request")
        request = backend[0]
        expected = ENDPOINTS[tool]
        require((entry.get("method"), entry.get("endpoint")) == expected
                and (request.get("method"), request.get("endpoint")) == expected,
                "Barrier/public API method or endpoint mismatch")
        require(request.get("phase") == "task" and request.get("admin") is False
                and request.get("committed") is True
                and request["origin"].get("kind") == "model_tool"
                and request["origin"].get("tool") == tool, "Public API provenance/commit mismatch")
        require(request.get("action") is (tool == "take_off"), "Public API action classification mismatch")
        require(event.get("scope") == ("action_receipt" if tool == "take_off" else "observation")
                and event.get("status") in (("completed", "unknown") if tool == "take_off" else ("observed",)),
                "Parallel event is not the expected completed observation/action attempt")
        if tool == "take_off":
            require(arguments.get("drone_id") == "3d229034"
                    and (request.get("params") or {}).get("altitude") == arguments.get("altitude"),
                    "Takeoff API parameters differ from raw arguments")
        links.append({"tool_call_id": ident, "tool_name": tool, "evidence_id": event["evidence_id"],
            "start_sequence": event["start_sequence"], "end_sequence": event["end_sequence"],
            "ledger_status": event["status"], "api_request_id": request["request_id"],
            "method": request["method"], "endpoint": request["endpoint"], "committed": True,
            "attempt_id": original["attempt_id"], "logical_call_id": original["logical_call_id"],
            "response_sha256": original["response_sha256"]})
    require(len({r["api_request_id"] for r in links}) == 2, "API request identities are not unique")
    return {"passed": True, "unique_call_links": 2, "actual_mutual_overlap": True, "links": links}


def audit_campaign(campaign):
    campaign = Path(campaign).resolve()
    seal_path = campaign / "campaign-seal.json"
    seal = read(seal_path)["files"]

    def verify_seal():
        files = [p for p in campaign.rglob("*") if p.is_file() and p != seal_path and "__pycache__" not in p.parts]
        require({str(p.relative_to(campaign)) for p in files} == set(seal), "Frozen campaign inventory changed")
        require(all(not p.is_symlink() and sha(p.read_bytes()) == seal[str(p.relative_to(campaign))] for p in files),
                "Frozen campaign hash changed")

    verify_seal()
    manifest = read(campaign / "comparison-manifest.json")
    require(manifest.get("scope") == "hover_compact_local_acceptance_v2"
            and manifest.get("planned_episodes") == 20 and manifest.get("provider_calls_authorized") == 0,
            "Not the fixed local twenty-slot campaign")
    require(len([ident for b in manifest["bundles"] for ident in b["trial_ids"]]) == 20,
            "Frozen twenty-slot denominator differs")
    reports = []
    for group, ident, tools in CASES:
        folder = campaign / "runs" / group / "trials" / ident
        archives = list((folder / "policy/modelio").glob("*.jsonl"))
        require(len(archives) == 1, "Expected exactly one original model archive")
        paths = {"ledger": folder / "policy/task-state.json", "barrier": folder / "private/local-api-barriers.jsonl",
                 "archive": archives[0], "public_api": folder / "policy/worldlogs/public-api.jsonl"}
        report = audit_parallel_records(read(paths["ledger"]), rows(paths["barrier"]), rows(paths["archive"]),
                                        rows(paths["public_api"]), tools)
        report.update(trial_id=ident, group=group,
            artifacts={k: {"path": str(p.relative_to(campaign)), "sha256": sha(p.read_bytes())} for k,p in paths.items()})
        reports.append(report)
    verify_seal()
    return {"schema": "hover-compact-parallel-independent-audit-v1", "passed": True,
        "campaign": str(campaign), "campaign_seal_sha256": sha(seal_path.read_bytes()),
        "frozen_files_verified_before_and_after": len(seal), "planned_local_slots": 20,
        "parallel_slots_audited": len(reports), "unique_call_links_verified": sum(r["unique_call_links"] for r in reports),
        "trials": reports, "provider_calls_added": 0, "frozen_sources_or_results_modified": False,
        "audit_source_sha256": sha(Path(__file__).read_bytes()),
        "scope": "supplementary read-only linkage audit; no rerun, no natural-model or token claim",
        "reasoning_content_exported": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    require(not output.is_relative_to(args.campaign.resolve()), "Audit output must be outside frozen campaign")
    report = audit_campaign(args.campaign)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({k: report[k] for k in ("passed", "parallel_slots_audited", "unique_call_links_verified", "provider_calls_added")}))


if __name__ == "__main__":
    main()
