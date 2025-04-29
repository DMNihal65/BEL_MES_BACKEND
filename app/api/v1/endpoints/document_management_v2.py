import os
import tempfile

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Path, status, Form
from typing import List, Union, Annotated, Optional, Dict, Any
import io

from starlette.responses import FileResponse

from ....models import DocType, Operation, DocFolder, Document, DocumentAccessLog, DocumentVersion
from ....schemas.document_management_v2 import *
from ....models.document_management_v2 import *
from ....models.user import User
from ....core.security import get_current_user
from ....services.minio_service import MinioService
from pony.orm import db_session, commit, TransactionError, select, desc, count
import hashlib
import json
from typing import Optional
from datetime import datetime
from fastapi.responses import StreamingResponse
from enum import Enum
from fastapi.logger import logger

router = APIRouter()
minio = MinioService()


# Add these constants at the top of the file
class DocumentTypes(str, Enum):
    MPP = "MPP"
    OARC = "OARC"
    ENGINEERING_DRAWING = "ENGINEERING_DRAWING"
    IPID = "IPID"
    MACHINE_DOCUMENT = "MACHINE_DOCUMENT"
    CNC_PROGRAM = "CNC_PROGRAM"


# Move these static routes before any routes with path parameters
@router.get("/documents/stats")
async def get_document_stats(
        current_user=Depends(get_current_user)
):
    """Get document statistics"""
    try:
        with db_session:
            total_documents = select(d for d in DocumentV2 if d.is_active).count()
            total_versions = select(v for v in DocumentVersionV2 if v.is_active).count()
            docs_by_type = select((d.doc_type.name, count(d))
                                  for d in DocumentV2
                                  if d.is_active).fetch()

            return {
                "total_documents": total_documents,
                "total_versions": total_versions,
                "documents_by_type": dict(docs_by_type)
            }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/documents/by-multiple-part-numbers", response_model=List[DocumentResponse])
async def get_documents_by_part_numbers(
        part_numbers: List[str] = Query(..., description="List of part numbers"),
        doc_type_id: int | None = Query(default=None, description="Filter by document type"),
        current_user=Depends(get_current_user)
):
    """Get documents for multiple part numbers"""
    try:
        with db_session:
            query = select(d for d in DocumentV2
                           if d.part_number in part_numbers and d.is_active)

            if doc_type_id:
                query = query.filter(lambda d: d.doc_type.id == doc_type_id)

            documents = list(query.order_by(desc(DocumentV2.created_at)))
            return [doc.to_dict() for doc in documents]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/documents/by-folder-recursive/{folder_id}", response_model=List[DocumentResponse])
async def get_documents_by_folder_recursive(
        folder_id: int,
        doc_type_id: int | None = Query(default=None, description="Filter by document type"),
        current_user=Depends(get_current_user)
):
    """Get all documents in a folder and its subfolders"""
    try:
        with db_session:
            folder = FolderV2.get(id=folder_id)
            if not folder:
                raise HTTPException(status_code=404, detail="Folder not found")

            # Get all subfolder IDs recursively
            def get_subfolder_ids(folder):
                ids = [folder.id]
                for child in folder.child_folders:
                    ids.extend(get_subfolder_ids(child))
                return ids

            folder_ids = get_subfolder_ids(folder)

            # Query documents
            query = select(d for d in DocumentV2
                           if d.folder.id in folder_ids and d.is_active)

            if doc_type_id:
                query = query.filter(lambda d: d.doc_type.id == doc_type_id)

            documents = list(query.order_by(desc(DocumentV2.created_at)))

            # Format response manually to match DocumentResponse model
            return [
                {
                    "id": doc.id,
                    "name": doc.name,
                    "folder_id": doc.folder.id,
                    "doc_type_id": doc.doc_type.id,
                    "description": doc.description,
                    "part_number": doc.part_number,
                    "production_order_id": doc.production_order.id if doc.production_order else None,
                    "created_at": doc.created_at,
                    "created_by_id": doc.created_by.id,
                    "is_active": doc.is_active,
                    "latest_version": {
                        "id": doc.latest_version.id,
                        "document_id": doc.id,
                        "version_number": doc.latest_version.version_number,
                        "minio_path": doc.latest_version.minio_path,
                        "file_size": doc.latest_version.file_size,
                        "checksum": doc.latest_version.checksum,
                        "created_at": doc.latest_version.created_at,
                        "created_by_id": doc.latest_version.created_by.id,
                        "is_active": doc.latest_version.is_active,
                        "metadata": doc.latest_version.metadata
                    } if doc.latest_version else None
                }
                for doc in documents
            ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


# Folder endpoints
@router.post("/folders/", response_model=FolderResponse)
async def create_folder(
        folder: FolderCreate,
        current_user=Depends(get_current_user)
):
    """Create a new folder"""
    try:
        with db_session:
            # Get user within this transaction
            user = User.get(id=current_user.id)
            if not user:
                return {"status_code": status.HTTP_404_NOT_FOUND, "detail": "User not found"}

            # Generate folder path
            parent_path = ""
            if folder.parent_folder_id:
                parent = FolderV2.get(id=folder.parent_folder_id)
                if not parent:
                    return {"status_code": status.HTTP_404_NOT_FOUND, "detail": "Parent folder not found"}
                parent_path = parent.path

            folder_path = f"{parent_path}/{folder.name}".lstrip("/")

            # Create folder
            new_folder = FolderV2(
                name=folder.name,
                path=folder_path,
                parent_folder=parent if folder.parent_folder_id else None,  # Use parent object directly
                created_by=user
            )
            commit()

            # Return the created folder data
            return {
                "id": new_folder.id,
                "name": new_folder.name,
                "path": new_folder.path,
                "parent_folder_id": new_folder.parent_folder.id if new_folder.parent_folder else None,
                "created_at": new_folder.created_at,
                "created_by_id": new_folder.created_by.id,
                "is_active": new_folder.is_active
            }
    except TransactionError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database transaction error: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/folders/", response_model=List[FolderResponse])
async def list_folders(
        parent_id: int | None = None,
        current_user=Depends(get_current_user)
):
    """List folders, optionally filtered by parent folder"""
    try:
        with db_session:
            if parent_id:
                folders = list(FolderV2.select(lambda f: f.parent_folder.id == parent_id))
            else:
                folders = list(FolderV2.select(lambda f: f.parent_folder is None))

            return [
                {
                    "id": f.id,
                    "name": f.name,
                    "path": f.path,
                    "parent_folder_id": f.parent_folder.id if f.parent_folder else None,
                    "created_at": f.created_at,
                    "created_by_id": f.created_by.id,
                    "is_active": f.is_active
                }
                for f in folders
            ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


# Document Type endpoints
@router.post("/document-types/", response_model=DocumentTypeResponse)
async def create_document_type(
        doc_type: DocumentTypeCreate,
        current_user=Depends(get_current_user)
):
    """Create a new document type"""
    with db_session:
        new_doc_type = DocumentTypeV2(
            name=doc_type.name,
            description=doc_type.description,
            allowed_extensions=doc_type.allowed_extensions
        )
        commit()
        return new_doc_type


@router.get("/document-types/", response_model=List[DocumentTypeResponse])
async def list_document_types(
        current_user=Depends(get_current_user)
):
    """List all active document types"""
    try:
        with db_session:
            types = list(DocumentTypeV2.select(lambda dt: dt.is_active))
            return [
                {
                    "id": dt.id,
                    "name": dt.name,
                    "description": dt.description,
                    "allowed_extensions": dt.allowed_extensions,
                    "is_active": dt.is_active
                }
                for dt in types
            ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


# Document endpoints
@router.post("/documents/upload/", response_model=DocumentResponse)
async def upload_document(
        file: UploadFile = File(...),
        name: str = Form(...),
        folder_id: int = Form(...),
        doc_type_id: int = Form(...),
        description: str | None = Form(default=None),
        part_number: str | None = Form(default=None),
        production_order_id: str = Form(default=""),
        version_number: str = Form(default="1.0"),
        metadata: str = Form(default="{}"),
        current_user: User = Depends(get_current_user)
):
    """Create a new document with initial version"""
    try:
        with db_session:
            # Get user within this transaction
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # Convert production_order_id to int if not empty
            prod_order_id = None
            if production_order_id and production_order_id.strip():
                try:
                    prod_order_id = int(production_order_id)
                    # Verify the order exists
                    order = Order.get(id=prod_order_id)
                    if not order:
                        raise HTTPException(
                            status_code=404,
                            detail=f"Production order with ID {prod_order_id} not found"
                        )
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid production order ID format"
                    )

            # Validate folder and document type
            folder = FolderV2.get(id=folder_id)
            doc_type = DocumentTypeV2.get(id=doc_type_id)

            if not folder or not doc_type:
                raise HTTPException(status_code=404, detail="Folder or document type not found")

            # Validate file extension
            file_ext = file.filename.split('.')[-1].lower()
            if file_ext not in [ext.lower().strip('.') for ext in doc_type.allowed_extensions]:
                raise HTTPException(
                    status_code=400,
                    detail=f"File type .{file_ext} not allowed for this document type"
                )

            try:
                metadata_dict = json.loads(metadata)
            except json.JSONDecodeError:
                metadata_dict = {}

            # Read file content
            file_content = await file.read()
            checksum = hashlib.sha256(file_content).hexdigest()
            file_size = len(file_content)

            # Create document with production order if provided
            new_doc = DocumentV2(
                name=name,
                folder=folder,
                doc_type=doc_type,
                description=description,
                part_number=part_number if part_number else None,
                production_order=order if prod_order_id else None,
                created_by=user
            )
            commit()

            # Generate MinIO path
            minio_path = f"documents/v2/{folder.path}/{new_doc.id}/v{version_number}/{file.filename}"

            try:
                # Upload to MinIO first
                file.file.seek(0)
                minio.upload_file(
                    file=file.file,
                    object_name=minio_path,
                    content_type=file.content_type or "application/octet-stream"
                )
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to upload file: {str(e)}"
                )

            # Now create the version
            version = DocumentVersionV2(
                document=new_doc,
                version_number=version_number,
                minio_path=minio_path,
                file_size=file_size,
                checksum=checksum,
                created_by=user,
                metadata=metadata_dict
            )
            commit()  # Commit the version

            # Update document with latest version in a separate step
            new_doc.latest_version = version
            commit()  # Final commit

            # Create access log
            DocumentAccessLogV2(
                document=new_doc,
                version=version,
                user=user,
                action_type=DocumentAction.UPDATE,
                ip_address="0.0.0.0"
            )
            commit()

            return {
                "id": new_doc.id,
                "name": new_doc.name,
                "folder_id": new_doc.folder.id,
                "doc_type_id": new_doc.doc_type.id,
                "description": new_doc.description,
                "part_number": new_doc.part_number,
                "production_order_id": new_doc.production_order.id if new_doc.production_order else None,
                "created_at": new_doc.created_at,
                "created_by_id": new_doc.created_by.id,
                "is_active": new_doc.is_active,
                "latest_version": {
                    "id": version.id,
                    "document_id": version.document.id,
                    "version_number": version.version_number,
                    "minio_path": version.minio_path,
                    "file_size": version.file_size,
                    "checksum": version.checksum,
                    "created_at": version.created_at,
                    "created_by_id": version.created_by.id,
                    "is_active": version.is_active,
                    "metadata": version.metadata
                } if version else None
            }

    except TransactionError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database transaction error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/documents/", response_model=DocumentListResponse)
async def list_documents(
        folder_id: int | None = None,
        part_number: str | None = None,
        production_order_id: int | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=100),
        current_user=Depends(get_current_user)
):
    """List documents with optional filters and pagination"""
    try:
        with db_session:
            # Start with base query
            base_query = DocumentV2.select()

            # Apply filters one by one
            if folder_id:
                base_query = base_query.filter(lambda d: d.folder.id == folder_id)
            if part_number:
                base_query = base_query.filter(lambda d: d.part_number == part_number)
            if production_order_id:
                base_query = base_query.filter(
                    lambda d: d.production_order and d.production_order.id == production_order_id)

            # Get total count
            total = base_query.count()

            # Apply pagination and ordering
            documents = list(base_query
                             .order_by(lambda d: desc(d.created_at))
                             .limit(page_size, offset=(page - 1) * page_size))

            # Format response
            return {
                "total": total,
                "items": [
                    {
                        "id": doc.id,
                        "name": doc.name,
                        "folder_id": doc.folder.id,
                        "doc_type_id": doc.doc_type.id,
                        "description": doc.description,
                        "part_number": doc.part_number,
                        "production_order_id": doc.production_order.id if doc.production_order else None,
                        "created_at": doc.created_at,
                        "created_by_id": doc.created_by.id,
                        "is_active": doc.is_active,
                        "latest_version": {
                            "id": doc.latest_version.id,
                            "document_id": doc.latest_version.document.id,
                            "version_number": doc.latest_version.version_number,
                            "minio_path": doc.latest_version.minio_path,
                            "file_size": doc.latest_version.file_size,
                            "checksum": doc.latest_version.checksum,
                            "created_at": doc.latest_version.created_at,
                            "created_by_id": doc.latest_version.created_by.id,
                            "is_active": doc.latest_version.is_active,
                            "metadata": doc.latest_version.metadata
                        } if doc.latest_version else None
                    }
                    for doc in documents
                ]
            }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
        document_id: int,
        current_user=Depends(get_current_user)
):
    """Get a specific document by ID"""
    try:
        with db_session:
            document = DocumentV2.get(id=document_id)
            if not document:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found"
                )

            # Format response manually to avoid session closing issues
            return {
                "id": document.id,
                "name": document.name,
                "folder_id": document.folder.id,
                "doc_type_id": document.doc_type.id,
                "description": document.description,
                "part_number": document.part_number,
                "production_order_id": document.production_order.id if document.production_order else None,
                "created_at": document.created_at,
                "created_by_id": document.created_by.id,
                "is_active": document.is_active,
                "latest_version": {
                    "id": document.latest_version.id,
                    "document_id": document.id,
                    "version_number": document.latest_version.version_number,
                    "minio_path": document.latest_version.minio_path,
                    "file_size": document.latest_version.file_size,
                    "checksum": document.latest_version.checksum,
                    "created_at": document.latest_version.created_at,
                    "created_by_id": document.latest_version.created_by.id,
                    "is_active": document.latest_version.is_active,
                    "metadata": document.latest_version.metadata
                } if document.latest_version else None
            }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.put("/documents/{document_id}", response_model=DocumentResponse)
