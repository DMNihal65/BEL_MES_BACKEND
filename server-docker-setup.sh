#!/bin/bash

# BEL Backend Docker Deployment Script
set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Dynamically assign variables
if [ -z "$IMAGE_TAR" ]; then
    LATEST_TAR=$(ls bel-fastapi-app-*.tar 2>/dev/null | sort | tail -n 1)
    if [ -z "$LATEST_TAR" ]; then
        echo -e "${RED}[ERROR] No Docker image tar file found.${NC}"
        exit 1
    fi
    IMAGE_TAR="$LATEST_TAR"
fi

BASENAME=$(basename "$IMAGE_TAR" .tar)
IMAGE_NAME=${IMAGE_NAME:-$BASENAME}
CONTAINER_NAME=${CONTAINER_NAME:-"${BASENAME/app/container}"}
PORT=${PORT:-8002}

BACKUP_DIR="/home/smc/bel/backups"
CURRENT_DIR="/home/smc/bel/current"

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}   BEL Backend Docker Deployment Script     ${NC}"
echo -e "${BLUE}============================================${NC}"

print_status() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }

# Ensure root
if [ "$EUID" -ne 0 ]; then
    print_error "Please run this script with sudo or as root"
fi

# Check Docker
if ! command -v docker &>/dev/null; then
    print_error "Docker is not installed. Please install Docker."
fi

mkdir -p "$BACKUP_DIR" "$CURRENT_DIR"

# Stop any container using the same port
used_container=$(docker ps --format '{{.ID}} {{.Ports}}' | grep "0.0.0.0:$PORT->" | awk '{print $1}')
if [ ! -z "$used_container" ]; then
    print_status "Stopping container using port $PORT..."
    docker stop "$used_container"
    docker rm "$used_container"
fi

# Backup current
print_status "Backing up current directory..."
if [ "$(ls -A $CURRENT_DIR 2>/dev/null)" ]; then
    timestamp=$(date +"%Y%m%d-%H%M%S")
    mkdir -p "$BACKUP_DIR/$timestamp"
    mv "$CURRENT_DIR"/* "$BACKUP_DIR/$timestamp"/ || print_warning "Nothing to backup"
    print_success "Backed up to: $BACKUP_DIR/$timestamp"
fi

# Move new tar
print_status "Moving new tar file to current directory..."
mv "$IMAGE_TAR" "$CURRENT_DIR/"
CURRENT_IMAGE_TAR="$CURRENT_DIR/$(basename $IMAGE_TAR)"

# Load image
print_status "Loading Docker image from $CURRENT_IMAGE_TAR..."
docker load -i "$CURRENT_IMAGE_TAR" || print_error "Failed to load image"
print_success "Docker image loaded: $IMAGE_NAME"

# Run container
print_status "Starting new container: $CONTAINER_NAME"
docker run -d \
    --name "$CONTAINER_NAME" \
    -p "$PORT:$PORT" \
    --restart always \
    "$IMAGE_NAME" || print_error "Failed to start container"

# Confirm run
print_status "Verifying container status..."
docker ps | grep "$CONTAINER_NAME" && print_success "Container running!" || print_error "Container failed"

# Create version log
echo "$IMAGE_NAME" > "$CURRENT_DIR/current_version.txt"
echo "$CONTAINER_NAME" >> "$CURRENT_DIR/current_version.txt"
echo "Deployed on: $(date)" >> "$CURRENT_DIR/current_version.txt"

# Optional: Setup systemd
if command -v systemctl &>/dev/null; then
    SERVICE_NAME="${CONTAINER_NAME}.service"
    SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"
    cat > "$SERVICE_FILE" << EOL
[Unit]
Description=BEL Backend Docker Container
After=docker.service
Requires=docker.service

[Service]
Type=simple
Restart=always
RestartSec=10
ExecStart=/usr/bin/docker start -a $CONTAINER_NAME
ExecStop=/usr/bin/docker stop $CONTAINER_NAME

[Install]
WantedBy=multi-user.target
EOL

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    print_success "Systemd service created: $SERVICE_NAME"
fi

# Cleanup old images
print_status "Cleaning up old images..."
docker images | grep "${IMAGE_NAME%-v*}" | awk '{print $1":"$2}' | sort -r | tail -n +4 | while read img; do
    docker rmi "$img" || print_warning "Failed to remove $img"
done

# Final summary
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}   BEL Backend Deployment Complete          ${NC}"
echo -e "${GREEN}============================================${NC}"
echo -e "Container: $CONTAINER_NAME"
echo -e "Image: $IMAGE_NAME"
echo -e "Port: $PORT"
echo -e "Deployment: $CURRENT_DIR"
docker ps --filter "name=$CONTAINER_NAME"
