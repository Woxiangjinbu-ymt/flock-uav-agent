"""Independent read-only audit of a sealed v6/v7 campaign; no runner imports."""
from collections import Counter, defaultdict
import hashlib
import json
import math
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
WORKSPACE = BASE.parents[2]
RUN = BASE / "run-20261024-01"
OLD = WORKSPACE / "docs/results/harness-terminal-progress-20261023/run-20261023-01"
TASKS = ["7eba7390", "24ac1838", "b1ad4f3e", "b1eb0270", "898cfdca", "784d71f8", "1905e0ad"]
REVISIONS = ["terminal_progress_v6", "observed_target_v7"]
CHECKS = []


def read(path):
    return json.loads(path.read_text())


def check(name, value, detail=None, category="invariant"):
    CHECKS.append({"name": name, "passed": bool(value), "detail": detail, "category": category})


def verify_files(name, root, mapping):
    errors = [n for n, h in mapping.items() if not (root / n).is_file()
              or hashlib.sha256((root / n).read_bytes()).hexdigest() != h]
    check(name, not errors, {"count": len(mapping), "mismatches": errors})


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def terminal_analysis(api, harness, tid):
    skill = [r for r in api if r['phase'] == 'task' and r['origin'].get('kind') == 'skill']
    calls = [e for e in harness['events'] if e['kind'] == 'public_call_start'
             and e.get('call', {}).get('tool') == 'get_mission_progress']
    ends = {e['call_id']: e for e in harness['events'] if e['kind'] == 'public_call_end'}
    progress = [r for r in skill if r['origin'].get('tool') == 'get_mission_progress']
    actions = [r for r in skill if r['action']]
    check(tid + '/raw_progress_call_count', len(progress) == len(calls))
    normalized, matched = [], []
    for row, call in zip(progress, calls):
        receipt = row['response']
        value = receipt.get('statistics', {}).get('task_progress', {})
        percent, detail = value.get('progress_percentage'), value.get('details', {})
        counts = {k: detail[k] for k in ('total_targets', 'visited_targets', 'unvisited_targets')
                  if isinstance(detail, dict) and isinstance(detail.get(k), int)
                  and not isinstance(detail[k], bool) and detail[k] >= 0}
        valid = (row['method'] == 'GET' and row['endpoint'] == '/sessions/current'
                 and row['action'] is False and row['admin'] is False and row['committed'] is True
                 and str(receipt.get('id')) == str(harness['scene_id'])
                 and isinstance(percent, (int, float)) and not isinstance(percent, bool)
                 and math.isfinite(percent) and 0 <= percent <= 100)
        if 'total_targets' in counts and 'visited_targets' in counts:
            valid = valid and counts['visited_targets'] <= counts['total_targets']
            if 'unvisited_targets' in counts:
                valid = valid and counts['total_targets'] - counts['visited_targets'] == counts['unvisited_targets']
            if value.get('task_type') == 'target_assignment' and counts['total_targets'] > 0:
                valid = valid and int(counts['visited_targets'] / counts['total_targets'] * 100) == percent
        normal = {'session_id': str(harness['scene_id']), 'task_type': value.get('task_type'),
                  'progress_percentage': percent, 'details': counts, 'source_endpoint': '/sessions/current',
                  'scope': 'public_session_aggregate_not_full_task_verdict'}
        end = ends.get(call['call_id'], {})
        normal_sha = hashlib.sha256(json.dumps(normal, sort_keys=True, default=str).encode()).hexdigest()
        ref_ok = (call.get('plan_id') == row['origin'].get('plan_id') == end.get('plan_id')
                  and call.get('evidence_id') == end.get('evidence_id')
                  and end.get('receipt_sha256') == normal_sha and call['sequence'] < end['sequence'])
        normalized.append(valid and ref_ok)
        matched.append({'request_id': row['request_id'], 'plan_id': call['plan_id'],
                        'evidence_ref': call['evidence_id'], 'percent': percent, 'normalized_receipt': normal,
                        'raw_and_evidence_valid': bool(valid and ref_ok),
                        'actions_after': sum(a['request_id'] > row['request_id'] for a in actions)})
    check(tid + '/raw_progress_and_evidence_hashes', all(normalized), {'read_count': len(progress)})
    terminal = []
    for frame in harness['frames']:
        if frame['skill'] != 'public_search':
            continue
        stored = frame['state'].get('terminal_progress_check')
        reference = next((r for r in matched if stored and r['evidence_ref'] == stored.get('evidence_ref')), None)
        if stored and stored.get('status') == 'confirmed':
            check(tid + '/fresh_terminal_read_confirmed', reference is not None and reference['raw_and_evidence_valid']
                  and reference['plan_id'] == frame['plan_id'] and reference['actions_after'] == 0
                  and reference['percent'] == stored['progress_percent'] == frame['state']['progress_percent'])
        if frame['status'] == 'completed':
            check(tid + '/completed_search_has_public_evidence', bool(matched) and matched[-1]['raw_and_evidence_valid']
                  and matched[-1]['percent'] > 95 and frame['state'].get('public_stop_condition') == 'session_progress_strictly_above_95')
        terminal.append({'plan_id': frame['plan_id'], 'status': frame['status'], 'reason': frame['reason'],
                         'api_calls': frame['api_calls'], 'cursor': frame['cursor'], 'repairs': frame['repairs'],
                         'cached_percent': frame['state'].get('progress_percent'), 'terminal_record': stored,
                         'terminal_raw_evidence': reference})
    return {'skill_api_count': len(skill), 'skill_progress_count': len(progress),
            'skill_progress_reads': matched, 'last_skill_progress': matched[-1] if matched else None,
            'terminal_frames': terminal}


