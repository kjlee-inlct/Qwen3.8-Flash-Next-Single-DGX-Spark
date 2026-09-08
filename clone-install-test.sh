#!/usr/bin/env bash
# Clone the validation branch, install the pinned DGX Spark service, run behavioral
# tests, and keep one reviewable log. No generated model answer or HF token is logged.
#
# Safe default:
#   ./clone-install-test.sh
#       Clone (or validate --reuse), run static tests and host preflight only.
#
# Explicit machine-changing workflow:
#   ./clone-install-test.sh --full
#       Also pull ~image data, download ~model data, apply system-wide VM settings,
#       install/start the systemd service and run smoke + mixed-load validation.
#
# Optional fitted draft vocabulary:
#   --draft-vocab /absolute/file
#   --corpus-dir  /absolute/directory   # needs local_code.txt + model_outputs.jsonl
set -Eeuo pipefail

REPOSITORY_URL="https://github.com/kjlee-inlct/Qwen3.8-Flash-Next-Single-DGX-Spark.git"
BRANCH="validate-runtime-behavior"
EXPECTED_REPOSITORY="kjlee-inlct/Qwen3.8-Flash-Next-Single-DGX-Spark"
IMAGE="vllm/vllm-openai:qwen38-flash-next@sha256:fc120ece0a388cc0aa1caad4a9f1cd92113484ab7ec2fd0efadd62585be05bf8"
MODEL_ID="Mia-AiLab/Qwen3.8-Flash-Next-NVFP4"
MODEL_REVISION="925d7be6c14c6c9442ef83e8f05b5a3c39304f69"
STATE_DIR="/var/lib/qwen3.8-flash-next"
SERVICE="qwen3.8-flash-next.service"

FULL=false
REUSE=false
TARGET="$PWD/Qwen3.8-Flash-Next-Single-DGX-Spark-validation"
DRAFT_VOCAB=""
CORPUS_DIR=""
PREFILL_TOKENS=32768
LOG_FILE=""
CHECKOUT=""
STARTED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

