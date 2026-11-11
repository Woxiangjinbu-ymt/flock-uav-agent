# DeepSeek 悬停与输入契约同期对照

计划 8 槽，启动 8，评分 8。本批为真实模型小规模开发验证。

| 槽 | 正常返回 | 独立真值 | 公开证据完成 | 原辅助 checker | 实际 token | input_json object/string |
|---|---:|---|---|---|---:|---|
| state_hover-control | True | True | True | True | 54793 | 0 / 4 |
| state_hover-candidate | True | True | 未取得/不可估计 | True | 73991 | 4 / 0 |
| explicit_hover-candidate | True | True | True | True | 87705 | 5 / 0 |
| explicit_hover-control | True | True | True | True | 79232 | 0 / 7 |
| unknown_takeoff-control | True | True | True | True | 57260 | 1 / 4 |
| unknown_takeoff-candidate | True | True | True | True | 73401 | 3 / 0 |
| duration_hover-candidate | True | 未取得/不可估计 | 未取得/不可估计 | True | 87594 | 5 / 0 |
| duration_hover-control | True | 未取得/不可估计 | 未取得/不可估计 | True | 65814 | 0 / 6 |

## all_8

```json
{
  "all": {
    "planned": 8,
    "started": 8,
    "unstarted": [],
    "scored": 8,
    "public_evidence_verified": 5,
    "independent_truth_verified": 6,
    "independent_truth_false": 0,
    "independent_truth_unverified": 2,
    "prompt_tokens": 573490,
    "prompt_tokens_observed_trials": 8,
    "completion_tokens": 6300,
    "completion_tokens_observed_trials": 8,
    "total_tokens": 579790,
    "total_tokens_observed_trials": 8,
    "known_usage_responses": 53,
    "known_usage_responses_observed_trials": 8,
    "delegated_http_attempts": 53,
    "delegated_http_attempts_observed_trials": 8,
    "usage_complete_trials": 8,
    "prompt_cache_hit_tokens_known_sum": 444800,
    "prompt_cache_hit_tokens_reported_responses": 53,
    "prompt_cache_miss_tokens_known_sum": 128690,
    "prompt_cache_miss_tokens_reported_responses": 53,
    "action_attempts": 15,
    "api_requests": 89,
    "worker_elapsed_seconds_known_sum": 81.86425675000282,
    "conservative_usd_known_sum": "0.1796070",
    "representation_counts": {
      "string": 21,
      "object": 18
    },
    "argument_finding_counts": {
      "legacy_string_type_mismatch": 1
    },
    "semantic_finding_counts": {
      "unexpected_control_fields": 1
    },
    "feedback_error_counts": {
      "tool_parameter_semantic_error": 1,
      "input_representation_type_error": 1,
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
    "prompt_tokens": 253617,
    "prompt_tokens_observed_trials": 4,
    "completion_tokens": 3482,
    "completion_tokens_observed_trials": 4,
    "total_tokens": 257099,
    "total_tokens_observed_trials": 4,
    "known_usage_responses": 27,
    "known_usage_responses_observed_trials": 4,
    "delegated_http_attempts": 27,
    "delegated_http_attempts_observed_trials": 4,
    "usage_complete_trials": 4,
    "prompt_cache_hit_tokens_known_sum": 192384,
    "prompt_cache_hit_tokens_reported_responses": 27,
    "prompt_cache_miss_tokens_known_sum": 61233,
    "prompt_cache_miss_tokens_reported_responses": 27,
    "action_attempts": 8,
    "api_requests": 52,
    "worker_elapsed_seconds_known_sum": 44.58469433400023,
    "conservative_usd_known_sum": "0.0802635",
    "representation_counts": {
      "string": 21,
      "object": 1
    },
    "argument_finding_counts": {
      "legacy_string_type_mismatch": 1
    },
    "semantic_finding_counts": {
      "unexpected_control_fields": 1
    },
    "feedback_error_counts": {
      "tool_parameter_semantic_error": 1,
      "input_representation_type_error": 1,
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
    "public_evidence_verified": 2,
    "independent_truth_verified": 3,
    "independent_truth_false": 0,
    "independent_truth_unverified": 1,
    "prompt_tokens": 319873,
    "prompt_tokens_observed_trials": 4,
    "completion_tokens": 2818,
    "completion_tokens_observed_trials": 4,
    "total_tokens": 322691,
    "total_tokens_observed_trials": 4,
    "known_usage_responses": 26,
    "known_usage_responses_observed_trials": 4,
    "delegated_http_attempts": 26,
    "delegated_http_attempts_observed_trials": 4,
    "usage_complete_trials": 4,
    "prompt_cache_hit_tokens_known_sum": 252416,
    "prompt_cache_hit_tokens_reported_responses": 26,
    "prompt_cache_miss_tokens_known_sum": 67457,
    "prompt_cache_miss_tokens_reported_responses": 26,
    "action_attempts": 7,
    "api_requests": 37,
    "worker_elapsed_seconds_known_sum": 37.27956241600259,
    "conservative_usd_known_sum": "0.0993435",
    "representation_counts": {
      "object": 17
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
    "public_evidence_verified": 5,
    "independent_truth_verified": 6,
    "independent_truth_false": 0,
    "independent_truth_unverified": 0,
    "prompt_tokens": 421779,
    "prompt_tokens_observed_trials": 6,
    "completion_tokens": 4603,
    "completion_tokens_observed_trials": 6,
    "total_tokens": 426382,
    "total_tokens_observed_trials": 6,
    "known_usage_responses": 39,
    "known_usage_responses_observed_trials": 6,
    "delegated_http_attempts": 39,
    "delegated_http_attempts_observed_trials": 6,
    "usage_complete_trials": 6,
    "prompt_cache_hit_tokens_known_sum": 312576,
    "prompt_cache_hit_tokens_reported_responses": 39,
    "prompt_cache_miss_tokens_known_sum": 109203,
    "prompt_cache_miss_tokens_reported_responses": 39,
    "action_attempts": 11,
    "api_requests": 69,
    "worker_elapsed_seconds_known_sum": 59.13282595900455,
    "conservative_usd_known_sum": "0.1320573",
    "representation_counts": {
      "string": 15,
      "object": 13
    },
    "argument_finding_counts": {
      "legacy_string_type_mismatch": 1
    },
    "semantic_finding_counts": {
      "unexpected_control_fields": 1
    },
    "feedback_error_counts": {
      "tool_parameter_semantic_error": 1,
      "input_representation_type_error": 1,
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
    "prompt_tokens": 188630,
    "prompt_tokens_observed_trials": 3,
    "completion_tokens": 2655,
    "completion_tokens_observed_trials": 3,
    "total_tokens": 191285,
    "total_tokens_observed_trials": 3,
    "known_usage_responses": 20,
    "known_usage_responses_observed_trials": 3,
    "delegated_http_attempts": 20,
    "delegated_http_attempts_observed_trials": 3,
    "usage_complete_trials": 3,
    "prompt_cache_hit_tokens_known_sum": 135424,
    "prompt_cache_hit_tokens_reported_responses": 20,
    "prompt_cache_miss_tokens_known_sum": 53206,
    "prompt_cache_miss_tokens_reported_responses": 20,
    "action_attempts": 6,
    "api_requests": 42,
    "worker_elapsed_seconds_known_sum": 33.25564550100171,
    "conservative_usd_known_sum": "0.0597750",
    "representation_counts": {
      "string": 15,
      "object": 1
    },
    "argument_finding_counts": {
      "legacy_string_type_mismatch": 1
    },
    "semantic_finding_counts": {
      "unexpected_control_fields": 1
    },
    "feedback_error_counts": {
      "tool_parameter_semantic_error": 1,
      "input_representation_type_error": 1,
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
    "public_evidence_verified": 2,
    "independent_truth_verified": 3,
    "independent_truth_false": 0,
    "independent_truth_unverified": 0,
    "prompt_tokens": 233149,
    "prompt_tokens_observed_trials": 3,
    "completion_tokens": 1948,
    "completion_tokens_observed_trials": 3,
    "total_tokens": 235097,
    "total_tokens_observed_trials": 3,
    "known_usage_responses": 19,
    "known_usage_responses_observed_trials": 3,
    "delegated_http_attempts": 19,
    "delegated_http_attempts_observed_trials": 3,
    "usage_complete_trials": 3,
    "prompt_cache_hit_tokens_known_sum": 177152,
    "prompt_cache_hit_tokens_reported_responses": 19,
    "prompt_cache_miss_tokens_known_sum": 55997,
    "prompt_cache_miss_tokens_reported_responses": 19,
    "action_attempts": 5,
    "api_requests": 27,
    "worker_elapsed_seconds_known_sum": 25.87718045800284,
    "conservative_usd_known_sum": "0.0722823",
    "representation_counts": {
      "object": 12
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
    "prompt_tokens": 151711,
    "prompt_tokens_observed_trials": 2,
    "completion_tokens": 1697,
    "completion_tokens_observed_trials": 2,
    "total_tokens": 153408,
    "total_tokens_observed_trials": 2,
    "known_usage_responses": 14,
    "known_usage_responses_observed_trials": 2,
    "delegated_http_attempts": 14,
    "delegated_http_attempts_observed_trials": 2,
    "usage_complete_trials": 2,
    "prompt_cache_hit_tokens_known_sum": 132224,
    "prompt_cache_hit_tokens_reported_responses": 14,
    "prompt_cache_miss_tokens_known_sum": 19487,
    "prompt_cache_miss_tokens_reported_responses": 14,
    "action_attempts": 4,
    "api_requests": 20,
    "worker_elapsed_seconds_known_sum": 22.731430790998274,
    "conservative_usd_known_sum": "0.0475497",
    "representation_counts": {
      "object": 5,
      "string": 6
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
    "prompt_tokens": 64987,
    "prompt_tokens_observed_trials": 1,
    "completion_tokens": 827,
    "completion_tokens_observed_trials": 1,
    "total_tokens": 65814,
    "total_tokens_observed_trials": 1,
    "known_usage_responses": 7,
    "known_usage_responses_observed_trials": 1,
    "delegated_http_attempts": 7,
    "delegated_http_attempts_observed_trials": 1,
    "usage_complete_trials": 1,
    "prompt_cache_hit_tokens_known_sum": 56960,
    "prompt_cache_hit_tokens_reported_responses": 7,
    "prompt_cache_miss_tokens_known_sum": 8027,
    "prompt_cache_miss_tokens_reported_responses": 7,
    "action_attempts": 2,
    "api_requests": 10,
    "worker_elapsed_seconds_known_sum": 11.329048832998524,
    "conservative_usd_known_sum": "0.0204885",
    "representation_counts": {
      "string": 6
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
    "prompt_tokens": 86724,
    "prompt_tokens_observed_trials": 1,
    "completion_tokens": 870,
    "completion_tokens_observed_trials": 1,
    "total_tokens": 87594,
    "total_tokens_observed_trials": 1,
    "known_usage_responses": 7,
    "known_usage_responses_observed_trials": 1,
    "delegated_http_attempts": 7,
    "delegated_http_attempts_observed_trials": 1,
    "usage_complete_trials": 1,
    "prompt_cache_hit_tokens_known_sum": 75264,
    "prompt_cache_hit_tokens_reported_responses": 7,
    "prompt_cache_miss_tokens_known_sum": 11460,
    "prompt_cache_miss_tokens_reported_responses": 7,
    "action_attempts": 2,
    "api_requests": 10,
    "worker_elapsed_seconds_known_sum": 11.40238195799975,
    "conservative_usd_known_sum": "0.0270612",
    "representation_counts": {
      "object": 5
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
    "prompt_tokens": 188630,
    "prompt_tokens_observed_trials": 3,
    "completion_tokens": 2655,
    "completion_tokens_observed_trials": 3,
    "total_tokens": 191285,
    "total_tokens_observed_trials": 3,
    "known_usage_responses": 20,
    "known_usage_responses_observed_trials": 3,
    "delegated_http_attempts": 20,
    "delegated_http_attempts_observed_trials": 3,
    "usage_complete_trials": 3,
    "prompt_cache_hit_tokens_known_sum": 135424,
    "prompt_cache_hit_tokens_reported_responses": 20,
    "prompt_cache_miss_tokens_known_sum": 53206,
    "prompt_cache_miss_tokens_reported_responses": 20,
    "action_attempts": 6,
    "api_requests": 42,
    "worker_elapsed_seconds_known_sum": 33.25564550100171,
    "conservative_usd_known_sum": "0.0597750",
    "representation_counts": {
      "string": 15,
      "object": 1
    },
    "argument_finding_counts": {
      "legacy_string_type_mismatch": 1
    },
    "semantic_finding_counts": {
      "unexpected_control_fields": 1
    },
    "feedback_error_counts": {
      "tool_parameter_semantic_error": 1,
      "input_representation_type_error": 1,
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
    "public_evidence_verified": 2,
    "independent_truth_verified": 3,
    "independent_truth_false": 0,
    "independent_truth_unverified": 0,
    "prompt_tokens": 233149,
    "prompt_tokens_observed_trials": 3,
    "completion_tokens": 1948,
    "completion_tokens_observed_trials": 3,
    "total_tokens": 235097,
    "total_tokens_observed_trials": 3,
    "known_usage_responses": 19,
    "known_usage_responses_observed_trials": 3,
    "delegated_http_attempts": 19,
    "delegated_http_attempts_observed_trials": 3,
    "usage_complete_trials": 3,
    "prompt_cache_hit_tokens_known_sum": 177152,
    "prompt_cache_hit_tokens_reported_responses": 19,
    "prompt_cache_miss_tokens_known_sum": 55997,
    "prompt_cache_miss_tokens_reported_responses": 19,
    "action_attempts": 5,
    "api_requests": 27,
    "worker_elapsed_seconds_known_sum": 25.87718045800284,
    "conservative_usd_known_sum": "0.0722823",
    "representation_counts": {
      "object": 12
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

输入错误：`{"tool_parameter_semantic_error": 1}`。
恢复：`not_estimable_no_trigger`。
公开完成证据：`public_hover_goal_and_required_action_evidence_verified`。

## state_hover-candidate

输入错误：`{}`。
恢复：`not_estimable_no_trigger`。
公开完成证据：`fresh_pre_takeoff_position_anchor_required`。

## explicit_hover-candidate

输入错误：`{}`。
恢复：`not_estimable_no_trigger`。
公开完成证据：`public_hover_goal_and_required_action_evidence_verified`。

## explicit_hover-control

输入错误：`{}`。
恢复：`not_estimable_no_trigger`。
公开完成证据：`public_hover_goal_and_required_action_evidence_verified`。

## unknown_takeoff-control

输入错误：`{"input_representation_type_error": 1, "unknown_execution_effect": 1}`。
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
