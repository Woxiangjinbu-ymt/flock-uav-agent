"""Re-score unexecuted Qwen tool calls from generated IDs and compare paired prompts."""
from collections import Counter
import json
import math
import os
from pathlib import Path

from flockcue_qwen35 import adapter
import student_input_v2 as v2

from .campaign_storage import atomic_write, digest, json_bytes, save_rows

SENSE = "sense_nearby_entities"
LABELS = {
    "sense": "整组通过且调用感知", "skip": "整组通过且跳过感知",
    "sense_arguments": "感知参数错", "other_call": "其他调用错",
    "other_format": "其他或无法定位的格式错误", "truncated": "截断",
    "timeout": "协议超时", "no_output": "无输出", "missing": "基础设施缺测",
    "unstarted": "未启动",
}
GROUPS = {
    "missing_input_json": "原缺外层参数", "flat_fields": "原字段展平",
    "observations_returned": "原有感知回执", "on": "开思考", "off": "关思考",
}
PAIR_KEYS = ("both", "only_a", "only_b", "neither", "incomplete")


def _failure_label(tokenizer, ids, thinking, tools, anomaly):
    if not ids or (len(ids) == 1 and ids[0] in adapter.EOS_IDS):
        return "no_output"
    if anomaly == "generation_truncated":
        return "truncated"
    if anomaly not in {"tool_arguments", "tool_calls", "generated_format"} or ids[-1] not in adapter.EOS_IDS:
        return "other_format"
    body = ids[:-1]
    if thinking:
        close = tokenizer.convert_tokens_to_ids("</think>")
        if body.count(close) != 1:
            return "other_format"
        body = body[body.index(close) + 1:]
    answer = tokenizer.decode(body, skip_special_tokens=False, clean_up_tokenization_spaces=False)
    intro, opening, rest = answer.partition("<tool_call>")
    if not opening or adapter.NATIVE_TAG.search(intro) or adapter.CONTROL.search(answer):
        return "other_format"
    # Attribute errors only when all outer blocks are complete and unambiguously separated.
    blocks = []
    remaining = opening + rest
    while remaining:
        if not remaining.startswith("<tool_call>"):
            return "other_format"
        content, closing, remaining = remaining[len("<tool_call>"):].partition("</tool_call>")
        if not closing or "<tool_call>" in content:
            return "other_format"
        func = adapter.FUNCTION.match(content.lstrip())
        if not func:
            return "other_format"
        blocks.append((func.group(1), "<tool_call>" + content + closing))
        remaining = remaining.lstrip()
    for name, block in blocks:
        try:
            adapter._decision(block, tools)
        except v2.InputRejected as error:
            if error.code != anomaly:
                return "other_format"
            if name != SENSE:
                return "other_call"
            return "sense_arguments" if error.code == "tool_arguments" else "other_format"
        except RecursionError:
            return "other_format"
    return "other_format"


def score_generation(tokenizer, ids, thinking, prepared, limit, eos_ids):
    parsed = adapter.parse_generation(tokenizer, ids, eos_ids, thinking, prepared, limit)
    message = parsed["message"]
    if message is None:
        code = _failure_label(tokenizer, ids, thinking, prepared["tools"], parsed["anomalies"][0])
        return 0, code, parsed
    success = any(call["function"]["name"] == SENSE for call in message.get("tool_calls", []))
    return int(success), "sense" if success else "skip", parsed


def _verified_evidence(work, slots, ledger):
    keys = {row["slot_key"] for row in slots}
    states = {p.stem: p for p in (work / "states").glob("*.json")}
    paths = {p.stem: p for p in (work / "receipts").glob("*.json")}
    if states.keys() != paths.keys() or not states.keys() <= keys:
        raise ValueError("Each dispatched slot must have one final claim and one receipt")
    services = {}
    for path in (work / "services").glob("*.json"):
        raw = path.read_bytes()
        if digest(raw) != path.stem:
            raise ValueError("Service metadata digest differs")
        services[path.stem] = json.loads(raw)
    receipts = {}
    counts = Counter()
    for key, path in paths.items():
        raw = path.read_bytes()
        claim = json.loads(states[key].read_bytes())
        receipt = json.loads(raw)
        if (claim["state"] != "terminal" or claim["receipt_sha256"] != digest(raw)
                or claim["slot_key"] != key or receipt["slot_key"] != key
                or claim["status"] != receipt["status"] or claim["service_sha256"] != receipt["service_sha256"]
                or receipt["service_sha256"] not in services):
            raise ValueError(f"Invalid final claim, receipt or service for {key}")
        receipts[key] = receipt
        counts[receipt["status"]] += 1
    counts["unstarted"] = len(keys) - len(receipts)
    if counts != Counter(ledger["status_counts"]):
        raise ValueError("Slot states differ from the budget ledger")
    segments = ledger["segments"]
    if (any(s["end_unix"] is None or s["gpu_seconds"] < 0 for s in segments)
            or not math.isclose(sum(s["gpu_seconds"] for s in segments), ledger["gpu_seconds"], abs_tol=1e-6)):
        raise ValueError("Unclosed segment or inconsistent total GPU seconds")
    return services, receipts


