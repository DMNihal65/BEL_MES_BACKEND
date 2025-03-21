.ps1
~~~~~~~~~~~~~~~


#!/usr/bin/env pwsh
<#
.SYNOPSIS
    BELMES Frontend Deployment Script
.DESCRIPTION
    This script builds the React application locally and deploys it to the remote server.
    It handles building with the correct base path, transferring files, and executing the server deployment script.
.NOTES
    Author: Claude
    Version: 1.2
#>

# Configuration parameters
param(
    [string]$RemoteUser = "smc",
    [string]$RemoteHost = "172.18.7.155",
    [string]$RemotePath = "/home/smc/belmes",
    [string]$AppBasePath = "/belmes/",
    [string]$BuildDir = "dist",
    [string]$ServerSetupScript = "server-setup.sh",
    [switch]$SkipBuild = $false,
    [switch]$SkipTransfer = $false,
    [switch]$SkipDeploy = $false,
    [string]$Password = $null
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

# Display banner
Write-ColorOutput "============================================" "Cyan"
Write-ColorOutput "   BELMES Frontend Deployment Script        " "Cyan"
Write-ColorOutput "============================================" "Cyan"
Write-Host

# Check prerequisites
Write-ColorOutput "Checking prerequisites..." "Yellow"

# Check if Node.js is installed
if (-not (Test-CommandExists "node")) {
    Handle-Error "Node.js is not installed. Please install Node.js and npm."
}

# Check if npm is installed
if (-not (Test-CommandExists "npm")) {
    Handle-Error "npm is not installed. Please install npm."
}

# Check if SSH and SCP are installed
if (-not (Test-CommandExists "ssh")) {
    Handle-Error "SSH is not installed. Please install OpenSSH."
}

if (-not (Test-CommandExists "scp")) {
    Handle-Error "SCP is not installed. Please install OpenSSH."
}

# Check if the project directory contains package.json
if (-not (Test-Path "package.json")) {
    Handle-Error "package.json not found. Please run this script from the project root directory."
}

# Create credential if password is provided
if ($Password) {
    $SecurePassword = ConvertTo-SecureString $Password -AsPlainText -Force
    $Credential = New-Object System.Management.Automation.PSCredential ($RemoteUser, $SecurePassword)
}

# Step 1: Build the application
if (-not $SkipBuild) {
    Write-ColorOutput "Step 1: Building the application..." "Green"
    
    # Update vite.config.js to set the correct base path
    Write-ColorOutput "Updating base path in vite.config.js..." "Yellow"
    
    $viteConfigPath = "vite.config.js"
    $viteConfig = Get-Content $viteConfigPath -Raw
    
    # Check if base path is commented out
    if ($viteConfig -match "//\s*base:") {
        $viteConfig = $viteConfig -replace "//\s*base:\s*'[^']*'", "base: '$AppBasePath'"
    }
    # Check if base path exists but with different value
    elseif ($viteConfig -match "base:\s*'[^']*'") {
        $viteConfig = $viteConfig -replace "base:\s*'[^']*'", "base: '$AppBasePath'"
    }
    # Add base path if it doesn't exist
    else {
        $viteConfig = $viteConfig -replace "plugins:\s*\[react\(\)\]", "base: '$AppBasePath',`n  plugins: [react()]"
    }
    
    # Save the updated config
    $viteConfig | Set-Content $viteConfigPath
    
    # Install dependencies
    Write-ColorOutput "Installing dependencies..." "Yellow"
    npm install --force
    
    if ($LASTEXITCODE -ne 0) {
        Handle-Error "Failed to install dependencies."
    }
    
    # Build the application
    Write-ColorOutput "Building the application..." "Yellow"
    npm run build
    
    if ($LASTEXITCODE -ne 0) {
        Handle-Error "Failed to build the application."
    }
    
    # Check if build directory exists
    if (-not (Test-Path $BuildDir)) {
        Handle-Error "Build directory '$BuildDir' not found after build."
    }
    
    Write-ColorOutput "Application built successfully!" "Green"
}
else {
    Write-ColorOutput "Skipping build step..." "Yellow"
    
    # Check if build directory exists
    if (-not (Test-Path $BuildDir)) {
        Handle-Error "Build directory '$BuildDir' not found. Please build the application first or remove the -SkipBuild flag."
    }
}

# Step 2: Create or update the server setup script
Write-ColorOutput "Step 2: Preparing server setup script..." "Green"

$serverSetupContent = @'
#!/bin/bash

# BELMES Frontend Deployment Script
# This script configures Nginx and deploys the BELMES frontend application

# Exit on any error
set -e

# Color codes for better readability
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration (can be overridden with environment variables)
APP_DIR=${BELMES_APP_DIR:-"/var/www/html/belmes"}
NGINX_SITES_AVAILABLE=${NGINX_SITES_AVAILABLE:-"/etc/nginx/sites-available"}
NGINX_SITES_ENABLED=${NGINX_SITES_ENABLED:-"/etc/nginx/sites-enabled"}
SERVER_NAME=${SERVER_NAME:-"172.18.7.155"}
APP_PATH=${APP_PATH:-"/belmes"}
BACKUP_DIR=${BACKUP_DIR:-"/var/backups/belmes"}
DIST_SOURCE=${DIST_SOURCE:-"./dist"}
NGINX_CONFIG_NAME=${NGINX_CONFIG_NAME:-"belmes.conf"}
NGINX_USER=${NGINX_USER:-"www-data"}
NGINX_GROUP=${NGINX_GROUP:-"www-data"}
BASE_HREF=${BASE_HREF:-"/belmes/"}

# Print banner
echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}   BELMES Frontend Deployment Script       ${NC}"
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
}

