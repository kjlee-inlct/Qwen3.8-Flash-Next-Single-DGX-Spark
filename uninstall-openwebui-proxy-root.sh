#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
    echo "Run with sudo: sudo bash ./uninstall-openwebui-proxy-root.sh" >&2
    exit 1
}

SOCKET_UNIT="qwen3.8-openwebui-proxy.socket"
SERVICE_UNIT="qwen3.8-openwebui-proxy.service"
SOCKET_FILE="/etc/systemd/system/$SOCKET_UNIT"
SERVICE_FILE="/etc/systemd/system/$SERVICE_UNIT"
ASSUME_YES=false

usage() {
    cat <<'EOF'
Usage: sudo bash ./uninstall-openwebui-proxy-root.sh [--yes]

Stops and removes only the managed OpenWebUI-to-Qwen proxy. The Qwen service,
model cache, releases, Docker containers and OpenWebUI configuration are kept.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes) ASSUME_YES=true ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

for path in "$SOCKET_FILE" "$SERVICE_FILE"; do
    if [[ -e "$path" || -L "$path" ]]; then
        [[ -f "$path" && ! -L "$path" ]] || { echo "Refusing unsafe unit file: $path" >&2; exit 1; }
        [[ "$(stat -c '%u' "$path")" == 0 ]] || { echo "Refusing non-root-owned unit: $path" >&2; exit 1; }
        grep -Fq 'Managed Qwen3.8 OpenWebUI proxy' "$path" || {
            echo "Refusing unmanaged unit: $path" >&2
            exit 1
        }
    fi
done

if [[ ! -e "$SOCKET_FILE" && ! -e "$SERVICE_FILE" ]]; then
    echo "Managed OpenWebUI proxy is not installed."
    exit 0
fi

if [[ "$ASSUME_YES" != true ]]; then
    echo "This removes only the managed OpenWebUI proxy; Qwen and OpenWebUI are preserved."
    [[ -r /dev/tty && -w /dev/tty ]] || {
        echo "No interactive terminal; rerun with --yes after reviewing the plan." >&2
        exit 1
    }
    read -r -p "Type REMOVE-PROXY to continue: " answer </dev/tty
    [[ "$answer" == "REMOVE-PROXY" ]] || { echo "Cancelled."; exit 0; }
fi

systemctl disable --now "$SOCKET_UNIT" 2>/dev/null || true
systemctl stop "$SERVICE_UNIT" 2>/dev/null || true
rm -f -- "$SOCKET_FILE" "$SERVICE_FILE"
systemctl daemon-reload
systemctl reset-failed "$SOCKET_UNIT" "$SERVICE_UNIT" 2>/dev/null || true
echo "Managed OpenWebUI proxy removed. Qwen and OpenWebUI were not changed."
