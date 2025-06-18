# BELMES Deployment Guide

This repository contains deployment scripts for both the frontend and backend components of the BELMES (BEL Manufacturing Execution System) application.

## Overview

The deployment system consists of two main PowerShell scripts:

1. `backend-deploy.ps1` - Deploys the FastAPI backend application as a Docker container
2. `frontend-deploy.ps1` - Deploys the React frontend application to a Nginx web server

Both scripts handle the entire deployment process, from building the application locally to setting it up on the remote server.

## Prerequisites

- **PowerShell 5.1+** or **PowerShell Core 6.0+**
- **Docker** (for backend deployment)
- **SSH client** installed and configured
- **Remote server** with:
  - SSH access
  - Nginx (for frontend)
  - Docker (for backend)
  - Sudo privileges

## Backend Deployment

The `backend-deploy.ps1` script automates the deployment of the FastAPI backend application as a Docker container.

### What the Script Does

1. **Builds a Docker image** from your Dockerfile
2. **Saves the image** as a tar file
3. **Transfers the image** to the remote server
4. **Loads and runs the container** on the remote server
5. **Sets up automatic restart** policies
6. **Manages versioning** with timestamps

### Usage

```powershell
powershell -ExecutionPolicy Bypass -File backend-deploy.ps1 -RemoteHost "172.18.7.155" -Port "8002" -Version "1.0.0"
```

### Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `-RemoteUser` | SSH username | `"smc"` |
| `-RemoteHost` | Server hostname or IP | `"172.18.7.155"` |
| `-RemotePath` | Remote path for deployment files | `"/home/smc/bel"` |
| `-DockerfilePath` | Path to Dockerfile | `"./Dockerfile"` |
| `-BackupDir` | Directory for backups | `"/home/smc/bel/backups"` |
| `-ImageName` | Base name for Docker image | `"bel-fastapi-app"` |
| `-ContainerName` | Base name for Docker container | `"bel-fastapi"` |
| `-Port` | Port to expose | `"8002"` |
| `-Version` | Version number | `"1.0.0"` |
| `-UseTimestampVersion` | Add timestamp to version | `$true` |
| `-SkipBuild` | Skip the build step | `$false` |
| `-SkipTransfer` | Skip the file transfer step | `$false` |
| `-SkipDeploy` | Skip the server deployment step | `$false` |
| `-Password` | SSH password (optional) | `$null` |

### Example Commands

Deploy with custom port:
```powershell
./backend-deploy.ps1 -RemoteHost "172.18.7.155" -Port "8080" -Version "1.0.0"
```

Skip build and only deploy:
```powershell
./backend-deploy.ps1 -SkipBuild -Version "1.0.0"
```

## Frontend Deployment

The `frontend-deploy.ps1` script automates the deployment of the React frontend application.

### What the Script Does

1. **Builds the React application** with the correct base path
2. **Transfers the build files** to the remote server
3. **Configures Nginx** to serve the application
4. **Sets up proper routing** for the application and API endpoints

### Usage

```powershell
powershell -ExecutionPolicy Bypass -File frontend-deploy.ps1 -RemoteHost "172.18.7.155" -AppBasePath "/belmes/"
```

### Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `-RemoteUser` | SSH username | `"smc"` |
| `-RemoteHost` | Server hostname or IP | `"172.18.7.155"` |
| `-RemotePath` | Remote path for deployment files | `"/home/smc/belmes"` |
| `-AppBasePath` | Base path for the application | `"/belmes/"` |
| `-BuildDir` | Local build directory | `"dist"` |
| `-ServerSetupScript` | Server setup script name | `"server-setup.sh"` |
| `-SkipBuild` | Skip the build step | `$false` |
| `-SkipTransfer` | Skip the file transfer step | `$false` |
| `-SkipDeploy` | Skip the server deployment step | `$false` |
| `-Password` | SSH password (optional) | `$null` |

### Example Commands

Deploy with custom base path:
```powershell
./frontend-deploy.ps1 -RemoteHost "172.18.7.155" -AppBasePath "/app/"
```

Skip build and only deploy:
```powershell
./frontend-deploy.ps1 -SkipBuild
```

## Docker Operations Guide for BEL MES Backend

