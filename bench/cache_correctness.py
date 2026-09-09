#!/usr/bin/env python3
"""Check prefix-cache correctness and greedy QSA determinism without server changes.

Generated answers are compared in memory and represented only by SHA-256 hashes.
The tool accepts credential-free loopback HTTP endpoints only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import secrets
import sys
from datetime import datetime, timezone

import runtime_validation as runtime


INSTRUCTIONS = {
    "prime-list": "Return exactly the first 16 prime numbers as one comma-separated line. /no_think",
    "python": "Write a Python function that merges overlapping closed integer intervals. Return code only. /no_think",
    "json": 'Return only JSON with keys "sum" and "product" for the integers 37 and 41. /no_think',
    "extract": "Read the context and return only the final validation section number as an integer. /no_think",
}


def parse_sizes(value: str) -> list[int]:
    """Parse unique comma-separated context sizes; zero means no added corpus."""
    try:
        values = [int(item) for item in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("sizes must be comma-separated integers") from exc
    if not values or len(values) != len(set(values)) or any(x < 0 or x > 250_000 for x in values):
        raise argparse.ArgumentTypeError("sizes must be unique values from 0 through 250000")
    return values


def answer_hash(text: str) -> str:
    """Represent generated text without retaining or printing the answer itself."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_prompt(
    base: str,
    model: str,
    corpus: pathlib.Path,
    target_tokens: int,
    instruction: str,
    marker: str,
) -> tuple[str, int]:
    """Build a real-text prompt with an early unique marker and target token count."""
    prefix = f"Validation marker {marker}. This marker identifies one test case.\n"
    if target_tokens == 0:
        prompt = prefix + instruction
        return prompt, runtime.tokenize_count(base, model, prompt)
    source = runtime.corpus_text(corpus, max(50_000, target_tokens * 8))
    low, high = 1, len(source)
    best = prefix + source + "\n\n" + instruction
    best_count = runtime.tokenize_count(base, model, best)
    for _ in range(20):
        middle = (low + high) // 2
        candidate = prefix + source[:middle] + "\n\n" + instruction
        count = runtime.tokenize_count(base, model, candidate)
        if abs(count - target_tokens) < abs(best_count - target_tokens):
            best, best_count = candidate, count
        if count < target_tokens:
            low = middle + 1
        else:
            high = middle - 1
    return best, best_count


def completion(base: str, model: str, prompt: str, max_tokens: int) -> dict:
    """Run one greedy completion and retain text only until its hash is computed."""
    response = runtime.request_json(
        base,
        "/v1/completions",
        {
            "model": model,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": 0,
            "logprobs": 5,
        },
        timeout=1800,
    )
    choice = response["choices"][0]
    text = choice.get("text", "")
    scores = runtime.first_logprobs(choice)
    return {
        "hash": answer_hash(text),
        "chars": len(text),
        "finish_reason": choice.get("finish_reason"),
        "scores": scores,
    }


def public_sample(sample: dict) -> dict:
    """Remove score values after comparison while recording their availability."""
    return {
        "answer_sha256": sample["hash"],
        "answer_chars": sample["chars"],
        "finish_reason": sample["finish_reason"],
        "first_logprobs_available": bool(sample["scores"]),
    }


def samples_match(left: dict, right: dict) -> tuple[bool, bool]:
    """Return exact-text-hash and first-token-score equality decisions."""
    return left["hash"] == right["hash"], runtime.scores_equal(left["scores"], right["scores"])


