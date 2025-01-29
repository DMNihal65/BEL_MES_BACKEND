from pydantic_settings import BaseSettings
from dotenv import load_dotenv
from typing import Optional

load_dotenv()

class Settings(BaseSettings):
    # Database settings
    DB_HOST: str
    DB_PORT: int
    DB_NAME: str
    DB_USER: str
    DB_PASSWORD: str
    
    # JWT settings
    SECRET_KEY: str = "BEL_MES_25"  # Default value
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30  # Default value
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Add MinIO settings
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "8BloN2A9ZJWvokaQihs4"
    MINIO_SECRET_KEY: str = "91CHmrowgeiOBHwJegAuV40hwkc2gmuLLtmayJDT"
    MINIO_BUCKET_NAME: str = "documents"
    MINIO_SECURE: bool = False

    class Config:
        env_file = ".env"
        case_sensitive = True

    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

settings = Settings()

# Validate settings
assert settings.SECRET_KEY, "SECRET_KEY environment variable is not set"
assert settings.ACCESS_TOKEN_EXPIRE_MINUTES > 0, "ACCESS_TOKEN_EXPIRE_MINUTES must be positive" 