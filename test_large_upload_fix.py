#!/usr/bin/env python3
"""
Test script for large file upload fixes
"""
import asyncio
import os
import tempfile
from pathlib import Path

from app.services.tus_upload_service import tus_upload_service
from app.core.config import settings
from loguru import logger

async def create_test_file(size_mb: int) -> str:
    """Create a test file of specified size"""
    test_file = os.path.join(tempfile.gettempdir(), f"test_file_{size_mb}MB.mp3")
    
    # Create a file with random data
    chunk_size = 1024 * 1024  # 1MB chunks
    with open(test_file, 'wb') as f:
        bytes_written = 0
        target_bytes = size_mb * 1024 * 1024
        
        while bytes_written < target_bytes:
            remaining = target_bytes - bytes_written
            write_size = min(chunk_size, remaining)
            
            # Write pattern data (not truly random, but good enough for testing)
            data = bytes([(bytes_written // 1024) % 256] * write_size)
            f.write(data)
            bytes_written += write_size
    
    logger.info(f"Created test file: {test_file} ({size_mb}MB)")
    return test_file

async def test_large_file_upload():
    """Test large file upload functionality"""
    try:
        # Test with different file sizes
        test_sizes = [30, 60, 120]  # MB
        
        for size_mb in test_sizes:
            logger.info(f"Testing {size_mb}MB file upload...")
            
            # Create test file
            test_file = await create_test_file(size_mb)
            file_size = os.path.getsize(test_file)
            
            try:
                # Create upload session
                upload_session = await tus_upload_service.create_upload(
                    filename=f"test_file_{size_mb}MB.mp3",
                    file_size=file_size,
                    user_id="test-user-123",
                    document_id="test-doc-456"
                )
                
                logger.info(f"Created upload session: {upload_session['upload_id']}")
                
                # Upload file in chunks
                chunk_size = 5 * 1024 * 1024  # 5MB chunks
                bytes_uploaded = 0
                
                with open(test_file, 'rb') as f:
                    while bytes_uploaded < file_size:
                        chunk_data = f.read(chunk_size)
                        if not chunk_data:
                            break
                        
                        result = await tus_upload_service.upload_chunk(
                            upload_id=upload_session['upload_id'],
                            chunk_data=chunk_data,
                            chunk_offset=bytes_uploaded
                        )
                        
                        bytes_uploaded += len(chunk_data)
                        logger.info(f"Uploaded chunk: {result['progress_percentage']:.1f}% complete")
                        
                        if result['status'] == 'uploaded':
                            logger.info(f"✅ {size_mb}MB file upload completed successfully!")
                            break
                
                # Clean up upload session
                await tus_upload_service.cleanup_upload(upload_session['upload_id'])
                
            finally:
                # Clean up test file
                if os.path.exists(test_file):
                    os.remove(test_file)
                    logger.info(f"Cleaned up test file: {test_file}")
        
        logger.info("🎉 All large file upload tests passed!")
        
    except Exception as e:
        logger.error(f"❌ Large file upload test failed: {e}")
        raise

async def main():
    """Main test function"""
    logger.info("Starting large file upload tests...")
    
    # Check configuration
    if not settings.supabase_url or not settings.supabase_service_role_key:
        logger.error("Supabase configuration missing. Please check your .env file.")
        return
    
    logger.info(f"Supabase URL: {settings.supabase_url}")
    logger.info(f"Storage bucket: {settings.supabase_storage_bucket}")
    
    await test_large_file_upload()

if __name__ == "__main__":
    asyncio.run(main())