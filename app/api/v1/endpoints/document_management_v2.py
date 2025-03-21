from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Path, status, Form
from typing import List, Union, Annotated, Optional, Dict, Any
import io
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
        # Process data outside db session
        metadata_dict = json.loads(metadata) if metadata else {}
        file_contents = await file.read()
        checksum = hashlib.sha256(file_contents).hexdigest()
        file_size = len(file_contents)
        file_ext = file.filename.split('.')[-1].lower()
        user_id = current_user.id

        with db_session:
            # Re-fetch user within session
            user = User[user_id]
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
            doc_type = DocType.get(type_name="IPID")
            if not doc_type:
                doc_type = DocType(
                    type_name="IPID",
                    description="In-Process Inspection Document",
                    file_extensions=[".pdf", ".doc", ".docx"],
                    is_active=True
                )
                flush()

            # Validate file extension
            if file_ext not in [ext.lower().strip('.') for ext in doc_type.file_extensions]:
                raise HTTPException(
                    status_code=400,
                    detail=f"File type .{file_ext} not allowed for IPID documents"
                )

            # Get default IPID folder
            ipid_folder = DocFolder.get(folder_name="IPID")
            if not ipid_folder:
                raise HTTPException(status_code=404, detail="IPID folder not found")

            # Create document with initial MinIO path
            temp_object_name = f"{production_order}/IPID/temp"

            # Create document record
            document = Document(
                folder=ipid_folder,
                part_number_id=order,
                doc_type=doc_type,
                document_name=document_name,
                description=description,
                created_by=user,
                minio_path=temp_object_name,
                is_active=True
            )
            flush()

            # Generate final MinIO path
            object_name = minio.generate_object_path(
                str(order.production_order),
                "IPID",
                document.id,
                1
            )

            # Upload to MinIO
            file_object = io.BytesIO(file_contents)
            minio_result = minio.upload_file(
                file=file_object,
                object_name=object_name,
                content_type=file.content_type or "application/octet-stream"
            )

            # Update document with final path
            document.minio_path = object_name

            # Create version with operation metadata
            version = DocumentVersion(
                document=document,
                version_number=version_number,
                minio_object_id=object_name,
                file_size=file_size,
                checksum=checksum,
                metadata={
                    **metadata_dict,
                    "operation_id": operation.id,
                    "operation_number": operation_number
                },
                created_by=user,
                status="active"
            )
            flush()

            document.latest_version = version

            # Log the action
            DocumentAccessLog(
                document=document,
                version=version,
                user=user,
                action_type="create"
            )

            # Format response according to DocumentResponse model
            response_data = {
                "id": document.id,
                "name": document.document_name,  # Changed from document_name to name
                "folder_id": document.folder.id,
                "doc_type_id": document.doc_type.id,
                "description": document.description,
                "part_number": order.production_order,
                "production_order_id": order.id,  # Added production_order_id
                "created_at": document.created_at,
                "created_by_id": document.created_by.id,  # Changed from created_by to created_by_id
                "is_active": document.is_active,
                "latest_version": {
                    "id": version.id,
                    "document_id": document.id,  # Added document_id
                    "version_number": version.version_number,
                    "minio_path": version.minio_object_id,  # Changed from minio_object_id to minio_path
                    "file_size": version.file_size,
                    "checksum": version.checksum,
                    "created_at": version.created_at,
                    "created_by_id": version.created_by.id,  # Changed from created_by to created_by_id
                    "is_active": True,  # Added is_active
                    "metadata": version.metadata
                }
            }

            commit()
            return response_data

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid metadata JSON format")
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