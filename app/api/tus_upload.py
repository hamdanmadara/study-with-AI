"""
TUS Resumable Upload API Endpoints
Handles large file uploads >10MB with chunking and resume capability
"""

from fastapi import APIRouter, HTTPException, Request, Depends, Header
from fastapi.responses import JSONResponse
from typing import Dict, Any, Optional
import uuid
import os
import asyncio
from loguru import logger

from app.services.tus_upload_service import tus_upload_service
from app.services.supabase_document_processor import supabase_document_processor
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/upload/tus", tags=["tus-upload"])

# TUS Protocol Headers
TUS_VERSION = "1.0.0"
TUS_RESUMABLE = "1.0.0"
TUS_EXTENSIONS = "creation,expiration,checksum,termination"
TUS_MAX_SIZE = 1073741824  # 1GB


@router.options("/")
async def tus_options():
    """TUS protocol discovery endpoint"""
    return JSONResponse(
        content={},
        headers={
            "Tus-Resumable": TUS_RESUMABLE,
            "Tus-Version": TUS_VERSION,
            "Tus-Extension": TUS_EXTENSIONS,
            "Tus-Max-Size": str(TUS_MAX_SIZE),
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, HEAD, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Tus-Resumable, Upload-Length, Upload-Metadata, Content-Type, Upload-Offset",
            "Access-Control-Expose-Headers": "Tus-Resumable, Upload-Offset, Location, Upload-Expires"
        }
    )


