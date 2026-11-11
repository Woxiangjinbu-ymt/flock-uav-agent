"""Print four development fixtures from sealed requests. Uses only the standard library."""
import base64
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BATCH = ROOT / "docs/results/deepseek-cost-pilot-20261103"
SCENARIOS = {
    "short_chain-candidate": (5, "状态查询确认 4 号机在原 XY、12 米处悬停。此前只有独立起飞命令，缺少协议要求的独立悬停动作回执。"),
    "short_chain-control": (6, "4 号机有独立起飞和悬停的成功回执，随后在原 XY、12 米处查到悬停状态。未核定悬停持续时间。"),
    "unknown_takeoff-control": (4, "原始起飞返回超时。子程序内随后的公开观测已确认 4 号机在原 XY、12 米处处于悬停状态。状态可以核实，历史起飞回执仍未知，独立悬停动作也没有回执。该例采用受控回执丢失。"),
    "search-control": (9, "同批 4 个移动和感知请求各有自己的回执，3 个移动成功。165247f7 的本次路径被多边形障碍拦截，至该终点的到达未获确认。该反馈说明了失败原因，只适用于这一航段，无法单凭它判断原选择最优、搜索总进度或整片区域不可达。"),
}


def subset(value, keys):
    return {key: value[key] for key in keys.split()}


def read_visible_messages(archive, attempt):
    for line_no, line in enumerate(archive.open(), 1):
        event = json.loads(line)
        data = event["data"]
        if event["kind"] == "http_request" and data["attempt_id"] == f"attempt-{attempt}":
            body = data["body"]
            raw = base64.b64decode(body["base64"], validate=True)
            assert len(raw) == body["length"] and hashlib.sha256(raw).hexdigest() == body["sha256"]
            return line_no, json.loads(raw)["messages"]
    raise ValueError(f"Missing request {attempt} in {archive}")


def receipt_excerpt(name, receipt):
    if name == "list_drones":
        return [subset(item, "id name status position") for item in receipt]
    if name == "get_drone_status":
        return subset(receipt, "id name status position")
    if name == "run_harness":
        state = receipt["state"]
        return {
            "last_action": state["last_action"],
            "observed_state": subset(state["unknown_effect_observation"], "id name status position"),
        }
    if name == "move_to_and_sense":
        move = receipt["move_result"]
        data = subset(move, "command_id drone_id outcome status")
        if move["status"] == "error":
            data["message"] = move["message"]
        return data
    return subset(receipt, "command_id command drone_id status outcome")


def public_projection(messages, trial):
    last_batch = max(i for i, m in enumerate(messages) if m["role"] == "assistant")
    receipts = {m["tool_call_id"]: m["content"] for m in messages if m["role"] == "tool"}
    batches = []
    excerpts = []
    for index, message in enumerate(messages):
        if message["role"] != "assistant":
            continue
        calls = message["tool_calls"]
        batches.append([c["function"]["name"] for c in calls])
        for call in calls:
            function = call["function"]
            name = function["name"]
            # For the mixed parallel batch show every sibling receipt; for the
            # short tasks show the aircraft's initial state and material events.
            if trial == "search-control":
                if index != last_batch and name != "list_drones":
                    continue
            elif name not in {"list_drones", "take_off", "hover", "get_drone_status", "run_harness"}:
                continue
            args = json.loads(function["arguments"])
            if "input_json" in args:
                args = json.loads(args["input_json"])
            evidence = receipt_excerpt(name, json.loads(receipts[call["id"]]))
            if name == "list_drones" and trial != "search-control":
                evidence = [item for item in evidence if item["id"] == "3d229034"]
            excerpts.append({"call_id": call["id"], "tool": name, "arguments": args, "receipt_excerpt": evidence})
    user = next(m["content"] for m in messages if m["role"] == "user")
    return {"task": user.split("\n\n", 1)[0], "requests_in_each_prior_model_turn": batches, "evidence": excerpts}


def main():
    report = json.loads((BATCH / "analysis-01/analysis.json").read_bytes())
    trials = {t["trial_id"]: t for t in report["trials"]}
    examples, end_scores = [], {}
    for trial_id, (attempt, note) in SCENARIOS.items():
        trial = trials[trial_id]
        model_io = trial["model_io"]
        archive = BATCH / "run-01/trials" / trial_id / "policy/modelio" / model_io["file"]
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == model_io["sha256"]
        line, messages = read_visible_messages(archive, attempt)
        public = public_projection(messages, trial_id)
        assert public["task"] == trial["command"]
        examples.append({
            "trial": trial_id, "cutoff": f"before provider response attempt-{attempt}",
            "source_path": str(archive.relative_to(ROOT)), "source_line": line, "source_sha256": model_io["sha256"],
            "public_prefix_excerpt": public, "provisional_annotation_from_prefix_cn": note,
        })
        end_scores[trial_id] = {
            "original_checker_passed": trial["checker_passed"],
            "original_checker_and_predeclared_action_audit_passed": trial["obligations"]["checker_and_additional_obligation_passed"],
        }
    print(json.dumps({
        "use": "Development examples only. Four deliberately chosen prefixes from four trials, two distinct tasks. Annotations are preliminary evidence interpretations, not independent gold labels or counterfactual rewards.",
        "projection": "Request payload only, through its last closed tool-call batch; query/observation payloads selectively projected, available raw via archive references. No assistant reasoning, automatic recovery recommendation, private state, future action or final score in public_prefix_excerpt.",
        "examples": examples, "audit_only_after_episode_never_model_input": end_scores,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
