#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
    echo "Run with sudo: sudo bash ./install-openwebui-proxy-root.sh" >&2
    exit 1
}

SOCKET_UNIT="qwen3.8-openwebui-proxy.socket"
SERVICE_UNIT="qwen3.8-openwebui-proxy.service"
SOCKET_FILE="/etc/systemd/system/$SOCKET_UNIT"
SERVICE_FILE="/etc/systemd/system/$SERVICE_UNIT"
BACKEND_HOST="127.0.0.1"
BACKEND_PORT=8888
LISTEN_PORT=8000

usage() {
    cat <<'EOF'
Usage: sudo bash ./install-openwebui-proxy-root.sh [options]

Installs a systemd socket proxy from Docker's host gateway to the loopback-only
Qwen API. OpenWebUI can keep using http://host.docker.internal:8000/v1 while
vLLM remains bound to 127.0.0.1:8888.

Options:
  --listen-port PORT   Docker-side port (default: 8000)
  --backend-port PORT  Loopback Qwen port (default: 8888)
  -h, --help           Show this help

The listen address is always discovered from docker0. Arbitrary addresses and
0.0.0.0 are intentionally unsupported.
EOF
}

valid_port() {
    [[ "$1" =~ ^[0-9]+$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 ))
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --listen-port)
            [[ $# -ge 2 ]] || { echo "--listen-port needs a value" >&2; exit 2; }
            LISTEN_PORT="$2"; shift 2 ;;
        --backend-port)
            [[ $# -ge 2 ]] || { echo "--backend-port needs a value" >&2; exit 2; }
            BACKEND_PORT="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

valid_port "$LISTEN_PORT" || { echo "Invalid listen port: $LISTEN_PORT" >&2; exit 2; }
valid_port "$BACKEND_PORT" || { echo "Invalid backend port: $BACKEND_PORT" >&2; exit 2; }
[[ "$LISTEN_PORT" != "$BACKEND_PORT" ]] || {
    echo "Listen and backend ports must differ." >&2
    exit 2
}

for command in ip systemctl ss awk grep install; do
    command -v "$command" >/dev/null 2>&1 || { echo "Required command missing: $command" >&2; exit 1; }
done

PROXY_BIN="$(command -v systemd-socket-proxyd 2>/dev/null || true)"
for candidate in /usr/lib/systemd/systemd-socket-proxyd /lib/systemd/systemd-socket-proxyd; do
    [[ -x "$candidate" ]] && PROXY_BIN="$candidate" && break
done
[[ -n "$PROXY_BIN" && -x "$PROXY_BIN" ]] || {
    echo "systemd-socket-proxyd was not found." >&2
    exit 1
}

LISTEN_HOST="$(ip -4 -o addr show dev docker0 scope global 2>/dev/null | awk 'NR == 1 {split($4, a, "/"); print a[1]}')"
[[ -n "$LISTEN_HOST" ]] || {
    echo "docker0 has no global IPv4 address; refusing to guess a Docker gateway." >&2
    exit 1
}
[[ "$LISTEN_HOST" != "0.0.0.0" && "$LISTEN_HOST" != "127."* ]] || {
    echo "Unsafe docker0 address: $LISTEN_HOST" >&2
    exit 1
}

if [[ -e "$SOCKET_FILE" || -e "$SERVICE_FILE" ]]; then
    [[ -f "$SOCKET_FILE" && ! -L "$SOCKET_FILE" && -f "$SERVICE_FILE" && ! -L "$SERVICE_FILE" ]] || {
        echo "Refusing unsafe or partial existing proxy unit files." >&2
        exit 1
    }
    grep -Fq 'Managed Qwen3.8 OpenWebUI proxy' "$SOCKET_FILE" || {
        echo "Refusing unmanaged unit: $SOCKET_FILE" >&2
        exit 1
    }
    grep -Fq 'systemd-socket-proxyd 127.0.0.1:' "$SERVICE_FILE" || {
        echo "Refusing unmanaged unit: $SERVICE_FILE" >&2
        exit 1
    }
else
    if ss -H -ltn | awk '{print $4}' | grep -Eq "(^|\])${LISTEN_HOST}:${LISTEN_PORT}$|^${LISTEN_HOST}:${LISTEN_PORT}$"; then
        echo "$LISTEN_HOST:$LISTEN_PORT is already in use." >&2
        exit 1
    fi
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "$tmp_dir"' EXIT

cat >"$tmp_dir/$SOCKET_UNIT" <<EOF
# Managed Qwen3.8 OpenWebUI proxy; installed by install-openwebui-proxy-root.sh
[Unit]
Description=Managed Qwen3.8 OpenWebUI proxy socket
After=docker.service
Requires=docker.service

[Socket]
ListenStream=$LISTEN_HOST:$LISTEN_PORT
NoDelay=true

[Install]
WantedBy=sockets.target
EOF

cat >"$tmp_dir/$SERVICE_UNIT" <<EOF
# Managed Qwen3.8 OpenWebUI proxy; installed by install-openwebui-proxy-root.sh
[Unit]
Description=Managed Qwen3.8 OpenWebUI proxy service
Requires=qwen3.8-flash-next.service
After=qwen3.8-flash-next.service

[Service]
ExecStart=$PROXY_BIN $BACKEND_HOST:$BACKEND_PORT
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
NoNewPrivileges=true
EOF

systemctl stop "$SOCKET_UNIT" "$SERVICE_UNIT" 2>/dev/null || true
install -o root -g root -m 0644 "$tmp_dir/$SOCKET_UNIT" "$SOCKET_FILE"
install -o root -g root -m 0644 "$tmp_dir/$SERVICE_UNIT" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable --now "$SOCKET_UNIT"

echo "OpenWebUI proxy installed."
echo "  Docker endpoint: http://host.docker.internal:$LISTEN_PORT/v1"
echo "  Listen socket:   $LISTEN_HOST:$LISTEN_PORT"
echo "  Qwen backend:    http://$BACKEND_HOST:$BACKEND_PORT/v1"
echo "Test from the container:"
echo "  docker exec open-webui curl -fsS http://host.docker.internal:$LISTEN_PORT/v1/models"
