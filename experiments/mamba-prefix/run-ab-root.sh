#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
    echo "Run with sudo: sudo bash experiments/mamba-prefix/run-ab-root.sh" >&2
    exit 1
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CURRENT="/opt/qwen3.8-flash-next/current"
SERVICE="qwen3.8-flash-next.service"
CONTAINER="vllm-fn-tp1"
IMAGE="qwen38-flash-next:mamba-prefix-ab"
REPORT="${QWEN_AB_REPORT:-/var/tmp/qwen38-mamba-prefix-ab-$(date -u +%Y%m%dT%H%M%SZ).json}"
LOG="${REPORT%.json}.container.log"
RESTORE_NEEDED=0

[[ -d "$CURRENT" && ! -L "$CURRENT" || -L "$CURRENT" ]] || {
    echo "Managed production release is missing: $CURRENT" >&2
    exit 1
}
[[ -f "$REPO_ROOT/bench/cache_correctness.py" ]] || {
    echo "Validator missing from checkout: $REPO_ROOT/bench/cache_correctness.py" >&2
    exit 1
}
docker image inspect "$IMAGE" >/dev/null 2>&1 || {
    echo "Experimental image missing. Run experiments/mamba-prefix/build.sh first." >&2
    exit 1
}
systemctl is-active --quiet "$SERVICE" || {
    echo "Production service must be active before the experiment." >&2
    exit 1
}
curl -fsS http://127.0.0.1:8888/health >/dev/null || {
    echo "Production endpoint is not ready; refusing an A/B test without a healthy baseline." >&2
    exit 1
}

restore_production() {
    local status=$?
    trap - EXIT INT TERM
    set +e
    if [[ "$RESTORE_NEEDED" -eq 1 ]]; then
        if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
            docker logs "$CONTAINER" >"$LOG" 2>&1
        fi
        /bin/bash "$CURRENT/stop.sh" >/dev/null 2>&1 || docker rm -f "$CONTAINER" >/dev/null 2>&1
        systemctl reset-failed "$SERVICE" >/dev/null 2>&1
        systemctl start "$SERVICE"
        echo "Production restart requested. It may take several minutes to become ready."
    fi
    echo "A/B report: $REPORT"
    echo "Experimental container log: $LOG"
    exit "$status"
}
trap restore_production EXIT INT TERM

echo "This planned outage stops the healthy production service, runs the Mamba-only"
echo "image and correctness suite, then requests restoration of the production service."
echo "OpenWebUI requests will fail during the experiment and production reload."
[[ -r /dev/tty && -w /dev/tty ]] || {
    echo "Interactive terminal required." >&2
    exit 1
}
read -r -p "Type RUN-MAMBA-AB to continue: " answer </dev/tty
[[ "$answer" == "RUN-MAMBA-AB" ]] || { echo "Cancelled."; exit 0; }

echo "experimental_image=$(docker image inspect --format '{{.Id}}' "$IMAGE")"
systemctl stop "$SERVICE"
RESTORE_NEEDED=1

cd "$CURRENT"
HOME=/var/lib/qwen3.8-flash-next HF_HOME=/var/lib/qwen3.8-flash-next/huggingface IMAGE="$IMAGE" ALLOW_IMAGE_PULL=0 /bin/bash ./start.sh

until curl -fsS http://127.0.0.1:8888/health >/dev/null 2>&1; do
    if ! docker container inspect "$CONTAINER" >/dev/null 2>&1; then
        echo "Experimental container disappeared before readiness." >&2
        exit 1
    fi
    status="$(docker inspect --format '{{.State.Status}}' "$CONTAINER")"
    [[ "$status" == "running" ]] || {
        echo "Experimental container stopped before readiness: $status" >&2
        exit 1
    }
    sleep 10
done

cd "$REPO_ROOT"
python3 bench/cache_correctness.py all   --cache-sizes 8192,32768,131072   --qsa-sizes 0,8192,32768   --repeats 5   --max-tokens 96   --require-prefix-hit   --output "$REPORT"
