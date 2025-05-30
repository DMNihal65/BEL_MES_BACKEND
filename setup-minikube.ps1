#!/usr/bin/env pwsh
# Script to properly set up Minikube for local Kubernetes deployment

Write-Host "🔧 Setting up Minikube for BEL MES Backend deployment..." -ForegroundColor Cyan

# Stop any existing minikube instance
Write-Host "Stopping any existing Minikube instance..." -ForegroundColor Yellow
minikube stop

# Delete existing minikube instance to start fresh
Write-Host "Deleting existing Minikube instance..." -ForegroundColor Yellow
minikube delete

# Start minikube with specific configuration
Write-Host "Starting Minikube with Docker driver..." -ForegroundColor Yellow
minikube start --driver=docker --kubernetes-version=v1.26.3 --memory=4096 --cpus=2 --alsologtostderr

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Failed to start Minikube" -ForegroundColor Red
    exit 1
}

# Enable ingress addon
Write-Host "Enabling Ingress addon..." -ForegroundColor Yellow
minikube addons enable ingress

# Verify Kubernetes is running
Write-Host "Verifying Kubernetes is running..." -ForegroundColor Yellow
kubectl get nodes

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Kubernetes is not running properly" -ForegroundColor Red
    exit 1
}

# Set kubectl context to minikube
Write-Host "Setting kubectl context to minikube..." -ForegroundColor Yellow
kubectl config use-context minikube

# Display minikube IP
Write-Host "Minikube IP:" -ForegroundColor Cyan
minikube ip

Write-Host "✅ Minikube is now set up and ready for deployment!" -ForegroundColor Green
Write-Host "You can now run the deploy-to-k8s.ps1 script to deploy your application." -ForegroundColor Cyan
