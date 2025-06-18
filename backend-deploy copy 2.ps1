#!/usr/bin/env pwsh
<#
.SYNOPSIS
    BEL Backend Docker Deployment Script
.DESCRIPTION
    This script builds a Docker image from the specified Dockerfile, saves it as a tar file,
    transfers it to a remote server, and deploys it as a Docker container. It also sets up
    automatic restart policies and manages versioning.
.NOTES
    Author: Claude
    Version: 1.0
#>

# Configuration parameters
param(
    [string]$RemoteUser = "smc",
    [string]$RemoteHost = "172.18.7.155",
    [string]$RemotePath = "/home/smc/bel",
    [string]$DockerfilePath = "./Dockerfile",
    [string]$BackupDir = "/home/smc/bel/backups",
    [string]$ImageName = "bel-fastapi-app",
    [string]$ContainerName = "bel-fastapi",
    [string]$Port = "8002",
    [string]$Version = "1.0.0",
    [switch]$SkipBuild = $false,
    [switch]$SkipTransfer = $false,
    [switch]$SkipDeploy = $false,
    [string]$Password = $null,
    [switch]$UseTimestampVersion = $true
)

# Stop on any error
$ErrorActionPreference = "Stop"

# Function to display colorful messages
function Write-ColorOutput {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,
        
        [Parameter(Mandatory = $false)]
        [string]$ForegroundColor = "White"
    )
    
    Write-Host $Message -ForegroundColor $ForegroundColor
}

# Function to check if a command exists
function Test-CommandExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command
    )
    
    $exists = $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
    return $exists
}

# Function to handle errors
function Handle-Error {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ErrorMessage,
        
        [Parameter(Mandatory = $false)]
        [string]$ExitCode = "1"
    )
    
    Write-ColorOutput "ERROR: $ErrorMessage" "Red"
    exit $ExitCode
}

# Function to get current timestamp for versioning
function Get-TimestampVersion {
    return Get-Date -Format "yyyyMMdd-HHmmss"
}

# Display banner
Write-ColorOutput "============================================" "Cyan"
Write-ColorOutput "   BEL Backend Docker Deployment Script    " "Cyan"
Write-ColorOutput "============================================" "Cyan"
Write-Host

# Generate full version string
$FullVersion = $Version
if ($UseTimestampVersion) {
    $Timestamp = Get-TimestampVersion
    $FullVersion = "$Version-$Timestamp"
}

$FullImageName = "$ImageName-v$FullVersion"
$FullContainerName = "$ContainerName-v$FullVersion"
$ImageTarName = "$FullImageName.tar"

Write-ColorOutput "Using version: $FullVersion" "Yellow"
Write-ColorOutput "Image name: $FullImageName" "Yellow"
Write-ColorOutput "Container name: $FullContainerName" "Yellow"

# Check prerequisites
Write-ColorOutput "Checking prerequisites..." "Yellow"

# Check if Docker is installed
if (-not (Test-CommandExists "docker")) {
    Handle-Error "Docker is not installed. Please install Docker."
}

# Check if SSH and SCP are installed
if (-not (Test-CommandExists "ssh")) {
    Handle-Error "SSH is not installed. Please install OpenSSH."
}

if (-not (Test-CommandExists "scp")) {
    Handle-Error "SCP is not installed. Please install OpenSSH."
}

# Check if Dockerfile exists
if (-not (Test-Path $DockerfilePath)) {
    Handle-Error "Dockerfile not found at path: $DockerfilePath"
}

# Create credential if password is provided
if ($Password) {
    $SecurePassword = ConvertTo-SecureString $Password -AsPlainText -Force
    $Credential = New-Object System.Management.Automation.PSCredential ($RemoteUser, $SecurePassword)
}

# Step 1: Build the Docker image
if (-not $SkipBuild) {
    Write-ColorOutput "Step 1: Building Docker image..." "Green"
    
    # Build the Docker image
    Write-ColorOutput "Building Docker image: $FullImageName" "Yellow"
    docker build -t $FullImageName -f $DockerfilePath .
    
    if ($LASTEXITCODE -ne 0) {
        Handle-Error "Failed to build Docker image."
    }
    
    # Save the Docker image as a tar file
    Write-ColorOutput "Saving Docker image as tar file: $ImageTarName" "Yellow"
    docker save -o $ImageTarName $FullImageName
    
    if ($LASTEXITCODE -ne 0) {
        Handle-Error "Failed to save Docker image as tar file."
    }
    
    Write-ColorOutput "Docker image built and saved successfully!" "Green"
}
else {
    Write-ColorOutput "Skipping build step..." "Yellow"
    
    # Check if image tar file exists
    if (-not (Test-Path $ImageTarName)) {
        Handle-Error "Image tar file not found: $ImageTarName. Please build the image first or remove the -SkipBuild flag."
    }
}

# Step 2: Create or update the server setup script
Write-ColorOutput "Step 2: Preparing server setup script..." "Green"

