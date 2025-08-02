import os
import tempfile
import aiofiles
import asyncio
from datetime import datetime, timedelta
from typing import Optional, BinaryIO, Dict, Any
from pathlib import Path
import boto3
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError, NoCredentialsError
from loguru import logger
from io import BytesIO

from app.core.config import settings


class R2StorageService:
    def __init__(self):
        self.client = None
        self.bucket_name = settings.effective_r2_bucket_name
        self.temp_dir = settings.temp_download_path
        self.credentials_available = self._check_credentials()
        if self.credentials_available:
            try:
                self._initialize_client()
            except Exception as e:
                logger.warning(f"R2 initialization failed (will retry during upload): {e}")
                self.client = None
        self._ensure_temp_dir()
        self._setup_transfer_config()
    
    def _check_credentials(self) -> bool:
        """Check if R2 credentials are available"""
        return bool(
            settings.effective_r2_access_key_id and 
            settings.effective_r2_secret_access_key and 
            settings.effective_r2_endpoint_url
        )
    
    def _initialize_client(self):
        """Initialize R2 client with Cloudflare credentials"""
        try:
            self.client = boto3.client(
                's3',
                aws_access_key_id=settings.effective_r2_access_key_id,
                aws_secret_access_key=settings.effective_r2_secret_access_key,
                endpoint_url=settings.effective_r2_endpoint_url,
                region_name=settings.r2_region,
                config=boto3.session.Config(
                    signature_version='s3v4',
                    retries={
                        'max_attempts': 3,
                        'mode': 'adaptive'
                    }
                )
            )
            logger.info("R2 storage client initialized successfully")
            
            # Test connection (skip bucket validation for now)
            logger.info("R2 client initialized (bucket validation will occur during first upload)")
            
        except Exception as e:
            logger.error(f"Failed to initialize R2 client: {e}")
            raise ValueError(f"R2 configuration error: {str(e)}")
    
    def _test_connection(self):
        """Test R2 connection by checking bucket access"""
        try:
            # Try to list objects (this will fail if credentials are wrong)
            self.client.list_objects_v2(Bucket=self.bucket_name, MaxKeys=1)
            logger.info(f"R2 bucket '{self.bucket_name}' is accessible")
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'NoSuchBucket':
                logger.warning(f"R2 bucket '{self.bucket_name}' does not exist, attempting to create it...")
                try:
                    self.client.create_bucket(Bucket=self.bucket_name)
                    logger.info(f"R2 bucket '{self.bucket_name}' created successfully")
                except Exception as create_error:
                    logger.error(f"Failed to create R2 bucket '{self.bucket_name}': {create_error}")
                    raise ValueError(f"Cannot create R2 bucket: {create_error}")
            elif error_code == 'AccessDenied':
                logger.error(f"Access denied to R2 bucket '{self.bucket_name}'")
                raise ValueError(f"Access denied to R2 bucket '{self.bucket_name}'")
            else:
                logger.error(f"R2 connection test failed: {e}")
                raise ValueError(f"R2 connection failed: {e}")
        except NoCredentialsError:
            logger.error("R2 credentials not found or invalid")
            raise ValueError("R2 credentials not found or invalid")
    
    def _ensure_temp_dir(self):
        """Ensure temporary download directory exists"""
        os.makedirs(self.temp_dir, exist_ok=True)
        logger.debug(f"Temporary download directory: {self.temp_dir}")
    
    def _setup_transfer_config(self):
        """Setup optimized transfer configuration for multipart uploads"""
        self.transfer_config = TransferConfig(
            multipart_threshold=1024 * 1024 * 10,    # 10MB - start multipart for files larger than 10MB
            max_concurrency=10,                       # 10 concurrent upload threads
            multipart_chunksize=1024 * 1024 * 10,     # 10MB chunk size
            use_threads=True,                         # Enable threading for parallel uploads
            max_io_queue=100,                         # Queue size for I/O operations
            io_chunksize=1024 * 1024                  # 1MB I/O chunk size
        )
        logger.info("Transfer config setup: 10MB threshold, 10 concurrent uploads, 10MB chunks")
    
    def _generate_object_key(self, filename: str, document_id: str) -> str:
        """Generate object key for R2 storage"""
        # Get file extension
        file_extension = Path(filename).suffix.lower()
        clean_filename = Path(filename).stem
        
        # Sanitize filename for object key
        clean_filename = self._sanitize_metadata_value(clean_filename)
        
        # Simple structure: uploads/filename-uuid.ext
        object_key = f"uploads/{clean_filename}-{document_id}{file_extension}"
        return object_key
    
    async def upload_file(self, file_content: bytes, filename: str, document_id: str) -> Dict[str, str]:
        """Upload file to R2 storage"""
        if not self.credentials_available:
            raise ValueError("R2 credentials not configured")
        
        try:
            object_key = self._generate_object_key(filename, document_id)
            
            # Prepare metadata (sanitize for ASCII-only requirement)
            metadata = {
                'uploaded_at': datetime.now().isoformat(),
                'document_id': document_id,
                'original_filename': self._sanitize_metadata_value(filename),
                'content_length': str(len(file_content))
            }
            
            # Upload file
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._upload_file_sync,
                file_content,
                object_key,
                metadata
            )
            
            # Generate public URL (if needed for future access)
            file_url = f"{settings.effective_r2_endpoint_url}/{self.bucket_name}/{object_key}"
            
            logger.info(f"File uploaded to R2: {object_key} ({len(file_content)} bytes)")
            
            return {
                'object_key': object_key,
                'file_url': file_url,
                'bucket_name': self.bucket_name,
                'file_size': len(file_content),
                'uploaded_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to upload file to R2: {e}")
            raise ValueError(f"R2 upload failed: {str(e)}")
    
    def _upload_file_sync(self, file_content: bytes, object_key: str, metadata: Dict[str, str]):
        """Synchronous file upload to R2 with multipart optimization"""
        try:
            file_size = len(file_content)
            
            # Create BytesIO object from file content
            file_obj = BytesIO(file_content)
            
            # Determine upload method based on file size
            if file_size >= self.transfer_config.multipart_threshold:
                logger.info(f"Using multipart upload for {file_size/1024/1024:.1f}MB file")
                
                # Use upload_fileobj with TransferConfig for multipart upload
                extra_args = {
                    'Metadata': metadata,
                    'ContentType': self._get_content_type(object_key)
                }
                
                self.client.upload_fileobj(
                    file_obj,
                    self.bucket_name,
                    object_key,
                    ExtraArgs=extra_args,
                    Config=self.transfer_config
                )
            else:
                logger.info(f"Using single upload for {file_size/1024/1024:.1f}MB file")
                
                # Use put_object for smaller files (faster for small files)
                self.client.put_object(
                    Bucket=self.bucket_name,
                    Key=object_key,
                    Body=file_content,
                    Metadata=metadata,
                    ContentType=self._get_content_type(object_key)
                )
                
        except Exception as e:
            logger.error(f"R2 sync upload error: {e}")
            raise
    
    def _sanitize_metadata_value(self, value: str) -> str:
        """Sanitize metadata value to contain only ASCII characters"""
        try:
            # Replace common non-ASCII characters with ASCII equivalents
            replacements = {
                '–': '-',  # en dash
                '—': '-',  # em dash  
                ''': "'",  # left single quote
                ''': "'",  # right single quote
                '"': '"',  # left double quote
                '"': '"',  # right double quote
                '…': '...',  # ellipsis
                'á': 'a', 'à': 'a', 'ä': 'a', 'â': 'a', 'ã': 'a', 'å': 'a',
                'é': 'e', 'è': 'e', 'ë': 'e', 'ê': 'e',
                'í': 'i', 'ì': 'i', 'ï': 'i', 'î': 'i',
                'ó': 'o', 'ò': 'o', 'ö': 'o', 'ô': 'o', 'õ': 'o',
                'ú': 'u', 'ù': 'u', 'ü': 'u', 'û': 'u',
                'ñ': 'n', 'ç': 'c'
            }
            
            sanitized = value
            for char, replacement in replacements.items():
                sanitized = sanitized.replace(char, replacement)
            
            # Encode to ASCII, replacing any remaining non-ASCII chars with '?'
            sanitized = sanitized.encode('ascii', errors='replace').decode('ascii')
            
            return sanitized
            
        except Exception as e:
            logger.warning(f"Error sanitizing metadata value '{value}': {e}")
            # Fallback: encode with replace errors
            return value.encode('ascii', errors='replace').decode('ascii')
    
    def _get_content_type(self, filename: str) -> str:
        """Get content type based on file extension"""
        extension = Path(filename).suffix.lower()
        content_types = {
            '.pdf': 'application/pdf',
            '.mp4': 'video/mp4',
            '.avi': 'video/x-msvideo',
            '.mov': 'video/quicktime',
            '.mkv': 'video/x-matroska',
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav',
            '.m4a': 'audio/mp4',
            '.aac': 'audio/aac',
            '.flac': 'audio/flac'
        }
        return content_types.get(extension, 'application/octet-stream')
    
    async def download_file(self, object_key: str, local_path: Optional[str] = None) -> str:
        """Download file from R2 to local temporary storage"""
        try:
            # Generate local path if not provided
            if local_path is None:
                filename = Path(object_key).name
                local_path = os.path.join(self.temp_dir, f"{datetime.now().timestamp()}_{filename}")
            
            # Download file
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._download_file_sync,
                object_key,
                local_path
            )
            
            logger.info(f"File downloaded from R2: {object_key} -> {local_path}")
            return local_path
            
        except Exception as e:
            logger.error(f"Failed to download file from R2: {e}")
            raise ValueError(f"R2 download failed: {str(e)}")
    
    def _download_file_sync(self, object_key: str, local_path: str):
        """Synchronous file download from R2"""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            
            # Download file
            self.client.download_file(
                Bucket=self.bucket_name,
                Key=object_key,
                Filename=local_path
            )
        except Exception as e:
            logger.error(f"R2 sync download error: {e}")
            raise
    
    async def get_file_info(self, object_key: str) -> Dict[str, Any]:
        """Get file information from R2"""
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                self.client.head_object,
                self.bucket_name,
                object_key
            )
            
            return {
                'object_key': object_key,
                'size': response.get('ContentLength', 0),
                'last_modified': response.get('LastModified'),
                'content_type': response.get('ContentType'),
                'metadata': response.get('Metadata', {})
            }
            
        except Exception as e:
            logger.error(f"Failed to get file info from R2: {e}")
            raise ValueError(f"R2 file info failed: {str(e)}")
    
    async def delete_file(self, object_key: str) -> bool:
        """Delete file from R2 storage (for future cleanup)"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self.client.delete_object,
                self.bucket_name,
                object_key
            )
            
            logger.info(f"File deleted from R2: {object_key}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete file from R2: {e}")
            return False
    
    async def cleanup_temp_file(self, local_path: str):
        """Clean up temporary downloaded file"""
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
                logger.debug(f"Temporary file cleaned up: {local_path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup temp file {local_path}: {e}")
    
    async def list_files(self, prefix: str = "uploads/") -> list:
        """List files in R2 bucket (for future management)"""
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                self.client.list_objects_v2,
                self.bucket_name,
                prefix
            )
            
            files = []
            for obj in response.get('Contents', []):
                files.append({
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'],
                    'etag': obj['ETag']
                })
            
            return files
            
        except Exception as e:
            logger.error(f"Failed to list files from R2: {e}")
            return []
    
    def get_signed_url(self, object_key: str, expiration: int = 3600) -> str:
        """Generate signed URL for temporary access (for future use)"""
        try:
            url = self.client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': object_key},
                ExpiresIn=expiration
            )
            return url
        except Exception as e:
            logger.error(f"Failed to generate signed URL: {e}")
            raise ValueError(f"Signed URL generation failed: {str(e)}")


# Create global R2 storage service instance
r2_storage_service = R2StorageService()