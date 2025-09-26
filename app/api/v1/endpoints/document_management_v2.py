import os
import tempfile

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Path, status, Form
from typing import List, Union, Annotated, Optional, Dict, Any
from collections import defaultdict
import io

import pandas as pd
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, PageBreak
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
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
import PyPDF2


def get_filename_from_path(path: str) -> str:
    """
    Extract filename from a path string.
    Args:
        path (str): The full path string
    Returns:
        str: The extracted filename
    """
    return os.path.basename(path)

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


@router.get("/documents/analytics")
async def get_document_analytics(current_user=Depends(get_current_user)):
    """Get comprehensive analytics for document management"""
    try:
        with db_session:
            # Total documents
            total_documents = select(d for d in DocumentV2 if d.is_active).count()
            # Total versions
            total_versions = select(v for v in DocumentVersionV2 if v.is_active).count()
            # Total downloads
            total_downloads = select(l for l in DocumentAccessLogV2 if l.action_type == "DOWNLOAD").count()
            # Total storage (sum of all active version file sizes)
            total_storage = select(sum(v.file_size) for v in DocumentVersionV2 if v.is_active).first() or 0

            # Document counts by type
            docs_by_type = select((d.doc_type.name, count(d)) for d in DocumentV2 if d.is_active).fetch()
            # Download counts by type
            downloads_by_type = {}
            for dt in DocumentTypeV2.select(lambda t: t.is_active):
                downloads_by_type[dt.name] = select(l for l in DocumentAccessLogV2 if l.action_type == "DOWNLOAD" and l.document.doc_type == dt).count()
            # Storage by type
            storage_by_type = {}
            for dt in DocumentTypeV2.select(lambda t: t.is_active):
                storage_by_type[dt.name] = select(sum(v.file_size) for v in DocumentVersionV2 if v.is_active and v.document.doc_type == dt).first() or 0
            # Versions by type
            versions_by_type = {}
            for dt in DocumentTypeV2.select(lambda t: t.is_active):
                versions_by_type[dt.name] = select(v for v in DocumentVersionV2 if v.is_active and v.document.doc_type == dt).count()

            # List all document types with their metadata
            doc_types = [
                {
                    "id": dt.id,
                    "name": dt.name,
                    "description": dt.description,
                    "allowed_extensions": dt.allowed_extensions,
                    "is_active": dt.is_active,
                    "document_count": docs_by_type.get(dt.name, 0) if isinstance(docs_by_type, dict) else dict(docs_by_type).get(dt.name, 0),
                    "download_count": downloads_by_type.get(dt.name, 0),
                    "storage": storage_by_type.get(dt.name, 0),
                    "version_count": versions_by_type.get(dt.name, 0)
                }
                for dt in DocumentTypeV2.select(lambda t: t.is_active)
            ]

            return {
                "total_documents": total_documents,
                "total_versions": total_versions,
                "total_downloads": total_downloads,
                "total_storage": total_storage,
                "documents_by_type": dict(docs_by_type),
                "downloads_by_type": downloads_by_type,
                "storage_by_type": storage_by_type,
                "versions_by_type": versions_by_type,
                "document_types": doc_types
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
        current_user=Depends(get_current_user)
):
    """List documents with optional filters (no pagination)"""
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

            # Get all documents (no pagination)
            documents = list(base_query.order_by(lambda d: desc(d.created_at)))
            total = len(documents)

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

            # Get file extension from minio path
            file_extension = version.minio_path.split('.')[
                -1] if '.' in version.minio_path else ''
            filename = f"{document.name}.{file_extension}" if file_extension else document.name

            return StreamingResponse(
                file_data,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"'
                }
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


# @router.get("/documents/{document_id}/download-latest")
# async def download_latest_document(
#         document_id: int,
#         current_user=Depends(get_current_user)
# ):
#     """Download the latest version of a document"""
#     try:
#         with db_session:
#             document = DocumentV2.get(id=document_id)
#             if not document:
#                 raise HTTPException(
#                     status_code=status.HTTP_404_NOT_FOUND,
#                     detail="Document not found"
#                 )

#             if not document.latest_version:
#                 raise HTTPException(
#                     status_code=status.HTTP_404_NOT_FOUND,
#                     detail="No version available for this document"
#                 )

#             # Log access
#             DocumentAccessLogV2(
#                 document=document,
#                 version=document.latest_version,
#                 user=User.get(id=current_user.id),
#                 action_type=DocumentAction.DOWNLOAD,
#                 ip_address="0.0.0.0"
#             )
#             commit()

#             try:
#                 # Get file from MinIO
#                 file_data = minio.download_file(document.latest_version.minio_path)

#                 # Get file extension from minio path
#                 file_extension = document.latest_version.minio_path.split('.')[
#                     -1] if '.' in document.latest_version.minio_path else ''
#                 filename = f"{document.name}.{file_extension}" if file_extension else document.name

#                 # Determine content type based on file extension
#                 content_type = "application/octet-stream"
#                 if file_extension.lower() in ['pdf']:
#                     content_type = "application/pdf"
#                 elif file_extension.lower() in ['doc', 'docx']:
#                     content_type = "application/msword"
#                 elif file_extension.lower() in ['xls', 'xlsx']:
#                     content_type = "application/vnd.ms-excel"

#                 return StreamingResponse(
#                     file_data,
#                     media_type=content_type,
#                     headers={
#                         "Content-Disposition": f'attachment; filename="{filename}"',
#                         # CORS headers to expose Content-Disposition
#                         "Access-Control-Expose-Headers": "Content-Disposition, Content-Type, Content-Length",
#                         "Access-Control-Allow-Origin": "*",  # Replace with your frontend domain for security
#                         "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
#                         "Access-Control-Allow-Headers": "Content-Type, Authorization"
#                     }
#                 )
#             except Exception as e:
#                 raise HTTPException(
#                     status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#                     detail=f"Failed to download file: {str(e)}"
#                 )
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"An error occurred: {str(e)}"
#         )


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

                # Get file from MinIO
            file_data = minio.download_file(document.latest_version.minio_path)

                # Get file extension from minio path
            file_extension = document.latest_version.minio_path.split('.')[
                -1] if '.' in document.latest_version.minio_path else ''
            filename = f"{document.name}.{file_extension}" if file_extension else document.name

            return StreamingResponse(
                file_data,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"'
                }
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
            root_folder = FolderV2.get(path="Document Types")
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
        part_number: str = Form(...),
        operation_number: int = Form(...),
        document_name: str = Form(...),
        description: Optional[str] = Form(None),
        version_number: str = Form(...),
        metadata: Optional[str] = Form("{}"),
        current_user: User = Depends(get_current_user)
):
    """Upload an in-process document for a specific part number and operation"""
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
            root_path = "Document Types"
            root_folder = FolderV2.get(lambda f: f.path == root_path)
            if not root_folder:
                root_folder = FolderV2(
                    name="Document Types",
                    path=root_path,
                    created_by=user
                )
                commit()
            elif not root_folder.is_active:
                root_folder.is_active = True
                commit()

            # Get or create IPID folder
            ipid_path = f"{root_path}/{DocumentTypes.IPID.value}"
            ipid_folder = FolderV2.get(lambda f: f.path == ipid_path)
            if not ipid_folder:
                ipid_folder = FolderV2(
                    name=DocumentTypes.IPID.value,
                    path=ipid_path,
                    parent_folder=root_folder,
                    created_by=user
                )
                commit()
            elif not ipid_folder.is_active:
                ipid_folder.is_active = True
                commit()

            # Get or create part number folder
            part_path = f"{ipid_path}/{part_number}"
            part_folder = FolderV2.get(lambda f: f.path == part_path)
            if not part_folder:
                part_folder = FolderV2(
                    name=part_number,
                    path=part_path,
                    parent_folder=ipid_folder,
                    created_by=user
                )
                commit()
            elif not part_folder.is_active:
                part_folder.is_active = True
                commit()

            # Get or create operation folder
            op_path = f"{part_path}/OP{operation_number}"
            op_folder = FolderV2.get(lambda f: f.path == op_path)
            if not op_folder:
                op_folder = FolderV2(
                    name=f"OP{operation_number}",
                    path=op_path,
                    parent_folder=part_folder,
                    created_by=user
                )
                commit()
            elif not op_folder.is_active:
                op_folder.is_active = True
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
                part_number=part_number,  # Use part_number instead of production_order
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


@router.get("/ipid/by-po/{production_order}", response_model=List[DocumentResponse])
async def get_ipid_documents_by_po(
        production_order: str,
        current_user: User = Depends(get_current_user)
):
    """Get all IPID documents for a specific production order across all operations"""
    try:
        with db_session:
            # Get IPID document type using DocumentTypes enum
            doc_type = DocumentTypeV2.get(name=DocumentTypes.IPID.value)
            if not doc_type:
                raise HTTPException(status_code=404, detail="IPID document type not found")

            # Get the order
            order = Order.get(production_order=production_order)
            if not order:
                raise HTTPException(status_code=404, detail="Production order not found")

            # Query documents using production_order
            documents = select(d for d in DocumentV2
                               if d.production_order == order
                               and d.doc_type == doc_type
                               and d.is_active == True)[:]

            # Format response according to DocumentResponse model
            response = []
            for doc in documents:
                if not doc.latest_version:
                    continue

                # Extract operation number from metadata if available
                operation_number = None
                if doc.latest_version.metadata and isinstance(doc.latest_version.metadata, dict):
                    operation_number = doc.latest_version.metadata.get("operation_number")

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
                        "metadata": {
                            **(doc.latest_version.metadata or {}),
                            "operation_number": operation_number
                        }
                    }
                }
                response.append(doc_response)

            # Sort response by operation number if available
            response.sort(
                key=lambda x: (
                    int(x["latest_version"]["metadata"].get("operation_number", 999999))
                    if x["latest_version"]["metadata"].get("operation_number") is not None
                    else 999999
                )
            )

            return response

    except Exception as e:
        logger.error(f"Error retrieving IPID documents: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving IPID documents: {str(e)}"
        )


# @router.get("/documents/download-latest_new/{part_number}/{doc_type}")
# async def download_latest_document_new_endpoint(
#         part_number: str,
#         doc_type: DocumentTypes,
#         operation_number: int | None = Query(None, description="Required for IPID documents"),
#         current_user: User = Depends(get_current_user)
# ):
#     """Download latest version of a document by part number and document type"""
#     try:
#         with db_session:
#             # Get document type
#             doc_type_obj = DocumentTypeV2.get(name=doc_type.value)
#             if not doc_type_obj:
#                 raise HTTPException(
#                     status_code=404,
#                     detail=f"Document type {doc_type.value} not found"
#                 )

