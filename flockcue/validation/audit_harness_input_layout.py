"""Read-only counterfactual byte audit: remove only a verified duplicate tool catalog.

No provider, model, tokenizer, runner, simulator or ledger is opened. The changed
body is never dispatched or exported. Reasoning and all non-system wire values
remain intact; output contains hashes and byte counts, never reasoning text.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path[:0] = [str(Path(__file__).resolve().parents[2] / folder) for folder in ("scripts", "compat/scripts", "compat/agent4drone", ".")]

import argparse
import copy
import json
from types import SimpleNamespace

import analyze_deepseek_contract_pilot as evidence

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())
sys.path.insert(0, str(ROOT / "agent4drone"))
from template import system_prompt

SOURCE_ANCHOR = "357de0553bdd0527c973a5e4311146b09465524ca000781b64f3818b2f1becb8"
MARKER = "AVAILABLE TOOLS:\n"
TAIL = "\n\nBe concise, safe, and operationally precise."


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return evidence.sha(canonical(value))


def replace_duplicate_catalog(body):
    """Require exact full-v1 text derived from the same actual wire tools list."""
    require = evidence.require
    require(isinstance(body, dict) and isinstance(body.get("messages"), list) and isinstance(body.get("tools"), list),
            "Expected chat-completion wire messages/tools")
    schemas = body["tools"]
    names, native = [], []
    for schema in schemas:
        require(isinstance(schema, dict) and schema.get("type") == "function" and isinstance(schema.get("function"), dict),
                "Unsupported native tool schema")
        function = schema["function"]
        name, description = function.get("name"), function.get("description")
        require(isinstance(name, str) and name and name not in names and isinstance(description, str),
                "Missing/duplicate native tool name or description")
        names.append(name)
        native.append(SimpleNamespace(name=name, description=description))
    require(bool(native), "Cannot audit empty native tool list")
    systems = [(i, msg) for i, msg in enumerate(body["messages"]) if msg.get("role") == "system"]
    require(len(systems) == 1 and isinstance(systems[0][1].get("content"), str), "Expected exactly one text system message")
    index, message = systems[0]
    text = message["content"]
    require(text.count(MARKER) == text.count(TAIL) == 1, "Ambiguous tool catalog boundaries")
    prefix, remaining = text.split(MARKER)
    catalog, suffix = remaining.split(TAIL)
    expected = "\n".join(f"- {tool.name}: {' '.join(tool.description.split())}" for tool in native)
    require(catalog == expected, "System tool descriptions do not exactly match native schema descriptions")
    full = system_prompt.build_system_prompt(native, input_layout="full_v1")
    compact = system_prompt.build_system_prompt(native, input_layout="compact_tools_v1")
    require(text.startswith(full), "System operating rules differ from frozen full-v1 renderer")
    replacement = prefix + MARKER + "\n".join("- " + name for name in names) + TAIL + suffix
    require(replacement == compact + text[len(full):], "Counterfactual differs from production compact renderer")
    result = copy.deepcopy(body)
    result["messages"][index]["content"] = replacement
    # All fields except this one content string must be equal, including reasoning,
    # provider controls, call order/IDs, native tool schema and original receipts.
    restore = copy.deepcopy(result)
    restore["messages"][index]["content"] = text
    require(restore == body, "Unexpected value mutation outside catalog")
    non_system = [msg for msg in body["messages"] if msg.get("role") != "system"]
    after_non_system = [msg for msg in result["messages"] if msg.get("role") != "system"]
    require(non_system == after_non_system and schemas == result["tools"], "History or native schema changed")
    audit = {"tool_count": len(native), "tool_names_in_original_order": names,
        "native_tool_schema_sha256": digest(schemas), "native_tool_schema_unchanged": True,
        "non_system_messages_sha256": digest(non_system), "non_system_messages_unchanged_including_reasoning": True,
        "all_other_request_values_unchanged": True, "system_prefix_and_suffix_unchanged": True,
        "system_before_sha256": evidence.sha(text.encode()), "system_after_sha256": evidence.sha(replacement.encode()),
        "system_utf8_bytes_before": len(text.encode()), "system_utf8_bytes_after": len(replacement.encode()),
        "canonical_request_bytes_before": len(canonical(body)), "canonical_request_bytes_after": len(canonical(result)),
        "canonical_request_sha256_before": digest(body), "canonical_request_sha256_after": digest(result)}
    return result, audit


def aggregate(records):
    result = {"requests": len(records)}
    for prefix in ("system_utf8_bytes", "canonical_request_bytes"):
        before = sum(row[prefix + "_before"] for row in records)
        after = sum(row[prefix + "_after"] for row in records)
        result.update({prefix + "_before": before, prefix + "_after": after,
                       prefix + "_saved": before - after, prefix + "_reduction_fraction": (before - after) / before if before else None})
    return result


def audit(run):
    run = Path(run).resolve()
    manifest, _, verification = evidence.verify_run(run)
    evidence.require(verification["manifest_sha256"] == SOURCE_ANCHOR and manifest["local_smoke"] is False,
                     "Expected the fixed real 11-03 pilot")
    records, excluded = [], []
    for trial in manifest["trials"]:
        archives = list((run / "trials" / trial["trial_id"] / "policy/modelio").glob("*.jsonl"))
        evidence.require(len(archives) == 1, "Expected one archived episode")
        archive = archives[0]
        wire = evidence.old.archive_analysis(archive)
        responses = {r["attempt_id"]: r for r in wire["responses"]}
        for row in evidence.rows(archive):
            if row["kind"] != "http_request":
                continue
            ident = row["data"]["attempt_id"]
            response = responses[ident]
            if response.get("status_code") is None:
                excluded.append({"trial_id": trial["trial_id"], "attempt_id": ident,
                    "request_sequence": row["sequence"], "reason": "archived_budget_rejection_not_provider_dispatch"})
                continue
            evidence.require(response["status_code"] == 200 and response["response_closed"] is True,
                             "Unexpected incomplete/failed actual response")
            raw = evidence.old.decode(row["data"]["body"])
            body = evidence.old.parse(raw)
            _, metrics = replace_duplicate_catalog(body)
            records.append({"trial_id": trial["trial_id"], "condition": trial["condition"], "arm": trial["arm"],
                "logical_call_id": row["data"]["logical_call_id"], "attempt_id": ident,
                "request_sequence": row["sequence"], "original_wire_bytes": len(raw),
                "original_wire_sha256": evidence.sha(raw), "archive_sha256": wire["sha256"], **metrics})
    evidence.require(len(records) == 56 and len(excluded) == 2, "Historical dispatch denominator changed")
    # Revalidate the input archive/seal after all reads. No source is rewritten.
    _, _, after = evidence.verify_run(run)
    evidence.require(after == verification, "Source drifted during read-only audit")
    dependencies = [Path(__file__), Path(evidence.__file__), Path(evidence.old.__file__), Path(system_prompt.__file__),
                    ROOT / "agent4drone/template/parsing_error.py", ROOT / "agent4drone/uav_agent.py"]
    return {"schema": "harness-input-layout-counterfactual-v1", "valid": True,
        "scope": "counterfactual_serialized_bytes_not_deepseek_tokens_cost_or_behavior",
        "source_run": str(run), "source_verification": verification,
        "layout_before": "full_v1", "layout_after": "compact_tools_v1",
        "serialization": "both requests: JSON ensure_ascii=False sort_keys=True separators=(',',':'); original wire size is reported separately",
        "source_files": {str(p.resolve().relative_to(ROOT)): evidence.sha(p.read_bytes()) for p in dependencies},
        "total": aggregate(records), "by_arm": {arm: aggregate([r for r in records if r["arm"] == arm]) for arm in ("baseline", "integrated")},
        "by_condition_arm": {condition + "-" + arm: aggregate([r for r in records if r["condition"] == condition and r["arm"] == arm])
            for condition, _, _, _ in evidence.CONDITIONS for arm in ("baseline", "integrated")},
        "records": records, "excluded_not_dispatched": excluded,
        "invariants": {"native_schema_all_values_unchanged": True, "all_non_system_messages_including_reasoning_unchanged": True,
            "tool_ids_arguments_order_receipts_unchanged": True, "recovery_and_evidence_projections_unchanged": True,
            "operating_and_safety_rules_unchanged": True, "original_run_unchanged": True},
        "provider_calls": 0, "tokenizer_downloads_or_calls": 0,
        "limits": ["Only the duplicated system tool catalog is removed; all descriptions remain in native tools.",
            "This is a same-history counterfactual, not a run of a model under the changed prompt.",
            "Input_json object errors remain unchanged; no action coercion or schema change.",
            "Byte savings are not token savings, cache savings, price savings or evidence of preserved model ability."]}


def markdown(report):
    lines = ["# 工具目录重复内容：历史 wire 字节反事实审计", "",
        "仅处理 11-03 实际已派发的 56 个请求；2 个预算拒绝归档请求单列排除。原始运行只读。", "",
        "每条只替换与该条 native tools description 完全对应的 system 工具目录。其余请求字段、规则、schema、原始消息、reasoning、工具 ID/参数/回执及恢复投影逐值不变。", "",
        "下表请求大小对原/反事实使用同一规范 JSON 序列化；不是 DeepSeek token、缓存、费用或行为结果。", "",
        "| 条件 / 臂 | 实际请求 | system UTF-8 字节：原→新 | 规范请求字节：原→新 | 请求字节减少 |",
        "| --- | ---: | ---: | ---: | ---: |"]
    for label, row in report["by_condition_arm"].items():
        lines.append(f"| {label} | {row['requests']} | {row['system_utf8_bytes_before']:,}→{row['system_utf8_bytes_after']:,} | "
                     f"{row['canonical_request_bytes_before']:,}→{row['canonical_request_bytes_after']:,} | {row['canonical_request_bytes_reduction_fraction']:.1%} |")
    row = report["total"]
    lines += ["", f"56 请求累计规范请求字节 {row['canonical_request_bytes_before']:,}→{row['canonical_request_bytes_after']:,}（减少 {row['canonical_request_bytes_reduction_fraction']:.1%}）。",
              "", "没有重新生成模型输出；不能据此宣称新提示保持自然任务成功率或按同百分比节省计费 token。未规范化历史错误参数，也未裁剪历史/恢复信息。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run, output = args.run.resolve(), args.output.resolve()
    if output.exists() or output.is_relative_to(run):
        parser.error("output must be a nonexistent directory outside run")
    report = audit(run)
    output.mkdir(parents=True, exist_ok=False)
    (output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    (output / "REPORT_CN.md").write_text(markdown(report))
    print(json.dumps({"valid": True, "requests": report["total"]["requests"], "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
