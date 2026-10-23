#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ $# != 1 ]]; then echo "Usage: sudo ./install.sh video.example.com"; exit 2; fi
[[ $EUID == 0 ]] || { echo 'Run as root'; exit 1; }
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || { echo 'Automatic installer supports Ubuntu 24.04 only'; exit 1; }
apt-get update
apt-get install -y python3 ca-certificates curl
python3 scripts/configure.py init --domain "$1"
if ! command -v docker >/dev/null; then
    for package in docker.io docker-compose containerd runc podman-docker; do
        if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
            echo "Conflicting existing package: $package; install Docker manually (README). No packages removed."; exit 1
        fi
    done
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
docker compose version
systemctl enable --now docker
# Do not touch containers from another installation.
for service in mariadb backend nginx worker cleanup; do
    if docker inspect "ytdlp-$service" >/dev/null 2>&1; then
        owner=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "ytdlp-$service")
        [[ "$owner" == "$PWD" ]] || { echo "Container ytdlp-$service belongs to $owner; stopping installation"; exit 1; }
    fi
done
printf "%s\n" "$PWD" > .box4one-install
chmod 600 .box4one-install
mkdir -p jobs mariadb/data certbot/www /etc/letsencrypt
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 240
./scripts/diagnose.sh
echo "Installed. Access words are in $PWD/.env (DOWNLOAD_ACCESS_WORD / QUEUE_ACCESS_WORD)."
echo 'Next: ./enable-https.sh (see README.md)'
