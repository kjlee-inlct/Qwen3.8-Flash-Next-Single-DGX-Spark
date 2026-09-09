#!/usr/bin/env python3
"""Observe real-text prefill, page faults, memory and storage without host changes."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
from datetime import datetime, timezone

import runtime_validation as runtime


VMSTAT_KEYS = {"pgfault", "pgmajfault", "pswpin", "pswpout"}
MEMINFO_KEYS = {
    "MemTotal", "MemFree", "MemAvailable", "Cached", "SwapCached",
    "Active(file)", "Inactive(file)", "SwapTotal", "SwapFree",
}
BLOCK_RE = re.compile(r"^(nvme\d+n\d+|sd[a-z]+|vd[a-z]+|mmcblk\d+)$")


def read_key_values(path: pathlib.Path, wanted: set[str]) -> dict[str, int]:
    """Read numeric kernel counters, returning only explicitly requested keys."""
    result: dict[str, int] = {}
    try:
        for line in path.read_text().splitlines():
            fields = line.replace(":", "").split()
            if len(fields) >= 2 and fields[0] in wanted:
                result[fields[0]] = int(fields[1])
    except (OSError, ValueError):
        pass
    return result


def block_stats(root: pathlib.Path = pathlib.Path("/sys/class/block")) -> dict[str, dict[str, int]]:
    """Read cumulative physical-device counters; sectors are normally 512 bytes."""
    result: dict[str, dict[str, int]] = {}
    try:
        devices = list(root.iterdir())
    except OSError:
        return result
    for device in devices:
        if not BLOCK_RE.fullmatch(device.name):
            continue
        try:
            fields = [int(value) for value in (device / "stat").read_text().split()]
        except (OSError, ValueError):
            continue
        if len(fields) >= 7:
            result[device.name] = {
                "reads_completed": fields[0],
                "sectors_read": fields[2],
                "read_bytes": fields[2] * 512,
                "read_time_ms": fields[3],
            }
    return result


def process_table(proc: pathlib.Path = pathlib.Path("/proc")) -> dict[int, tuple[int, str]]:
    """Return PID -> (PPID, command line) for readable processes."""
    result: dict[int, tuple[int, str]] = {}
    for item in proc.iterdir():
        if not item.name.isdigit():
            continue
        try:
            stat = (item / "stat").read_text().split()
            command = (item / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            result[int(item.name)] = (int(stat[3]), command)
        except (OSError, ValueError, IndexError):
            continue
    return result


def vllm_pids(port: int, proc: pathlib.Path = pathlib.Path("/proc")) -> list[int]:
    """Find the vLLM serve process for the port and all readable descendants."""
    table = process_table(proc)
    roots = {
        pid for pid, (_, command) in table.items()
        if "vllm serve" in command and (f"--port {port}" in command or f"--port={port}" in command)
    }
    selected = set(roots)
    changed = True
    while changed:
        changed = False
        for pid, (ppid, _) in table.items():
            if ppid in selected and pid not in selected:
                selected.add(pid)
                changed = True
    return sorted(selected)


def process_counters(pids: list[int], proc: pathlib.Path = pathlib.Path("/proc")) -> dict[str, int] | None:
    """Sum readable I/O and fault counters across the serving process tree."""
    total = {"read_bytes": 0, "rchar": 0, "minor_faults": 0, "major_faults": 0}
    observed = False
    for pid in pids:
        base = proc / str(pid)
        try:
            stat = (base / "stat").read_text().split()
            total["minor_faults"] += int(stat[9])
            total["major_faults"] += int(stat[11])
            observed = True
        except (OSError, ValueError, IndexError):
            pass
        try:
            values = read_key_values(base / "io", {"read_bytes", "rchar"})
            for key in ("read_bytes", "rchar"):
                total[key] += values.get(key, 0)
            observed = observed or bool(values)
        except OSError:
            pass
    return total if observed else None


def ple_tables(root: pathlib.Path) -> dict:
    """Record packed-table paths and sizes without reading their large contents."""
    paths: list[dict] = []
    if root.is_dir():
        try:
            for path in root.glob("*/*.packed_u8"):
                try:
                    paths.append({"path": str(path), "bytes": path.stat().st_size})
                except OSError:
                    continue
        except OSError:
            pass
    return {"root": str(root), "files": paths, "total_bytes": sum(x["bytes"] for x in paths)}


def snapshot(port: int, ple_root: pathlib.Path) -> dict:
    """Capture lightweight cumulative counters at one instant."""
    pids = vllm_pids(port)
    return {
        "time": datetime.now(timezone.utc).isoformat(),
        "monotonic_s": time.monotonic(),
        "meminfo_kib": read_key_values(pathlib.Path("/proc/meminfo"), MEMINFO_KEYS),
        "vmstat": read_key_values(pathlib.Path("/proc/vmstat"), VMSTAT_KEYS),
        "block": block_stats(),
        "vllm_pids": pids,
        "vllm_process": process_counters(pids),
        "prefix_hits": None,
        "ple": ple_tables(ple_root),
    }


def numeric_delta(before, after):
    """Recursively subtract matching numeric counters; omit non-counters."""
    if isinstance(before, dict) and isinstance(after, dict):
        return {key: numeric_delta(before[key], after[key]) for key in before.keys() & after.keys()
                if numeric_delta(before[key], after[key]) is not None}
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        return after - before
    return None


def unique_prompt(base: str, model: str, corpus: pathlib.Path, target: int) -> tuple[str, int]:
    """Create a real-text prompt whose early marker prevents cross-size prefix reuse."""
    marker = f"Storage observation target {target}; use only this numbered corpus.\n"
    source = runtime.corpus_text(corpus, max(50_000, target * 8))
    low, high = 1, len(source)
    best, best_count = marker + source, 0
    for _ in range(20):
        middle = (low + high) // 2
        candidate = marker + source[:middle] + "\nReply with only: ok"
        count = runtime.tokenize_count(base, model, candidate)
        if not best_count or abs(count - target) < abs(best_count - target):
            best, best_count = candidate, count
        if count < target:
            low = middle + 1
        else:
            high = middle - 1
    return best, best_count


def measured_request(base: str, model: str, prompt: str, port: int, ple_root: pathlib.Path) -> dict:
    """Run one-token prefill and report system/process/storage counter deltas."""
    before = snapshot(port, ple_root)
    before["prefix_hits"] = runtime.prefix_hits(base)
    timing = runtime.long_prefill(base, model, prompt)
    after = snapshot(port, ple_root)
    after["prefix_hits"] = runtime.prefix_hits(base)
    return {
        "ttft_s": round(timing["ttft_s"], 3) if timing.get("ttft_s") is not None else None,
        "elapsed_s": round(timing["elapsed_s"], 3),
        "before": before,
        "after": after,
        "delta": numeric_delta(before, after),
    }


def parse_sizes(value: str) -> list[int]:
    """Parse a unique comma-separated token-size list."""
    try:
        sizes = [int(item) for item in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("sizes must be comma-separated integers") from exc
    if not sizes or len(set(sizes)) != len(sizes) or any(x < 512 or x > 250_000 for x in sizes):
        raise argparse.ArgumentTypeError("sizes must be unique values between 512 and 250000")
    return sizes


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--base-url", default="http://127.0.0.1:8888")
    result.add_argument("--model", default=runtime.MODEL)
    result.add_argument("--corpus", type=pathlib.Path, default=runtime.ROOT / "README.md")
    result.add_argument("--sizes", type=parse_sizes, default=parse_sizes("8192,32768,131072"))
    result.add_argument("--ple-root", type=pathlib.Path,
                        default=pathlib.Path("/var/lib/qwen3.8-flash-next/.cache/vllm/ple_cache"))
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
    runtime.check_backend(args.base_url, args.model)
    port = int(args.base_url.rsplit(":", 1)[1])
    report = {
        "schema": 1,
        "mode": "storage-prefill-observation",
        "started": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "base_url": args.base_url,
        "corpus": str(args.corpus.resolve()),
        "note": "first-observed does not mean OS drop_caches; no caches were cleared",
        "runs": [],
    }
    for target in args.sizes:
        prompt, actual = unique_prompt(args.base_url, args.model, args.corpus, target)
        first = measured_request(args.base_url, args.model, prompt, port, args.ple_root)
        repeated = measured_request(args.base_url, args.model, prompt, port, args.ple_root)
        report["runs"].append({
            "requested_tokens": target,
            "actual_tokens": actual,
            "first_observed": first,
            "immediate_repeat": repeated,
            "ttft_speedup": round(first["ttft_s"] / repeated["ttft_s"], 3)
                if first["ttft_s"] and repeated["ttft_s"] else None,
        })
    report["completed"] = datetime.now(timezone.utc).isoformat()
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
