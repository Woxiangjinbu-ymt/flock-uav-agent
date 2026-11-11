"""Check the tokenizer boundary on the four public, development-only excerpts."""
import json
import sys
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(DIRECTORY.parents[2] / "scripts"))
from student_input import encode_decision, load_tokenizer


def main():
    examples = json.loads((DIRECTORY / "evidence-examples.json").read_text())["examples"]
    tokenizer = load_tokenizer()
    for example in examples:
        observation = json.dumps(
            example["public_prefix_excerpt"], ensure_ascii=False, separators=(",", ":")
        )
        messages = [
            {"role": "system", "content": "根据公开前缀简述已证实的进展和仍缺的证据。"},
            {"role": "user", "content": observation},
        ]
        target = {"role": "assistant", "content": example["provisional_annotation_from_prefix_cn"]}
        encoded = encode_decision(tokenizer, messages, [], target, max_length=4096)
        boundary = encoded["target_start"]
        assert all(label == -100 for label in encoded["labels"][:boundary])
        assert encoded["labels"][boundary:] == encoded["input_ids"][boundary:]
        assert not encoded["training_eligible"] and not encoded["truncated"]
        print(example["trial"], boundary, encoded["supervised_tokens"], encoded["sequence_length"])


if __name__ == "__main__":
    main()
