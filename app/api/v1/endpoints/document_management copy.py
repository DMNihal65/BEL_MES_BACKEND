from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from typing import List, Optional
from pony.orm import db_session, select, flush
from ....services.minio_service import MinioService
from ....models.document_management import DocFolder, DocType, Document, DocumentVersion
from ....models.user import User
from ....schemas.document_schemas import (
    DocTypeCreate, DocTypeResponse, FolderCreate, FolderResponse,
    DocumentCreate, DocumentResponse, DocumentVersionCreate, DocumentVersionResponse
)
from app.core.security import get_current_user, get_current_admin_user
import hashlib

router = APIRouter(prefix="/documents", tags=["Document Management"])
minio_service = MinioService()


# Document Type endpoints
@router.post("/types/", response_model=DocTypeResponse)
async def create_doc_type(
        doc_type: DocTypeCreate,
        current_user: User = Depends(get_current_admin_user)
):
    """Create a new document type"""
    with db_session:
        existing = DocType.get(type_name=doc_type.type_name)
        if existing:
            raise HTTPException(status_code=400, detail="Document type already exists")

        db_doc_type = DocType(
            type_name=doc_type.type_name,
            description=doc_type.description,
            file_extensions=doc_type.file_extensions,
            is_active=doc_type.is_active
        )
        return db_doc_type


# Folder endpoints
@router.post("/folders/", response_model=FolderResponse)
async def create_folder(
        folder: FolderCreate,
        current_user: User = Depends(get_current_user)
):
    """Create a new folder"""
    with db_session:
        try:
            parent_path = ""

            # Handle parent folder
            if folder.parent_folder_id:
                if folder.parent_folder_id == 0:  # Root folder
                    folder.parent_folder_id = None
                else:
                    parent = DocFolder.get(id=folder.parent_folder_id)
                    if not parent:
                        raise HTTPException(status_code=404, detail="Parent folder not found")
                    parent_path = parent.folder_path

            folder_path = f"{parent_path}/{folder.folder_name}".lstrip("/")

            # Check if folder path already exists
            existing_folder = DocFolder.get(folder_path=folder_path)
            if existing_folder:
                raise HTTPException(
                    status_code=400,
                    detail="A folder with this path already exists"
                )

            db_folder = DocFolder(
                parent_folder=folder.parent_folder_id if folder.parent_folder_id and folder.parent_folder_id != 0 else None,
                folder_name=folder.folder_name,
                folder_path=folder_path,
                created_by=current_user.id,
                is_active=folder.is_active
            )

            # Flush to ensure the folder is created
            flush()

            # Convert to dict before the session ends
            return {
                "id": db_folder.id,
                "folder_name": db_folder.folder_name,
                "folder_path": db_folder.folder_path,
                "parent_folder_id": db_folder.parent_folder,
                "created_at": db_folder.created_at,
                "created_by": db_folder.created_by,
                "is_active": db_folder.is_active
            }

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create folder: {str(e)}"
            )


# Document endpoints
@router.post("/upload/", response_model=DocumentResponse)
async def upload_document(
        file: UploadFile = File(...),
        document: DocumentCreate = Depends(),
        version: DocumentVersionCreate = Depends(),
        current_user: User = Depends(get_current_user)
):
    """Upload a new document with initial version"""
    try:
        # Read file content first to avoid session timeout
        contents = await file.read()
        checksum = hashlib.sha256(contents).hexdigest()
        file_size = len(contents)

        with db_session:
            # Validate folder and doc type
            folder = DocFolder.get(id=document.folder_id)
            if not folder:
                raise HTTPException(status_code=404, detail="Folder not found")

            doc_type = DocType.get(id=document.doc_type_id)
            if not doc_type:
                raise HTTPException(status_code=404, detail="Document type not found")

            # Validate file extension
            file_ext = file.filename.split('.')[-1].lower()
            if file_ext not in [ext.lower().strip('.') for ext in doc_type.file_extensions]:
                raise HTTPException(
                    status_code=400,
                    detail=f"File type .{file_ext} not allowed for this document type"
                )

            # Create document record
            db_document = Document(
                folder=folder,
                part_number_id=document.part_number_id,
                doc_type=doc_type,
                document_name=document.document_name,
                description=document.description,
                created_by=current_user.id
            )

            # Flush to get document ID
            flush()

            # Generate MinIO object path
            object_name = minio_service.generate_object_path(
                str(document.part_number_id),
                doc_type.type_name,
                db_document.id,
                1  # First version
            )

            try:
                # Upload to MinIO
                minio_result = minio_service.upload_file(
                    file=contents,
                    object_name=object_name,
                    content_type=file.content_type or "application/octet-stream"
                )
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to upload file to storage: {str(e)}"
                )

            # Create version record
            db_version = DocumentVersion(
                document=db_document,
                version_number=version.version_number,
                minio_object_id=object_name,
                file_size=file_size,
                checksum=checksum,
                metadata=version.metadata,
                created_by=current_user.id,
                status="active"
            )

            # Set as latest version
            db_document.latest_version = db_version

            # Convert to dict before session ends
            return {
                "id": db_document.id,
                "folder_id": db_document.folder.id,
                "part_number_id": db_document.part_number_id,
                "doc_type_id": db_document.doc_type.id,
                "document_name": db_document.document_name,
                "description": db_document.description,
                "created_at": db_document.created_at,
                "created_by": db_document.created_by,
                "is_active": db_document.is_active,
                "latest_version": {
                    "id": db_version.id,
                    "version_number": db_version.version_number,
                    "file_size": db_version.file_size,
                    "checksum": db_version.checksum,
                    "metadata": db_version.metadata,
                    "created_at": db_version.created_at,
                    "created_by": db_version.created_by,
                    "status": db_version.status
                },
                "versions": [{
                    "id": db_version.id,
                    "version_number": db_version.version_number,
                    "file_size": db_version.file_size,
                    "checksum": db_version.checksum,
                    "metadata": db_version.metadata,
                    "created_at": db_version.created_at,
                    "created_by": db_version.created_by,
                    "status": db_version.status
                }]
            }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while processing the document: {str(e)}"
        )

# Add more endpoints for:
# - Downloading documents
# - Updating documents
# - Creating new versions
# - Managing folders
# - Document search
# - Access logs