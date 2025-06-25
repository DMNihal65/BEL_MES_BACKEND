# BEL MES API

This is the backend API for the BEL Manufacturing Execution System (MES).

## Prerequisites

- Docker and Docker Compose (for Docker deployment)
- Python 3.12 (for local development)
- NSSM (for Windows service deployment)

## Deployment Options

### 1. Docker Deployment

```bash
# For Windows
start.bat

# For Linux/Mac
./start.sh
```

### 2. Local Development

```bash
run_local.bat
```

### 3. Production Mode without Docker

```bash
run_production.bat
```

### 4. Windows Service

```bash
# Install as a service
install_service.bat

# Start the service
nssm start BEL_MES_API
```

## Configuration

Make sure your `.env` file is properly configured with all the required variables:

```
# Database settings
DB_HOST=<your-db-host>
DB_PORT=<your-db-port>
DB_NAME=<your-db-name>
DB_USER=<your-db-user>
DB_PASSWORD=<your-db-password>

# JWT settings
SECRET_KEY=<your-secret-key>
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# MinIO settings
MINIO_ENDPOINT=<your-minio-endpoint>
MINIO_ACCESS_KEY=<your-minio-access-key>
MINIO_SECRET_KEY=<your-minio-secret-key>
MINIO_BUCKET_NAME=<your-minio-bucket-name>
MINIO_SECURE=false
```

## Monitoring

To check if the application is running and restart it if needed:

```bash
monitor_app.bat
```

## API Documentation

Once the application is running, you can access the API documentation at:

- Swagger UI: http://localhost:8001/docs
- ReDoc: http://localhost:8001/redoc

## Detailed Setup Guide

For detailed setup instructions, please refer to the [Setup Guide](SETUP_GUIDE.md).

## Troubleshooting

If you encounter any issues, please refer to the [Troubleshooting Guide](TROUBLESHOOTING.md).

## License

[Your License Information] 