@router.post("/")
async def create_upload(
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
    upload_length: Optional[str] = Header(None, alias="Upload-Length"),
    upload_metadata: Optional[str] = Header(None, alias="Upload-Metadata"),
    tus_resumable: Optional[str] = Header(None, alias="Tus-Resumable")
):
    """Create a new TUS upload session"""
    try:
        # Validate TUS headers
        if tus_resumable != TUS_RESUMABLE:
            raise HTTPException(status_code=412, detail="Tus-Resumable header required")
        
        if not upload_length:
            raise HTTPException(status_code=400, detail="Upload-Length header required")
        
        file_size = int(upload_length)
        
        # Check file size limits
        if file_size > TUS_MAX_SIZE:
            raise HTTPException(status_code=413, detail=f"File too large. Max size: {TUS_MAX_SIZE} bytes")
        
        if file_size < 10 * 1024 * 1024:  # 10MB
            raise HTTPException(status_code=400, detail="Use regular upload for files <10MB")
        
        # Parse metadata
        filename = "unknown_file"
        if upload_metadata:
            # TUS metadata format: key value,key value (base64 encoded values)
            import base64
            metadata_pairs = upload_metadata.split(',')
            for pair in metadata_pairs:
                if ' ' in pair:
                    key, encoded_value = pair.split(' ', 1)
                    if key == "filename":
                        filename = base64.b64decode(encoded_value).decode('utf-8')
        
        # Generate document ID
        document_id = str(uuid.uuid4())
        user_id = current_user["user_id"]
        
        # Create upload session
        upload_session = await tus_upload_service.create_upload(
            filename=filename,
            file_size=file_size,
            user_id=user_id,
            document_id=document_id
        )
        
        logger.info(f"Created TUS upload session {upload_session['upload_id']} for {filename} ({file_size} bytes)")
        
        return JSONResponse(
            content={
                "message": "Upload session created",
                "upload_id": upload_session["upload_id"],
                "document_id": document_id,
                "chunk_size": upload_session["chunk_size"]
            },
            status_code=201,
            headers={
                "Tus-Resumable": TUS_RESUMABLE,
                "Location": f"/api/upload/tus/{upload_session['upload_id']}",
                "Upload-Expires": upload_session["expires_at"]
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create TUS upload: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create upload: {str(e)}")


@router.head("/{upload_id}")
async def get_upload_status(
    upload_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    tus_resumable: Optional[str] = Header(None, alias="Tus-Resumable")
):
    """Get upload progress (TUS HEAD request)"""
    try:
        if tus_resumable != TUS_RESUMABLE:
            raise HTTPException(status_code=412, detail="Tus-Resumable header required")
        
        status = await tus_upload_service.get_upload_status(upload_id)
        
        return JSONResponse(
            content={},
            headers={
                "Tus-Resumable": TUS_RESUMABLE,
                "Upload-Offset": str(status["bytes_uploaded"]),
                "Upload-Length": str(status["file_size"]),
                "Cache-Control": "no-store"
            }
        )
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to get upload status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get status: {str(e)}")


@router.patch("/{upload_id}")
async def upload_chunk(
    upload_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
    upload_offset: Optional[str] = Header(None, alias="Upload-Offset"),
    tus_resumable: Optional[str] = Header(None, alias="Tus-Resumable"),
    content_type: Optional[str] = Header(None, alias="Content-Type")
):
    """Upload a chunk (TUS PATCH request)"""
    try:
        if tus_resumable != TUS_RESUMABLE:
            raise HTTPException(status_code=412, detail="Tus-Resumable header required")
        
        if content_type != "application/offset+octet-stream":
            raise HTTPException(status_code=400, detail="Content-Type must be application/offset+octet-stream")
        
        if not upload_offset:
            raise HTTPException(status_code=400, detail="Upload-Offset header required")
        
        chunk_offset = int(upload_offset)
        
        # Read chunk data
        chunk_data = await request.body()
        
        if not chunk_data:
            raise HTTPException(status_code=400, detail="No chunk data received")
        
        # Upload chunk
        result = await tus_upload_service.upload_chunk(
            upload_id=upload_id,
            chunk_data=chunk_data,
            chunk_offset=chunk_offset
        )
        
        # If upload is complete, queue for processing
        if result["status"] in ["uploaded", "uploaded_local"]:
            # Get upload metadata for processing
            status = await tus_upload_service.get_upload_status(upload_id)
            
            # Queue document for processing
            try:
                # Extract document_id from upload metadata
                metadata_file = f"{tus_upload_service.metadata_dir}/{upload_id}.json"
                import json
                import aiofiles
                async with aiofiles.open(metadata_file, 'r') as f:
                    metadata = json.loads(await f.read())
                
                # Check if there was an error during upload
                if metadata.get("status") == "failed" or "error" in metadata:
                    logger.error(f"TUS upload {upload_id} failed: {metadata.get('error', 'Unknown error')}")
                    raise Exception(f"Upload failed: {metadata.get('error', 'Unknown error')}")
                
                # All uploads should now be in Supabase
                if "supabase_upload" in metadata:
                    storage_info = metadata["supabase_upload"]
                    storage_type = "supabase"
                    
                    # Handle different upload methods
                    if storage_info.get("storage_method") == "chunked_parts":
                        file_path = storage_info.get("manifest_path")
                    else:
                        file_path = storage_info.get("storage_path")
                    
                    if not file_path:
                        raise Exception("No file path found in upload result")
                else:
                    raise Exception("No Supabase upload result found in metadata")
                
                # Create document record and queue for processing ONLY if upload was successful
                await supabase_document_processor.process_document(
                    file_path=file_path,
                    filename=metadata["filename"],
                    document_id=metadata["document_id"],
                    user_id=metadata["user_id"],
                    storage_info=storage_info,
                    storage_type=storage_type
                )
                
                # Don't cleanup immediately - let frontend check status first
                logger.info(f"TUS upload {upload_id} completed and queued for processing")
                
                # Schedule cleanup after delay (30 seconds)
                async def delayed_cleanup():
                    await asyncio.sleep(30)  # Wait 30 seconds
                    try:
                        await tus_upload_service.cleanup_upload(upload_id)
                        logger.info(f"Delayed cleanup completed for upload {upload_id}")
                    except Exception as e:
                        logger.warning(f"Delayed cleanup failed for upload {upload_id}: {e}")
                
                asyncio.create_task(delayed_cleanup())
                
            except Exception as e:
                logger.error(f"Failed to queue processed TUS upload: {e}")
        
        # Use 204 No Content for TUS protocol compliance (no response body)
        from fastapi import Response
        
        response = Response(
            status_code=204,
            headers={
                "Tus-Resumable": TUS_RESUMABLE,
                "Upload-Offset": str(result["bytes_uploaded"]),
                "Content-Length": "0"
            }
        )
        return response
        
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to upload chunk: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload chunk: {str(e)}")


@router.delete("/{upload_id}")
async def cancel_upload(
    upload_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    tus_resumable: Optional[str] = Header(None, alias="Tus-Resumable")
):
    """Cancel upload session (TUS DELETE request)"""
    try:
        if tus_resumable != TUS_RESUMABLE:
            raise HTTPException(status_code=412, detail="Tus-Resumable header required")
        
        await tus_upload_service.cleanup_upload(upload_id)
        
        return JSONResponse(
            content={"message": "Upload cancelled"},
            status_code=204,
            headers={"Tus-Resumable": TUS_RESUMABLE}
        )
        
    except Exception as e:
        logger.error(f"Failed to cancel upload: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to cancel upload: {str(e)}")


@router.get("/{upload_id}/status")
async def get_detailed_status(
    upload_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Get detailed upload status (non-TUS endpoint for UI)"""
    try:
        status = await tus_upload_service.get_upload_status(upload_id)
        return status
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to get detailed status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get status: {str(e)}")