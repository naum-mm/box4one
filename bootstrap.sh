#!/usr/bin/env bash
# Single downloaded entry point for the complete BOX4ONE lifecycle.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root'; exit 1; }
case "${1:-}" in
    install|update|backup|restore|uninstall) ;;
    --help|-h|'') echo 'Usage: bootstrap.sh install DOMAIN | update | backup [--include-files] | restore ARCHIVE | uninstall [--purge] [--include-files]'; exit 0 ;;
    *) set -- install "$@" ;; # Compatibility with the original DOMAIN argument.
esac
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || { echo 'Ubuntu 24.04 required'; exit 1; }
if ! command -v git >/dev/null || ! command -v python3 >/dev/null; then
    apt-get update
    apt-get install -y git python3 ca-certificates curl
fi
stage=$(mktemp -d /tmp/box4one-manage.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
git clone --quiet --depth 1 --branch main https://github.com/naum-mm/box4one.git "$stage/project"
python3 "$stage/project/scripts/manage.py" --source "$stage/project" "$@"
