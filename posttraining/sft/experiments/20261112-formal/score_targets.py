"""Score the same twelve original training probes and nine newly reviewed final replies."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import torch

from posttraining.inference.weights import MANIFEST, verify_weights
from posttraining.sft.benchmark import load_text_model
from posttraining.sft.checkpoint import load_adapter
from posttraining.sft.train import read_release

def main():
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('model', 'adapter', 'first', 'closing', 'output'):
        cli.add_argument('--' + name, type=Path, required=True)
    args = cli.parse_args()
    revision = json.loads(MANIFEST.read_bytes())['revision']
    model_hash = verify_weights(args.model)
    first, _, first_manifest = read_release(torch, args.first, revision)
    closing, _, closing_manifest = read_release(torch, args.closing, revision)
    selected = {}
    for sample in first:
        ref = sample[2]
        key = hashlib.sha256(('flockcue-first-pass-loss-' + ref['train_row_sha256']).encode()).hexdigest()
        scene = ref['session_id']
        if scene not in selected or key < selected[scene][0]:
            selected[scene] = (key, sample)
    groups = {
        'original_training_probes': [sample for _, sample in selected.values()],
        'reviewed_closings': [sample for sample in closing if sample[2]['kind'] == 'closing'],
    }
    assert [len(rows) for rows in groups.values()] == [12, 9]
    model = load_text_model(args.model)
    adapter = load_adapter(model, args.adapter, revision)
    model.eval()
    result = dict(model_revision=revision, model_manifest_sha256=model_hash, adapter=adapter,
                  first_manifest_sha256=first_manifest, closing_manifest_sha256=closing_manifest, groups={})
    with torch.inference_mode(), torch.autocast('cuda', dtype=torch.bfloat16):
        for name, samples in groups.items():
            scores = []
            for tokens, start, ref in samples:
                ids = tokens.to(device='cuda', dtype=torch.long).unsqueeze(0)
                hidden = model.model(input_ids=ids, use_cache=False, return_dict=True).last_hidden_state
                logits = model.lm_head(hidden[0, start-1:-1, :])
                loss = torch.nn.functional.cross_entropy(logits.float(), ids[0,start:]).item()
                if not math.isfinite(loss):
                    raise ValueError('Nonfinite loss')
                scores.append(dict(sha256=ref['train_row_sha256'], task_id=ref['task_id'],
                                   supervised_tokens=ref['supervised_tokens'], loss=loss))
                del hidden, logits, ids
            count = sum(row['supervised_tokens'] for row in scores)
            result['groups'][name] = dict(rows=scores, supervised_tokens=count,
                                          loss=sum(r['loss'] * r['supervised_tokens'] for r in scores) / count)
    tmp = args.output.with_suffix('.tmp')
    tmp.write_text(json.dumps(result, indent=2) + '\n')
    tmp.replace(args.output)
    print({key: value['loss'] for key, value in result['groups'].items()})

if __name__ == '__main__':
    main()
