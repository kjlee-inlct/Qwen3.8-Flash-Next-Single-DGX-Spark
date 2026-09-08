#!/usr/bin/env bash
set -euo pipefail

cd /opt/qwen3.8-flash-next/current
source .env
/bin/bash deployment/check-host-profile.sh

EXPECTED_IMAGE="vllm/vllm-openai:qwen38-flash-next@sha256:fc120ece0a388cc0aa1caad4a9f1cd92113484ab7ec2fd0efadd62585be05bf8"
EXPECTED_MODEL="Mia-AiLab/Qwen3.8-Flash-Next-NVFP4"
EXPECTED_REVISION="925d7be6c14c6c9442ef83e8f05b5a3c39304f69"

[[ "$IMAGE" == "$EXPECTED_IMAGE" ]] || { echo "Refusing unpinned image: $IMAGE" >&2; exit 1; }
[[ "$TP1_MODEL_ID" == "$EXPECTED_MODEL" ]] || { echo "Refusing unexpected model: $TP1_MODEL_ID" >&2; exit 1; }
[[ "$MODEL_REVISION" == "$EXPECTED_REVISION" ]] || { echo "Refusing unexpected model revision: $MODEL_REVISION" >&2; exit 1; }
[[ "$BIND_HOST" == "127.0.0.1" ]] || { echo "Refusing non-loopback bind: $BIND_HOST" >&2; exit 1; }
[[ "$ABLIT" == "0" ]] || { echo "Refusing unqualified abliterated checkpoint." >&2; exit 1; }
[[ "$ALLOW_IMAGE_PULL" == "0" ]] || { echo "Runtime network pulls must remain disabled." >&2; exit 1; }

# A bounded systemd retry must wait for the previous GPU allocation to unwind.
# Do not weaken the launch or runtime memory thresholds in order to restart.
[[ "${CONTAINER_MEM_GIB:-}" =~ ^[1-9][0-9]*$ ]] || {
    echo "Production requires an explicit positive CONTAINER_MEM_GIB." >&2
    exit 1
}
required_kib=$(( (CONTAINER_MEM_GIB + 4) * 1048576 ))
for _ in $(seq 1 120); do
    available_kib=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
    [[ "$available_kib" -ge "$required_kib" ]] && break
    sleep 2
done
[[ "$available_kib" -ge "$required_kib" ]] || {
    echo "Host memory did not recover to $((CONTAINER_MEM_GIB + 4)) GiB; refusing restart." >&2
    exit 1
}

/usr/bin/timeout --signal=TERM 1800 /bin/bash ./start.sh
container="${TP1_CONTAINER_NAME:-vllm-fn-tp1}"
/usr/bin/docker logs --follow --since 0s "$container" &
log_pid=$!
trap 'kill "$log_pid" 2>/dev/null || true' EXIT
container_status="$(/usr/bin/docker wait "$container")"
echo "Inference container stopped (exit $container_status); marking the service failed for bounded recovery." >&2
# docker logs/wait can exit successfully when the watchdog stops the container.
# A stopped container is not a healthy serving process, even for exit code 0.
exit 1