# Function to print warning messages
print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check if a directory exists and is writable
check_dir() {
    if [ ! -d "$1" ]; then
        print_error "Directory $1 does not exist"
        return 1
    fi
    if [ ! -w "$1" ]; then
        print_error "Directory $1 is not writable"
        return 1
    fi
    return 0
}

# Function to create a backup of existing deployment
create_backup() {
    if [ -d "$APP_DIR" ]; then
        print_status "Creating backup of existing deployment..."
        BACKUP_NAME="belmes-$(date +%Y%m%d-%H%M%S)"
        mkdir -p "$BACKUP_DIR"
        if tar -czf "$BACKUP_DIR/$BACKUP_NAME.tar.gz" -C "$(dirname "$APP_DIR")" "$(basename "$APP_DIR")"; then
            print_success "Backup created at $BACKUP_DIR/$BACKUP_NAME.tar.gz"
        else
            print_warning "Failed to create backup, continuing anyway..."
        fi
    else
        print_status "No existing deployment found, skipping backup..."
    fi
}

# Function to restore from backup in case of failure
restore_from_backup() {
    if [ -n "$1" ] && [ -f "$1" ]; then
        print_status "Restoring from backup $1..."
        if tar -xzf "$1" -C "$(dirname "$APP_DIR")"; then
            print_success "Restored from backup $1"
        else
            print_error "Failed to restore from backup $1"
        fi
    else
        print_error "No backup file specified or file does not exist"
    fi
}

# Function to clean up in case of failure
cleanup() {
    print_status "Cleaning up..."
    # Add any cleanup tasks here
}

# Function to fix asset paths in index.html if needed
fix_asset_paths() {
    print_status "Checking index.html..."
    
    # Check if index.html exists
    if [ ! -f "$APP_DIR/index.html" ]; then
        print_error "index.html not found in $APP_DIR"
        return 1
    fi
    
    # Create a backup of the original index.html
    cp "$APP_DIR/index.html" "$APP_DIR/index.html.bak"
    print_success "index.html backup created"
}

# Set trap for cleanup on error
trap 'print_error "An error occurred. Exiting..."; cleanup; exit 1' ERR

# Check if running as root or with sudo
if [ "$EUID" -ne 0 ]; then
    print_error "Please run this script with sudo or as root"
    exit 1
fi

# Check if Nginx is installed
if ! command_exists nginx; then
    print_error "Nginx is not installed. Please install Nginx first."
    exit 1
fi

