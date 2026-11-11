# DeepSeek 悬停与输入契约同期对照

计划 8 槽，启动 8，评分 8。本批为本地合成 SDK smoke，不是自然模型结果。

| 槽 | 正常返回 | 独立真值 | 公开证据完成 | 原辅助 checker | 实际 token | input_json object/string |
|---|---:|---|---|---|---:|---|
| state_hover-control | True | True | True | True | 104 | 0 / 2 |
| state_hover-candidate | True | True | True | True | 104 | 2 / 0 |
| explicit_hover-candidate | True | True | True | True | 130 | 3 / 0 |
| explicit_hover-control | True | True | True | True | 130 | 0 / 3 |
| unknown_takeoff-control | True | True | True | True | 104 | 0 / 2 |
| unknown_takeoff-candidate | True | True | True | True | 104 | 2 / 0 |
| duration_hover-candidate | True | 未取得/不可估计 | 未取得/不可估计 | True | 130 | 3 / 0 |
| duration_hover-control | True | 未取得/不可估计 | 未取得/不可估计 | True | 130 | 0 / 3 |

## all_8

```json
{
  "all": {
    "planned": 8,
    "started": 8,
    "unstarted": [],
    "scored": 8,
    "public_evidence_verified": 6,
    "independent_truth_verified": 6,
    "independent_truth_false": 0,
    "independent_truth_unverified": 2,
    "prompt_tokens": 612,
    "prompt_tokens_observed_trials": 8,
    "completion_tokens": 324,
    "completion_tokens_observed_trials": 8,
    "total_tokens": 936,
    "total_tokens_observed_trials": 8,
    "known_usage_responses": 36,
    "known_usage_responses_observed_trials": 8,
    "delegated_http_attempts": 36,
    "delegated_http_attempts_observed_trials": 8,
    "usage_complete_trials": 8,
    "prompt_cache_hit_tokens_known_sum": null,
    "prompt_cache_hit_tokens_reported_responses": 0,
    "prompt_cache_miss_tokens_known_sum": null,
    "prompt_cache_miss_tokens_reported_responses": 0,
    "action_attempts": 12,
    "api_requests": 58,
    "worker_elapsed_seconds_known_sum": 1.7225919170014095,
    "conservative_usd_known_sum": "0.0005724",
    "representation_counts": {
      "string": 10,
      "object": 10
    },
    "argument_finding_counts": {},
    "semantic_finding_counts": {},
    "feedback_error_counts": {
      "unknown_execution_effect": 2
    },
    "unknown_recovery_opportunities": 2,
    "unknown_recovery_completed": 2,
    "unknown_recovery_rate": 1.0
  },
  "control": {
    "planned": 4,
    "started": 4,
    "unstarted": [],
    "scored": 4,
    "public_evidence_verified": 3,
    "independent_truth_verified": 3,
    "independent_truth_false": 0,
    "independent_truth_unverified": 1,
    "prompt_tokens": 306,
    "prompt_tokens_observed_trials": 4,
    "completion_tokens": 162,
    "completion_tokens_observed_trials": 4,
    "total_tokens": 468,
    "total_tokens_observed_trials": 4,
    "known_usage_responses": 18,
    "known_usage_responses_observed_trials": 4,
    "delegated_http_attempts": 18,
    "delegated_http_attempts_observed_trials": 4,
    "usage_complete_trials": 4,
    "prompt_cache_hit_tokens_known_sum": null,
    "prompt_cache_hit_tokens_reported_responses": 0,
    "prompt_cache_miss_tokens_known_sum": null,
    "prompt_cache_miss_tokens_reported_responses": 0,
    "action_attempts": 6,
    "api_requests": 29,
    "worker_elapsed_seconds_known_sum": 0.8174777919994085,
    "conservative_usd_known_sum": "0.0002862",
    "representation_counts": {
      "string": 10
    },
    "argument_finding_counts": {},
    "semantic_finding_counts": {},
    "feedback_error_counts": {
      "unknown_execution_effect": 1
    },
    "unknown_recovery_opportunities": 1,
    "unknown_recovery_completed": 1,
    "unknown_recovery_rate": 1.0
  },
  "candidate": {
    "planned": 4,
    "started": 4,
    "unstarted": [],
    "scored": 4,
    "public_evidence_verified": 3,
    "independent_truth_verified": 3,
    "independent_truth_false": 0,
    "independent_truth_unverified": 1,
    "prompt_tokens": 306,
    "prompt_tokens_observed_trials": 4,
    "completion_tokens": 162,
    "completion_tokens_observed_trials": 4,
    "total_tokens": 468,
    "total_tokens_observed_trials": 4,
    "known_usage_responses": 18,
    "known_usage_responses_observed_trials": 4,
    "delegated_http_attempts": 18,
    "delegated_http_attempts_observed_trials": 4,
    "usage_complete_trials": 4,
    "prompt_cache_hit_tokens_known_sum": null,
    "prompt_cache_hit_tokens_reported_responses": 0,
    "prompt_cache_miss_tokens_known_sum": null,
    "prompt_cache_miss_tokens_reported_responses": 0,
    "action_attempts": 6,
    "api_requests": 29,
    "worker_elapsed_seconds_known_sum": 0.905114125002001,
    "conservative_usd_known_sum": "0.0002862",
    "representation_counts": {
      "object": 10
    },
    "argument_finding_counts": {},
    "semantic_finding_counts": {},
    "feedback_error_counts": {
      "unknown_execution_effect": 1
    },
    "unknown_recovery_opportunities": 1,
    "unknown_recovery_completed": 1,
    "unknown_recovery_rate": 1.0
  }
}
```

