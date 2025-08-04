import os
import aiofiles
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import JSONResponse
from pathlib import Path
from loguru import logger

from app.core.config import settings
from app.services.supabase_document_processor import supabase_document_processor
# R2 storage removed - using only Supabase storage
from app.services.supabase_service import supabase_service
from app.services.auth_service import get_current_user
from app.models.document import ProcessingStatus
from typing import Dict, Any
import asyncio
import tempfile

router = APIRouter(prefix="/upload", tags=["upload"])


async def handle_large_file_upload(file: UploadFile, current_user: Dict[str, Any]) -> JSONResponse:
    """Handle large file uploads with chunked processing"""
    try:
        # Generate unique document ID first
        import uuid
        document_id = str(uuid.uuid4())
        user_id = current_user["user_id"]
        
        logger.info(f"Processing large file {file.filename} ({file.size} bytes) in chunks")
        
        # Create temporary file to store chunks
        temp_dir = tempfile.gettempdir()
        temp_filename = f"large_upload_{document_id}_{file.filename}"
        # Sanitize temp filename
        import re
        temp_filename = re.sub(r'[^\w\-_\.]', '_', temp_filename)
        temp_file_path = os.path.join(temp_dir, temp_filename)
        
        # Read and write file in chunks to avoid memory issues
        chunk_size = 5 * 1024 * 1024  # 5MB chunks
        total_bytes = 0
        
        logger.info(f"Writing large file to temporary location: {temp_file_path}")
        
        async with aiofiles.open(temp_file_path, 'wb') as temp_file:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                await temp_file.write(chunk)
                total_bytes += len(chunk)
                
                # Log progress for very large files
                if total_bytes % (50 * 1024 * 1024) == 0:  # Every 50MB
                    logger.info(f"Progress: {total_bytes / (1024*1024):.1f}MB written")
        
        logger.info(f"Large file written to disk: {total_bytes} bytes")
        
        # Read the complete file and upload to Supabase
        async with aiofiles.open(temp_file_path, 'rb') as temp_file:
            file_content = await temp_file.read()
        
        logger.info(f"Large file read into memory: {len(file_content)} bytes")
        
        # Upload to Supabase with extended timeout and validation
        upload_successful = False
        try:
            logger.info(f"Starting upload validation for {file.filename} ({len(file_content)} bytes)")
            
            supabase_info = await supabase_service.upload_file(
                file_content=file_content,
                filename=file.filename,
                user_id=user_id,
                document_id=document_id
            )
            
            # Validate upload by checking if file exists and is accessible
            if supabase_info and supabase_info.get('storage_path'):
                try:
                    # Test download a small portion to verify upload
                    test_download = await supabase_service.download_file(supabase_info['storage_path'])
                    if test_download and len(test_download) > 0:
                        upload_successful = True
                        storage_info = supabase_info
                        storage_type = "supabase"
                        file_path = supabase_info['storage_path']
                        logger.info(f"Large file successfully uploaded and validated: {file_path}")
                    else:
                        raise Exception("Upload validation failed - file not accessible")
                except Exception as validation_error:
                    logger.error(f"Upload validation failed: {validation_error}")
                    raise Exception(f"Upload validation failed: {validation_error}")
            else:
                raise Exception("Upload returned empty response or missing storage path")
            
        except Exception as upload_error:
            logger.error(f"Supabase upload failed for large file: {upload_error}")
            upload_successful = False
            
            # Only use local fallback if explicitly allowed
            # For large files, we should fail rather than create database pollution
            logger.error(f"Upload failed for {file.filename} - not proceeding with processing to prevent database pollution")
            
            # Clean up temporary file
            if os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                    logger.info(f"Cleaned up failed upload temp file: {temp_file_path}")
                except Exception as cleanup_error:
                    logger.warning(f"Failed to cleanup temp file: {cleanup_error}")
            
            # Return error instead of proceeding
            raise HTTPException(
                status_code=500, 
                detail=f"Upload failed: {str(upload_error)}. File was not processed to prevent database pollution."
            )
        
        # Clean up memory
        del file_content
        
        # Only proceed if upload was successful (already validated above via exception)
        # This point is only reached if upload_successful = True
        
        # Process document (queue it)
        document = await supabase_document_processor.process_document(
            file_path, 
            file.filename, 
            document_id=document_id,
            user_id=user_id,
            storage_info=storage_info,
            storage_type=storage_type
        )
        
        # Clean up temporary file if uploaded to Supabase successfully
        if storage_type == "supabase" and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.info(f"Cleaned up temporary file: {temp_file_path}")
            except Exception as cleanup_error:
                logger.warning(f"Failed to cleanup temp file: {cleanup_error}")
        
        # Get queue information
        queue_status = supabase_document_processor.get_queue_status()
        estimated_wait = supabase_document_processor.get_estimated_wait_time(file.filename)
        
        # Determine appropriate queue size based on file type
        file_extension = Path(file.filename).suffix.lower()
        is_media_file = file_extension in ['.mp4', '.avi', '.mov', '.mkv', '.mp3', '.wav', '.flac', '.aac', '.m4a']
        position_in_queue = queue_status["media_queue_size"] if is_media_file else queue_status["pdf_queue_size"]
        
        response_data = {
            "message": "Large file uploaded successfully and queued for processing",
            "document_id": document["id"],
            "filename": document["filename"],
            "file_type": document["file_type"],
            "status": document["status"],
            "created_at": document["created_at"],
            "storage_type": storage_type,
            "file_size": document.get("file_size", 0),
            "large_file_processed": True,
            "queue_info": {
                "position_in_queue": position_in_queue,
                "total_queue_size": queue_status["total_queue_size"],
                "pdf_queue_size": queue_status["pdf_queue_size"],
                "media_queue_size": queue_status["media_queue_size"],
                "active_workers": queue_status["active_workers"],
                "estimated_wait_minutes": estimated_wait,
                "processing_message": f"Your large {document['file_type']} file has been uploaded and is in the processing queue."
            }
        }
        
        logger.info(f"Large file upload completed: {document_id}")
        
        return JSONResponse(
            status_code=202,
            content=response_data
        )
        
    except Exception as e:
        # Clean up temp file on error
        if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except:
                pass
        
        logger.error(f"Large file upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Large file upload failed: {str(e)}")