# Check if the dist source directory exists
if [ ! -d "$DIST_SOURCE" ]; then
    print_error "Dist source directory $DIST_SOURCE does not exist. Please build the application first."
    exit 1
fi

# Step 1: Create necessary directories
print_status "Creating necessary directories..."
mkdir -p "$APP_DIR"
mkdir -p "$BACKUP_DIR"

# Step 2: Create backup of existing deployment
create_backup

# Step 3: Copy dist files to the application directory
print_status "Copying dist files to $APP_DIR..."
if [ -d "$APP_DIR" ] && [ "$(ls -A "$APP_DIR" 2>/dev/null)" ]; then
    rm -rf "$APP_DIR"/*
fi

# Copy all files from dist to APP_DIR
cp -r "$DIST_SOURCE"/* "$APP_DIR/"

# Step 4: Fix asset paths in index.html if needed
fix_asset_paths

# Step 5: Create Nginx configuration
print_status "Setting up Nginx configuration..."

# Backup existing Nginx config if it exists
if [ -f "$NGINX_SITES_AVAILABLE/$NGINX_CONFIG_NAME" ]; then
    cp "$NGINX_SITES_AVAILABLE/$NGINX_CONFIG_NAME" "$NGINX_SITES_AVAILABLE/$NGINX_CONFIG_NAME.bak"
    print_status "Backed up existing Nginx configuration to $NGINX_SITES_AVAILABLE/$NGINX_CONFIG_NAME.bak"
fi

cat > "$NGINX_SITES_AVAILABLE/$NGINX_CONFIG_NAME" << EOF
server {
    listen 80;
    server_name $SERVER_NAME;
    
    # BELMES Application
    location $APP_PATH/ {
        alias $APP_DIR/;
        try_files \$uri \$uri/ $APP_PATH/index.html;
        
        # Include the default MIME types
        include /etc/nginx/mime.types;
        
        # Add additional MIME types
        types {
            application/javascript js mjs;
            text/css css;
            font/woff woff;
            font/woff2 woff2;
            application/vnd.ms-fontobject eot;
            font/ttf ttf;
        }

        # Set default type
        default_type application/octet-stream;
    }

    # Handle JavaScript files with proper MIME types
    location $APP_PATH/assets/js/ {
        alias $APP_DIR/assets/js/;
        add_header Content-Type application/javascript;
        expires 30d;
        add_header Cache-Control "public, max-age=2592000";
    }

    # Handle CSS files with proper MIME types
    location $APP_PATH/assets/css/ {
        alias $APP_DIR/assets/css/;
        add_header Content-Type text/css;
        expires 30d;
        add_header Cache-Control "public, max-age=2592000";
    }

    # Handle font files
    location ~ $APP_PATH/assets/.*\.(woff|woff2|eot|ttf|otf)$ {
        expires 30d;
        add_header Cache-Control "public, max-age=2592000";
    }

    # Handle image files
    location ~ $APP_PATH/assets/.*\.(png|jpg|jpeg|gif|ico|svg)$ {
        expires 30d;
        add_header Cache-Control "public, max-age=2592000";
    }

    # API proxy configuration
    location /api/v5/ {
        proxy_pass http://172.18.7.91:7777/api/v5/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_cache_bypass \$http_upgrade;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # CORS headers for API
        add_header 'Access-Control-Allow-Origin' '*';
        add_header 'Access-Control-Allow-Methods' 'GET, POST, OPTIONS, PUT, DELETE';
        add_header 'Access-Control-Allow-Headers' 'DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range,Authorization';
        add_header 'Access-Control-Expose-Headers' 'Content-Length,Content-Range';
        
        # Handle OPTIONS method for CORS preflight requests
        if (\$request_method = 'OPTIONS') {
            add_header 'Access-Control-Allow-Origin' '*';
            add_header 'Access-Control-Allow-Methods' 'GET, POST, OPTIONS, PUT, DELETE';
            add_header 'Access-Control-Allow-Headers' 'DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range,Authorization';
            add_header 'Access-Control-Max-Age' 1728000;
            add_header 'Content-Type' 'text/plain charset=UTF-8';
            add_header 'Content-Length' 0;
            return 204;
        }
    }

    # API v1 proxy configuration (required for your frontend)
    location /api/v1/ {
        # Enhanced logging for API requests
        access_log /var/log/nginx/api-access.log;
        error_log /var/log/nginx/api-error.log debug;
        
        # Use fixed port without URI rewriting
        proxy_pass http://172.18.7.155:8002;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_cache_bypass \$http_upgrade;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # CORS headers for API
        add_header 'Access-Control-Allow-Origin' '*';
        add_header 'Access-Control-Allow-Methods' 'GET, POST, OPTIONS, PUT, DELETE';
        add_header 'Access-Control-Allow-Headers' 'DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range,Authorization';
        add_header 'Access-Control-Expose-Headers' 'Content-Length,Content-Range';

        # Handle OPTIONS method for CORS preflight requests
        if (\$request_method = 'OPTIONS') {
            add_header 'Access-Control-Allow-Origin' '*';
            add_header 'Access-Control-Allow-Methods' 'GET, POST, OPTIONS, PUT, DELETE';
            add_header 'Access-Control-Allow-Headers' 'DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range,Authorization';
            add_header 'Access-Control-Max-Age' 1728000;
            add_header 'Content-Type' 'text/plain charset=UTF-8';
            add_header 'Content-Length' 0;
            return 204;
        }
    }

    # Redirect root to the application
    location = / {
        return 301 $APP_PATH/;
    }
    
    # Global settings
    # Enable gzip compression
    gzip on;
    gzip_comp_level 5;
    gzip_min_length 256;
    gzip_proxied any;
    gzip_vary on;
    gzip_types
        application/javascript
        application/json
        application/x-javascript
        text/css
        text/javascript
        text/plain
        font/woff
        font/woff2
        application/vnd.ms-fontobject
        font/ttf;
    
    # Security headers
    add_header X-Frame-Options "SAMEORIGIN";
    add_header X-XSS-Protection "1; mode=block";
    add_header X-Content-Type-Options "nosniff";
}
EOF

print_success "Nginx configuration created at $NGINX_SITES_AVAILABLE/$NGINX_CONFIG_NAME"

# Step 6: Enable the site and set permissions
print_status "Enabling site and setting permissions..."

# Check if the sites-enabled directory exists
if [ ! -d "$NGINX_SITES_ENABLED" ]; then
    print_warning "Nginx sites-enabled directory does not exist. Creating it..."
    mkdir -p "$NGINX_SITES_ENABLED"
fi

# Remove existing symlink if it exists
if [ -L "$NGINX_SITES_ENABLED/$NGINX_CONFIG_NAME" ]; then
    rm "$NGINX_SITES_ENABLED/$NGINX_CONFIG_NAME"
fi

# Create symlink
ln -sf "$NGINX_SITES_AVAILABLE/$NGINX_CONFIG_NAME" "$NGINX_SITES_ENABLED/"

# Set permissions
chown -R "$NGINX_USER:$NGINX_GROUP" "$APP_DIR"
chmod -R 755 "$APP_DIR"

# Step 7: Test Nginx configuration and reload
print_status "Testing Nginx configuration..."
if nginx -t; then
    print_success "Nginx configuration test passed"
    print_status "Reloading Nginx..."
    
    # Check which service manager is available
    if command_exists systemctl; then
        systemctl reload nginx
    elif command_exists service; then
        service nginx reload
    else
        nginx -s reload
    fi
    
    print_success "Nginx reloaded successfully"
else
    print_error "Nginx configuration test failed. Please check the configuration."
    # Restore the backup if it exists
    if [ -f "$NGINX_SITES_AVAILABLE/$NGINX_CONFIG_NAME.bak" ]; then
        mv "$NGINX_SITES_AVAILABLE/$NGINX_CONFIG_NAME.bak" "$NGINX_SITES_AVAILABLE/$NGINX_CONFIG_NAME"
        print_status "Restored previous Nginx configuration"
        
        if command_exists systemctl; then
            systemctl reload nginx
        elif command_exists service; then
            service nginx reload
        else
            nginx -s reload
        fi
    fi
    exit 1
fi

# Print deployment information
print_success "Server setup completed successfully!"
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}   BELMES Frontend Deployment Summary      ${NC}"
echo -e "${GREEN}============================================${NC}"
echo -e "Frontend URL: http://$SERVER_NAME$APP_PATH/"
echo -e "Application directory: $APP_DIR"
echo -e "Nginx configuration: $NGINX_SITES_AVAILABLE/$NGINX_CONFIG_NAME"
echo -e "Backup directory: $BACKUP_DIR"
echo ""
echo -e "To customize this deployment, you can set these environment variables:"
echo -e "  BELMES_APP_DIR - Application directory (default: $APP_DIR)"
echo -e "  SERVER_NAME - Server name (default: $SERVER_NAME)"
echo -e "  APP_PATH - Application path (default: $APP_PATH)"
echo -e "  DIST_SOURCE - Source directory for dist files (default: $DIST_SOURCE)"
echo -e "  BASE_HREF - Base href for the application (default: $BASE_HREF)"
echo ""
echo -e "${GREEN}============================================${NC}"

exit 0
'@

# Ensure the server setup script has Unix line endings (LF only)
$serverSetupContent = $serverSetupContent -replace "`r`n", "`n"

# Save the server setup script with UTF-8 encoding without BOM
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
    
    # Transfer build files
    Write-ColorOutput "Transferring build files..." "Yellow"
    
    if ($Password) {
        # Using password authentication
        $scpProcess = Start-Process -FilePath "scp" -ArgumentList "-r", "$BuildDir/*", "${RemoteUser}@${RemoteHost}:${RemotePath}/dist" -NoNewWindow -Wait -PassThru
    }
    else {
        # Using key-based authentication
        scp -r "$BuildDir/*" "${RemoteUser}@${RemoteHost}:${RemotePath}/dist"
    }
    
    if ($LASTEXITCODE -ne 0) {
        Handle-Error "Failed to transfer build files."
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
    
    # Always use bash explicitly for the server script
    Write-ColorOutput "Checking shell environment..." "Yellow"
    # No need to check shell type, just use bash explicitly
    $shellType = "dash" # Assume dash to be safe
    
    # Now run the script with sudo, using bash explicitly
    Write-ColorOutput "Running server setup script with sudo (password will be required)..." "Yellow"
    # Always use bash explicitly to ensure compatibility
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

# Deployment summary
Write-ColorOutput "============================================" "Cyan"
Write-ColorOutput "   BELMES Frontend Deployment Complete     " "Cyan"
Write-ColorOutput "============================================" "Cyan"
Write-Host
Write-ColorOutput "Your application has been deployed to:" "Green"
Write-ColorOutput "http://${RemoteHost}/belmes/" "Green"
Write-ColorOutput ""
Write-ColorOutput "To customize the deployment, you can use these parameters:" "Yellow"
Write-ColorOutput "-RemoteUser        : SSH username (default: $RemoteUser)" "Yellow"
Write-ColorOutput "-RemoteHost        : Server hostname or IP (default: $RemoteHost)" "Yellow"
Write-ColorOutput "-RemotePath        : Remote path for deployment files (default: $RemotePath)" "Yellow"
Write-ColorOutput "-AppBasePath       : Base path for the application (default: $AppBasePath)" "Yellow"
Write-ColorOutput "-BuildDir          : Local build directory (default: $BuildDir)" "Yellow"
Write-ColorOutput "-ServerSetupScript : Server setup script name (default: $ServerSetupScript)" "Yellow"
Write-ColorOutput "-SkipBuild         : Skip the build step (default: $SkipBuild)" "Yellow"
Write-ColorOutput "-SkipTransfer      : Skip the file transfer step (default: $SkipTransfer)" "Yellow"
Write-ColorOutput "-SkipDeploy        : Skip the server deployment step (default: $SkipDeploy)" "Yellow"
Write-ColorOutput "-Password          : SSH password (optional, use SSH keys instead if possible)" "Yellow"
Write-ColorOutput ""
Write-ColorOutput "Example:" "Yellow"
Write-ColorOutput "./deploy-belmes.ps1 -RemoteHost 'example.com' -AppBasePath '/app/'" "Yellow"
Write-ColorOutput ""