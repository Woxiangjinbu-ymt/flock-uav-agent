"""Publish a Chinese T37 report using only the sealed files and command receipts."""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import mean
import tempfile


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(path, sha=None):
    raw = path.read_bytes()
    if sha is not None:
        require(hashlib.sha256(raw).hexdigest() == sha, f"SHA-256 mismatch: {path}")
    return json.loads(raw)


def percentile(values, q, population=None):
    """Nearest rank; right-censored durations sort after every complete request."""
    values = sorted(x for x in values if x is not None)
    rank = math.ceil((len(values) if population is None else population) * q)
    return values[rank-1] if 0 < rank <= len(values) else None


def number(value):
    return "—" if value is None else f"{value:.2f}"


def band(values):
    known = [v for v in values if v is not None]
    if not known:
        return "— (0)"
    return " / ".join(number(x) for x in (mean(known), percentile(known, .5), percentile(known, .95))) + f" ({len(known)})"


def rows_for_backend(directory):
    verdict = read(directory / "verdict.json")
    bundle = read(directory / "measurements.json", verdict["measurement_sha256"])
    fixtures = read(directory / "fixtures.json", bundle["fixtures_sha256"])
    config = read(directory / "configuration.json")
    require(config["campaign_sha256"] == bundle["campaign_sha256"] and config["backend"] == bundle["backend"]
            and config["fixtures_sha256"] == bundle["fixtures_sha256"], "Calibration binding changed")
    models = list(bundle["models"])
    require(set(models) == {"Qwen3.5-2B", "Qwen3-4B"} and set(models) == set(fixtures["revisions"]),
            "Unexpected model population")
    inputs = fixtures["histories"]
    expected_names = {"records/" + s["id"] + ".json" for s in fixtures["schedule"]}
    require(len(expected_names) == len(fixtures["schedule"]) == bundle["request_count"] == 80
            and set(bundle["raw_files_sha256"]) == expected_names, "Incomplete T37 request archive")
    eos = {m: set(read(directory / ("metadata-"+m+".json"))["eos_ids"]) for m in models}
    rows = []
    for item in fixtures["schedule"]:
        name = item["id"]
        require(Path(name).name == name and item["model"] in eos, "Unexpected request identity")
        path = "records/" + name + ".json"
        row = read(directory / path, bundle["raw_files_sha256"][path])
        require(all(row[k] == v for k, v in item.items()) and row["fixtures_sha256"] == bundle["fixtures_sha256"],
                f"Fixture/schedule identity changed: {name}")
        resp, fixture = row["response"], inputs[row["fixture"]]
        timeout = resp.get("stop_reason") == "request_timeout"
        seconds = resp["request_seconds"]
        require(seconds >= 120 if timeout else 0 < seconds <= 120, f"Invalid request deadline: {name}")
        output = resp.get("generated_ids")
        prompt = fixture["input_ids"] + (fixture["stress_think_prefill_ids"] if row["kind"] == "stress" else [])
        if resp.get("input_ids") is not None:
            require(resp["input_ids"] == prompt, f"Incorrect rendered prompt: {name}")
        if not timeout:
            require(isinstance(output, list) and resp["input_ids"] == prompt, f"Missing raw IDs: {name}")
            if row["kind"] == "stress":
                require(len(output) == 2048 and not eos[row["model"]].intersection(output) and resp["status"] == "ok",
                        f"Incomplete pressure output: {name}")
            else:
                require(bool(output and output[-1] in eos[row["model"]]) == resp["terminated"] and
                        (resp["usage"]["prompt_tokens"], resp["usage"]["completion_tokens"], resp["usage"]["total_tokens"])
                        == (len(prompt), len(output), len(prompt) + len(output)), f"Invalid EOS or token usage: {name}")
        timing = resp.get("timing", {})
        if timing.get("decode_tokens_per_second") is not None:
            require(math.isclose(timing["decode_tokens_per_second"], (len(output)-1)/timing["decode_seconds"]),
                    f"Inconsistent decode timing: {name}")
        rows.append(row)
    return bundle, verdict, rows, config


