from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from typing import List, Optional, Dict
from pony.orm import db_session, select, flush, commit, rollback, desc, count as pony_count
import hashlib
import json
import io
import shutil
from datetime import datetime, timedelta
from app.schemas.document_schemas import (
    DocTypeCreate, DocTypeResponse, FolderCreate, FolderResponse,
    DocumentCreate, DocumentResponse, DocumentUpdate, DocumentVersionCreate,
    DocumentVersionResponse, DocumentSearchResponse, UploadDocumentRequest,
    DocumentVersionUpdateRequest, DocumentVersionFileUpdate, FolderOperation,
    FolderOperationResponse, DocumentMetrics, DocumentActivitySummary,
    TopAccessedDocument, FolderUtilization
)
from app.models.document_management import DocFolder, DocType, Document, DocumentVersion, DocumentAccessLog
from app.services.minio_service import MinioService
from app.core.security import get_current_user, get_current_admin_user
from app.models.user import User
from app.models.master_order import Order
from app.models.master_order import Operation


router = APIRouter(prefix="/documents", tags=["Document Management"])
minio_service = MinioService()


# Document Type Endpoints
@router.post("/types/", response_model=DocTypeResponse)
async def create_doc_type(
        doc_type: DocTypeCreate,
        current_user: User = Depends(get_current_admin_user)
):
    """Create a new document type"""
    with db_session:
        try:
            existing = DocType.get(type_name=doc_type.type_name)
            if existing:
                raise HTTPException(status_code=400, detail="Document type already exists")

            db_doc_type = DocType(
                type_name=doc_type.type_name,
                description=doc_type.description,
                file_extensions=doc_type.file_extensions,
                is_active=doc_type.is_active
            )
            commit()
            return db_doc_type
        except HTTPException:
            raise
        except Exception as e:
            rollback()
            raise HTTPException(status_code=500, detail=str(e))


@router.get("/types/", response_model=List[DocTypeResponse])
async def list_doc_types(
        current_user: User = Depends(get_current_user),
        include_inactive: bool = False
):
    """List all document types"""
    with db_session:
        query = select(dt for dt in DocType)
        if not include_inactive:
            query = query.filter(lambda dt: dt.is_active)
        return list(query)


# Folder Endpoints
@router.post("/folders/", response_model=FolderResponse)
async def create_folder(
        folder: FolderCreate,
        current_user: User = Depends(get_current_user)
):
    """Create a new folder"""
    user_id = current_user.id  # Get user ID outside session

    with db_session:
        try:
            # Re-fetch user within session
            user = User.get(id=user_id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            parent_path = ""
            if folder.parent_folder_id:
                if folder.parent_folder_id == 0:
                    folder.parent_folder_id = None
                else:
                    parent = DocFolder.get(id=folder.parent_folder_id)
                    if not parent:
                        raise HTTPException(status_code=404, detail="Parent folder not found")
                    if not parent.is_active:
                        raise HTTPException(status_code=400, detail="Parent folder is inactive")
                    parent_path = parent.folder_path

            folder_path = f"{parent_path}/{folder.folder_name}".lstrip("/")

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
                created_by=user,  # Use re-fetched user
                is_active=folder.is_active
            )

            flush()
            commit()

            return FolderResponse(
                id=db_folder.id,
                folder_name=db_folder.folder_name,
                folder_path=db_folder.folder_path,
                parent_folder_id=db_folder.parent_folder,
                created_at=db_folder.created_at,
                created_by=user.id,
                is_active=db_folder.is_active
            )

        except HTTPException:
            rollback()
            raise
        except Exception as e:
            rollback()
            raise HTTPException(status_code=500, detail=str(e))


@router.get("/folders/", response_model=List[FolderResponse])
async def list_folders(
        current_user: User = Depends(get_current_user),
        parent_id: Optional[int] = None
):
    """List folders, optionally filtered by parent folder"""
    with db_session:
        query = select(f for f in DocFolder if f.is_active)
        if parent_id is not None:
            if parent_id == 0:  # Handle root level folders
                query = query.filter(lambda f: f.parent_folder is None)
            else:
                query = query.filter(lambda f: f.parent_folder == parent_id)

        folders = list(query)
        return [
            FolderResponse(
                id=f.id,
                folder_name=f.folder_name,
                folder_path=f.folder_path,
                parent_folder_id=f.parent_folder,
                created_at=f.created_at,
                created_by=f.created_by.id,
                is_active=f.is_active
            ) for f in folders
        ]


# Document Endpoints


