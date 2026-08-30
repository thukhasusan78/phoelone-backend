#!/usr/bin/env bash
set -euo pipefail
cp /root/phoe_lone_server/Caddyfile /etc/caddy/Caddyfile
systemctl reload caddy
echo "Caddy reloaded"
