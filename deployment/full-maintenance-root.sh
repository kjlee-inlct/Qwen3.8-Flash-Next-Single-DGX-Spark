#!/usr/bin/env bash
set -euo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
DEPLOYMENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAINTENANCE_STARTED=0
[[ "$(systemctl show qwen3.8-flash-next.service --property=LoadState --value)" == loaded ]] || {
    echo "This maintenance wrapper requires an existing Qwen service. For first install use install-root.sh." >&2
    exit 1
}

recover_on_failure() {
    status=$?
    trap - EXIT INT TERM
    if [[ "$status" -ne 0 && "$MAINTENANCE_STARTED" -eq 1 ]]; then
        # The install script may already have restored and started the previous
        # release. Never interrupt a running service or an automatic restart.
        unit_state="$(systemctl show qwen3.8-flash-next.service --property=ActiveState --value 2>/dev/null || true)"
        case "$unit_state" in
            inactive|failed)
                echo "Maintenance failed; attempting to restart the current Qwen release." >&2
                systemctl reset-failed qwen3.8-flash-next.service 2>/dev/null || true
                systemctl start qwen3.8-flash-next.service || \
                    echo "Qwen recovery did not start; inspect its journal." >&2
                ;;
        esac
    fi
    exit "$status"
}
trap recover_on_failure EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

/bin/bash "$DEPLOYMENT_DIR/assert-idle.sh"
echo "Stopping Qwen before OS maintenance to release its memory budget."
systemctl stop qwen3.8-flash-next.service
MAINTENANCE_STARTED=1
/bin/bash "$DEPLOYMENT_DIR/maintenance-root.sh"
/bin/bash "$DEPLOYMENT_DIR/install-root.sh"
