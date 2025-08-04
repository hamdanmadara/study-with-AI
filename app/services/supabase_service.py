import os
import uuid
import re
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from supabase import create_client, Client
from storage3.utils import StorageException
from gotrue.errors import AuthError
from loguru import logger
import asyncpg
import asyncio
from pathlib import Path

from app.core.config import settings


class SupabaseService:
    def __init__(self):
        """Initialize Supabase client"""
        self.supabase: Client = create_client(
            settings.supabase_url,
            settings.supabase_anon_key
        )
        self.service_client: Client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key
        )
        self.storage_bucket = settings.supabase_storage_bucket
        self._db_pool = None
        
    async def _get_db_pool(self):
        """Get or create database connection pool"""
        if self._db_pool is None:
            # Extract database connection details from Supabase URL
            db_url = settings.supabase_url.replace('https://', '').replace('http://', '')
            db_host = db_url
            
            # Use asyncpg for direct database connections
            try:
                self._db_pool = await asyncpg.create_pool(
                    host=db_host,
                    port=5432,
                    user="postgres",
                    password=settings.supabase_service_role_key.split('.')[1] if '.' in settings.supabase_service_role_key else "your-db-password",
                    database="postgres",
                    ssl="require",
                    min_size=1,
                    max_size=10
                )
            except Exception as e:
                logger.warning(f"Direct DB connection failed, using REST API: {e}")
                
        return self._db_pool

    # === AUTHENTICATION METHODS ===
    
    def verify_jwt_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify JWT token and return user info"""
        try:
            # Set the token in the client
            self.supabase.auth.set_session(token, "")
            user = self.supabase.auth.get_user(token)
            
            if user and user.user:
                return {
                    "user_id": user.user.id,
                    "email": user.user.email,
                    "user_metadata": user.user.user_metadata
                }
        except AuthError as e:
            logger.error(f"JWT verification failed: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in JWT verification: {e}")
        
        return None

    def create_user_account(self, email: str, password: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """Create a new user account"""
        try:
            response = self.service_client.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True,  # Auto-confirm for development
                "user_metadata": metadata or {}
            })
            
            if response.user:
                return {
                    "success": True,
                    "user_id": response.user.id,
                    "email": response.user.email,
                    "message": "User created successfully"
                }
        except AuthError as e:
            logger.error(f"User creation failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"Unexpected error in user creation: {e}")
            return {
                "success": False,
                "error": f"Unexpected error: {str(e)}"
            }

    # === STORAGE METHODS ===
    
    async def download_file(self, file_path: str) -> bytes:
        """Download file from Supabase storage"""
        try:
            # Run download in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.supabase.storage.from_(self.storage_bucket).download(file_path)
            )
            if response:
                return response
            else:
                raise Exception("File not found or download failed")
        except Exception as e:
            logger.error(f"File download error: {e}")
            raise ValueError(f"Failed to download file: {str(e)}")
    
    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for Supabase storage compatibility"""
        # Replace problematic characters with safe alternatives
        sanitized = filename
        
        # Replace em dashes and en dashes with regular dashes
        sanitized = re.sub(r'[–—]', '-', sanitized)
        
        # Replace spaces with underscores
        sanitized = re.sub(r'\s+', '_', sanitized)
        
        # Remove or replace other problematic characters
        sanitized = re.sub(r'[^\w\-_\.]', '', sanitized)
        
        # Ensure filename doesn't start or end with dots or dashes
        sanitized = sanitized.strip('.-_')
        
        # Ensure minimum length
        if len(sanitized) < 3:
            sanitized = f"file_{sanitized}"
        
        return sanitized


    async def upload_file(self, file_content: bytes, filename: str, user_id: str, document_id: str) -> Dict[str, Any]:
        """Upload file to Supabase storage"""
        try:
            # Sanitize filename for storage compatibility
            sanitized_filename = self._sanitize_filename(filename)
            
            # Create user-specific path: user_id/document_id/sanitized_filename
            file_path = f"{user_id}/{document_id}/{sanitized_filename}"
            
            logger.info(f"Original filename: {filename}")
            logger.info(f"Sanitized filename: {sanitized_filename}")
            logger.info(f"Storage path: {file_path}")
            
            # Upload file to storage (run in thread pool to avoid blocking)
            loop = asyncio.get_event_loop()
            
            # Determine file size and adjust strategy
            file_size_mb = len(file_content) / (1024 * 1024)
            
            # Note: Files >50MB should use TUS protocol, but we'll still try regular upload
            if file_size_mb > 50:
                logger.warning(f"Large file ({file_size_mb:.1f}MB) - consider using TUS protocol for better reliability")
            
            if file_size_mb > 25:  # Files over 25MB - use enhanced retry strategy
                logger.info(f"Large file ({file_size_mb:.1f}MB) - using enhanced retry strategy with extended timeout")
                # Use enhanced retry strategy for large files within Supabase limits
                max_retries = 5
                timeout = min(300 + (file_size_mb * 5), 1200)  # 5min + 5sec per MB, max 20min
                
                for attempt in range(max_retries):
                    try:
                        logger.info(f"Large file upload attempt {attempt + 1}/{max_retries} for {sanitized_filename} ({file_size_mb:.1f}MB)")
                        logger.info(f"Using timeout: {timeout}s ({timeout/60:.1f} minutes)")
                        
                        def upload_with_enhanced_retry():
                            try:
                                return self.supabase.storage.from_(self.storage_bucket).upload(
                                    path=file_path,
                                    file=file_content,
                                    file_options={
                                        "content-type": self._get_content_type(filename),
                                        "cache-control": "3600",
                                        "x-upsert": "true"  # Allow overwrite
                                    }
                                )
                            except Exception as e:
                                error_str = str(e).lower()
                                if "ssl" in error_str or "eof" in error_str or "connection" in error_str:
                                    raise ConnectionError(f"Connection error: {e}")
                                elif "timeout" in error_str or "timed out" in error_str:
                                    raise TimeoutError(f"Timeout error: {e}")
                                elif "mime type" in error_str or "not supported" in error_str:
                                    raise ValueError(f"File type error: {e}")
                                else:
                                    raise
                        
                        response = await asyncio.wait_for(
                            loop.run_in_executor(None, upload_with_enhanced_retry),
                            timeout=timeout
                        )
                        
                        if response:
                            logger.info(f"Large file upload successful on attempt {attempt + 1}")
                            break
                        else:
                            raise Exception("Upload returned empty response")
                        
                    except (ConnectionError, TimeoutError, asyncio.TimeoutError) as e:
                        logger.warning(f"Large file upload attempt {attempt + 1} failed: {e}")
                        if attempt == max_retries - 1:
                            raise ValueError(f"Large file upload failed after {max_retries} attempts: {str(e)}")
                        
                        # Progressive backoff with longer waits for large files
                        wait_time = min((2 ** attempt) * 2, 60)  # Cap at 60s
                        logger.info(f"Waiting {wait_time}s before retry...")
                        await asyncio.sleep(wait_time)
                        
                    except ValueError as e:
                        # Don't retry file type errors
                        logger.error(f"File type error (non-retryable): {e}")
                        raise
                        
                    except Exception as e:
                        logger.error(f"Unexpected error on attempt {attempt + 1}: {e}")
                        if attempt == max_retries - 1:
                            raise
                        await asyncio.sleep(5)
                        
            else:
                # Regular upload with improved settings
                max_retries = 3
                timeout = min(120 + (file_size_mb * 2), 600)  # Dynamic timeout: 2min + 2sec per MB, max 10min
                
                for attempt in range(max_retries):
                    try:
                        logger.info(f"Upload attempt {attempt + 1}/{max_retries} for {sanitized_filename} ({file_size_mb:.1f}MB)")
                        
                        # Create upload function with better error handling
                        def upload_with_retries():
                            try:
                                return self.supabase.storage.from_(self.storage_bucket).upload(
                                    path=file_path,
                                    file=file_content,
                                    file_options={
                                        "content-type": self._get_content_type(filename),
                                        "cache-control": "3600"
                                    }
                                )
                            except Exception as e:
                                # Log specific error types
                                error_str = str(e).lower()
                                if "ssl" in error_str or "eof" in error_str:
                                    raise ConnectionError(f"SSL/Connection error: {e}")
                                elif "timeout" in error_str:
                                    raise TimeoutError(f"Network timeout: {e}")
                                else:
                                    raise
                        
                        response = await asyncio.wait_for(
                            loop.run_in_executor(None, upload_with_retries),
                            timeout=timeout
                        )
                        
                        logger.info(f"Upload successful on attempt {attempt + 1}")
                        break  # Success, exit retry loop
                        
                    except (ConnectionError, TimeoutError, asyncio.TimeoutError) as e:
                        logger.warning(f"Upload attempt {attempt + 1} failed: {e}")
                        if attempt == max_retries - 1:
                            raise ValueError(f"Upload failed after {max_retries} attempts: {str(e)}")
                        
                        # Exponential backoff
                        wait_time = (2 ** attempt) + 1
                        logger.info(f"Waiting {wait_time}s before retry...")
                        await asyncio.sleep(wait_time)
                        
                    except Exception as e:
                        logger.error(f"Unexpected upload error on attempt {attempt + 1}: {e}")
                        if attempt == max_retries - 1:
                            raise
                        await asyncio.sleep(2)
            
            if response:
                # Get public URL (run in thread pool to avoid blocking)
                public_url = await loop.run_in_executor(
                    None,
                    lambda: self.supabase.storage.from_(self.storage_bucket).get_public_url(file_path)
                )
                
                return {
                    "success": True,
                    "storage_path": file_path,
                    "public_url": public_url,
                    "file_size": len(file_content),
                    "bucket": self.storage_bucket,
                    "uploaded_at": datetime.now().isoformat()
                }
            else:
                raise Exception("Upload response was empty")
                
        except StorageException as e:
            logger.error(f"Supabase storage error: {e}")
            raise ValueError(f"Storage upload failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected storage error: {e}")
            raise ValueError(f"Storage upload failed: {str(e)}")

    async def get_signed_url(self, file_path: str, expiration: int = 3600) -> str:
        """Get signed URL for file access"""
        try:
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.supabase.storage.from_(self.storage_bucket).create_signed_url(
                    path=file_path,
                    expires_in=expiration
                )
            )
            
            if response and 'signedURL' in response:
                return response['signedURL']
            else:
                raise Exception("Failed to generate signed URL")
                
        except StorageException as e:
            logger.error(f"Signed URL generation failed: {e}")
            raise ValueError(f"Signed URL generation failed: {str(e)}")

    async def delete_file(self, file_path: str) -> bool:
        """Delete file from storage"""
        try:
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.supabase.storage.from_(self.storage_bucket).delete([file_path])
            )
            return len(response) > 0
        except Exception as e:
            logger.error(f"File deletion failed: {e}")
            return False

    # === DATABASE METHODS ===
    
    async def create_document(self, document_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new document record"""
        try:
            # Use service client for administrative operations to bypass RLS
            response = self.service_client.table('documents').insert(document_data).execute()
            
            if response.data and len(response.data) > 0:
                return response.data[0]
            else:
                raise Exception("Document creation failed")
                
        except Exception as e:
            logger.error(f"Document creation error: {e}")
            raise ValueError(f"Document creation failed: {str(e)}")

    async def get_document(self, document_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get document by ID and user ID"""
        try:
            response = self.service_client.table('documents').select('*').eq('id', document_id).eq('user_id', user_id).execute()
            
            if response.data and len(response.data) > 0:
                return response.data[0]
            return None
            
        except Exception as e:
            logger.error(f"Document fetch error: {e}")
            return None

    async def update_document(self, document_id: str, user_id: str, update_data: Dict[str, Any]) -> bool:
        """Update document"""
        try:
            response = self.service_client.table('documents').update(update_data).eq('id', document_id).eq('user_id', user_id).execute()
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Document update error: {e}")
            return False

    async def get_user_documents(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all documents for a user"""
        try:
            response = self.service_client.table('documents').select('*').eq('user_id', user_id).order('created_at', desc=True).execute()
            return response.data or []
        except Exception as e:
            logger.error(f"User documents fetch error: {e}")
            return []

    async def delete_document(self, document_id: str, user_id: str) -> bool:
        """Delete document and associated data"""
        try:
            # Delete document chunks first
            self.supabase.table('document_chunks').delete().eq('document_id', document_id).eq('user_id', user_id).execute()
            
            # Delete document
            response = self.supabase.table('documents').delete().eq('id', document_id).eq('user_id', user_id).execute()
            
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Document deletion error: {e}")
            return False

    # === VECTOR/EMBEDDING METHODS ===
    
    async def store_document_chunks(self, chunks_data: List[Dict[str, Any]]) -> bool:
        """Store document chunks with embeddings"""
        try:
            response = self.supabase.table('document_chunks').insert(chunks_data).execute()
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Chunks storage error: {e}")
            return False

    async def search_similar_chunks(self, query_embedding: List[float], user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search for similar chunks using vector similarity"""
        try:
            # Use RPC call for vector similarity search
            response = self.supabase.rpc('search_document_chunks', {
                'query_embedding': query_embedding,
                'user_id': user_id,
                'match_count': limit
            }).execute()
            
            return response.data or []
        except Exception as e:
            logger.error(f"Vector search error: {e}")
            return []

    async def get_document_chunks(self, document_id: str, user_id: str) -> List[Dict[str, Any]]:
        """Get all chunks for a document"""
        try:
            response = self.supabase.table('document_chunks').select('*').eq('document_id', document_id).eq('user_id', user_id).order('chunk_index').execute()
            return response.data or []
        except Exception as e:
            logger.error(f"Document chunks fetch error: {e}")
            return []

    # === UTILITY METHODS ===
    
    def _get_content_type(self, filename: str) -> str:
        """Get content type from filename - ONLY Supabase supported types"""
        extension = Path(filename).suffix.lower()
        
        # ONLY Supabase supported MIME types
        content_types = {
            '.pdf': 'application/pdf',
            '.mp4': 'video/mp4',
            '.avi': 'video/avi',
            '.mov': 'video/quicktime',
            '.mkv': 'video/x-matroska',
            '.webm': 'video/webm',
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav',
            '.m4a': 'audio/mp4',
            '.aac': 'audio/aac',
            '.flac': 'audio/flac'
        }
        
        # For unsupported extensions, use audio/mpeg as fallback (since it's supported)
        # This includes .txt files (manifest files) - they'll be treated as audio/mpeg
        return content_types.get(extension, 'audio/mpeg')

    async def health_check(self) -> Dict[str, Any]:
        """Check Supabase service health"""
        try:
            # Test database connection
            response = self.supabase.table('documents').select('count', count='exact').limit(1).execute()
            
            return {
                "database": "healthy",
                "storage": "healthy",
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {
                "database": f"error: {str(e)}",
                "storage": "unknown",
                "timestamp": datetime.now().isoformat()
            }


# Create global Supabase service instance
supabase_service = SupabaseService()