#!/usr/bin/env bash
set -euo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "Run as root." >&2; exit 1; }

# Explicit operator-invoked maintenance, never run automatically by install.
# Keep Ubuntu's phased-update policy. Never force phased packages, autoremove
# rollback kernels, install firmware, or reboot without a separate decision.
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y dist-upgrade
if command -v snap >/dev/null 2>&1; then snap refresh; fi
if command -v fwupdmgr >/dev/null 2>&1; then
    fwupdmgr refresh --force || true
    fwupdmgr get-updates || true
fi

if [[ -f /var/run/reboot-required ]]; then
    echo "REBOOT_REQUIRED"
    cat /var/run/reboot-required.pkgs 2>/dev/null || true
else
    echo "REBOOT_NOT_REQUIRED"
fi
