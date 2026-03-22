#!/bin/bash
# Deploy AI-Cargo outreach bot on a fresh Ubuntu VPS (22.04+)
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/.../deploy-vps.sh | bash
#   — or —
#   bash scripts/deploy-vps.sh
#
# Prerequisites: SSH access to Ubuntu VPS with sudo

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ai-cargo/outreach-bot.git}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/ai-cargo}"
BRANCH="${BRANCH:-main}"

log() { echo "==> $*"; }

# -------------------------------------------------------------------
# 1. System packages
# -------------------------------------------------------------------
log "Updating system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq curl git ca-certificates gnupg lsb-release

# -------------------------------------------------------------------
# 2. Install Docker (if not present)
# -------------------------------------------------------------------
if ! command -v docker &>/dev/null; then
    log "Installing Docker..."
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
        sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
       https://download.docker.com/linux/ubuntu \
       $(lsb_release -cs) stable" | \
      sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update -qq
    sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin

    sudo systemctl enable docker
    sudo systemctl start docker
    sudo usermod -aG docker "$USER"
    log "Docker installed. You may need to re-login for group changes."
else
    log "Docker already installed: $(docker --version)"
fi

# -------------------------------------------------------------------
# 3. Clone / update repo
# -------------------------------------------------------------------
if [ -d "$DEPLOY_DIR" ]; then
    log "Updating repo in $DEPLOY_DIR..."
    cd "$DEPLOY_DIR"
    git fetch origin
    git checkout "$BRANCH"
    git pull origin "$BRANCH"
else
    log "Cloning repo to $DEPLOY_DIR..."
    sudo mkdir -p "$DEPLOY_DIR"
    sudo chown "$USER":"$USER" "$DEPLOY_DIR"
    git clone -b "$BRANCH" "$REPO_URL" "$DEPLOY_DIR"
    cd "$DEPLOY_DIR"
fi

# -------------------------------------------------------------------
# 4. Environment file
# -------------------------------------------------------------------
if [ ! -f .env ]; then
    log "Creating .env from template..."
    cp .env.example .env
    echo ""
    echo "!!! IMPORTANT: Edit .env with real credentials before starting !!!"
    echo "    nano $DEPLOY_DIR/.env"
    echo ""
fi

# -------------------------------------------------------------------
# 5. Build and start
# -------------------------------------------------------------------
log "Building Docker images..."
docker compose build

log "Starting services..."
docker compose up -d healthcheck

echo ""
log "Deploy complete!"
echo ""
echo "Commands:"
echo "  docker compose up -d           # start all services"
echo "  docker compose logs -f sender  # follow sender logs"
echo "  docker compose ps              # check status"
echo "  curl localhost:8080/health      # health check"
echo ""
echo "Pipeline (run in order):"
echo "  docker compose run parser      # parse Avito sellers"
echo "  docker compose run scorer      # score sellers"
echo "  docker compose run sender      # send outreach messages"
