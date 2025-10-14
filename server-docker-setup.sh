#!/bin/bash

# BEL Backend Docker Deployment Script
# This script loads a Docker image and runs it as a container

# Exit on any error
set -e

# Color codes for better readability
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration (can be overridden with environment variables)
IMAGE_NAME=${IMAGE_NAME:-"bel-fastapi-app-v13.1.2-20251007-171123"}
CONTAINER_NAME=${CONTAINER_NAME:-"bel-fastapi-v13.1.2-20251007-171123"}
PORT=${PORT:-"8002"}
BACKUP_DIR=${BACKUP_DIR:-"/home/smc/bel/backups"}
CURRENT_DIR=${CURRENT_DIR:-"/home/smc/bel/current"}
IMAGE_TAR=${IMAGE_TAR:-"bel-fastapi-app-v13.1.2-20251007-171123.tar"}

# Print banner
echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}   BEL Backend Docker Deployment Script     ${NC}"
echo -e "${BLUE}============================================${NC}"

# Function to print status messages
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

# Function to print success messages
print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

# Function to print error messages
print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

# Function to print warning messages
print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check if running as root or with sudo
if [ "$EUID" -ne 0 ]; then
    print_error "Please run this script with sudo or as root"
    exit 1
fi

# Check if Docker is installed
if ! command_exists docker; then
    print_error "Docker is not installed. Please install Docker first."
fi

# Create backup and current directories if they don't exist
mkdir -p "$BACKUP_DIR"
mkdir -p "$CURRENT_DIR"

# Step 1: Check if an existing container is running and stop it
print_status "Checking for existing containers..."
if docker ps -a --format '{{.Names}}' | grep -q "$CONTAINER_NAME"; then
    print_status "Found existing container $CONTAINER_NAME. Stopping and removing..."
    docker stop "$CONTAINER_NAME" || print_warning "Failed to stop container, it may not be running"
    docker rm "$CONTAINER_NAME" || print_warning "Failed to remove container, continuing anyway"
fi

# Look for old containers with similar names and stop them too
for old_container in $(docker ps -a --format '{{.Names}}' | grep "${CONTAINER_NAME%-v*}"); do
    if [ "$old_container" != "$CONTAINER_NAME" ]; then
        print_status "Found old container $old_container. Stopping and removing..."
        docker stop "$old_container" || print_warning "Failed to stop container, it may not be running"
        docker rm "$old_container" || print_warning "Failed to remove container, continuing anyway"
    fi
done

# Step 2: Backup existing files from current directory
print_status "Backing up files from current directory..."
if [ -d "$CURRENT_DIR" ] && [ "$(ls -A $CURRENT_DIR 2>/dev/null)" ]; then
    timestamp=$(date +"%Y%m%d-%H%M%S")
    print_status "Moving current deployment files to backup directory..."

    # Create a subdirectory in the backup folder with timestamp
    backup_subdir="$BACKUP_DIR/$timestamp"
    mkdir -p "$backup_subdir"

    # Move all content from current to backup
    mv $CURRENT_DIR/* $backup_subdir/ 2>/dev/null || print_warning "No files to backup from current directory"
    print_success "Previous deployment backed up to: $backup_subdir"
fi

# Step 3: Move new tar file to current directory
print_status "Moving new tar file to current directory..."
if [ -f "$IMAGE_TAR" ]; then
    mv "$IMAGE_TAR" "$CURRENT_DIR/"
    print_success "New tar file moved to current directory"
else
    print_error "Docker image file not found: $IMAGE_TAR"
fi

# Full path to the tar file in current directory
CURRENT_IMAGE_TAR="$CURRENT_DIR/$(basename $IMAGE_TAR)"

# Step 4: Executing server setup script with environment variables
Write-Host "Step 4: Executing server setup script..."
$remoteCommand = @"
export IMAGE_NAME='$ImageName'
export CONTAINER_NAME='$ContainerName'
export IMAGE_TAR='$TarFile'
export PORT=$Port
cd /home/smc/bel && sudo -E bash ./server-docker-setup.sh
"@

sshpass -p $Password ssh $RemoteUser@$RemoteHost "$remoteCommand"

# Step 5: Run the Docker container
print_status "Starting new container: $CONTAINER_NAME"
docker run -d \
    --name "$CONTAINER_NAME" \
    -p "$PORT:$PORT" \
    --restart always \
    "$IMAGE_NAME"

if [ $? -ne 0 ]; then
    print_error "Failed to start Docker container"
fi

print_status "Verifying container is running..."
if docker ps | grep -q "$CONTAINER_NAME"; then
    print_success "Container $CONTAINER_NAME is running successfully!"
else
    print_error "Container $CONTAINER_NAME failed to start"
fi

# Step 6: Clean up old images
print_status "Cleaning up old images..."
# Keep only the 3 most recent versions of our images
image_count=$(docker images | grep "${IMAGE_NAME%-v*}" | wc -l)
if [ "$image_count" -gt 3 ]; then
    print_status "Removing old images to save space..."
    # Sort by creation date, keep the newest 3
    for old_image in $(docker images --format '{{.Repository}}:{{.Tag}} {{.CreatedAt}}' | grep "${IMAGE_NAME%-v*}" | sort -k2 -r | tail -n +4 | awk '{print $1}'); do
        print_status "Removing old image: $old_image"
        docker rmi "$old_image" || print_warning "Failed to remove image $old_image, continuing anyway"
    done
fi

# Step 7: Setup systemd service for automatic startup (if systemd is available)
if command_exists systemctl; then
    print_status "Setting up systemd service for automatic startup..."

    SERVICE_NAME="${CONTAINER_NAME}.service"
    SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"

    # Create systemd service file
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

    # Reload systemd, enable and start the service
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    print_success "Systemd service created and enabled: $SERVICE_NAME"
fi

# Create a symbolic link to the latest deployment
print_status "Creating symbolic link to latest deployment..."
echo "$IMAGE_NAME" > "$CURRENT_DIR/current_version.txt"
echo "$CONTAINER_NAME" >> "$CURRENT_DIR/current_version.txt"
echo "Deployed on: $(date)" >> "$CURRENT_DIR/current_version.txt"

# Print deployment information
print_success "Backend container deployed successfully!"
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}   BEL Backend Deployment Summary          ${NC}"
echo -e "${GREEN}============================================${NC}"
echo -e "Container name: $CONTAINER_NAME"
echo -e "Image name: $IMAGE_NAME"
echo -e "Port: $PORT"
echo -e "Current deployment: $CURRENT_DIR"
echo -e "Container status: $(docker ps --filter "name=$CONTAINER_NAME" --format "{{.Status}}")"
echo -e "Logs: Run 'docker logs $CONTAINER_NAME' to view logs"
echo ""
echo -e "${GREEN}============================================${NC}"

exit 0