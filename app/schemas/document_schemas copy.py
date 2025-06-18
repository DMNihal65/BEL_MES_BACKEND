from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum

class DocTypeBase(BaseModel):
    type_name: str
    description: Optional[str] = None
    file_extensions: List[str]
    is_active: bool = True

class DocTypeCreate(DocTypeBase):
    pass

class DocTypeResponse(DocTypeBase):
    id: int

    class Config:
        from_attributes = True

class FolderBase(BaseModel):
    folder_name: str
    parent_folder_id: Optional[int] = None
    is_active: bool = True

class FolderCreate(FolderBase):
    pass

class FolderResponse(FolderBase):
    id: int
    folder_path: str
    created_at: datetime
    created_by: int

    class Config:
        from_attributes = True

class DocumentAction(str, Enum):
    VIEW = "view"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"

class DocumentBase(BaseModel):
    folder_id: int
    part_number_id: int
    doc_type_id: int
    document_name: str
    description: Optional[str] = None

class DocumentCreate(DocumentBase):
    pass

class DocumentUpdate(BaseModel):
    folder_id: Optional[int] = None
    document_name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None

class DocumentVersionCreate(BaseModel):
    version_number: str
    metadata: Optional[dict] = None

class DocumentVersionResponse(BaseModel):
    id: int
    version_number: str
    file_size: int
    checksum: str
    metadata: Optional[dict]
    created_at: datetime
    created_by: int
    status: str

    class Config:
        from_attributes = True

class DocumentResponse(DocumentBase):
    id: int
    created_at: datetime
    created_by: int
    is_active: bool
    latest_version: Optional[DocumentVersionResponse]
    versions: List[DocumentVersionResponse]

    class Config:
        from_attributes = True 