#             # Check if operation number is provided for IPID documents
#             if doc_type == DocumentTypes.IPID:
#                 if operation_number is None:
#                     raise HTTPException(
#                         status_code=400,
#                         detail="Operation number is required for IPID documents"
#                     )

#                 # Get all documents first
#                 documents = list(DocumentV2.select(
#                     lambda d: d.part_number == part_number and
#                               d.doc_type.id == doc_type_obj.id and
#                               d.is_active and
#                               d.latest_version
#                 ).order_by(lambda d: desc(d.created_at)))

#                 # Then filter for matching operation number
#                 document = None
#                 for doc in documents:
#                     try:
#                         metadata = doc.latest_version.metadata
#                         if isinstance(metadata, str):
#                             metadata = json.loads(metadata)
#                         if metadata.get("operation_number") == operation_number:
#                             document = doc
#                             break
#                     except (json.JSONDecodeError, AttributeError):
#                         continue
#             else:
#                 # For other document types, get latest document
#                 document = DocumentV2.select(
#                     lambda d: d.part_number == part_number and
#                               d.doc_type.id == doc_type_obj.id and
#                               d.is_active
#                 ).order_by(lambda d: desc(d.created_at)).first()

#             if not document or not document.latest_version:
#                 error_msg = (f"No {doc_type.value} document found for part number {part_number}"
#                              f"{f' and operation {operation_number}' if doc_type == DocumentTypes.IPID else ''}")
#                 raise HTTPException(status_code=404, detail=error_msg)

#             # Log access
#             DocumentAccessLogV2(
#                 document=document,
#                 version=document.latest_version,
#                 user=User.get(id=current_user.id),
#                 action_type=DocumentAction.DOWNLOAD,
#                 ip_address="0.0.0.0"
#             )
#             commit()

#             try:
#                 file_data = minio.download_file(document.latest_version.minio_path)

#                 # Generate filename based on document type
#                 if doc_type == DocumentTypes.IPID:
#                     filename = f"{part_number}_OP{operation_number}_{doc_type.value}_{document.latest_version.version_number}.{document.latest_version.minio_path.split('.')[-1]}"
#                 else:
#                     filename = f"{part_number}_{doc_type.value}_{document.latest_version.version_number}.{document.latest_version.minio_path.split('.')[-1]}"

#                 return StreamingResponse(
#                     file_data,
#                     media_type="application/octet-stream",
#                     headers={
#                         "Content-Disposition": f'attachment; filename="{filename}"'
#                     }
#                 )
#             except Exception as e:
#                 raise HTTPException(
#                     status_code=500,
#                     detail=f"Failed to download file: {str(e)}"
#                 )

#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"An error occurred: {str(e)}"
#         )



# @router.get("/documents/download-latest_new/{part_number}/{doc_type}")
# async def download_latest_document_new_endpoint(
#         part_number: str,
#         doc_type: DocumentTypes,
#         operation_number: int | None = Query(None, description="Required for IPID documents"),
#         current_user: User = Depends(get_current_user)
# ):
#     """Download latest version of a document by part number and document type"""
#     try:
#         with db_session:
#             # Get document type
#             doc_type_obj = DocumentTypeV2.get(name=doc_type.value)
#             if not doc_type_obj:
#                 raise HTTPException(
#                     status_code=404,
#                     detail=f"Document type {doc_type.value} not found"
#                 )

#             def check_file_versions(doc_id, operation_number):
#                 """Check for all versions of a file"""
#                 found_versions = []
#                 base_path = f"documents/v2/Document Types/{doc_type.value}/{part_number}"
#                 if operation_number is not None:
#                     base_path = f"{base_path}/OP{operation_number}"
#                 base_path = f"{base_path}/{doc_id}"
                
#                 try:
#                     # List all objects in the base path to find version folders
#                     objects = minio.client.list_objects(bucket_name="documents3", prefix=base_path + "/")
#                     version_folders = set()
#                     for obj in objects:
#                         # Extract version folder from path
#                         parts = obj.object_name.split('/')
#                         for part in parts:
#                             if part.startswith('v') and len(part) > 1 and part[1:].replace('.', '').isdigit():
#                                 version_folders.add(part)
                    
#                     print(f"Found version folders: {sorted(version_folders)}")
                    
#                     if not version_folders:
#                         print(f"No version folders found in {base_path}")
#                         return found_versions
                    
#                     # Sort version folders by version number
#                     sorted_versions = sorted(version_folders, key=lambda v: [float(x) if '.' in x else int(x) for x in v[1:].split('.')])
#                     print(f"Sorted version folders: {sorted_versions}")
                    
#                     # Try each version folder, starting from highest
#                     for v in reversed(sorted_versions):
#                         try:
#                             path = f"{base_path}/{v}"
#                             # List files in this version folder
#                             version_objects = minio.client.list_objects(bucket_name="documents3", prefix=path + "/")
#                             for obj in version_objects:
#                                 try:
#                                     print(f"Checking path: {obj.object_name}")
#                                     # Try to get the file
#                                     stream = minio.download_file(obj.object_name)
#                                     version_num = int(v[1:]) if v[1:].isdigit() else float(v[1:])
#                                     found_versions.append((obj.object_name, version_num))
#                                     print(f"Found file in version {v}: {obj.object_name}")
#                                 except Exception as e:
#                                     print(f"Error accessing file {obj.object_name}: {str(e)}")
#                                     continue
#                         except Exception as e:
#                             print(f"Error listing files in version {v}: {path}, error: {str(e)}")
#                             continue
#                 except Exception as e:
#                     print(f"Error listing objects in path {base_path}: {str(e)}")
                
#                 # Sort found versions by version number and return all of them
#                 found_versions.sort(key=lambda x: x[1], reverse=True)
#                 return found_versions

#             # Special handling for IPID documents
#             if doc_type == DocumentTypes.IPID:
#                 if operation_number is None:
#                     raise HTTPException(
#                         status_code=400,
#                         detail="Operation number is required for IPID documents"
#                     )

#                 # Get all active documents for this part number
#                 documents = list(DocumentV2.select(
#                     lambda d: d.part_number == part_number and
#                               d.doc_type.id == doc_type_obj.id and
#                               d.is_active
#                 ))

#                 print(f"Found {len(documents)} active documents for part number {part_number}")

#                 # Find all matching versions across all documents
#                 matching_versions = []
#                 for doc in documents:
#                     print(f"Processing document {doc.id}")
                    
#                     # Get database versions
#                     db_versions = list(DocumentVersionV2.select(
#                         lambda v: v.document == doc and v.is_active
#                     ))
                    
#                     print(f"Found {len(db_versions)} database versions for document {doc.id}")
                    
#                     # Process each version
#                     for version in db_versions:
#                         try:
#                             metadata = version.metadata
#                             if isinstance(metadata, str):
#                                 metadata = json.loads(metadata)
                            
#                             print(f"Version {version.id} metadata: {metadata}")
#                             stored_op_num = metadata.get("operation_number")
#                             # Convert operation numbers to integers for comparison
#                             if isinstance(stored_op_num, str):
#                                 stored_op_num = int(stored_op_num)
                            
#                             if stored_op_num == operation_number:
#                                 print(f"Found matching operation number {operation_number} in version {version.id}")
#                                 # Check all version folders
#                                 found_versions = check_file_versions(doc.id, operation_number)
#                                 for path, ver_num in found_versions:
#                                     matching_versions.append((doc, version, [ver_num], path))
#                                     print(f"Found matching version: doc_id={doc.id}, version_id={version.id}, version_path={path}, version_num={ver_num}, created_at={version.created_at}")
#                         except (json.JSONDecodeError, AttributeError, ValueError) as e:
#                             print(f"Error processing version {version.id} metadata: {str(e)}")
#                             continue

#                 if not matching_versions:
#                     # Try to provide more detailed error message
#                     if not documents:
#                         error_detail = f"No documents found for part number {part_number}"
#                     else:
#                         error_detail = f"No IPID document found for part number {part_number} and operation {operation_number}. "
#                         error_detail += f"Found {len(documents)} documents but none match the operation number."
                    
#                     raise HTTPException(
#                         status_code=404,
#                         detail=error_detail
#                     )

#                 # Sort by version number (primary) and creation date (secondary)
#                 matching_versions.sort(key=lambda x: (x[2], x[1].created_at.timestamp() if x[1].created_at else 0), reverse=True)
#                 document, version, version_num, minio_path = matching_versions[0]
#                 print(f"Selected latest version: doc_id={document.id}, version_id={version.id}, version_path={minio_path}, version_num={version_num}, created_at={version.created_at}")
                
#                 # Instead of updating the version's path, use the found path directly
#                 try:
#                     file_data = minio.download_file(minio_path)
                    
#                     # Generate filename based on document type
#                     filename = f"PO{part_number}_OP{operation_number}_{doc_type.value}_v{version_num[0]}.{minio_path.split('.')[-1]}"

#                     return StreamingResponse(
#                         file_data,
#                         media_type="application/octet-stream",
#                         headers={
#                             "Content-Disposition": f'attachment; filename="{filename}"'
#                         }
#                     )
#                 except Exception as e:
#                     raise HTTPException(
#                         status_code=500,
#                         detail=f"Failed to download file: {str(e)}"
#                     )

#             else:
#                 # For other document types, get all active documents
#                 documents = list(DocumentV2.select(
#                     lambda d: d.part_number == part_number and
#                               d.doc_type.id == doc_type_obj.id and
#                               d.is_active
#                 ))

#                 if not documents:
#                     raise HTTPException(
#                         status_code=404,
#                         detail=f"No {doc_type.value} document found for part number {part_number}"
#                     )

#                 # Get all versions across all documents
#                 all_versions = []
#                 for doc in documents:
#                     # Get database versions
#                     db_versions = list(DocumentVersionV2.select(
#                         lambda v: v.document == doc and v.is_active
#                     ))
                    
