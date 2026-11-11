"""Check the released training rows against their registered 2B token projection."""
import argparse
import gzip
import json
from pathlib import Path

from .adapter import DEFAULT_CACHE, load_tokenizer
from .export import PROJECTION, PUBLIC, RELEASE, verified_examples


def verify(release, tokenizer):
    counts = {"decisions": 0, "supervised_tokens": 0, "reasoning_tokens": 0}
    with (release / PROJECTION).open("rb") as audit, gzip.open(release / PUBLIC, "rb") as source:
        for _, _, _, actual in verified_examples(audit, source, tokenizer):
            counts["decisions"] += 1
            counts["supervised_tokens"] += actual["supervised_tokens"]
            counts["reasoning_tokens"] += actual["supervised_reasoning_tokens"]
    return counts


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--release", type=Path, default=RELEASE, help="teacher release with public/ and audit/")
    cli.add_argument("--tokenizer", type=Path, default=DEFAULT_CACHE, help="pinned native Qwen3.5-2B tokenizer files")
    args = cli.parse_args()
    print(json.dumps(verify(args.release, load_tokenizer(args.tokenizer))))


if __name__ == "__main__":
    main()
