#!/usr/bin/env bash
set -Eeuo pipefail

# Safely remove the managed Qwen3.8 service. Large caches and host-wide tuning
# are preserved unless their explicit options are selected.
[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "Run with sudo: sudo bash ./uninstall-root.sh" >&2; exit 1; }

INSTALL_BASE="/opt/qwen3.8-flash-next"
STATE_DIR="/var/lib/qwen3.8-flash-next"
BACKUP_BASE="/var/backups/qwen3.8-flash-next"
UNIT="qwen3.8-flash-next.service"
UNIT_FILE="/etc/systemd/system/$UNIT"
SYSCTL_FILE="/etc/sysctl.d/90-qwen38-qualified-profile.conf"
CONTAINER="vllm-fn-tp1"
IMAGE="vllm/vllm-openai:qwen38-flash-next@sha256:fc120ece0a388cc0aa1caad4a9f1cd92113484ab7ec2fd0efadd62585be05bf8"

PURGE_STATE=false
PURGE_BACKUPS=false
REMOVE_IMAGE=false
REMOVE_SYSCTL=false
ASSUME_YES=false

usage() {
    cat <<'EOF'
Usage: sudo bash ./uninstall-root.sh [options]

Default removal:
  - stop and disable qwen3.8-flash-next.service
  - remove the managed vllm-fn-tp1 container if it remains
  - remove the systemd unit and /opt/qwen3.8-flash-next releases

Preserved by default:
  - model/Hugging Face cache and draft/corpus state
  - rollback backups
  - pinned Docker image
  - system-wide VM/sysctl profile

Options:
  --purge-state           Also delete /var/lib/qwen3.8-flash-next
  --purge-backups         Also delete /var/backups/qwen3.8-flash-next
  --remove-image          Also remove the pinned Docker image
  --remove-sysctl-profile Remove the persistent Qwen sysctl file
  --purge-all             Select all four optional removals
  --yes                   Skip the interactive REMOVE confirmation
  -h, --help              Show this help

Removing the sysctl file does not guess or immediately restore previous runtime
values. Reboot or apply a separately reviewed host policy after removal.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge-state) PURGE_STATE=true ;;
        --purge-backups) PURGE_BACKUPS=true ;;
        --remove-image) REMOVE_IMAGE=true ;;
        --remove-sysctl-profile) REMOVE_SYSCTL=true ;;
        --purge-all)
            PURGE_STATE=true
            PURGE_BACKUPS=true
            REMOVE_IMAGE=true
            REMOVE_SYSCTL=true
            ;;
        --yes) ASSUME_YES=true ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

require_safe_directory() {
    local path="$1"
    if [[ -e "$path" || -L "$path" ]]; then
        [[ -d "$path" && ! -L "$path" ]] || { echo "Refusing unsafe directory: $path" >&2; exit 1; }
        [[ "$(stat -c '%u' "$path")" == 0 ]] || { echo "Refusing non-root-owned directory: $path" >&2; exit 1; }
    fi
}

require_safe_file() {
    local path="$1"
    if [[ -e "$path" || -L "$path" ]]; then
        [[ -f "$path" && ! -L "$path" ]] || { echo "Refusing unsafe file: $path" >&2; exit 1; }
        [[ "$(stat -c '%u' "$path")" == 0 ]] || { echo "Refusing non-root-owned file: $path" >&2; exit 1; }
    fi
}

require_safe_directory "$INSTALL_BASE"
require_safe_directory "$STATE_DIR"
require_safe_directory "$BACKUP_BASE"
require_safe_file "$UNIT_FILE"
require_safe_file "$SYSCTL_FILE"

# Do not delete an unrelated unit merely because it has the same filename.
if [[ -f "$UNIT_FILE" ]]; then
    grep -Fq '/opt/qwen3.8-flash-next/current/deployment/service-runner.sh' "$UNIT_FILE" || {
        echo "Refusing unmanaged unit file: $UNIT_FILE" >&2
        exit 1
    }
fi

echo "Qwen3.8 managed uninstall plan"
echo "  remove service/unit: yes"
echo "  remove releases:     yes ($INSTALL_BASE)"
echo "  purge model/state:   $PURGE_STATE ($STATE_DIR)"
echo "  purge backups:       $PURGE_BACKUPS ($BACKUP_BASE)"
echo "  remove Docker image: $REMOVE_IMAGE"
echo "  remove sysctl file:  $REMOVE_SYSCTL ($SYSCTL_FILE)"
echo
echo "The Git checkout is never deleted."

if [[ "$ASSUME_YES" != true ]]; then
    [[ -r /dev/tty && -w /dev/tty ]] || { echo "No interactive terminal; rerun with --yes after reviewing the plan." >&2; exit 1; }
    read -r -p "Type REMOVE to continue: " answer </dev/tty
    [[ "$answer" == REMOVE ]] || { echo "Cancelled."; exit 0; }
fi

echo "Stopping and disabling $UNIT ..."
systemctl stop "$UNIT" 2>/dev/null || true
systemctl disable "$UNIT" 2>/dev/null || true

# service-runner normally removes/stops its child. Clean up only the exact
# container name owned by this recipe if it is still present.
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
    docker rm -f "$CONTAINER"
fi

if [[ -f "$UNIT_FILE" ]]; then
    rm -f -- "$UNIT_FILE"
fi
systemctl daemon-reload
systemctl reset-failed "$UNIT" 2>/dev/null || true

if [[ -d "$INSTALL_BASE" ]]; then
    rm -rf --one-file-system -- "$INSTALL_BASE"
fi
if [[ "$PURGE_STATE" == true && -d "$STATE_DIR" ]]; then
    rm -rf --one-file-system -- "$STATE_DIR"
fi
if [[ "$PURGE_BACKUPS" == true && -d "$BACKUP_BASE" ]]; then
    rm -rf --one-file-system -- "$BACKUP_BASE"
fi
if [[ "$REMOVE_IMAGE" == true ]] && docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker image rm "$IMAGE"
fi
if [[ "$REMOVE_SYSCTL" == true && -f "$SYSCTL_FILE" ]]; then
    rm -f -- "$SYSCTL_FILE"
    echo "Persistent sysctl profile removed. Runtime values remain until reboot or an explicitly applied host policy."
fi

echo
echo "Qwen3.8 managed service uninstall completed."
[[ "$PURGE_STATE" == true ]] || echo "Preserved state/model cache: $STATE_DIR"
[[ "$PURGE_BACKUPS" == true ]] || echo "Preserved rollback backups: $BACKUP_BASE"
[[ "$REMOVE_IMAGE" == true ]] || echo "Preserved Docker image: $IMAGE"
[[ "$REMOVE_SYSCTL" == true ]] || echo "Preserved sysctl profile: $SYSCTL_FILE"