async def update_document(
        document_id: int,
        document: DocumentUpdate,
        current_user=Depends(get_current_user)
):
    """Update a document's metadata"""
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found"
                )

            doc = DocumentV2.get(id=document_id)
            if not doc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found"
                )

            # Update fields
            if document.name is not None:
                doc.name = document.name
            if document.description is not None:
                doc.description = document.description
            if document.is_active is not None:
                doc.is_active = document.is_active

            commit()
            return doc
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/documents/{document_id}/versions", response_model=List[DocumentVersionResponse])
async def list_document_versions(
        document_id: int,
        current_user=Depends(get_current_user)
):
    """List all versions of a document"""
    try:
        with db_session:
            document = DocumentV2.get(id=document_id)
            if not document:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found"
                )

            # Format response manually to avoid session issues
            versions = list(document.versions)
            return [
                {
                    "id": version.id,
                    "document_id": version.document.id,
                    "version_number": version.version_number,
                    "minio_path": version.minio_path,
                    "file_size": version.file_size,
                    "checksum": version.checksum,
                    "created_at": version.created_at,
                    "created_by_id": version.created_by.id,
                    "is_active": version.is_active,
                    "metadata": version.metadata
                }
                for version in versions
            ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.post("/documents/{document_id}/versions", response_model=DocumentVersionResponse)
async def create_document_version(
        document_id: int,
        file: UploadFile = File(...),
        version_number: str = Form(...),
        metadata: str = Form(default="{}"),
        current_user=Depends(get_current_user)
):
    """Create a new version for an existing document"""
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found"
                )

            document = DocumentV2.get(id=document_id)
            if not document:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found"
                )

            # Validate file extension
            file_ext = file.filename.split('.')[-1].lower()
            if file_ext not in [ext.lower().strip('.') for ext in document.doc_type.allowed_extensions]:
                raise HTTPException(
                    status_code=400,
                    detail=f"File type .{file_ext} not allowed for this document type"
                )

            # Parse metadata
            try:
                metadata_dict = json.loads(metadata)
            except json.JSONDecodeError:
                metadata_dict = {}

            # Upload file to MinIO
            file_content = await file.read()
            checksum = hashlib.sha256(file_content).hexdigest()

            # Generate MinIO path
            minio_path = f"documents/v2/{document.folder.path}/{document.id}/v{version_number}/{file.filename}"

            try:
                # Upload to MinIO first
                file.file.seek(0)
                minio.upload_file(
                    file=file.file,
                    object_name=minio_path,
                    content_type=file.content_type or "application/octet-stream"
                )
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to upload file: {str(e)}"
                )

            # Create version
            new_version = DocumentVersionV2(
                document=document,
                version_number=version_number,
                minio_path=minio_path,
                file_size=len(file_content),
                checksum=checksum,
                metadata=metadata_dict,
                created_by=user
            )

            # Update latest version
            document.latest_version = new_version

            # Create access log
            DocumentAccessLogV2(
                document=document,
                version=new_version,
                user=user,
                action_type=DocumentAction.UPDATE,
                ip_address="0.0.0.0"
            )

            commit()

            # Return formatted response
            return {
                "id": new_version.id,
                "document_id": new_version.document.id,
                "version_number": new_version.version_number,
                "minio_path": new_version.minio_path,
                "file_size": new_version.file_size,
                "checksum": new_version.checksum,
                "created_at": new_version.created_at,
                "created_by_id": new_version.created_by.id,
                "is_active": new_version.is_active,
                "metadata": new_version.metadata
            }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/documents/{document_id}/download")
async def download_document(
        document_id: int,
        version_id: int | None = None,
        current_user=Depends(get_current_user)
):
    """Download a specific version or the latest version of a document"""
    try:
        with db_session:
            document = DocumentV2.get(id=document_id)
            if not document:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found"
                )

            # Get requested version or latest version
            version = None
            if version_id:
                version = DocumentVersionV2.get(id=version_id, document=document)
                if not version:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Version not found"
                    )
            else:
                version = document.latest_version

            if not version:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No version available"
                )

            # Log access
            DocumentAccessLogV2(
                document=document,
                version=version,
                user=User.get(id=current_user.id),
                action_type="DOWNLOAD",
                ip_address="0.0.0.0"  # You might want to get the actual IP
            )

            # Get file from MinIO
            file_data = minio.download_file(version.minio_path)

            return StreamingResponse(
                file_data,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{document.name}"'
                }
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/documents/{document_id}/download-latest")
async def download_latest_document(
        document_id: int,
        current_user=Depends(get_current_user)
):
    """Download the latest version of a document"""
    try:
        with db_session:
            document = DocumentV2.get(id=document_id)
            if not document:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found"
                )

            if not document.latest_version:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No version available for this document"
                )

            # Log access
            DocumentAccessLogV2(
                document=document,
                version=document.latest_version,
                user=User.get(id=current_user.id),
                action_type=DocumentAction.DOWNLOAD,
                ip_address="0.0.0.0"
            )
            commit()

            try:
                # Get file from MinIO
                file_data = minio.download_file(document.latest_version.minio_path)

                # Get file extension from minio path
                file_extension = document.latest_version.minio_path.split('.')[
                    -1] if '.' in document.latest_version.minio_path else ''
                filename = f"{document.name}.{file_extension}" if file_extension else document.name

                # Determine content type based on file extension
                content_type = "application/octet-stream"
                if file_extension.lower() in ['pdf']:
                    content_type = "application/pdf"
                elif file_extension.lower() in ['doc', 'docx']:
                    content_type = "application/msword"
                elif file_extension.lower() in ['xls', 'xlsx']:
                    content_type = "application/vnd.ms-excel"

                return StreamingResponse(
                    file_data,
                    media_type=content_type,
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"'
                    }
                )
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to download file: {str(e)}"
                )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/documents/by-part-number/{part_number}", response_model=List[DocumentResponse])