#                     # Process each version
#                     for version in db_versions:
#                         # Get filename from the original path
#                         filename = get_filename_from_path(version.minio_path)
#                         # Check all version folders
#                         found_versions = check_file_versions(doc.id, None)
#                         for path, ver_num in found_versions:
#                             all_versions.append((doc, version, [ver_num], path))
#                             print(f"Found version: doc_id={doc.id}, version_id={version.id}, version_path={path}, version_num={ver_num}, created_at={version.created_at}")

#                 if not all_versions:
#                     raise HTTPException(
#                         status_code=404,
#                         detail=f"No active versions found for {doc_type.value} document"
#                     )

#                 # Sort by version number (primary) and creation date (secondary)
#                 all_versions.sort(key=lambda x: (x[2], x[1].created_at.timestamp() if x[1].created_at else 0), reverse=True)
#                 document, version, version_num, minio_path = all_versions[0]
#                 print(f"Selected latest version: doc_id={document.id}, version_id={version.id}, version_path={minio_path}, version_num={version_num}, created_at={version.created_at}")
                
#                 # Update the version's MinIO path to the latest found
#                 version.minio_path = minio_path
#                 commit()

#             # Log access
#             DocumentAccessLogV2(
#                 document=document,
#                 version=version,
#                 user=User.get(id=current_user.id),
#                 action_type=DocumentAction.DOWNLOAD,
#                 ip_address="0.0.0.0"
#             )
#             commit()

#             try:
#                 file_data = minio.download_file(version.minio_path)

#                 # Generate filename based on document type
#                 if doc_type == DocumentTypes.IPID:
#                     filename = f"PO{part_number}_OP{operation_number}_{doc_type.value}_v{version_num[0]}.{version.minio_path.split('.')[-1]}"
#                 else:
#                     filename = f"{part_number}_{doc_type.value}_v{version_num[0]}.{version.minio_path.split('.')[-1]}"

#                 return StreamingResponse(
#                     file_data,
#                     media_type="application/octet-stream",
#                     headers={
#                         "Content-Disposition": f'attachment; filename="{filename}"'
#                     }
#                 )
#             except Exception as e:
#                 raise HTTPException(
#                     status_code=500,
#                     detail=f"Failed to download file: {str(e)}"
#                 )

#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"An error occurred: {str(e)}"
#         )

bucket_name = os.getenv("MINIO_BUCKET_NAME", "documents")

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

            def check_file_versions(doc_id, operation_number):
                """Check for all versions of a file"""
                found_versions = []
                base_path = f"documents/v2/Document Types/{doc_type.value}/{part_number}"
                if operation_number is not None:
                    base_path = f"{base_path}/OP{operation_number}"
                base_path = f"{base_path}/{doc_id}"
                
                try:
                    # List all objects in the base path to find version folders
                    objects = minio.client.list_objects(bucket_name, prefix=base_path + "/")
                    version_folders = set()
                    for obj in objects:
                        # Extract version folder from path
                        parts = obj.object_name.split('/')
                        for part in parts:
                            if part.startswith('v') and len(part) > 1 and part[1:].replace('.', '').isdigit():
                                version_folders.add(part)
                    
                    print(f"Found version folders: {sorted(version_folders)}")
                    
                    if not version_folders:
                        print(f"No version folders found in {base_path}")
                        return found_versions
                    
                    # Sort version folders by version number
                    sorted_versions = sorted(version_folders, key=lambda v: [float(x) if '.' in x else int(x) for x in v[1:].split('.')])
                    print(f"Sorted version folders: {sorted_versions}")
                    
                    # Try each version folder, starting from highest
                    for v in reversed(sorted_versions):
                        try:
                            path = f"{base_path}/{v}"
                            # List files in this version folder
                            version_objects = minio.client.list_objects(bucket_name, prefix=path + "/")
                            for obj in version_objects:
                                try:
                                    print(f"Checking path: {obj.object_name}")
                                    # Try to get the file
                                    stream = minio.download_file(obj.object_name)
                                    version_num = int(v[1:]) if v[1:].isdigit() else float(v[1:])
                                    found_versions.append((obj.object_name, version_num))
                                    print(f"Found file in version {v}: {obj.object_name}")
                                except Exception as e:
                                    print(f"Error accessing file {obj.object_name}: {str(e)}")
                                    continue
                        except Exception as e:
                            print(f"Error listing files in version {v}: {path}, error: {str(e)}")
                            continue
                except Exception as e:
                    print(f"Error listing objects in path {base_path}: {str(e)}")
                
                # Sort found versions by version number and return all of them
                found_versions.sort(key=lambda x: x[1], reverse=True)
                return found_versions

            # Special handling for IPID documents
            if doc_type == DocumentTypes.IPID:
                if operation_number is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Operation number is required for IPID documents"
                    )

                # Get all active documents for this part number
                documents = list(DocumentV2.select(
                    lambda d: d.part_number == part_number and
                              d.doc_type.id == doc_type_obj.id and
                              d.is_active
                ))

                print(f"Found {len(documents)} active documents for part number {part_number}")

                # Find all matching versions across all documents
                matching_versions = []
                for doc in documents:
                    print(f"Processing document {doc.id}")
                    
                    # Get database versions
                    db_versions = list(DocumentVersionV2.select(
                        lambda v: v.document == doc and v.is_active
                    ))
                    
                    print(f"Found {len(db_versions)} database versions for document {doc.id}")
                    
                    # Process each version
                    for version in db_versions:
                        try:
                            metadata = version.metadata
                            if isinstance(metadata, str):
                                metadata = json.loads(metadata)
                            
                            print(f"Version {version.id} metadata: {metadata}")
                            stored_op_num = metadata.get("operation_number")
                            # Convert operation numbers to integers for comparison
                            if isinstance(stored_op_num, str):
                                stored_op_num = int(stored_op_num)
                            
                            if stored_op_num == operation_number:
                                print(f"Found matching operation number {operation_number} in version {version.id}")
                                # Check all version folders
                                found_versions = check_file_versions(doc.id, operation_number)
                                for path, ver_num in found_versions:
                                    matching_versions.append((doc, version, [ver_num], path))
                                    print(f"Found matching version: doc_id={doc.id}, version_id={version.id}, version_path={path}, version_num={ver_num}, created_at={version.created_at}")
                        except (json.JSONDecodeError, AttributeError, ValueError) as e:
                            print(f"Error processing version {version.id} metadata: {str(e)}")
                            continue

                if not matching_versions:
                    # Try to provide more detailed error message
                    if not documents:
                        error_detail = f"No documents found for part number {part_number}"
                    else:
                        error_detail = f"No IPID document found for part number {part_number} and operation {operation_number}. "
                        error_detail += f"Found {len(documents)} documents but none match the operation number."
                    
                    raise HTTPException(
                        status_code=404,
                        detail=error_detail
                    )

                # Sort by version number (primary) and creation date (secondary)
                matching_versions.sort(key=lambda x: (x[2], x[1].created_at.timestamp() if x[1].created_at else 0), reverse=True)
                document, version, version_num, minio_path = matching_versions[0]
                print(f"Selected latest version: doc_id={document.id}, version_id={version.id}, version_path={minio_path}, version_num={version_num}, created_at={version.created_at}")
                
                # Instead of updating the version's path, use the found path directly
                try:
                    file_data = minio.download_file(minio_path)
                    
                    # Generate filename based on document type
                    filename = f"PO{part_number}_OP{operation_number}_{doc_type.value}_v{version_num[0]}.{minio_path.split('.')[-1]}"

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

            else:
                # For other document types, get all active documents
                documents = list(DocumentV2.select(
                    lambda d: d.part_number == part_number and
                              d.doc_type.id == doc_type_obj.id and
                              d.is_active
                ))

                if not documents:
                    raise HTTPException(
                        status_code=404,
                        detail=f"No {doc_type.value} document found for part number {part_number}"
                    )

                # Get all versions across all documents
                all_versions = []
                for doc in documents:
                    # Get database versions
                    db_versions = list(DocumentVersionV2.select(
                        lambda v: v.document == doc and v.is_active
                    ))
                    
                    # Process each version
                    for version in db_versions:
                        # Get filename from the original path
                        filename = get_filename_from_path(version.minio_path)
                        # Check all version folders
                        found_versions = check_file_versions(doc.id, None)
                        for path, ver_num in found_versions:
                            all_versions.append((doc, version, [ver_num], path))
                            print(f"Found version: doc_id={doc.id}, version_id={version.id}, version_path={path}, version_num={ver_num}, created_at={version.created_at}")

                if not all_versions:
                    raise HTTPException(
                        status_code=404,
                        detail=f"No active versions found for {doc_type.value} document"
                    )

                # Sort by version number (primary) and creation date (secondary)
                all_versions.sort(key=lambda x: (x[2], x[1].created_at.timestamp() if x[1].created_at else 0), reverse=True)
                document, version, version_num, minio_path = all_versions[0]
                print(f"Selected latest version: doc_id={document.id}, version_id={version.id}, version_path={minio_path}, version_num={version_num}, created_at={version.created_at}")
                
                # Update the version's MinIO path to the latest found
                version.minio_path = minio_path
                commit()

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
                file_data = minio.download_file(version.minio_path)

                # Generate filename based on document type
                if doc_type == DocumentTypes.IPID:
                    filename = f"PO{part_number}_OP{operation_number}_{doc_type.value}_v{version_num[0]}.{version.minio_path.split('.')[-1]}"
                else:
                    filename = f"{part_number}_{doc_type.value}_v{version_num[0]}.{version.minio_path.split('.')[-1]}"

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
        part_number: str = Form(...),
        operation_number: str = Form(...),
        document_name: str = Form(...),
        description: Optional[str] = Form(None),
        production_order: Optional[str] = Form(None),
        metadata: Optional[str] = Form("{}"),
        current_user: User = Depends(get_current_user)
):
    """Upload a ballooned drawing for a specific part number and operation.
       Reuses existing document if available and adds a new version.
    """
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # Lookup production order if provided
            order = None
            if production_order:
                order = Order.get(production_order=production_order)
                if not order:
                    raise HTTPException(status_code=404, detail="Production order not found")

            # Get or create Document Type
            doc_type_obj = DocumentTypeV2.get(name="BALLOONED_DRAWING")
            if not doc_type_obj:
                doc_type_obj = DocumentTypeV2(
                    name="BALLOONED_DRAWING",
                    description="Ballooned Engineering Drawings",
                    allowed_extensions=[".pdf", ".dwg", ".dxf"]
                )
                commit()

            # Get or create root "Balloon" folder
            root_path = "Balloon"
            balloon_folder = FolderV2.get(lambda f: f.path == root_path)
            if not balloon_folder:
                balloon_folder = FolderV2(
                    name="Balloon",
                    path=root_path,
                    created_by=user
                )
                commit()
            elif not balloon_folder.is_active:
                balloon_folder.is_active = True
                commit()

            # Part number folder
            part_path = f"{root_path}/{part_number}"
            part_folder = FolderV2.get(lambda f: f.path == part_path)
            if not part_folder:
                part_folder = FolderV2(
                    name=part_number,
                    path=part_path,
                    parent_folder=balloon_folder,
                    created_by=user
                )
                commit()
            elif not part_folder.is_active:
                part_folder.is_active = True
                commit()

            # Operation folder
            op_folder_name = f"OP{operation_number}"
            op_path = f"{part_path}/{op_folder_name}"
            op_folder = FolderV2.get(lambda f: f.path == op_path)
            if not op_folder:
                op_folder = FolderV2(
                    name=op_folder_name,
                    path=op_path,
                    parent_folder=part_folder,
                    created_by=user
                )
                commit()
            elif not op_folder.is_active:
                op_folder.is_active = True
                commit()

            # File extension validation
            file_ext = f".{file.filename.split('.')[-1].lower()}"
            if file_ext not in doc_type_obj.allowed_extensions:
                raise HTTPException(
                    status_code=400,
                    detail=f"File type {file_ext} not allowed for Ballooned Drawing documents"
                )

            # Check if document already exists
            existing_doc = DocumentV2.select(
                lambda d: d.name == document_name and
                          d.folder == op_folder and
                          d.part_number == part_number and
                          d.doc_type == doc_type_obj
            ).first()

            if existing_doc:
                new_doc = existing_doc
            else:
                new_doc = DocumentV2(
                    name=document_name,
                    folder=op_folder,
                    doc_type=doc_type_obj,
                    description=description,
                    part_number=part_number,
                    production_order=order,
                    created_by=user
                )
                commit()

            # Get next version number (fixed)
            # Get next version number (fixed for float or string inputs)
            existing_versions = list(
                DocumentVersionV2.select(lambda v: v.document == new_doc)
                .order_by(lambda v: int(float(v.version_number)))
            )

            next_version_number = (
                str(int(float(existing_versions[-1].version_number)) + 1)
                if existing_versions else "1"
            )

            # Read file and compute checksum
            file_content = await file.read()
            checksum = hashlib.sha256(file_content).hexdigest()

            # MinIO path
            minio_path = f"documents/v2/Balloon/{part_number}/OP{operation_number}/{new_doc.id}/v{next_version_number}/{file.filename}"

            # Upload to MinIO
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
                metadata_dict = json.loads(metadata or "{}")
            except json.JSONDecodeError:
                metadata_dict = {}

            metadata_dict["production_order"] = production_order
            metadata_dict["operation_number"] = operation_number
            metadata_dict["part_number"] = part_number

            # Create version
            version = DocumentVersionV2(
                document=new_doc,
                version_number=next_version_number,
                minio_path=minio_path,
                file_size=len(file_content),
                checksum=checksum,
                created_by=user,
                metadata=metadata_dict
            )
            new_doc.latest_version = version

            # Log access
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