def public_analysis(api):
    state = {}
    actions, errors, moves = [], [], []
    endpoint_groups = defaultdict(list)
    signature_groups = defaultdict(list)
    kinds = Counter()
    for row in api:
        if row["phase"] != "task" or row["admin"] or row["origin"].get("kind") != "skill":
            continue
        tool = row["origin"].get("tool")
        response = row["response"]
        if tool == "list_drones":
            state.update({str(d["id"]): {"request_id": row["request_id"], **d} for d in response})
        elif tool == "get_drone_status":
            state[str(response["id"])] = {"request_id": row["request_id"], **response}
        if not row["action"]:
            continue
        actor = row["endpoint"].split("/")[2]
        event = {"request_id": row["request_id"], "actor": actor,
                 "request": {k: row.get(k) for k in ("method", "endpoint", "params", "body")},
                 "public_state_before": state.get(actor), "receipt": response}
        actions.append(event)
        if tool != "move_to":
            continue
        start = tuple(state[actor]["position"][k] for k in ("x", "y", "z"))
        dest = tuple(row["params"][k] for k in ("x", "y", "z"))
        status = response.get("status")
        message = str(response.get("message", ""))
        kind = ("endpoint_conflict" if status == "error" and message.lower().startswith("target position conflicts with obstacle:")
                else "path_conflict" if status == "error" and "flight path intersects with" in message.lower()
                else "other_error" if status == "error" else "success" if status == "success"
                else "partial" if status == "partial_success" else "unknown")
        kinds[kind] += 1
        move = {**event, "start": start, "destination": dest, "kind": kind}
        move["plan_id"] = row["origin"].get("plan_id")
        moves.append(move)
        if kind in {"endpoint_conflict", "path_conflict", "other_error"}:
            signature = (kind, actor, dest)
            if kind != "endpoint_conflict":
                signature += (start,)
            if kind == "other_error":
                signature += (message,)
            signature_groups[signature].append(row["request_id"])
            errors.append(move)
        if kind == "endpoint_conflict":
            endpoint_groups[(actor, dest)].append(move)
    qualified, invalid = [], []
    for move in moves:
        if move["kind"] != "endpoint_conflict":
            continue
        after = None
        for row in api:
            if (row['request_id'] <= move['request_id'] or row['phase'] != 'task'
                    or row['admin'] or row['origin'].get('kind') != 'skill'
                    or row['origin'].get('plan_id') != move['plan_id']):
                continue
            if row['action'] and row['endpoint'].startswith('/drones/' + move['actor'] + '/'):
                break
            if row['origin'].get('tool') == 'get_drone_status' and row['endpoint'] == '/drones/' + move['actor']:
                after = row
                break
        receipt = move['receipt']
        actual = (after or {}).get('response', {})
        values = actual.get('position', {})
        actual_xyz = tuple(values.get(k) for k in ('x', 'y', 'z'))
        finite_xyz = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                         for v in (*move['start'], *move['destination'], *actual_xyz))
        valid = (receipt.get('command') == 'move_to' and receipt.get('drone_id') == move['actor']
                 and receipt.get('status') == 'error' and re.fullmatch(
                     r"Target position conflicts with obstacle: Position conflicts with (?:circle|ellipse|polygon) obstacle '[^\n]+'",
                     receipt.get('message', '')) is not None
                 and all(receipt.get(k) is None or receipt[k] is False for k in ('success', 'partial_success'))
                 and not re.search(r'\bbattery\b|\bcurrent status\b|\b(?:invalid|missing|required)\b|\baltitude\b[^.\n]*\b(?:maximum|negative|valid)\b',
                     ' '.join(receipt[k] for k in ('message', 'error', 'reason', 'reason_code', 'error_code', 'blocked_reason')
                              if isinstance(receipt.get(k), str)), re.I)
                 and actual.get('id') == move['actor'] and finite_xyz
                 and all(abs(a-b) <= 1e-6 for a,b in zip(move['start'], actual_xyz)))
        move['post_state_request_id'] = (after or {}).get('request_id')
        move['qualified_endpoint_evidence'] = bool(valid)
        (qualified if valid else invalid).append(move)
    prior, cross_events = defaultdict(list), []
    for move in qualified:
        key = move['plan_id'], move['destination']
        sources = [e for e in prior[key] if e['actor'] != move['actor'] and e['request_id'] < move['request_id']]
        if sources:
            cross_events.append({'request_id': move['request_id'], 'actor': move['actor'], 'xyz': move['destination'],
                                 'source_request_ids': [e['request_id'] for e in sources],
                                 'same_actor_overlap': any(e['actor'] == move['actor'] for e in prior[key])})
        prior[key].append(move)
    cross_groups = [{'plan_id': key[0], 'xyz': key[1], 'actors': sorted({e['actor'] for e in events}),
                     'request_ids': [e['request_id'] for e in events]}
                    for key,events in prior.items() if len({e['actor'] for e in events}) > 1]
    return {"actions": actions, "moves": moves, "errors": errors, "classification": dict(kinds),
            "cross_actor_events": cross_events, "cross_actor_groups": cross_groups,
            "unqualified_endpoint_conflicts": [e['request_id'] for e in invalid],
            "unique_failures": len(signature_groups), "signatures": signature_groups,
            "endpoint_repeat_count": sum(len(v) - 1 for v in endpoint_groups.values()),
            "endpoint_repeat_groups": [{"actor": k[0], "endpoint": k[1], "request_ids": [e["request_id"] for e in v],
                                         "starts": [e["start"] for e in v]} for k, v in endpoint_groups.items() if len(v) > 1],
            "idle_move_errors": [e["request_id"] for e in moves if "DroneStatus.IDLE" in str(e["receipt"].get("message", ""))]}


