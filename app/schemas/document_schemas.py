from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime
from enum import Enum
from pydantic import validator


class DocTypeBase(BaseModel):
    type_name: str = Field(..., description="Name of the document type")
    description: Optional[str] = Field(None, description="Optional description of the document type")
    file_extensions: List[str] = Field(..., description="List of allowed file extensions")
    is_active: bool = Field(True, description="Whether the document type is active")


class DocTypeCreate(DocTypeBase):
    pass


class DocTypeResponse(DocTypeBase):
    id: int

    class Config:
        from_attributes = True


class FolderBase(BaseModel):
    folder_name: str = Field(..., description="Name of the folder")
    parent_folder_id: Optional[int] = Field(None, description="ID of parent folder, null for root")
    is_active: bool = Field(True, description="Whether the folder is active")


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
    folder_id: int = Field(..., description="ID of the folder containing the document")
    part_number_id: int = Field(..., description="Part number ID associated with the document")
    doc_type_id: int = Field(..., description="Document type ID")
    document_name: str = Field(..., description="Name of the document")
    description: Optional[str] = Field(None, description="Optional document description")


class DocumentCreate(DocumentBase):
    pass


class DocumentUpdate(BaseModel):
    folder_id: Optional[int] = Field(None, description="New folder ID")
    document_name: Optional[str] = Field(None, description="New document name")
    description: Optional[str] = Field(None, description="New document description")
    is_active: Optional[bool] = Field(None, description="Update active status")


class DocumentVersionCreate(BaseModel):
    version_number: str = Field(..., description="Version number string")
    metadata: Optional[Dict] = Field(default_factory=dict, description="Version metadata")


class DocumentVersionResponse(BaseModel):
    id: int
    version_number: str
    file_size: int
    checksum: str
    metadata: Optional[Dict]
    created_at: datetime
    created_by: int
    status: str

    class Config:
        from_attributes = True


class DocumentResponse(DocumentBase):
    id: int
    part_number: str
    created_at: datetime
    created_by: int
    is_active: bool
    latest_version: Optional[DocumentVersionResponse]
    versions: List[DocumentVersionResponse]

    class Config:
        from_attributes = True


class DocumentSearchResponse(BaseModel):
    total: int
    documents: List[DocumentResponse]
    skip: int
    limit: int

    class Config:
        from_attributes = True


class UploadDocumentRequest(BaseModel):
    folder_id: int
    part_number_id: int
    doc_type_id: int
    document_name: str
    description: Optional[str] = None
    version_number: str
    metadata: Optional[Dict] = Field(default_factory=dict)


class DocumentVersionUpdateRequest(BaseModel):
    status: str = Field(..., description="New status for the version")
    metadata: Optional[Dict] = Field(None, description="Updated metadata")


class DocumentVersionFileUpdate(BaseModel):
    version_number: Optional[str] = Field(None, description="New version number (optional)")
    metadata: Optional[Dict] = Field(None, description="Updated metadata (optional)")


class FolderOperation(BaseModel):
    destination_folder_id: int = Field(..., description="Destination folder ID")
    operation_type: str = Field(..., description="Operation type: 'copy' or 'cut'")

    @validator('operation_type')
    def validate_operation_type(cls, v):
        if v not in ['copy', 'cut']:
            raise ValueError('Operation type must be either "copy" or "cut"')
        return v


class FolderOperationResponse(BaseModel):
    success: bool
    message: str
    new_folder_id: Optional[int] = None

    class Config:
        from_attributes = True


class TopAccessedDocument(BaseModel):
    document_id: int
    document_name: str
    access_count: int
    doc_type: str


class FolderUtilization(BaseModel):
    folder_id: int
    folder_name: str
    document_count: int
    storage_mb: float


class DocumentActivitySummary(BaseModel):
    action_type: str
    count: int
    last_24h_count: int

    class Config:
        from_attributes = True


class DocumentMetrics(BaseModel):
    total_documents: int
    total_views: int
    total_downloads: int
    active_folders: int
    total_versions: int
    documents_by_type: Dict[str, int]
    storage_usage_mb: float
    recent_activity_count: int

    class Config:
        from_attributes = True