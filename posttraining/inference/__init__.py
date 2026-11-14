"""Pinned native student decoding backends and execution budgets."""

MAX_CONTEXT = 32768
EVALUATION_CONTEXTS = (MAX_CONTEXT, 65536)
MAX_GENERATION = 2048
EPISODE_TOKEN_LIMIT = 100000
SAMPLING = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "repetition_penalty": 1.0}


def output_budget(prompt_tokens, spent_tokens, episode_limit=EPISODE_TOKEN_LIMIT, context_tokens=MAX_CONTEXT):
    available = MAX_GENERATION
    if episode_limit is not None:
        available = min(available, episode_limit - spent_tokens - prompt_tokens)
    if available < 1:
        return 0, "token_budget"
    if prompt_tokens + available > context_tokens:
        return 0, "context_length"
    return available, None