## supported_6

```json
{
  "all": {
    "planned": 6,
    "started": 6,
    "unstarted": [],
    "scored": 6,
    "public_evidence_verified": 6,
    "independent_truth_verified": 6,
    "independent_truth_false": 0,
    "independent_truth_unverified": 0,
    "prompt_tokens": 442,
    "prompt_tokens_observed_trials": 6,
    "completion_tokens": 234,
    "completion_tokens_observed_trials": 6,
    "total_tokens": 676,
    "total_tokens_observed_trials": 6,
    "known_usage_responses": 26,
    "known_usage_responses_observed_trials": 6,
    "delegated_http_attempts": 26,
    "delegated_http_attempts_observed_trials": 6,
    "usage_complete_trials": 6,
    "prompt_cache_hit_tokens_known_sum": null,
    "prompt_cache_hit_tokens_reported_responses": 0,
    "prompt_cache_miss_tokens_known_sum": null,
    "prompt_cache_miss_tokens_reported_responses": 0,
    "action_attempts": 8,
    "api_requests": 44,
    "worker_elapsed_seconds_known_sum": 1.285928793000494,
    "conservative_usd_known_sum": "0.0004134",
    "representation_counts": {
      "string": 7,
      "object": 7
    },
    "argument_finding_counts": {},
    "semantic_finding_counts": {},
    "feedback_error_counts": {
      "unknown_execution_effect": 2
    },
    "unknown_recovery_opportunities": 2,
    "unknown_recovery_completed": 2,
    "unknown_recovery_rate": 1.0
  },
  "control": {
    "planned": 3,
    "started": 3,
    "unstarted": [],
    "scored": 3,
    "public_evidence_verified": 3,
    "independent_truth_verified": 3,
    "independent_truth_false": 0,
    "independent_truth_unverified": 0,
    "prompt_tokens": 221,
    "prompt_tokens_observed_trials": 3,
    "completion_tokens": 117,
    "completion_tokens_observed_trials": 3,
    "total_tokens": 338,
    "total_tokens_observed_trials": 3,
    "known_usage_responses": 13,
    "known_usage_responses_observed_trials": 3,
    "delegated_http_attempts": 13,
    "delegated_http_attempts_observed_trials": 3,
    "usage_complete_trials": 3,
    "prompt_cache_hit_tokens_known_sum": null,
    "prompt_cache_hit_tokens_reported_responses": 0,
    "prompt_cache_miss_tokens_known_sum": null,
    "prompt_cache_miss_tokens_reported_responses": 0,
    "action_attempts": 4,
    "api_requests": 22,
    "worker_elapsed_seconds_known_sum": 0.6104974589979975,
    "conservative_usd_known_sum": "0.0002067",
    "representation_counts": {
      "string": 7
    },
    "argument_finding_counts": {},
    "semantic_finding_counts": {},
    "feedback_error_counts": {
      "unknown_execution_effect": 1
    },
    "unknown_recovery_opportunities": 1,
    "unknown_recovery_completed": 1,
    "unknown_recovery_rate": 1.0
  },
  "candidate": {
    "planned": 3,
    "started": 3,
    "unstarted": [],
    "scored": 3,
    "public_evidence_verified": 3,
    "independent_truth_verified": 3,
    "independent_truth_false": 0,
    "independent_truth_unverified": 0,
    "prompt_tokens": 221,
    "prompt_tokens_observed_trials": 3,
    "completion_tokens": 117,
    "completion_tokens_observed_trials": 3,
    "total_tokens": 338,
    "total_tokens_observed_trials": 3,
    "known_usage_responses": 13,
    "known_usage_responses_observed_trials": 3,
    "delegated_http_attempts": 13,
    "delegated_http_attempts_observed_trials": 3,
    "usage_complete_trials": 3,
    "prompt_cache_hit_tokens_known_sum": null,
    "prompt_cache_hit_tokens_reported_responses": 0,
    "prompt_cache_miss_tokens_known_sum": null,
    "prompt_cache_miss_tokens_reported_responses": 0,
    "action_attempts": 4,
    "api_requests": 22,
    "worker_elapsed_seconds_known_sum": 0.6754313340024964,
    "conservative_usd_known_sum": "0.0002067",
    "representation_counts": {
      "object": 7
    },
    "argument_finding_counts": {},
    "semantic_finding_counts": {},
    "feedback_error_counts": {
      "unknown_execution_effect": 1
    },
    "unknown_recovery_opportunities": 1,
    "unknown_recovery_completed": 1,
    "unknown_recovery_rate": 1.0
  }
}
```

