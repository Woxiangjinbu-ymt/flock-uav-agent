"""Time first and final tokens against the process monotonic clock."""
import math
import time


class TokenTimes:
    """Transformers puts the prompt once, then a CPU tensor per decode step."""
    def __init__(self):
        self.prompt_seen = False
        self.first = None
        self.last = None
        self.count = 0

    def put(self, value):
        if not self.prompt_seen:
            self.prompt_seen = True
            return
        if value.numel() != 1:
            raise ValueError("Expected one new token from batch-1 unassisted generation")
        now = time.monotonic()
        self.first = now if self.first is None else self.first
        self.last = now
        self.count += 1

    def end(self):
        pass


def generation_stats(ids, started, finished, *, source, first=None, last=None,
                     reserved=None, allocated=None, unavailable_reason=None):
    if not math.isfinite(started) or not math.isfinite(finished) or finished < started:
        raise ValueError("invalid generation clock interval")
    if first is not None and not started <= first <= finished:
        raise ValueError("first token outside generation interval")
    if last is not None and (first is None or not first <= last <= finished):
        raise ValueError("last token outside generation interval")
    unavailable = {}
    if first is None:
        unavailable["ttft_seconds"] = unavailable_reason or "no_measured_first_token"
    decode = last - first if first is not None and last is not None and len(ids) > 1 else None
    if decode is None or decode <= 0:
        reason = ("fewer_than_two_tokens" if len(ids) < 2 else
                  unavailable_reason or "no_measured_positive_decode_interval")
        decode = None
        unavailable.update(decode_seconds=reason, decode_tokens_per_second=reason)
    if reserved is None:
        unavailable["peak_vram_bytes"] = ("cpu_fixture_no_gpu" if source == "cpu_scripted_bulk"
                                          else "engine_does_not_expose_per_request_allocator_peak")
    return {"generated_ids": ids, "first_token_monotonic": first, "peak_vram_bytes": reserved,
            "peak_allocated_bytes": allocated, "timing": {
                "timing_source": source, "generation_seconds": finished - started,
                "decode_seconds": decode, "decode_tokens_per_second": (len(ids)-1)/decode if decode else None,
                "unavailable": unavailable}}