def _grade_slot(work, slot, tokenizer, services, receipt, stop_reason):
    from .sense_example_prepare import ids_sha256

    body = json.loads((work / slot["request_path"]).read_bytes())
    rendered, prepared = adapter.render(tokenizer, body["messages"], body["tools"], slot["thinking"])
    prompt = rendered["input_ids"]
    if ids_sha256(prompt) != slot["input_ids_sha256"] or len(prompt) != slot["input_tokens"]:
        raise ValueError(f"Input replay differs for {slot['slot_key']}")
    inputs = (slot["thinking"], prepared, slot["max_new_tokens"])
    history = slot["historical_generated_ids"]
    historical, _, _ = score_generation(tokenizer, history, *inputs, adapter.EOS_IDS)
    if historical != slot["historical_success"]:
        raise ValueError(f"Historical event differs for {slot['slot_key']}")

    raw_path = Path("receipts") / (slot["slot_key"] + ".json")
    status = receipt["status"] if receipt else "unstarted"
    response = receipt.get("response") or {}
    usage = response.get("usage") or {}
    ids = response.get("generated_ids")
    score = None
    label = status
    if status == "completed":
        service = services[receipt["service_sha256"]]
        eos = service["eos_ids"]
        if (set(eos) != adapter.EOS_IDS or response["input_ids"] != prompt
                or response["thinking"] is not slot["thinking"] or response["seed"] != slot["seed"]
                or response["max_new_tokens"] != slot["max_new_tokens"] or not isinstance(ids, list)):
            raise ValueError(f"Returned request or EOS differs for {slot['slot_key']}")
        score, label, parsed = score_generation(tokenizer, ids, *inputs, eos)
        expected = {
            "message": parsed["message"], "anomalies": parsed["anomalies"], "terminated": parsed["terminated"],
            "status": "ok" if parsed["message"] is not None else "format_error",
            "stop_reason": next(iter(parsed["anomalies"]), None),
            "usage": {"prompt_tokens": len(prompt), "completion_tokens": len(ids),
                      "total_tokens": len(prompt) + len(ids),
                      "completion_tokens_details": {"reasoning_tokens": parsed["reasoning_tokens"]}},
        }
        if any(response[k] != v for k, v in expected.items()):
            raise ValueError(f"Offline parse or token usage disagrees with receipt {slot['slot_key']}")
    elif status == "timeout":
        if response.get("stop_reason") != "request_timeout":
            raise ValueError("Timeout receipt lacks a protocol timeout")
        score = 0
    elif status not in {"missing", "unstarted"}:
        raise ValueError(f"Unexpected slot state {status}")

    row = {"slot_key": slot["slot_key"], "status": status, "score": score, "diagnosis": label,
           "stop_reason": receipt.get("reason") or response.get("stop_reason") or (stop_reason if not receipt else None),
           "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
           "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
           "total_tokens": usage.get("total_tokens"), "request_seconds": receipt.get("request_seconds"),
           "peak_vram_bytes": response.get("peak_vram_bytes"), "service_sha256": receipt.get("service_sha256"),
           "receipt": raw_path.as_posix() if receipt else None}
    if slot["condition"] == "A":
        row["historical_score"] = historical
        row["same_event"] = historical == score if score is not None else None
        row["same_ids"] = ids == history if isinstance(ids, list) else None
    return row


def _cell(a, b):
    if a is None or b is None:
        return "incomplete"
    return {(1, 1): "both", (1, 0): "only_a", (0, 1): "only_b", (0, 0): "neither"}[(a, b)]


def pair_summary(pairs):
    cells = Counter(pair["cell"] for pair in pairs)
    scores = {arm: sum(pair[arm]["score"] == 1 for pair in pairs) for arm in ("A", "B")}
    return {"n": len(pairs), **scores, **{key: cells[key] for key in PAIR_KEYS},
            "comparable": len(pairs) - cells["incomplete"], "net": cells["only_b"] - cells["only_a"]}


