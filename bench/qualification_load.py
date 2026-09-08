#!/usr/bin/env python3
"""Run the published short-prompt load test locally on a Linux inference host.

No generated answer text, credentials, hostname or endpoint is saved. Run only
against a loopback backend; use separate tooling for authenticated network tests.
Request parameters match the measured 2026-09-07 workload. SSE completion checks
are stricter than in the original measurement harness.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import threading
import time
import urllib.request
import urllib.parse


PROMPT = """Write a self-contained technical explanation for an experienced developer about
designing a bounded, thread-safe work queue with backpressure, cancellation, graceful
shutdown, and observability. Include a compact Python implementation and discuss failure
modes. Be precise and continue until the response limit."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Benchmark endpoint redirected; refusing to leave the configured backend")


def mem_available_gib() -> float:
    with open("/proc/meminfo", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1048576
    raise RuntimeError("MemAvailable missing from /proc/meminfo")


def run_request(endpoint: str, model: str, max_tokens: int, gate: threading.Barrier) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    gate.wait()
    started = time.monotonic()
    first_content = None
    completion_tokens = 0
    chars = 0
    finish_reason = None
    done = False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=300) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").strip()
            if line == "data: [DONE]":
                done = True
                continue
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event.get("error"):
                raise RuntimeError("Server returned an error in the stream")
            usage = event.get("usage") or {}
            completion_tokens = usage.get("completion_tokens", completion_tokens)
            for choice in event.get("choices") or []:
                content = (choice.get("delta") or {}).get("content") or ""
                if content and first_content is None:
                    first_content = time.monotonic()
                chars += len(content)
                finish_reason = choice.get("finish_reason") or finish_reason
    ended = time.monotonic()
    return {
        "ok": chars > 0 and done and completion_tokens > 0 and finish_reason in ("stop", "length"),
        "done": done,
        "elapsed_s": ended - started,
        "ttft_s": (first_content - started) if first_content else None,
        "completion_tokens": completion_tokens,
        "chars": chars,
        "finish_reason": finish_reason,
    }


def sample_memory(stop: threading.Event, values: list[float]) -> None:
    while not stop.wait(0.25):
        values.append(mem_available_gib())


def benchmark_level(endpoint: str, model: str, concurrency: int, max_tokens: int) -> dict:
    gate = threading.Barrier(concurrency + 1)
    stop = threading.Event()
    memory_samples = [mem_available_gib()]
    sampler = threading.Thread(target=sample_memory, args=(stop, memory_samples), daemon=True)
    sampler.start()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [
                pool.submit(run_request, endpoint, model, max_tokens, gate)
                for _ in range(concurrency)
            ]
            gate.wait()
            wall_started = time.monotonic()
            results = [future.result() for future in futures]
            wall_elapsed = time.monotonic() - wall_started
    finally:
        stop.set()
        sampler.join(timeout=1)
    total_tokens = sum(item["completion_tokens"] for item in results)
    ttfts = [item["ttft_s"] for item in results if item["ttft_s"] is not None]
    return {
        "concurrency": concurrency,
        "ok": sum(bool(item["ok"]) for item in results),
        "requests": concurrency,
        "wall_s": round(wall_elapsed, 3),
        "total_completion_tokens": total_tokens,
        "aggregate_tokens_s": round(total_tokens / wall_elapsed, 2) if wall_elapsed else None,
        "per_stream_tokens_s": round(total_tokens / wall_elapsed / concurrency, 2)
        if wall_elapsed
        else None,
        "median_ttft_s": round(statistics.median(ttfts), 3) if ttfts else None,
        "min_mem_available_gib": round(min(memory_samples), 2),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:8888/v1/chat/completions")
    parser.add_argument("--model", default="qwen3.8-flash-next")
    parser.add_argument("--max-tokens", type=int, default=384)
    parser.add_argument("--levels", default="1,2,4,8")
    args = parser.parse_args()
    endpoint = urllib.parse.urlsplit(args.endpoint)
    if (endpoint.scheme not in ("http", "https") or
            endpoint.hostname not in ("127.0.0.1", "localhost", "::1") or
            endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
        parser.error("Run on the inference host against a loopback endpoint without credentials or query parameters")
    try:
        levels = [int(value) for value in args.levels.split(",")]
    except ValueError:
        parser.error("--levels must contain comma-separated integers")
    if not levels or any(value < 1 or value > 32 for value in levels):
        parser.error("Each concurrency level must be between 1 and 32")
    if not 1 <= args.max_tokens <= 8192:
        parser.error("--max-tokens must be between 1 and 8192")
    report = {
        "endpoint_scope": "loopback on inference host",
        "model": args.model,
        "max_tokens": args.max_tokens,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "levels": [],
    }
    for level in levels:
        result = benchmark_level(args.endpoint, args.model, level, args.max_tokens)
        report["levels"].append(result)
        print(json.dumps(result), flush=True)
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print("REPORT " + json.dumps(report), flush=True)
    if any(item["ok"] != item["requests"] for item in report["levels"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
