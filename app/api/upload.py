import os
import aiofiles
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from pathlib import Path

from app.core.config import settings
from app.services.document_processor import document_processor
from app.models.document import ProcessingStatus

router = APIRouter(prefix="/upload", tags=["upload"])

ALLOWED_EXTENSIONS = {
    "pdf": [".pdf"],
    "video": [".mp4", ".avi", ".mov", ".mkv", ".webm"]  # Now enabled!
}

@router.post("/file")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file for processing"""
    try:
        # Validate file
        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename provided")
        
        file_extension = Path(file.filename).suffix.lower()
        
        # Check if extension is allowed
        allowed = False
        for file_type, extensions in ALLOWED_EXTENSIONS.items():
            if file_extension in extensions:
                allowed = True
                break
        
        if not allowed:
            raise HTTPException(
                status_code=400, 
                detail=f"File type {file_extension} not supported. Supported types: PDF, MP4, AVI, MOV, MKV, WEBM"
            )
        
        # Check file size
        if file.size and file.size > settings.max_file_size:
            raise HTTPException(
                status_code=400,
                detail=f"File size {file.size} exceeds maximum allowed size of {settings.max_file_size} bytes"
            )
        
        # Ensure upload directory exists
        os.makedirs(settings.upload_path, exist_ok=True)
        
        # Save file
        file_path = os.path.join(settings.upload_path, file.filename)
        
        # If file exists, add timestamp to make it unique
        if os.path.exists(file_path):
            name_part = Path(file.filename).stem
            extension = Path(file.filename).suffix
            timestamp = str(int(datetime.now().timestamp()))
            file_path = os.path.join(settings.upload_path, f"{name_part}_{timestamp}{extension}")
        
        async with aiofiles.open(file_path, 'wb') as f:
            content = await file.read()
            await f.write(content)
        
        # Process document
        document = await document_processor.process_document(file_path, file.filename)
        
        return JSONResponse(
            status_code=202,
            content={
                "message": "File uploaded successfully and processing started",
                "document_id": document.id,
                "filename": document.filename,
                "file_type": document.file_type.value,
                "status": document.status.value,
                "created_at": document.created_at.isoformat()
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@router.get("/status/{document_id}")
async def get_processing_status(document_id: str):
    """Get processing status of uploaded document"""
    try:
        document = document_processor.get_document_status(document_id)
        
        response_data = {
            "document_id": document.id,
            "filename": document.filename,
            "file_type": document.file_type.value,
            "status": document.status.value,
            "created_at": document.created_at.isoformat()
        }
        
        if document.processed_at:
            response_data["processed_at"] = document.processed_at.isoformat()
        
        if document.chunk_count is not None:
            response_data["chunk_count"] = document.chunk_count
        
        if document.error_message:
            response_data["error_message"] = document.error_message
        
        return response_data
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get status: {str(e)}")

@router.get("/documents")
async def list_documents():
    """List all uploaded documents"""
    try:
        documents = document_processor.get_all_documents()
        
        document_list = []
        for doc_id, document in documents.items():
            doc_data = {
                "document_id": document.id,
                "filename": document.filename,
                "file_type": document.file_type.value,
                "status": document.status.value,
                "created_at": document.created_at.isoformat()
            }
            
            if document.processed_at:
                doc_data["processed_at"] = document.processed_at.isoformat()
            
            if document.chunk_count is not None:
                doc_data["chunk_count"] = document.chunk_count
                
            document_list.append(doc_data)
        
        # Sort by creation time (newest first)
        document_list.sort(key=lambda x: x["created_at"], reverse=True)
        
        return {
            "documents": document_list,
            "total_count": len(document_list)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {str(e)}")

@router.delete("/documents/{document_id}")
async def delete_document(document_id: str):
    """Delete a document and its associated data"""
    try:
        await document_processor.delete_document(document_id)
        
        return {
            "message": f"Document {document_id} deleted successfully"
        }
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {str(e)}")