def arm_metrics(rows):
    def values(metric):
        return [row[metric] for row in rows if row[metric] is not None]

    tokens = {}
    for key in ("input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
        observed = values(key)
        tokens[key] = {"sum": sum(observed) if observed else None, "known": len(observed)}
    durations = sorted(values("request_seconds"))
    peaks = values("peak_vram_bytes")
    return {"diagnoses": dict(Counter(row["diagnosis"] for row in rows)),
            "status": dict(Counter(row["status"] for row in rows)), "tokens": tokens,
            "request_seconds": {"known": len(durations), "sum": sum(durations) if durations else None,
                                "p50": durations[math.ceil(len(durations) * .5)-1] if durations else None,
                                "p95": durations[math.ceil(len(durations) * .95)-1] if durations else None},
            "peak_vram_bytes": max(peaks, default=None), "peak_vram_known": len(peaks)}


def summarize(pairs, ledger):
    groups = {}
    for key in GROUPS:
        group = [p for p in pairs if (p["original_outcome"] == key or
                 key == ("on" if p["thinking"] else "off"))]
        groups[key] = pair_summary(group)
        groups[key]["metrics"] = {arm: arm_metrics([p[arm] for p in group]) for arm in ("A", "B")}
    reproduction = {}
    for field in ("same_ids", "same_event"):
        vals = [p["A"][field] for p in pairs if p["A"][field] is not None]
        reproduction[field] = {"equal": sum(vals), "known": len(vals)}
    return {"overall": pair_summary(pairs), "groups": groups, "historical_reproduction": reproduction,
            "arms": {arm: arm_metrics([p[arm] for p in pairs]) for arm in ("A", "B")}, "ledger": ledger}


def _show(value, digits=2):
    return f"{value:,.{digits}f}" if value is not None else "未知"


def _markdown(summary, work_link):
    total, arms, ledger = summary["overall"], summary["arms"], summary["ledger"]
    rep = summary["historical_reproduction"]
    amount = ledger["gpu_seconds"]
    fixture = summary["backend"] == "fixture"
    heading = "固定回复 CPU 复验" if fixture else "旧、新感知工具说明的 2B 单轮配对结果"
    model = "CPU 固定回复" if fixture else "Qwen3.5-2B"
    cost_line = (f"本地 CPU 复验累计 {_show(amount)} 秒，使用固定回复，GPU 用量为 0。"
                 if fixture else f"单张 A100 从装载至释放收尾累计 {_show(amount)} 秒（{amount / 3600:.4f} 卡时），"
                 f"包周内新增服务器费 {ledger['incremental_cny']} 元。本次仅执行单轮推理。")
    group_rows = [("全部", total)] + [(GROUPS[k], group) for k, group in summary["groups"].items()]
    diagnoses = [f"| {name} | {arms['A']['diagnoses'].get(k, 0)} | {arms['B']['diagnoses'].get(k, 0)} |"
                 for k, name in LABELS.items()]
    usage_rows = list(arms.items())
    for key in ("on", "off"):
        group = summary["groups"][key]
        usage_rows += [(f"{arm} · {GROUPS[key]}（{group['n']}）", metrics)
                       for arm, metrics in group["metrics"].items()]
    uses = []
    for arm, metrics in usage_rows:
        cost = metrics["tokens"]
        counts = [f"{_show(cost[k]['sum'], 0)}（{cost[k]['known']}）"
                  for k in ("input_tokens", "reasoning_tokens", "output_tokens", "total_tokens")]
        secs = metrics["request_seconds"]
        peak = metrics["peak_vram_bytes"]
        uses.append(f"| {arm} | " + " | ".join(counts) + f" | {_show(secs['sum'])} | {_show(secs['p50'])} | "
                    f"{_show(secs['p95'])} | {secs['known']} | {_show(peak / 2**30 if peak is not None else None)} |")
    counts = Counter(ledger["status_counts"])
    unstarted = counts["unstarted"]
    coverage = "部分运行" if unstarted or counts["missing"] else "完整运行"
    return "\n".join([
        f"# {heading}", "",
        f"{model} 使用旧说明 A 时有 **{total['A']}/{total['n']}** 个位置发出整组可解析的感知调用，"
        f"使用新说明 B 时为 **{total['B']}/{total['n']}**。可比较的 {total['comparable']} 对中，"
        f"{total['only_b']} 对仅 B 通过，{total['only_a']} 对仅 A 通过，配对净变化为 **{total['net']:+d}**。", "",
        f"{coverage}。计划 {2 * total['n']} 槽，已派发 {2 * total['n'] - unstarted} 槽，"
        f"收到可复算生成 {counts['completed']} 条、协议超时 {counts['timeout']} 条、"
        f"基础设施缺测 {counts['missing']} 条、未启动 {unstarted} 条。", "",
        "## 配对结果", "",
        "| 原分组 | 位置 | A 通过 | B 通过 | 均通过 | 仅 A | 仅 B | 均未通过 | 缺对 | 净变化 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        *["| " + " | ".join(map(str, (name, g["n"], g["A"], g["B"],
          *(g[k] for k in PAIR_KEYS), g["net"]))) + " |" for name, g in group_rows], "",
        "主分由冻结解析器从生成 ID 复算。只有整组解析通过且实际调用感知工具才得 1 分。缺对保留两臂的原分母，"
        "其中具有有效观测的一侧仍计入本臂成功数。", "",
        f"新 A 与历史 A 的输出 ID 完全一致 {rep['same_ids']['equal']}/{rep['same_ids']['known']}，"
        f"主事件判定一致 {rep['same_event']['equal']}/{rep['same_event']['known']}。分母为各自可核对的记录数。", "",
        "## 逐请求诊断", "",
        "| 互斥诊断 | A | B |", "| --- | ---: | ---: |", *diagnoses, "",
        "感知参数错误与其他调用错误依据完整回答段中的工具块定位，遇到多个错误时列冻结解析器首先拒绝的调用。"
        "无完整输出时，健康服务的协议超时记为未通过，基础设施缺测与未启动保留为空值。", "",
        "## 用量与耗时", "",
        "| 条件 | 输入 token | 思考 token | 输出 token | 全部 token | 请求合计（秒） | P50（秒） | P95（秒） | 时延记录数 | 峰值预留显存（GiB） |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |", *uses, "",
        "token 列括号内为已知记录数。分位数采用最近秩。显存取逐请求的最大预留量。"
        "开关思考子组的请求耗时按单卡顺序执行记录，装载与收尾统一计入总账。[全部分组统计与账本](summary.json)", "",
        cost_line, "",
        "50 个决策位置覆盖 28 道已暴露开发题，按历史输出选取。这里度量对工具说明的响应，整题成功率沿用原 116 个回合的独立评分。", "",
        f"[逐位置配对表](pairs.jsonl) · [输入清单]({work_link}/plan.json) · [逐槽原始回执]({work_link}/receipts/) · "
        f"[运行账本]({work_link}/ledger.json)", "",
    ])


