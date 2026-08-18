#!/usr/bin/env bash
# Deploy do agezada: puxa o codigo novo, atualiza deps e reinicia o servico.
# Uso na VM: /opt/agezada/deploy/deploy.sh
set -euo pipefail

APP_DIR=/opt/agezada
APP_USER=aoe4
SERVICE=agezada.service
HEALTH_URL=http://127.0.0.1:8000/

echo ">> git pull"
sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only

echo ">> dependencias"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo ">> restart"
sudo systemctl restart "$SERVICE"

echo ">> health check"
for i in $(seq 1 10); do
    code=$(curl -fsS -o /dev/null -w '%{http_code}' "$HEALTH_URL" 2>/dev/null || echo 000)
    if [ "$code" = "200" ]; then
        echo "OK ($code)"
        exit 0
    fi
    sleep 1
done

echo "FALHOU - servico nao respondeu 200. Ultimos logs:" >&2
sudo journalctl -u "$SERVICE" -n 20 --no-pager >&2
exit 1
