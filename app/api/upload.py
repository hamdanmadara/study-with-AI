import os
import aiofiles
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from pathlib import Path

from app.core.config import settings
from app.services.document_processor import document_processor
from app.services.r2_storage import r2_storage_service
from app.models.document import ProcessingStatus

router = APIRouter(prefix="/upload", tags=["upload"])

ALLOWED_EXTENSIONS = {
    "pdf": [".pdf"],
    "video": [".mp4", ".avi", ".mov", ".mkv", ".webm"],
    "audio": [".mp3", ".wav", ".m4a", ".aac", ".flac"]
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
                detail=f"File type {file_extension} not supported. Supported types: PDF, MP4, AVI, MOV, MKV, WEBM, MP3, WAV, M4A, AAC, FLAC"
            )
        
        # Check file size
        if file.size and file.size > settings.max_file_size:
            raise HTTPException(
                status_code=400,
                detail=f"File size {file.size} exceeds maximum allowed size of {settings.max_file_size} bytes"
            )
        
        # Read file content
        content = await file.read()
        
        if not content:
            raise HTTPException(status_code=400, detail="File is empty")
        
        # Generate unique document ID first
        import uuid
        document_id = str(uuid.uuid4())
        
        # Determine storage method
        if settings.use_r2_storage:
            # Upload to R2 storage
            try:
                r2_info = await r2_storage_service.upload_file(content, file.filename, document_id)
                file_path = r2_info['object_key']  # Use R2 object key as file_path
                storage_info = r2_info
                storage_type = "r2"
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to upload to R2: {str(e)}")
        else:
            # Fallback to local storage
            os.makedirs(settings.upload_path, exist_ok=True)
            file_path = os.path.join(settings.upload_path, file.filename)
            
            # If file exists, add timestamp to make it unique
            if os.path.exists(file_path):
                name_part = Path(file.filename).stem
                extension = Path(file.filename).suffix
                timestamp = str(int(datetime.now().timestamp()))
                file_path = os.path.join(settings.upload_path, f"{name_part}_{timestamp}{extension}")
            
            async with aiofiles.open(file_path, 'wb') as f:
                await f.write(content)
            
            storage_info = {"file_path": file_path, "file_size": len(content)}
            storage_type = "local"
        
        # Process document (now queues it)
        document = await document_processor.process_document(
            file_path, 
            file.filename, 
            document_id=document_id,
            storage_info=storage_info,
            storage_type=storage_type
        )
        
        # Get queue information
        queue_status = document_processor.get_queue_status()
        estimated_wait = document_processor.get_estimated_wait_time(file.filename)
        
        # Determine appropriate queue size based on file type
        file_extension = Path(file.filename).suffix.lower()
        is_media_file = file_extension in ['.mp4', '.avi', '.mov', '.mkv', '.mp3', '.wav', '.flac', '.aac', '.m4a']
        position_in_queue = queue_status["media_queue_size"] if is_media_file else queue_status["pdf_queue_size"]
        
        response_data = {
            "message": "File uploaded successfully and queued for processing",
            "document_id": document.id,
            "filename": document.filename,
            "file_type": document.file_type.value,
            "status": document.status.value,
            "created_at": document.created_at.isoformat(),
            "storage_type": document.storage_type,
            "file_size": document.file_size,
            "queue_info": {
                "position_in_queue": position_in_queue,
                "total_queue_size": queue_status["total_queue_size"],
                "pdf_queue_size": queue_status["pdf_queue_size"],
                "media_queue_size": queue_status["media_queue_size"],
                "active_workers": queue_status["active_workers"],
                "estimated_wait_minutes": estimated_wait,
                "processing_message": "Your file is in the processing queue. You can refresh this page in a few minutes to check progress."
            }
        }
        
        # Add specific messages based on file type and queue status
        if document.file_type.value in ["video", "audio"]:
            if estimated_wait and estimated_wait > 0:
                response_data["queue_info"]["processing_message"] = f"Your {document.file_type.value} file is queued for processing. Audio/video files are processed ONE AT A TIME to prevent conflicts. Estimated wait time: {estimated_wait} minutes. You can refresh this page to check progress."
            else:
                response_data["queue_info"]["processing_message"] = f"Your {document.file_type.value} file is being processed now. Audio/video files are processed exclusively to ensure quality. This may take several minutes depending on file length. You can refresh this page to check progress."
        else:
            if estimated_wait and estimated_wait > 0:
                response_data["queue_info"]["processing_message"] = f"Your PDF file is queued for processing. Estimated wait time: {estimated_wait} minutes. You can refresh this page to check progress."
            else:
                response_data["queue_info"]["processing_message"] = "Your PDF file is being processed now. You can refresh this page in a few minutes to check progress."
        
        return JSONResponse(
            status_code=202,
            content=response_data
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
            "created_at": document.created_at.isoformat(),
            "storage_type": document.storage_type,
            "file_size": document.file_size
        }
        
        if document.processed_at:
            response_data["processed_at"] = document.processed_at.isoformat()
        
        if document.chunk_count is not None:
            response_data["chunk_count"] = document.chunk_count
        
        if document.error_message:
            response_data["error_message"] = document.error_message
        
        # Add progress information for video/audio processing
        if document.file_type.value in ["video", "audio"] and document.status.value == "processing":
        
            progress_data = {
                "progress": {
                    "total_duration_seconds": document.total_duration,
                    "processed_duration_seconds": document.processed_duration,
                    "total_segments": document.total_segments,
                    "processed_segments": document.processed_segments,
                    "current_segment": document.current_segment
                }
            }
            
            # Add formatted duration strings
            if document.total_duration:
                progress_data["progress"]["total_duration_formatted"] = f"{document.total_duration/60:.1f} minutes"
            
            if document.processed_duration:
                progress_data["progress"]["processed_duration_formatted"] = f"{document.processed_duration/60:.1f} minutes"
            
            # Add percentage complete
            if document.total_segments and document.processed_segments is not None:
                percentage = (document.processed_segments / document.total_segments) * 100
                progress_data["progress"]["percentage_complete"] = round(percentage, 1)
            
            # Add estimated completion time
            if document.estimated_completion:
                progress_data["progress"]["estimated_completion"] = document.estimated_completion.isoformat()
                
                # Calculate estimated remaining time
                remaining_time = (document.estimated_completion - datetime.now()).total_seconds()
                if remaining_time > 0:
                    progress_data["progress"]["estimated_remaining_minutes"] = round(remaining_time / 60, 1)
                else:
                    progress_data["progress"]["estimated_remaining_minutes"] = 0
            
            # Add processing started time
            if document.processing_started_at:
                progress_data["progress"]["processing_started_at"] = document.processing_started_at.isoformat()
                elapsed_seconds = (datetime.now() - document.processing_started_at).total_seconds()
                progress_data["progress"]["elapsed_time_minutes"] = round(elapsed_seconds / 60, 1)
            
            response_data.update(progress_data)
        
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
                "created_at": document.created_at.isoformat(),
                "storage_type": document.storage_type,
                "file_size": document.file_size
            }
            
            if document.processed_at:
                doc_data["processed_at"] = document.processed_at.isoformat()
            
            if document.chunk_count is not None:
                doc_data["chunk_count"] = document.chunk_count
            
            # Add progress information for video/audio processing
            if document.file_type.value in ["video", "audio"] and document.status.value == "processing":
                progress_data = {
                    "progress": {
                        "total_duration_seconds": document.total_duration,
                        "processed_duration_seconds": document.processed_duration,
                        "total_segments": document.total_segments,
                        "processed_segments": document.processed_segments,
                        "current_segment": document.current_segment
                    }
                }
                
                # Add formatted duration strings
                if document.total_duration:
                    progress_data["progress"]["total_duration_formatted"] = f"{document.total_duration/60:.1f} minutes"
                
                if document.processed_duration:
                    progress_data["progress"]["processed_duration_formatted"] = f"{document.processed_duration/60:.1f} minutes"
                
                # Add percentage complete
                if document.total_segments and document.processed_segments is not None:
                    percentage = (document.processed_segments / document.total_segments) * 100
                    progress_data["progress"]["percentage_complete"] = round(percentage, 1)
                
                # Add estimated completion time
                if document.estimated_completion:
                    progress_data["progress"]["estimated_completion"] = document.estimated_completion.isoformat()
                    
                    # Calculate estimated remaining time
                    remaining_time = (document.estimated_completion - datetime.now()).total_seconds()
                    if remaining_time > 0:
                        progress_data["progress"]["estimated_remaining_minutes"] = round(remaining_time / 60, 1)
                    else:
                        progress_data["progress"]["estimated_remaining_minutes"] = 0
                
                # Add processing started time
                if document.processing_started_at:
                    progress_data["progress"]["processing_started_at"] = document.processing_started_at.isoformat()
                    elapsed_seconds = (datetime.now() - document.processing_started_at).total_seconds()
                    progress_data["progress"]["elapsed_time_minutes"] = round(elapsed_seconds / 60, 1)
                
                doc_data.update(progress_data)
                
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

@router.get("/queue/status")
async def get_queue_status():
    """Get current processing queue status"""
    try:
        queue_status = document_processor.get_queue_status()
        estimated_wait = document_processor.get_estimated_wait_time()
        
        return {
            "queue_status": queue_status,
            "estimated_wait_minutes": estimated_wait,
            "message": "Queue status retrieved successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get queue status: {str(e)}")

@router.get("/queue/wait-time")
async def get_estimated_wait_time():
    """Get estimated wait time for new uploads"""
    try:
        estimated_wait = document_processor.get_estimated_wait_time()
        queue_status = document_processor.get_queue_status()
        
        if estimated_wait is None:
            message = "Queue system is starting up"
        elif estimated_wait == 0:
            message = "Your file will be processed immediately"
        else:
            message = f"Estimated wait time: {estimated_wait} minutes"
        
        return {
            "estimated_wait_minutes": estimated_wait,
            "total_queue_size": queue_status["total_queue_size"],
            "pdf_queue_size": queue_status["pdf_queue_size"],
            "media_queue_size": queue_status["media_queue_size"],
            "active_workers": queue_status["active_workers"],
            "message": message
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get wait time: {str(e)}")