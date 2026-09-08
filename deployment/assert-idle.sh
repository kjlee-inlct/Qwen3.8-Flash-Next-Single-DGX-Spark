#!/usr/bin/env bash
set -euo pipefail

# A responding vLLM must report both running and queued request counts. A
# backend with no listener can instead be recovered without a metrics endpoint.
if metrics_response="$(curl -sS --connect-timeout 3 --max-time 5 \
        --write-out '\n%{http_code}' http://127.0.0.1:8888/metrics)"; then
    metrics_status="${metrics_response##*$'\n'}"
    [[ "$metrics_status" == 200 ]] || {
        echo "Refusing interruption: metrics endpoint responded HTTP $metrics_status." >&2
        exit 1
    }
    active_requests="$(python3 -c '
import math, re, sys
pattern = re.compile(r"^vllm:num_requests_(running|waiting)(?:\{.*\})?\s+(\S+)(?:\s+\S+)?$")
seen = set()
total = 0
for line in sys.stdin:
    match = pattern.fullmatch(line.strip())
    if not match:
        if re.match(r"^vllm:num_requests_(running|waiting)(?:\{|\s|$)", line):
            raise SystemExit("Malformed inference request metric; refusing interruption.")
        continue
    kind, raw_value = match.groups()
    value = float(raw_value)
    if not math.isfinite(value) or value < 0 or not value.is_integer():
        raise SystemExit("Invalid inference request count in metrics; refusing interruption.")
    seen.add(kind)
    total += int(value)
if seen != {"running", "waiting"}:
    raise SystemExit("Missing running/waiting metrics; refusing interruption.")
print(total)
' <<< "${metrics_response%$'\n'*}")"
    [[ "$active_requests" -eq 0 ]] || {
        echo "Refusing interruption while $active_requests inference request(s) are running or queued." >&2
        exit 1
    }
else
    metrics_error=$?
    # A timeout, HTTP error, or malformed responding endpoint is not evidence
    # that existing requests have drained. curl exit 7 plus no listener is.
    listeners="$(ss -H -ltn 'sport = :8888')"
    if [[ "$metrics_error" -ne 7 || -n "$listeners" ]]; then
        echo "Cannot establish that the previous backend is idle (curl exit $metrics_error); refusing interruption." >&2
        exit 1
    fi
    echo "Previous backend is already unavailable; proceeding with recovery."
fi