## duration_2

```json
{
  "all": {
    "planned": 2,
    "started": 2,
    "unstarted": [],
    "scored": 2,
    "public_evidence_verified": 0,
    "independent_truth_verified": 0,
    "independent_truth_false": 0,
    "independent_truth_unverified": 2,
    "prompt_tokens": 170,
    "prompt_tokens_observed_trials": 2,
    "completion_tokens": 90,
    "completion_tokens_observed_trials": 2,
    "total_tokens": 260,
    "total_tokens_observed_trials": 2,
    "known_usage_responses": 10,
    "known_usage_responses_observed_trials": 2,
    "delegated_http_attempts": 10,
    "delegated_http_attempts_observed_trials": 2,
    "usage_complete_trials": 2,
    "prompt_cache_hit_tokens_known_sum": null,
    "prompt_cache_hit_tokens_reported_responses": 0,
    "prompt_cache_miss_tokens_known_sum": null,
    "prompt_cache_miss_tokens_reported_responses": 0,
    "action_attempts": 4,
    "api_requests": 14,
    "worker_elapsed_seconds_known_sum": 0.43666312400091556,
    "conservative_usd_known_sum": "0.0001590",
    "representation_counts": {
      "object": 3,
      "string": 3
    },
    "argument_finding_counts": {},
    "semantic_finding_counts": {},
    "feedback_error_counts": {},
    "unknown_recovery_opportunities": 0,
    "unknown_recovery_completed": 0,
    "unknown_recovery_rate": null
  },
  "control": {
    "planned": 1,
    "started": 1,
    "unstarted": [],
    "scored": 1,
    "public_evidence_verified": 0,
    "independent_truth_verified": 0,
    "independent_truth_false": 0,
    "independent_truth_unverified": 1,
    "prompt_tokens": 85,
    "prompt_tokens_observed_trials": 1,
    "completion_tokens": 45,
    "completion_tokens_observed_trials": 1,
    "total_tokens": 130,
    "total_tokens_observed_trials": 1,
    "known_usage_responses": 5,
    "known_usage_responses_observed_trials": 1,
    "delegated_http_attempts": 5,
    "delegated_http_attempts_observed_trials": 1,
    "usage_complete_trials": 1,
    "prompt_cache_hit_tokens_known_sum": null,
    "prompt_cache_hit_tokens_reported_responses": 0,
    "prompt_cache_miss_tokens_known_sum": null,
    "prompt_cache_miss_tokens_reported_responses": 0,
    "action_attempts": 2,
    "api_requests": 7,
    "worker_elapsed_seconds_known_sum": 0.20698033300141105,
    "conservative_usd_known_sum": "0.0000795",
    "representation_counts": {
      "string": 3
    },
    "argument_finding_counts": {},
    "semantic_finding_counts": {},
    "feedback_error_counts": {},
    "unknown_recovery_opportunities": 0,
    "unknown_recovery_completed": 0,
    "unknown_recovery_rate": null
  },
  "candidate": {
    "planned": 1,
    "started": 1,
    "unstarted": [],
    "scored": 1,
    "public_evidence_verified": 0,
    "independent_truth_verified": 0,
    "independent_truth_false": 0,
    "independent_truth_unverified": 1,
    "prompt_tokens": 85,
    "prompt_tokens_observed_trials": 1,
    "completion_tokens": 45,
    "completion_tokens_observed_trials": 1,
    "total_tokens": 130,
    "total_tokens_observed_trials": 1,
    "known_usage_responses": 5,
    "known_usage_responses_observed_trials": 1,
    "delegated_http_attempts": 5,
    "delegated_http_attempts_observed_trials": 1,
    "usage_complete_trials": 1,
    "prompt_cache_hit_tokens_known_sum": null,
    "prompt_cache_hit_tokens_reported_responses": 0,
    "prompt_cache_miss_tokens_known_sum": null,
    "prompt_cache_miss_tokens_reported_responses": 0,
    "action_attempts": 2,
    "api_requests": 7,
    "worker_elapsed_seconds_known_sum": 0.2296827909995045,
    "conservative_usd_known_sum": "0.0000795",
    "representation_counts": {
      "object": 3
    },
    "argument_finding_counts": {},
    "semantic_finding_counts": {},
    "feedback_error_counts": {},
    "unknown_recovery_opportunities": 0,
    "unknown_recovery_completed": 0,
    "unknown_recovery_rate": null
  }
}
```