def report(out_dir: Path, model_path: Path, report_dir: Path) -> dict:
    from .sense_example_prepare import load_plan

    work = Path(out_dir)
    report_dir = Path(report_dir)
    plan = load_plan(work)
    tokenizer = adapter.load_tokenizer(model_path)
    ledger = json.loads((work / "ledger.json").read_bytes())
    execution = json.loads((work / "execution.json").read_bytes())
    if digest((work / "plan.json").read_bytes()) != execution["plan_sha256"]:
        raise ValueError("Execution used a different prepared plan")
    for module in (adapter, v2, v2.base):
        name = "scripts/" + module.__name__.replace(".", "/") + ".py"
        if digest(Path(module.__file__).read_bytes()) != plan["dependencies"]["source_sha256"][name]:
            raise ValueError(f"Replay source differs from the prepared plan: {name}")
    services, receipts = _verified_evidence(work, plan["slots"], ledger)
    pairs = {}
    for slot in plan["slots"]:
        pair = pairs.setdefault(slot["pair_index"], {k: slot[k] for k in
            ("pair_index", "slot_id", "turn", "task_id", "scene_id", "thinking", "original_outcome")})
        arm = slot["condition"]
        if arm not in {"A", "B"} or arm in pair:
            raise ValueError("Invalid or duplicate condition")
        pair[arm] = _grade_slot(work, slot, tokenizer, services, receipts.get(slot["slot_key"], {}),
                                ledger["stop_reason"])
    rows = [pairs[index] for index in sorted(pairs)]
    for pair in rows:
        pair["cell"] = _cell(pair["A"]["score"], pair["B"]["score"])
    summary = summarize(rows, ledger)
    summary["backend"] = execution["backend"]
    report_dir.mkdir(parents=True, exist_ok=True)
    save_rows(report_dir / "pairs.jsonl", rows)
    atomic_write(report_dir / "summary.json", json_bytes(summary))
    atomic_write(report_dir / "REPORT_CN.md", _markdown(summary, os.path.relpath(work, report_dir)).encode())
    return summary
