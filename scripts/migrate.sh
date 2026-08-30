#!/usr/bin/env bash
set -euo pipefail
cd /root/phoe_lone_server
exec .venv/bin/alembic upgrade head