def target_analysis(api, harness, tid, result):
    """Independently join explicit candidate intent to earlier public receipts."""
    skill = [r for r in api if r['phase'] == 'task' and not r['admin'] and r['origin'].get('kind') == 'skill']
    starts = [e for e in harness['events'] if e['kind'] == 'public_call_start']
    ends = {e['call_id']: e for e in harness['events'] if e['kind'] == 'public_call_end'}
    check(tid + '/target_raw_event_count', len(skill) == len(starts))
    references = {start['evidence_id']: (row, start, ends.get(start['call_id'], {}))
                  for row, start in zip(skill, starts)}
    def xyz(value):
        return tuple(value.get(k) for k in ('x', 'y', 'z')) if isinstance(value, dict) else None
    def finite(point):
        return point is not None and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in point)
    def valid_nearby(row):
        v = row['response']
        return (row['origin'].get('tool') == 'get_nearby_entities' and row['method'] == 'GET'
                and row['committed'] is True and row['action'] is False and isinstance(v, dict)
                and v.get('status') in (None, 'success') and not v.get('error') and not v.get('harness_transport_error')
                and (v.get('success') is None or v['success'] is True)
                and (v.get('partial_success') is None or v['partial_success'] is False)
                and all(isinstance(v.get(k), list) for k in ('targets', 'obstacles', 'drones')))
    def valid_target(t):
        return (isinstance(t, dict) and isinstance(t.get('id'), str) and bool(t['id'])
                and t.get('type') == 'fixed' and isinstance(t.get('name'), str)
                and re.fullmatch(r'Fixed Target [1-9]\d*', t['name']) is not None and finite(xyz(t.get('position'))))
    def joined(ref, plan, tool, actor, outcome=None):
        row, start, end = references.get(ref, ({}, {}, {}))
        response = row.get('response')
        if tool == 'move_to' and isinstance(response, dict):
            response = {k: v for k, v in response.items() if k not in (
                'successful_points_count', 'successful_points', 'unsuccessful_points_count', 'unsuccessful_points')}
            response['outcome'] = outcome
        digest = hashlib.sha256(json.dumps(response, sort_keys=True, default=str).encode()).hexdigest()
        endpoint = '/drones/' + actor + ('/nearby' if tool == 'get_nearby_entities' else '/command/move_to')
        valid = (row.get('origin', {}).get('plan_id') == start.get('plan_id') == end.get('plan_id') == plan
                 and row.get('origin', {}).get('tool') == start.get('call', {}).get('tool') == tool
                 and row.get('method') == ('GET' if tool == 'get_nearby_entities' else 'POST')
                 and row.get('endpoint') == endpoint and row.get('committed') is True
                 and row.get('action') is (tool == 'move_to')
                 and start.get('call', {}).get('args', {}).get('drone_id') == actor
                 and start.get('evidence_id') == end.get('evidence_id') == ref
                 and start.get('sequence', 0) < end.get('sequence', 0) and end.get('receipt_sha256') == digest)
        return row, valid
    outputs, names, entries, followup_frames = [], [], [], []
    for frame in harness['frames']:
        record = frame['state'].get('observed_target_followup')
        if record is None:
            continue
        followup_frames.append(frame['plan_id'])
        plan = frame['plan_id']
        planrows = [r for r in skill if r['origin'].get('plan_id') == plan]
        nearby = [r for r in planrows if valid_nearby(r)]
        moves = [r for r in planrows if r['origin'].get('tool') == 'move_to']
        first = {}
        for r in nearby:
            for t in r['response']['targets']:
                if valid_target(t):
                    first.setdefault(t['id'], (r, t))
        for item in record['targets']:
            source, good = joined(item['observation_ref'], plan, 'get_nearby_entities', item['owner'])
            found, original = first.get(item['target_id'], ({}, {}))
            check(tid + '/target_source/' + item['target_id'], good and valid_nearby(source)
                  and found.get('request_id') == source.get('request_id')
                  and original.get('name') == item['name'] and xyz(original.get('position')) == tuple(item['position']))
            entries.append({'target_id': item['target_id'], 'name': item['name'], 'owner': item['owner'],
                            'first_request_id': source.get('request_id'), 'position': item['position'],
                            'scheduled': item['scheduled'], 'disabled_reason': item.get('disabled_reason')})
        attempted_ids = [a['target_id'] for a in record['attempts']]
        check(tid + '/target_once_per_id', len(set(attempted_ids)) == len(attempted_ids))
        for attempt in record['attempts']:
            ref = attempt.get('action_ref')
            observation, observed_ok = joined(attempt['observation_ref'], plan, 'get_nearby_entities', attempt['actor'])
            action, action_ok = joined(ref, plan, 'move_to', attempt['actor'], attempt.get('outcome')) if ref else ({}, False)
            expected = moves[attempt['sequence'] - 1] if 1 <= attempt['sequence'] <= len(moves) else {}
            if attempt['status'] == 'action_returned':
                prior_states = [r for r in planrows if r['request_id'] < action.get('request_id', -1)
                                and r['origin'].get('tool') == 'get_drone_status' and r['endpoint'] == '/drones/' + attempt['actor']]
                fresh = prior_states[-1]['response'] if prior_states else {}
                radius = fresh.get('task_radius')
                dest = tuple(attempt['destination'])
                after = next((r for r in planrows if r['request_id'] > action.get('request_id', -1)
                              and r['origin'].get('tool') == 'get_drone_status' and r['endpoint'] == '/drones/' + attempt['actor']), {})
                receipt = action.get('response', {})
                mapping = {'success': 'completed', 'partial_success': 'partial', 'error': 'blocked'}
                check(tid + '/directed_action_source/' + attempt['target_id'], observed_ok and action_ok
                      and observation['request_id'] < action['request_id'] == expected.get('request_id')
                      and xyz(action['params']) == dest == (*attempt['observed_position'][:2], 20)
                      and receipt.get('command') == 'move_to' and receipt.get('drone_id') == attempt['actor']
                      and mapping.get(receipt.get('status')) == attempt['outcome']
                      and isinstance(radius, (int, float)) and not isinstance(radius, bool) and math.isfinite(radius)
                      and radius > 0 and abs(20-attempt['observed_position'][2]) <= radius
                      and 1 <= attempt['round'] <= 16)
                check(tid + '/directed_action_after_state/' + attempt['target_id'],
                      after.get('response', {}).get('id') == attempt['actor']
                      and (attempt['outcome'] != 'completed' or xyz(after['response']['position']) == dest))
                outputs.append({**attempt, 'source_request_id': observation['request_id'],
                                'action_request_id': action['request_id'], 'source_actor_state': fresh,
                                'raw_receipt': receipt, 'post_state_request_id': after.get('request_id'),
                                'post_state': after.get('response')})
            else:
                outputs.append({**attempt, 'source_request_id': observation.get('request_id'),
                                'action_request_id': None, 'matched_sequence_request': expected.get('request_id')})
        for name, suppress in record['positive_names'].items():
            row, good = joined(suppress['evidence_ref'], plan, 'move_to', suppress['actor'], 'completed')
            receipt = row.get('response', {})
            message = receipt.get('message', '')
            tokens = message.split(' - Reached target(s): ')[1].split(', ') if message.count(' - Reached target(s): ') == 1 else []
            check(tid + '/name_suppression/' + name, good and receipt.get('status') == 'success' and name in tokens
                  and suppress['scope'] == 'name_only_suppression_not_target_id_completion')
            names.append({'name': name, 'request_id': row.get('request_id'), 'actor': suppress['actor'],
                          'target_id_completion_inferred': False})
    metrics = result['observed_target_metrics']
    check(tid + '/independent_target_metric_counts', metrics['followup_frame_count'] == len(followup_frames)
          and metrics['observed_fixed_target_count'] == len(entries) and metrics['scheduled_record_count'] == len(outputs)
          and metrics['returned_action_count'] == sum(a['status'] == 'action_returned' for a in outputs)
          and metrics['scheduled_only_count'] == sum(a['status'] == 'scheduled' for a in outputs)
          and metrics['name_suppression_count'] == len(names) and metrics['issues_count'] == 0)
    return {'observed': entries, 'directed_attempts': outputs, 'name_suppressions': names}


