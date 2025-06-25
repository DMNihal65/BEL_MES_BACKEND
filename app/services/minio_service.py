from minio import Minio
from minio.error import S3Error
from fastapi import HTTPException
from datetime import timedelta
from typing import BinaryIO
from io import BytesIO
import os

class MinioService:
    def __init__(self):
        # Replace these values with your direct MinIO connection settings
        self.client = Minio(
            endpoint=os.getenv("MINIO_ENDPOINT", "172.18.7.155:9000"),
            access_key=os.getenv("MINIO_ACCESS_KEY", "MrKxgiZXGyBArDz8bEnl"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "DJnTcMpypd6x75DlQfCM2MocFIjRON0jU06OgKnn"),
            secure=False  # Set to True if using HTTPS
        )
        self.bucket_name = os.getenv("MINIO_BUCKET_NAME", "documents")
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self):
        """Ensure the bucket exists, create if it doesn't"""
        try:
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)
        except S3Error as e:
            raise Exception(f"Failed to create bucket: {str(e)}")

    def generate_object_path(self, part_number: str, doc_type: str, doc_id: int, version_id: int) -> str:
        """Generate standardized object path"""
        return f"{part_number}/{doc_type}/{doc_id}/{version_id}"

    def upload_file(self, file: BinaryIO, object_name: str, content_type: str | None = None) -> bool:
        """Upload a file to MinIO"""
        try:
            # If file is a BytesIO, get its size
            if isinstance(file, BytesIO):
                file_size = file.getbuffer().nbytes
            else:
                # For other file-like objects, seek to end to get size
                file.seek(0, 2)  # Seek to end
                file_size = file.tell()
                file.seek(0)  # Reset to beginning

            self.client.put_object(
                bucket_name=self.bucket_name,
                object_name=object_name,
                data=file,
                length=file_size,
                content_type=content_type or 'application/octet-stream'
            )
            return True
        except S3Error as e:
            raise Exception(f"Failed to upload file: {str(e)}")

    def download_file(self, object_name: str) -> BytesIO:
        """Download a file from MinIO"""
        try:
            # Get object data
            data = self.client.get_object(
                bucket_name=self.bucket_name,
                object_name=object_name
            )
            
            # Create a BytesIO object to store the file data
            file_data = BytesIO()
            
            # Read the data in chunks and write to BytesIO
            for d in data.stream(32*1024):
                file_data.write(d)
            
            # Reset the pointer to the beginning of the file
            file_data.seek(0)
            
            return file_data
        except S3Error as e:
            raise Exception(f"Failed to download file: {str(e)}")

    def get_file(self, object_name: str) -> BinaryIO:
        """Get a file from MinIO"""
        try:
            response = self.client.get_object(self.bucket_name, object_name)
            return response
        except S3Error as e:
            raise HTTPException(status_code=404, detail=f"File not found: {str(e)}")

    def get_presigned_url(self, object_name: str, expires: timedelta = timedelta(hours=1)) -> str:
        """Generate a presigned URL for object access"""
        try:
            return self.client.presigned_get_object(
                bucket_name=self.bucket_name,
                object_name=object_name,
                expires=expires
            )
        except S3Error as e:
            raise HTTPException(status_code=500, detail=f"URL generation failed: {str(e)}")

    def delete_file(self, object_name: str) -> bool:
        """Delete a file from MinIO"""
        try:
            self.client.remove_object(
                bucket_name=self.bucket_name,
                object_name=object_name
            )
            return True
        except S3Error as e:
            raise Exception(f"Failed to delete file: {str(e)}")

    def get_file_url(self, object_name: str, expires: int = 3600) -> str:
        """Get a presigned URL for object download"""
        try:
            return self.client.presigned_get_object(
                bucket_name=self.bucket_name,
                object_name=object_name,
                expires=expires
            )
        except S3Error as e:
            raise Exception(f"Failed to get file URL: {str(e)}")
