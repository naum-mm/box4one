#!/usr/bin/env bash
# Download and install BOX4ONE on a fresh Ubuntu 24.04 VPS.
set -euo pipefail
[[ $# == 1 ]] || { echo 'Usage: sudo bash bootstrap.sh video.example.com'; exit 2; }
[[ $EUID == 0 ]] || { echo 'Run as root'; exit 1; }
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || { echo 'Ubuntu 24.04 required'; exit 1; }
[[ $1 =~ ^[a-z0-9][a-z0-9.-]*\.[a-z0-9-]+$ ]] || { echo 'Expected an ASCII domain, without scheme or path'; exit 2; }
target=/opt/ytdlp-web
[[ ! -e $target ]] || { echo "$target already exists. Nothing overwritten. See docs/SETUP.md."; exit 1; }
apt-get update
apt-get install -y git python3 ca-certificates curl
stage=$(mktemp -d /opt/box4one-install.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
git clone --depth 1 --branch main https://github.com/naum-mm/box4one.git "$stage/project"
python3 "$stage/project/scripts/configure.py" init --domain "$1"
mv -T -- "$stage/project" "$target"
cd "$target"
bash ./install.sh "$1"
