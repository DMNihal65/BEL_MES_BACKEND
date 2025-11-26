param (
    [switch]$SkipDockerInstall,
    [switch]$SkipBuild,
    [switch]$MonitoringOnly,
    [switch]$ResetAll
)

$Green = [ConsoleColor]::Green
$Yellow = [ConsoleColor]::Yellow
$Cyan = [ConsoleColor]::Cyan
$Red = [ConsoleColor]::Red

# Global variables
$DockerDesktopUrl = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
$DockerInstallerPath = "$env:TEMP\DockerDesktopInstaller.exe"
$ProjectPath = $PSScriptRoot
$ImageName = "bel-fastapi-app:v1.0.4"
$LogFile = "$ProjectPath\deployment.log"

# Start logging
Start-Transcript -Path $LogFile -Append -Force

function Write-ColorOutput($ForegroundColor) {
    $fc = $host.UI.RawUI.ForegroundColor
    $host.UI.RawUI.ForegroundColor = $ForegroundColor
    if ($args) {
        Write-Output $args
    }
    else {
        $input | Write-Output
    }
    $host.UI.RawUI.ForegroundColor = $fc
}

function Check-Command($cmdname) {
    return [bool](Get-Command -Name $cmdname -ErrorAction SilentlyContinue)
}

function Wait-ForDeployment($name, $namespace = "default", $timeout = 300) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    
    while ($sw.Elapsed.TotalSeconds -lt $timeout) {
        $deployment = kubectl get deployment $name -n $namespace -o json 2>$null | ConvertFrom-Json
        
        if (-not $deployment) {
            Start-Sleep -Seconds 2
            continue
        }
        
        $status = $deployment.status
        $desired = $status.replicas
        $available = $status.availableReplicas
        
        if ($available -eq $desired -and $desired -gt 0) {
            return $true
        }
        
        Write-ColorOutput $Yellow "Waiting for deployment $name... Available: $($available ?? 0)/$desired"
        Start-Sleep -Seconds 5
    }
    
    Write-ColorOutput $Red "Deployment $name did not become ready within timeout period"
    return $false
}

function Wait-ForServicePort($serviceName, $timeout = 60) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $port = $null
    
    while ($sw.Elapsed.TotalSeconds -lt $timeout) {
        $port = kubectl get service $serviceName -o jsonpath="{.spec.ports[0].nodePort}" 2>$null
        
        if ($port) {
            return $port
        }
        
        Write-ColorOutput $Yellow "Waiting for service $serviceName to get a port..."
        Start-Sleep -Seconds 2
    }
    
    Write-ColorOutput $Red "Service $serviceName did not get a port within timeout period"
    return $null
}

function Reset-Deployment {
    Write-ColorOutput $Yellow "Resetting previous deployments..."
    
    # Delete existing deployments and services
    kubectl delete deployment --all 2>$null
    kubectl delete service --all --ignore-not-found=true 2>$null
    kubectl delete service kubernetes --ignore-not-found=true 2>$null
    kubectl delete pod --all --force --grace-period=0 2>$null
    kubectl delete hpa --all 2>$null
    kubectl delete configmap --all 2>$null
    
    Write-ColorOutput $Green "Reset completed"
}

function Install-DockerDesktop {
    if ($SkipDockerInstall) {
        Write-ColorOutput $Yellow "Skipping Docker Desktop installation as requested"
        return
    }
    
    Write-ColorOutput $Cyan "Checking Docker Desktop installation..."
    
    # Check if Docker is already installed and running
    if ((Check-Command "docker") -and (docker info 2>$null)) {
        Write-ColorOutput $Green "Docker Desktop is already installed and running"
        return
    }
    
    # Download Docker Desktop if not already downloaded
    if (-not (Test-Path $DockerInstallerPath)) {
        Write-ColorOutput $Yellow "Downloading Docker Desktop installer..."
        try {
            Invoke-WebRequest -Uri $DockerDesktopUrl -OutFile $DockerInstallerPath
        }
        catch {
            Write-ColorOutput $Red "Failed to download Docker Desktop installer: $_"
            Write-ColorOutput $Yellow "Please download and install Docker Desktop manually from https://www.docker.com/products/docker-desktop/"
            exit 1
        }
    }
    
    # Install Docker Desktop
    Write-ColorOutput $Yellow "Installing Docker Desktop... (This may take a while)"
    try {
        Start-Process -FilePath $DockerInstallerPath -ArgumentList "install --quiet" -Wait
        Write-ColorOutput $Green "Docker Desktop installed. Please restart your computer and then run this script again."
        exit 0
    }
    catch {
        Write-ColorOutput $Red "Failed to install Docker Desktop: $_"
        Write-ColorOutput $Yellow "Please install Docker Desktop manually from https://www.docker.com/products/docker-desktop/"
        exit 1
    }
}