@router.get("/ballooned-drawing/download/{part_number}/{operation_number}")
def download_ballooned_drawing_by_part_and_op(
        part_number: str,
        operation_number: str,
        current_user: User = Depends(get_current_user)
):
    """Download the latest ballooned drawing for a part number and operation"""
    try:
        with db_session:
            # Re-fetch user within session
            user = User[current_user.id]

            # Get document type for ballooned drawings
            doc_type = DocumentTypeV2.get(name="BALLOONED_DRAWING")
            if not doc_type:
                raise HTTPException(status_code=404, detail="Ballooned drawing document type not defined")

            # Find the operation folder using path-based approach
            root_path = "Balloon"
            balloon_folder = FolderV2.get(lambda f: f.path == root_path)
            if not balloon_folder:
                raise HTTPException(status_code=404, detail="Balloon folder not found")

            part_path = f"{root_path}/{part_number}"
            part_folder = FolderV2.get(lambda f: f.path == part_path)
            if not part_folder:
                raise HTTPException(status_code=404, detail=f"Folder for part number {part_number} not found")

            op_folder_name = f"OP{operation_number}"
            op_path = f"{part_path}/{op_folder_name}"
            op_folder = FolderV2.get(lambda f: f.path == op_path)
            if not op_folder:
                raise HTTPException(status_code=404, detail=f"Folder for operation {operation_number} not found")

            # Find the most recent ballooned drawing for this part number and operation
            documents = select(d for d in DocumentV2
                               if d.is_active and
                               d.doc_type == doc_type and
                               d.part_number == part_number and
                               d.folder == op_folder and
                               d.latest_version).order_by(lambda d: desc(d.created_at))[:]

            if not documents:
                raise HTTPException(
                    status_code=404,
                    detail=f"No ballooned drawings found for part number {part_number} operation {operation_number}"
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
                download_filename = f"{part_number}_OP{operation_number}_Ballooned_Drawing.{file_extension}"

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
        operation_number: Optional[int] = Form(None),
        quantity: Optional[int] = Form(None),
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
            root_path = "Document Types"
            root_folder = FolderV2.get(lambda f: f.path == root_path)
            if not root_folder:
                root_folder = FolderV2(
                    name="Document Types",
                    path=root_path,
                    created_by=user
                )
                commit()
            elif not root_folder.is_active:
                root_folder.is_active = True
                commit()

            # Get or create Report folder
            report_path = f"{root_path}/{REPORT_DOC_TYPE}"
            report_folder = FolderV2.get(lambda f: f.path == report_path)
            if not report_folder:
                report_folder = FolderV2(
                    name=REPORT_DOC_TYPE,
                    path=report_path,
                    parent_folder=root_folder,
                    created_by=user
                )
                commit()
            elif not report_folder.is_active:
                report_folder.is_active = True
                commit()

            # Process the custom folder path to get to the target folder
            if folder_path:
                folder_parts = folder_path.strip("/").split("/")
                current_folder = report_folder
                current_path = report_path

                # Create each folder in the path if it doesn't exist
                for folder_name in folder_parts:
                    if not folder_name:
                        continue

                    current_path += f"/{folder_name}"
                    next_folder = FolderV2.get(lambda f: f.path == current_path)

                    if not next_folder:
                        next_folder = FolderV2(
                            name=folder_name,
                            path=current_path,
                            parent_folder=current_folder,
                            created_by=user
                        )
                        commit()
                    elif not next_folder.is_active:
                        next_folder.is_active = True
                        commit()

                    current_folder = next_folder

                # The specified folder path will be the parent folder
                parent_folder = current_folder
            else:
                # If no folder path is provided, use the report root folder as parent
                parent_folder = report_folder

            # Get production order if order_number is provided
            production_order = None
            operation = None
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

                # If operation number is provided, get the operation and create its folder
                if operation_number is not None:
                    # Special handling for operation 999 (Final Inspection)
                    if operation_number == 999:
                        operation = None  # No need to validate against operations table for final inspection
                    else:
                        operation = Operation.get(lambda op: op.order == production_order and op.operation_number == operation_number)
                        if not operation:
                            raise HTTPException(status_code=404, detail=f"Operation {operation_number} not found for order {order_number}")

                    # Create a folder for the operation_number inside the order folder
                    operation_folder_path = f"{order_folder_path}/OP{operation_number}"
                    operation_folder = FolderV2.get(lambda f: f.name == f"OP{operation_number}" and f.parent_folder == order_folder)
                    if not operation_folder:
                        operation_folder = FolderV2(
                            name=f"OP{operation_number}",
                            path=operation_folder_path,
                            parent_folder=order_folder,
                            created_by=user
                        )
                        commit()

                    # Use the operation folder as the target folder for the document
                    target_folder = operation_folder
                else:
                    # If no operation number, use the order folder
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
                # Add quantity to metadata if provided
                if quantity is not None:
                    metadata_dict["quantity"] = quantity
            except json.JSONDecodeError:
                metadata_dict = {"quantity": quantity} if quantity is not None else {}

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


# General Document Management Deletion Endpoints
@router.delete("/folders/{folder_id}", status_code=200)
async def delete_folder(
        folder_id: int,
        current_user: User = Depends(get_current_user)
):
    """
    Soft delete a folder and all its contents (documents and subfolders).
    Sets is_active=False rather than actually deleting records.
    """
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            folder = FolderV2.get(id=folder_id)
            if not folder:
                raise HTTPException(status_code=404, detail="Folder not found")

            if not folder.is_active:
                return {"message": f"Folder '{folder.name}' is already deleted"}

            # Function to recursively mark folder and all its contents as inactive
            def mark_folder_inactive(target_folder):
                # Mark all documents in the folder as inactive
                documents = select(d for d in DocumentV2 if d.folder == target_folder and d.is_active)
                for doc in documents:
                    doc.is_active = False

                    # Log deletion
                    DocumentAccessLogV2(
                        document=doc,
                        version=doc.latest_version,
                        user=user,
                        action_type=DocumentAction.DELETE,
                        ip_address="0.0.0.0"
                    )

                # Recursively mark all subfolders and their contents as inactive
                subfolders = select(f for f in FolderV2 if f.parent_folder == target_folder and f.is_active)
                for subfolder in subfolders:
                    mark_folder_inactive(subfolder)

                # Finally mark the folder itself as inactive
                target_folder.is_active = False

            # Execute the recursive deletion
            mark_folder_inactive(folder)
            commit()

            return {"message": f"Folder '{folder.name}' and all its contents have been deleted"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.delete("/documents/{document_id}", status_code=200)
async def delete_document(
        document_id: int,
        current_user: User = Depends(get_current_user)
):
    """
    Soft delete a document.
    Sets is_active=False rather than actually deleting the record.
    """
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            document = DocumentV2.get(id=document_id)
            if not document:
                raise HTTPException(status_code=404, detail="Document not found")

            if not document.is_active:
                return {"message": f"Document '{document.name}' is already deleted"}

            # Mark document as inactive
            document.is_active = False

            # Log deletion
            DocumentAccessLogV2(
                document=document,
                version=document.latest_version,
                user=user,
                action_type=DocumentAction.DELETE,
                ip_address="0.0.0.0"
            )

            commit()

            return {"message": f"Document '{document.name}' has been deleted"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.delete("/document-versions/{version_id}", status_code=200)
async def delete_document_version(
        version_id: int,
        current_user: User = Depends(get_current_user)
):
    """
    Soft delete a document version.
    Sets is_active=False rather than actually deleting the record.
    If this is the latest version, updates the document's latest_version to the next most recent active version.
    """
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            version = DocumentVersionV2.get(id=version_id)
            if not version:
                raise HTTPException(status_code=404, detail="Version not found")

            if not version.is_active:
                return {"message": f"Version {version.version_number} is already deleted"}

            document = version.document
            is_latest = document.latest_version == version

            # Mark version as inactive
            version.is_active = False

            # Log deletion
            DocumentAccessLogV2(
                document=document,
                version=version,
                user=user,
                action_type=DocumentAction.DELETE,
                ip_address="0.0.0.0"
            )

            # If this was the latest version, update the document's latest_version
            if is_latest:
                # Find the next most recent active version
                next_latest = select(v for v in DocumentVersionV2
                                     if v.document == document and
                                     v.is_active and
                                     v.id != version_id
                                     ).order_by(desc(DocumentVersionV2.created_at)).first()
                document.latest_version = next_latest

            commit()

            return {"message": f"Version {version.version_number} has been deleted" +
                               (", document latest_version has been updated" if is_latest else "")}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.delete("/document-types/{doc_type_id}", status_code=200)
async def delete_document_type(
        doc_type_id: int,
        force: bool = Query(False, description="If true, will delete even if documents exist with this type"),
        current_user: User = Depends(get_current_user)
):
    """
    Soft delete a document type.
    By default, will not delete if there are active documents using this type unless force=True.
    """
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            doc_type = DocumentTypeV2.get(id=doc_type_id)
            if not doc_type:
                raise HTTPException(status_code=404, detail="Document type not found")

            if not doc_type.is_active:
                return {"message": f"Document type '{doc_type.name}' is already deleted"}

            # Check if there are active documents using this type
            active_docs_count = select(d for d in DocumentV2
                                       if d.doc_type == doc_type and d.is_active).count()

            if active_docs_count > 0 and not force:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot delete document type '{doc_type.name}' as it is used by {active_docs_count} active documents. Use 'force=true' to delete anyway."
                )

            # If forced, mark all related documents as inactive
            if force and active_docs_count > 0:
                active_docs = select(d for d in DocumentV2 if d.doc_type == doc_type and d.is_active)
                for doc in active_docs:
                    doc.is_active = False
                    # Log document deletion
                    DocumentAccessLogV2(
                        document=doc,
                        version=doc.latest_version,
                        user=user,
                        action_type=DocumentAction.DELETE,
                        ip_address="0.0.0.0"
                    )

            # Mark document type as inactive
            doc_type.is_active = False
            commit()

            return {"message": f"Document type '{doc_type.name}' has been deleted" +
                               (
                                   f" along with {active_docs_count} related documents" if force and active_docs_count > 0 else "")}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.post("/documents/{document_id}/restore", status_code=200)
async def restore_document(
        document_id: int,
        current_user: User = Depends(get_current_user)
):
    """
    Restore a previously deleted document by setting is_active back to True.
    """
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            document = DocumentV2.get(id=document_id)
            if not document:
                raise HTTPException(status_code=404, detail="Document not found")

            if document.is_active:
                return {"message": f"Document '{document.name}' is already active"}

            # Check if the document's folder is active
            if not document.folder.is_active:
                # Restore the folder first
                folder = document.folder
                folder.is_active = True
                # Also restore parent folders if needed
                while folder.parent_folder and not folder.parent_folder.is_active:
                    folder.parent_folder.is_active = True
                    folder = folder.parent_folder

            # Restore the document
            document.is_active = True

            # Log restoration
            DocumentAccessLogV2(
                document=document,
                version=document.latest_version,
                user=user,
                action_type=DocumentAction.UPDATE,
                ip_address="0.0.0.0"
            )

            commit()

            return {"message": f"Document '{document.name}' has been restored"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.post("/folders/{folder_id}/restore", status_code=200)
async def restore_folder(
        folder_id: int,
        restore_contents: bool = Query(True,
                                       description="If true, will also restore all documents and subfolders within this folder"),
        current_user: User = Depends(get_current_user)
):
    """
    Restore a previously deleted folder by setting is_active back to True.
    Optionally also restores all contents (documents and subfolders).
    """
    try:
        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            folder = FolderV2.get(id=folder_id)
            if not folder:
                raise HTTPException(status_code=404, detail="Folder not found")

            if folder.is_active:
                return {"message": f"Folder '{folder.name}' is already active"}

            # Check if parent folders are active, and restore them if not
            current_folder = folder
            while current_folder.parent_folder and not current_folder.parent_folder.is_active:
                current_folder.parent_folder.is_active = True
                current_folder = current_folder.parent_folder

            # Restore this folder
            folder.is_active = True

            # If requested, restore all contents too
            restored_doc_count = 0
            restored_folder_count = 1  # Count this folder

            if restore_contents:
                # Function to recursively restore folder contents
                def restore_folder_contents(target_folder):
                    nonlocal restored_doc_count, restored_folder_count

                    # Restore all documents in the folder
                    inactive_docs = select(d for d in DocumentV2 if d.folder == target_folder and not d.is_active)
                    for doc in inactive_docs:
                        doc.is_active = True
                        restored_doc_count += 1

                        # Log restoration
                        DocumentAccessLogV2(
                            document=doc,
                            version=doc.latest_version,
                            user=user,
                            action_type=DocumentAction.UPDATE,
                            ip_address="0.0.0.0"
                        )

                    # Recursively restore all subfolders and their contents
                    inactive_subfolders = select(
                        f for f in FolderV2 if f.parent_folder == target_folder and not f.is_active)
                    for subfolder in inactive_subfolders:
                        subfolder.is_active = True
                        restored_folder_count += 1
                        restore_folder_contents(subfolder)

                # Execute the recursive restoration
                restore_folder_contents(folder)

            commit()

            return {
                "message": f"Folder '{folder.name}' has been restored",
                "restored_folders": restored_folder_count,
                "restored_documents": restored_doc_count
            }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.get("/trash/documents", response_model=DocumentListResponse)
async def list_deleted_documents(
        folder_id: int | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=100),
        current_user=Depends(get_current_user)
):
    """List soft-deleted documents with optional filters and pagination"""
    try:
        with db_session:
            # Start with base query for inactive documents
            base_query = DocumentV2.select(lambda d: not d.is_active)

            # Apply folder filter if provided
            if folder_id:
                base_query = base_query.filter(lambda d: d.folder.id == folder_id)

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


@router.get("/trash/folders", response_model=List[FolderResponse])
async def list_deleted_folders(
        parent_id: int | None = None,
        current_user=Depends(get_current_user)
):
    """List soft-deleted folders, optionally filtered by parent folder"""
    try:
        with db_session:
            if parent_id:
                folders = list(FolderV2.select(lambda f: not f.is_active and f.parent_folder.id == parent_id))
            else:
                folders = list(FolderV2.select(lambda f: not f.is_active))

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


@router.get("/ipid/all", response_model=List[DocumentResponse])
async def get_all_ipid_documents(
        current_user: User = Depends(get_current_user)
):
    """Get all IPID documents across all production orders and operations"""
    try:
        with db_session:
            # Get IPID document type using DocumentTypes enum
            doc_type = DocumentTypeV2.get(name=DocumentTypes.IPID.value)
            if not doc_type:
                raise HTTPException(status_code=404, detail="IPID document type not found")

            # Query all active IPID documents, ordered by most recent first
            documents = select(d for d in DocumentV2
                               if d.doc_type == doc_type
                               and d.is_active == True
                               ).order_by(lambda d: desc(d.created_at))[:]

            # Format response according to DocumentResponse model
            response = []
            for doc in documents:
                if not doc.latest_version:
                    continue

                # Extract operation number from metadata if available
                operation_number = None
                if doc.latest_version.metadata and isinstance(doc.latest_version.metadata, dict):
                    operation_number = doc.latest_version.metadata.get("operation_number")

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
                        "metadata": {
                            **(doc.latest_version.metadata or {}),
                            "operation_number": operation_number
                        }
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


@router.get("/ipid/structure/{po_number}")
async def get_ipid_folder_structure(
        po_number: str,
        current_user: User = Depends(get_current_user)
):
    """Get the complete folder structure and documents under IPID folder for a specific PO number"""
    try:
        with db_session:
            # Get IPID document type
            doc_type = DocumentTypeV2.get(name=DocumentTypes.IPID.value)
            if not doc_type:
                raise HTTPException(status_code=404, detail="IPID document type not found")

            # Find the root IPID folder
            root_folder = FolderV2.get(lambda f: f.name == "IPID"
                                                 and f.path == "Document Types/IPID"
                                                 and f.is_active == True)
            if not root_folder:
                raise HTTPException(status_code=404, detail="IPID root folder not found")

            # Find the PO folder
            po_folder = FolderV2.get(lambda f: f.name == po_number
                                               and f.parent_folder == root_folder
                                               and f.is_active == True)
            if not po_folder:
                raise HTTPException(status_code=404, detail=f"No folder found for PO: {po_number}")

            def get_folder_structure(folder):
                # Get all active documents in this folder with complete details
                docs = select(d for d in DocumentV2
                              if d.folder == folder
                              and d.doc_type == doc_type
                              and d.is_active == True).order_by(lambda d: desc(d.created_at))[:]

                documents = []
                for doc in docs:
                    if not doc.latest_version:
                        continue

                    # Get all versions for this document
                    versions = select(v for v in DocumentVersionV2
                                      if v.document == doc
                                      and v.is_active == True
                                      ).order_by(lambda v: desc(v.created_at))[:]

                    # Format all versions
                    formatted_versions = []
                    for version in versions:
                        # Extract operation number from metadata if available
                        operation_number = None
                        if version.metadata and isinstance(version.metadata, dict):
                            operation_number = version.metadata.get("operation_number")

                        formatted_versions.append({
                            "id": version.id,
                            "version_number": version.version_number,
                            "minio_path": version.minio_path,
                            "file_size": version.file_size,
                            "checksum": version.checksum,
                            "created_at": version.created_at.isoformat() if version.created_at else None,
                            "created_by_id": version.created_by.id,
                            "is_active": version.is_active,
                            "metadata": {
                                **(version.metadata or {}),
                                "operation_number": operation_number
                            }
                        })

                    documents.append({
                        "id": doc.id,
                        "name": doc.name,
                        "folder_id": doc.folder.id,
                        "folder_path": doc.folder.path,
                        "doc_type_id": doc.doc_type.id,
                        "description": doc.description,
                        "part_number": doc.part_number,
                        "production_order_id": doc.production_order.id if doc.production_order else None,
                        "created_at": doc.created_at.isoformat() if doc.created_at else None,
                        "created_by_id": doc.created_by.id,
                        "is_active": doc.is_active,
                        "latest_version": formatted_versions[0] if formatted_versions else None,
                        "all_versions": formatted_versions,
                        "version_count": len(formatted_versions)
                    })

                # Get all active subfolders
                subfolders = select(f for f in FolderV2
                                    if f.parent_folder == folder
                                    and f.is_active == True
                                    ).order_by(lambda f: f.name)

                # Build folder structure
                result = {
                    "folder_info": {
                        "id": folder.id,
                        "name": folder.name,
                        "path": folder.path,
                        "created_at": folder.created_at.isoformat() if folder.created_at else None,
                        "is_active": folder.is_active,
                        "document_count": len(documents),
                        "total_versions": sum(doc["version_count"] for doc in documents)
                    },
                    "documents": documents,
                    "subfolders": {},
                    "total_documents_recursive": len(documents)  # Will be updated below
                }

                # Recursively process subfolders
                for subfolder in subfolders:
                    subfolder_structure = get_folder_structure(subfolder)
                    result["subfolders"][subfolder.name] = subfolder_structure
                    # Add subfolder's documents to total count
                    result["total_documents_recursive"] += subfolder_structure["total_documents_recursive"]

                return result

            # Get the complete structure starting from PO folder
            structure = get_folder_structure(po_folder)

            # Calculate totals
            total_documents = structure["total_documents_recursive"]
            total_versions = structure["folder_info"]["total_versions"]
            for subfolder in structure["subfolders"].values():
                total_versions += subfolder["folder_info"]["total_versions"]

            return {
                "po_number": po_number,
                "structure": structure,
                "summary": {
                    "total_documents": total_documents,
                    "total_versions": total_versions,
                    "folder_count": 1 + sum(1 for _ in FolderV2.select(
                        lambda f: f.path.startswith(po_folder.path + "/")
                                  and f.is_active == True
                    ))
                }
            }

    except Exception as e:
        logger.error(f"Error retrieving IPID folder structure: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving IPID folder structure: {str(e)}"
        )
    

    
# @router.post("/report/generate-consolidated/{order_number}", response_model=DocumentResponse)
# async def generate_consolidated_report(
#     order_number: str,
#     current_user: User = Depends(get_current_user)
# ):
#     """
#     Generate a consolidated report by merging PDFs from each operation folder for a given PO number.
#     The consolidated report will be stored in a 'Consolidated Reports' folder under the PO number.
#     """
#     try:
#         with db_session:
#             user = User.get(id=current_user.id)
#             if not user:
#                 raise HTTPException(status_code=404, detail="User not found")

#             # Get the order
#             order = Order.get(production_order=order_number)
#             if not order:
#                 raise HTTPException(status_code=404, detail=f"Order {order_number} not found")

#             # Get or create root folder structure
#             root_folder = FolderV2.get(lambda f: f.path == "Document Types")
#             if not root_folder:
#                 root_folder = FolderV2(
#                     name="Document Types",
#                     path="Document Types",
#                     created_by=user
#                 )
#                 commit()
#             elif not root_folder.is_active:
#                 root_folder.is_active = True
#                 commit()

#             # Get or create Report folder
#             report_path = "Document Types/REPORT"
#             report_folder = FolderV2.get(lambda f: f.path == report_path)
#             if not report_folder:
#                 report_folder = FolderV2(
#                     name="REPORT",
#                     path=report_path,
#                     parent_folder=root_folder,
#                     created_by=user
#                 )
#                 commit()
#             elif not report_folder.is_active:
#                 report_folder.is_active = True
#                 commit()

#             # Get or create PO folder
#             po_folder_path = f"{report_path}/{order_number}"
#             po_folder = FolderV2.get(lambda f: f.path == po_folder_path)
#             if not po_folder:
#                 po_folder = FolderV2(
#                     name=order_number,
#                     path=po_folder_path,
#                     parent_folder=report_folder,
#                     created_by=user
#                 )
#                 commit()
#             elif not po_folder.is_active:
#                 po_folder.is_active = True
#                 commit()

#             # Get or create Consolidated Reports folder
#             consolidated_folder_path = f"{po_folder_path}/Consolidated Reports"
#             consolidated_folder = FolderV2.get(lambda f: f.path == consolidated_folder_path)
#             if not consolidated_folder:
#                 consolidated_folder = FolderV2(
#                     name="Consolidated Reports",
#                     path=consolidated_folder_path,
#                     parent_folder=po_folder,
#                     created_by=user
#                 )
#                 commit()
#             elif not consolidated_folder.is_active:
#                 consolidated_folder.is_active = True
#                 commit()

#             # Get all operation folders under the PO folder
#             operation_folders = []
#             print(f"Searching for operation folders under PO folder: {po_folder.path}")
            
#             # First, get all folders under the PO folder (including inactive ones)
#             all_folders = list(FolderV2.select(
#                 lambda f: f.path.startswith(po_folder.path + "/") and 
#                          f.path != consolidated_folder_path and
#                          f.path != po_folder.path
#             ))
            
#             print(f"Found all folders (active and inactive): {[(f.path, f.is_active) for f in all_folders]}")
            
#             # Filter for operation folders and reactivate if needed
#             for folder in all_folders:
#                 if folder.name.startswith("OP"):
#                     if not folder.is_active:
#                         print(f"Reactivating inactive operation folder: {folder.path}")
#                         folder.is_active = True
#                         commit()
#                     operation_folders.append(folder)
#                     print(f"Found operation folder: {folder.path} (active: {folder.is_active})")
            
#             print(f"Found operation folders: {[f.path for f in operation_folders]}")
            
#             # Also try a direct search for specific operation folders if none found
#             if not operation_folders:
#                 print("No operation folders found, trying direct search...")
#                 direct_search = list(FolderV2.select())
#                 for f in direct_search:
#                     if f.path.startswith(po_folder.path + "/OP"):
#                         print(f"Direct search found: {f.path} (active: {f.is_active})")
#                         if not f.is_active:
#                             f.is_active = True
#                             commit()
#                         operation_folders.append(f)

#             if not operation_folders:
#                 raise HTTPException(status_code=404, detail="No operation folders found")

#             # Create a temporary directory to store PDFs
#             with tempfile.TemporaryDirectory() as temp_dir:
#                 merger = PyPDF2.PdfMerger()
#                 found_pdfs = False

#                 # Sort operation folders by operation number
#                 operation_folders.sort(key=lambda f: int(f.name.replace("OP", "")))
#                 print(f"Processing operation folders in order: {[f.name for f in operation_folders]} with paths: {[f.path for f in operation_folders]}")

#                 # Collect PDFs from each operation folder
#                 for op_folder in operation_folders:
#                     print(f"Processing operation folder: {op_folder.name} (path: {op_folder.path})")
                    
#                     # Get all folders under this operation folder (including the operation folder itself)
#                     operation_related_folders = []
#                     operation_related_folders.append(op_folder)  # Include the operation folder itself
                    
#                     # Get all subfolders under this operation folder
#                     subfolders = list(FolderV2.select(
#                         lambda f: f.path.startswith(op_folder.path + "/") and f.is_active
#                     ))
#                     operation_related_folders.extend(subfolders)
                    
#                     print(f"Found {len(operation_related_folders)} folders under {op_folder.name}: {[f.path for f in operation_related_folders]}")
                    
#                     # Collect all PDFs from all folders under this operation
#                     operation_pdfs = []
#                     for folder in operation_related_folders:
#                         # Get all PDF documents in this folder
#                         pdfs_in_folder = list(select(d for d in DocumentV2 
#                                         if d.folder == folder and 
#                                         d.is_active and 
#                                         d.latest_version and 
#                                         d.latest_version.minio_path.lower().endswith('.pdf')
#                                         ).order_by(lambda d: desc(d.created_at)))
                        
#                         for pdf_doc in pdfs_in_folder:
#                             operation_pdfs.append((pdf_doc, folder))
#                             print(f"Found PDF in {folder.path}: {pdf_doc.name} (version: {pdf_doc.latest_version.version_number})")
                    
#                     # Sort all PDFs by creation date (most recent first)
#                     operation_pdfs.sort(key=lambda x: x[0].created_at, reverse=True)
                    
#                     # Add all PDFs to the merger
#                     pdf_counter = 1
#                     for pdf_doc, folder in operation_pdfs:
#                         found_pdfs = True
#                         # Create a safe filename by removing invalid characters
#                         safe_name = "".join(c for c in pdf_doc.name if c.isalnum() or c in ('-', '_'))
#                         pdf_filename = f"{op_folder.name}_doc{pdf_counter}.pdf"
#                         pdf_path = os.path.join(temp_dir, pdf_filename)
                        
#                         try:
#                             with open(pdf_path, 'wb') as pdf_file:
#                                 file_data = minio.download_file(pdf_doc.latest_version.minio_path)
#                                 pdf_file.write(file_data.read())
#                             merger.append(pdf_path)
#                             print(f"Added {pdf_filename} to merger from folder: {folder.path} (original: {pdf_doc.name})")
#                         except Exception as e:
#                             print(f"Error processing PDF {pdf_doc.name}: {str(e)}")
#                             continue
#                         pdf_counter += 1
                    
#                     if not operation_pdfs:
#                         print(f"No PDFs found in any folder under operation: {op_folder.name}")

#                 if not found_pdfs:
#                     raise HTTPException(status_code=404, detail="No PDF reports found in operation folders")

#                 # Create the consolidated PDF
#                 consolidated_pdf_path = os.path.join(temp_dir, f"Consolidated_Report_{order_number}.pdf")
#                 merger.write(consolidated_pdf_path)
#                 merger.close()

#                 # Create document in the consolidated folder
#                 doc_type_obj = DocumentTypeV2.get(name="REPORT")
#                 if not doc_type_obj:
#                     raise HTTPException(status_code=404, detail="REPORT document type not found")

#                 # Create the document
#                 new_doc = DocumentV2(
#                     name=f"Consolidated_Report_{order_number}",
#                     folder=consolidated_folder,
#                     doc_type=doc_type_obj,
#                     description=f"Consolidated report for PO {order_number}",
#                     part_number=order_number,
#                     production_order=order,
#                     created_by=user
#                 )
#                 commit()

#                 # Upload the consolidated PDF to MinIO
#                 version_number = "1.0"
#                 minio_path = f"documents/v2/{consolidated_folder.path}/{new_doc.id}/v{version_number}/Consolidated_Report_{order_number}.pdf"
                
#                 with open(consolidated_pdf_path, 'rb') as pdf_file:
#                     file_content = pdf_file.read()
#                     checksum = hashlib.sha256(file_content).hexdigest()
                    
#                     minio.upload_file(
#                         file=open(consolidated_pdf_path, 'rb'),
#                         object_name=minio_path,
#                         content_type="application/pdf"
#                     )

#                 # Create version
#                 version = DocumentVersionV2(
#                     document=new_doc,
#                     version_number=version_number,
#                     minio_path=minio_path,
#                     file_size=len(file_content),
#                     checksum=checksum,
#                     created_by=user,
#                     metadata={
#                         "consolidated_report": True,
#                         "order_number": order_number,
#                         "generation_date": datetime.utcnow().isoformat()
#                     }
#                 )
#                 new_doc.latest_version = version

#                 # Create access log
#                 DocumentAccessLogV2(
#                     document=new_doc,
#                     version=version,
#                     user=user,
#                     action_type="UPDATE",
#                     ip_address="0.0.0.0"
#                 )

#                 commit()

#                 return {
#                     "id": new_doc.id,
#                     "name": new_doc.name,
#                     "folder_id": new_doc.folder.id,
#                     "doc_type_id": new_doc.doc_type.id,
#                     "description": new_doc.description,
#                     "part_number": new_doc.part_number,
#                     "production_order_id": new_doc.production_order.id if new_doc.production_order else None,
#                     "created_at": new_doc.created_at,
#                     "created_by_id": new_doc.created_by.id,
#                     "is_active": new_doc.is_active,
#                     "latest_version": {
#                         "id": version.id,
#                         "document_id": new_doc.id,
#                         "version_number": version.version_number,
#                         "minio_path": version.minio_path,
#                         "file_size": version.file_size,
#                         "checksum": version.checksum,
#                         "created_at": version.created_at,
#                         "created_by_id": version.created_by.id,
#                         "is_active": version.is_active,
#                         "metadata": version.metadata
#                     }
#                 }

#     except Exception as e:
#         print(f"Error in generate_consolidated_report: {str(e)}")
#         import traceback
#         print(traceback.format_exc())
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"An error occurred: {str(e)}"
#         )
    





def save_df_to_pdf(df, metadata, pdf_path):

    # Document setup
    doc = SimpleDocTemplate(pdf_path, pagesize=landscape(A4),
                            leftMargin=20, rightMargin=20, topMargin=20, bottomMargin=20)
    styles = getSampleStyleSheet()

    # ------------------------
    # Column Widths
    # ------------------------
    inspection_col_widths = [30, 150, 50, 50, 40] + [45] * 10
    total_width = sum(inspection_col_widths)

    meta_col_widths = [total_width * 0.15, total_width * 0.35,
                       total_width * 0.15, total_width * 0.35]

    elements = []
    page_counter = 1  # Start counting pages
    total_pages = 0  # Placeholder for total pages, to be updated after document build

    # ------------------------
    # Title Table
    # ------------------------
    title_para = Paragraph("<b>INSPECTION REPORT</b>",
                           ParagraphStyle("title", parent=styles["Title"], fontSize=14))
    title_table = Table([[title_para]], colWidths=[total_width])
    title_table.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                    ("FONTSIZE", (0, 0), (-1, -1), 14),
                                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                                    ]))

    # ------------------------
    # Metadata Table (on all pages)
    # ------------------------
    def add_metadata_page_number(metadata, page_num, total_pages):
        # Update metadata with page number information
        metadata[4] = ["Stage Detail ", f"Page {page_num} of {total_pages}", "", " "]
        meta_table = Table(metadata, colWidths=meta_col_widths)
        meta_table.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 10),
                                       ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                                       ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                                       ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                       ]))
        return meta_table

    # ------------------------
    # Paginated Inspection Tables
    # ------------------------
    measurement_cols = [c for c in df.columns if c.isdigit()]
    fixed_cols = ["Sl. No.", "Nominal", "Upper Tol", "Lower Tol", "Zone"]
    total_rows = len(df)
    total_pages = (len(measurement_cols) // 10) + (1 if len(measurement_cols) % 10 > 0 else 0)

    for page_idx, start in enumerate(range(0, len(measurement_cols), 10)):
        elements.append(title_table)

        subset = measurement_cols[start:start + 10]

        # pad with None to reach 10 if fewer
        if len(subset) < 10:
            subset += [None] * (10 - len(subset))

        # headers always 1–10
        page_labels = [str(i) for i in range(start + 1, start + len(subset) + 1)]

        # rows
        page_data = []
        for _, row in df.iterrows():
            base = [row["Sl. No."], row["Nominal"], row["Upper Tol"], row["Lower Tol"], row["Zone"]]
            values = [(row[c] if c and c in df.columns else "") for c in subset]
            page_data.append(base + values)

        inspection_data = [["ID", "Nominal", "Upper\nTol", "Lower\nTol", "Zone"] + page_labels] + page_data

        inspection_table = Table(inspection_data, repeatRows=1, colWidths=inspection_col_widths)
        inspection_table.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 10),
                                             ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                                             ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                                             ("BACKGROUND", (0, 0), (-1, 0), colors.white),
                                             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                             ("ALIGN", (0, 1), (-1, -1), "CENTER"),
                                             ]))
        # Nominal column left aligned
        nominal_col_index = inspection_data[0].index("Nominal")
        inspection_table.setStyle(TableStyle([("ALIGN", (nominal_col_index, 0), (nominal_col_index, -1), "LEFT"),
                                             ]))

        # Add metadata and page number to each page
        elements.append(add_metadata_page_number(metadata, page_counter, total_pages))
        page_counter += 1

        elements.append(inspection_table)

        # Add page break if more pages remain
        if start + 10 < len(measurement_cols):
            elements.append(PageBreak())

    # ------------------------
    # Build PDF
    # ------------------------
    doc.build(elements)