## 两臂同样成功的支持任务子集

仅作配对描述，与完整分母并列；排除时长任务。

```json
{
  "conditions": [
    "state_hover",
    "explicit_hover",
    "unknown_takeoff"
  ],
  "pairs": 3,
  "scope": "descriptive_matched_success_subset_excluding_duration; report alongside full fixed denominator, not causal attribution",
  "control": {
    "planned": 3,
    "started": 3,
    "unstarted": [],
    "scored": 3,
    "public_evidence_verified": 3,
    "independent_truth_verified": 3,
    "independent_truth_false": 0,
    "independent_truth_unverified": 0,
    "prompt_tokens": 221,
    "prompt_tokens_observed_trials": 3,
    "completion_tokens": 117,
    "completion_tokens_observed_trials": 3,
    "total_tokens": 338,
    "total_tokens_observed_trials": 3,
    "known_usage_responses": 13,
    "known_usage_responses_observed_trials": 3,
    "delegated_http_attempts": 13,
    "delegated_http_attempts_observed_trials": 3,
    "usage_complete_trials": 3,
    "prompt_cache_hit_tokens_known_sum": null,
    "prompt_cache_hit_tokens_reported_responses": 0,
    "prompt_cache_miss_tokens_known_sum": null,
    "prompt_cache_miss_tokens_reported_responses": 0,
    "action_attempts": 4,
    "api_requests": 22,
    "worker_elapsed_seconds_known_sum": 0.6104974589979975,
    "conservative_usd_known_sum": "0.0002067",
    "representation_counts": {
      "string": 7
    },
    "argument_finding_counts": {},
    "semantic_finding_counts": {},
    "feedback_error_counts": {
      "unknown_execution_effect": 1
    },
    "unknown_recovery_opportunities": 1,
    "unknown_recovery_completed": 1,
    "unknown_recovery_rate": 1.0
  },
  "candidate": {
    "planned": 3,
    "started": 3,
    "unstarted": [],
    "scored": 3,
    "public_evidence_verified": 3,
    "independent_truth_verified": 3,
    "independent_truth_false": 0,
    "independent_truth_unverified": 0,
    "prompt_tokens": 221,
    "prompt_tokens_observed_trials": 3,
    "completion_tokens": 117,
    "completion_tokens_observed_trials": 3,
    "total_tokens": 338,
    "total_tokens_observed_trials": 3,
    "known_usage_responses": 13,
    "known_usage_responses_observed_trials": 3,
    "delegated_http_attempts": 13,
    "delegated_http_attempts_observed_trials": 3,
    "usage_complete_trials": 3,
    "prompt_cache_hit_tokens_known_sum": null,
    "prompt_cache_hit_tokens_reported_responses": 0,
    "prompt_cache_miss_tokens_known_sum": null,
    "prompt_cache_miss_tokens_reported_responses": 0,
    "action_attempts": 4,
    "api_requests": 22,
    "worker_elapsed_seconds_known_sum": 0.6754313340024964,
    "conservative_usd_known_sum": "0.0002067",
    "representation_counts": {
      "object": 7
    },
    "argument_finding_counts": {},
    "semantic_finding_counts": {},
    "feedback_error_counts": {
      "unknown_execution_effect": 1
    },
    "unknown_recovery_opportunities": 1,
    "unknown_recovery_completed": 1,
    "unknown_recovery_rate": 1.0
  }
}
```