function Enable-Kubernetes {
    # Check if Kubernetes is already enabled in Docker Desktop
    if (kubectl cluster-info 2>$null) {
        Write-ColorOutput $Green "Kubernetes is already enabled in Docker Desktop"
        return
    }
    
    Write-ColorOutput $Yellow "Kubernetes is not enabled in Docker Desktop. Enabling it now..."
    
    # Create Docker Desktop settings file to enable Kubernetes
    $settingsPath = "$env:USERPROFILE\AppData\Roaming\Docker\settings.json"
    
    if (Test-Path $settingsPath) {
        try {
            $settings = Get-Content $settingsPath | ConvertFrom-Json
            $settings.kubernetes.enabled = $true
            $settings | ConvertTo-Json -Depth 10 | Set-Content $settingsPath
        }
        catch {
            Write-ColorOutput $Red "Failed to update Docker Desktop settings: $_"
            Write-ColorOutput $Yellow "Please enable Kubernetes manually in Docker Desktop settings and run this script again."
            exit 1
        }
    }
    else {
        Write-ColorOutput $Red "Docker Desktop settings file not found. Please enable Kubernetes manually in Docker Desktop settings and run this script again."
        Write-ColorOutput $Yellow "1. Open Docker Desktop"
        Write-ColorOutput $Yellow "2. Go to Settings > Kubernetes"
        Write-ColorOutput $Yellow "3. Check 'Enable Kubernetes'"
        Write-ColorOutput $Yellow "4. Click 'Apply & Restart'"
        Write-ColorOutput $Yellow "5. Wait for Kubernetes to start"
        Write-ColorOutput $Yellow "6. Run this script again"
        exit 1
    }
    
    # Restart Docker to apply settings
    Write-ColorOutput $Yellow "Restarting Docker Desktop to apply Kubernetes settings..."
    
    try {
        $dockerProcess = Get-Process "Docker Desktop" -ErrorAction SilentlyContinue
        if ($dockerProcess) {
            $dockerProcess | Stop-Process -Force
            Start-Sleep -Seconds 5
        }
        
        Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
        
        # Wait for Docker to start
        $maxWait = 180
        $waited = 0
        $interval = 5
        
        Write-ColorOutput $Yellow "Waiting for Docker Desktop to restart (this may take a few minutes)..."
        
        while ($waited -lt $maxWait) {
            if (docker info 2>$null) {
                break
            }
            
            Write-ColorOutput $Yellow "Still waiting for Docker Desktop to restart... ($waited/$maxWait seconds)"
            Start-Sleep -Seconds $interval
            $waited += $interval
        }
        
        if ($waited -ge $maxWait) {
            Write-ColorOutput $Red "Docker Desktop did not restart within the timeout period."
            Write-ColorOutput $Yellow "Please restart Docker Desktop manually, make sure Kubernetes is enabled, and run this script again."
            exit 1
        }
        
        # Wait for Kubernetes to start
        $maxWait = 300
        $waited = 0
        
        Write-ColorOutput $Yellow "Waiting for Kubernetes to start (this may take a few minutes)..."
        
        while ($waited -lt $maxWait) {
            if (kubectl cluster-info 2>$null) {
                Write-ColorOutput $Green "Kubernetes is now running"
                return
            }
            
            Write-ColorOutput $Yellow "Still waiting for Kubernetes to start... ($waited/$maxWait seconds)"
            Start-Sleep -Seconds $interval
            $waited += $interval
        }
        
        Write-ColorOutput $Red "Kubernetes did not start within the timeout period."
        Write-ColorOutput $Yellow "Please check Docker Desktop settings and ensure Kubernetes is enabled, then run this script again."
        exit 1
    }
    catch {
        Write-ColorOutput $Red "Failed to restart Docker Desktop: $_"
        Write-ColorOutput $Yellow "Please restart Docker Desktop manually, make sure Kubernetes is enabled, and run this script again."
        exit 1
    }
}

