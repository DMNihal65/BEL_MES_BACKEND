from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class DocumentAction(str, Enum):
    VIEW = "VIEW"
    DOWNLOAD = "DOWNLOAD"
    UPDATE = "UPDATE"
    DELETE = "DELETE"

class FolderBase(BaseModel):
    name: str
    parent_folder_id: int | None = None

class FolderCreate(FolderBase):
    pass

class FolderUpdate(FolderBase):
    is_active: bool | None = None

class FolderResponse(BaseModel):
    id: int
    name: str
    path: str
    parent_folder_id: int | None
    created_at: datetime
    created_by_id: int
    is_active: bool

    class Config:
        from_attributes = True

class DocumentTypeBase(BaseModel):
    name: str
    description: str | None = None
    allowed_extensions: List[str]

class DocumentTypeCreate(DocumentTypeBase):
    pass

class DocumentTypeUpdate(DocumentTypeBase):
    is_active: bool | None = None

class DocumentTypeResponse(BaseModel):
    id: int
    name: str
    description: str | None
    allowed_extensions: List[str]
    is_active: bool

    class Config:
        from_attributes = True

class DocumentVersionBase(BaseModel):
    version_number: str
    metadata: Dict[str, Any] | None = None

class DocumentVersionCreate(DocumentVersionBase):
    pass

class DocumentVersionResponse(BaseModel):
    id: int
    document_id: int
    version_number: str
    minio_path: str
    file_size: int
    checksum: str
    created_at: datetime
    created_by_id: int
    is_active: bool
    metadata: Dict[str, Any] | None

    class Config:
        from_attributes = True

class DocumentBase(BaseModel):
    name: str
    folder_id: int
    doc_type_id: int
    description: str | None = None
    part_number: str | None = None
    production_order_id: int | None = None

class DocumentCreate(DocumentBase):
    pass

class DocumentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None

class DocumentResponse(BaseModel):
    id: int
    name: str
    folder_id: int
    doc_type_id: int
    description: str | None
    part_number: str | None
    production_order_id: int | None
    created_at: datetime
    created_by_id: int
    is_active: bool
    latest_version: DocumentVersionResponse | None

    class Config:
        from_attributes = True

class DocumentListResponse(BaseModel):
    total: int
    items: List[DocumentResponse]

    class Config:
        from_attributes = True

class DocumentUploadRequest(BaseModel):
    name: str
    folder_id: int
    doc_type_id: int
    description: str | None = None
    part_number: str | None = None
    production_order_id: int | None = None
    version_number: str = "1.0"  # Default version number
    metadata: Dict[str, Any] | None = None 