$serverSetupContent = @'
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
IMAGE_NAME=${IMAGE_NAME:-"@@IMAGE_NAME@@"}
CONTAINER_NAME=${CONTAINER_NAME:-"@@CONTAINER_NAME@@"}
PORT=${PORT:-"@@PORT@@"}
BACKUP_DIR=${BACKUP_DIR:-"@@BACKUP_DIR@@"}
IMAGE_TAR=${IMAGE_TAR:-"@@IMAGE_TAR@@"}

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

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

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

# Step 2: Backup existing Docker image tar files
print_status "Backing up any existing Docker image tar files..."
for old_tar in $(find . -name "${IMAGE_NAME%-v*}*.tar" -not -name "$IMAGE_TAR"); do
    if [ -f "$old_tar" ]; then
        tar_basename=$(basename "$old_tar")
        print_status "Moving $tar_basename to backup directory..."
        mv "$old_tar" "$BACKUP_DIR/" || print_warning "Failed to move $old_tar to backup directory"
    fi
done

# Step 3: Load the Docker image
print_status "Loading Docker image from $IMAGE_TAR..."
if [ ! -f "$IMAGE_TAR" ]; then
    print_error "Docker image file not found: $IMAGE_TAR"
fi

docker load -i "$IMAGE_TAR"
if [ $? -ne 0 ]; then
    print_error "Failed to load Docker image from $IMAGE_TAR"
fi
print_success "Docker image loaded successfully: $IMAGE_NAME"

# Step 4: Run the Docker container
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

# Step 5: Clean up old images
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

# Step 6: Setup systemd service for automatic startup (if systemd is available)
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

# Print deployment information
print_success "Backend container deployed successfully!"
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}   BEL Backend Deployment Summary          ${NC}"
echo -e "${GREEN}============================================${NC}"
echo -e "Container name: $CONTAINER_NAME"
echo -e "Image name: $IMAGE_NAME"
echo -e "Port: $PORT"
echo -e "Container status: $(docker ps --filter "name=$CONTAINER_NAME" --format "{{.Status}}")"
echo -e "Logs: Run 'docker logs $CONTAINER_NAME' to view logs"
echo ""
echo -e "${GREEN}============================================${NC}"

exit 0
'@

# Replace placeholders in the script
$serverSetupContent = $serverSetupContent.Replace("@@IMAGE_NAME@@", $FullImageName)
$serverSetupContent = $serverSetupContent.Replace("@@CONTAINER_NAME@@", $FullContainerName)
$serverSetupContent = $serverSetupContent.Replace("@@PORT@@", $Port)
$serverSetupContent = $serverSetupContent.Replace("@@BACKUP_DIR@@", $BackupDir)
$serverSetupContent = $serverSetupContent.Replace("@@IMAGE_TAR@@", $ImageTarName)

# Ensure the server setup script has Unix line endings (LF only)
$serverSetupContent = $serverSetupContent -replace "`r`n", "`n"

# Save the server setup script with UTF-8 encoding without BOM
$ServerSetupScript = "server-docker-setup.sh"
$utf8NoBomEncoding = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($ServerSetupScript, $serverSetupContent, $utf8NoBomEncoding)

Write-ColorOutput "Server setup script created with Unix line endings: $ServerSetupScript" "Green"

# Step 3: Transfer files to the server
if (-not $SkipTransfer) {
    Write-ColorOutput "Step 3: Transferring files to the server..." "Green"
    
    # Create remote directory if it doesn't exist
    Write-ColorOutput "Creating remote directory..." "Yellow"
    
    $sshCommand = "mkdir -p $RemotePath"
    
    if ($Password) {
        # Using password authentication
        $sshProcess = Start-Process -FilePath "ssh" -ArgumentList "${RemoteUser}@${RemoteHost}", $sshCommand -NoNewWindow -Wait -PassThru
    }
    else {
        # Using key-based authentication
        ssh "${RemoteUser}@${RemoteHost}" $sshCommand
    }
    
    if ($LASTEXITCODE -ne 0) {
        Handle-Error "Failed to create remote directory."
    }
    
    # Transfer Docker image tar file
    Write-ColorOutput "Transferring Docker image tar file ($ImageTarName)..." "Yellow"
    Write-ColorOutput "This may take some time depending on the image size..." "Yellow"
    
    if ($Password) {
        # Using password authentication
        $scpProcess = Start-Process -FilePath "scp" -ArgumentList $ImageTarName, "${RemoteUser}@${RemoteHost}:${RemotePath}/$ImageTarName" -NoNewWindow -Wait -PassThru
    }
    else {
        # Using key-based authentication
        scp $ImageTarName "${RemoteUser}@${RemoteHost}:${RemotePath}/$ImageTarName"
    }
    
    if ($LASTEXITCODE -ne 0) {
        Handle-Error "Failed to transfer Docker image tar file."
    }
    
    # Transfer server setup script
    Write-ColorOutput "Transferring server setup script..." "Yellow"
    
    if ($Password) {
        # Using password authentication
        $scpProcess = Start-Process -FilePath "scp" -ArgumentList $ServerSetupScript, "${RemoteUser}@${RemoteHost}:${RemotePath}/$ServerSetupScript" -NoNewWindow -Wait -PassThru
    }
    else {
        # Using key-based authentication
        scp $ServerSetupScript "${RemoteUser}@${RemoteHost}:${RemotePath}/$ServerSetupScript"
    }
    
    if ($LASTEXITCODE -ne 0) {
        Handle-Error "Failed to transfer server setup script."
    }
    
    Write-ColorOutput "Files transferred successfully!" "Green"
}
else {
    Write-ColorOutput "Skipping file transfer step..." "Yellow"
}

