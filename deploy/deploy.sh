#!/usr/bin/env bash
# Deploy do agezada: puxa o codigo novo, atualiza deps e reinicia o servico.
# Uso na VM: /opt/agezada/deploy/deploy.sh
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/agezada}
APP_USER=${APP_USER:-aoe4}
SERVICE=agezada.service
HEALTH_URL=http://127.0.0.1:8000/api/health

echo ">> git pull"
sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only

echo ">> dependencias"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo ">> restart"
sudo systemctl restart "$SERVICE"

echo ">> health check"
for i in $(seq 1 10); do
    body=$(curl -fsS "$HEALTH_URL" 2>/dev/null || echo "")
    if [ -n "$body" ]; then
        echo "OK: $body"
        # Sem a chave summaries o processo ainda e o antigo (codigo pre-resumos).
        case "$body" in
            *'"summaries"'*) ;;
            *) echo "AVISO: resposta sem a chave 'summaries' - confira se o servico recarregou o codigo novo." >&2 ;;
        esac
        exit 0
    fi
    sleep 1
done

echo "FALHOU - servico nao respondeu. Ultimos logs:" >&2
sudo journalctl -u "$SERVICE" -n 20 --no-pager >&2
exit 1
