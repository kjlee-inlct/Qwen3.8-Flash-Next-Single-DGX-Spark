#!/usr/bin/env bash
set -euo pipefail

# The shipped 4 GiB watchdog was measured with these exact kernel reserves.
# Check rather than silently applying system-wide changes; recheck at each boot.
for specification in vm.min_free_kbytes:4194304 vm.watermark_scale_factor:300 vm.swappiness:30; do
    key="${specification%:*}"
    expected="${specification##*:}"
    actual="$(sysctl -n "$key")" || {
        echo "Cannot read $key; refusing the qualified memory profile." >&2
        exit 1
    }
    [[ "$actual" == "$expected" ]] || {
        echo "$key=$actual; qualified profile requires $expected. Review files/sysctl-spark3.conf and the explicit opt-in commands in deployment/README.md before installation/startup." >&2
        exit 1
    }
done
