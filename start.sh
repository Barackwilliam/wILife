#!/usr/bin/env bash
# Start the WhatsApp bridge in the background, then Django in the foreground.
# Used by the Dockerfile. The bridge listens on localhost only — the outside
# world reaches it through Django (/agent/whatsapp/qr/ for linking).
set -euo pipefail

export PORT="${PORT:-8000}"
WEB_PORT="$PORT"
export BRIDGE_PORT="${BRIDGE_PORT:-3001}"

# One shared secret for both sides.
export BRIDGE_API_KEY="${BRIDGE_API_KEY:-${WHATSAPP_BRIDGE_KEY:-}}"
export WHATSAPP_BRIDGE_KEY="${WHATSAPP_BRIDGE_KEY:-$BRIDGE_API_KEY}"
export WHATSAPP_BRIDGE_URL="${WHATSAPP_BRIDGE_URL:-http://127.0.0.1:$BRIDGE_PORT}"

# Keep the WhatsApp session in the same Postgres as Django, so a redeploy does
# not need a new QR scan. Built from the DB_* variables unless set explicitly.
if [ -z "${DATABASE_URL:-}" ] && [ -n "${DB_HOST:-}" ]; then
    export DATABASE_URL="$(python - <<'PY'
import os
from urllib.parse import quote
e = os.environ
print("postgresql://{}:{}@{}:{}/{}".format(
    quote(e["DB_USER"], safe=""), quote(e["DB_PASSWORD"], safe=""),
    e["DB_HOST"], e.get("DB_PORT", "5432"), e.get("DB_NAME", "postgres")))
PY
)"
fi

python manage.py migrate --noinput

if [ -n "$BRIDGE_API_KEY" ]; then
    (
        cd whatsapp_bridge
        # Restart the bridge if it ever exits; Django keeps serving meanwhile.
        while true; do
            DJANGO_URL="http://127.0.0.1:$WEB_PORT" PORT="$BRIDGE_PORT" BRIDGE_HOST=127.0.0.1 \
                node server.js || true
            echo "whatsapp bridge exited — restarting in 5s"
            sleep 5
        done
    ) &
else
    echo "WHATSAPP_BRIDGE_KEY not set — WhatsApp bridge not started"
fi

exec gunicorn personal_assistant.wsgi \
    --bind "0.0.0.0:$PORT" \
    --workers "${WEB_CONCURRENCY:-2}" \
    --timeout 60 \
    --access-logfile -
