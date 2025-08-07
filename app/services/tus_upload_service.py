"""
TUS Resumable Upload Service for Large Files
Handles files >10MB with chunked, resumable uploads
"""

import os
import asyncio
import hashlib
import tempfile
import json
from typing import Dict, Any, Optional, AsyncGenerator
from pathlib import Path
from loguru import logger
import aiofiles
from datetime import datetime, timedelta

from app.core.config import settings
from app.services.supabase_service import supabase_service


class TusUploadService:
    def __init__(self):
        self.chunk_size = 5 * 1024 * 1024  # 5MB chunks
        self.upload_dir = os.path.join(tempfile.gettempdir(), "tus_uploads")
        self.metadata_dir = os.path.join(tempfile.gettempdir(), "tus_metadata")
        
        # Ensure directories exist
        os.makedirs(self.upload_dir, exist_ok=True)
        os.makedirs(self.metadata_dir, exist_ok=True)
    
    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for safe storage"""
        import re
        sanitized = filename
        sanitized = re.sub(r'[–—]', '-', sanitized)
        sanitized = re.sub(r'\s+', '_', sanitized)
        sanitized = re.sub(r'[^\w\-_\.]', '', sanitized)
        sanitized = sanitized.strip('.-_')
        
        if len(sanitized) < 3:
            sanitized = f"file_{sanitized}"
        
        return sanitized
    
    async def create_upload(self, filename: str, file_size: int, user_id: str, document_id: str) -> Dict[str, Any]:
        """Create a new TUS upload session"""
        try:
            sanitized_filename = self._sanitize_filename(filename)
            upload_id = f"{document_id}_{user_id}_{int(datetime.now().timestamp())}"
            
            # Create metadata
            metadata = {
                "upload_id": upload_id,
                "filename": filename,
                "sanitized_filename": sanitized_filename,
                "file_size": file_size,
                "user_id": user_id,
                "document_id": document_id,
                "created_at": datetime.now().isoformat(),
                "chunks_uploaded": 0,
                "bytes_uploaded": 0,
                "status": "created",
                "chunks": []
            }
            
            # Save metadata
            metadata_file = os.path.join(self.metadata_dir, f"{upload_id}.json")
            async with aiofiles.open(metadata_file, 'w') as f:
                await f.write(json.dumps(metadata, indent=2))
            
            logger.info(f"Created TUS upload session: {upload_id} for file {filename} ({file_size} bytes)")
            
            return {
                "upload_id": upload_id,
                "upload_url": f"/api/upload/tus/{upload_id}",
                "chunk_size": self.chunk_size,
                "file_size": file_size,
                "expires_at": (datetime.now() + timedelta(hours=24)).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to create TUS upload: {e}")
            raise ValueError(f"Failed to create upload session: {str(e)}")
    
    async def get_upload_status(self, upload_id: str) -> Dict[str, Any]:
        """Get current upload status"""
        try:
            metadata_file = os.path.join(self.metadata_dir, f"{upload_id}.json")
            
            if not os.path.exists(metadata_file):
                raise ValueError("Upload session not found")
            
            async with aiofiles.open(metadata_file, 'r') as f:
                metadata = json.loads(await f.read())
            
            progress_percentage = (metadata["bytes_uploaded"] / metadata["file_size"]) * 100 if metadata["file_size"] > 0 else 0
            
            return {
                "upload_id": upload_id,
                "filename": metadata["filename"],
                "file_size": metadata["file_size"],
                "bytes_uploaded": metadata["bytes_uploaded"],
                "chunks_uploaded": metadata["chunks_uploaded"],
                "progress_percentage": round(progress_percentage, 2),
                "status": metadata["status"],
                "created_at": metadata["created_at"]
            }
            
        except Exception as e:
            logger.error(f"Failed to get upload status: {e}")
            raise ValueError(f"Failed to get upload status: {str(e)}")
    
    async def upload_chunk(self, upload_id: str, chunk_data: bytes, chunk_offset: int) -> Dict[str, Any]:
        """Upload a single chunk"""
        try:
            metadata_file = os.path.join(self.metadata_dir, f"{upload_id}.json")
            
            if not os.path.exists(metadata_file):
                raise ValueError("Upload session not found")
            
            # Load metadata
            async with aiofiles.open(metadata_file, 'r') as f:
                metadata = json.loads(await f.read())
            
            # Create chunk file
            chunk_file = os.path.join(self.upload_dir, f"{upload_id}_chunk_{chunk_offset}")
            
            # Write chunk to disk
            async with aiofiles.open(chunk_file, 'wb') as f:
                await f.write(chunk_data)
            
            # Update metadata
            chunk_info = {
                "offset": chunk_offset,
                "size": len(chunk_data),
                "uploaded_at": datetime.now().isoformat(),
                "chunk_file": chunk_file
            }
            
            metadata["chunks"].append(chunk_info)
            metadata["chunks_uploaded"] += 1
            metadata["bytes_uploaded"] += len(chunk_data)
            
            # Check if upload is complete
            if metadata["bytes_uploaded"] >= metadata["file_size"]:
                metadata["status"] = "assembling"
                
                file_size_mb = metadata["file_size"] / (1024 * 1024)
                logger.info(f"Upload complete, starting Supabase upload ({file_size_mb:.1f}MB)")
                
                try:
                    supabase_limit_mb = settings.supabase_max_file_size / (1024 * 1024)
                    
                    if file_size_mb > supabase_limit_mb:
                        # For files larger than Supabase limit, use chunked storage approach
                        upload_result = await self._upload_large_file_chunked(upload_id, metadata)
                    elif file_size_mb > 25:
                        # For large files within Supabase limits, use streaming upload  
                        final_file = await self._assemble_chunks(upload_id, metadata)
                        metadata["final_file"] = final_file
                        upload_result = await self._upload_large_to_supabase_direct(final_file, metadata)
                        # Clean up temp file
                        if os.path.exists(final_file):
                            os.remove(final_file)
                    else:
                        # For smaller files, use regular upload
                        final_file = await self._assemble_chunks(upload_id, metadata)
                        metadata["final_file"] = final_file
                        upload_result = await self._upload_to_supabase(upload_id, metadata)
                        # Clean up temp file
                        if os.path.exists(final_file):
                            os.remove(final_file)
                    
                    metadata["supabase_upload"] = upload_result
                    metadata["status"] = "uploaded"
                    
                    logger.info(f"File uploaded successfully: {upload_result.get('storage_path', 'chunked storage')}")
                    
                except Exception as e:
                    logger.error(f"Failed to upload file to Supabase: {e}")
                    metadata["status"] = "failed"
                    metadata["error"] = str(e)
                    raise
            
            # Save updated metadata
            async with aiofiles.open(metadata_file, 'w') as f:
                await f.write(json.dumps(metadata, indent=2))
            
            progress_percentage = (metadata["bytes_uploaded"] / metadata["file_size"]) * 100
            
            logger.info(f"Uploaded chunk for {upload_id}: {len(chunk_data)} bytes at offset {chunk_offset} ({progress_percentage:.1f}% complete)")
            
            return {
                "upload_id": upload_id,
                "bytes_uploaded": metadata["bytes_uploaded"],
                "progress_percentage": round(progress_percentage, 2),
                "status": metadata["status"],
                "chunk_uploaded": True
            }
            
        except Exception as e:
            logger.error(f"Failed to upload chunk: {e}")
            raise ValueError(f"Failed to upload chunk: {str(e)}")
    
    async def _assemble_chunks(self, upload_id: str, metadata: Dict[str, Any]) -> str:
        """Assemble all chunks into final file"""
        try:
            final_file = os.path.join(self.upload_dir, f"{upload_id}_final_{metadata['sanitized_filename']}")
            
            # Sort chunks by offset
            sorted_chunks = sorted(metadata["chunks"], key=lambda x: x["offset"])
            
            logger.info(f"Assembling {len(sorted_chunks)} chunks for {upload_id}")
            
            async with aiofiles.open(final_file, 'wb') as output_file:
                for chunk_info in sorted_chunks:
                    chunk_file = chunk_info["chunk_file"]
                    if os.path.exists(chunk_file):
                        async with aiofiles.open(chunk_file, 'rb') as chunk_f:
                            chunk_data = await chunk_f.read()
                            await output_file.write(chunk_data)
                        
                        # Clean up chunk file
                        os.remove(chunk_file)
            
            logger.info(f"Successfully assembled file: {final_file}")
            return final_file
            
        except Exception as e:
            logger.error(f"Failed to assemble chunks: {e}")
            raise
    
    async def _upload_to_supabase(self, upload_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Upload assembled file to Supabase using regular client"""
        try:
            final_file = metadata["final_file"]
            
            # Read assembled file
            async with aiofiles.open(final_file, 'rb') as f:
                file_content = await f.read()
            
            # Upload to Supabase
            upload_result = await supabase_service.upload_file(
                file_content=file_content,
                filename=metadata["filename"],
                user_id=metadata["user_id"],
                document_id=metadata["document_id"]
            )
            
            logger.info(f"Successfully uploaded {upload_id} to Supabase: {upload_result['storage_path']}")
            
            return upload_result
            
        except Exception as e:
            logger.error(f"Failed to upload to Supabase: {e}")
            raise

    async def _upload_large_to_supabase_direct(self, final_file: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Upload large file directly to Supabase using streaming POST"""
        try:
            import aiohttp
            import asyncio
            
            # Create file path for Supabase storage
            sanitized_filename = self._sanitize_filename(metadata["filename"])
            file_path = f"{metadata['user_id']}/{metadata['document_id']}/{sanitized_filename}"
            
            # Supabase storage API endpoint
            storage_url = f"{settings.supabase_url}/storage/v1/object/{settings.supabase_storage_bucket}/{file_path}"
            
            logger.info(f"Starting streaming upload for: {file_path}")
            
            # Headers for streaming upload
            headers = {
                'Authorization': f'Bearer {settings.supabase_service_role_key}',
                'Content-Type': supabase_service._get_content_type(metadata["filename"]),
                'x-upsert': 'true',  # Allow overwrite
                'Content-Length': str(metadata["file_size"])
            }
            
            # Use extended timeout for large files  
            timeout_seconds = min(300 + (metadata["file_size"] // (1024*1024) * 10), 3600)  # 5min + 10s per MB, max 1hr
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logger.info(f"Streaming upload attempt {attempt + 1}/{max_retries} for {sanitized_filename}")
                    
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        # Create async file reader for streaming
                        async def file_sender():
                            chunk_size = 64 * 1024  # 64KB chunks for streaming
                            async with aiofiles.open(final_file, 'rb') as f:
                                while True:
                                    chunk = await f.read(chunk_size)
                                    if not chunk:
                                        break
                                    yield chunk
                        
                        # Upload with streaming
                        async with session.post(storage_url, data=file_sender(), headers=headers) as response:
                            if response.status in [200, 201]:
                                # Get public URL
                                public_url = f"{settings.supabase_url}/storage/v1/object/public/{settings.supabase_storage_bucket}/{file_path}"
                                
                                result = {
                                    "success": True,
                                    "storage_path": file_path,
                                    "public_url": public_url,
                                    "file_size": metadata["file_size"],
                                    "bucket": settings.supabase_storage_bucket,
                                    "uploaded_at": datetime.now().isoformat(),
                                    "upload_method": "streaming"
                                }
                                
                                logger.info(f"Large file uploaded successfully via streaming: {file_path}")
                                return result
                            else:
                                error_text = await response.text()
                                raise Exception(f"HTTP {response.status}: {error_text}")
                
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.warning(f"Streaming upload attempt {attempt + 1} failed: {e}")
                    if attempt == max_retries - 1:
                        raise
                    # Exponential backoff
                    wait_time = (2 ** attempt) * 2
                    logger.info(f"Waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)
                
                except Exception as e:
                    logger.error(f"Unexpected error on attempt {attempt + 1}: {e}")
                    if attempt == max_retries - 1:
                        raise
                    await asyncio.sleep(5)
                
        except Exception as e:
            logger.error(f"Streaming upload failed: {e}")
            raise
    
    async def _upload_streaming_multipart(self, upload_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Upload very large file using direct streaming (no temp file assembly)"""
        try:
            import aiohttp
            import asyncio
            
            # Create file path for Supabase storage
            sanitized_filename = self._sanitize_filename(metadata["filename"])
            file_path = f"{metadata['user_id']}/{metadata['document_id']}/{sanitized_filename}"
            
            # Supabase storage API endpoint
            storage_url = f"{settings.supabase_url}/storage/v1/object/{settings.supabase_storage_bucket}/{file_path}"
            
            logger.info(f"Starting direct streaming upload for very large file: {file_path}")
            
            # Headers for streaming upload
            headers = {
                'Authorization': f'Bearer {settings.supabase_service_role_key}',
                'Content-Type': supabase_service._get_content_type(metadata["filename"]),
                'x-upsert': 'true',  # Allow overwrite
                'Content-Length': str(metadata["file_size"])
            }
            
            # Use extended timeout for very large files  
            timeout_seconds = min(600 + (metadata["file_size"] // (1024*1024) * 15), 7200)  # 10min + 15s per MB, max 2hr
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logger.info(f"Direct streaming upload attempt {attempt + 1}/{max_retries} for {sanitized_filename}")
                    
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        # Create async chunk reader that streams directly from chunks
                        async def chunk_stream_sender():
                            # Sort chunks by offset
                            sorted_chunks = sorted(metadata["chunks"], key=lambda x: x["offset"])
                            
                            for chunk_info in sorted_chunks:
                                chunk_file = chunk_info["chunk_file"]
                                if os.path.exists(chunk_file):
                                    async with aiofiles.open(chunk_file, 'rb') as f:
                                        while True:
                                            chunk_data = await f.read(64 * 1024)  # 64KB streaming chunks
                                            if not chunk_data:
                                                break
                                            yield chunk_data
                                    # Clean up chunk file immediately after streaming
                                    os.remove(chunk_file)
                        
                        # Upload with streaming
                        async with session.post(storage_url, data=chunk_stream_sender(), headers=headers) as response:
                            if response.status in [200, 201]:
                                # Get public URL
                                public_url = f"{settings.supabase_url}/storage/v1/object/public/{settings.supabase_storage_bucket}/{file_path}"
                                
                                result = {
                                    "success": True,
                                    "storage_path": file_path,
                                    "public_url": public_url,
                                    "file_size": metadata["file_size"],
                                    "bucket": settings.supabase_storage_bucket,
                                    "uploaded_at": datetime.now().isoformat(),
                                    "upload_method": "direct_streaming"
                                }
                                
                                logger.info(f"Very large file uploaded successfully via direct streaming: {file_path}")
                                return result
                            else:
                                error_text = await response.text()
                                raise Exception(f"HTTP {response.status}: {error_text}")
                
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.warning(f"Direct streaming upload attempt {attempt + 1} failed: {e}")
                    if attempt == max_retries - 1:
                        raise
                    # Exponential backoff
                    wait_time = (2 ** attempt) * 3
                    logger.info(f"Waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)
                
                except Exception as e:
                    logger.error(f"Unexpected error on attempt {attempt + 1}: {e}")
                    if attempt == max_retries - 1:
                        raise
                    await asyncio.sleep(10)
                
        except Exception as e:
            logger.error(f"Direct streaming upload failed: {e}")
            raise
    
    async def _upload_large_file_chunked(self, upload_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Upload very large files by splitting into multiple Supabase-compatible chunks"""
        try:
            # Create file path for Supabase storage
            sanitized_filename = self._sanitize_filename(metadata["filename"])
            base_path = f"{metadata['user_id']}/{metadata['document_id']}"
            
            # Split size: 40MB to stay well under 50MB limit
            split_size = 40 * 1024 * 1024  # 40MB
            total_size = metadata["file_size"]
            
            logger.info(f"Splitting large file ({total_size / (1024*1024):.1f}MB) into {split_size / (1024*1024):.0f}MB chunks")
            
            # Sort chunks by offset for reassembly
            sorted_chunks = sorted(metadata["chunks"], key=lambda x: x["offset"])
            
            # Upload chunks as separate files with memory optimization
            uploaded_parts = []
            current_data = b""
            part_number = 1
            bytes_processed = 0
            
            try:
                for chunk_info in sorted_chunks:
                    chunk_file = chunk_info["chunk_file"]
                    if not os.path.exists(chunk_file):
                        continue
                    
                    # Read chunk data with memory management
                    try:
                        async with aiofiles.open(chunk_file, 'rb') as f:
                            chunk_data = await f.read()
                        
                        current_data += chunk_data
                        bytes_processed += len(chunk_data)
                        
                        # If current data exceeds split size or this is the last chunk
                        if len(current_data) >= split_size or bytes_processed >= total_size:
                            # Upload this part with same extension as original file
                            original_ext = os.path.splitext(metadata["filename"])[1] or '.bin'
                            part_filename = f"{sanitized_filename}.part{part_number:03d}{original_ext}"
                            
                            logger.info(f"Uploading part {part_number}: {part_filename} ({len(current_data) / (1024*1024):.1f}MB)")
                            
                            # Upload part to Supabase with retry logic
                            max_retries = 3
                            for attempt in range(max_retries):
                                try:
                                    upload_result = await supabase_service.upload_file(
                                        file_content=current_data,
                                        filename=part_filename,
                                        user_id=metadata["user_id"],
                                        document_id=metadata["document_id"]
                                    )
                                    break
                                except Exception as e:
                                    if attempt == max_retries - 1:
                                        raise
                                    logger.warning(f"Part {part_number} upload attempt {attempt + 1} failed: {e}")
                                    await asyncio.sleep(2 ** attempt)
                            
                            uploaded_parts.append({
                                "part_number": part_number,
                                "filename": part_filename,
                                "storage_path": upload_result["storage_path"],
                                "public_url": upload_result["public_url"],
                                "size": len(current_data),
                                "uploaded_at": datetime.now().isoformat()
                            })
                            
                            logger.info(f"✅ Part {part_number} uploaded successfully: {part_filename}")
                            
                            # Clear memory and reset for next part
                            current_data = b""
                            part_number += 1
                        
                    finally:
                        # Clean up chunk file immediately to free disk space
                        try:
                            if os.path.exists(chunk_file):
                                os.remove(chunk_file)
                        except Exception as e:
                            logger.warning(f"Failed to cleanup chunk file {chunk_file}: {e}")
                
                # Ensure any remaining data is uploaded
                if current_data:
                    original_ext = os.path.splitext(metadata["filename"])[1] or '.bin'
                    part_filename = f"{sanitized_filename}.part{part_number:03d}{original_ext}"
                    
                    logger.info(f"Uploading final part {part_number}: {part_filename} ({len(current_data) / (1024*1024):.1f}MB)")
                    
                    upload_result = await supabase_service.upload_file(
                        file_content=current_data,
                        filename=part_filename,
                        user_id=metadata["user_id"],
                        document_id=metadata["document_id"]
                    )
                    
                    uploaded_parts.append({
                        "part_number": part_number,
                        "filename": part_filename,
                        "storage_path": upload_result["storage_path"],
                        "public_url": upload_result["public_url"],
                        "size": len(current_data),
                        "uploaded_at": datetime.now().isoformat()
                    })
                
                # Create manifest file with all parts information
                manifest = {
                    "original_filename": metadata["filename"],
                    "total_size": total_size,
                    "parts": uploaded_parts,
                    "upload_method": "chunked_parts",
                    "created_at": datetime.now().isoformat(),
                    "metadata": {
                        "upload_id": upload_id,
                        "user_id": metadata["user_id"],
                        "document_id": metadata["document_id"]
                    }
                }
                
                # Upload manifest as TXT file (JSON content but .txt extension for Supabase compatibility)
                manifest_filename = f"{sanitized_filename}.manifest.txt"
                manifest_json = json.dumps(manifest, indent=2)
                
                logger.info(f"Creating manifest file: {manifest_filename}")
                
                manifest_upload = await supabase_service.upload_file(
                    file_content=manifest_json.encode('utf-8'),
                    filename=manifest_filename,
                    user_id=metadata["user_id"],
                    document_id=metadata["document_id"]
                )
                
                result = {
                    "success": True,
                    "storage_method": "chunked_parts",
                    "manifest_path": manifest_upload["storage_path"],
                    "manifest_url": manifest_upload["public_url"],
                    "total_parts": len(uploaded_parts),
                    "file_size": total_size,
                    "bucket": settings.supabase_storage_bucket,
                    "uploaded_at": datetime.now().isoformat(),
                    "parts": uploaded_parts[:3]  # Only return first 3 parts in response to avoid large payloads
                }
                
                logger.info(f"🎉 Large file successfully uploaded as {len(uploaded_parts)} parts with manifest")
                return result
                
            except Exception as e:
                # Clean up any remaining chunk files on failure
                for chunk_info in sorted_chunks:
                    try:
                        if os.path.exists(chunk_info["chunk_file"]):
                            os.remove(chunk_info["chunk_file"])
                    except:
                        pass
                raise
                
        except Exception as e:
            logger.error(f"Chunked large file upload failed: {e}")
            raise
    
    async def cleanup_upload(self, upload_id: str):
        """Clean up upload session files"""
        try:
            # Remove metadata file
            metadata_file = os.path.join(self.metadata_dir, f"{upload_id}.json")
            if os.path.exists(metadata_file):
                os.remove(metadata_file)
            
            # Remove any remaining chunk files
            for filename in os.listdir(self.upload_dir):
                if filename.startswith(upload_id):
                    chunk_file = os.path.join(self.upload_dir, filename)
                    os.remove(chunk_file)
            
            logger.info(f"Cleaned up upload session: {upload_id}")
            
        except Exception as e:
            logger.error(f"Failed to cleanup upload: {e}")
    
    async def cleanup_expired_uploads(self):
        """Clean up uploads older than 24 hours"""
        try:
            cutoff_time = datetime.now() - timedelta(hours=24)
            
            for filename in os.listdir(self.metadata_dir):
                if filename.endswith('.json'):
                    metadata_file = os.path.join(self.metadata_dir, filename)
                    upload_id = filename[:-5]  # Remove .json extension
                    
                    try:
                        async with aiofiles.open(metadata_file, 'r') as f:
                            metadata = json.loads(await f.read())
                        
                        created_at = datetime.fromisoformat(metadata["created_at"])
                        if created_at < cutoff_time:
                            await self.cleanup_upload(upload_id)
                            logger.info(f"Cleaned up expired upload: {upload_id}")
                    
                    except Exception as e:
                        logger.warning(f"Error checking upload {upload_id}: {e}")
                        
        except Exception as e:
            logger.error(f"Failed to cleanup expired uploads: {e}")
    
    async def reconstruct_chunked_file(self, manifest_path: str, output_path: str) -> bool:
        """Reconstruct a large file from its chunked parts"""
        try:
            # Download manifest (stored as .txt file with JSON content)
            manifest_data = await supabase_service.download_file(manifest_path)
            manifest = json.loads(manifest_data.decode('utf-8'))
            
            logger.info(f"Reconstructing file: {manifest['original_filename']} from {len(manifest['parts'])} parts")
            
            # Download and assemble parts
            async with aiofiles.open(output_path, 'wb') as output_file:
                for part_info in sorted(manifest['parts'], key=lambda x: x['part_number']):
                    # Download part
                    part_data = await supabase_service.download_file(part_info['storage_path'])
                    await output_file.write(part_data)
                    
                    logger.info(f"Reconstructed part {part_info['part_number']}: {part_info['filename']}")
            
            # Verify file size
            reconstructed_size = os.path.getsize(output_path)
            if reconstructed_size != manifest['total_size']:
                raise ValueError(f"Reconstructed file size mismatch: {reconstructed_size} != {manifest['total_size']}")
            
            logger.info(f"Successfully reconstructed file: {output_path} ({reconstructed_size} bytes)")
            return True
            
        except Exception as e:
            logger.error(f"Failed to reconstruct chunked file: {e}")
            return False


# Global TUS upload service instance
tus_upload_service = TusUploadService()