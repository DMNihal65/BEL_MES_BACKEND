from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from typing import List, Optional, Dict
from pony.orm import db_session, select, flush, commit, rollback, desc
import hashlib
import json
import io

from app.schemas.document_schemas import (
    DocTypeCreate, DocTypeResponse, FolderCreate, FolderResponse,
    DocumentCreate, DocumentResponse, DocumentUpdate, DocumentVersionCreate,
    DocumentVersionResponse, DocumentSearchResponse, UploadDocumentRequest,
    DocumentVersionUpdateRequest
)
from app.models.document_management import DocFolder, DocType, Document, DocumentVersion, DocumentAccessLog
from app.services.minio_service import MinioService
from app.core.security import get_current_user, get_current_admin_user
from app.models.user import User
from app.models.master_order import Order

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
            documents = query[skip:skip+limit]
            
            # Convert Pony entities to dict format
            doc_list = []
            for d in documents:
                latest_ver = d.latest_version
                versions = list(d.versions)
                
                doc_dict = {
                    "id": d.id,
                    "folder_id": d.folder.id,
                    "part_number_id": d.part_number_id.id,
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
            documents = list(query[skip:skip+limit])
            
            doc_list = [{
                "id": d.id,
                "folder_id": d.folder.id,
                "part_number_id": d.part_number_id.id,
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