async def get_documents_by_part_number(
        part_number: str = Path(..., description="Part number to search for"),
        doc_type_id: int | None = Query(default=None, description="Filter by document type"),
        current_user=Depends(get_current_user)
):
    """Get all documents for a specific part number with optional document type filter"""
    try:
        with db_session:
            query = select(d for d in DocumentV2 if d.part_number == part_number and d.is_active)

            if doc_type_id:
                query = query.filter(lambda d: d.doc_type.id == doc_type_id)

            documents = list(query.order_by(desc(DocumentV2.created_at)))

            return [
                {
                    "id": doc.id,
                    "name": doc.name,
                    "folder_id": doc.folder.id,
                    "doc_type_id": doc.doc_type.id,
                    "description": doc.description,
                    "part_number": doc.part_number,
                    "production_order_id": doc.production_order.id if doc.production_order else None,
                    "created_at": doc.created_at,
                    "created_by_id": doc.created_by.id,
                    "is_active": doc.is_active,
                    "latest_version": {
                        "id": doc.latest_version.id,
                        "document_id": doc.id,
                        "version_number": doc.latest_version.version_number,
                        "minio_path": doc.latest_version.minio_path,
                        "file_size": doc.latest_version.file_size,
                        "checksum": doc.latest_version.checksum,
                        "created_at": doc.latest_version.created_at,
                        "created_by_id": doc.latest_version.created_by.id,
                        "is_active": doc.latest_version.is_active,
                        "metadata": doc.latest_version.metadata
                    } if doc.latest_version else None
                }
                for doc in documents
            ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/documents/latest/{part_number}/{doc_type_id}", response_model=DocumentResponse)
async def get_latest_document(
        part_number: str = Path(..., description="Part number to search for"),
        doc_type_id: int = Path(..., description="Document type ID"),
        current_user=Depends(get_current_user)
):
    """Get the latest document for a specific part number and document type"""
    try:
        with db_session:
            document = DocumentV2.select(
                lambda d: d.part_number == part_number and
                          d.doc_type.id == doc_type_id and
                          d.is_active
            ).order_by(lambda d: desc(d.created_at)).first()

            if not document:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No document found for the given part number and document type"
                )

            # Log access
            DocumentAccessLogV2(
                document=document,
                version=document.latest_version,
                user=User.get(id=current_user.id),
                action_type=DocumentAction.VIEW,
                ip_address="0.0.0.0"
            )
            commit()

            return {
                "id": document.id,
                "name": document.name,
                "folder_id": document.folder.id,
                "doc_type_id": document.doc_type.id,
                "description": document.description,
                "part_number": document.part_number,
                "production_order_id": document.production_order.id if document.production_order else None,
                "created_at": document.created_at,
                "created_by_id": document.created_by.id,
                "is_active": document.is_active,
                "latest_version": {
                    "id": document.latest_version.id,
                    "document_id": document.id,
                    "version_number": document.latest_version.version_number,
                    "minio_path": document.latest_version.minio_path,
                    "file_size": document.latest_version.file_size,
                    "checksum": document.latest_version.checksum,
                    "created_at": document.latest_version.created_at,
                    "created_by_id": document.latest_version.created_by.id,
                    "is_active": document.latest_version.is_active,
                    "metadata": document.latest_version.metadata
                } if document.latest_version else None
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/documents/by-production-order/{production_order_id}", response_model=List[DocumentResponse])
async def get_documents_by_production_order(
        production_order_id: int = Path(..., description="Production order ID"),
        doc_type_id: int | None = Query(default=None, description="Filter by document type"),
        current_user=Depends(get_current_user)
):
    """Get all documents for a specific production order with optional document type filter"""
    try:
        with db_session:
            query = select(d for d in DocumentV2 if d.production_order.id == production_order_id and d.is_active)

            if doc_type_id:
                query = query.filter(lambda d: d.doc_type.id == doc_type_id)

            documents = list(query.order_by(desc(DocumentV2.created_at)))

            return [
                {
                    "id": doc.id,
                    "name": doc.name,
                    "folder_id": doc.folder.id,
                    "doc_type_id": doc.doc_type.id,
                    "description": doc.description,
                    "part_number": doc.part_number,
                    "production_order_id": doc.production_order.id if doc.production_order else None,
                    "created_at": doc.created_at,
                    "created_by_id": doc.created_by.id,
                    "is_active": doc.is_active,
                    "latest_version": {
                        "id": doc.latest_version.id,
                        "document_id": doc.id,
                        "version_number": doc.latest_version.version_number,
                        "minio_path": doc.latest_version.minio_path,
                        "file_size": doc.latest_version.file_size,
                        "checksum": doc.latest_version.checksum,
                        "created_at": doc.latest_version.created_at,
                        "created_by_id": doc.latest_version.created_by.id,
                        "is_active": doc.latest_version.is_active,
                        "metadata": doc.latest_version.metadata
                    } if doc.latest_version else None
                }
                for doc in documents
            ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/documents/search/", response_model=List[DocumentResponse])
async def search_documents(
        query: str = Query(..., min_length=3, description="Search query (min 3 characters)"),
        doc_type_id: int | None = Query(default=None, description="Filter by document type"),
        folder_id: int | None = Query(default=None, description="Filter by folder"),
        current_user=Depends(get_current_user)
):
    """Search documents by name, description, or part number"""
    try:
        with db_session:
            search_query = select(d for d in DocumentV2
                                  if d.is_active and (
                                          query.lower() in d.name.lower() or
                                          (d.description and query.lower() in d.description.lower()) or
                                          (d.part_number and query.lower() in d.part_number.lower())
                                  ))

            if doc_type_id:
                search_query = search_query.filter(lambda d: d.doc_type.id == doc_type_id)

            if folder_id:
                search_query = search_query.filter(lambda d: d.folder.id == folder_id)

            documents = list(search_query.order_by(desc(DocumentV2.created_at)))

            return [
                {
                    "id": doc.id,
                    "name": doc.name,
                    "folder_id": doc.folder.id,
                    "doc_type_id": doc.doc_type.id,
                    "description": doc.description,
                    "part_number": doc.part_number,
                    "production_order_id": doc.production_order.id if doc.production_order else None,
                    "created_at": doc.created_at,
                    "created_by_id": doc.created_by.id,
                    "is_active": doc.is_active,
                    "latest_version": {
                        "id": doc.latest_version.id,
                        "document_id": doc.id,
                        "version_number": doc.latest_version.version_number,
                        "minio_path": doc.latest_version.minio_path,
                        "file_size": doc.latest_version.file_size,
                        "checksum": doc.latest_version.checksum,
                        "created_at": doc.latest_version.created_at,
                        "created_by_id": doc.latest_version.created_by.id,
                        "is_active": doc.latest_version.is_active,
                        "metadata": doc.latest_version.metadata
                    } if doc.latest_version else None
                }
                for doc in documents
            ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.put("/documents/{document_id}/version/{version_id}", response_model=DocumentVersionResponse)
async def update_document_version(
        document_id: int,
        version_id: int,
        file: UploadFile = File(...),
        metadata: str = Form(default="{}"),
        current_user=Depends(get_current_user)
):
    """Update an existing document version with a new file"""
    try:
        with db_session:
            document = DocumentV2.get(id=document_id)
            if not document:
                raise HTTPException(status_code=404, detail="Document not found")

            version = DocumentVersionV2.get(id=version_id, document=document)
            if not version:
                raise HTTPException(status_code=404, detail="Version not found")

            # Validate file extension
            file_ext = file.filename.split('.')[-1].lower()
            if file_ext not in [ext.lower().strip('.') for ext in document.doc_type.allowed_extensions]:
                raise HTTPException(
                    status_code=400,
                    detail=f"File type .{file_ext} not allowed for this document type"
                )

            # Read and validate new file
            file_content = await file.read()
            checksum = hashlib.sha256(file_content).hexdigest()

            # Upload to MinIO
            try:
                file.file.seek(0)
                minio.upload_file(
                    file=file.file,
                    object_name=version.minio_path,  # Use same path to override
                    content_type=file.content_type or "application/octet-stream"
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

            # Update version metadata
            try:
                metadata_dict = json.loads(metadata)
            except json.JSONDecodeError:
                metadata_dict = {}

            version.file_size = len(file_content)
            version.checksum = checksum
            version.metadata = metadata_dict

            # Log update
            DocumentAccessLogV2(
                document=document,
                version=version,
                user=User.get(id=current_user.id),
                action_type=DocumentAction.UPDATE,
                ip_address="0.0.0.0"
            )

            commit()
            return {
                "id": version.id,
                "document_id": version.document.id,
                "version_number": version.version_number,
                "minio_path": version.minio_path,
                "file_size": version.file_size,
                "checksum": version.checksum,
                "created_at": version.created_at,
                "created_by_id": version.created_by.id,
                "is_active": version.is_active,
                "metadata": version.metadata
            }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.post("/documents/move/{document_id}", response_model=DocumentResponse)
async def move_document(
        document_id: int,
        folder_id: int = Query(..., description="Target folder ID"),
        current_user=Depends(get_current_user)
):
    """Move a document to a different folder"""
    try:
        with db_session:
            document = DocumentV2.get(id=document_id)
            if not document:
                raise HTTPException(status_code=404, detail="Document not found")

            target_folder = FolderV2.get(id=folder_id)
            if not target_folder:
                raise HTTPException(status_code=404, detail="Target folder not found")

            # Update document's folder
            document.folder = target_folder

            # Log move operation
            DocumentAccessLogV2(
                document=document,
                version=document.latest_version,
                user=User.get(id=current_user.id),
                action_type=DocumentAction.UPDATE,
                ip_address="0.0.0.0"
            )

            commit()
            return document
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.post("/document-types/bulk-create", response_model=List[DocumentTypeResponse])
async def bulk_create_document_types(
        doc_types: List[DocumentTypeCreate],
        current_user=Depends(get_current_user)
):
    """Create multiple document types at once"""
    try:
        with db_session:
            created_types = []
            for doc_type in doc_types:
                new_type = DocumentTypeV2(
                    name=doc_type.name,
                    description=doc_type.description,
                    allowed_extensions=doc_type.allowed_extensions
                )
                created_types.append(new_type)
            commit()
            return created_types
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.post("/documents/upload-by-type", response_model=DocumentResponse)
async def upload_document_by_type(
        file: UploadFile = File(...),
        name: str = Form(...),
        doc_type: DocumentTypes = Form(...),
        part_number: str = Form(...),
        description: str | None = Form(default=None),
        version_number: str = Form(default="1.0"),
        metadata: str = Form(default="{}"),
        current_user: User = Depends(get_current_user)
):
    """Upload document for specific document type and part number"""
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # Get or create document type
            doc_type_extensions = {
                DocumentTypes.MPP: [".pdf", ".doc", ".docx"],
                DocumentTypes.OARC: [".pdf"],
                DocumentTypes.ENGINEERING_DRAWING: [".pdf", ".dwg", ".dxf"],
                DocumentTypes.IPID: [".pdf"]
            }

            # Get or create document type
            doc_type_obj = DocumentTypeV2.get(name=doc_type.value)
            if not doc_type_obj:
                doc_type_obj = DocumentTypeV2(
                    name=doc_type.value,
                    description=f"{doc_type.value} Document Type",
                    allowed_extensions=doc_type_extensions[doc_type]
                )
                commit()

            # Get or create root folder for document types
            root_folder = FolderV2.get(name="Document Types", parent_folder=None)
            if not root_folder:
                root_folder = FolderV2(
                    name="Document Types",
                    path="Document Types",
                    created_by=user
                )
                commit()

            # Get or create document type folder
            doc_type_folder = FolderV2.get(lambda f: f.name == doc_type.value and f.parent_folder == root_folder)
            if not doc_type_folder:
                doc_type_folder = FolderV2(
                    name=doc_type.value,
                    path=f"Document Types/{doc_type.value}",
                    parent_folder=root_folder,
                    created_by=user
                )
                commit()

            # Get or create part number folder
            part_folder = FolderV2.get(lambda f: f.name == part_number and f.parent_folder == doc_type_folder)
            if not part_folder:
                part_folder = FolderV2(
                    name=part_number,
                    path=f"Document Types/{doc_type.value}/{part_number}",
                    parent_folder=doc_type_folder,
                    created_by=user
                )
                commit()

            # Validate file extension
            file_ext = file.filename.split('.')[-1].lower()
            if f".{file_ext}" not in doc_type_extensions[doc_type]:
                raise HTTPException(
                    status_code=400,
                    detail=f"File type .{file_ext} not allowed for {doc_type.value}"
                )

            # Create document
            new_doc = DocumentV2(
                name=name,
                folder=part_folder,  # Use the part number folder
                doc_type=doc_type_obj,
                description=description,
                part_number=part_number,
                created_by=user
            )
            commit()

            # Handle file upload and version creation
            file_content = await file.read()
            checksum = hashlib.sha256(file_content).hexdigest()
            minio_path = f"documents/v2/{part_folder.path}/{new_doc.id}/v{version_number}/{file.filename}"

            try:
                file.file.seek(0)
                minio.upload_file(
                    file=file.file,
                    object_name=minio_path,
                    content_type=file.content_type or "application/octet-stream"
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

            # Create version
            version = DocumentVersionV2(
                document=new_doc,
                version_number=version_number,
                minio_path=minio_path,
                file_size=len(file_content),
                checksum=checksum,
                created_by=user,
                metadata=json.loads(metadata)
            )
            new_doc.latest_version = version

            # Create access log
            DocumentAccessLogV2(
                document=new_doc,
                version=version,
                user=user,
                action_type=DocumentAction.UPDATE,
                ip_address="0.0.0.0"
            )

            commit()

            return {
                "id": new_doc.id,
                "name": new_doc.name,
                "folder_id": new_doc.folder.id,
                "doc_type_id": new_doc.doc_type.id,
                "description": new_doc.description,
                "part_number": new_doc.part_number,
                "production_order_id": None,
                "created_at": new_doc.created_at,
                "created_by_id": new_doc.created_by.id,
                "is_active": new_doc.is_active,
                "latest_version": {
                    "id": version.id,
                    "document_id": new_doc.id,
                    "version_number": version.version_number,
                    "minio_path": version.minio_path,
                    "file_size": version.file_size,
                    "checksum": version.checksum,
                    "created_at": version.created_at,
                    "created_by_id": version.created_by.id,
                    "is_active": version.is_active,
                    "metadata": version.metadata
                }
            }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/documents/download-latest/{part_number}/{doc_type}")
async def download_latest_document_by_type(
        part_number: str,
        doc_type: DocumentTypes,
        current_user: User = Depends(get_current_user)
):
    """Download latest version of a document by part number and document type"""
    try:
        with db_session:
            # Get document type
            doc_type_obj = DocumentTypeV2.get(name=doc_type.value)
            if not doc_type_obj:
                raise HTTPException(
                    status_code=404,
                    detail=f"Document type {doc_type.value} not found"
                )

            # Get latest document
            document = DocumentV2.select(
                lambda d: d.part_number == part_number and
                          d.doc_type.id == doc_type_obj.id and
                          d.is_active
            ).order_by(lambda d: desc(d.created_at)).first()

            if not document or not document.latest_version:
                raise HTTPException(
                    status_code=404,
                    detail=f"No {doc_type.value} document found for part number {part_number}"
                )

            # Log access
            DocumentAccessLogV2(
                document=document,
                version=document.latest_version,
                user=User.get(id=current_user.id),
                action_type=DocumentAction.DOWNLOAD,
                ip_address="0.0.0.0"
            )
            commit()

            try:
                file_data = minio.download_file(document.latest_version.minio_path)
                filename = f"{part_number}_{doc_type.value}_{document.latest_version.version_number}.{document.latest_version.minio_path.split('.')[-1]}"

                return StreamingResponse(
                    file_data,
                    media_type="application/octet-stream",
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"'
                    }
                )
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to download file: {str(e)}"
                )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


class DocumentsByTypeResponse(BaseModel):
    part_number: str
    mpp_document: DocumentResponse | None = None
    oarc_document: DocumentResponse | None = None
    engineering_drawing_document: DocumentResponse | None = None
    ipid_document: DocumentResponse | None = None
    all_documents: List[DocumentResponse]


@router.get("/documents/by-part-number-all/{part_number}", response_model=DocumentsByTypeResponse)
async def get_all_documents_by_part_number(
        part_number: str,
        current_user: User = Depends(get_current_user)
):
    """Get all documents for a part number across all document types, organized by type"""
    try:
        with db_session:
            # Get all documents for the part number
            documents = list(DocumentV2.select(
                lambda d: d.part_number == part_number and d.is_active
            ).order_by(lambda d: (d.doc_type.name, desc(d.created_at))))

            # Initialize response with None for each document type
            response = {
                "part_number": part_number,
                "mpp_document": None,
                "oarc_document": None,
                "engineering_drawing_document": None,
                "ipid_document": None,
                "all_documents": []
            }

            # Helper function to format document response
            def format_document(doc):
                return {
                    "id": doc.id,
                    "name": doc.name,
                    "folder_id": doc.folder.id,
                    "doc_type_id": doc.doc_type.id,
                    "description": doc.description,
                    "part_number": doc.part_number,
                    "production_order_id": doc.production_order.id if doc.production_order else None,
                    "created_at": doc.created_at,
                    "created_by_id": doc.created_by.id,
                    "is_active": doc.is_active,
                    "latest_version": {
                        "id": doc.latest_version.id,
                        "document_id": doc.id,
                        "version_number": doc.latest_version.version_number,
                        "minio_path": doc.latest_version.minio_path,
                        "file_size": doc.latest_version.file_size,
                        "checksum": doc.latest_version.checksum,
                        "created_at": doc.latest_version.created_at,
                        "created_by_id": doc.latest_version.created_by.id,
                        "is_active": doc.latest_version.is_active,
                        "metadata": doc.latest_version.metadata
                    } if doc.latest_version else None
                }

            # Process each document
            for doc in documents:
                formatted_doc = format_document(doc)
                response["all_documents"].append(formatted_doc)

                # Map document to its type in the response
                doc_type_map = {
                    DocumentTypes.MPP.value: "mpp_document",
                    DocumentTypes.OARC.value: "oarc_document",
                    DocumentTypes.ENGINEERING_DRAWING.value: "engineering_drawing_document",
                    DocumentTypes.IPID.value: "ipid_document"
                }

                # If this document type is newer than what we have, update it
                response_key = doc_type_map.get(doc.doc_type.name)
                if response_key:
                    if not response[response_key] or (
                            doc.created_at > response[response_key]["created_at"]
                    ):
                        response[response_key] = formatted_doc

            return response

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.post("/ipid/upload/", response_model=DocumentResponse)
async def upload_ipid_document(
        file: UploadFile = File(...),
        production_order: str = Form(...),
        operation_number: int = Form(...),
        document_name: str = Form(...),
        description: Optional[str] = Form(None),
        version_number: str = Form(...),
        metadata: Optional[str] = Form("{}"),
        current_user: User = Depends(get_current_user)
):
    """Upload an in-process document for a specific production order and operation"""
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # Get the order
            order = Order.get(production_order=production_order)
            if not order:
                raise HTTPException(status_code=404, detail="Production order not found")

            # Get the operation
            operation = Operation.get(order=order, operation_number=operation_number)
            if not operation:
                raise HTTPException(status_code=404, detail="Operation not found")

            # Get or create IPID document type
            doc_type_obj = DocumentTypeV2.get(name=DocumentTypes.IPID.value)
            if not doc_type_obj:
                doc_type_obj = DocumentTypeV2(
                    name=DocumentTypes.IPID.value,
                    description="In-Process Inspection Document",
                    allowed_extensions=[".pdf", ".doc", ".docx"]
                )
                commit()

            # Get or create root folder for document types
            root_folder = FolderV2.get(name="Document Types", parent_folder=None)
            if not root_folder:
                root_folder = FolderV2(
                    name="Document Types",
                    path="Document Types",
                    created_by=user
                )
                commit()

            # Get or create IPID folder
            ipid_folder = FolderV2.get(lambda f: f.name == DocumentTypes.IPID.value and f.parent_folder == root_folder)
            if not ipid_folder:
                ipid_folder = FolderV2(
                    name=DocumentTypes.IPID.value,
                    path=f"Document Types/{DocumentTypes.IPID.value}",
                    parent_folder=root_folder,
                    created_by=user
                )
                commit()

            # Get or create production order folder
            po_folder = FolderV2.get(lambda f: f.name == production_order and f.parent_folder == ipid_folder)
            if not po_folder:
                po_folder = FolderV2(
                    name=production_order,
                    path=f"Document Types/{DocumentTypes.IPID.value}/{production_order}",
                    parent_folder=ipid_folder,
                    created_by=user
                )
                commit()

            # Get or create operation folder
            op_folder = FolderV2.get(lambda f: f.name == f"OP{operation_number}" and f.parent_folder == po_folder)
            if not op_folder:
                op_folder = FolderV2(
                    name=f"OP{operation_number}",
                    path=f"Document Types/{DocumentTypes.IPID.value}/{production_order}/OP{operation_number}",
                    parent_folder=po_folder,
                    created_by=user
                )
                commit()

            # Validate file extension
            file_ext = file.filename.split('.')[-1].lower()
            if f".{file_ext}" not in doc_type_obj.allowed_extensions:
                raise HTTPException(
                    status_code=400,
                    detail=f"File type .{file_ext} not allowed for IPID documents"
                )

            # Create document
            new_doc = DocumentV2(
                name=document_name,
                folder=op_folder,
                doc_type=doc_type_obj,
                description=description,
                part_number=production_order,
                production_order=order,
                created_by=user
            )
            commit()

            # Handle file upload and version creation
            file_content = await file.read()
            checksum = hashlib.sha256(file_content).hexdigest()
            minio_path = f"documents/v2/{op_folder.path}/{new_doc.id}/v{version_number}/{file.filename}"

            try:
                file.file.seek(0)
                minio.upload_file(
                    file=file.file,
                    object_name=minio_path,
                    content_type=file.content_type or "application/octet-stream"
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

            # Parse metadata
            try:
                metadata_dict = json.loads(metadata) if metadata else {}
            except json.JSONDecodeError:
                metadata_dict = {}

            # Add operation information to metadata
            metadata_dict.update({
                "operation_id": operation.id,
                "operation_number": operation_number
            })

            # Create version
            version = DocumentVersionV2(
                document=new_doc,
                version_number=version_number,
                minio_path=minio_path,
                file_size=len(file_content),
                checksum=checksum,
                created_by=user,
                metadata=metadata_dict
            )
            new_doc.latest_version = version

            # Create access log
            DocumentAccessLogV2(
                document=new_doc,
                version=version,
                user=user,
                action_type=DocumentAction.UPDATE,
                ip_address="0.0.0.0"
            )

            commit()

            return {
                "id": new_doc.id,
                "name": new_doc.name,
                "folder_id": new_doc.folder.id,
                "doc_type_id": new_doc.doc_type.id,
                "description": new_doc.description,
                "part_number": new_doc.part_number,
                "production_order_id": new_doc.production_order.id if new_doc.production_order else None,
                "created_at": new_doc.created_at,
                "created_by_id": new_doc.created_by.id,
                "is_active": new_doc.is_active,
                "latest_version": {
                    "id": version.id,
                    "document_id": new_doc.id,
                    "version_number": version.version_number,
                    "minio_path": version.minio_path,
                    "file_size": version.file_size,
                    "checksum": version.checksum,
                    "created_at": version.created_at,
                    "created_by_id": version.created_by.id,
                    "is_active": version.is_active,
                    "metadata": version.metadata
                }
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ipid/{part_number}", response_model=List[DocumentResponse])
async def get_ipid_documents(
        part_number: str,
        current_user: User = Depends(get_current_user)
):
    """Get all IPID documents for a specific part number"""
    try:
        with db_session:
            # Get IPID document type
            doc_type = DocType.get(type_name="IPID")
            if not doc_type:
                raise HTTPException(status_code=404, detail="IPID document type not found")

            # Query documents using part_number_id.production_order instead of part_number
            documents = select(d for d in Document
                               if d.part_number_id.production_order == part_number
                               and d.doc_type == doc_type
                               and d.is_active == True)[:]

            # Format response according to DocumentResponse model
            response = []
            for doc in documents:
                latest_version = max(doc.versions, key=lambda v: v.created_at) if doc.versions else None
                if not latest_version:
                    continue

                doc_response = {
                    "id": doc.id,
                    "name": doc.document_name,
                    "folder_id": doc.folder.id,
                    "doc_type_id": doc.doc_type.id,
                    "description": doc.description,
                    "part_number": doc.part_number_id.production_order,  # Use production_order as part_number
                    "production_order_id": doc.part_number_id.id,
                    "created_at": doc.created_at,
                    "created_by_id": doc.created_by.id,
                    "is_active": doc.is_active,
                    "latest_version": {
                        "id": latest_version.id,
                        "document_id": doc.id,
                        "version_number": latest_version.version_number,
                        "minio_path": latest_version.minio_object_id,
                        "file_size": latest_version.file_size,
                        "checksum": latest_version.checksum,
                        "created_at": latest_version.created_at,
                        "created_by_id": latest_version.created_by.id,
                        "is_active": latest_version.status == 'active',
                        "metadata": latest_version.metadata
                    }
                }
                response.append(doc_response)

            return response

    except Exception as e:
        logger.error(f"Error retrieving IPID documents: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving IPID documents: {str(e)}"
        )


# Add a helper function to get documents by operation
@router.get("/by-operation/{production_order}/{operation_number}", response_model=List[DocumentResponse])
def get_documents_by_operation(
        production_order: str,
        operation_number: int,
        current_user: User = Depends(get_current_user)
):
    """Get all documents (including IPID) for a specific operation of a production order"""
    try:
        with db_session:
            # Re-fetch user within session
            user = User[current_user.id]

            # Get the order and operation
            order = Order.get(production_order=production_order)
            if not order:
                raise HTTPException(status_code=404, detail="Production order not found")

            operation = Operation.get(order=order, operation_number=operation_number)
            if not operation:
                raise HTTPException(status_code=404, detail="Operation not found")

            # Get all documents for this operation
            documents = select(d for d in Document
                               if d.is_active and
                               d.part_number_id == order and
                               d.latest_version
                               )[:]

            # Prepare response data
            response_data = []
            for doc in documents:
                metadata = doc.latest_version.metadata
                if isinstance(metadata, dict) and metadata.get("operation_number") == operation_number:
                    # Log access
                    DocumentAccessLog(
                        document=doc,
                        version=doc.latest_version,
                        user=user,
                        action_type="view"
                    )

                    # Create response dictionary matching DocumentResponse model
                    response_data.append({
                        "id": doc.id,
                        "name": doc.document_name,  # Changed from document_name to name
                        "folder_id": doc.folder.id,
                        "doc_type_id": doc.doc_type.id,
                        "description": doc.description,
                        "part_number": order.production_order,  # Use production_order as part_number
                        "production_order_id": order.id,
                        "created_at": doc.created_at,
                        "created_by_id": doc.created_by.id,
                        "is_active": doc.is_active,
                        "latest_version": {
                            "id": doc.latest_version.id,
                            "document_id": doc.id,
                            "version_number": doc.latest_version.version_number,
                            "minio_path": doc.latest_version.minio_object_id,
                            "file_size": doc.latest_version.file_size,
                            "checksum": doc.latest_version.checksum,
                            "created_at": doc.latest_version.created_at,
                            "created_by_id": doc.latest_version.created_by.id,
                            "is_active": doc.latest_version.status == 'active',
                            "metadata": doc.latest_version.metadata
                        } if doc.latest_version else None
                    })

            commit()
            return response_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ipid/download/{production_order}/{operation_number}")
def download_ipid_document(
        production_order: str,
        operation_number: int,
        current_user: User = Depends(get_current_user)
):
    """Download the latest IPID document for a specific production order and operation"""
    try:
        with db_session:
            # Re-fetch user within session
            user = User[current_user.id]

            # Get the order
            order = Order.get(production_order=production_order)
            if not order:
                raise HTTPException(status_code=404, detail="Production order not found")

            # Get IPID document type
            doc_type = DocType.get(type_name="IPID")
            if not doc_type:
                raise HTTPException(status_code=404, detail="IPID document type not found")

            # Get all active documents
            documents = select(d for d in Document
                               if d.is_active and
                               d.part_number_id == order and
                               d.doc_type == doc_type and
                               d.latest_version
                               ).order_by(lambda d: desc(d.created_at))[:]

            # Filter for matching operation number
            matching_docs = []
            for doc in documents:
                metadata = doc.latest_version.metadata
                if isinstance(metadata, dict) and metadata.get("operation_number") == operation_number:
                    matching_docs.append(doc)

            if not matching_docs:
                raise HTTPException(
                    status_code=404,
                    detail=f"No IPID document found for production order {production_order} and operation {operation_number}"
                )

            # Get the most recent document
            document = matching_docs[0]
            latest_version = document.latest_version

            try:
                # Get file from MinIO
                file_stream = minio.get_file(latest_version.minio_object_id)

                # Log the download access
                DocumentAccessLog(
                    document=document,
                    version=latest_version,
                    user=user,
                    action_type="download"
                )

                # Determine file extension and content type
                file_extension = document.document_name.split('.')[-1] if '.' in document.document_name else ''
                content_type = file_stream.headers.get("content-type", "application/octet-stream")

                # Generate filename
                download_filename = f"{production_order}_OP{operation_number}_IPID.{file_extension}"

                commit()

                return StreamingResponse(
                    file_stream,
                    media_type=content_type,
                    headers={
                        "Content-Disposition": f'attachment; filename="{download_filename}"',
                        "Content-Length": str(latest_version.file_size)
                    }
                )

            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Error retrieving file from storage: {str(e)}"
                )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Optional: Add an endpoint to list available documents before downloading
@router.get("/ipid/available/{production_order}/{operation_number}")
def list_available_ipid_documents(
        production_order: str,
        operation_number: int,
        current_user: User = Depends(get_current_user)
):
    """List all available IPID documents for a specific production order and operation"""
    try:
        with db_session:
            # Get the order
            order = Order.get(production_order=production_order)
            if not order:
                raise HTTPException(status_code=404, detail="Production order not found")

            # Get IPID document type
            doc_type = DocType.get(type_name="IPID")
            if not doc_type:
                return []

            # Get all documents
            documents = select(d for d in Document
                               if d.is_active and
                               d.part_number_id == order and
                               d.doc_type == doc_type and
                               d.latest_version
                               ).order_by(lambda d: desc(d.created_at))[:]

            # Filter and prepare response
            response_data = []
            for doc in documents:
                metadata = doc.latest_version.metadata
                if isinstance(metadata, dict) and metadata.get("operation_number") == operation_number:
                    response_data.append({
                        "id": doc.id,
                        "document_name": doc.document_name,
                        "created_at": doc.created_at,
                        "version": doc.latest_version.version_number,
                        "file_size": doc.latest_version.file_size,
                        "created_by": doc.created_by.id
                    })

            return response_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/documents/download-latest_new/{part_number}/{doc_type}")
async def download_latest_document_new_endpoint(
        part_number: str,
        doc_type: DocumentTypes,
        operation_number: int | None = Query(None, description="Required for IPID documents"),
        current_user: User = Depends(get_current_user)
):
    """Download latest version of a document by part number and document type"""
    try:
        with db_session:
            # Get document type
            doc_type_obj = DocumentTypeV2.get(name=doc_type.value)
            if not doc_type_obj:
                raise HTTPException(
                    status_code=404,
                    detail=f"Document type {doc_type.value} not found"
                )

            # Check if operation number is provided for IPID documents
            if doc_type == DocumentTypes.IPID:
                if operation_number is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Operation number is required for IPID documents"
                    )

                # Get all documents first
                documents = list(DocumentV2.select(
                    lambda d: d.part_number == part_number and
                              d.doc_type.id == doc_type_obj.id and
                              d.is_active and
                              d.latest_version
                ).order_by(lambda d: desc(d.created_at)))

                # Then filter for matching operation number
                document = None
                for doc in documents:
                    try:
                        metadata = doc.latest_version.metadata
                        if isinstance(metadata, str):
                            metadata = json.loads(metadata)
                        if metadata.get("operation_number") == operation_number:
                            document = doc
                            break
                    except (json.JSONDecodeError, AttributeError):
                        continue
            else:
                # For other document types, get latest document
                document = DocumentV2.select(
                    lambda d: d.part_number == part_number and
                              d.doc_type.id == doc_type_obj.id and
                              d.is_active
                ).order_by(lambda d: desc(d.created_at)).first()

            if not document or not document.latest_version:
                error_msg = (f"No {doc_type.value} document found for part number {part_number}"
                             f"{f' and operation {operation_number}' if doc_type == DocumentTypes.IPID else ''}")
                raise HTTPException(status_code=404, detail=error_msg)

            # Log access
            DocumentAccessLogV2(
                document=document,
                version=document.latest_version,
                user=User.get(id=current_user.id),
                action_type=DocumentAction.DOWNLOAD,
                ip_address="0.0.0.0"
            )
            commit()

            try:
                file_data = minio.download_file(document.latest_version.minio_path)

                # Generate filename based on document type
                if doc_type == DocumentTypes.IPID:
                    filename = f"{part_number}_OP{operation_number}_{doc_type.value}_{document.latest_version.version_number}.{document.latest_version.minio_path.split('.')[-1]}"
                else:
                    filename = f"{part_number}_{doc_type.value}_{document.latest_version.version_number}.{document.latest_version.minio_path.split('.')[-1]}"

                return StreamingResponse(
                    file_data,
                    media_type="application/octet-stream",
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"'
                    }
                )
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to download file: {str(e)}"
                )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


class DocumentsByTypeResponse(BaseModel):
    part_number: str
    mpp_document: DocumentResponse | None = None
    oarc_document: DocumentResponse | None = None
    engineering_drawing_document: DocumentResponse | None = None
    ipid_document: DocumentResponse | None = None
    all_documents: List[DocumentResponse]


@router.post("/ballooned-drawing/upload/", response_model=DocumentResponse)
async def upload_ballooned_drawing(
        file: UploadFile = File(...),
        production_order: str = Form(...),
        operation_number: str = Form(...),  # Required operation number
        document_name: str = Form(...),
        description: Optional[str] = Form(None),
        version_number: str = Form(...),
        part_number: Optional[str] = Form(None),
        metadata: Optional[str] = Form("{}"),
        current_user: User = Depends(get_current_user)
):
    """Upload a ballooned drawing for a specific production order and operation"""
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # Get the order
            order = Order.get(production_order=production_order)
            if not order:
                raise HTTPException(status_code=404, detail="Production order not found")

            # Get or create Ballooned Drawing document type
            doc_type_obj = DocumentTypeV2.get(name="BALLOONED_DRAWING")
            if not doc_type_obj:
                doc_type_obj = DocumentTypeV2(
                    name="BALLOONED_DRAWING",
                    description="Ballooned Engineering Drawings",
                    allowed_extensions=[".pdf", ".dwg", ".dxf"]
                )
                commit()

            # Get or create root folder for Balloon drawings
            balloon_folder = FolderV2.get(name="Balloon", parent_folder=None)
            if not balloon_folder:
                balloon_folder = FolderV2(
                    name="Balloon",
                    path="Balloon",
                    created_by=user
                )
                commit()

            # Get or create production order folder
            po_folder = FolderV2.get(lambda f: f.name == production_order and f.parent_folder == balloon_folder)
            if not po_folder:
                po_folder = FolderV2(
                    name=production_order,
                    path=f"Balloon/{production_order}",
                    parent_folder=balloon_folder,
                    created_by=user
                )
                commit()

            # Get or create operation folder under production order
            op_folder_name = f"OP{operation_number}"
            op_folder = FolderV2.get(lambda f: f.name == op_folder_name and f.parent_folder == po_folder)
            if not op_folder:
                op_folder = FolderV2(
                    name=op_folder_name,
                    path=f"Balloon/{production_order}/{op_folder_name}",
                    parent_folder=po_folder,
                    created_by=user
                )
                commit()

            # Validate file extension
            file_ext = file.filename.split('.')[-1].lower()
            if f".{file_ext}" not in doc_type_obj.allowed_extensions:
                raise HTTPException(
                    status_code=400,
                    detail=f"File type .{file_ext} not allowed for Ballooned Drawing documents"
                )

            # Create document
            new_doc = DocumentV2(
                name=document_name,
                folder=op_folder,  # Now using operation folder
                doc_type=doc_type_obj,
                description=description,
                part_number=part_number,
                production_order=order,
                created_by=user
            )
            commit()

            # Handle file upload and version creation
            file_content = await file.read()
            checksum = hashlib.sha256(file_content).hexdigest()

            # Path structure with required operation number
            minio_path = f"documents/v2/Balloon/{production_order}/OP{operation_number}/{new_doc.id}/v{version_number}/{file.filename}"

            try:
                file.file.seek(0)
                minio.upload_file(
                    file=file.file,
                    object_name=minio_path,
                    content_type=file.content_type or "application/octet-stream"
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

            # Parse metadata
            try:
                metadata_dict = json.loads(metadata) if metadata else {}
            except json.JSONDecodeError:
                metadata_dict = {}

            # Add production order and operation number to metadata
            metadata_dict["production_order"] = production_order
            metadata_dict["operation_number"] = operation_number
            if part_number:
                metadata_dict["part_number"] = part_number

            # Create version
            version = DocumentVersionV2(
                document=new_doc,
                version_number=version_number,
                minio_path=minio_path,
                file_size=len(file_content),
                checksum=checksum,
                created_by=user,
                metadata=metadata_dict
            )
            new_doc.latest_version = version

            # Create access log
            DocumentAccessLogV2(
                document=new_doc,
                version=version,
                user=user,
                action_type=DocumentAction.UPDATE,
                ip_address="0.0.0.0"
            )

            commit()

            return {
                "id": new_doc.id,
                "name": new_doc.name,
                "folder_id": new_doc.folder.id,
                "doc_type_id": new_doc.doc_type.id,
                "description": new_doc.description,
                "part_number": new_doc.part_number,
                "production_order_id": new_doc.production_order.id if new_doc.production_order else None,
                "operation_number": operation_number,
                "created_at": new_doc.created_at,
                "created_by_id": new_doc.created_by.id,
                "is_active": new_doc.is_active,
                "latest_version": {
                    "id": version.id,
                    "document_id": new_doc.id,
                    "version_number": version.version_number,
                    "minio_path": version.minio_path,
                    "file_size": version.file_size,
                    "checksum": version.checksum,
                    "created_at": version.created_at,
                    "created_by_id": version.created_by.id,
                    "is_active": version.is_active,
                    "metadata": version.metadata
                }
            }

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error uploading ballooned drawing: {error_details}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ballooned-drawing/download/{production_order}/{operation_number}")
def download_ballooned_drawing_by_po_and_op(
        production_order: str,
        operation_number: str,
        current_user: User = Depends(get_current_user)
):
    """Download the latest ballooned drawing for a production order and operation"""
    try:
        with db_session:
            # Re-fetch user within session
            user = User[current_user.id]

            # Get the order
            order = Order.get(production_order=production_order)
            if not order:
                raise HTTPException(status_code=404, detail="Production order not found")

            # Get document type for ballooned drawings
            doc_type = DocumentTypeV2.get(name="BALLOONED_DRAWING")
            if not doc_type:
                raise HTTPException(status_code=404, detail="Ballooned drawing document type not defined")

            # Find the operation folder
            balloon_folder = FolderV2.get(name="Balloon", parent_folder=None)
            if not balloon_folder:
                raise HTTPException(status_code=404, detail="Balloon folder not found")

            po_folder = FolderV2.get(lambda f: f.name == production_order and f.parent_folder == balloon_folder)
            if not po_folder:
                raise HTTPException(status_code=404, detail=f"Folder for production order {production_order} not found")

            op_folder_name = f"OP{operation_number}"
            op_folder = FolderV2.get(lambda f: f.name == op_folder_name and f.parent_folder == po_folder)
            if not op_folder:
                raise HTTPException(status_code=404, detail=f"Folder for operation {operation_number} not found")

            # Find the most recent ballooned drawing for this production order and operation
            documents = select(d for d in DocumentV2
                               if d.is_active and
                               d.doc_type == doc_type and
                               d.production_order == order and
                               d.folder == op_folder and
                               d.latest_version).order_by(lambda d: desc(d.created_at))[:]

            if not documents:
                raise HTTPException(
                    status_code=404,
                    detail=f"No ballooned drawings found for production order {production_order} operation {operation_number}"
                )

            # Get the most recent document
            document = documents[0]
            latest_version = document.latest_version

            # Verify operation number in metadata as a double check
            metadata = latest_version.metadata or {}
            if metadata.get("operation_number") != operation_number:
                # If not found in metadata, continue anyway as we've already verified the folder structure
                # but log a warning for data consistency checks
                print(f"Warning: Operation number mismatch in metadata for document {document.id}")

            try:
                # Get file from MinIO
                file_stream = minio.get_file(latest_version.minio_path)

                # Log the download access
                DocumentAccessLogV2(
                    document=document,
                    version=latest_version,
                    user=user,
                    action_type=DocumentAction.DOWNLOAD,
                    ip_address="0.0.0.0"
                )

                # Determine file extension and content type
                file_extension = document.name.split('.')[-1] if '.' in document.name else 'pdf'
                content_type = file_stream.headers.get("content-type", "application/octet-stream")

                # Generate filename
                download_filename = f"{production_order}_OP{operation_number}_Ballooned_Drawing.{file_extension}"

                commit()

                return StreamingResponse(
                    file_stream,
                    media_type=content_type,
                    headers={
                        "Content-Disposition": f'attachment; filename="{download_filename}"',
                        "Content-Length": str(latest_version.file_size)
                    }
                )

            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Error retrieving file from storage: {str(e)}"
                )

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error downloading ballooned drawing: {error_details}")
        raise HTTPException(status_code=500, detail=str(e))


# Machine document management endpoints
@router.post("/machine-documents/upload/", response_model=DocumentResponse)
async def upload_machine_document(
        file: UploadFile = File(...),
        machine_id: int = Form(...),
        document_name: str = Form(...),
        document_type: str = Form(...),  # Type of machine document: "MANUAL", "MAINTENANCE", "CALIBRATION", etc.
        description: Optional[str] = Form(None),
        version_number: str = Form(default="1.0"),
        metadata: Optional[str] = Form("{}"),
        current_user: User = Depends(get_current_user)
):
    """Upload document for a specific machine"""
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # Get or create machine documents type
            doc_type_obj = DocumentTypeV2.get(name=DocumentTypes.MACHINE_DOCUMENT.value)
            if not doc_type_obj:
                doc_type_obj = DocumentTypeV2(
                    name=DocumentTypes.MACHINE_DOCUMENT.value,
                    description="Machine Documents",
                    allowed_extensions=[".pdf", ".doc", ".docx", ".xls", ".xlsx", ".jpg", ".jpeg", ".png"]
                )
                commit()

            # Get or create root folder for machine documents
            root_folder = FolderV2.get(name="MachineDocuments", parent_folder=None)
            if not root_folder:
                root_folder = FolderV2(
                    name="MachineDocuments",
                    path="MachineDocuments",
                    created_by=user
                )
                commit()

            # Get or create machine folder using machine_id
            machine_folder_name = f"Machine_{machine_id}"
            machine_folder = FolderV2.get(lambda f: f.name == machine_folder_name and f.parent_folder == root_folder)
            if not machine_folder:
                machine_folder = FolderV2(
                    name=machine_folder_name,
                    path=f"MachineDocuments/{machine_folder_name}",
                    parent_folder=root_folder,
                    created_by=user
                )
                commit()

            # Get or create document type folder (e.g., MANUAL, MAINTENANCE)
            doc_type_folder = FolderV2.get(lambda f: f.name == document_type and f.parent_folder == machine_folder)
            if not doc_type_folder:
                doc_type_folder = FolderV2(
                    name=document_type,
                    path=f"MachineDocuments/{machine_folder_name}/{document_type}",
                    parent_folder=machine_folder,
                    created_by=user
                )
                commit()

            # Validate file extension
            file_ext = file.filename.split('.')[-1].lower()
            if f".{file_ext}" not in doc_type_obj.allowed_extensions:
                raise HTTPException(
                    status_code=400,
                    detail=f"File type .{file_ext} not allowed for machine documents"
                )

            # Add machine info to metadata
            try:
                metadata_dict = json.loads(metadata)
                metadata_dict.update({
                    "machine_id": machine_id,
                    "document_type": document_type,
                })
                metadata = json.dumps(metadata_dict)
            except json.JSONDecodeError:
                metadata = json.dumps({
                    "machine_id": machine_id,
                    "document_type": document_type,
                })

            # Create document
            new_doc = DocumentV2(
                name=document_name,
                folder=doc_type_folder,
                doc_type=doc_type_obj,
                description=description,
                created_by=user
            )
            commit()

            # Handle file upload and version creation
            file_content = await file.read()
            checksum = hashlib.sha256(file_content).hexdigest()
            minio_path = f"documents/machine/{machine_id}/{document_type}/{new_doc.id}/v{version_number}/{file.filename}"

            try:
                file.file.seek(0)
                minio.upload_file(
                    file=file.file,
                    object_name=minio_path,
                    content_type=file.content_type or "application/octet-stream"
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

            # Create version
            version = DocumentVersionV2(
                document=new_doc,
                version_number=version_number,
                minio_path=minio_path,
                file_size=len(file_content),
                checksum=checksum,
                created_by=user,
                metadata=json.loads(metadata)
            )
            new_doc.latest_version = version

            # Create access log
            DocumentAccessLogV2(
                document=new_doc,
                version=version,
                user=user,
                action_type=DocumentAction.UPDATE,
                ip_address="0.0.0.0"
            )

            commit()

            return {
                "id": new_doc.id,
                "name": new_doc.name,
                "folder_id": new_doc.folder.id,
                "doc_type_id": new_doc.doc_type.id,
                "description": new_doc.description,
                "part_number": None,
                "production_order_id": None,
                "created_at": new_doc.created_at,
                "created_by_id": new_doc.created_by.id,
                "is_active": new_doc.is_active,
                "latest_version": {
                    "id": version.id,
                    "document_id": new_doc.id,
                    "version_number": version.version_number,
                    "minio_path": version.minio_path,
                    "file_size": version.file_size,
                    "checksum": version.checksum,
                    "created_at": version.created_at,
                    "created_by_id": version.created_by.id,
                    "is_active": version.is_active,
                    "metadata": version.metadata
                }
            }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/machine-documents/{machine_id}", response_model=List[DocumentResponse])
async def get_machine_documents(
        machine_id: int,
        document_type: Optional[str] = Query(None, description="Filter by document type (MANUAL, MAINTENANCE, etc.)"),
        current_user: User = Depends(get_current_user)
):
    """Get all documents for a specific machine"""
    try:
        with db_session:
            # Get the machine folder
            root_folder = FolderV2.get(name="MachineDocuments", parent_folder=None)
            if not root_folder:
                return []

            machine_folder_name = f"Machine_{machine_id}"
            machine_folder = FolderV2.get(lambda f: f.name == machine_folder_name and f.parent_folder == root_folder)
            if not machine_folder:
                return []

            # Collect all relevant folder IDs
            folder_ids = [machine_folder.id]

            # If no document_type is specified, include all subfolders
            if document_type is None:
                for child in machine_folder.child_folders:
                    folder_ids.append(child.id)
            else:
                # Otherwise, just include the specific document type folder
                doc_type_folder = FolderV2.get(lambda f: f.name == document_type and f.parent_folder == machine_folder)
                if doc_type_folder:
                    folder_ids.append(doc_type_folder.id)

            # Query documents
            documents = list(DocumentV2.select(
                lambda d: d.folder.id in folder_ids and d.is_active
            ).order_by(lambda d: desc(d.created_at)))

            # Format response
            return [
                {
                    "id": doc.id,
                    "name": doc.name,
                    "folder_id": doc.folder.id,
                    "doc_type_id": doc.doc_type.id,
                    "description": doc.description,
                    "part_number": doc.part_number,
                    "production_order_id": doc.production_order.id if doc.production_order else None,
                    "created_at": doc.created_at,
                    "created_by_id": doc.created_by.id,
                    "is_active": doc.is_active,
                    "latest_version": {
                        "id": doc.latest_version.id,
                        "document_id": doc.id,
                        "version_number": doc.latest_version.version_number,
                        "minio_path": doc.latest_version.minio_path,
                        "file_size": doc.latest_version.file_size,
                        "checksum": doc.latest_version.checksum,
                        "created_at": doc.latest_version.created_at,
                        "created_by_id": doc.latest_version.created_by.id,
                        "is_active": doc.latest_version.is_active,
                        "metadata": doc.latest_version.metadata
                    } if doc.latest_version else None
                }
                for doc in documents
            ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/machine-documents/download/{document_id}")
async def download_machine_document(
        document_id: int,
        version_id: int | None = Query(None, description="Specific version to download, omit for latest"),
        current_user: User = Depends(get_current_user)
):
    """Download a specific machine document, either the latest version or a specific version"""
    try:
        with db_session:
            # Get the document
            document = DocumentV2.get(id=document_id)
            if not document or not document.is_active:
                raise HTTPException(status_code=404, detail="Document not found")

            # Check if this is a machine document (should be in the MachineDocuments folder hierarchy)
            root_folder_path_part = "MachineDocuments/"
            if not document.folder.path.startswith(root_folder_path_part):
                raise HTTPException(status_code=400, detail="Not a machine document")

            # Determine which version to download
            version = None
            if version_id:
                version = DocumentVersionV2.get(id=version_id, document=document)
                if not version or not version.is_active:
                    raise HTTPException(status_code=404, detail="Document version not found")
            else:
                version = document.latest_version
                if not version:
                    raise HTTPException(status_code=404, detail="No available version for this document")

            # Log access
            DocumentAccessLogV2(
                document=document,
                version=version,
                user=User.get(id=current_user.id),
                action_type=DocumentAction.DOWNLOAD,
                ip_address="0.0.0.0"
            )
            commit()

            try:
                # Get machine_id from the folder path (MachineDocuments/Machine_X/...)
                machine_id = document.folder.path.split('/')[1].replace('Machine_', '')
                doc_type = document.folder.name  # e.g. MANUAL, MAINTENANCE

                file_data = minio.download_file(version.minio_path)
                filename = f"Machine_{machine_id}_{doc_type}_{document.name}_{version.version_number}.{version.minio_path.split('.')[-1]}"

                return StreamingResponse(
                    file_data,
                    media_type="application/octet-stream",
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"'
                    }
                )
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to download file: {str(e)}"
                )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/machine-documents/{document_id}/versions", response_model=List[DocumentVersionResponse])
async def list_machine_document_versions(
        document_id: int,
        current_user: User = Depends(get_current_user)
):
    """Get all versions of a specific machine document"""
    try:
        with db_session:
            # Get the document
            document = DocumentV2.get(id=document_id)
            if not document or not document.is_active:
                raise HTTPException(status_code=404, detail="Document not found")

            # Check if this is a machine document (should be in the MachineDocuments folder hierarchy)
            root_folder_path_part = "MachineDocuments/"
            if not document.folder.path.startswith(root_folder_path_part):
                raise HTTPException(status_code=400, detail="Not a machine document")

            # Get all versions
            versions = list(DocumentVersionV2.select(
                lambda v: v.document.id == document_id and v.is_active
            ).order_by(lambda v: desc(v.created_at)))

            # Format response
            return [
                {
                    "id": version.id,
                    "document_id": document.id,
                    "version_number": version.version_number,
                    "minio_path": version.minio_path,
                    "file_size": version.file_size,
                    "checksum": version.checksum,
                    "created_at": version.created_at,
                    "created_by_id": version.created_by.id,
                    "is_active": version.is_active,
                    "metadata": version.metadata
                }
                for version in versions
            ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.post("/machine-documents/{document_id}/versions", response_model=DocumentVersionResponse)
async def create_machine_document_version(
        document_id: int,
        file: UploadFile = File(...),
        version_number: str = Form(...),
        metadata: str = Form(default="{}"),
        current_user: User = Depends(get_current_user)
):
    """Add a new version to an existing machine document"""
    try:
        with db_session:
            # Get the document and user
            document = DocumentV2.get(id=document_id)
            user = User.get(id=current_user.id)

            if not document or not document.is_active:
                raise HTTPException(status_code=404, detail="Document not found")

            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # Check if this is a machine document (should be in the MachineDocuments folder hierarchy)
            root_folder_path_part = "MachineDocuments/"
            if not document.folder.path.startswith(root_folder_path_part):
                raise HTTPException(status_code=400, detail="Not a machine document")

            # Extract machine_id and document_type from folder path
            path_parts = document.folder.path.split('/')
            machine_id = path_parts[1].replace('Machine_', '')
            document_type = path_parts[2] if len(path_parts) > 2 else "GENERAL"

            # Add machine info to metadata
            try:
                metadata_dict = json.loads(metadata)
                metadata_dict.update({
                    "machine_id": machine_id,
                    "document_type": document_type,
                })
                metadata = json.dumps(metadata_dict)
            except json.JSONDecodeError:
                metadata = json.dumps({
                    "machine_id": machine_id,
                    "document_type": document_type,
                })

            # Validate file extension
            file_ext = file.filename.split('.')[-1].lower()
            if f".{file_ext}" not in document.doc_type.allowed_extensions:
                raise HTTPException(
                    status_code=400,
                    detail=f"File type .{file_ext} not allowed for this document type"
                )

            # Handle file upload and version creation
            file_content = await file.read()
            checksum = hashlib.sha256(file_content).hexdigest()
            minio_path = f"documents/machine/{machine_id}/{document_type}/{document_id}/v{version_number}/{file.filename}"

            try:
                file.file.seek(0)
                minio.upload_file(
                    file=file.file,
                    object_name=minio_path,
                    content_type=file.content_type or "application/octet-stream"
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

            # Create version
            version = DocumentVersionV2(
                document=document,
                version_number=version_number,
                minio_path=minio_path,
                file_size=len(file_content),
                checksum=checksum,
                created_by=user,
                metadata=json.loads(metadata)
            )

            # Update document's latest version
            document.latest_version = version

            # Create access log
            DocumentAccessLogV2(
                document=document,
                version=version,
                user=user,
                action_type=DocumentAction.UPDATE,
                ip_address="0.0.0.0"
            )

            commit()

            return {
                "id": version.id,
                "document_id": document.id,
                "version_number": version.version_number,
                "minio_path": version.minio_path,
                "file_size": version.file_size,
                "checksum": version.checksum,
                "created_at": version.created_at,
                "created_by_id": version.created_by.id,
                "is_active": version.is_active,
                "metadata": version.metadata
            }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


class MachineDocumentTypeResponse(BaseModel):
    name: str
    description: str
    count: int


@router.get("/machine-documents/document-types/", response_model=List[MachineDocumentTypeResponse])
async def list_machine_document_types(
        machine_id: Optional[int] = Query(None, description="Filter by machine ID"),
        current_user: User = Depends(get_current_user)
):
    """Get all available document types for machine documents, with counts"""
    try:
        with db_session:
            # Get the machine folders
            root_folder = FolderV2.get(name="MachineDocuments", parent_folder=None)
            if not root_folder:
                return []

            if machine_id:
                # For a specific machine, get its folder
                machine_folder_name = f"Machine_{machine_id}"
                machine_folder = FolderV2.get(
                    lambda f: f.name == machine_folder_name and f.parent_folder == root_folder)
                if not machine_folder:
                    return []

                # Get document types as subfolder names
                doc_types = []
                for subfolder in machine_folder.child_folders:
                    if subfolder.is_active:
                        # Count documents in this folder
                        doc_count = select(d for d in DocumentV2 if d.folder.id == subfolder.id and d.is_active).count()
                        doc_types.append({
                            "name": subfolder.name,
                            "description": f"Machine {machine_id} {subfolder.name} Documents",
                            "count": doc_count
                        })
                return doc_types
            else:
                # For all machines, aggregate document types
                doc_types = {}

                # Get all machine folders
                machine_folders = list(FolderV2.select(lambda f: f.parent_folder == root_folder and f.is_active))

                for machine_folder in machine_folders:
                    for doc_type_folder in machine_folder.child_folders:
                        if doc_type_folder.is_active:
                            doc_type = doc_type_folder.name
                            if doc_type not in doc_types:
                                doc_types[doc_type] = {
                                    "name": doc_type,
                                    "description": f"{doc_type} Documents",
                                    "count": 0
                                }

                            # Count documents in this folder
                            doc_count = select(
                                d for d in DocumentV2 if d.folder.id == doc_type_folder.id and d.is_active).count()
                            doc_types[doc_type]["count"] += doc_count

                return list(doc_types.values())

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


class MachineWithDocumentsResponse(BaseModel):
    machine_id: int
    document_count: int
    document_types: List[str]


@router.get("/machine-documents/machines/", response_model=List[MachineWithDocumentsResponse])
async def list_machines_with_documents(
        current_user: User = Depends(get_current_user)
):
    """Get all machines that have documents in the system"""
    try:
        with db_session:
            # Get the machine folders
            root_folder = FolderV2.get(name="MachineDocuments", parent_folder=None)
            if not root_folder:
                return []

            machines = []
            # Get all machine folders
            machine_folders = list(FolderV2.select(lambda f: f.parent_folder == root_folder and f.is_active))

            for machine_folder in machine_folders:
                machine_id = int(machine_folder.name.replace('Machine_', ''))

                # Get all document type folders for this machine
                doc_type_folders = list(FolderV2.select(lambda f: f.parent_folder == machine_folder and f.is_active))
                doc_types = [folder.name for folder in doc_type_folders]

                # Count total documents for this machine
                folder_ids = [folder.id for folder in doc_type_folders]
                if folder_ids:
                    doc_count = select(d for d in DocumentV2 if d.folder.id in folder_ids and d.is_active).count()
                else:
                    doc_count = 0

                machines.append({
                    "machine_id": machine_id,
                    "document_count": doc_count,
                    "document_types": doc_types
                })

            return machines

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.post("/report/upload/", response_model=DocumentResponse)
async def upload_report_document(
        file: UploadFile = File(...),
        folder_path: str = Form(...),
        document_name: str = Form(...),
        description: Optional[str] = Form(None),
        version_number: str = Form(...),
        order_number: Optional[str] = Form(None),
        metadata: Optional[str] = Form("{}"),
        current_user: User = Depends(get_current_user)
):
    """Upload a report document with the ability to create custom folder paths"""
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # Define report document type name as a string constant
            REPORT_DOC_TYPE = "REPORT"

            # Get or create Report document type
            doc_type_obj = DocumentTypeV2.get(name=REPORT_DOC_TYPE)
            if not doc_type_obj:
                doc_type_obj = DocumentTypeV2(
                    name=REPORT_DOC_TYPE,
                    description="Report Documents",
                    allowed_extensions=[".pdf", ".doc", ".docx", ".xlsx", ".xls", ".csv", ".txt"]
                )
                commit()

            # Get or create root folder for document types
            root_folder = FolderV2.get(name="Document Types", parent_folder=None)
            if not root_folder:
                root_folder = FolderV2(
                    name="Document Types",
                    path="Document Types",
                    created_by=user
                )
                commit()

            # Get or create Report folder
            report_folder = FolderV2.get(lambda f: f.name == REPORT_DOC_TYPE and f.parent_folder == root_folder)
            if not report_folder:
                report_folder = FolderV2(
                    name=REPORT_DOC_TYPE,
                    path=f"Document Types/{REPORT_DOC_TYPE}",
                    parent_folder=root_folder,
                    created_by=user
                )
                commit()

            # Process the custom folder path to get to the target folder
            if folder_path:
                folder_parts = folder_path.strip("/").split("/")
                current_folder = report_folder
                current_path = f"Document Types/{REPORT_DOC_TYPE}"

                # Create each folder in the path if it doesn't exist
                for folder_name in folder_parts:
                    if not folder_name:
                        continue

                    current_path += f"/{folder_name}"
                    next_folder = FolderV2.get(lambda f: f.name == folder_name and f.parent_folder == current_folder)

                    if not next_folder:
                        next_folder = FolderV2(
                            name=folder_name,
                            path=current_path,
                            parent_folder=current_folder,
                            created_by=user
                        )
                        commit()

                    current_folder = next_folder

                # The specified folder path will be the parent folder
                parent_folder = current_folder
            else:
                # If no folder path is provided, use the report root folder as parent
                parent_folder = report_folder

            # Get production order if order_number is provided
            production_order = None
            if order_number:
                production_order = Order.get(production_order=order_number)
                if not production_order:
                    raise HTTPException(status_code=404, detail=f"Order {order_number} not found")

                # Create a folder for the order_number inside the parent folder
                order_folder_path = f"{parent_folder.path}/{order_number}"
                order_folder = FolderV2.get(lambda f: f.name == order_number and f.parent_folder == parent_folder)
                if not order_folder:
                    order_folder = FolderV2(
                        name=order_number,
                        path=order_folder_path,
                        parent_folder=parent_folder,
                        created_by=user
                    )
                    commit()

                # Use the order folder as the target folder for the document
                target_folder = order_folder
            else:
                # If no order number, just use the parent folder
                target_folder = parent_folder

            # Validate file extension
            file_ext = file.filename.split('.')[-1].lower()
            if f".{file_ext}" not in doc_type_obj.allowed_extensions:
                raise HTTPException(
                    status_code=400,
                    detail=f"File type .{file_ext} not allowed for Report documents"
                )

            # Create document
            new_doc = DocumentV2(
                name=document_name,
                folder=target_folder,
                doc_type=doc_type_obj,
                description=description,
                part_number=order_number if order_number else "",
                production_order=production_order,
                created_by=user
            )
            commit()

            # Handle file upload and version creation
            file_content = await file.read()
            checksum = hashlib.sha256(file_content).hexdigest()
            minio_path = f"documents/v2/{target_folder.path}/{new_doc.id}/v{version_number}/{file.filename}"

            try:
                file.file.seek(0)
                minio.upload_file(
                    file=file.file,
                    object_name=minio_path,
                    content_type=file.content_type or "application/octet-stream"
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

            # Parse metadata
            try:
                metadata_dict = json.loads(metadata) if metadata else {}
            except json.JSONDecodeError:
                metadata_dict = {}

            # Create version
            version = DocumentVersionV2(
                document=new_doc,
                version_number=version_number,
                minio_path=minio_path,
                file_size=len(file_content),
                checksum=checksum,
                created_by=user,
                metadata=metadata_dict
            )
            new_doc.latest_version = version

            # Create access log - using string constant instead of enum
            DocumentAccessLogV2(
                document=new_doc,
                version=version,
                user=user,
                action_type="UPDATE",
                ip_address="0.0.0.0"
            )

            commit()

            return {
                "id": new_doc.id,
                "name": new_doc.name,
                "folder_id": new_doc.folder.id,
                "doc_type_id": new_doc.doc_type.id,
                "description": new_doc.description,
                "part_number": new_doc.part_number,
                "production_order_id": new_doc.production_order.id if new_doc.production_order else None,
                "created_at": new_doc.created_at,
                "created_by_id": new_doc.created_by.id,
                "is_active": new_doc.is_active,
                "latest_version": {
                    "id": version.id,
                    "document_id": new_doc.id,
                    "version_number": version.version_number,
                    "minio_path": version.minio_path,
                    "file_size": version.file_size,
                    "checksum": version.checksum,
                    "created_at": version.created_at,
                    "created_by_id": version.created_by.id,
                    "is_active": version.is_active,
                    "metadata": version.metadata
                }
            }

    except Exception as e:
        # Add more detailed error logging
        print(f"Error in upload_report_document: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/debug/folder/{folder_name}", response_model=Dict)
async def debug_folder(
        folder_name: str,
        current_user: User = Depends(get_current_user)
):
    """
    Debug endpoint to check if a folder exists and its properties.
    """
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # Find the Document Types root folder
            root_folder = FolderV2.get(name="Document Types", parent_folder=None)
            if not root_folder:
                raise HTTPException(status_code=404, detail="Document Types folder not found")

            # Find the Report folder under Document Types
            report_folder = FolderV2.get(lambda f: f.name == "REPORT" and f.parent_folder == root_folder)
            if not report_folder:
                raise HTTPException(status_code=404, detail="REPORT folder not found")

            # Look for the specified folder directly
            all_matching_folders = select(f for f in FolderV2 if f.name == folder_name)

            # Look for the specified folder under REPORT
            target_folder = FolderV2.get(lambda f: f.name == folder_name and f.parent_folder == report_folder)

            folder_results = []

            # Check all matching folders with the name
            for folder in all_matching_folders:
                folder_results.append({
                    "id": folder.id,
                    "name": folder.name,
                    "parent_folder_id": folder.parent_folder.id if folder.parent_folder else None,
                    "parent_folder_name": folder.parent_folder.name if folder.parent_folder else None,
                    "is_active": folder.is_active,
                    "path": folder.path,
                    "created_at": folder.created_at.isoformat() if folder.created_at else None,
                    "created_by_id": folder.created_by.id if folder.created_by else None
                })

            return {
                "folder_name": folder_name,
                "report_folder_id": report_folder.id,
                "direct_match_under_report": {
                    "found": target_folder is not None,
                    "is_active": target_folder.is_active if target_folder else None,
                    "id": target_folder.id if target_folder else None
                },
                "all_matching_folders": folder_results,
                "count_all_matching": len(folder_results)
            }

    except Exception as e:
        print(f"Error in debug_folder: {str(e)}")
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/report/structure/", response_model=List[Dict])
async def get_report_structure(
        current_user: User = Depends(get_current_user),
        force_refresh: bool = False  # Add a parameter to force refresh from DB
):
    """
    Get all folders and files under the report folder structure.
    Returns a hierarchical representation of folders and their documents.
    """
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # Define report document type name as a string constant
            REPORT_DOC_TYPE = "REPORT"

            # Find the Document Types root folder
            root_folder = FolderV2.get(name="Document Types", parent_folder=None)
            if not root_folder:
                raise HTTPException(status_code=404, detail="Document Types folder not found")

            # Find the Report folder under Document Types
            report_folder = FolderV2.get(lambda f: f.name == REPORT_DOC_TYPE and f.parent_folder == root_folder)
            if not report_folder:
                raise HTTPException(status_code=404, detail="REPORT folder not found")

            # Function to recursively build folder structure
            def build_folder_structure(folder):
                result = {
                    "id": folder.id,
                    "name": folder.name,
                    "type": "folder",
                    "path": folder.path,
                    "created_at": folder.created_at.isoformat() if folder.created_at else None,
                    "created_by_id": folder.created_by.id if folder.created_by else None,
                    "children": []
                }

                # Print debug information for this folder
                print(f"Processing folder: {folder.name} (ID: {folder.id}, is_active: {folder.is_active})")

                # Debug: Check for specific folder
                if folder.name == "REPORT":
                    # Check for VMS folder directly to see if it exists at all
                    vms_folder = select(f for f in FolderV2 if f.name == "VMS" and f.parent_folder == folder)
                    for vf in vms_folder:
                        print(f"VMS folder found: ID={vf.id}, is_active={vf.is_active}, parent={vf.parent_folder.id}")

                # Get all documents in this folder
                documents = select(d for d in DocumentV2 if d.folder == folder and d.is_active)

                # Debug: print count of documents found
                print(f"Found {documents.count()} active documents in folder {folder.name}")

                for doc in documents:
                    # Get the latest version info
                    latest_version = doc.latest_version
                    if latest_version:
                        result["children"].append({
                            "id": doc.id,
                            "name": doc.name,
                            "type": "document",
                            "description": doc.description,
                            "part_number": doc.part_number,
                            "production_order_id": doc.production_order.id if doc.production_order else None,
                            "created_at": doc.created_at.isoformat() if doc.created_at else None,
                            "created_by_id": doc.created_by.id if doc.created_by else None,
                            "latest_version": {
                                "id": latest_version.id,
                                "version_number": latest_version.version_number,
                                "minio_path": latest_version.minio_path,
                                "file_size": latest_version.file_size,
                                "created_at": latest_version.created_at.isoformat() if latest_version.created_at else None
                            }
                        })

                # IMPORTANT: Get all subfolders - query directly for active status
                subfolders = list(
                    select(f for f in FolderV2 if f.parent_folder.id == folder.id and f.is_active == True))

                # Debug: print detailed info about subfolders
                print(f"Found {len(subfolders)} active subfolders in folder {folder.name}")
                for sf in subfolders:
                    print(f"  - Subfolder: {sf.name} (ID: {sf.id}, is_active: {sf.is_active})")

                # Recursively build structure for each subfolder
                for subfolder in subfolders:
                    subfolder_structure = build_folder_structure(subfolder)
                    result["children"].append(subfolder_structure)

                return result

            # Build the complete structure starting from report folder
            result = build_folder_structure(report_folder)

            return [result]  # Return as a list for consistency with response_model

    except Exception as e:
        # Log the error for debugging
        print(f"Error in get_report_structure: {str(e)}")
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/report/structure/{item_type}/{item_id}", status_code=200)
async def delete_report_item(
        item_type: str,
        item_id: int,
        current_user: User = Depends(get_current_user)
):
    """
    Delete a folder or document from the report structure.

    - item_type: Must be either "folder" or "document"
    - item_id: The ID of the item to delete

    This performs a soft delete by setting is_active=False.
    """
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            if item_type not in ["folder", "document"]:
                raise HTTPException(status_code=400, detail="Invalid item type. Must be 'folder' or 'document'")

            # Handle folder deletion
            if item_type == "folder":
                folder = FolderV2.get(id=item_id)
                if not folder:
                    raise HTTPException(status_code=404, detail="Folder not found")

                # Check if user has permission to delete this folder
                # Add your permission logic here if needed

                # Function to recursively mark folder and all its contents as inactive
                def mark_folder_inactive(folder):
                    # Mark all documents in the folder as inactive
                    documents = select(d for d in DocumentV2 if d.folder == folder and d.is_active)
                    for doc in documents:
                        doc.is_active = False
                        doc.modified_at = datetime.utcnow()
                        doc.modified_by = user

                    # Recursively mark all subfolders and their contents as inactive
                    subfolders = select(f for f in FolderV2 if f.parent_folder == folder and f.is_active)
                    for subfolder in subfolders:
                        mark_folder_inactive(subfolder)

                    # Finally mark the folder itself as inactive
                    folder.is_active = False
                    folder.modified_at = datetime.utcnow()
                    folder.modified_by = user

                # Execute the recursive deletion
                mark_folder_inactive(folder)

                return {"message": f"Folder '{folder.name}' and all its contents have been deleted"}

            # Handle document deletion
            elif item_type == "document":
                document = DocumentV2.get(id=item_id)
                if not document:
                    raise HTTPException(status_code=404, detail="Document not found")

                # Check if user has permission to delete this document
                # Add your permission logic here if needed

                # Mark document as inactive
                document.is_active = False
                document.modified_at = datetime.utcnow()
                document.modified_by = user

                return {"message": f"Document '{document.name}' has been deleted"}

    except Exception as e:
        # Log the error for debugging
        print(f"Error in delete_report_item: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/report/download-latest/{report_type}/{part_number}")
async def download_latest_report(
        report_type: str = Path(..., description="Report subfolder type (e.g., CMM, CAL, etc.)"),
        part_number: str = Path(..., description="Part number"),
        current_user: User = Depends(get_current_user)
):
    """
    Download the latest report document for a given report subfolder type and part number.

    Parameters:
    - report_type: Type of report subfolder (e.g., CMM, CAL, or any other subfolder under REPORT)
    - part_number: Part number

    Returns:
    - File stream response with the latest document
    """
    try:
        with db_session:
            # Get root folder
            root_folder = FolderV2.get(name="Document Types", parent_folder=None)
            if not root_folder:
                raise HTTPException(status_code=404, detail="Document Types folder not found")

            # Get report folder
            report_folder = FolderV2.get(lambda f: f.name == "REPORT" and f.parent_folder == root_folder)
            if not report_folder:
                raise HTTPException(status_code=404, detail="REPORT folder not found")

            # Get report type folder
            report_type_folder = FolderV2.get(lambda f: f.name == report_type and f.parent_folder == report_folder)
            if not report_type_folder:
                raise HTTPException(status_code=404, detail=f"Report type folder not found: {report_type}")

            # Get part number folder
            part_folder = FolderV2.get(lambda f: f.name == part_number and f.parent_folder == report_type_folder)
            if not part_folder:
                raise HTTPException(status_code=404, detail=f"Part number folder not found: {part_number}")

            # Find all documents in the part number folder
            documents = select(d for d in DocumentV2
                               if d.folder == part_folder
                               and d.is_active).order_by(lambda d: desc(d.created_at))[:]

            if not documents:
                raise HTTPException(
                    status_code=404,
                    detail=f"No documents found for part number {part_number} in {report_type}"
                )

            # Get the most recent document
            latest_document = documents[0]

            # Get the latest version of the most recent document
            latest_version = latest_document.versions.select().order_by(lambda v: desc(v.created_at)).first()
            if not latest_version:
                raise HTTPException(
                    status_code=404,
                    detail="No versions found for the latest document"
                )

            # Get the file from MinioService
            minio_service = MinioService()
            file_stream = minio_service.get_file(latest_version.minio_path)

            # Get the file extension from the minio path
            file_extension = os.path.splitext(latest_version.minio_path)[1]
            if not file_extension:
                file_extension = '.pdf'  # Default to .pdf if no extension found

            # Create a response with the file
            content_type = "application/pdf" if file_extension.lower() == '.pdf' else "application/octet-stream"

            return StreamingResponse(
                file_stream,
                media_type=content_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{latest_document.name}{file_extension}"'
                }
            )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error downloading latest document: {str(e)}"
        )


@router.post("/cnc-program/init-document-type")
async def init_cnc_program_document_type(
        current_user: User = Depends(get_current_user)
):
    """Initialize CNC program document type if it doesn't exist"""
    try:
        with db_session:
            # Check if already exists
            doc_type = DocumentTypeV2.get(name=DocumentTypes.CNC_PROGRAM.value)
            if doc_type:
                return {
                    "id": doc_type.id,
                    "name": doc_type.name,
                    "description": doc_type.description,
                    "allowed_extensions": doc_type.allowed_extensions,
                    "is_active": doc_type.is_active
                }

            # Also check for uppercase version for consistency
            doc_type = DocumentTypeV2.get(name="CNC_PROGRAM")
            if doc_type:
                return {
                    "id": doc_type.id,
                    "name": doc_type.name,
                    "description": doc_type.description,
                    "allowed_extensions": doc_type.allowed_extensions,
                    "is_active": doc_type.is_active
                }

            # CNC program extensions
            cnc_program_extensions = [
                ".NC", ".TXT", ".CNC", ".EIA", ".ISO", ".H",
                ".PGM", ".MIN", ".MZK", ".APL", ".ARF",
                ".SUB", ".DNC", ".MPF", ".SPF"
            ]

            try:
                # Create document type
                new_doc_type = DocumentTypeV2(
                    name=DocumentTypes.CNC_PROGRAM.value,
                    description="CNC Program Files",
                    allowed_extensions=cnc_program_extensions,
                    is_active=True
                )
                commit()

                return {
                    "id": new_doc_type.id,
                    "name": new_doc_type.name,
                    "description": new_doc_type.description,
                    "allowed_extensions": new_doc_type.allowed_extensions,
                    "is_active": new_doc_type.is_active
                }
            except Exception as transaction_error:
                # If there was an error, check if the type was created by another process
                doc_type = DocumentTypeV2.get(name=DocumentTypes.CNC_PROGRAM.value)
                if doc_type:
                    return {
                        "id": doc_type.id,
                        "name": doc_type.name,
                        "description": doc_type.description,
                        "allowed_extensions": doc_type.allowed_extensions,
                        "is_active": doc_type.is_active
                    }
                else:
                    # Re-raise the error if we still can't find the document type
                    raise transaction_error

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize CNC program document type: {str(e)}"
        )


@router.post("/cnc-program/upload/", response_model=DocumentResponse)
async def upload_cnc_program(
        file: UploadFile = File(...),
        part_number: str = Form(...),
        operation_number: str = Form(...),
        program_name: str = Form(...),
        description: Optional[str] = Form(None),
        version_number: str = Form(default="1.0"),
        metadata: Optional[str] = Form("{}"),
        current_user: User = Depends(get_current_user)
):
    """Upload a CNC program file for a specific part number and operation"""
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # Get or create CNC program document type by both the enum value and upper case version
            doc_type_obj = DocumentTypeV2.get(name=DocumentTypes.CNC_PROGRAM.value)
            if not doc_type_obj:
                # Try with all caps version too (for legacy compatibility)
                doc_type_obj = DocumentTypeV2.get(name="CNC_PROGRAM")

            if not doc_type_obj:
                # Create the document type if it doesn't exist
                cnc_program_extensions = [
                    ".NC", ".TXT", ".CNC", ".EIA", ".ISO", ".H",
                    ".PGM", ".MIN", ".MZK", ".APL", ".ARF",
                    ".SUB", ".DNC", ".MPF", ".SPF"
                ]

                doc_type_obj = DocumentTypeV2(
                    name=DocumentTypes.CNC_PROGRAM.value,
                    description="CNC Program Files",
                    allowed_extensions=cnc_program_extensions,
                    is_active=True
                )
                commit()

            # Get or create root folder for CNC programs
            root_folder = FolderV2.get(name="CNCPrograms", parent_folder=None)
            if not root_folder:
                root_folder = FolderV2(
                    name="CNCPrograms",
                    path="CNCPrograms",
                    created_by=user
                )
                commit()

            # Get or create part_number folder
            part_folder_name = f"PN_{part_number}"
            part_folder = FolderV2.get(lambda f: f.name == part_folder_name and f.parent_folder == root_folder)
            if not part_folder:
                part_folder = FolderV2(
                    name=part_folder_name,
                    path=f"CNCPrograms/{part_folder_name}",
                    parent_folder=root_folder,
                    created_by=user
                )
                commit()

            # Get or create operation folder
            op_folder_name = f"OP_{operation_number}"
            op_folder = FolderV2.get(lambda f: f.name == op_folder_name and f.parent_folder == part_folder)
            if not op_folder:
                op_folder = FolderV2(
                    name=op_folder_name,
                    path=f"CNCPrograms/{part_folder_name}/{op_folder_name}",
                    parent_folder=part_folder,
                    created_by=user
                )
                commit()

            # Validate file extension
            file_ext = os.path.splitext(file.filename)[1].upper()
            if not any(ext.upper() == file_ext.upper() for ext in doc_type_obj.allowed_extensions):
                raise HTTPException(
                    status_code=400,
                    detail=f"File type {file_ext} not allowed for CNC programs. Allowed types: {doc_type_obj.allowed_extensions}"
                )

            # Add program info to metadata
            try:
                metadata_dict = json.loads(metadata)
                metadata_dict.update({
                    "part_number": part_number,
                    "operation_number": operation_number,
                    "program_path": file.filename
                })
                metadata = json.dumps(metadata_dict)
            except json.JSONDecodeError:
                metadata = json.dumps({
                    "part_number": part_number,
                    "operation_number": operation_number,
                    "program_path": file.filename
                })

            # Create document
            new_doc = DocumentV2(
                name=program_name,
                folder=op_folder,
                doc_type=doc_type_obj,
                description=description,
                part_number=part_number,
                created_by=user
            )
            commit()

            # Handle file upload and version creation
            file_content = await file.read()
            checksum = hashlib.sha256(file_content).hexdigest()
            minio_path = f"documents/cnc_programs/{part_number}/op{operation_number}/{new_doc.id}/v{version_number}/{file.filename}"

            try:
                file.file.seek(0)
                minio.upload_file(
                    file=file.file,
                    object_name=minio_path,
                    content_type=file.content_type or "application/octet-stream"
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

            # Create version
            version = DocumentVersionV2(
                document=new_doc,
                version_number=version_number,
                minio_path=minio_path,
                file_size=len(file_content),
                checksum=checksum,
                created_by=user,
                metadata=json.loads(metadata)
            )
            new_doc.latest_version = version

            # Create access log
            DocumentAccessLogV2(
                document=new_doc,
                version=version,
                user=user,
                action_type=DocumentAction.UPDATE,
                ip_address="0.0.0.0"
            )

            commit()

            return {
                "id": new_doc.id,
                "name": new_doc.name,
                "folder_id": new_doc.folder.id,
                "doc_type_id": new_doc.doc_type.id,
                "description": new_doc.description,
                "part_number": new_doc.part_number,
                "production_order_id": None,
                "created_at": new_doc.created_at,
                "created_by_id": new_doc.created_by.id,
                "is_active": new_doc.is_active,
                "latest_version": {
                    "id": version.id,
                    "document_id": new_doc.id,
                    "version_number": version.version_number,
                    "minio_path": version.minio_path,
                    "file_size": version.file_size,
                    "checksum": version.checksum,
                    "created_at": version.created_at,
                    "created_by_id": version.created_by.id,
                    "is_active": version.is_active,
                    "metadata": version.metadata
                }
            }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.post("/cnc-program/{document_id}/versions", response_model=DocumentVersionResponse)
async def create_cnc_program_version(
        document_id: int,
        file: UploadFile = File(...),
        version_number: str = Form(...),
        metadata: str = Form(default="{}"),
        current_user: User = Depends(get_current_user)
):
    """Add a new version to an existing CNC program document"""
    try:
        with db_session:
            # Get the document and user
            document = DocumentV2.get(id=document_id)
            user = User.get(id=current_user.id)

            if not document or not document.is_active:
                raise HTTPException(status_code=404, detail="Document not found")

            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # Check if this is a CNC program document
            if document.doc_type.name != DocumentTypes.CNC_PROGRAM.value:
                raise HTTPException(status_code=400, detail="Not a CNC program document")

            # Extract path information from folder path
            path_parts = document.folder.path.split('/')
            if len(path_parts) < 3 or path_parts[0] != "CNCPrograms":
                raise HTTPException(status_code=400, detail="Invalid document folder structure")

            part_number = path_parts[1].replace('PN_', '')
            operation_number = path_parts[2].replace('OP_', '')

            # Parse metadata
            try:
                metadata_dict = json.loads(metadata)
                metadata_dict.update({
                    "part_number": part_number,
                    "operation_number": operation_number,
                    "program_path": file.filename
                })
            except json.JSONDecodeError:
                metadata_dict = {
                    "part_number": part_number,
                    "operation_number": operation_number,
                    "program_path": file.filename
                }

            # Validate file extension
            file_ext = os.path.splitext(file.filename)[1].upper()
            if file_ext not in document.doc_type.allowed_extensions:
                raise HTTPException(
                    status_code=400,
                    detail=f"File type {file_ext} not allowed for CNC programs"
                )

            # Handle file upload and version creation
            file_content = await file.read()
            checksum = hashlib.sha256(file_content).hexdigest()
            minio_path = f"documents/cnc_programs/{part_number}/op{operation_number}/{document_id}/v{version_number}/{file.filename}"

            try:
                file.file.seek(0)
                minio.upload_file(
                    file=file.file,
                    object_name=minio_path,
                    content_type=file.content_type or "application/octet-stream"
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

            # Create version
            version = DocumentVersionV2(
                document=document,
                version_number=version_number,
                minio_path=minio_path,
                file_size=len(file_content),
                checksum=checksum,
                created_by=user,
                metadata=metadata_dict
            )

            # Update document's latest version
            document.latest_version = version

            # Create access log
            DocumentAccessLogV2(
                document=document,
                version=version,
                user=user,
                action_type=DocumentAction.UPDATE,
                ip_address="0.0.0.0"
            )

            commit()

            return {
                "id": version.id,
                "document_id": document.id,
                "version_number": version.version_number,
                "minio_path": version.minio_path,
                "file_size": version.file_size,
                "checksum": version.checksum,
                "created_at": version.created_at,
                "created_by_id": version.created_by.id,
                "is_active": version.is_active,
                "metadata": version.metadata
            }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


class DocumentWithAllVersionsResponse(BaseModel):
    id: int
    name: str
    folder_id: int
    doc_type_id: int
    description: str | None = None
    part_number: str | None = None
    production_order_id: int | None = None
    created_at: datetime
    created_by_id: int
    is_active: bool
    latest_version: DocumentVersionResponse | None = None
    all_versions: List[DocumentVersionResponse] = []

    class Config:
        from_attributes = True


@router.get("/cnc-program/by-part-op/{part_number}/{operation_number}",
            response_model=List[DocumentWithAllVersionsResponse])
async def get_cnc_programs_by_part_and_operation(
        part_number: str,
        operation_number: str,
        current_user: User = Depends(get_current_user)
):
    """Get all CNC programs with all versions for a specific part number and operation"""
    try:
        with db_session:
            # Get folder path
            part_folder_name = f"PN_{part_number}"
            op_folder_name = f"OP_{operation_number}"
            folder_path = f"CNCPrograms/{part_folder_name}/{op_folder_name}"

            # Get the operation folder
            folder = FolderV2.get(path=folder_path)
            if not folder:
                return []

            # Get documents in this folder
            documents = list(DocumentV2.select(lambda d: d.folder == folder and
                                                         d.doc_type.name == DocumentTypes.CNC_PROGRAM.value and
                                                         d.is_active))

            # Format response
            result = []
            for doc in documents:
                # Get all versions for this document
                versions = list(DocumentVersionV2.select(lambda v: v.document == doc and v.is_active).order_by(
                    desc(DocumentVersionV2.created_at)))

                # Format all versions
                formatted_versions = [
                    {
                        "id": version.id,
                        "document_id": doc.id,
                        "version_number": version.version_number,
                        "minio_path": version.minio_path,
                        "file_size": version.file_size,
                        "checksum": version.checksum,
                        "created_at": version.created_at,
                        "created_by_id": version.created_by.id,
                        "is_active": version.is_active,
                        "metadata": version.metadata
                    }
                    for version in versions
                ]

                # Create document response with all versions
                doc_response = {
                    "id": doc.id,
                    "name": doc.name,
                    "folder_id": doc.folder.id,
                    "doc_type_id": doc.doc_type.id,
                    "description": doc.description,
                    "part_number": doc.part_number,
                    "production_order_id": doc.production_order.id if doc.production_order else None,
                    "created_at": doc.created_at,
                    "created_by_id": doc.created_by.id,
                    "is_active": doc.is_active,
                    "latest_version": {
                        "id": doc.latest_version.id,
                        "document_id": doc.id,
                        "version_number": doc.latest_version.version_number,
                        "minio_path": doc.latest_version.minio_path,
                        "file_size": doc.latest_version.file_size,
                        "checksum": doc.latest_version.checksum,
                        "created_at": doc.latest_version.created_at,
                        "created_by_id": doc.latest_version.created_by.id,
                        "is_active": doc.latest_version.is_active,
                        "metadata": doc.latest_version.metadata
                    } if doc.latest_version else None,
                    "all_versions": formatted_versions
                }

                result.append(doc_response)

                # Create access log entry for this view
                DocumentAccessLogV2(
                    document=doc,
                    user=User.get(id=current_user.id),
                    action_type=DocumentAction.VIEW,
                    ip_address="0.0.0.0"
                )

            commit()
            return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/cnc-program/by-part/{part_number}", response_model=List[DocumentWithAllVersionsResponse])
async def get_cnc_programs_by_part(
        part_number: str,
        current_user: User = Depends(get_current_user)
):
    """Get all CNC programs with all versions for a specific part number across all operations"""
    try:
        with db_session:
            # Get all documents with the given part number and CNC program type
            documents = list(DocumentV2.select(lambda d: d.part_number == part_number and
                                                         d.doc_type.name == DocumentTypes.CNC_PROGRAM.value and
                                                         d.is_active))

            # Format response
            result = []
            for doc in documents:
                # Get all versions for this document
                versions = list(DocumentVersionV2.select(lambda v: v.document == doc and v.is_active).order_by(
                    desc(DocumentVersionV2.created_at)))

                # Format all versions
                formatted_versions = [
                    {
                        "id": version.id,
                        "document_id": doc.id,
                        "version_number": version.version_number,
                        "minio_path": version.minio_path,
                        "file_size": version.file_size,
                        "checksum": version.checksum,
                        "created_at": version.created_at,
                        "created_by_id": version.created_by.id,
                        "is_active": version.is_active,
                        "metadata": version.metadata
                    }
                    for version in versions
                ]

                # Create document response with all versions
                doc_response = {
                    "id": doc.id,
                    "name": doc.name,
                    "folder_id": doc.folder.id,
                    "doc_type_id": doc.doc_type.id,
                    "description": doc.description,
                    "part_number": doc.part_number,
                    "production_order_id": doc.production_order.id if doc.production_order else None,
                    "created_at": doc.created_at,
                    "created_by_id": doc.created_by.id,
                    "is_active": doc.is_active,
                    "latest_version": {
                        "id": doc.latest_version.id,
                        "document_id": doc.id,
                        "version_number": doc.latest_version.version_number,
                        "minio_path": doc.latest_version.minio_path,
                        "file_size": doc.latest_version.file_size,
                        "checksum": doc.latest_version.checksum,
                        "created_at": doc.latest_version.created_at,
                        "created_by_id": doc.latest_version.created_by.id,
                        "is_active": doc.latest_version.is_active,
                        "metadata": doc.latest_version.metadata
                    } if doc.latest_version else None,
                    "all_versions": formatted_versions
                }

                result.append(doc_response)

                # Create access log entry for this view
                DocumentAccessLogV2(
                    document=doc,
                    user=User.get(id=current_user.id),
                    action_type=DocumentAction.VIEW,
                    ip_address="0.0.0.0"
                )

            commit()
            return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/cnc-program/{document_id}/versions", response_model=List[DocumentVersionResponse])
async def list_cnc_program_versions(
        document_id: int,
        current_user: User = Depends(get_current_user)
):
    """List all versions of a CNC program document"""
    try:
        with db_session:
            document = DocumentV2.get(id=document_id)
            if not document or not document.is_active:
                raise HTTPException(status_code=404, detail="Document not found")

            # Verify document type
            if document.doc_type.name != DocumentTypes.CNC_PROGRAM.value:
                raise HTTPException(status_code=400, detail="Not a CNC program document")

            # Get all versions
            versions = list(DocumentVersionV2.select(lambda v: v.document == document and v.is_active).order_by(
                desc(DocumentVersionV2.created_at)))

            # Create access log entry
            DocumentAccessLogV2(
                document=document,
                user=User.get(id=current_user.id),
                action_type=DocumentAction.VIEW,
                ip_address="0.0.0.0"
            )
            commit()

            return [
                {
                    "id": version.id,
                    "document_id": document.id,
                    "version_number": version.version_number,
                    "minio_path": version.minio_path,
                    "file_size": version.file_size,
                    "checksum": version.checksum,
                    "created_at": version.created_at,
                    "created_by_id": version.created_by.id,
                    "is_active": version.is_active,
                    "metadata": version.metadata
                }
                for version in versions
            ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/cnc-program/{document_id}/download")
async def download_cnc_program(
        document_id: int,
        version_id: int | None = Query(None, description="Specific version to download, omit for latest"),
        current_user: User = Depends(get_current_user)
):
    """Download a specific CNC program document, either the latest version or a specific version"""
    try:
        with db_session:
            # Get the document
            document = DocumentV2.get(id=document_id)
            if not document or not document.is_active:
                raise HTTPException(status_code=404, detail="Document not found")

            # Verify document type
            if document.doc_type.name != DocumentTypes.CNC_PROGRAM.value:
                raise HTTPException(status_code=400, detail="Not a CNC program document")

            # Determine which version to download
            version = None
            if version_id:
                version = DocumentVersionV2.get(id=version_id, document=document)
                if not version or not version.is_active:
                    raise HTTPException(status_code=404, detail="Document version not found")
            else:
                version = document.latest_version
                if not version:
                    raise HTTPException(status_code=404, detail="No available version for this document")

            # Get file from MinIO
            try:
                file_data = minio.download_file(version.minio_path)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to download file: {str(e)}")

            # Extract filename from minio_path
            filename = version.minio_path.split('/')[-1]

            # Log access
            DocumentAccessLogV2(
                document=document,
                version=version,
                user=User.get(id=current_user.id),
                action_type=DocumentAction.DOWNLOAD,
                ip_address="0.0.0.0"
            )
            commit()

            # Return file as a streaming response
            return StreamingResponse(
                file_data,
                media_type="application/octet-stream",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/cnc-program/document-type")
async def get_cnc_program_document_type(
        current_user: User = Depends(get_current_user)
):
    """Get the CNC program document type ID if it exists, or 404 if not"""
    try:
        with db_session:
            # Check if already exists
            doc_type = DocumentTypeV2.get(name=DocumentTypes.CNC_PROGRAM.value)
            if not doc_type:
                # Also check for uppercase version for consistency
                doc_type = DocumentTypeV2.get(name="CNC_PROGRAM")

            if not doc_type:
                raise HTTPException(status_code=404, detail="CNC Program document type not found")

            return {
                "id": doc_type.id,
                "name": doc_type.name,
                "description": doc_type.description,
                "allowed_extensions": doc_type.allowed_extensions,
                "is_active": doc_type.is_active
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving CNC program document type: {str(e)}"
        )