def cache_case(args: argparse.Namespace, target: int) -> dict:
    """Compare a unique first observation with an immediate prefix-cache hit."""
    marker = secrets.token_hex(16)
    prompt, actual = make_prompt(
        args.base_url, args.model, args.corpus, target, INSTRUCTIONS["extract"], marker
    )
    hits_before = runtime.prefix_hits(args.base_url)
    first = completion(args.base_url, args.model, prompt, args.max_tokens)
    hits_after_first = runtime.prefix_hits(args.base_url)
    repeated = completion(args.base_url, args.model, prompt, args.max_tokens)
    hits_after_repeat = runtime.prefix_hits(args.base_url)
    text_equal, scores_equal = samples_match(first, repeated)
    hit = None if hits_after_first is None or hits_after_repeat is None else hits_after_repeat > hits_after_first
    return {
        "requested_tokens": target,
        "actual_tokens": actual,
        "first": public_sample(first),
        "repeat": public_sample(repeated),
        "text_equal": text_equal,
        "first_logprobs_equal": scores_equal,
        "prefix_hits_before": hits_before,
        "prefix_hits_after_first": hits_after_first,
        "prefix_hits_after_repeat": hits_after_repeat,
        "repeated_prefix_hit": hit,
        "passed": text_equal and scores_equal and (hit is True or not args.require_prefix_hit),
    }


def qsa_case(args: argparse.Namespace, target: int, name: str, instruction: str) -> dict:
    """Repeat one prompt across a QSA-relevant shape and detect any variation."""
    marker = secrets.token_hex(16)
    prompt, actual = make_prompt(args.base_url, args.model, args.corpus, target, instruction, marker)
    samples = [completion(args.base_url, args.model, prompt, args.max_tokens) for _ in range(args.repeats)]
    text_stable = all(sample["hash"] == samples[0]["hash"] for sample in samples[1:])
    score_stable = all(runtime.scores_equal(samples[0]["scores"], sample["scores"]) for sample in samples[1:])
    return {
        "name": name,
        "requested_tokens": target,
        "actual_tokens": actual,
        "repeats": args.repeats,
        "unique_answer_hashes": sorted({sample["hash"] for sample in samples}),
        "first_logprobs_available": bool(samples[0]["scores"]),
        "text_stable": text_stable,
        "first_logprobs_stable": score_stable,
        "passed": text_stable and score_stable,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("mode", choices=("cache", "qsa", "all"))
    result.add_argument("--base-url", default="http://127.0.0.1:8888")
    result.add_argument("--model", default=runtime.MODEL)
    result.add_argument("--corpus", type=pathlib.Path, default=runtime.ROOT / "README.md")
    result.add_argument("--cache-sizes", type=parse_sizes, default=parse_sizes("8192,32768,131072"))
    result.add_argument("--qsa-sizes", type=parse_sizes, default=parse_sizes("0,8192,32768"))
    result.add_argument("--repeats", type=int, default=5)
    result.add_argument("--max-tokens", type=int, default=96)
    result.add_argument("--require-prefix-hit", action="store_true")
    result.add_argument("--output", type=pathlib.Path)
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        args.base_url = runtime.validate_base_url(args.base_url)
    except ValueError as exc:
        parser().error(str(exc))
    if not args.corpus.is_file():
        parser().error(f"Corpus file does not exist: {args.corpus}")
    if not 2 <= args.repeats <= 20:
        parser().error("--repeats must be between 2 and 20")
    if not 16 <= args.max_tokens <= 512:
        parser().error("--max-tokens must be between 16 and 512")
    runtime.check_backend(args.base_url, args.model)

    report = {
        "schema": 1,
        "mode": args.mode,
        "model": args.model,
        "started": datetime.now(timezone.utc).isoformat(),
        "generated_text_retained": False,
    }
    if args.mode in {"cache", "all"}:
        report["cache"] = [cache_case(args, size) for size in args.cache_sizes]
    if args.mode in {"qsa", "all"}:
        report["qsa"] = [
            qsa_case(args, size, name, instruction)
            for size in args.qsa_sizes
            for name, instruction in INSTRUCTIONS.items()
        ]
    cases = [case for group in (report.get("cache", []), report.get("qsa", [])) for case in group]
    report["passed"] = all(case["passed"] for case in cases)
    report["completed"] = datetime.now(timezone.utc).isoformat()
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    else:
        sys.stdout.write(rendered)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
