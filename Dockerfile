# wILife — Django and the WhatsApp (Baileys) bridge in one Render service.
# start.sh runs the bridge on 127.0.0.1:3001 and gunicorn on $PORT.

# --- Stage 1: install the bridge's Node dependencies -------------------------
FROM node:22-bookworm-slim AS bridge
WORKDIR /bridge
COPY whatsapp_bridge/package.json whatsapp_bridge/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY whatsapp_bridge/ ./

# --- Stage 2: Python app + the node binary -----------------------------------
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BRIDGE_PORT=3001 \
    WHATSAPP_BRIDGE_URL=http://127.0.0.1:3001

# Same Debian release as stage 1, so native modules (e.g. sharp) still load.
COPY --from=bridge /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
COPY --from=bridge /bridge /app/whatsapp_bridge

# Settings require these at import time; collectstatic never touches the DB.
RUN DJANGO_SECRET_KEY=build DB_USER=build DB_PASSWORD=build DB_HOST=build \
    python manage.py collectstatic --noinput

RUN chmod +x start.sh
CMD ["./start.sh"]
