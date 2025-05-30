param (
    [switch]$OpenDashboards,
    [switch]$ShowMetrics,
    [switch]$StressTest
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

function Get-ServicePort($serviceName) {
    $nodePort = kubectl get service $serviceName -o jsonpath="{.spec.ports[0].nodePort}" 2>$null
    if (-not $nodePort) {
        return $null
    }
    return $nodePort
}

function Get-ClusterStatus {
    Write-ColorOutput $Cyan "=== Kubernetes Cluster Status ==="
    
    # Get node status
    Write-ColorOutput $Yellow "Node Status:"
    kubectl get nodes | Out-Host
    
    # Get resource usage for nodes
    Write-ColorOutput $Yellow "`nNode Resource Usage:"
    kubectl top nodes | Out-Host
    
    # Get pod status
    Write-ColorOutput $Yellow "`nPod Status:"
    kubectl get pods --all-namespaces | Out-Host
    
    # Get pod resource usage
    Write-ColorOutput $Yellow "`nPod Resource Usage:"
    kubectl top pods | Out-Host
    
    # Get HPA status
    Write-ColorOutput $Yellow "`nHorizontal Pod Autoscaler Status:"
    kubectl get hpa | Out-Host
    
    # Get service status
    Write-ColorOutput $Yellow "`nService Status:"
    kubectl get services | Out-Host
}

function Open-MonitoringDashboards {
    Write-ColorOutput $Cyan "=== Opening Monitoring Dashboards ==="
    
    # Get all NodePorts
    $grafanaPort = Get-ServicePort "grafana"
    $prometheusPort = Get-ServicePort "prometheus"
    $kubeDashboardPort = Get-ServicePort "kube-dashboard"
    $apiPort = Get-ServicePort "bel-fastapi-nodeport"
    
    if ($grafanaPort) {
        Write-ColorOutput $Green "Opening Grafana dashboard at http://localhost:$grafanaPort"
        Start-Process "http://localhost:$grafanaPort"
    } else {
        Write-ColorOutput $Red "Grafana service not found"
    }
    
    if ($prometheusPort) {
        Write-ColorOutput $Green "Opening Prometheus dashboard at http://localhost:$prometheusPort"
        Start-Process "http://localhost:$prometheusPort"
    } else {
        Write-ColorOutput $Red "Prometheus service not found"
    }
    
    if ($kubeDashboardPort) {
        Write-ColorOutput $Green "Opening Weave Scope dashboard at http://localhost:$kubeDashboardPort"
        Start-Process "http://localhost:$kubeDashboardPort"
    } else {
        Write-ColorOutput $Red "Kube Dashboard service not found"
    }
    
    if ($apiPort) {
        Write-ColorOutput $Green "Opening BEL FastAPI at http://localhost:$apiPort"
        Start-Process "http://localhost:$apiPort"
    } else {
        Write-ColorOutput $Red "BEL FastAPI service not found"
    }
}

function Start-StressTest {
    Write-ColorOutput $Cyan "=== Starting Stress Test ==="
    
    # Get API service port
    $apiPort = Get-ServicePort "bel-fastapi-nodeport"
    if (-not $apiPort) {
        Write-ColorOutput $Red "BEL FastAPI service not found. Cannot run stress test."
        return
    }
    
    $apiUrl = "http://localhost:$apiPort"
    Write-ColorOutput $Yellow "Running stress test against $apiUrl"
    
    # Create a stress test pod if it doesn't exist
    $podExists = kubectl get pod stress-test 2>$null
    if (-not $podExists) {
        Write-ColorOutput $Yellow "Creating stress test pod..."
        kubectl run stress-test --image=busybox --restart=Never -- /bin/sh -c "while true; do wget -q -O- $apiUrl > /dev/null; done"
    } else {
        Write-ColorOutput $Yellow "Stress test pod already exists"
    }
    
    Write-ColorOutput $Green "Stress test started. This will generate load on your application."
    Write-ColorOutput $Yellow "Watch your HPA in another terminal with: kubectl get hpa -w"
    Write-ColorOutput $Yellow "Press Ctrl+C when you want to stop the stress test."
    
    try {
        while ($true) {
            $hpaStatus = kubectl get hpa bel-fastapi-hpa -o custom-columns=NAME:.metadata.name,TARGETS:.status.currentMetrics[*].resource.current.utilization,REPLICAS:.status.currentReplicas,MAX_REPLICAS:.spec.maxReplicas
            Write-Output $hpaStatus | Out-Host
            Start-Sleep -Seconds 5
        }
    }
    catch {
        Write-ColorOutput $Yellow "Stopping stress test..."
        kubectl delete pod stress-test --grace-period=0 --force
    }
}

# Main script execution
if ($ShowMetrics) {
    Get-ClusterStatus
}

if ($OpenDashboards) {
    Open-MonitoringDashboards
}

if ($StressTest) {
    Start-StressTest
}

# If no switches provided, show all options
if (-not ($ShowMetrics -or $OpenDashboards -or $StressTest)) {
    Get-ClusterStatus
    
    Write-ColorOutput $Cyan "`n=== Monitoring URLs ==="
    
    $grafanaPort = Get-ServicePort "grafana"
    $prometheusPort = Get-ServicePort "prometheus"
    $kubeDashboardPort = Get-ServicePort "kube-dashboard"
    $apiPort = Get-ServicePort "bel-fastapi-nodeport"
    
    if ($grafanaPort) {
        Write-ColorOutput $Green "Grafana: http://localhost:$grafanaPort"
        Write-ColorOutput $Yellow "   - Login: admin/admin"
        Write-ColorOutput $Yellow "   - Recommended dashboards to import: 10856, 6417, 8588, 315"
    }
    
    if ($prometheusPort) {
        Write-ColorOutput $Green "Prometheus: http://localhost:$prometheusPort"
    }
    
    if ($kubeDashboardPort) {
        Write-ColorOutput $Green "Weave Scope: http://localhost:$kubeDashboardPort"
    }
    
    if ($apiPort) {
        Write-ColorOutput $Green "BEL FastAPI: http://localhost:$apiPort"
    }
    
    Write-ColorOutput $Cyan "`n=== Script Options ==="
    Write-ColorOutput $Yellow "- Show current metrics: .\monitoring-dashboard.ps1 -ShowMetrics"
    Write-ColorOutput $Yellow "- Open all dashboards: .\monitoring-dashboard.ps1 -OpenDashboards"
    Write-ColorOutput $Yellow "- Run stress test: .\monitoring-dashboard.ps1 -StressTest"
    
    Write-ColorOutput $Cyan "`n=== Useful Commands ==="
    Write-ColorOutput $Yellow "- Watch HPA scaling: kubectl get hpa -w"
    Write-ColorOutput $Yellow "- Watch pod creation: kubectl get pods -w"
    Write-ColorOutput $Yellow "- View detailed pod metrics: kubectl describe pod <pod-name>"
    Write-ColorOutput $Yellow "- Scale deployment manually: kubectl scale deployment/bel-fastapi-deployment --replicas=3"
    Write-ColorOutput $Yellow "- View logs: kubectl logs -l app=bel-fastapi --tail=50"
}
