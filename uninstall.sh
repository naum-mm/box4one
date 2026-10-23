#!/usr/bin/env bash
# Default: stop the application, preserving its configuration and data.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
root=$(pwd -P)
mode=keep
confirmed=no
for arg in "$@"; do
    case "$arg" in
        --purge) mode=purge ;;
        --yes) confirmed=yes ;;
        --help|-h) echo 'Usage: sudo ./uninstall.sh [--purge [--yes]]'; exit 0 ;;
        *) echo "Unknown option: $arg"; exit 2 ;;
    esac
done
[[ $EUID == 0 ]] || { echo 'Run as root'; exit 1; }
[[ -f compose.yaml && -f .box4one-install ]] || { echo 'Installation marker missing. Nothing removed.'; exit 1; }
[[ $(cat .box4one-install) == "$root" ]] || { echo 'Installation path changed. Nothing removed; inspect the marker.'; exit 1; }
case "$root" in /|/opt|/usr|/etc|/var|/home|/root|/tmp) echo 'Unsafe project directory'; exit 1 ;; esac
for service in mariadb backend nginx worker cleanup; do
    if docker inspect "ytdlp-$service" >/dev/null 2>&1; then
        owner=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "ytdlp-$service")
        [[ "$owner" == "$root" ]] || { echo "Container belongs to another installation: $owner"; exit 1; }
    fi
done
if [[ $mode == purge && $confirmed != yes ]]; then
    echo "Permanently remove $root, including database, downloads and passwords?"
    read -r -p 'Type DELETE to continue: ' answer
    [[ $answer == DELETE ]] || { echo 'Cancelled'; exit 1; }
fi
docker compose down
# Remove only our renewal hook, when it points to this installation.
python3 - "$root" <<'PY'
import shlex, sys
from pathlib import Path
hook=Path('/etc/letsencrypt/renewal-hooks/deploy/ytdlp-nginx')
if hook.is_file() and not hook.is_symlink():
    lines=hook.read_text().splitlines()
    if 'cd '+shlex.quote(sys.argv[1]) in lines:
        hook.unlink()
        print('Removed this installation\'s Nginx renewal hook.')
PY
if [[ $mode == purge ]]; then
    cd /
    rm -rf --one-file-system -- "$root"
    echo 'Project and data removed. Docker, images, Certbot and certificates retained.'
else
    echo "Application stopped. Data and configuration retained in $root."
    echo 'Resume: docker compose up -d (in project directory); rerun enable-https.sh to restore the renewal hook.'
fi