### Docker Configuration 1 (Automated Deployment)
The backend deployment script uses a Dockerfile similar to the one below and automates the entire process.

### Docker Configuration 2 (Manual Deployment)

#### Dockerfile
```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create .env file with the provided settings
RUN echo "DB_HOST=172.18.7.155" > .env && \
echo "DB_PORT=5432" >> .env && \
echo "DB_NAME=BEL_DEMO" >> .env && \
echo "DB_USER=cmtismc" >> .env && \
echo "DB_PASSWORD=cmtismc@2025" >> .env && \
echo "SECRET_KEY=BEL_MES_25" >> .env && \
echo "ALGORITHM=HS256" >> .env && \
echo "ACCESS_TOKEN_EXPIRE_MINUTES=30" >> .env && \
echo "REFRESH_TOKEN_EXPIRE_DAYS=7" >> .env && \
echo "MINIO_ENDPOINT=172.18.7.155:9000" >> .env && \
echo "MINIO_ACCESS_KEY=MrKxgiZXGyBArDz8bEnl" >> .env && \
echo "MINIO_SECRET_KEY=DJnTcMpypd6x75DlQfCM2MocFIjRON0jU06OgKnn" >> .env && \
echo "MINIO_BUCKET_NAME=documents" >> .env && \
echo "MINIO_SECURE=false" >> .env

# Expose port 8002
EXPOSE 8002

# Command to run the application with the correct module path
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
```

#### Docker Commands for Manual Deployment

Navigate to project directory:
```bash
cd C:\Users\SMPM\Desktop\Projects - SUPRIYA\2025\BEL_BACKEND_2025\BEL_MES_BACKEND
```

Build Docker image:
```bash
docker build -t bel-fastapi-app-v6 .
```

Run container:
```bash
docker run -d -p 8002:8002 --name bel-fastapi-v6 bel-fastapi-app-v6
```

View running containers:
```bash
docker ps -a
```

View container logs:
```bash
docker logs bel-fastapi-v6
```

Stop and remove container:
```bash
docker stop bel-fastapi-v6
docker rm -f bel-fastapi-v6
```

Run with auto-restart:
```bash
docker run -d --restart unless-stopped -p 8002:8002 --name bel_mes_v1.0.2 bel-fastapi-app-v1.0.0-20250318-162725
```

#### Deployment to Remote Server

Save Docker image:
```bash
docker save bel-fastapi-app-v6 -o bel-fastapi-app-v6.tar
```

Copy to remote server:
```bash
scp bel-fastapi-app-v6.tar smc@172.18.7.155:/home/smc/bel
```

SSH to server:
```bash
ssh smc@172.18.7.155
```

Load and run on server:
```bash
cd bel
ls
docker load -i bel-fastapi-app-v6.tar
docker run -p 8002:8002 --name bel-fastapi-app-v1.0.3-20250408-172202 bel-fastapi-app-v6
```

## MinIO Setup

SSH to the server:
```bash
ssh smc@172.18.7.155
```

Navigate to MinIO directory:
```bash
ls
cd minio
```

Start MinIO server:
```bash
minio server ~/minio --console-address :9001
```

## Troubleshooting

### Permission Issues

If you encounter permission issues, make sure:
- You have SSH access to the remote server
- Your user has sudo privileges on the remote server
- The remote directories are writable by your user

### SSH Authentication

The scripts support both key-based and password-based authentication:
- For key-based authentication (recommended), ensure your SSH keys are properly set up
- For password-based authentication, use the `-Password` parameter

### Execution Policy

If PowerShell blocks script execution, run with the `-ExecutionPolicy Bypass` flag:

```powershell
powershell -ExecutionPolicy Bypass -File backend-deploy.ps1 [parameters]
```

### Docker Issues

If Docker-related commands fail:
- Ensure Docker is installed and running on both local and remote machines
- Verify your user has permissions to run Docker commands
- Check if the Docker daemon is running

## Security Considerations

- Use key-based SSH authentication instead of passwords when possible
- Consider using environment variables for sensitive information
- Review the generated Nginx configuration for security best practices
- Regularly update the Docker base images to include security patches

## License

This project is licensed under the MIT License - see the LICENSE file for details.