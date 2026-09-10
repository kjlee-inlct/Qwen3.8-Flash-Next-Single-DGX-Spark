#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="qwen38-flash-next:qsa-exact-topk-ab"
BASE="vllm/vllm-openai:qwen38-flash-next@sha256:fc120ece0a388cc0aa1caad4a9f1cd92113484ab7ec2fd0efadd62585be05bf8"

command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
docker image inspect "$BASE" >/dev/null 2>&1 || {
    echo "Pinned base image is absent; bootstrap it through the reviewed production workflow first." >&2
    exit 1
}

docker build --pull=false --tag "$IMAGE" "$SCRIPT_DIR"
docker image inspect --format 'image={{.Id}} created={{.Created}}' "$IMAGE"
