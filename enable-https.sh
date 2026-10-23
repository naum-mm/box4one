#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
[[ $EUID == 0 ]] || { echo 'Run as root'; exit 1; }
domain=$(python3 scripts/configure.py domain)
if ! command -v certbot >/dev/null; then
    if [[ -x /snap/bin/certbot ]]; then
        export PATH="/snap/bin:$PATH"
    else
        echo 'Install Certbot as described in README.md, then rerun.'; exit 1
    fi
fi
# Certbot asks for email and agreement interactively; no implicit consent.
certbot certonly --webroot -w "$PWD/certbot/www" --cert-name "$domain" -d "$domain"
test -s "/etc/letsencrypt/live/$domain/fullchain.pem"
test -s "/etc/letsencrypt/live/$domain/privkey.pem"
backup=$(mktemp "$PWD/nginx/pre-https.XXXXXX")
cp nginx/nginx.conf "$backup"
restore() { cat "$backup" > nginx/nginx.conf; echo "Restored configuration from $backup" >&2; }
python3 scripts/configure.py https
if ! docker compose run --rm --no-deps nginx nginx -t; then restore; exit 1; fi
# Recreate ensures the new bind-mounted file and published ports are used.
if ! docker compose up -d --no-deps --force-recreate nginx; then
    restore
    docker compose up -d --no-deps --force-recreate nginx
    exit 1
fi
# Write a real shebang, without a leading backslash. Quote the project path.
python3 - <<'PY'
from pathlib import Path
import shlex
root=Path.cwd()
hook=Path('/etc/letsencrypt/renewal-hooks/deploy/ytdlp-nginx')
hook.parent.mkdir(parents=True,exist_ok=True)
hook.write_text('#!/bin/sh\nset -eu\ncd '+shlex.quote(str(root))+'\n/usr/bin/docker compose exec -T nginx nginx -t\n/usr/bin/docker compose exec -T nginx nginx -s reload\n')
hook.chmod(0o755)
PY
/etc/letsencrypt/renewal-hooks/deploy/ytdlp-nginx
curl --retry 8 --retry-connrefused --retry-delay 2 --fail "https://$domain/health"
echo
certbot renew --cert-name "$domain" --dry-run --run-deploy-hooks
echo "HTTPS ready: https://$domain"