function Build-DockerImage {
    if ($SkipBuild) {
        Write-ColorOutput $Yellow "Skipping Docker image build as requested"
        return
    }
    
    Write-ColorOutput $Cyan "Building Docker image..."
    
    # Check if Dockerfile.fixed exists
    if (-not (Test-Path "$ProjectPath\Dockerfile.fixed")) {
        Write-ColorOutput $Red "Dockerfile.fixed not found in $ProjectPath"
        exit 1
    }
    
    # Build Docker image
    try {
        docker build -f "$ProjectPath\Dockerfile.fixed" -t $ImageName "$ProjectPath"
        
        if ($LASTEXITCODE -ne 0) {
            Write-ColorOutput $Red "Docker build failed with exit code $LASTEXITCODE"
            exit 1
        }
        
        Write-ColorOutput $Green "Docker image built successfully: $ImageName"
    }
    catch {
        Write-ColorOutput $Red "Failed to build Docker image: $_"
        exit 1
    }
}

function Deploy-Application {
    Write-ColorOutput $Cyan "Deploying application to Kubernetes..."
    
    # Switch to docker-desktop context
    kubectl config use-context docker-desktop
    
    # Apply the deployment
    try {
        kubectl apply -f "$ProjectPath\final-deployment.yaml"
        
        if ($LASTEXITCODE -ne 0) {
            Write-ColorOutput $Red "Failed to apply deployment with exit code $LASTEXITCODE"
            exit 1
        }
        
        Write-ColorOutput $Green "Deployment applied successfully"
    }
    catch {
        Write-ColorOutput $Red "Failed to apply deployment: $_"
        exit 1
    }
    
    # Apply the HPA
    try {
        kubectl apply -f "$ProjectPath\hpa-config.yaml"
        
        if ($LASTEXITCODE -ne 0) {
            Write-ColorOutput $Red "Failed to apply HPA with exit code $LASTEXITCODE"
            # Continue anyway, this is not critical
        } else {
            Write-ColorOutput $Green "HPA applied successfully"
        }
    }
    catch {
        Write-ColorOutput $Red "Failed to apply HPA: $_"
        # Continue anyway, this is not critical
    }
    
    # Apply the LoadBalancer service
    try {
        kubectl apply -f "$ProjectPath\loadbalancer-service.yaml"
        
        if ($LASTEXITCODE -ne 0) {
            Write-ColorOutput $Red "Failed to apply LoadBalancer service with exit code $LASTEXITCODE"
            # Continue anyway, this is not critical
        } else {
            Write-ColorOutput $Green "LoadBalancer service applied successfully"
        }
    }
    catch {
        Write-ColorOutput $Red "Failed to apply LoadBalancer service: $_"
        # Continue anyway, this is not critical
    }
    
    # Wait for deployment to be ready
    Write-ColorOutput $Yellow "Waiting for deployment to be ready..."
    Wait-ForDeployment "bel-fastapi-deployment"
}

function Setup-Monitoring {
    Write-ColorOutput $Cyan "Setting up monitoring..."
    
    # Apply the metrics server
    try {
        kubectl apply -f "$ProjectPath\metrics-server.yaml"
        
        if ($LASTEXITCODE -ne 0) {
            Write-ColorOutput $Red "Failed to apply metrics server with exit code $LASTEXITCODE"
            # Continue anyway, this is not critical
        } else {
            Write-ColorOutput $Green "Metrics server applied successfully"
        }
    }
    catch {
        Write-ColorOutput $Red "Failed to apply metrics server: $_"
        # Continue anyway, this is not critical
    }
    
    # Apply Prometheus and Grafana
    try {
        kubectl apply -f "$ProjectPath\prometheus-config.yaml"
        
        if ($LASTEXITCODE -ne 0) {
            Write-ColorOutput $Red "Failed to apply Prometheus/Grafana with exit code $LASTEXITCODE"
            # Continue anyway, this is not critical
        } else {
            Write-ColorOutput $Green "Prometheus and Grafana applied successfully"
        }
    }
    catch {
        Write-ColorOutput $Red "Failed to apply Prometheus/Grafana: $_"
        # Continue anyway, this is not critical
    }
    
    # Apply the dashboard
    try {
        kubectl apply -f "$ProjectPath\monitoring-dashboard.yaml"
        
        if ($LASTEXITCODE -ne 0) {
            Write-ColorOutput $Red "Failed to apply monitoring dashboard with exit code $LASTEXITCODE"
            # Continue anyway, this is not critical
        } else {
            Write-ColorOutput $Green "Monitoring dashboard applied successfully"
        }
    }
    catch {
        Write-ColorOutput $Red "Failed to apply monitoring dashboard: $_"
        # Continue anyway, this is not critical
    }
    
    # Wait for monitoring deployments to be ready
    Write-ColorOutput $Yellow "Waiting for monitoring deployments to be ready..."
    Wait-ForDeployment "prometheus" -timeout 120
    Wait-ForDeployment "grafana" -timeout 120
}