# Step 4: Execute server setup script
if (-not $SkipDeploy) {
    Write-ColorOutput "Step 4: Executing server setup script..." "Green"
    
    # First, make the script executable on the server
    Write-ColorOutput "Making script executable..." "Yellow"
    $chmodCommand = "chmod +x ${RemotePath}/${ServerSetupScript}"
    
    if ($Password) {
        # Using password authentication
        $sshProcess = Start-Process -FilePath "ssh" -ArgumentList "${RemoteUser}@${RemoteHost}", $chmodCommand -NoNewWindow -Wait -PassThru
    }
    else {
        # Using key-based authentication
        ssh "${RemoteUser}@${RemoteHost}" $chmodCommand
    }
    
    if ($LASTEXITCODE -ne 0) {
        Handle-Error "Failed to make script executable."
    }
    
    # Now run the script with sudo, using bash explicitly
    Write-ColorOutput "Running server setup script with sudo (password will be required)..." "Yellow"
    $deployCommand = "cd $RemotePath && sudo -S bash ./$ServerSetupScript"
    
    Write-ColorOutput "Command: $deployCommand" "Yellow"
    
    if ($Password) {
        # Using password authentication
        $sshProcess = Start-Process -FilePath "ssh" -ArgumentList "${RemoteUser}@${RemoteHost}", $deployCommand -NoNewWindow -Wait -PassThru
    }
    else {
        # Using key-based authentication
        Write-ColorOutput "Using key-based authentication. You will be prompted for your sudo password:" "Yellow"
        ssh "${RemoteUser}@${RemoteHost}" $deployCommand
    }
    
    if ($LASTEXITCODE -ne 0) {
        Handle-Error "Failed to execute server setup script."
    }
    
    Write-ColorOutput "Server setup script executed successfully!" "Green"
}
else {
    Write-ColorOutput "Skipping server deployment step..." "Yellow"
}

# Clean up local files
Write-ColorOutput "Cleaning up local files..." "Yellow"
if (Test-Path $ImageTarName) {
    Remove-Item -Path $ImageTarName -Force
    Write-ColorOutput "Removed local image tar file: $ImageTarName" "Yellow"
}

# Deployment summary
Write-ColorOutput "============================================" "Cyan"
Write-ColorOutput "   BEL Backend Deployment Complete         " "Cyan"
Write-ColorOutput "============================================" "Cyan"
Write-Host
Write-ColorOutput "Your Docker container has been deployed:" "Green"
Write-ColorOutput "Container: $FullContainerName" "Green"
Write-ColorOutput "Image: $FullImageName" "Green"
Write-ColorOutput "Port: $Port" "Green"
Write-ColorOutput ""
Write-ColorOutput "To customize the deployment, you can use these parameters:" "Yellow"
Write-ColorOutput "-RemoteUser          : SSH username (default: $RemoteUser)" "Yellow"
Write-ColorOutput "-RemoteHost          : Server hostname or IP (default: $RemoteHost)" "Yellow"
Write-ColorOutput "-RemotePath          : Remote path for deployment files (default: $RemotePath)" "Yellow"
Write-ColorOutput "-DockerfilePath      : Path to Dockerfile (default: $DockerfilePath)" "Yellow"
Write-ColorOutput "-BackupDir           : Directory for backups (default: $BackupDir)" "Yellow"
Write-ColorOutput "-ImageName           : Base name for Docker image (default: $ImageName)" "Yellow"
Write-ColorOutput "-ContainerName       : Base name for Docker container (default: $ContainerName)" "Yellow"
Write-ColorOutput "-Port                : Port to expose (default: $Port)" "Yellow"
Write-ColorOutput "-Version             : Version number (default: $Version)" "Yellow"
Write-ColorOutput "-UseTimestampVersion : Add timestamp to version (default: $UseTimestampVersion)" "Yellow"
Write-ColorOutput "-SkipBuild           : Skip the build step (default: $SkipBuild)" "Yellow"
Write-ColorOutput "-SkipTransfer        : Skip the file transfer step (default: $SkipTransfer)" "Yellow"
Write-ColorOutput "-SkipDeploy          : Skip the server deployment step (default: $SkipDeploy)" "Yellow"
Write-ColorOutput "-Password            : SSH password (optional, use SSH keys instead if possible)" "Yellow"
Write-ColorOutput ""
Write-ColorOutput "Example:" "Yellow"
Write-ColorOutput "./deploy-backend.ps1 -RemoteHost 'example.com' -Port '8080' -Version '2.0.0'" "Yellow"
Write-ColorOutput ""