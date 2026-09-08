#!/usr/bin/env python3
"""Validate locally built token IDs and preservation of tokenizer added tokens."""
import json
import pathlib
import sys


def validate(vocab, model):
    if vocab.is_symlink() or not vocab.is_file():
        raise ValueError("Draft vocabulary must be an ordinary file")
    values = [int(line) for line in vocab.read_text().splitlines() if line.strip()]
    if not 0 < len(values) < 248320 or len(values) != len(set(values)):
        raise ValueError("Draft vocabulary must contain a nonempty, unique, reduced set of token IDs")
    if min(values) < 0 or max(values) >= 248320:
        raise ValueError("Draft vocabulary token ID is outside this pinned tokenizer")
    tokenizer = json.loads((model / "tokenizer.json").read_text())
    added = {int(entry["id"]) for entry in tokenizer.get("added_tokens", [])}
    config = json.loads((model / "tokenizer_config.json").read_text())
    added |= {int(key) for key in config.get("added_tokens_decoder", {})}
    if not added or not added.issubset(values):
        raise ValueError("Draft vocabulary misses added/special tokenizer IDs; rebuild with the pinned tokenizer")
    return len(values)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: validate_draft_vocab.py VOCAB MODEL_SNAPSHOT")
    try:
        count = validate(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Draft vocabulary validated: {count} unique token IDs; added/special tokens retained")