@router.post("/upload/", response_model=DocumentResponse)
async def upload_document(
        file: UploadFile = File(...),
        folder_id: int = Form(...),
        part_number_id: int = Form(...),
        doc_type_id: int = Form(...),
        document_name: str = Form(...),
        description: Optional[str] = Form(None),
        version_number: str = Form(...),
        metadata: Optional[str] = Form("{}"),
        current_user: User = Depends(get_current_user)
):
    """Upload a new document with initial version"""
    try:
        # Process data outside db session
        metadata_dict = json.loads(metadata) if metadata else {}
        file_contents = await file.read()
        checksum = hashlib.sha256(file_contents).hexdigest()
        file_size = len(file_contents)
        file_ext = file.filename.split('.')[-1].lower()
        user_id = current_user.id

        with db_session:
            try:
                # Re-fetch user within session
                user = User.get(id=user_id)
                if not user:
                    raise HTTPException(status_code=404, detail="User not found")

                # Validate folder
                folder = DocFolder.get(id=folder_id)
                if not folder:
                    raise HTTPException(status_code=404, detail="Folder not found")
                if not folder.is_active:
                    raise HTTPException(status_code=400, detail="Folder is inactive")

                # Validate order
                order = Order.get(id=part_number_id)
                if not order:
                    raise HTTPException(status_code=404, detail="Order not found")

                # Validate document type
                doc_type = DocType.get(id=doc_type_id)
                if not doc_type:
                    raise HTTPException(status_code=404, detail="Document type not found")
                if not doc_type.is_active:
                    raise HTTPException(status_code=400, detail="Document type is inactive")

                if file_ext not in [ext.lower().strip('.') for ext in doc_type.file_extensions]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"File type .{file_ext} not allowed for this document type"
                    )

                # Create document with initial minio_path
                temp_object_name = f"{order.production_order}/{doc_type.type_name}/temp"
                db_document = Document(
                    folder=folder,
                    part_number_id=order,
                    doc_type=doc_type,
                    document_name=document_name,
                    description=description,
                    created_by=user,
                    minio_path=temp_object_name,
                    is_active=True
                )
                flush()

                # Generate final MinIO path using document ID
                object_name = minio_service.generate_object_path(
                    str(order.production_order),
                    doc_type.type_name,
                    db_document.id,
                    1  # First version
                )

                try:
                    # Create BytesIO object with the file contents
                    file_object = io.BytesIO(file_contents)

                    # Upload to MinIO
                    minio_result = minio_service.upload_file(
                        file=file_object,
                        object_name=object_name,
                        content_type=file.content_type or "application/octet-stream"
                    )
                except Exception as e:
                    rollback()
                    raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

                # Update document with final minio_path
                db_document.minio_path = object_name

                # Create version
                db_version = DocumentVersion(
                    document=db_document,
                    version_number=version_number,
                    minio_object_id=object_name,
                    file_size=file_size,
                    checksum=checksum,
                    metadata=metadata_dict,
                    created_by=user,
                    status="active"
                )

                db_document.latest_version = db_version

                # Create access log
                DocumentAccessLog(
                    document=db_document,
                    version=db_version,
                    user=user,
                    action_type="create"
                )

                commit()

                return DocumentResponse(
                    id=db_document.id,
                    folder_id=folder.id,
                    part_number_id=order.id,
                    part_number=order.production_order,
                    doc_type_id=doc_type.id,
                    document_name=document_name,
                    description=description,
                    created_at=db_document.created_at,
                    created_by=user.id,
                    is_active=True,
                    latest_version=DocumentVersionResponse(
                        id=db_version.id,
                        version_number=version_number,
                        file_size=file_size,
                        checksum=checksum,
                        metadata=metadata_dict,
                        created_at=db_version.created_at,
                        created_by=user.id,
                        status="active"
                    ),
                    versions=[DocumentVersionResponse(
                        id=db_version.id,
                        version_number=version_number,
                        file_size=file_size,
                        checksum=checksum,
                        metadata=metadata_dict,
                        created_at=db_version.created_at,
                        created_by=user.id,
                        status="active"
                    )]
                )

            except HTTPException:
                rollback()
                raise
            except Exception as e:
                rollback()
                raise HTTPException(status_code=500, detail=str(e))

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid metadata JSON format")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{document_id}/download/{version_id}")
async def download_document(
        document_id: int,
        version_id: int,
        current_user: User = Depends(get_current_user)
):
    """Download a specific version of a document"""
    # First db session to get and validate entities
    with db_session:
        document = Document.get(id=document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        if not document.is_active:
            raise HTTPException(status_code=400, detail="Document is inactive")

        version = DocumentVersion.get(id=version_id, document=document)
        if not version:
            raise HTTPException(status_code=404, detail="Version not found")

        # Store necessary values
        minio_object_id = version.minio_object_id
        file_size = version.file_size
        document_name = document.document_name

    try:
        # Get file from MinIO (outside db session)
        file_stream = minio_service.get_file(minio_object_id)

        # Create access log in a separate db session
        with db_session:
            DocumentAccessLog(
                document=Document[document_id],
                version=DocumentVersion[version_id],
                user=User[current_user.id],
                action_type="download"
            )
            commit()

        return StreamingResponse(
            file_stream,
            media_type=file_stream.headers.get("content-type", "application/octet-stream"),
            headers={
                "Content-Disposition": f'attachment; filename="{document_name}"',
                "Content-Length": str(file_size)
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve file: {str(e)}"
        )


@router.post("/{document_id}/versions/", response_model=DocumentVersionResponse)
async def create_document_version(
        document_id: int,
        file: UploadFile = File(...),
        version_number: str = Form(...),
        metadata: Optional[str] = Form("{}"),
        current_user: User = Depends(get_current_user)
):
    """Create a new version of an existing document"""
    try:
        metadata_dict = json.loads(metadata) if metadata else {}
        file_contents = await file.read()
        checksum = hashlib.sha256(file_contents).hexdigest()
        file_size = len(file_contents)
        user_id = current_user.id

        with db_session:
            try:
                user = User.get(id=user_id)
                if not user:
                    raise HTTPException(status_code=404, detail="User not found")

                document = Document.get(id=document_id)
                if not document:
                    raise HTTPException(status_code=404, detail="Document not found")

                # Generate new version object name
                object_name = minio_service.generate_object_path(
                    str(document.part_number_id.production_order),
                    document.doc_type.type_name,
                    document_id,
                    len(document.versions) + 1
                )

                # Upload to MinIO
                file_data = io.BytesIO(file_contents)
                minio_service.upload_file(
                    file=file_data,
                    object_name=object_name,
                    content_type=file.content_type or "application/octet-stream"
                )

                # Create new version
                new_version = DocumentVersion(
                    document=document,
                    version_number=version_number,
                    minio_object_id=object_name,
                    file_size=file_size,
                    checksum=checksum,
                    metadata=metadata_dict,
                    created_by=user,
                    status="active"
                )

                document.latest_version = new_version

                # Create access log
                DocumentAccessLog(
                    document=document,
                    version=new_version,
                    user=user,
                    action_type="create_version"
                )

                commit()

                # Return response in correct format
                return {
                    "id": new_version.id,
                    "version_number": new_version.version_number,
                    "file_size": new_version.file_size,
                    "checksum": new_version.checksum,
                    "metadata": new_version.metadata,
                    "created_at": new_version.created_at,
                    "created_by": new_version.created_by.id,
                    "status": new_version.status
                }

            except HTTPException:
                rollback()
                raise
            except Exception as e:
                rollback()
                raise HTTPException(status_code=500, detail=str(e))

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid metadata JSON format")


@router.get("/folder/{folder_id}/documents", response_model=DocumentSearchResponse)
async def list_folder_documents(
        folder_id: int,
        skip: int = 0,
        limit: int = 100,
        current_user: User = Depends(get_current_user)
):
    """List all documents in a folder with pagination"""
    with db_session:
        try:
            folder = DocFolder.get(id=folder_id)
            if not folder:
                raise HTTPException(status_code=404, detail="Folder not found")

            query = select(d for d in Document if d.folder.id == folder_id and d.is_active)
            total = query.count()
            documents = query[skip:skip + limit]

            # Convert Pony entities to dict format
            doc_list = []
            for d in documents:
                latest_ver = d.latest_version
                versions = list(d.versions)

                doc_dict = {
                    "id": d.id,
                    "folder_id": d.folder.id,
                    "part_number_id": d.part_number_id.id,
                    "part_number": d.part_number_id.production_order,
                    "doc_type_id": d.doc_type.id,
                    "document_name": d.document_name,
                    "description": d.description,
                    "created_at": d.created_at,
                    "created_by": d.created_by.id,
                    "is_active": d.is_active,
                    "latest_version": {
                        "id": latest_ver.id,
                        "version_number": latest_ver.version_number,
                        "file_size": latest_ver.file_size,
                        "checksum": latest_ver.checksum,
                        "metadata": latest_ver.metadata,
                        "created_at": latest_ver.created_at,
                        "created_by": latest_ver.created_by.id,
                        "status": latest_ver.status
                    } if latest_ver else None,
                    "versions": [{
                        "id": v.id,
                        "version_number": v.version_number,
                        "file_size": v.file_size,
                        "checksum": v.checksum,
                        "metadata": v.metadata,
                        "created_at": v.created_at,
                        "created_by": v.created_by.id,
                        "status": v.status
                    } for v in versions]
                }
                doc_list.append(doc_dict)

            return DocumentSearchResponse(
                total=total,
                documents=doc_list,
                skip=skip,
                limit=limit
            )

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.get("/search/", response_model=DocumentSearchResponse)
async def search_documents(
        search_text: Optional[str] = None,
        doc_type_id: Optional[int] = None,
        folder_id: Optional[int] = None,
        skip: int = 0,
        limit: int = 100,
        current_user: User = Depends(get_current_user)
):
    """Search documents by name, description"""
    with db_session:
        try:
            # Base query for active documents
            query = select(d for d in Document if d.is_active)

            # Apply filters
            if search_text:
                query = query.filter(lambda d:
                                     search_text.lower() in d.document_name.lower() or
                                     (d.description and search_text.lower() in d.description.lower())
                                     )

            if doc_type_id:
                query = query.filter(lambda d: d.doc_type.id == doc_type_id)

            if folder_id:
                query = query.filter(lambda d: d.folder.id == folder_id)

            total = query.count()
            documents = list(query[skip:skip + limit])

            doc_list = [{
                "id": d.id,
                "folder_id": d.folder.id,
                "part_number_id": d.part_number_id.id,
                "part_number": d.part_number_id.production_order,
                "doc_type_id": d.doc_type.id,
                "document_name": d.document_name,
                "description": d.description,
                "created_at": d.created_at,
                "created_by": d.created_by.id,
                "is_active": d.is_active,
                "latest_version": {
                    "id": d.latest_version.id,
                    "version_number": d.latest_version.version_number,
                    "file_size": d.latest_version.file_size,
                    "checksum": d.latest_version.checksum,
                    "metadata": d.latest_version.metadata,
                    "created_at": d.latest_version.created_at,
                    "created_by": d.latest_version.created_by.id,
                    "status": d.latest_version.status
                } if d.latest_version else None,
                "versions": [{
                    "id": v.id,
                    "version_number": v.version_number,
                    "file_size": v.file_size,
                    "checksum": v.checksum,
                    "metadata": v.metadata,
                    "created_at": v.created_at,
                    "created_by": v.created_by.id,
                    "status": v.status
                } for v in d.versions]
            } for d in documents]

            return DocumentSearchResponse(
                total=total,
                documents=doc_list,
                skip=skip,
                limit=limit
            )

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.get("/by-part-number/", response_model=DocumentSearchResponse)
async def get_documents_by_part_number(
        part_number: str,
        doc_type_id: Optional[int] = None,
        current_user: User = Depends(get_current_user)
):
    """Get documents by part number and optional document type"""
    with db_session:
        try:
            # Find the order by production order number
            order = Order.get(production_order=part_number)
            if not order:
                raise HTTPException(status_code=404, detail="Part number not found")

            # Base query for active documents with matching part number
            query = select(d for d in Document
                           if d.is_active and d.part_number_id.id == order.id)

            # Apply doc type filter if provided
            if doc_type_id:
                query = query.filter(lambda d: d.doc_type.id == doc_type_id)

            documents = list(query)

            doc_list = [{
                "id": d.id,
                "folder_id": d.folder.id,
                "part_number_id": d.part_number_id.id,
                "part_number": d.part_number_id.production_order,
                "doc_type_id": d.doc_type.id,
                "document_name": d.document_name,
                "description": d.description,
                "created_at": d.created_at,
                "created_by": d.created_by.id,
                "is_active": d.is_active,
                "latest_version": {
                    "id": d.latest_version.id,
                    "version_number": d.latest_version.version_number,
                    "file_size": d.latest_version.file_size,
                    "checksum": d.latest_version.checksum,
                    "metadata": d.latest_version.metadata,
                    "created_at": d.latest_version.created_at,
                    "created_by": d.latest_version.created_by.id,
                    "status": d.latest_version.status
                } if d.latest_version else None,
                "versions": [{
                    "id": v.id,
                    "version_number": v.version_number,
                    "file_size": v.file_size,
                    "checksum": v.checksum,
                    "metadata": v.metadata,
                    "created_at": v.created_at,
                    "created_by": v.created_by.id,
                    "status": v.status
                } for v in d.versions]
            } for d in documents]

            return DocumentSearchResponse(
                total=len(documents),
                documents=doc_list,
                skip=0,
                limit=len(documents)
            )

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.get("/{document_id}/download")
async def download_latest_document(
        document_id: int,
        current_user: User = Depends(get_current_user)
):
    """Download the latest version of a document"""
    with db_session:
        document = Document.get(id=document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        if not document.is_active:
            raise HTTPException(status_code=400, detail="Document is inactive")

        latest_version = document.latest_version
        if not latest_version:
            raise HTTPException(status_code=404, detail="No versions found for this document")

        # Store necessary values
        minio_object_id = latest_version.minio_object_id
        file_size = latest_version.file_size
        document_name = document.document_name

    try:
        # Get file from MinIO (outside db session)
        file_stream = minio_service.get_file(minio_object_id)

        # Create access log in a separate db session
        with db_session:
            DocumentAccessLog(
                document=Document[document_id],
                version=latest_version.id,
                user=User[current_user.id],
                action_type="download"
            )
            commit()

        return StreamingResponse(
            file_stream,
            media_type=file_stream.headers.get("content-type", "application/octet-stream"),
            headers={
                "Content-Disposition": f'attachment; filename="{document_name}"',
                "Content-Length": str(file_size)
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve file: {str(e)}"
        )


@router.put("/{document_id}", response_model=DocumentResponse)
async def update_document(
        document_id: int,
        update_data: DocumentUpdate,
        current_user: User = Depends(get_current_user)
):
    """Update document metadata"""
    with db_session:
        try:
            document = Document.get(id=document_id)
            if not document:
                raise HTTPException(status_code=404, detail="Document not found")

            if update_data.folder_id is not None:
                folder = DocFolder.get(id=update_data.folder_id)
                if not folder:
                    raise HTTPException(status_code=404, detail="Folder not found")
                if not folder.is_active:
                    raise HTTPException(status_code=400, detail="Folder is inactive")
                document.folder = folder

            if update_data.document_name is not None:
                document.document_name = update_data.document_name

            if update_data.description is not None:
                document.description = update_data.description

            if update_data.is_active is not None:
                document.is_active = update_data.is_active

            # Create access log
            DocumentAccessLog(
                document=document,
                user=current_user,
                action_type="update"
            )

            commit()
            return DocumentResponse.from_orm(document)

        except HTTPException:
            rollback()
            raise
        except Exception as e:
            rollback()
            raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{document_id}")
async def delete_document(
        document_id: int,
        current_user: User = Depends(get_current_user)
):
    """Soft delete a document"""
    with db_session:
        try:
            document = Document.get(id=document_id)
            if not document:
                raise HTTPException(status_code=404, detail="Document not found")

            document.is_active = False

            # Create access log
            DocumentAccessLog(
                document=document,
                user=current_user,
                action_type="delete"
            )

            commit()
            return {"message": "Document deleted successfully"}

        except HTTPException:
            rollback()
            raise
        except Exception as e:
            rollback()
            raise HTTPException(status_code=500, detail=str(e))


@router.put("/{document_id}/versions/{version_id}", response_model=DocumentVersionResponse)
async def update_version(
        document_id: int,
        version_id: int,
        update_data: DocumentVersionUpdateRequest,
        current_user: User = Depends(get_current_user)
):
    """Update version metadata or status"""
    with db_session:
        try:
            document = Document.get(id=document_id)
            if not document:
                raise HTTPException(status_code=404, detail="Document not found")
            if not document.is_active:
                raise HTTPException(status_code=400, detail="Document is inactive")

            version = DocumentVersion.get(id=version_id, document=document)
            if not version:
                raise HTTPException(status_code=404, detail="Version not found")

            # Validate status
            if update_data.status not in ["active", "archived", "deprecated"]:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid status. Must be one of: active, archived, deprecated"
                )

            version.status = update_data.status
            if update_data.metadata is not None:
                version.metadata = update_data.metadata

            # Create access log
            DocumentAccessLog(
                document=document,
                version=version,
                user=current_user,
                action_type="update_version"
            )

            commit()
            return DocumentVersionResponse(
                id=version.id,
                version_number=version.version_number,
                file_size=version.file_size,
                checksum=version.checksum,
                metadata=version.metadata,
                created_at=version.created_at,
                created_by=version.created_by.id,
                status=version.status
            )

        except HTTPException:
            rollback()
            raise
        except Exception as e:
            rollback()
            raise HTTPException(status_code=500, detail=str(e))


@router.get("/{document_id}/versions", response_model=List[DocumentVersionResponse])
async def list_versions(
        document_id: int,
        current_user: User = Depends(get_current_user)
):
    """List all versions of a document"""
    with db_session:
        try:
            document = Document.get(id=document_id)
            if not document:
                raise HTTPException(status_code=404, detail="Document not found")

            versions = list(document.versions)
            return [{
                "id": v.id,
                "version_number": v.version_number,
                "file_size": v.file_size,
                "checksum": v.checksum,
                "metadata": v.metadata,
                "created_at": v.created_at,
                "created_by": v.created_by.id,
                "status": v.status
            } for v in versions]

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.put("/folders/{folder_id}", response_model=FolderResponse)
async def update_folder(
        folder_id: int,
        folder_data: FolderCreate,
        current_user: User = Depends(get_current_user)
):
    """Update folder details"""
    with db_session:
        try:
            folder = DocFolder.get(id=folder_id)
            if not folder:
                raise HTTPException(status_code=404, detail="Folder not found")

            if folder_data.parent_folder_id:
                parent = DocFolder.get(id=folder_data.parent_folder_id)
                if not parent:
                    raise HTTPException(status_code=404, detail="Parent folder not found")
                if not parent.is_active:
                    raise HTTPException(status_code=400, detail="Parent folder is inactive")
                folder.parent_folder = folder_data.parent_folder_id

            folder.folder_name = folder_data.folder_name
            folder.is_active = folder_data.is_active

            parent_path = ""
            if folder.parent_folder:
                parent = DocFolder.get(id=folder.parent_folder)
                parent_path = parent.folder_path

            new_folder_path = f"{parent_path}/{folder.folder_name}".lstrip("/")

            # Check if new path already exists
            existing = DocFolder.get(folder_path=new_folder_path)
            if existing and existing.id != folder_id:
                raise HTTPException(
                    status_code=400,
                    detail="A folder with this path already exists"
                )

            folder.folder_path = new_folder_path

            commit()
            return FolderResponse.from_orm(folder)

        except HTTPException:
            rollback()
            raise
        except Exception as e:
            rollback()
            raise HTTPException(status_code=500, detail=str(e))


@router.delete("/folders/{folder_id}")
async def delete_folder(
        folder_id: int,
        current_user: User = Depends(get_current_user)
):
    """Soft delete a folder"""
    with db_session:
        try:
            folder = DocFolder.get(id=folder_id)
            if not folder:
                raise HTTPException(status_code=404, detail="Folder not found")

            # Check if folder has active documents
            docs_count = select(
                d for d in Document
                if d.folder.id == folder_id and d.is_active
            ).count()

            if docs_count > 0:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot delete folder containing active documents"
                )

            folder.is_active = False
            commit()
            return {"message": "Folder deleted successfully"}

        except HTTPException:
            rollback()
            raise
        except Exception as e:
            rollback()
            raise HTTPException(status_code=500, detail=str(e))


@router.get("/download-by-part-number")
async def download_by_part_number_and_type(
        part_number: str,
        doc_type_id: int,
        current_user: User = Depends(get_current_user)
):
    """Download the latest version of a document for a specific part number and document type"""
    with db_session:
        try:
            # Find the order by production order number
            order = Order.get(production_order=part_number)
            if not order:
                raise HTTPException(status_code=404, detail="Part number not found")

            # First get all matching documents
            documents = select(d for d in Document
                               if d.is_active and
                               d.part_number_id.id == order.id and
                               d.doc_type.id == doc_type_id
                               ).order_by(lambda d: desc(d.created_at))

            # Get the first document with a latest version
            document = None
            for d in documents:
                if d.latest_version is not None:
                    document = d
                    break

            if not document:
                raise HTTPException(
                    status_code=404,
                    detail="No document found for this part number and document type"
                )

            latest_version = document.latest_version
            if not latest_version:
                raise HTTPException(
                    status_code=404,
                    detail="No versions found for this document"
                )

            # Store necessary values
            minio_object_id = latest_version.minio_object_id
            file_size = latest_version.file_size
            document_name = document.document_name
            version_id = latest_version.id
            document_id = document.id

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    try:
        # Get file from MinIO (outside db session)
        file_stream = minio_service.get_file(minio_object_id)

        # Create access log in a separate db session
        with db_session:
            DocumentAccessLog(
                document=Document[document_id],
                version=DocumentVersion[version_id],
                user=User[current_user.id],
                action_type="download"
            )
            commit()

        return StreamingResponse(
            file_stream,
            media_type=file_stream.headers.get("content-type", "application/octet-stream"),
            headers={
                "Content-Disposition": f'attachment; filename="{document_name}"',
                "Content-Length": str(file_size)
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve file: {str(e)}"
        )


@router.put("/{document_id}/versions/{version_id}/file", response_model=DocumentVersionResponse)
async def update_version_file(
        document_id: int,
        version_id: int,
        file: UploadFile = File(...),
        version_number: Optional[str] = Form(None),
        metadata: Optional[str] = Form("{}"),
        current_user: User = Depends(get_current_user)
):
    """Update a version with a new file, replacing the existing one"""
    try:
        file_contents = await file.read()
        checksum = hashlib.sha256(file_contents).hexdigest()
        file_size = len(file_contents)
        file_ext = file.filename.split('.')[-1].lower()
        user_id = current_user.id

        # Parse metadata
        try:
            metadata_dict = json.loads(metadata) if metadata else {}
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid metadata JSON format")

        with db_session:
            try:
                document = Document.get(id=document_id)
                if not document:
                    raise HTTPException(status_code=404, detail="Document not found")
                if not document.is_active:
                    raise HTTPException(status_code=400, detail="Document is inactive")

                version = DocumentVersion.get(id=version_id, document=document)
                if not version:
                    raise HTTPException(status_code=404, detail="Version not found")

                # Validate file extension
                if file_ext not in [ext.lower().strip('.') for ext in document.doc_type.file_extensions]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"File type .{file_ext} not allowed for this document type"
                    )

                # Store old MinIO object ID for deletion
                old_object_id = version.minio_object_id

                # Generate new object name
                object_name = minio_service.generate_object_path(
                    str(document.part_number_id.production_order),
                    document.doc_type.type_name,
                    document_id,
                    version_id
                )

                # Upload new file to MinIO
                file_data = io.BytesIO(file_contents)
                minio_service.upload_file(
                    file=file_data,
                    object_name=object_name,
                    content_type=file.content_type or "application/octet-stream"
                )

                # Update version details
                version.minio_object_id = object_name
                version.file_size = file_size
                version.checksum = checksum

                if version_number:
                    version.version_number = version_number
                if metadata_dict is not None:
                    version.metadata = metadata_dict

                # Create access log
                DocumentAccessLog(
                    document=document,
                    version=version,
                    user=User[user_id],
                    action_type="update_version_file"
                )

                commit()

                # Delete old file from MinIO after successful commit
                try:
                    minio_service.delete_file(old_object_id)
                except Exception as e:
                    # Log error but don't fail the request
                    print(f"Error deleting old file from MinIO: {str(e)}")

                return DocumentVersionResponse(
                    id=version.id,
                    version_number=version.version_number,
                    file_size=version.file_size,
                    checksum=version.checksum,
                    metadata=version.metadata,
                    created_at=version.created_at,
                    created_by=version.created_by.id,
                    status=version.status
                )

            except HTTPException:
                rollback()
                raise
            except Exception as e:
                rollback()
                raise HTTPException(status_code=500, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/folders/{folder_id}/operation", response_model=FolderOperationResponse)
async def folder_operation(
        folder_id: int,
        operation: FolderOperation,
        current_user: User = Depends(get_current_user)
):
    """Copy or cut (move) a folder to another location"""
    with db_session:
        try:
            # Get source folder
            source_folder = DocFolder.get(id=folder_id)
            if not source_folder:
                raise HTTPException(status_code=404, detail="Source folder not found")
            if not source_folder.is_active:
                raise HTTPException(status_code=400, detail="Source folder is inactive")

            # Get destination folder
            dest_folder = DocFolder.get(id=operation.destination_folder_id)
            if not dest_folder:
                raise HTTPException(status_code=404, detail="Destination folder not found")
            if not dest_folder.is_active:
                raise HTTPException(status_code=400, detail="Destination folder is inactive")

            # Prevent moving folder to itself or its subfolder
            if operation.operation_type == 'cut':
                current = dest_folder
                while current:
                    if current.id == source_folder.id:
                        raise HTTPException(
                            status_code=400,
                            detail="Cannot move folder into itself or its subfolder"
                        )
                    current = DocFolder.get(id=current.parent_folder) if current.parent_folder else None

            # Generate new folder path
            new_folder_name = source_folder.folder_name
            new_parent_path = dest_folder.folder_path
            new_folder_path = f"{new_parent_path}/{new_folder_name}".lstrip("/")

            # Check if destination path already exists
            existing = DocFolder.get(folder_path=new_folder_path)
            if existing:
                # Append number to folder name if it exists
                counter = 1
                while True:
                    new_folder_name = f"{source_folder.folder_name}_{counter}"
                    new_folder_path = f"{new_parent_path}/{new_folder_name}".lstrip("/")
                    existing = DocFolder.get(folder_path=new_folder_path)
                    if not existing:
                        break
                    counter += 1

            if operation.operation_type == 'copy':
                # Create new folder
                new_folder = DocFolder(
                    parent_folder=operation.destination_folder_id,
                    folder_name=new_folder_name,
                    folder_path=new_folder_path,
                    created_by=current_user,
                    is_active=True
                )
                flush()

                # Store document data for copying
                docs_to_copy = []
                for doc in select(d for d in Document if d.folder == source_folder and d.is_active):
                    doc_data = {
                        'part_number_id': doc.part_number_id,
                        'doc_type': doc.doc_type,
                        'document_name': doc.document_name,
                        'description': doc.description,
                        'versions': []
                    }

                    for ver in doc.versions:
                        ver_data = {
                            'version_number': ver.version_number,
                            'minio_object_id': ver.minio_object_id,
                            'file_size': ver.file_size,
                            'checksum': ver.checksum,
                            'metadata': ver.metadata,
                            'status': ver.status,
                            'is_latest': ver == doc.latest_version
                        }
                        doc_data['versions'].append(ver_data)

                    docs_to_copy.append(doc_data)

                # Process each document
                for doc_data in docs_to_copy:
                    new_doc = Document(
                        folder=new_folder,
                        part_number_id=doc_data['part_number_id'],
                        doc_type=doc_data['doc_type'],
                        document_name=doc_data['document_name'],
                        description=doc_data['description'],
                        created_by=current_user,
                        is_active=True
                    )
                    flush()

                    # Copy versions
                    for ver_data in doc_data['versions']:
                        # Generate new MinIO path
                        new_object_name = minio_service.generate_object_path(
                            str(doc_data['part_number_id'].production_order),
                            doc_data['doc_type'].type_name,
                            new_doc.id,
                            len(new_doc.versions) + 1
                        )

                        # Copy file in MinIO
                        try:
                            file_stream = minio_service.get_file(ver_data['minio_object_id'])
                            minio_service.upload_file(
                                file=file_stream,
                                object_name=new_object_name,
                                content_type=file_stream.headers.get("content-type", "application/octet-stream")
                            )
                        except Exception as e:
                            rollback()
                            raise HTTPException(
                                status_code=500,
                                detail=f"Failed to copy file in storage: {str(e)}"
                            )

                        # Create new version
                        new_version = DocumentVersion(
                            document=new_doc,
                            version_number=ver_data['version_number'],
                            minio_object_id=new_object_name,
                            file_size=ver_data['file_size'],
                            checksum=ver_data['checksum'],
                            metadata=ver_data['metadata'],
                            created_by=current_user,
                            status=ver_data['status']
                        )
                        if ver_data['is_latest']:
                            new_doc.latest_version = new_version

                commit()
                return FolderOperationResponse(
                    success=True,
                    message=f"Folder copied successfully as '{new_folder_name}'",
                    new_folder_id=new_folder.id
                )

            else:  # Cut operation
                # Update folder path and parent
                source_folder.parent_folder = operation.destination_folder_id
                source_folder.folder_name = new_folder_name
                source_folder.folder_path = new_folder_path

                # Update paths of all subfolders
                old_path_prefix = f"{source_folder.folder_path}/"
                for subfolder in select(f for f in DocFolder if f.folder_path.startswith(old_path_prefix)):
                    subfolder.folder_path = f"{new_folder_path}/{subfolder.folder_path[len(old_path_prefix):]}"

                commit()
                return FolderOperationResponse(
                    success=True,
                    message=f"Folder moved successfully as '{new_folder_name}'",
                    new_folder_id=source_folder.id
                )

        except HTTPException:
            rollback()
            raise
        except Exception as e:
            rollback()
            raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{document_id}/versions/{version_id}", response_model=DocumentResponse)
async def delete_document_version(
        document_id: int,
        version_id: int,
        current_user: User = Depends(get_current_user)
):
    """Delete a specific version of a document"""
    # First transaction: Get necessary data and validate
    with db_session:
        try:
            document = Document.get(id=document_id)
            if not document:
                raise HTTPException(status_code=404, detail="Document not found")
            if not document.is_active:
                raise HTTPException(status_code=400, detail="Document is inactive")

            version = DocumentVersion.get(id=version_id, document=document)
            if not version:
                raise HTTPException(status_code=404, detail="Version not found")

            # Check if this is the only version
            versions_count = len(document.versions)
            if versions_count == 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot delete the only version of a document"
                )

            # Store necessary data
            minio_object_id = version.minio_object_id
            is_latest = document.latest_version.id == version_id if document.latest_version else False
            new_latest_id = None

            if is_latest:
                # Find the new latest version
                other_versions = select(
                    v for v in DocumentVersion
                    if v.document == document and v.id != version_id
                ).order_by(lambda v: desc(v.created_at))

                if other_versions:
                    new_latest_id = other_versions.first().id

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # Second transaction: Update and delete
    with db_session:
        try:
            document = Document.get(id=document_id)
            version = DocumentVersion.get(id=version_id, document=document)

            # Update latest version if needed
            if is_latest and new_latest_id:
                new_latest = DocumentVersion.get(id=new_latest_id)
                document.latest_version = new_latest
            elif is_latest:
                document.latest_version = None

            # Create access log
            DocumentAccessLog(
                document=document,
                user=current_user,
                action_type="delete_version"
            )

            # Delete the version
            version.delete()
            commit()

            # Delete file from MinIO after successful commit
            try:
                minio_service.delete_file(minio_object_id)
            except Exception as e:
                # Log error but don't fail the request
                print(f"Error deleting file from MinIO: {str(e)}")

        except Exception as e:
            rollback()
            raise HTTPException(status_code=500, detail=str(e))

    # Third transaction: Get updated document data
    with db_session:
        try:
            updated_doc = Document.get(id=document_id)
            if not updated_doc:
                raise HTTPException(status_code=404, detail="Document not found after version deletion")

            # Prepare response
            response_data = {
                "id": updated_doc.id,
                "folder_id": updated_doc.folder.id,
                "part_number_id": updated_doc.part_number_id.id,
                "part_number": updated_doc.part_number_id.production_order,
                "doc_type_id": updated_doc.doc_type.id,
                "document_name": updated_doc.document_name,
                "description": updated_doc.description,
                "created_at": updated_doc.created_at,
                "created_by": updated_doc.created_by.id,
                "is_active": updated_doc.is_active,
                "versions": []
            }

            # Add versions data
            for v in updated_doc.versions:
                version_data = {
                    "id": v.id,
                    "version_number": v.version_number,
                    "file_size": v.file_size,
                    "checksum": v.checksum,
                    "metadata": v.metadata,
                    "created_at": v.created_at,
                    "created_by": v.created_by.id,
                    "status": v.status
                }
                response_data["versions"].append(version_data)

            # Add latest version data if exists
            if updated_doc.latest_version:
                latest = updated_doc.latest_version
                response_data["latest_version"] = {
                    "id": latest.id,
                    "version_number": latest.version_number,
                    "file_size": latest.file_size,
                    "checksum": latest.checksum,
                    "metadata": latest.metadata,
                    "created_at": latest.created_at,
                    "created_by": latest.created_by.id,
                    "status": latest.status
                }
            else:
                response_data["latest_version"] = None

            return DocumentResponse(**response_data)

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
        document_id: int,
        current_user: User = Depends(get_current_user)
):
    with db_session:
        document = Document.get(id=document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        return {
            "id": document.id,
            "folder_id": document.folder.id,
            "part_number_id": document.part_number_id.id,
            "part_number": document.part_number_id.production_order,
            "doc_type_id": document.doc_type.id,
            "document_name": document.document_name,
            "description": document.description,
            "created_at": document.created_at,
            "created_by": document.created_by.id,
            "is_active": document.is_active,
            "latest_version": {
                "id": document.latest_version.id,
                "version_number": document.latest_version.version_number,
                "file_size": document.latest_version.file_size,
                "checksum": document.latest_version.checksum,
                "metadata": document.latest_version.metadata,
                "created_at": document.latest_version.created_at,
                "created_by": document.latest_version.created_by.id,
                "status": document.latest_version.status
            } if document.latest_version else None,
            "versions": [
                {
                    "id": v.id,
                    "version_number": v.version_number,
                    "file_size": v.file_size,
                    "checksum": v.checksum,
                    "metadata": v.metadata,
                    "created_at": v.created_at,
                    "created_by": v.created_by.id,
                    "status": v.status
                } for v in document.versions
            ]
        }


@router.get("/search/by-partnumber/", response_model=DocumentSearchResponse)
async def search_documents_by_partnumber(
        part_number_query: str,
        skip: int = 0,
        limit: int = 100,
        current_user: User = Depends(get_current_user)
):
    """
    Search documents by partial part number match.

    Args:
        part_number_query (str): Partial part number to search for (minimum 3 characters)
        skip (int): Number of records to skip for pagination
        limit (int): Maximum number of records to return

    Returns:
        DocumentSearchResponse: Matching documents with pagination info

    Raises:
        HTTPException: If part number query is less than 3 characters
    """
    if len(part_number_query) < 3:
        raise HTTPException(
            status_code=400,
            detail="Part number search query must be at least 3 characters long"
        )

    with db_session:
        try:
            # First find matching orders using like operator
            matching_orders = select(o for o in Order
                                     if part_number_query.lower() in o.production_order.lower())

            # Then find documents for these orders
            query = select(d for d in Document
                           if d.is_active and d.part_number_id in matching_orders)

            total = query.count()
            documents = query.order_by(desc(Document.created_at))[skip:skip + limit]

            doc_list = []
            for d in documents:
                latest_ver = d.latest_version
                versions = list(d.versions)

                doc_dict = {
                    "id": d.id,
                    "folder_id": d.folder.id,
                    "part_number_id": d.part_number_id.id,
                    "part_number": d.part_number_id.production_order,
                    "doc_type_id": d.doc_type.id,
                    "document_name": d.document_name,
                    "description": d.description,
                    "created_at": d.created_at,
                    "created_by": d.created_by.id,
                    "is_active": d.is_active,
                    "latest_version": {
                        "id": latest_ver.id,
                        "version_number": latest_ver.version_number,
                        "file_size": latest_ver.file_size,
                        "checksum": latest_ver.checksum,
                        "metadata": latest_ver.metadata,
                        "created_at": latest_ver.created_at,
                        "created_by": latest_ver.created_by.id,
                        "status": latest_ver.status
                    } if latest_ver else None,
                    "versions": [{
                        "id": v.id,
                        "version_number": v.version_number,
                        "file_size": v.file_size,
                        "checksum": v.checksum,
                        "metadata": v.metadata,
                        "created_at": v.created_at,
                        "created_by": v.created_by.id,
                        "status": v.status
                    } for v in versions]
                }
                doc_list.append(doc_dict)

            return DocumentSearchResponse(
                total=total,
                documents=doc_list,
                skip=skip,
                limit=limit
            )

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.get("/analytics/metrics", response_model=DocumentMetrics)
async def get_document_metrics(
        current_user: User = Depends(get_current_user)
):
    """
    Get basic document management metrics
    """
    with db_session:
        try:
            # Current timestamp and 24h ago timestamp
            now = datetime.utcnow()
            last_24h = now - timedelta(days=1)

            # Basic counts
            total_documents = select(d for d in Document if d.is_active).count()
            total_views = select(l for l in DocumentAccessLog if l.action_type == "view").count()
            total_downloads = select(l for l in DocumentAccessLog if l.action_type == "download").count()
            active_folders = select(f for f in DocFolder if f.is_active).count()
            total_versions = select(v for v in DocumentVersion).count()

            # Documents by type
            doc_type_counts = {}
            doc_types = select(dt for dt in DocType)[:]
            for dt in doc_types:
                count = select(d for d in Document if d.doc_type == dt and d.is_active).count()
                doc_type_counts[dt.type_name] = count

            # Calculate storage usage
            total_size_bytes = select(sum(v.file_size) for v in DocumentVersion).first() or 0
            storage_usage_mb = round(total_size_bytes / (1024 * 1024), 2)

            # Recent activity count (last 24h)
            recent_activity = select(l for l in DocumentAccessLog
                                     if l.action_timestamp >= last_24h).count()

            return DocumentMetrics(
                total_documents=total_documents,
                total_views=total_views,
                total_downloads=total_downloads,
                active_folders=active_folders,
                total_versions=total_versions,
                documents_by_type=doc_type_counts,
                storage_usage_mb=storage_usage_mb,
                recent_activity_count=recent_activity
            )

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


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
            object_name = minio_service.generate_object_path(
                str(order.production_order),
                "IPID",
                document.id,
                1
            )

            # Upload to MinIO
            file_object = io.BytesIO(file_contents)
            minio_result = minio_service.upload_file(
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

            # Prepare response data
            response_data = {
                "id": document.id,
                "folder_id": ipid_folder.id,
                "part_number_id": order.id,
                "part_number": order.production_order,
                "doc_type_id": doc_type.id,
                "document_name": document_name,
                "description": description,
                "created_at": document.created_at,
                "created_by": user_id,
                "is_active": True,
                "latest_version": {
                    "id": version.id,
                    "version_number": version_number,
                    "file_size": file_size,
                    "checksum": checksum,
                    "metadata": version.metadata,
                    "created_at": version.created_at,
                    "created_by": user_id,
                    "status": "active"
                },
                "versions": [{
                    "id": version.id,
                    "version_number": version_number,
                    "file_size": file_size,
                    "checksum": checksum,
                    "metadata": version.metadata,
                    "created_at": version.created_at,
                    "created_by": user_id,
                    "status": "active"
                }]
            }

            commit()
            return response_data

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid metadata JSON format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ipid/{production_order}", response_model=List[DocumentResponse])
def get_ipid_documents(
        production_order: str,
        operation_number: Optional[int] = None,
        current_user: User = Depends(get_current_user)
):
    """Get all IPID documents for a production order, optionally filtered by operation number"""
    try:
        with db_session:
            user = User[current_user.id]

            # Get the order
            order = Order.get(production_order=production_order)
            if not order:
                raise HTTPException(status_code=404, detail="Production order not found")

            # Get IPID document type
            doc_type = DocType.get(type_name="IPID")
            if not doc_type:
                return []

            # Build query for IPID documents
            documents = select(d for d in Document
                               if d.is_active and
                               d.part_number_id == order and
                               d.doc_type == doc_type
                               )[:]

            # Filter by operation number if provided
            if operation_number is not None:
                documents = [
                    d for d in documents
                    if d.latest_version and
                       d.latest_version.metadata.get("operation_number") == operation_number
                ]

            # Log access and prepare response
            response_data = []
            for doc in documents:
                DocumentAccessLog(
                    document=doc,
                    version=doc.latest_version,
                    user=user,
                    action_type="view"
                )

                response_data.append({
                    "id": doc.id,
                    "folder_id": doc.folder.id,
                    "part_number_id": order.id,
                    "part_number": order.production_order,
                    "doc_type_id": doc_type.id,
                    "document_name": doc.document_name,
                    "description": doc.description,
                    "created_at": doc.created_at,
                    "created_by": doc.created_by.id,
                    "is_active": doc.is_active,
                    "latest_version": {
                        "id": doc.latest_version.id,
                        "version_number": doc.latest_version.version_number,
                        "file_size": doc.latest_version.file_size,
                        "checksum": doc.latest_version.checksum,
                        "metadata": doc.latest_version.metadata,
                        "created_at": doc.latest_version.created_at,
                        "created_by": doc.latest_version.created_by.id,
                        "status": doc.latest_version.status
                    } if doc.latest_version else None,
                    "versions": [{
                        "id": v.id,
                        "version_number": v.version_number,
                        "file_size": v.file_size,
                        "checksum": v.checksum,
                        "metadata": v.metadata,
                        "created_at": v.created_at,
                        "created_by": v.created_by.id,
                        "status": v.status
                    } for v in doc.versions]
                })

            commit()
            return response_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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

                    # Create response dictionary
                    response_data.append({
                        "id": doc.id,
                        "folder_id": doc.folder.id,
                        "part_number_id": order.id,
                        "part_number": order.production_order,
                        "doc_type_id": doc.doc_type.id,
                        "document_name": doc.document_name,
                        "description": doc.description,
                        "created_at": doc.created_at,
                        "created_by": doc.created_by.id,
                        "is_active": doc.is_active,
                        "latest_version": {
                            "id": doc.latest_version.id,
                            "version_number": doc.latest_version.version_number,
                            "file_size": doc.latest_version.file_size,
                            "checksum": doc.latest_version.checksum,
                            "metadata": doc.latest_version.metadata,
                            "created_at": doc.latest_version.created_at,
                            "created_by": doc.latest_version.created_by.id,
                            "status": doc.latest_version.status
                        } if doc.latest_version else None,
                        "versions": [{
                            "id": v.id,
                            "version_number": v.version_number,
                            "file_size": v.file_size,
                            "checksum": v.checksum,
                            "metadata": v.metadata,
                            "created_at": v.created_at,
                            "created_by": v.created_by.id,
                            "status": v.status
                        } for v in doc.versions]
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
                file_stream = minio_service.get_file(latest_version.minio_object_id)

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