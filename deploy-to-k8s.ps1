#!/usr/bin/env pwsh
# Script to build Docker image and deploy to local Kubernetes

# Set variables
$IMAGE_NAME = "bel-mes-backend"
$IMAGE_TAG = "latest"
$NAMESPACE = "default"

Write-Host "🚀 Starting deployment process for BEL MES Backend..." -ForegroundColor Cyan

# Check if Docker is running
try {
    docker info | Out-Null
    Write-Host "✅ Docker is running" -ForegroundColor Green
} catch {
    Write-Host "❌ Docker is not running. Please start Docker and try again." -ForegroundColor Red
    exit 1
}

# Check if kubectl is installed
try {
    kubectl version --client | Out-Null
    Write-Host "✅ kubectl is installed" -ForegroundColor Green
} catch {
    Write-Host "❌ kubectl is not installed. Please install kubectl and try again." -ForegroundColor Red
    exit 1
}

# Check if Kubernetes is running
$kubeRunning = $false

# First check if minikube is available
$minikubeAvailable = $null -ne (Get-Command minikube -ErrorAction SilentlyContinue)

if ($minikubeAvailable) {
    # Try to use minikube
    Write-Host "Checking minikube status..." -ForegroundColor Cyan
    $minikubeStatus = minikube status 2>&1
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ Minikube is running" -ForegroundColor Green
        
        # Set kubectl context to minikube
        Write-Host "Setting kubectl context to minikube..." -ForegroundColor Cyan
        kubectl config use-context minikube | Out-Null
        
        $kubeRunning = $true
    } else {
        Write-Host "❌ Minikube is not running properly" -ForegroundColor Red
        Write-Host "Attempting to start minikube..." -ForegroundColor Yellow
        
        # Try to start minikube
        minikube start --driver=docker --alsologtostderr
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "✅ Minikube started successfully" -ForegroundColor Green
            $kubeRunning = $true
            
            # Set kubectl context to minikube
            kubectl config use-context minikube | Out-Null
        } else {
            Write-Host "❌ Failed to start minikube" -ForegroundColor Red
        }
    }
} else {
    # Try standard kubectl
    try {
        $kubeStatus = kubectl get nodes 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "✅ Kubernetes is running" -ForegroundColor Green
            $kubeRunning = $true
        } else {
            Write-Host "❌ Kubernetes is not running properly" -ForegroundColor Red
            Write-Host $kubeStatus -ForegroundColor Yellow
        }
    } catch {
        Write-Host "❌ Error checking Kubernetes status" -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Yellow
    }
}

if (-not $kubeRunning) {
    Write-Host "`nKubernetes setup instructions:" -ForegroundColor Cyan
    Write-Host "1. If using Docker Desktop:" -ForegroundColor White
    Write-Host "   - Open Docker Desktop" -ForegroundColor White
    Write-Host "   - Go to Settings > Kubernetes" -ForegroundColor White
    Write-Host "   - Check 'Enable Kubernetes' and click Apply & Restart" -ForegroundColor White
    Write-Host "`n2. If using Minikube:" -ForegroundColor White
    Write-Host "   - Run: minikube start" -ForegroundColor White
    Write-Host "`nPlease start Kubernetes and try again." -ForegroundColor White
    exit 1
}

# Build Docker image
Write-Host "🔨 Building Docker image: ${IMAGE_NAME}:${IMAGE_TAG}..." -ForegroundColor Cyan
docker build -t "${IMAGE_NAME}:${IMAGE_TAG}" .

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Failed to build Docker image" -ForegroundColor Red
    exit 1
}
Write-Host "✅ Docker image built successfully" -ForegroundColor Green

# Apply Kubernetes configurations
Write-Host "📦 Deploying to Kubernetes..." -ForegroundColor Cyan

# Apply deployment
Write-Host "Applying deployment..." -ForegroundColor Cyan
kubectl apply -f ./k8s/deployment.yaml
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Failed to apply deployment" -ForegroundColor Red
    exit 1
}

# Apply service
Write-Host "Applying service..." -ForegroundColor Cyan
kubectl apply -f ./k8s/service.yaml
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Failed to apply service" -ForegroundColor Red
    exit 1
}

# Apply HPA
Write-Host "Applying Horizontal Pod Autoscaler..." -ForegroundColor Cyan
kubectl apply -f ./k8s/hpa.yaml
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Failed to apply HPA" -ForegroundColor Red
    exit 1
}

# Wait for deployment to be ready
Write-Host "⏳ Waiting for deployment to be ready..." -ForegroundColor Cyan
kubectl rollout status deployment/bel-mes-backend

# Get service details
Write-Host "🔍 Getting service details..." -ForegroundColor Cyan
kubectl get service bel-mes-backend-service

# Print access information
Write-Host "🎉 Deployment complete!" -ForegroundColor Green
Write-Host "Your application should be accessible at the external IP shown above, port 8002" -ForegroundColor Cyan
Write-Host "If you're using Docker Desktop's Kubernetes, try accessing at: http://localhost:8002" -ForegroundColor Cyan
Write-Host "If you're using Minikube, run 'minikube service bel-mes-backend-service' to access the application" -ForegroundColor Cyan

# Print scaling information
Write-Host "📊 Scaling Information:" -ForegroundColor Cyan
Write-Host "- Initial replicas: 3" -ForegroundColor White
Write-Host "- Will automatically scale up to 10 replicas based on CPU usage" -ForegroundColor White
Write-Host "- To check current replicas: kubectl get hpa bel-mes-backend-hpa" -ForegroundColor White

# Print helpful commands
Write-Host "🛠️ Helpful commands:" -ForegroundColor Cyan
Write-Host "- View all pods: kubectl get pods" -ForegroundColor White
Write-Host "- View pod logs: kubectl logs <pod-name>" -ForegroundColor White
Write-Host "- Scale manually: kubectl scale deployment/bel-mes-backend --replicas=<number>" -ForegroundColor White
Write-Host "- Delete deployment: kubectl delete -f ./k8s/" -ForegroundColor White
