#!/usr/bin/env bash
set -euo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
CURRENT_LINK="/opt/qwen3.8-flash-next/current"
PREVIOUS="$(cat /var/lib/qwen3.8-flash-next/previous-release)"
[[ -n "$PREVIOUS" && "$PREVIOUS" == /opt/qwen3.8-flash-next/releases/* && -d "$PREVIOUS" && ! -L "$PREVIOUS" && "$(readlink -f "$PREVIOUS")" == "$PREVIOUS" ]] || {
    echo "Previous managed release is unavailable; no rollback was performed." >&2; exit 1;
}
[[ -L "$CURRENT_LINK" && ! -L /etc/systemd/system/qwen3.8-flash-next.service ]] || { echo "Unsafe current link or service file." >&2; exit 1; }
[[ -f "$PREVIOUS/deployment/qwen3.8-flash-next.service" ]] || {
    echo "Previous service definition is unavailable." >&2
    exit 1
}
/bin/bash "$(dirname "${BASH_SOURCE[0]}")/assert-idle.sh"
systemctl stop qwen3.8-flash-next.service
ln -sfnT "$PREVIOUS" "$CURRENT_LINK"
install -m 0644 "$PREVIOUS/deployment/qwen3.8-flash-next.service" /etc/systemd/system/qwen3.8-flash-next.service
systemctl daemon-reload
systemctl reset-failed qwen3.8-flash-next.service 2>/dev/null || true
systemctl start qwen3.8-flash-next.service
printf '%s\n' "$PREVIOUS" > /var/lib/qwen3.8-flash-next/current-release
echo "Rollback started: $PREVIOUS"
