"""Check the released training rows against their registered 2B token projection."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

from .adapter import DEFAULT_CACHE, load_tokenizer
from .supervision import supervise

RELEASE = Path(__file__).resolve().parents[2] / 'datasets/teacher/2026-11-11-fold-a'

FIELDS = {'masked_prompt_tokens': 'prompt_tokens', 'supervised_tokens': 'target_tokens',
          'supervised_reasoning_tokens': 'target_reasoning_tokens',
          'prompt_ids_sha256': 'prompt_ids_sha256', 'target_ids_sha256': 'target_ids_sha256'}

def verify(release, tokenizer):
    projection = release / 'audit/reviewed-qwen-projection.jsonl'
    public = release / 'public/reviewed-compatible-public-turns.jsonl.gz'
    counts = {'decisions': 0, 'supervised_tokens': 0, 'reasoning_tokens': 0}
    with projection.open('rb') as audit, gzip.open(public, 'rb') as source:
        reviewed = (row for row in map(json.loads, audit) if row['status'] == 'compatible')
        for line_number, (record, line) in enumerate(zip(reviewed, source, strict=True), 1):
            if line_number != record['reviewed_file_line'] or hashlib.sha256(line).hexdigest() != record['source_row_sha256']:
                raise ValueError(f'Reviewed row {line_number} differs from its registered identity')
            row = json.loads(line)
            _, _, actual = supervise(tokenizer, row['messages'], row['tools'], row['target'])
            for field, registered in FIELDS.items():
                if actual[field] != record[registered]:
                    raise ValueError(f'Reviewed row {line_number} differs in {field}')
            counts['decisions'] += 1
            counts['supervised_tokens'] += actual['supervised_tokens']
            counts['reasoning_tokens'] += actual['supervised_reasoning_tokens']
    if counts['decisions'] != 386:
        raise ValueError(f'Expected 386 reviewed decisions, got {counts["decisions"]}')
    return counts


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--release', type=Path, default=RELEASE, help='teacher release with public/ and audit/')
    cli.add_argument('--tokenizer', type=Path, default=DEFAULT_CACHE, help='pinned native Qwen3.5-2B tokenizer files')
    args = cli.parse_args()
    print(json.dumps(verify(args.release, load_tokenizer(args.tokenizer))))


if __name__ == '__main__':
    main()