ALLOWED_EXTENSIONS = {
    "pdf": [".pdf"],
    "video": [".mp4", ".avi", ".mov", ".mkv", ".webm"],
    "audio": [".mp3", ".wav", ".m4a", ".aac", ".flac"]
}

@router.post("/file")
async def upload_file(
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Upload a file for processing (requires authentication)"""
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
        
        # Check file size and route accordingly
        file_size_limit_small = 10 * 1024 * 1024  # 10MB
        supabase_limit = 50 * 1024 * 1024  # 50MB - Supabase direct upload limit
        
        if file.size and file.size > settings.max_file_size:
            raise HTTPException(
                status_code=400,
                detail=f"File size {file.size} exceeds maximum allowed size of {settings.max_file_size} bytes"
            )
        
        # Route large files to TUS protocol for files >= 50MB
        if file.size and file.size >= supabase_limit:
            logger.info(f"Very large file detected ({file.size} bytes), should use TUS protocol for {file.filename}")
            raise HTTPException(
                status_code=413,
                detail=f"File size {file.size / (1024*1024):.1f}MB exceeds Supabase direct upload limit. Please use TUS resumable upload for files over 50MB. Use the TUS upload endpoint: /api/upload/tus/"
            )
        
        # Handle medium-large files (10-50MB) with chunked processing
        if file.size and file.size >= file_size_limit_small:
            logger.info(f"Large file detected ({file.size} bytes), using enhanced upload processing for {file.filename}")
            return await handle_large_file_upload(file, current_user)
        
        # Read file content in chunks to avoid blocking
        content = b""
        chunk_size = 1024 * 1024  # 1MB chunks
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            content += chunk
        
        if not content:
            raise HTTPException(status_code=400, detail="File is empty")
        
        # Generate unique document ID first
        import uuid
        document_id = str(uuid.uuid4())
        user_id = current_user["user_id"]
        
        # Use Supabase storage with validation
        upload_successful = False
        try:
            logger.info(f"Starting upload validation for {file.filename} ({len(content)} bytes)")
            
            supabase_info = await supabase_service.upload_file(
                file_content=content,
                filename=file.filename,
                user_id=user_id,
                document_id=document_id
            )
            
            # Validate upload by checking if file exists and is accessible
            if supabase_info and supabase_info.get('storage_path'):
                try:
                    # Test download a small portion to verify upload
                    test_download = await supabase_service.download_file(supabase_info['storage_path'])
                    if test_download and len(test_download) > 0:
                        upload_successful = True
                        file_path = supabase_info['storage_path']
                        storage_info = supabase_info
                        storage_type = "supabase"
                        logger.info(f"File successfully uploaded and validated: {file_path}")
                    else:
                        raise Exception("Upload validation failed - file not accessible")
                except Exception as validation_error:
                    logger.error(f"Upload validation failed: {validation_error}")
                    raise Exception(f"Upload validation failed: {validation_error}")
            else:
                raise Exception("Upload returned empty response or missing storage path")
                
        except Exception as e:
            logger.error(f"Supabase upload failed: {e}")
            upload_successful = False
            
            # Fallback to local storage if Supabase fails
            logger.warning(f"Supabase upload failed, falling back to local storage: {e}")
            try:
                os.makedirs(settings.upload_path, exist_ok=True)
                
                # Sanitize filename for local storage too
                import re
                sanitized_filename = re.sub(r'[–—]', '-', file.filename)
                sanitized_filename = re.sub(r'\s+', '_', sanitized_filename)
                sanitized_filename = re.sub(r'[^\w\-_\.]', '', sanitized_filename)
                
                file_path = os.path.join(settings.upload_path, sanitized_filename)
                
                # If file exists, add timestamp to make it unique
                if os.path.exists(file_path):
                    name_part = Path(sanitized_filename).stem
                    extension = Path(sanitized_filename).suffix
                    timestamp = str(int(datetime.now().timestamp()))
                    file_path = os.path.join(settings.upload_path, f"{name_part}_{timestamp}{extension}")
                
                async with aiofiles.open(file_path, 'wb') as f:
                    await f.write(content)
                
                # Validate local file was written successfully
                if os.path.exists(file_path) and os.path.getsize(file_path) == len(content):
                    upload_successful = True
                    storage_info = {"storage_path": file_path, "file_size": len(content)}
                    storage_type = "local"
                    logger.info(f"File successfully saved locally and validated: {file_path}")
                else:
                    raise Exception("Local file validation failed")
                    
            except Exception as local_error:
                logger.error(f"Both Supabase and local storage failed: {local_error}")
                raise HTTPException(
                    status_code=500, 
                    detail=f"Upload failed to both Supabase and local storage: {str(local_error)}. File was not processed to prevent database pollution."
                )
        
        # Only process document if upload was successful
        if not upload_successful:
            raise HTTPException(
                status_code=500, 
                detail="Upload validation failed. File was not processed to prevent database pollution."
            )
        
        # Process document (now queues it)
        document = await supabase_document_processor.process_document(
            file_path, 
            file.filename, 
            document_id=document_id,
            user_id=user_id,
            storage_info=storage_info,
            storage_type=storage_type
        )
        
        # Get queue information
        queue_status = supabase_document_processor.get_queue_status()
        estimated_wait = supabase_document_processor.get_estimated_wait_time(file.filename)
        
        # Determine appropriate queue size based on file type
        file_extension = Path(file.filename).suffix.lower()
        is_media_file = file_extension in ['.mp4', '.avi', '.mov', '.mkv', '.mp3', '.wav', '.flac', '.aac', '.m4a']
        position_in_queue = queue_status["media_queue_size"] if is_media_file else queue_status["pdf_queue_size"]
        
        response_data = {
            "message": "File uploaded successfully and queued for processing",
            "document_id": document["id"],
            "filename": document["filename"],
            "file_type": document["file_type"],
            "status": document["status"],
            "created_at": document["created_at"],
            "storage_type": storage_type,
            "file_size": document.get("file_size", 0),
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
        if document["file_type"] in ["video", "audio"]:
            if estimated_wait and estimated_wait > 0:
                response_data["queue_info"]["processing_message"] = f"Your {document['file_type']} file is queued for processing. Audio/video files are processed ONE AT A TIME to prevent conflicts. Estimated wait time: {estimated_wait} minutes. You can refresh this page to check progress."
            else:
                response_data["queue_info"]["processing_message"] = f"Your {document['file_type']} file is being processed now. Audio/video files are processed exclusively to ensure quality. This may take several minutes depending on file length. You can refresh this page to check progress."
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
async def get_processing_status(
    document_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Get processing status of uploaded document"""
    try:
        document = await supabase_document_processor.get_document_status(document_id, current_user["user_id"])
        
        response_data = {
            "document_id": document["id"],
            "filename": document["filename"],
            "file_type": document["file_type"],
            "status": document["status"],
            "created_at": document["created_at"],
            "storage_type": "supabase",
            "file_size": document.get("file_size", 0)
        }
        
        if document.get("processed_at"):
            response_data["processed_at"] = document["processed_at"]
        
        if document.get("chunk_count") is not None:
            response_data["chunk_count"] = document["chunk_count"]
        
        if document.get("error_message"):
            response_data["error_message"] = document["error_message"]
        
        # Add progress information for video/audio processing
        if document["file_type"] in ["video", "audio"] and document["status"] == "processing":
        
            progress_data = {
                "progress": {
                    "total_duration_seconds": document.get("total_duration"),
                    "processed_duration_seconds": document.get("processed_duration"),
                    "total_segments": document.get("total_segments"),
                    "processed_segments": document.get("processed_segments"),
                    "current_segment": document.get("current_segment")
                }
            }
            
            # Add formatted duration strings
            if document.get("total_duration"):
                progress_data["progress"]["total_duration_formatted"] = f"{document['total_duration']/60:.1f} minutes"
            
            if document.get("processed_duration"):
                progress_data["progress"]["processed_duration_formatted"] = f"{document['processed_duration']/60:.1f} minutes"
            
            # Add percentage complete
            if document.get("total_segments") and document.get("processed_segments") is not None:
                percentage = (document["processed_segments"] / document["total_segments"]) * 100
                progress_data["progress"]["percentage_complete"] = round(percentage, 1)
            
            # Add estimated completion time
            if document.get("estimated_completion"):
                progress_data["progress"]["estimated_completion"] = document["estimated_completion"]
                
                # Calculate estimated remaining time (skip for now as datetime parsing is complex)
                # remaining_time = (document.estimated_completion - datetime.now()).total_seconds()
                # if remaining_time > 0:
                #     progress_data["progress"]["estimated_remaining_minutes"] = round(remaining_time / 60, 1)
                # else:
                #     progress_data["progress"]["estimated_remaining_minutes"] = 0
            
            # Add processing started time
            if document.get("processing_started_at"):
                progress_data["progress"]["processing_started_at"] = document["processing_started_at"]
                # Skip elapsed time calculation for now
                # elapsed_seconds = (datetime.now() - document.processing_started_at).total_seconds()
                # progress_data["progress"]["elapsed_time_minutes"] = round(elapsed_seconds / 60, 1)
            
            response_data.update(progress_data)
        
        return response_data
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get status: {str(e)}")

@router.get("/documents")
async def list_documents(
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """List all uploaded documents"""
    try:
        documents = await supabase_document_processor.get_user_documents(current_user["user_id"])
        
        document_list = []
        for doc_id, document in documents.items():
            doc_data = {
                "document_id": document["id"],
                "filename": document["filename"],
                "file_type": document["file_type"],
                "status": document["status"],
                "created_at": document["created_at"],
                "storage_type": "supabase",
                "file_size": document.get("file_size", 0)
            }
            
            if document.get("processed_at"):
                doc_data["processed_at"] = document["processed_at"]
            
            if document.get("chunk_count") is not None:
                doc_data["chunk_count"] = document["chunk_count"]
            
            # Add progress information for video/audio processing
            if document["file_type"] in ["video", "audio"] and document["status"] == "processing":
                progress_data = {
                    "progress": {
                        "total_duration_seconds": document.get("total_duration"),
                        "processed_duration_seconds": document.get("processed_duration"),
                        "total_segments": document.get("total_segments"),
                        "processed_segments": document.get("processed_segments"),
                        "current_segment": document.get("current_segment")
                    }
                }
                
                # Add formatted duration strings
                if document.get("total_duration"):
                    progress_data["progress"]["total_duration_formatted"] = f"{document['total_duration']/60:.1f} minutes"
                
                if document.get("processed_duration"):
                    progress_data["progress"]["processed_duration_formatted"] = f"{document['processed_duration']/60:.1f} minutes"
                
                # Add percentage complete
                if document.get("total_segments") and document.get("processed_segments") is not None:
                    percentage = (document["processed_segments"] / document["total_segments"]) * 100
                    progress_data["progress"]["percentage_complete"] = round(percentage, 1)
                
                # Add estimated completion time
                if document.get("estimated_completion"):
                    progress_data["progress"]["estimated_completion"] = document["estimated_completion"]
                    
                    # Calculate estimated remaining time
                    remaining_time = (document.estimated_completion - datetime.now()).total_seconds()
                    if remaining_time > 0:
                        progress_data["progress"]["estimated_remaining_minutes"] = round(remaining_time / 60, 1)
                    else:
                        progress_data["progress"]["estimated_remaining_minutes"] = 0
                
                # Add processing started time
                if document.get("processing_started_at"):
                    progress_data["progress"]["processing_started_at"] = document["processing_started_at"]
                    # Skip elapsed time calculation for now
                
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
async def delete_document(
    document_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Delete a document and its associated data"""
    try:
        await supabase_document_processor.delete_document(document_id, current_user["user_id"])
        
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
        queue_status = supabase_document_processor.get_queue_status()
        estimated_wait = supabase_document_processor.get_estimated_wait_time()
        
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
        estimated_wait = supabase_document_processor.get_estimated_wait_time()
        queue_status = supabase_document_processor.get_queue_status()
        
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

@router.get("/view/{document_id}")
async def get_document_view_url(
    document_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Get a signed URL to view the document content"""
    try:
        # Get document info
        document = await supabase_document_processor.get_document_status(document_id, current_user["user_id"])
        
        if document["status"] != 'completed':
            raise HTTPException(
                status_code=400, 
                detail=f"Document is not ready for viewing. Status: {document['status']}"
            )
        
        # Generate signed URL from Supabase storage
        try:
            signed_url = await supabase_service.get_signed_url(
                document["storage_path"],
                expiration=3600  # 1 hour expiration
            )
            
            response_data = {
                "document_id": document["id"],
                "filename": document["filename"],
                "file_type": document["file_type"],
                "view_url": signed_url,
                "content_type": _get_content_type_from_filename(document["filename"]),
                "expires_in": 3600,
                "storage_type": "supabase"
            }
            
            return response_data
            
        except Exception as e:
            raise HTTPException(
                status_code=500, 
                detail=f"Failed to generate view URL: {str(e)}"
            )
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get view URL: {str(e)}")

def _get_content_type_from_filename(filename: str) -> str:
    """Get content type based on file extension"""
    extension = Path(filename).suffix.lower()
    content_types = {
        '.pdf': 'application/pdf',
        '.mp4': 'video/mp4',
        '.avi': 'video/x-msvideo',
        '.mov': 'video/quicktime',
        '.mkv': 'video/x-matroska',
        '.webm': 'video/webm',
        '.mp3': 'audio/mpeg',
        '.wav': 'audio/wav',
        '.m4a': 'audio/mp4',
        '.aac': 'audio/aac',
        '.flac': 'audio/flac'
    }
    return content_types.get(extension, 'application/octet-stream')