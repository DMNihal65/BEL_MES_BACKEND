param (
    [switch]$MonitoringOnly
)

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

function Wait-ForDeployment($name, $namespace = "default", $timeout = 300) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    
    while ($sw.Elapsed.TotalSeconds -lt $timeout) {
        $deployment = kubectl get deployment $name -n $namespace -o json | ConvertFrom-Json
        
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
        
        Write-ColorOutput $Yellow "Waiting for deployment $name... Available: $available/$desired"
        Start-Sleep -Seconds 5
    }
    
    Write-ColorOutput $Red "Deployment $name did not become ready within timeout period"
    return $false
}

if (-not $MonitoringOnly) {
    # Step 1: Apply the updated deployment
    Write-ColorOutput $Cyan "Applying FastAPI deployment with fixed scaling configuration..."
    kubectl apply -f final-deployment.yaml
    
    # Step 2: Apply LoadBalancer and HPA
    Write-ColorOutput $Cyan "Applying LoadBalancer service..."
    kubectl apply -f loadbalancer-service.yaml
    
    Write-ColorOutput $Cyan "Applying Horizontal Pod Autoscaler..."
    kubectl apply -f hpa-config.yaml
    
    # Step 3: Wait for deployment to be ready
    Write-ColorOutput $Yellow "Waiting for deployment to be ready..."
    Wait-ForDeployment "bel-fastapi-deployment"
}

# Step 4: Apply monitoring configuration
Write-ColorOutput $Cyan "Setting up Prometheus and Grafana for monitoring..."
kubectl apply -f prometheus-config.yaml

# Step 5: Wait for monitoring deployments to be ready
Write-ColorOutput $Yellow "Waiting for Prometheus to be ready..."
Wait-ForDeployment "prometheus"

Write-ColorOutput $Yellow "Waiting for Grafana to be ready..."
Wait-ForDeployment "grafana"

# Step 6: Get service URLs
Write-ColorOutput $Cyan "`nGetting service URLs..."
$prometheusNodePort = kubectl get service prometheus -o jsonpath="{.spec.ports[0].nodePort}"
$grafanaNodePort = kubectl get service grafana -o jsonpath="{.spec.ports[0].nodePort}"
$belApiNodePort = kubectl get service bel-fastapi-nodeport -o jsonpath="{.spec.ports[0].nodePort}"

# Step 7: Print out connection information
Write-ColorOutput $Green "`n=== Deployment Completed Successfully ==="
Write-ColorOutput $Green "BEL FastAPI Application: http://localhost:$belApiNodePort"
Write-ColorOutput $Green "Prometheus Monitoring: http://localhost:$prometheusNodePort"
Write-ColorOutput $Green "Grafana Dashboard: http://localhost:$grafanaNodePort"
Write-ColorOutput $Green "  - Default Grafana login: admin/admin"

Write-ColorOutput $Cyan "`nMonitoring Setup Instructions:"
Write-ColorOutput $Yellow "1. Open Grafana at http://localhost:$grafanaNodePort"
Write-ColorOutput $Yellow "2. Log in with username 'admin' and password 'admin'"
Write-ColorOutput $Yellow "3. Navigate to Configuration > Data Sources"
Write-ColorOutput $Yellow "4. Add a new Prometheus data source"
Write-ColorOutput $Yellow "5. Set the URL to http://prometheus:9090"
Write-ColorOutput $Yellow "6. Click 'Save & Test'"
Write-ColorOutput $Yellow "7. Import a dashboard for Kubernetes monitoring using ID 10856"

Write-ColorOutput $Cyan "`nUseful Commands:"
Write-ColorOutput $Yellow "- Check pod status: kubectl get pods"
Write-ColorOutput $Yellow "- Check HPA status: kubectl get hpa"
Write-ColorOutput $Yellow "- Scale manually: kubectl scale deployment/bel-fastapi-deployment --replicas=3"
Write-ColorOutput $Yellow "- View logs: kubectl logs -l app=bel-fastapi"
Write-ColorOutput $Yellow "- Monitor scaling: kubectl get hpa -w"
