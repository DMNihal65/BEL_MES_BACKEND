from minio import Minio
from minio.error import S3Error
from fastapi import HTTPException
from datetime import timedelta
from typing import BinaryIO

class MinioService:
    def __init__(self):
        # Replace these values with your direct MinIO connection settings
        self.client = Minio(
            endpoint="localhost:9000",  # MinIO endpoint
            access_key="SkSfn28fOBig7L7nraY4",  # MinIO access key
            secret_key="4oSBg8nSsabb3kF0ZwG0IBz3Sfhkyb4EHLeTZKHn",  # MinIO secret key
            secure=False  # Set to True if MinIO uses HTTPS
        )
        self.bucket_name = "documents2"  # MinIO bucket name
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self):
        """Ensure the configured bucket exists, create if it doesn't"""
        try:
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)
        except S3Error as e:
            raise HTTPException(status_code=500, detail=f"MinIO error: {str(e)}")

    def generate_object_path(self, part_number: str, doc_type: str, doc_id: int, version_id: int) -> str:
        """Generate standardized object path"""
        return f"{part_number}/{doc_type}/{doc_id}/{version_id}"

    def upload_file(self, file: BinaryIO, object_name: str, content_type: str) -> dict:
        """Upload a file to MinIO"""
        try:
            file.seek(0, 2)  # Go to end of file
            file_size = file.tell()  # Get current position (file size)
            file.seek(0)  # Go back to start of file

            result = self.client.put_object(
                bucket_name=self.bucket_name,
                object_name=object_name,
                data=file,
                length=file_size,  # Specify the file size
                content_type=content_type
            )
            return {
                "bucket_name": self.bucket_name,
                "object_name": object_name,
                "etag": result.etag
            }
        except S3Error as e:
            raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

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

    def delete_file(self, object_name: str):
        """Delete a file from MinIO"""
        try:
            self.client.remove_object(self.bucket_name, object_name)
        except S3Error as e:
            raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")