def summarize_model(model, rows, archived):
    rows = [r for r in rows if r["model"] == model]
    require(Counter(r["kind"] for r in rows) == {"warmup": 2, "natural": 18, "stress": 20}, "Wrong group size")
    output = ["## " + model, "", "| 思考 | 目标输入 token | 实际输入范围 | n / EOS / 超时 / 短输出 | 首 token（秒） | 解码（token/s） | 自然结束总时延（秒） |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    on_means = []
    for arm in ("on", "off"):
        for n in (4096, 8192, 13000):
            group = [r["response"] for r in rows if (r["kind"], r["arm"], r["target"]) == ("natural", arm, n)]
            lengths = [len(r["input_ids"]) for r in group if r["input_ids"] is not None]
            times = [r["request_seconds"] if r.get("terminated") else None for r in group]
            timeouts = sum(r.get("stop_reason") == "request_timeout" for r in group)
            ended = sum(x is not None for x in times)
            short = sum(r["generated_ids"] is not None and len(r["generated_ids"]) < 2 for r in group)
            require(len(group) == 3 and times == archived["natural_seconds"][arm][str(n)], "Natural timings changed")
            counts = archived["natural_groups"][arm][str(n)]
            require((counts["n"], counts["natural_ends"], counts["timeouts"], counts["short_outputs"])
                    == (len(group), ended, timeouts, short), "Archived natural counts differ")
            if arm == "on":
                on_means.append(mean(times) if ended == 3 else None)
            span = f"{min(lengths)}–{max(lengths)}" if lengths else "—"
            output.append(f"| {'开' if arm=='on' else '关'} | {n} | {span} | 3 / {ended} / {timeouts} / {short} | "
                          f"{band([r.get('timing', {}).get('ttft_seconds') for r in group])} | "
                          f"{band([r.get('timing', {}).get('decode_tokens_per_second') for r in group])} | {band(times)} |")
    warmup_arms = {r["arm"] for r in rows if r["kind"] == "warmup" and r["response"].get("status") == "ok"
                   and r["response"].get("terminated")
                   and r["response"]["usage"]["completion_tokens_details"]["reasoning_tokens"] is not None}
    functional = warmup_arms == {"on", "off"}
    require(archived["functional_smoke"] is functional, "Archived warmup result differs")
    stress = [r["response"] for r in rows if r["kind"] == "stress"]
    full = [{"seconds": r["request_seconds"] if r["status"] == "ok" else None, "generated_tokens": len(r["generated_ids"]),
             "count_is_lower_bound": r.get("count_is_lower_bound", False)} for r in stress]
    require(full == archived["full_length"], "Archived 20 pressure results differ")
    complete = [r["seconds"] for r in full if r["seconds"] is not None]
    p50, p95 = (percentile(complete, p, 20) for p in (.5, .95))
    require(p95 == archived["full_length_p95_seconds"], "Archived censored P95 differs")
    workload = {}
    if all(x is not None for x in on_means):
        l4, l8, l13 = on_means
        workload = {"seven_round_seconds": l4 + 2*l8 + 4*l13, "twelve_round_seconds": 7*l4 + 4*l8 + l13}
        workload["largest_share_of_episode"] = max(workload.values())/500
    require(workload == archived["workload"], "Archived model-wait estimate differs")
    peak_reserved = [r["response"]["peak_vram_bytes"] for r in rows if r["response"].get("peak_vram_bytes") is not None]
    peak_allocated = [r["response"]["peak_allocated_bytes"] for r in rows if r["response"].get("peak_allocated_bytes") is not None]
    output.extend(["", f"满长开思考请求完成 **{len(complete)}/20**，120 秒右删失 {20-len(complete)} 次。"
                   f"全部 20 次按最近秩计算的 P50 为 {number(p50)} 秒，P95 为 {number(p95)} 秒。",
                   "", f"按开思考三档实测均值估算，7 轮和 12 轮模型等待分别为 "
                   f"{number(workload.get('seven_round_seconds'))} 秒和 {number(workload.get('twelve_round_seconds'))} 秒。"
                   f"硬件峰值按单次请求内 CUDA 分配器记录，预留 "
                   f"{number(max(peak_reserved)/2**30 if peak_reserved else None)} GiB，已分配 "
                   f"{number(max(peak_allocated)/2**30 if peak_allocated else None)} GiB。", ""])
    valid_natural = all(r["response"].get("terminated") or r["response"].get("stop_reason") == "request_timeout"
                        for r in rows if r["kind"] == "natural")
    accepted = bool(workload and workload["largest_share_of_episode"] <= 0.5 and p95 is not None
                    and functional and valid_natural)
    return output, accepted


def receipt_lines(logs, bundle, config):
    phase = "measurement-" + bundle["backend"]
    run_path = str(Path(config["source_scripts"]).parent.parent)
    attempts = []
    for path in sorted(logs.glob(phase + "-*.json")):
        r = read(path)
        argv = r["argv"]
        if r["phase"] == phase and "--run" in argv and argv[argv.index("--run") + 1] == run_path:
            require("finished_unix" in r and "exit_code" in r and r["seconds"] >= 0, f"Incomplete receipt: {path}")
            attempts.append(r)
    require(attempts, "No timed command receipts for this calibration")
    attempts.sort(key=lambda r: r["started_unix"])
    start, end = attempts[0]["started_unix"], max(r["finished_unix"] for r in attempts)
    previous_end = start
    for r in attempts:
        require(r["started_unix"] >= previous_end, "Command attempt times overlap")
        previous_end = r["finished_unix"]
    all_commands = sum(r["seconds"] for r in attempts)
    finish_code = 0 if bundle["passed"] else 2
    require(any(r["exit_code"] == finish_code for r in attempts), "No command completed the archived verdict")
    zone = timezone(timedelta(hours=8))
    fmt = lambda unix: datetime.fromtimestamp(unix, zone).strftime("%Y-%m-%d %H:%M:%S")
    lines = ["## 墙钟与启动记录", "", f"本后端从首条命令启动到末条命令退出共 **{end-start:.2f} 秒**"
             f"（北京时间 {fmt(start)} 至 {fmt(end)}）。各次命令运行时间合计 **{all_commands:.2f} 秒**。"
             f"测速程序从内部计时起点到产出测量文件记录 {bundle['wall_seconds']:.2f} 秒。"
             "三种口径包含重叠时段，分别列示。", "",
             "| 命令序号 | 启动（北京时间） | 退出码 | 实际运行秒数 |", "|---:|---|---:|---:|"]
    for i, r in enumerate(attempts, 1):
        lines.append(f"| {i} | {fmt(r['started_unix'])} | {r['exit_code']} | {r['seconds']:.2f} |")
    return lines


def write_report(source, logs, destination):
    bundle, verdict, rows, config = rows_for_backend(source)
    output = ["# T37 真实权重测速", "", f"后端为 {bundle['backend']}，A100 40 GB 单卡、BF16、batch=1。"
              f"按冻结准入判据，结果为 **{'通过' if verdict['passed'] else '未通过'}**。"
              "4 次预热、36 次自然生成和 40 次满长压力请求分别计数。",
              "", "各自然生成单元格依次列出均值 / P50 / P95 及有效样本数。"
              "P50、P95 使用最近秩。短输出指少于两个生成 ID，解码速率使用首至末 token 的时间。"
              "缺测值以 — 表示。", ""]
    decisions = []
    for model in ("Qwen3.5-2B", "Qwen3-4B"):
        lines, accepted = summarize_model(model, rows, bundle["models"][model])
        output.extend(lines)
        decisions.append(accepted)
    require(all(decisions) is verdict["passed"], "Raw records disagree with the sealed T37 gate")
    output.extend(receipt_lines(logs, {**bundle, "passed": verdict["passed"]}, config))
    output.extend(["", "| 请求组 | 记录数 | 请求端到端用时之和（秒） |", "|---|---:|---:|"])
    for kind, label in (("warmup", "预热"), ("natural", "自然生成"), ("stress", "满长压力")):
        times = [r["response"]["request_seconds"] for r in rows if r["kind"] == kind]
        output.append(f"| {label} | {len(times)} | {sum(times):.2f} |")
    output.extend(["", "请求耗时取自协调器，含到达时限后的停止处理。重试命令保留在墙钟账本中，"
                  "逐组统计仅纳入冻结清单的 80 条终态记录。", "",
                  f"[测量档案]({os.path.relpath(source/'measurements.json',destination.parent)}) · "
                  f"[准入结论]({os.path.relpath(source/'verdict.json',destination.parent)}) · "
                  f"[逐请求记录]({os.path.relpath(source/'records',destination.parent)}/)", ""])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as f:
        f.write("\n".join(output))
        temp = f.name
    os.replace(temp, destination)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--logs", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    write_report(args.source.resolve(), args.logs.resolve(), args.output.resolve())