usage() {
    cat <<'EOF'
Usage: clone-install-test.sh [options]

Without --full, only clone/static tests/host preflight are performed.

Options:
  --full                    Download assets, apply sysctl, install and test service
  --reuse                   Reuse an existing clean checkout at the requested branch
  --target ABSOLUTE_PATH    Checkout directory (default: directory under $PWD)
  --draft-vocab ABS_PATH    Existing draft token-ID file used by the installer
  --corpus-dir ABS_PATH     Corpus directory used to build a draft vocabulary
  --prefill-tokens N        Mixed-test long prompt size (default: 32768)
  --log ABSOLUTE_PATH       Log destination (default: current directory + timestamp)
  -h, --help                Show this help

--draft-vocab and --corpus-dir are mutually exclusive. With neither, --full uses
the complete MTP vocabulary (QWEN_DRAFT_VOCAB=off) for functional bootstrap.
EOF
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

require_absolute() {
    local label="$1"
    local value="$2"
    [[ "$value" == /* ]] || die "$label must be an absolute path: $value"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --full) FULL=true; shift ;;
        --reuse) REUSE=true; shift ;;
        --target) [[ $# -ge 2 ]] || die "--target needs a path"; TARGET="$2"; shift 2 ;;
        --draft-vocab) [[ $# -ge 2 ]] || die "--draft-vocab needs a path"; DRAFT_VOCAB="$2"; shift 2 ;;
        --corpus-dir) [[ $# -ge 2 ]] || die "--corpus-dir needs a path"; CORPUS_DIR="$2"; shift 2 ;;
        --prefill-tokens) [[ $# -ge 2 ]] || die "--prefill-tokens needs a value"; PREFILL_TOKENS="$2"; shift 2 ;;
        --log) [[ $# -ge 2 ]] || die "--log needs a path"; LOG_FILE="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done

require_absolute "--target" "$TARGET"
[[ -z "$DRAFT_VOCAB" ]] || require_absolute "--draft-vocab" "$DRAFT_VOCAB"
[[ -z "$CORPUS_DIR" ]] || require_absolute "--corpus-dir" "$CORPUS_DIR"
[[ -z "$LOG_FILE" ]] || require_absolute "--log" "$LOG_FILE"
[[ -z "$DRAFT_VOCAB" || -z "$CORPUS_DIR" ]] || die "Use only one of --draft-vocab and --corpus-dir"
[[ "$PREFILL_TOKENS" =~ ^[0-9]+$ ]] || die "--prefill-tokens must be an integer"
(( PREFILL_TOKENS >= 512 && PREFILL_TOKENS <= 250000 )) || die "--prefill-tokens must be 512..250000"
[[ -z "$DRAFT_VOCAB" || -f "$DRAFT_VOCAB" ]] || die "Draft vocabulary not found: $DRAFT_VOCAB"
if [[ -n "$CORPUS_DIR" ]]; then
    [[ -d "$CORPUS_DIR" ]] || die "Corpus directory not found: $CORPUS_DIR"
    [[ -s "$CORPUS_DIR/local_code.txt" ]] || die "Missing $CORPUS_DIR/local_code.txt"
    [[ -s "$CORPUS_DIR/model_outputs.jsonl" ]] || die "Missing $CORPUS_DIR/model_outputs.jsonl"
fi

if [[ -z "$LOG_FILE" ]]; then
    LOG_FILE="$PWD/qwen38-install-test-$(date -u '+%Y%m%dT%H%M%SZ').log"
fi
mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"
chmod 0600 "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

collect_diagnostics() {
    local status="$1"
    set +e
    echo
    echo "===== DIAGNOSTICS (exit=$status) ====="
    date -u '+time=%Y-%m-%dT%H:%M:%SZ'
    free -h
    swapon --show
    df -h "$TARGET" "$STATE_DIR" 2>/dev/null
    if command -v nvidia-smi >/dev/null 2>&1; then
        nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv,noheader
    fi
    if command -v systemctl >/dev/null 2>&1; then
        systemctl status "$SERVICE" --no-pager -l 2>&1 | tail -n 80
        journalctl -u "$SERVICE" -n 160 --no-pager -o short-iso 2>&1
    fi
    if command -v docker >/dev/null 2>&1; then
        docker ps -a --filter name=vllm-fn-tp1 --no-trunc
        docker logs --tail 160 vllm-fn-tp1 2>&1
    fi
    echo "===== END DIAGNOSTICS ====="
    echo "LOG_FILE=$LOG_FILE"
    return 0
}

on_exit() {
    local status=$?
    trap - EXIT
    collect_diagnostics "$status"
    exit "$status"
}
trap on_exit EXIT

echo "===== QWEN3.8 DGX SPARK CLONE / INSTALL / TEST ====="
echo "started=$STARTED_AT"
echo "mode=$([[ "$FULL" == true ]] && echo full || echo preflight)"
echo "repository=$EXPECTED_REPOSITORY"
echo "branch=$BRANCH"
echo "target=$TARGET"
echo "log=$LOG_FILE"
echo "HF_TOKEN is never printed or passed to the serving container."

for command in git bash python3 curl sha256sum awk sed tee; do
    command -v "$command" >/dev/null 2>&1 || die "Required command missing: $command"
done
if [[ "$FULL" == true ]]; then
    for command in sudo docker systemctl nvidia-smi ss; do
        command -v "$command" >/dev/null 2>&1 || die "Required full-mode command missing: $command"
    done
    sudo -v
fi

echo
echo "===== CLONE ====="
if [[ -e "$TARGET" ]]; then
    [[ "$REUSE" == true ]] || die "Target exists; inspect it and rerun with --reuse: $TARGET"
    [[ -d "$TARGET/.git" ]] || die "Reuse target is not a Git checkout: $TARGET"
    CHECKOUT="$TARGET"
    remote_url="$(git -C "$CHECKOUT" remote get-url origin)"
    case "$remote_url" in
        "$REPOSITORY_URL"|git@github.com:kjlee-inlct/Qwen3.8-Flash-Next-Single-DGX-Spark.git) ;;
        *) die "Reuse target origin is unexpected: $remote_url" ;;
    esac
    [[ -z "$(git -C "$CHECKOUT" status --porcelain)" ]] || die "Reuse target has local changes"
    git -C "$CHECKOUT" fetch origin "$BRANCH" --prune
    git -C "$CHECKOUT" switch "$BRANCH"
    [[ "$(git -C "$CHECKOUT" rev-parse HEAD)" == "$(git -C "$CHECKOUT" rev-parse "origin/$BRANCH")" ]] || \
        die "Reuse checkout is not exactly origin/$BRANCH; update it manually after review"
else
    git clone --branch "$BRANCH" --single-branch "$REPOSITORY_URL" "$TARGET"
    CHECKOUT="$TARGET"
fi
cd "$CHECKOUT"
echo "commit=$(git rev-parse HEAD)"
git status --short --branch

echo
echo "===== STATIC VALIDATION ====="
python3 -m unittest discover -s deployment/tests -v
python3 -m unittest discover -s bench -p 'test_*.py' -v
python3 -m py_compile bench/runtime_validation.py bench/test_runtime_validation.py
bash -n start.sh download.sh stop.sh files/memwatch.sh deployment/*.sh clone-install-test.sh
python3 deployment/manifest_release.py verify . .
git diff --check

echo
echo "===== HOST PREFLIGHT ====="
uname -a
cat /etc/os-release 2>/dev/null | sed -n '1,12p'
awk '/^(MemTotal|MemAvailable|MemFree|SwapTotal|SwapFree):/ {print}' /proc/meminfo
df -h . "$STATE_DIR" 2>/dev/null || true
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv,noheader
else
    echo "nvidia-smi: unavailable"
fi
if command -v docker >/dev/null 2>&1; then
    docker version --format 'docker_client={{.Client.Version}} docker_server={{.Server.Version}}' 2>&1 || true
else
    echo "docker: unavailable"
fi
sysctl vm.min_free_kbytes vm.watermark_scale_factor vm.swappiness 2>&1 || true

if [[ "$FULL" != true ]]; then
    echo
    echo "Preflight completed. Review this log, then run:"
    echo "  $0 --full --reuse --target '$TARGET' --log '$LOG_FILE'"
    exit 0
fi

echo
echo "===== PINNED ASSET BOOTSTRAP ====="
sudo docker pull "$IMAGE"
sudo install -d -m 0755 "$STATE_DIR/huggingface"
sudo docker run --rm --pull=never \
    -e HF_HOME=/hf \
    -e HF_ENDPOINT=https://huggingface.co \
    -e HF_HUB_DISABLE_TELEMETRY=1 \
    -e HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
    -e DO_NOT_TRACK=1 \
    -v "$STATE_DIR/huggingface:/hf" \
    --entrypoint python3 \
    "$IMAGE" \
    -c 'from huggingface_hub import snapshot_download; snapshot_download(repo_id="'"$MODEL_ID"'", revision="'"$MODEL_REVISION"'", cache_dir="/hf/hub", token=False)'

echo
echo "===== SYSTEM-WIDE VM PROFILE ====="
echo "Applying reviewed values: min_free_kbytes=4194304, watermark_scale_factor=300, swappiness=30"
sudo sysctl -p files/sysctl-spark3.conf
sudo install -b -m 0644 files/sysctl-spark3.conf /etc/sysctl.d/90-qwen38-qualified-profile.conf
/bin/bash deployment/check-host-profile.sh

echo
echo "===== IMMUTABLE SERVICE INSTALL ====="
if [[ -n "$DRAFT_VOCAB" ]]; then
    sudo env QWEN_DRAFT_VOCAB="$DRAFT_VOCAB" /bin/bash deployment/install-root.sh
elif [[ -n "$CORPUS_DIR" ]]; then
    sudo env QWEN_CORPUS_DIR="$CORPUS_DIR" /bin/bash deployment/install-root.sh
else
    echo "No corpus/vocabulary supplied: using complete MTP vocabulary (functional bootstrap mode)."
    sudo env QWEN_DRAFT_VOCAB=off /bin/bash deployment/install-root.sh
fi

echo
echo "===== RUNTIME BEHAVIOR VALIDATION ====="
python3 bench/runtime_validation.py smoke --prefill-tokens 8192
python3 bench/runtime_validation.py mixed --prefill-tokens "$PREFILL_TOKENS"

echo
echo "===== SUCCESS ====="
echo "Clone, installation and runtime validation completed."
echo "LOG_FILE=$LOG_FILE"