function Show-DeploymentStatus {
    Write-ColorOutput $Cyan "`n=== Deployment Status ==="
    
    # Show pods
    Write-ColorOutput $Yellow "Pods:"
    kubectl get pods
    
    # Show services
    Write-ColorOutput $Yellow "`nServices:"
    kubectl get services
    
    # Show HPA
    Write-ColorOutput $Yellow "`nHPA:"
    kubectl get hpa
    
    # Get service URLs
    $belApiNodePort = Wait-ForServicePort "bel-fastapi-nodeport"
    $belApiLBPort = kubectl get service bel-fastapi-loadbalancer -o jsonpath="{.spec.ports[0].port}" 2>$null
    $prometheusPort = Wait-ForServicePort "prometheus"
    $grafanaPort = Wait-ForServicePort "grafana"
    $kubeDashboardPort = Wait-ForServicePort "kube-dashboard"
    
    Write-ColorOutput $Cyan "`n=== Access URLs ==="
    
    if ($belApiNodePort) {
        Write-ColorOutput $Green "BEL FastAPI (NodePort): http://localhost:$belApiNodePort"
    }
    
    if ($belApiLBPort) {
        Write-ColorOutput $Green "BEL FastAPI (LoadBalancer): http://localhost:$belApiLBPort"
    }
    
    if ($prometheusPort) {
        Write-ColorOutput $Green "Prometheus: http://localhost:$prometheusPort"
    }
    
    if ($grafanaPort) {
        Write-ColorOutput $Green "Grafana: http://localhost:$grafanaPort"
        Write-ColorOutput $Yellow "   - Login: admin/admin"
        Write-ColorOutput $Yellow "   - Recommended dashboards to import: 10856, 6417, 8588, 315"
    }
    
    if ($kubeDashboardPort) {
        Write-ColorOutput $Green "Kubernetes Dashboard: http://localhost:$kubeDashboardPort"
    }
    
    Write-ColorOutput $Cyan "`n=== Monitoring Commands ==="
    Write-ColorOutput $Yellow "- Monitor pods: kubectl get pods"
    Write-ColorOutput $Yellow "- Monitor HPA: kubectl get hpa -w"
    Write-ColorOutput $Yellow "- View logs: kubectl logs -l app=bel-fastapi"
    
    Write-ColorOutput $Cyan "`n=== Monitoring Scripts ==="
    Write-ColorOutput $Yellow "- Open monitoring dashboard: .\monitor.ps1 -Dashboard"
    Write-ColorOutput $Yellow "- Watch HPA scaling: .\monitor.ps1 -WatchHPA"
    Write-ColorOutput $Yellow "- Run load test: .\monitor.ps1 -StressTest"
}

# Main Script Execution

Write-ColorOutput $Cyan "=== BEL MES Backend Complete Deployment Script ==="
Write-ColorOutput $Yellow "This script will set up everything needed to deploy and monitor your application"
Write-ColorOutput $Yellow "Logs are being saved to $LogFile"

# Check if reset is requested
if ($ResetAll) {
    Reset-Deployment
}

# Install and set up prerequisites
Install-DockerDesktop
Enable-Kubernetes

if (-not $MonitoringOnly) {
    # Build and deploy the application
    Build-DockerImage
    Deploy-Application
}

# Set up monitoring
Setup-Monitoring

# Show deployment status
Show-DeploymentStatus

Write-ColorOutput $Green "`n=== Deployment Complete ==="
Write-ColorOutput $Green "Your BEL MES Backend application is now deployed and ready to use!"

# Stop logging
Stop-Transcript
