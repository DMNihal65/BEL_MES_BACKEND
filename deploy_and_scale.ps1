param (
    [switch]$DeployOnly,
    [switch]$TestOnly,
    [int]$TestRequests = 100,
    [int]$TestConcurrency = 10
)

# Set colors for console output
$Green = [ConsoleColor]::Green
$Yellow = [ConsoleColor]::Yellow
$Cyan = [ConsoleColor]::Cyan
$Red = [ConsoleColor]::Red

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

# Check for required tools
if (-not (Check-Command "kubectl")) {
    Write-ColorOutput $Red "Error: kubectl is not installed or not in path."
    exit 1
}

if (-not (Check-Command "docker")) {
    Write-ColorOutput $Red "Error: docker is not installed or not in path."
    exit 1
}

if (-not $TestOnly) {
    Write-ColorOutput $Cyan "=== BEL MES Backend Deployment and Scaling ==="
    Write-ColorOutput $Cyan "Setting up Kubernetes deployment, services, and autoscaler..."

    # Apply all configurations
    Write-ColorOutput $Yellow "Applying main deployment..."
    kubectl apply -f final-deployment.yaml
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput $Red "Error applying deployment configuration."
        exit 1
    }

    Write-ColorOutput $Yellow "Applying LoadBalancer service..."
    kubectl apply -f loadbalancer-service.yaml
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput $Red "Error applying LoadBalancer service configuration."
    }

    Write-ColorOutput $Yellow "Applying Horizontal Pod Autoscaler..."
    kubectl apply -f hpa-config.yaml
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput $Red "Error applying HPA configuration."
    }

    # Wait for deployment to be ready
    Write-ColorOutput $Yellow "Waiting for deployment to be ready..."
    kubectl rollout status deployment/bel-fastapi-deployment --timeout=120s
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput $Red "Deployment didn't become ready within timeout period."
    } else {
        Write-ColorOutput $Green "Deployment is ready!"
    }

    # Print resource status
    Write-ColorOutput $Cyan "`nCurrent Kubernetes Resources:"
    kubectl get all | Out-Host
}

if (-not $DeployOnly) {
    # Verify Python and required packages for load testing
    Write-ColorOutput $Cyan "`n=== Load Testing Setup ==="
    
    if (-not (Check-Command "pip")) {
        Write-ColorOutput $Red "Error: pip is not installed or not in path."
        Write-ColorOutput $Yellow "To install test dependencies: pip install aiohttp matplotlib colorama"
        exit 1
    }

    # Optionally install dependencies if not already installed
    Write-ColorOutput $Yellow "Checking/installing required Python packages..."
    pip install aiohttp matplotlib colorama | Out-Null

    # Get service information
    $nodePort = (kubectl get service bel-fastapi-nodeport -o jsonpath="{.spec.ports[0].nodePort}" 2>$null)
    if (-not $nodePort) {
        $nodePort = "32000" # Default fallback
    }
    
    $baseUrl = "http://localhost:$nodePort"
    Write-ColorOutput $Green "API base URL for testing: $baseUrl"

    # Run load test
    Write-ColorOutput $Cyan "`n=== Running Load Test ==="
    Write-ColorOutput $Yellow "Starting test with $TestRequests requests per endpoint, $TestConcurrency concurrent connections..."
    python load_test.py --url $baseUrl --requests $TestRequests --concurrency $TestConcurrency
    
    # Monitor scaling
    Write-ColorOutput $Cyan "`n=== Monitoring Scaling ==="
    Write-ColorOutput $Yellow "Checking HPA status..."
    kubectl get hpa
    
    Write-ColorOutput $Yellow "`nChecking pod status..."
    kubectl get pods
    
    Write-ColorOutput $Yellow "`nChecking resource usage..."
    kubectl top pods 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput $Yellow "Note: kubectl top command requires metrics-server to be installed."
    }
}

Write-ColorOutput $Cyan "`n=== Deployment & Scaling Guide ==="
Write-ColorOutput $Green "✓ Your BEL MES Backend is now deployed with autoscaling capabilities."
Write-ColorOutput $Green "✓ The Horizontal Pod Autoscaler will scale based on CPU and memory usage."
Write-ColorOutput $Green "✓ The LoadBalancer service will distribute traffic across pods."

Write-ColorOutput $Cyan "`nUseful Commands:"
Write-ColorOutput $Yellow "- Monitor scaling: kubectl get hpa -w"
Write-ColorOutput $Yellow "- View all resources: kubectl get all"
Write-ColorOutput $Yellow "- Check logs: kubectl logs -l app=bel-fastapi --tail=50"
Write-ColorOutput $Yellow "- Run load test again: python load_test.py --requests 200 --concurrency 20"
Write-ColorOutput $Yellow "- Scale manually: kubectl scale deployment/bel-fastapi-deployment --replicas=3"
