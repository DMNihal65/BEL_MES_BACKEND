# Deployment script for BEL MES Backend
# This script will build and deploy the application to Minikube

# Stop on first error
$ErrorActionPreference = "Stop"

Write-Host "🚀 Starting BEL MES Backend deployment..." -ForegroundColor Cyan

# 1. Clean up any existing resources
Write-Host "🧹 Cleaning up existing resources..." -ForegroundColor Yellow
kubectl delete -f .\k8s\ --ignore-not-found=true

# 2. Set Docker to use Minikube's Docker daemon
Write-Host "🐳 Configuring Docker to use Minikube's daemon..." -ForegroundColor Cyan
minikube docker-env | Invoke-Expression

# 3. Build the Docker image
Write-Host "🔨 Building Docker image..." -ForegroundColor Cyan
docker build -t bel-mes-backend:latest .

# 4. Apply Kubernetes configurations
Write-Host "📦 Applying Kubernetes configurations..." -ForegroundColor Cyan
kubectl apply -f .\k8s\

# 5. Wait for the deployment to be ready
Write-Host "⏳ Waiting for deployment to be ready..." -ForegroundColor Cyan
kubectl rollout status deployment/bel-mes-backend --timeout=120s

# 6. Get the service URL
$url = minikube service bel-mes-backend-service --url
Write-Host "`n✅ Deployment successful!" -ForegroundColor Green
Write-Host "🌐 Your application is now running at: $url" -ForegroundColor Green

# 7. Show how to access the service
Write-Host "`n🔗 Access methods:" -ForegroundColor Cyan
Write-Host "1. Direct URL: $url"
Write-Host "2. Port-forwarding: kubectl port-forward svc/bel-mes-backend-service 8002:8002"

# 8. Show pod status
Write-Host "`n📊 Pod status:" -ForegroundColor Cyan
kubectl get pods

# 9. Show service status
Write-Host "`n🔧 Service status:" -ForegroundColor Cyan
kubectl get svc

# 10. Show logs from the first pod
$podName = (kubectl get pods -l app=bel-mes-backend -o name | Select-Object -First 1) -replace '^pod/'
Write-Host "`n📝 Logs from pod $podName :" -ForegroundColor Cyan
kubectl logs $podName

Write-Host "`n✨ Deployment complete!" -ForegroundColor Green