def main():
    manifest, summary, results = (read(RUN / f) for f in ("manifest.json", "summary.json", "results.json"))
    verify_files("campaign_seal", RUN, read(RUN / "campaign-seal.json")["files"])
    verify_files("frozen_source", RUN / "source", manifest["sources"])
    verify_files("live_source_same_as_freeze", WORKSPACE, manifest["sources"])
    study = Path(manifest["study"])
    verify_files("selected_inputs", study, manifest["inputs"])
    verify_files("original_full_manifest", study, read(study / "manifest.json")["files"])
    verify_files("old_campaign_unchanged", OLD, read(OLD / "campaign-seal.json")["files"])
    for name in ('harness-entry-contract-20261023', 'harness-flight-state-20261023',
                 'harness-blocked-candidate-20261023', 'harness-shared-endpoint-20261023'):
        prior = WORKSPACE / 'docs/results' / name / 'run-20261023-01'
        verify_files('prior_campaign_unchanged/' + name, prior, read(prior / 'campaign-seal.json')['files'])
    check("protocol_hash", hashlib.sha256((RUN / "PROTOCOL_CN.md").read_bytes()).hexdigest() == manifest["protocol_sha256"])
    check("fixed_budgets", [manifest[k] for k in ("runtime_seconds", "api_budget", "repair_budget", "search_max_rounds", "worker_watchdog_seconds")] == [500, 2000, 16, 16, 90])
    expected_order = [task + "-" + revision for i, task in enumerate(TASKS) for revision in REVISIONS[::1 if i % 2 == 0 else -1]]
    check("fourteen_fixed_alternating_trials", [r["trial_id"] for r in results] == expected_order)
    check("fourteen_verified", len(results) == 14 and all(r["status"] == "verified" and r["process_returncode"] == 0 for r in results))
    analyses, by_trial, pids, trial_output, pair_output, energy_differences = {}, {}, [], [], [], []
    for result in results:
        tid, task = result["trial_id"], result["task_id"]
        folder = RUN / "trials" / tid
        start, harness, fixture = read(folder / "start.json"), read(folder / "harness.json"), read(study / "fixtures" / (task + ".json"))
        api = rows(folder / "public-api.jsonl")
        analysis = public_analysis(api)
        terminal = terminal_analysis(api, harness, tid)
        target_audit = target_analysis(api, harness, tid, result)
        analyses[tid], by_trial[tid] = analysis, result
        pids.append(start["pid"])
        check(tid + "/initial_hash", result["initial_state_sha256"] == start["initial_state_sha256"] == fixture["state_sha256"])
        check(tid + "/public_hash", result["public_startup_sha256"] == start["public_startup_sha256"] == fixture["public_startup_sha256"])
        check(tid + "/command", start["command"] == fixture["command"])
        check(tid + "/empty_board", all(not start["board_initial"][k] for k in ("targets", "obstacles", "drones")))
        check(tid + "/no_model_network", result["model_calls"] == 0 and not result["network_attempts"] and result["model"] is None)
        check(tid + "/same_scheduler_budget", harness["scheduler"] == "applicability" and start["deadline_seconds"] == 500
              and all(f["api_budget"] == 2000 and f["repair_budget"] == 16 for f in harness["frames"]))
        check(tid + "/terminal_control", all(c["no_effect"] and c["before"] == c["after"] for c in result["controls"][1:]))
        if result["response"].get("plan_id"):
            check(tid + "/terminal_explicit_reject", all(c["response"]["status"] == "rejected" and c["response"]["resumable"] is False for c in result["controls"][1:]))
        scoring = [r for r in api if r["phase"] == "scoring"]
        check(tid + "/isolated_original_checker", len(scoring) == 1 and scoring[0]["admin"]
              and scoring[0]["response"]["task"]["execution_check_apis"] == fixture["original_task"]["execution_check_apis"]
              and scoring[0]["response"]["details"] == result["checker"]["details"]
              and scoring[0]["response"]["result"] == result["checker"]["passed"]
              and result["checker"]["checks_expected"] == fixture["n_checks"] and not result["checker"]["errors"])
        check(tid + "/no_checker_to_skill", all(not r["admin"] and "/check" not in r["endpoint"] for r in api if r["origin"].get("kind") == "skill"))
        taskrows = [r for r in api if r["phase"] == "task" and not r["admin"]]
        contract = read(folder / 'static-obstacle-contract.json')
        check(tid + '/static_contract', contract['validated_before_start'] and contract['geometry_unchanged']
              and contract['geometry_before'] == contract['geometry_after'] and not contract['denied_task_requests']
              and contract['runtime_parameter'] is True
              and contract['timeline']['obstacle_events'] == 0 and contract['lifespan_started'] is False
              and contract['private_geometry_sent_to_policy'] is False)
        check(tid + '/raw_task_allowlist', all(
            not r['admin'] and (r['method'].upper() == 'GET' and (r['endpoint'] in ('/drones', '/sessions/current')
                or re.fullmatch(r'/drones/[^/]+(?:/nearby)?', r['endpoint']) is not None)
                or r['method'].upper() == 'POST' and re.fullmatch(
                    r'/drones/[^/]+/command/(?:take_off|change_altitude|rotate|hover|move_to)', r['endpoint']) is not None)
            for r in api if r['phase'] == 'task'))
        obstacle_state = read(folder / 'score-world.json')['controllers']['obstacle_controller']['obstacles']
        geometry = {oid: {k: o.get(k) for k in contract['geometry_after']['fields']} for oid,o in obstacle_state.items()}
        geometry_sha = hashlib.sha256(json.dumps(geometry, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        check(tid + '/independent_final_geometry_digest', geometry_sha == contract['geometry_after']['sha256']
              and len(geometry) == contract['geometry_after']['obstacle_count'])
        for_frame = harness['frames']
        check(tid + '/single_static_scope', len(for_frame) <= 1 and harness['scene_epoch'] == 0
              and all(f['scene_epoch'] == 0 and f['scene_id'] == start['scene_id'] for f in for_frame))
        raw_actions = [r for r in taskrows if r["action"]]
        action_counts = Counter(r["response"].get("status") for r in raw_actions)
        cost = result["cost"]
        check(tid + "/raw_api_and_action_cost", cost["api_requests"] == len(taskrows) and cost["action_attempts"] == len(raw_actions)
              and all(cost["action_receipts"][s] == action_counts[s] for s in ("success", "partial_success", "error")))
        consumed = sum(e["consumed_mah"] for r in taskrows for e in r.get("energy", []))
        check(tid + "/raw_energy", math.isclose(consumed, cost["consumed_mah"], abs_tol=1e-8))
        deltas = [[e["history"]["position"][k] - e["history"]["start_position"][k] for k in ("x", "y", "z")]
                  for r in taskrows if r["committed"] for e in r.get("history_events", [])]
        check(tid + "/raw_physical_path", len(deltas) == cost["physical_history_events"]
              and math.isclose(sum(math.hypot(d[0], d[1]) for d in deltas), cost["length_xy"], abs_tol=1e-8)
              and math.isclose(sum(math.sqrt(sum(v*v for v in d)) for d in deltas), cost["length_xyz"], abs_tol=1e-8)
              and math.isclose(sum(abs(d[2]) for d in deltas), cost["vertical_distance"], abs_tol=1e-8))
        counts = result["blocked_candidate_metrics"]
        check(tid + "/raw_blocked_metrics", counts["move_attempt_count"] == len(analysis["moves"])
              and counts["all_move_error_count"] == len(analysis["errors"])
              and counts["unique_failure_count"] == analysis["unique_failures"]
              and counts["same_actor_endpoint_conflict_repeat_count"] == analysis["endpoint_repeat_count"]
              and counts["same_actor_endpoint_conflict_repeat_group_count"] == len(analysis["endpoint_repeat_groups"])
              and all(counts["classification"][kind] == analysis["classification"].get(kind, 0) for kind in counts["classification"]))
        check(tid + "/no_idle_move_regression", not analysis["idle_move_errors"])
        shared = result['shared_endpoint_metrics']
        check(tid + '/raw_qualified_shared_metrics', shared['cross_actor_endpoint_repeat_event_count'] == len(analysis['cross_actor_events'])
              and shared['cross_actor_endpoint_repeat_group_count'] == len(analysis['cross_actor_groups'])
              and shared['same_actor_overlap_count'] == sum(e['same_actor_overlap'] for e in analysis['cross_actor_events'])
              and shared['unqualified_endpoint_conflict_count'] == len(analysis['unqualified_endpoint_conflicts']))
        if result["revision"] == "terminal_progress_v6":
            old = read(OLD / "trials" / tid / "result.json")
            old_analysis = public_analysis(rows(OLD / "trials" / tid / "public-api.jsonl"))
            check(tid + "/historical_reproduction", result["checker"] == old["checker"]
                  and result["public_progress"] == old["public_progress"] and result["response"]["reason"] == old["response"]["reason"]
                  and [a["request"] for a in analysis["actions"]] == [a["request"] for a in old_analysis["actions"]])
            check(tid + "/historical_cost_exact", result["cost"] == old["cost"],
                  {key: {"historical": old["cost"][key], "current": value} for key, value in cost.items() if old["cost"][key] != value},
                  category="reproducibility_diagnostic")
            old_api = rows(OLD / 'trials' / tid / 'public-api.jsonl')
            old_actions = [r for r in old_api if r['phase'] == 'task' and r['action'] and not r['admin']]
            current_actions = [r for r in api if r['phase'] == 'task' and r['action'] and not r['admin']]
            material = []
            for i, (a, b) in enumerate(zip(old_actions, current_actions), 1):
                old_energy = sum(v['consumed_mah'] for v in a.get('energy', []))
                new_energy = sum(v['consumed_mah'] for v in b.get('energy', []))
                if abs(new_energy-old_energy) > 1e-8:
                    signature = lambda r: [r.get(k) for k in ('method', 'endpoint', 'params', 'body')]
                    material.append({'action_index': i, 'historical_request_id': a['request_id'],
                        'current_request_id': b['request_id'], 'same_action': signature(a) == signature(b),
                        'old_consumed_mah': old_energy, 'new_consumed_mah': new_energy,
                        'delta_mah': new_energy-old_energy,
                        'old_relative_seconds': a['relative_seconds'], 'new_relative_seconds': b['relative_seconds'],
                        'different_elapsed_integer_second': int(a['relative_seconds']) != int(b['relative_seconds'])})
            energy_differences.append({'trial_id': tid, 'old_total': old['cost']['consumed_mah'],
                'current_total': cost['consumed_mah'], 'delta': cost['consumed_mah']-old['cost']['consumed_mah'],
                'material_per_action_differences': material,
                'all_material_changes_are_same_action_different_simulated_second': all(
                    a['same_action'] and a['different_elapsed_integer_second'] for a in material)})
        trial_output.append({"trial_id": tid, "checker_passed": result["checker"]["passed"],
                             "checks": [result["checker"]["checks_passed"], result["checker"]["checks_expected"]],
                             "progress": result["public_progress"], "stop_reason": result["response"]["reason"],
                             "plan_counters": result["plan_counters"], "cost": cost,
                             "all_move_errors": len(analysis["errors"]), "unique_failures": analysis["unique_failures"],
                             "cross_actor_events": analysis['cross_actor_events'],
                             "cross_actor_groups": analysis['cross_actor_groups'],
                             "unqualified_endpoint_conflicts": analysis['unqualified_endpoint_conflicts'],
                             "terminal_progress": terminal,
                             "observed_targets": target_audit,
                             "endpoint_repeats": analysis["endpoint_repeat_groups"]})
    check("fourteen_distinct_processes", len(set(pids)) == 14)
    for task in TASKS:
        left, right = [analyses[task + "-" + v] for v in REVISIONS]
        n = 0
        while n < min(len(left["actions"]), len(right["actions"])) and left["actions"][n]["request"] == right["actions"][n]["request"]:
            n += 1
        identical = n == len(left["actions"]) == len(right["actions"])
        lset, rset = set(left["signatures"]), set(right["signatures"])
        pair = {"task_id": task, "identical_action_sequence": identical, "common_prefix": n,
                "first_different_action_index": None if identical else n + 1,
                "baseline_first_different": None if identical or n == len(left["actions"]) else left["actions"][n],
                "candidate_first_different": None if identical or n == len(right["actions"]) else right["actions"][n],
                "baseline_unique_errors": len(lset), "candidate_unique_errors": len(rset),
                "new_unique_errors": len(rset - lset), "removed_unique_errors": len(lset - rset)}
        pair_output.append(pair)
        saved = next(p for p in summary["pairs"] if p["task_id"] == task)
        pair_results = [by_trial[task + '-' + v] for v in REVISIONS]
        check(task + '/paired_same_initial_state', pair_results[0]['initial_state_sha256'] == pair_results[1]['initial_state_sha256']
              and pair_results[0]['public_startup_sha256'] == pair_results[1]['public_startup_sha256'])
        contracts = [read(RUN / 'trials' / (task + '-' + revision) / 'static-obstacle-contract.json') for revision in REVISIONS]
        check(task + '/same_initial_obstacle_geometry', contracts[0]['geometry_before'] == contracts[1]['geometry_before'])
        check(task + "/saved_first_divergence", saved["action_comparison"]["common_prefix_actions"] == n
              and saved["action_comparison"]["identical"] == identical)
        if "failure_comparison" in saved:
            check(task + "/saved_unique_failure_delta", saved["failure_comparison"]["new_unique_failure_count"] == len(rset-lset)
                  and saved["failure_comparison"]["removed_unique_failure_count"] == len(lset-rset))
        terminal_pair = [next(t['terminal_progress'] for t in trial_output if t['trial_id'] == task + '-' + v) for v in REVISIONS]
        differences = {'skill_api_count': terminal_pair[1]['skill_api_count'] - terminal_pair[0]['skill_api_count'],
                       'skill_progress_count': terminal_pair[1]['skill_progress_count'] - terminal_pair[0]['skill_progress_count']}
        pair['read_differences'] = differences
        check(task + '/saved_public_read_delta', differences['skill_api_count'] == saved['terminal_progress_comparison']['candidate_minus_baseline_skill_api']
              and differences['skill_progress_count'] == saved['terminal_progress_comparison']['candidate_minus_baseline_skill_progress_reads'])
    output = {"method": "Independent static checks and raw public log recomputation; no runner imports, simulation, or model calls.",
              "audit_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "campaign_seal_sha256": hashlib.sha256((RUN / "campaign-seal.json").read_bytes()).hexdigest(),
              "checks_passed": sum(c["passed"] for c in CHECKS), "checks_failed": sum(not c["passed"] for c in CHECKS),
              "invariant_failures": sum(not c["passed"] and c["category"] == "invariant" for c in CHECKS),
              "reproducibility_differences": [c for c in CHECKS if not c["passed"] and c["category"] == "reproducibility_diagnostic"],
              "checks": CHECKS, "trials": trial_output, "pairs": pair_output,
              "historical_energy_differences": energy_differences}
    (BASE / "independent-verification.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: output[k] for k in ("checks_passed", "checks_failed")}, ensure_ascii=False))
    for c in CHECKS:
        if not c["passed"]:
            print(json.dumps(c, ensure_ascii=False))


if __name__ == "__main__":
    main()
