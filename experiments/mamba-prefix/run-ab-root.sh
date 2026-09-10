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
REPORT="/var/tmp/qwen38-mamba-prefix-ab-$(date -u +%Y%m%dT%H%M%SZ).json"
LOG="${REPORT%.json}.container.log"
RESTORE_NEEDED=0

CURRENT_REAL="$(readlink -f "$CURRENT" 2>/dev/null || true)"
[[ "$CURRENT_REAL" == /opt/qwen3.8-flash-next/releases/* && -d "$CURRENT_REAL" && ! -L "$CURRENT_REAL" ]] || {
    echo "Managed production release is missing or unsafe: $CURRENT" >&2
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
    local original_status=$?
    local restore_status=0
    trap - EXIT INT TERM
    set +e
    if [[ "$RESTORE_NEEDED" -eq 1 ]]; then
        if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
            docker logs "$CONTAINER" >"$LOG" 2>&1
            chmod 0600 "$LOG"
        fi
        /bin/bash "$CURRENT_REAL/stop.sh" >/dev/null 2>&1 || \
            docker rm -f "$CONTAINER" >/dev/null 2>&1
        systemctl reset-failed "$SERVICE" >/dev/null 2>&1
        systemctl start "$SERVICE" || restore_status=1
        if [[ "$restore_status" -eq 0 ]]; then
            for _ in $(seq 1 180); do
                curl -fsS http://127.0.0.1:8888/health >/dev/null 2>&1 && break
                systemctl is-active --quiet "$SERVICE" || { restore_status=1; break; }
                sleep 10
            done
            curl -fsS http://127.0.0.1:8888/health >/dev/null 2>&1 || restore_status=1
        fi
        if [[ "$restore_status" -eq 0 ]]; then
            echo "Production service restored and healthy."
        else
            echo "WARNING: production service did not recover; inspect its journal now." >&2
        fi
    fi
    echo "A/B report: $REPORT"
    echo "Experimental container log: $LOG"
    [[ "$restore_status" -eq 0 ]] || exit 1
    exit "$original_status"
}
trap restore_production EXIT INT TERM

echo "This planned outage stops the healthy production service, runs the Mamba-only"
echo "image and correctness suite, and restores the pinned production service."
echo "OpenWebUI requests will fail during both model loads."
[[ -r /dev/tty && -w /dev/tty ]] || {
    echo "Interactive terminal required." >&2
    exit 1
}
read -r -p "Type RUN-MAMBA-AB to continue: " answer </dev/tty
[[ "$answer" == "RUN-MAMBA-AB" ]] || { echo "Cancelled."; exit 0; }

echo "experimental_image=$(docker image inspect --format '{{.Id}}' "$IMAGE")"
systemctl stop "$SERVICE"
RESTORE_NEEDED=1

cd "$CURRENT_REAL"
HOME=/var/lib/qwen3.8-flash-next \
HF_HOME=/var/lib/qwen3.8-flash-next/huggingface \
IMAGE="$IMAGE" \
ALLOW_IMAGE_PULL=0 \
/bin/bash ./start.sh

for _ in $(seq 1 180); do
    curl -fsS http://127.0.0.1:8888/health >/dev/null 2>&1 && break
    docker container inspect "$CONTAINER" >/dev/null 2>&1 || {
        echo "Experimental container disappeared before readiness." >&2
        exit 1
    }
    status="$(docker inspect --format '{{.State.Status}}' "$CONTAINER")"
    [[ "$status" == "running" ]] || {
        echo "Experimental container stopped before readiness: $status" >&2
        exit 1
    }
    sleep 10
done
curl -fsS http://127.0.0.1:8888/health >/dev/null || {
    echo "Experimental endpoint did not become ready within 30 minutes." >&2
    exit 1
}

cd "$REPO_ROOT"
python3 bench/cache_correctness.py all \
  --cache-sizes 8192,32768,131072 \
  --qsa-sizes 0,8192,32768 \
  --repeats 5 \
  --max-tokens 96 \
  --min-tokens 8 \
  --require-prefix-hit \
  --output "$REPORT"
chmod 0600 "$REPORT"
