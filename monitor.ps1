param (
    [switch]$WatchPods,
    [switch]$WatchHPA,
    [switch]$Logs,
    [switch]$Dashboard,
    [switch]$StressTest
)

$Green = [ConsoleColor]::Green
$Yellow = [ConsoleColor]::Yellow
$Cyan = [ConsoleColor]::Cyan

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

function Get-ServiceNodePort($serviceName) {
    $nodePort = kubectl get service $serviceName -o jsonpath="{.spec.ports[0].nodePort}" 2>$null
    return $nodePort
}

# Show basic application status
if (-not ($WatchPods -or $WatchHPA -or $Logs -or $Dashboard -or $StressTest)) {
    Write-ColorOutput $Cyan "=== BEL FastAPI Application Status ==="
    
    Write-ColorOutput $Yellow "Deployments:"
    kubectl get deployments | Out-Host
    
    Write-ColorOutput $Yellow "Pods:"
    kubectl get pods | Out-Host
    
    Write-ColorOutput $Yellow "Services:"
    kubectl get services | Out-Host
    
    Write-ColorOutput $Yellow "HPA:"
    kubectl get hpa | Out-Host
    
    # Get service URLs
    $grafanaPort = Get-ServiceNodePort "grafana"
    $prometheusPort = Get-ServiceNodePort "prometheus"
    $belApiPort = Get-ServiceNodePort "bel-fastapi-nodeport"
    
    Write-ColorOutput $Cyan "`n=== Access URLs ==="
    if ($belApiPort) {
        Write-ColorOutput $Green "BEL FastAPI: http://localhost:$belApiPort"
    }
    if ($grafanaPort) {
        Write-ColorOutput $Green "Grafana: http://localhost:$grafanaPort (login: admin/admin)"
    }
    if ($prometheusPort) {
        Write-ColorOutput $Green "Prometheus: http://localhost:$prometheusPort"
    }
    
    Write-ColorOutput $Cyan "`n=== Script Options ==="
    Write-ColorOutput $Yellow "- Watch pods in real-time: .\monitor.ps1 -WatchPods"
    Write-ColorOutput $Yellow "- Watch HPA in real-time: .\monitor.ps1 -WatchHPA"
    Write-ColorOutput $Yellow "- View application logs: .\monitor.ps1 -Logs"
    Write-ColorOutput $Yellow "- Open all dashboards: .\monitor.ps1 -Dashboard"
    Write-ColorOutput $Yellow "- Run stress test: .\monitor.ps1 -StressTest"
    
    exit 0
}

# Watch pods in real-time
if ($WatchPods) {
    Write-ColorOutput $Cyan "Watching pods in real-time (Ctrl+C to exit)..."
    kubectl get pods -w
    exit 0
}

# Watch HPA in real-time
if ($WatchHPA) {
    Write-ColorOutput $Cyan "Watching HPA in real-time (Ctrl+C to exit)..."
    kubectl get hpa -w
    exit 0
}

# View application logs
if ($Logs) {
    Write-ColorOutput $Cyan "Showing application logs (Ctrl+C to exit)..."
    kubectl logs -l app=bel-fastapi --tail=50 -f
    exit 0
}

# Open all dashboards
if ($Dashboard) {
    # Get service URLs
    $grafanaPort = Get-ServiceNodePort "grafana"
    $prometheusPort = Get-ServiceNodePort "prometheus"
    $kubeDashPort = Get-ServiceNodePort "kube-dashboard"
    $belApiPort = Get-ServiceNodePort "bel-fastapi-nodeport"
    
    Write-ColorOutput $Cyan "Opening dashboards in your browser..."
    
    if ($grafanaPort) {
        Write-ColorOutput $Green "Opening Grafana: http://localhost:$grafanaPort"
        Start-Process "http://localhost:$grafanaPort"
    }
    
    if ($prometheusPort) {
        Write-ColorOutput $Green "Opening Prometheus: http://localhost:$prometheusPort"
        Start-Process "http://localhost:$prometheusPort"
    }
    
    if ($kubeDashPort) {
        Write-ColorOutput $Green "Opening Kubernetes Dashboard: http://localhost:$kubeDashPort"
        Start-Process "http://localhost:$kubeDashPort"
    }
    
    if ($belApiPort) {
        Write-ColorOutput $Green "Opening BEL FastAPI: http://localhost:$belApiPort"
        Start-Process "http://localhost:$belApiPort"
    }
    
    exit 0
}

# Run stress test
if ($StressTest) {
    $belApiPort = Get-ServiceNodePort "bel-fastapi-nodeport"
    if (-not $belApiPort) {
        Write-ColorOutput $Cyan "Error: Could not find BEL FastAPI NodePort service"
        exit 1
    }
    
    $apiUrl = "http://localhost:$belApiPort"
    Write-ColorOutput $Cyan "Running stress test against $apiUrl"
    Write-ColorOutput $Yellow "This will create load on your application to test autoscaling"
    Write-ColorOutput $Yellow "Open another terminal and run '.\monitor.ps1 -WatchHPA' to monitor scaling"
    Write-ColorOutput $Yellow "Press Ctrl+C to stop the stress test"
    
    # Check if hey load testing tool is installed
    $heyInstalled = Get-Command hey -ErrorAction SilentlyContinue
    
    if (-not $heyInstalled) {
        Write-ColorOutput $Yellow "Installing hey load testing tool..."
        go install github.com/rakyll/hey@latest
    }
    
    if (Get-Command hey -ErrorAction SilentlyContinue) {
        # Run load test with hey
        hey -z 5m -c 50 -q 10 $apiUrl
    } else {
        # Fallback to PowerShell-based load testing
        Write-ColorOutput $Yellow "Using PowerShell for load testing (install Go and hey for better results)"
        
        $jobs = @()
        for ($i = 0; $i -lt 10; $i++) {
            $jobs += Start-Job -ScriptBlock {
                param($url, $requests)
                for ($j = 0; $j -lt $requests; $j++) {
                    try {
                        Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 | Out-Null
                    } catch {}
                    Start-Sleep -Milliseconds 100
                }
            } -ArgumentList $apiUrl, 1000
        }
        
        try {
            # Show progress while jobs are running
            while (Get-Job | Where-Object { $_.State -eq 'Running' }) {
                $runningJobs = (Get-Job | Where-Object { $_.State -eq 'Running' }).Count
                Write-ColorOutput $Yellow "Running $runningJobs parallel load test jobs..."
                
                # Show HPA status
                kubectl get hpa
                
                Start-Sleep -Seconds 5
            }
        } finally {
            # Clean up jobs
            Get-Job | Remove-Job -Force
        }
    }
    
    exit 0
}
