FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create .env file with the provided settings
RUN echo "DB_HOST=172.18.7.91" > .env && \
    echo "DB_PORT=5432" >> .env && \
    echo "DB_NAME=BEL_MES13" >> .env && \
    echo "DB_USER=postgres" >> .env && \
    echo "DB_PASSWORD=postgres" >> .env && \
    echo "SECRET_KEY=BEL_MES_25" >> .env && \
    echo "ALGORITHM=HS256" >> .env && \
    echo "ACCESS_TOKEN_EXPIRE_MINUTES=99999" >> .env && \
    echo "REFRESH_TOKEN_EXPIRE_DAYS=30" >> .env && \
    echo "MINIO_ENDPOINT=172.18.7.91:9000" >> .env && \
    echo "MINIO_ACCESS_KEY=ZslCVCUXAuYpCXN9P86x" >> .env && \
    echo "MINIO_SECRET_KEY=OLyyfRnRXtizJeydQztnIYanKsDqGSoUP7w7hKrR" >> .env && \
    echo "MINIO_BUCKET_NAME=documents3" >> .env && \
    echo "MINIO_SECURE=false" >> .env

# Expose port 8002
EXPOSE 8008

# Command to run the application - updated to use package structure
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8008", "--workers", "4"]