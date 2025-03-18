# BELMES Deployment Guide

This repository contains deployment scripts for both the frontend and backend components of the BELMES (BEL Manufacturing Execution System) application.

## Overview

The deployment system consists of two main PowerShell scripts:

1. `backend-deploy.ps1` - Deploys the FastAPI backend application as a Docker container

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