## state_hover-control

输入错误：`{}`。
恢复：`not_estimable_no_trigger`。
公开完成证据：`public_hover_goal_and_required_action_evidence_verified`。

## state_hover-candidate

输入错误：`{}`。
恢复：`not_estimable_no_trigger`。
公开完成证据：`public_hover_goal_and_required_action_evidence_verified`。

## explicit_hover-candidate

输入错误：`{}`。
恢复：`not_estimable_no_trigger`。
公开完成证据：`public_hover_goal_and_required_action_evidence_verified`。

## explicit_hover-control

输入错误：`{}`。
恢复：`not_estimable_no_trigger`。
公开完成证据：`public_hover_goal_and_required_action_evidence_verified`。

## unknown_takeoff-control

输入错误：`{"unknown_execution_effect": 1}`。
恢复：`verified_state_recovery_without_replay`。
公开完成证据：`public_hover_goal_and_required_action_evidence_verified`。

## unknown_takeoff-candidate

输入错误：`{"unknown_execution_effect": 1}`。
恢复：`verified_state_recovery_without_replay`。
公开完成证据：`public_hover_goal_and_required_action_evidence_verified`。

## duration_hover-candidate

输入错误：`{}`。
恢复：`not_estimable_no_trigger`。
公开完成证据：`elapsed_hover_duration_not_verified_by_current_public_api`。

## duration_hover-control

输入错误：`{}`。
恢复：`not_estimable_no_trigger`。
公开完成证据：`elapsed_hover_duration_not_verified_by_current_public_api`。

## 解释边界

- Known development scene, one repetition per condition/arm; not a generalization or significance claim.
- Both arms use compact_tools_v1/search_confirm_v9; completion and input contracts change together.
- Independent truth, common public assessment, online candidate result and auxiliary old checker are separate.
- Duration is unsupported; report its two slots separately, never add them as ordinary successes.
- Recovery requires a new model response after seeing feedback; parallel preselected/program calls do not prove model recovery.
- Argument findings, observed rejection, and backend effects are distinct; object duplicate keys may be collapsed upstream.
- Token counts are actual response usage; missing coverage is not zero and request bytes are not token estimates.
- Conservative cache-miss accounting is not the supplier invoice; no hidden reasoning is exported.