@router.post("/report/generate-consolidated/{order_number}", response_model=DocumentResponse)
async def generate_consolidated_report_yeet(
        order_number: str,
        operation_no: str = Query(..., description="Operation number for the report"),
        current_user: User = Depends(get_current_user)
):
    print(f"\n=== Starting consolidated report generation ===")
    print(f"Order Number: {order_number}")
    print(f"Operation Number: {operation_no}")
    print(f"User ID: {current_user.id}")

    # Sanitize order number by removing leading/trailing whitespace
    order_number = order_number.strip()
    print(f"Sanitized Order Number: {order_number}")

    try:
        grouped_measurements = defaultdict(list)
        import io, hashlib

        with db_session:
            user = User.get(id=current_user.id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            # Get order using sanitized order number
            order = Order.get(production_order=order_number)
            if not order:
                print(f"Order not found with exact match: {order_number}")
                # Try with original order number in case the database has whitespace
                order = Order.get(production_order=order_number.strip())
                if not order:
                    print(f"Order not found with stripped value either: {order_number.strip()}")
                    raise HTTPException(status_code=404, detail=f"Order {order_number} not found")
            print(f"Found Order: ID={order.id}, PO={order.production_order}")

            print("\n=== Setting up Folder Structure ===")
            # Folder structure (all inside same db_session)
            # Create root folder if it doesn't exist
            print("Checking/Creating root folder...")
            root_folder = FolderV2.get(lambda f: f.path == "Document Types")
            if not root_folder:
                print("Creating root folder: Document Types")
                root_folder = FolderV2(
                    name="Document Types", 
                    path="Document Types", 
                    created_by=user
                )
                commit()
            print(f"Root folder ID: {root_folder.id}")

            # Create REPORT folder if it doesn't exist
            print("\nChecking/Creating REPORT folder...")
            report_folder = FolderV2.get(lambda f: f.path == "Document Types/REPORT")
            if not report_folder:
                print("Creating REPORT folder")
                report_folder = FolderV2(
                    name="REPORT", 
                    path="Document Types/REPORT", 
                    parent_folder=root_folder, 
                    created_by=user
                )
                commit()
            print(f"REPORT folder ID: {report_folder.id}")

            # Create PO folder if it doesn't exist
            print("\nChecking/Creating PO folder...")
            po_folder_path = f"Document Types/REPORT/{order_number}"
            po_folder = FolderV2.get(lambda f: f.path == po_folder_path)
            if not po_folder:
                print(f"Creating PO folder: {order_number}")
                po_folder = FolderV2(
                    name=order_number, 
                    path=po_folder_path, 
                    parent_folder=report_folder, 
                    created_by=user
                )
                commit()
            print(f"PO folder ID: {po_folder.id}")

            # Create Consolidated Reports folder if it doesn't exist
            print("\nChecking/Creating Consolidated Reports folder...")
            consolidated_folder_path = f"{po_folder_path}/Consolidated Reports"
            consolidated_folder = FolderV2.get(lambda f: f.path == consolidated_folder_path)
            if not consolidated_folder:
                print("Creating Consolidated Reports folder")
                consolidated_folder = FolderV2(
                    name="Consolidated Reports", 
                    path=consolidated_folder_path, 
                    parent_folder=po_folder, 
                    created_by=user
                )
                commit()
            print(f"Consolidated Reports folder ID: {consolidated_folder.id}")
            
            # Create operation-specific subfolder if it doesn't exist
            print("\nChecking/Creating operation folder...")
            operation_folder_name = "FINAL" if operation_no == "999" else f"OP{operation_no}"
            operation_folder_path = f"{consolidated_folder_path}/{operation_folder_name}"
            print(f"Operation folder path: {operation_folder_path}")
            operation_folder = FolderV2.get(lambda f: f.path == operation_folder_path)
            if not operation_folder:
                print(f"Creating operation folder: {operation_folder_name}")
                operation_folder = FolderV2(
                    name=operation_folder_name, 
                    path=operation_folder_path, 
                    parent_folder=consolidated_folder, 
                    created_by=user
                )
                commit()
            print(f"Operation folder ID: {operation_folder.id}")

                        # Measurements
            try:
                print("\n=== Fetching Measurements ===")
                # Use Pony ORM's get_connection method for direct SQL execution
                from app.database.connection import db
                
                # Get the underlying database connection
                with db_session:
                    conn = db.get_connection()
                    cursor = conn.cursor()

                    print(f"Fetching inspections for operation {operation_no}...")
                    # For specific operation
                    query = '''
                        SELECT id, order_id, op_no, quantity_no, op_id, 
                               nominal_value, uppertol, lowertol, zone, dimension_type,
                               measured_1, measured_2, measured_3, measured_mean, 
                               measured_instrument, used_inst, bbox, is_done, created_at
                        FROM quality.stage_inspection 
                        WHERE order_id = %s 
                        AND op_no = %s
                        ORDER BY id
                    '''
                    print(f"Query params: order_id={order.id}, op_no={operation_no}")
                    cursor.execute(query, (order.id, operation_no))

                    rows = cursor.fetchall()
                    cursor.close()

                print(f"Found {len(rows) if rows else 0} measurements")
                if not rows:
                    error_msg = "No stage inspections found for "
                    error_msg += f"order {order_number}" if operation_no == "999" else f"order {order_number}, operation {operation_no}"
                    print(f"Error: {error_msg}")
                    raise HTTPException(status_code=404, detail=error_msg)

                # Convert rows to measurement objects manually
                measurements = []
                for row in rows:
                    # Create a simple object with the row data matching actual table structure
                    measurement = type('Measurement', (object,), {})()
                    measurement.id = row[0]
                    measurement.order_id = row[1]
                    measurement.op_no = row[2]
                    measurement.quantity_no = row[3]
                    measurement.op_id = row[4]
                    measurement.nominal_value = row[5]
                    measurement.uppertol = row[6]
                    measurement.lowertol = row[7]
                    measurement.zone = row[8]
                    measurement.dimension_type = row[9]
                    measurement.measured_1 = row[10]
                    measurement.measured_2 = row[11]
                    measurement.measured_3 = row[12]
                    measurement.measured_mean = row[13]
                    measurement.measured_instrument = row[14]
                    measurement.used_inst = row[15]
                    measurement.bbox = row[16]
                    measurement.is_done = row[17]
                    measurement.created_at = row[18]
                    measurements.append(measurement)

            except Exception as e:
                error_msg = str(e)
                print(f"Query error: {error_msg}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error querying measurements: {error_msg}"
                )

            for m in measurements:
                grouped_measurements[m.quantity_no].append(m)

            first_measurement = measurements[0]
            unique_values = grouped_measurements[1]

            # Use the operation_no parameter, not the measurement op_no for display
            operation_display = "FINAL" if operation_no == "999" else f"OP{operation_no}"

            metadata = [
                ["Part Description", order.part_description or "-", "Inspection Plan", str(order.id)],
                ["Project", order.project.name if order.project else "-", "Part Number", order.part_number],
                ["Launched Quantity", order.launched_quantity, "Production Order", order.production_order],
                ["Measured Quantity", f"{len(grouped_measurements.keys())}/{order.launched_quantity}", "Timestamp",
                 pd.Timestamp.now().strftime("%d-%m-%Y %H:%M:%S")],
                ["Stage Detail ", operation_display, "", " "],
            ]

            report_data = []

            for m in unique_values:
                row = [m.nominal_value, m.uppertol, m.lowertol, m.zone]
                for quantity_no in sorted(grouped_measurements.keys()):
                    matching = next(
                        (x for x in grouped_measurements[quantity_no] if x.nominal_value == m.nominal_value), None)
                    row.append(matching.measured_mean if matching else None)
                report_data.append(row)

            print(report_data)

            columns = ["Nominal", "Upper Tol", "Lower Tol", "Zone"] + [str(q) for q in sorted(grouped_measurements.keys())]
            df = pd.DataFrame(report_data, columns=columns)
            df.insert(0, "Sl. No.", range(1, len(df) + 1))

            # Generate PDF in-memory
            pdf_buffer = io.BytesIO()
            save_df_to_pdf(df, metadata, pdf_buffer)
            pdf_buffer.seek(0)
            file_content = pdf_buffer.read()
            checksum = hashlib.sha256(file_content).hexdigest()

            # Upload to MinIO
            version_number = "1.0"
            minio_path = f"documents/v2/{consolidated_folder.path}/{order_number}/v{version_number}/Consolidated_Report_{order_number}.pdf"
            MinioService().upload_file(
                file=io.BytesIO(file_content),
                object_name=minio_path,
                content_type="application/pdf"
            )

            print("\n=== Document Creation ===")
            doc_type_obj = DocumentTypeV2.get(name="REPORT")
            if not doc_type_obj:
                print("Error: REPORT document type not found")
                raise HTTPException(status_code=404, detail="REPORT document type not found")
            print(f"Found REPORT document type: ID={doc_type_obj.id}")

            # Get latest consolidated doc if exists using raw SQL to avoid complex query issues
            doc_name = f"Consolidated_Report_{order_number}_{'FINAL' if operation_no == '999' else f'OP{operation_no}'}"
            print(f"Document name: {doc_name}")
            
            with db_session:
                conn = db.get_connection()
                cursor = conn.cursor()
                
                query = '''
                    SELECT id, name, folder_id_v2, doc_type_id_v2, description, part_number, 
                           production_order_id_v2, created_by_id_v2, created_at, is_active
                    FROM document_management_v2.documents 
                    WHERE folder_id_v2 = %s 
                    AND name = %s 
                    AND is_active = true
                    ORDER BY created_at DESC
                    LIMIT 1
                '''
                cursor.execute(query, (operation_folder.id, doc_name))
                doc_row = cursor.fetchone()
                cursor.close()

            existing_doc = None
            if doc_row:
                # Create a document object from the row
                existing_doc = type('Document', (object,), {})()
                existing_doc.id = doc_row[0]
                existing_doc.name = doc_row[1]
                existing_doc.folder_id_v2 = doc_row[2]
                existing_doc.doc_type_id_v2 = doc_row[3]
                existing_doc.description = doc_row[4]
                existing_doc.part_number = doc_row[5]
                existing_doc.production_order_id_v2 = doc_row[6]
                existing_doc.created_by_id_v2 = doc_row[7]
                existing_doc.created_at = doc_row[8]
                existing_doc.is_active = doc_row[9]
                
                # Get the latest version for this document
                with db_session:
                    conn = db.get_connection()
                    cursor = conn.cursor()
                    
                    version_query = '''
                        SELECT version_number
                        FROM document_management_v2.document_versions
                        WHERE document_id_v2 = %s
                        ORDER BY created_at DESC
                        LIMIT 1
                    '''
                    cursor.execute(version_query, (existing_doc.id,))
                    version_row = cursor.fetchone()
                    cursor.close()
                
                existing_doc.latest_version = None
                if version_row:
                    existing_doc.latest_version = type('Version', (object,), {})()
                    existing_doc.latest_version.version_number = version_row[0]

            if existing_doc:
                print("\nFound existing document, getting latest version...")
                # Get the actual DocumentV2 object from the database
                new_doc = DocumentV2.get(id=existing_doc.id)
                print(f"Retrieved document: ID={new_doc.id}")
                latest_version = new_doc.latest_version
                if latest_version and latest_version.version_number:
                    try:
                        major, minor = map(int, latest_version.version_number.split("."))
                        version_number = f"{major}.{minor + 1}"
                        print(f"Incrementing version from {latest_version.version_number} to {version_number}")
                    except Exception as e:
                        print(f"Error parsing version number: {e}")
                        # fallback if version format unexpected
                        version_number = "1.0"
                        print("Using fallback version: 1.0")
                else:
                    version_number = "1.0"
                    print("No previous version found, using version: 1.0")
            else:
                print("\nCreating new document...")
                # Create new document
                new_doc = DocumentV2(
                    name=f"Consolidated_Report_{order_number}_{'FINAL' if operation_no == '999' else f'OP{operation_no}'}",
                    folder=operation_folder,
                    doc_type=doc_type_obj,
                    description=f"Consolidated report for PO {order_number} {'Final Inspection' if operation_no == '999' else f'Operation {operation_no}'}",
                    part_number=order_number,
                    production_order=order,
                    created_by=user
                )
                commit()
                version_number = "1.0"
                print(f"Created new document: ID={new_doc.id}, Version={version_number}")

            print("\n=== File Upload and Version Creation ===")
            # Compute MinIO path
            minio_path = f"documents/v2/{operation_folder.path}/{new_doc.id}/v{version_number}/Consolidated_Report_{order_number}_{'FINAL' if operation_no == '999' else f'OP{operation_no}'}.pdf"
            print(f"MinIO path: {minio_path}")

            # Upload to MinIO directly from memory
            print("Uploading file to MinIO...")
            try:
                MinioService().upload_file(
                    file=io.BytesIO(file_content),
                    object_name=minio_path,
                    content_type="application/pdf"
                )
                print("File uploaded successfully")
            except Exception as e:
                print(f"Error uploading to MinIO: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error uploading file to MinIO: {str(e)}"
                )

            print("\nCreating document version...")
            # Create new version
            version = DocumentVersionV2(
                document=new_doc,
                version_number=version_number,
                minio_path=minio_path,
                file_size=len(file_content),
                checksum=checksum,
                created_by=user,
                metadata={
                    "consolidated_report": True,
                    "order_number": order_number,
                    "generation_date": datetime.utcnow().isoformat()
                }
            )
            print(f"Created version: {version_number} for document {new_doc.id}")
            new_doc.latest_version = version

            # Create access log
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
                    "id": new_doc.latest_version.id,
                    "document_id": new_doc.id,
                    "version_number": new_doc.latest_version.version_number,
                    "minio_path": new_doc.latest_version.minio_path,
                    "file_size": new_doc.latest_version.file_size,
                    "checksum": new_doc.latest_version.checksum,
                    "created_at": new_doc.latest_version.created_at,
                    "created_by_id": new_doc.latest_version.created_by.id,
                    "is_active": new_doc.latest_version.is_active,
                    "metadata": new_doc.latest_version.metadata
                }
            }

    except Exception as e:
        print(f"YEET ERROR: {str(e)}")
        import traceback
        print(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )

