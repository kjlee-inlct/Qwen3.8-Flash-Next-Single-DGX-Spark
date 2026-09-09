#!/usr/bin/env python3
"""Validate a running Qwen3.8-Flash-Next backend without changing it.

Why this exists:
    Performance patches for prefix caching, QSA top-k and prefill scheduling can
    silently change output or improve one benchmark while hurting live clients.
    This tool establishes evidence on the exact pinned image before such patches
    are considered.

What it checks:
    ``smoke`` verifies health/model identity, coherent output, deterministic
    greedy text and first-token logprobs, long-prompt TTFT, and prefix-cache hits
    when the metric is exported.

    ``mixed`` compares ordinary decode pacing with decode pacing while a long
    prefill request is running. This exposes multi-client stalls that aggregate
    tokens/second can hide.

How it stays safe:
    Only a credential-free loopback HTTP endpoint is accepted. It does not edit
    configuration, clear caches, stop containers, or save generated text.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterable


MODEL = "qwen3.8-flash-next"
ROOT = pathlib.Path(__file__).resolve().parents[1]
DETERMINISM_PROMPT = (
    "Return exactly one line containing the first eight prime numbers, separated "
    "by commas. Do not add commentary. /no_think"
)
DECODE_PROMPT = (
    "Explain how an operating-system page cache serves random reads from an "
    "NVMe-backed memory mapping. Write at least 350 words with no headings. /no_think"
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Prevent a misconfigured endpoint from redirecting requests off-host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Backend redirected the request; refusing to leave loopback")


def validate_base_url(value: str) -> str:
    """Return a normalized credential-free loopback URL or raise ValueError."""

    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("--base-url must be credential-free loopback HTTP with no path")
    return value.rstrip("/")


def opener() -> urllib.request.OpenerDirector:
    """Build an opener that ignores proxy environment variables and redirects."""

    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def request_json(base: str, path: str, payload: dict | None = None, timeout: int = 600) -> dict:
    """Perform one local JSON request and return the decoded object."""

    data = None if payload is None else json.dumps(payload).encode()
    headers = {} if data is None else {"Content-Type": "application/json"}
    request = urllib.request.Request(base + path, data=data, headers=headers)
    with opener().open(request, timeout=timeout) as response:
        return json.load(response)


def iter_sse(response: Iterable[bytes]) -> Iterable[dict]:
    """Yield JSON Server-Sent Events, ignoring comments and the DONE marker."""

    for raw_line in response:
        line = raw_line.decode("utf-8", "replace").strip()
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            return
        event = json.loads(payload)
        if event.get("error"):
            raise RuntimeError(f"Backend stream error: {event['error']}")
        yield event


def prefix_hits(base: str) -> float | None:
    """Read vLLM's prefix hit counter, or return None when unavailable."""

    try:
        request = urllib.request.Request(base + "/metrics")
        with opener().open(request, timeout=10) as response:
            lines = response.read().decode("utf-8", "replace").splitlines()
    except (OSError, urllib.error.URLError):
        return None
    values = []
    for line in lines:
        if line.startswith("vllm:prefix_cache_hits_total"):
            try:
                values.append(float(line.rsplit(" ", 1)[1]))
            except (IndexError, ValueError):
                continue
    return sum(values) if values else None


def tokenize_count(base: str, model: str, text: str) -> int:
    """Use the serving tokenizer so the requested prefill size is reproducible."""

    result = request_json(base, "/tokenize", {"model": model, "prompt": text})
    count = result.get("count")
    if isinstance(count, int):
        return count
    tokens = result.get("tokens")
    if isinstance(tokens, list):
        return len(tokens)
    raise RuntimeError("/tokenize returned neither count nor tokens")


def corpus_text(path: pathlib.Path, minimum_chars: int) -> str:
    """Build varied local text with unique section markers, never model output."""

    source = path.read_text(encoding="utf-8", errors="replace")
    if not source.strip():
        raise ValueError(f"Corpus is empty: {path}")
    sections: list[str] = []
    total = 0
    index = 0
    while total < minimum_chars:
        section = f"\n\n<!-- validation section {index} -->\n\n{source}"
        sections.append(section)
        total += len(section)
        index += 1
    return "".join(sections)


def prompt_for_tokens(base: str, model: str, path: pathlib.Path, target: int) -> tuple[str, int]:
    """Binary-search a real-text prompt to approximately the requested token count."""

    text = corpus_text(path, max(50_000, target * 8))
    low, high = 1, len(text)
    best = text
    best_count = tokenize_count(base, model, text)
    for _ in range(18):
        middle = (low + high) // 2
        candidate = text[:middle] + "\n\nReply with only: ok"
        count = tokenize_count(base, model, candidate)
        if abs(count - target) < abs(best_count - target):
            best, best_count = candidate, count
        if count < target:
            low = middle + 1
        else:
            high = middle - 1
    return best, best_count


def first_logprobs(choice: dict) -> dict[str, float]:
    """Normalize completion or chat first-token top-logprobs for comparison."""

    logprobs = choice.get("logprobs") or {}
    if isinstance(logprobs.get("top_logprobs"), list) and logprobs["top_logprobs"]:
        return {str(k): float(v) for k, v in logprobs["top_logprobs"][0].items()}
    content = logprobs.get("content") or []
    if content:
        return {
            str(item["token"]): float(item["logprob"])
            for item in content[0].get("top_logprobs") or []
        }
    return {}


def deterministic_sample(base: str, model: str) -> tuple[str, dict[str, float]]:
    """Request a small greedy completion and return text plus first-token scores."""

    result = request_json(
        base,
        "/v1/completions",
        {
            "model": model,
            "prompt": DETERMINISM_PROMPT,
            "max_tokens": 32,
            "temperature": 0,
            "logprobs": 5,
        },
    )
    choice = result["choices"][0]
    return choice.get("text", ""), first_logprobs(choice)


def scores_equal(left: dict[str, float], right: dict[str, float], tolerance: float = 1e-6) -> bool:
    """Compare the same top-token set with a small numeric tolerance."""

    return bool(left) and left.keys() == right.keys() and all(
        math.isclose(left[key], right[key], abs_tol=tolerance, rel_tol=0)
        for key in left
    )


def stream_chat(
    base: str,
    model: str,
    prompt: str,
    max_tokens: int,
    on_first: Callable[[], None] | None = None,
) -> dict:
    """Stream one chat request and retain timing metadata, not generated text."""

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        base + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    event_times: list[float] = []
    completion_tokens = 0
    callback_called = False
    with opener().open(request, timeout=1800) as response:
        for event in iter_sse(response):
            usage = event.get("usage") or {}
            completion_tokens = usage.get("completion_tokens", completion_tokens)
            for choice in event.get("choices") or []:
                if (choice.get("delta") or {}).get("content"):
                    event_times.append(time.monotonic())
                    if on_first is not None and not callback_called:
                        callback_called = True
                        on_first()
    ended = time.monotonic()
    gaps = [right - left for left, right in zip(event_times, event_times[1:])]
    return {
        "elapsed_s": ended - started,
        "ttft_s": event_times[0] - started if event_times else None,
        "completion_tokens": completion_tokens,
        "content_events": len(event_times),
        "gaps": gaps,
    }


def long_prefill(base: str, model: str, prompt: str) -> dict:
    """Measure time to the first generated content after a long prompt."""

    return stream_chat(base, model, prompt, max_tokens=1)


def gap_summary(values: list[float]) -> dict:
    """Summarize stream-event gaps without assuming one event equals one token."""

    if not values:
        return {"count": 0, "median_s": None, "p95_s": None, "max_s": None}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "count": len(values),
        "median_s": round(statistics.median(values), 4),
        "p95_s": round(ordered[p95_index], 4),
        "max_s": round(max(values), 4),
    }


def check_backend(base: str, model: str) -> None:
    """Fail early unless health and the requested served-model ID are present."""

    request = urllib.request.Request(base + "/health")
    with opener().open(request, timeout=10) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Health endpoint returned HTTP {response.status}")
    models = request_json(base, "/v1/models")
    if not any(item.get("id") == model for item in models.get("data", [])):
        raise RuntimeError(f"Model {model!r} not present in /v1/models")


def run_smoke(args: argparse.Namespace) -> dict:
    """Run deterministic-output and repeated-long-prefix validation."""

    check_backend(args.base_url, args.model)
    samples = [deterministic_sample(args.base_url, args.model) for _ in range(args.repeats)]
    text_stable = all(text == samples[0][0] for text, _ in samples[1:])
    score_stable = all(scores_equal(samples[0][1], scores) for _, scores in samples[1:])

    prompt, actual_tokens = prompt_for_tokens(
        args.base_url, args.model, args.corpus, args.prefill_tokens
    )
    before = prefix_hits(args.base_url)
    cold = long_prefill(args.base_url, args.model, prompt)
    middle = prefix_hits(args.base_url)
    warm = long_prefill(args.base_url, args.model, prompt)
    after = prefix_hits(args.base_url)
    prefix_hit = None if middle is None or after is None else after > middle

    report = {
        "mode": "smoke",
        "model": args.model,
        "determinism": {
            "repeats": args.repeats,
            "text_stable": text_stable,
            "first_logprobs_available": bool(samples[0][1]),
            "first_logprobs_stable": score_stable,
        },
        "long_prompt": {
            "requested_tokens": args.prefill_tokens,
            "actual_tokens": actual_tokens,
            "cold_ttft_s": round(cold["ttft_s"], 3) if cold["ttft_s"] else None,
            "warm_ttft_s": round(warm["ttft_s"], 3) if warm["ttft_s"] else None,
            "prefix_hits_before": before,
            "prefix_hits_after_cold": middle,
            "prefix_hits_after_warm": after,
            "repeated_prefix_hit": prefix_hit,
        },
    }
    if not text_stable or (samples[0][1] and not score_stable):
        raise RuntimeError("Greedy output or first-token logprobs changed across repeats")
    if args.require_prefix_hit and prefix_hit is not True:
        raise RuntimeError("A repeated prompt did not increase the prefix-cache hit metric")
    return report


def run_mixed(args: argparse.Namespace) -> dict:
    """Compare decode stream pacing with and without a competing long prefill."""

    check_backend(args.base_url, args.model)
    prompt, actual_tokens = prompt_for_tokens(
        args.base_url, args.model, args.corpus, args.prefill_tokens
    )
    baseline = stream_chat(
        args.base_url, args.model, DECODE_PROMPT, args.decode_tokens
    )
    prefill_result: dict = {}
    prefill_error: list[BaseException] = []
    prefill_thread: threading.Thread | None = None

    def launch_prefill() -> None:
        nonlocal prefill_thread

        def worker() -> None:
            try:
                prefill_result.update(long_prefill(args.base_url, args.model, prompt))
            except BaseException as exc:  # propagate after joining the worker
                prefill_error.append(exc)

        prefill_thread = threading.Thread(target=worker, name="long-prefill")
        prefill_thread.start()

    mixed = stream_chat(
        args.base_url,
        args.model,
        DECODE_PROMPT,
        args.decode_tokens,
        on_first=launch_prefill,
    )
    if prefill_thread is None:
        raise RuntimeError("Decode produced no content; competing prefill was not started")
    prefill_thread.join(timeout=1900)
    if prefill_thread.is_alive():
        raise RuntimeError("Competing prefill did not finish within the timeout")
    if prefill_error:
        raise RuntimeError("Competing prefill failed") from prefill_error[0]

    baseline_gaps = gap_summary(baseline["gaps"])
    mixed_gaps = gap_summary(mixed["gaps"])
    report = {
        "mode": "mixed",
        "model": args.model,
        "prefill_tokens": actual_tokens,
        "competing_prefill_ttft_s": round(prefill_result["ttft_s"], 3)
        if prefill_result.get("ttft_s")
        else None,
        "baseline_decode": {
            "completion_tokens": baseline["completion_tokens"],
            "elapsed_s": round(baseline["elapsed_s"], 3),
            "event_gaps": baseline_gaps,
        },
        "mixed_decode": {
            "completion_tokens": mixed["completion_tokens"],
            "elapsed_s": round(mixed["elapsed_s"], 3),
            "event_gaps": mixed_gaps,
        },
    }
    if baseline_gaps["p95_s"] and mixed_gaps["p95_s"]:
        report["p95_gap_ratio"] = round(
            mixed_gaps["p95_s"] / baseline_gaps["p95_s"], 2
        )
    return report


def parser() -> argparse.ArgumentParser:
    """Construct the command-line interface."""

    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("mode", choices=("smoke", "mixed", "all"))
    result.add_argument("--base-url", default="http://127.0.0.1:8888")
    result.add_argument("--model", default=MODEL)
    result.add_argument("--corpus", type=pathlib.Path, default=ROOT / "README.md")
    result.add_argument("--prefill-tokens", type=int, default=8192)
    result.add_argument("--decode-tokens", type=int, default=384)
    result.add_argument("--repeats", type=int, default=4)
    result.add_argument("--require-prefix-hit", action="store_true")
    return result


def main() -> None:
    """Validate arguments, run the selected checks, and print JSON reports."""

    args = parser().parse_args()
    try:
        args.base_url = validate_base_url(args.base_url)
    except ValueError as exc:
        parser().error(str(exc))
    if not 512 <= args.prefill_tokens <= 250_000:
        parser().error("--prefill-tokens must be between 512 and 250000")
    if not 32 <= args.decode_tokens <= 4096:
        parser().error("--decode-tokens must be between 32 and 4096")
    if not 2 <= args.repeats <= 20:
        parser().error("--repeats must be between 2 and 20")
    if not args.corpus.is_file():
        parser().error(f"Corpus file does not exist: {args.corpus}")

    modes = ("smoke", "mixed") if args.mode == "all" else (args.mode,)
    for mode in modes:
        report = run_smoke(args) if mode == "smoke" else run_mixed(args)
        print(json.dumps(report, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
