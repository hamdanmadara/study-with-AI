"""
Supabase-based Document Processor
Replaces the old in-memory document processor with full Supabase integration
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path
import tempfile
import os
from loguru import logger

from app.core.config import settings
from app.services.supabase_service import supabase_service
from app.services.queue_service import queue_service
from app.services.text_extraction import text_extraction_service
from app.services.embedding_service import embedding_service
from app.services.tus_upload_service import tus_upload_service
from app.utils.text_chunker import TextChunker
from app.models.document import Document, DocumentType, ProcessingStatus


class SupabaseDocumentProcessor:
    def __init__(self):
        """Initialize Supabase document processor"""
        self.text_chunker = TextChunker(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap
        )
        
    async def process_document(
        self, 
        file_path: str, 
        filename: str, 
        document_id: str,
        user_id: str,
        storage_info: Dict[str, Any],
        storage_type: str
    ) -> Dict[str, Any]:
        """
        Process document and store in Supabase
        This creates the document record and queues it for processing
        """
        try:
            # Determine file type
            file_extension = Path(filename).suffix.lower()
            if file_extension == '.pdf':
                file_type = DocumentType.PDF
            elif file_extension in ['.mp4', '.avi', '.mov', '.mkv', '.webm']:
                file_type = DocumentType.VIDEO
            elif file_extension in ['.mp3', '.wav', '.m4a', '.aac', '.flac']:
                file_type = DocumentType.AUDIO
            else:
                raise ValueError(f"Unsupported file type: {file_extension}")
            
            # Create document record in Supabase with storage information
            document_data = {
                'id': document_id,
                'user_id': user_id,
                'filename': filename,
                'file_type': file_type.value,
                'status': ProcessingStatus.PENDING.value,
                'file_size': storage_info.get('file_size', 0)
            }
            
            # Add storage path (Supabase storage only)
            if storage_info.get('storage_path'):  # Supabase storage
                document_data['storage_path'] = storage_info['storage_path']
            else:  # Local storage fallback
                document_data['storage_path'] = file_path
            
            # Store in Supabase database
            document_record = await supabase_service.create_document(document_data)
            
            # Add to processing queue
            await self._queue_document_processing(document_id, user_id, file_type)
            
            logger.info(f"Document {document_id} created and queued for processing")
            
            return document_record
            
        except Exception as e:
            logger.error(f"Failed to process document {document_id}: {e}")
            # Update document status to failed if it was created
            try:
                await supabase_service.update_document(
                    document_id, 
                    user_id, 
                    {
                        'status': ProcessingStatus.FAILED.value,
                        'error_message': str(e)
                    }
                )
            except:
                pass
            raise
    
    async def _queue_document_processing(self, document_id: str, user_id: str, file_type: DocumentType):
        """Queue document for background processing"""
        try:
            # Add to Redis queue based on file type
            queue_name = "media_processing" if file_type in [DocumentType.VIDEO, DocumentType.AUDIO] else "pdf_processing"
            
            task_data = {
                'document_id': document_id,
                'user_id': user_id,
                'file_type': file_type.value,
                'queued_at': datetime.now().isoformat()
            }
            
            # Create a document object for the queue system
            
            # Convert file_type to DocumentType
            if file_type == DocumentType.PDF:
                doc_type = DocumentType.PDF
            elif file_type == DocumentType.VIDEO:
                doc_type = DocumentType.VIDEO
            elif file_type == DocumentType.AUDIO:
                doc_type = DocumentType.AUDIO
            else:
                doc_type = DocumentType.PDF
            
            # Create document object for queue
            # Get the original filename from the document record to preserve file extension
            original_document = await supabase_service.get_document(document_id, user_id)
            original_filename = original_document.get('filename', 'document.pdf') if original_document else 'document.pdf'
            
            queue_document = Document(
                id=document_id,
                filename=original_filename,  # Use original filename so queue can detect file type
                file_type=doc_type,
                file_path=f"user_id:{user_id}",  # Encode user_id in file_path for callback
                status=ProcessingStatus.PENDING,
                created_at=datetime.now()
            )
            
            # Add to queue with processing callback
            await queue_service.add_document_to_queue(
                queue_document, 
                self._queue_processing_callback
            )
            
            logger.info(f"Document {document_id} added to {queue_name} queue")
            
        except Exception as e:
            logger.error(f"Failed to queue document {document_id}: {e}")
            raise
    
    async def _queue_processing_callback(self, document):
        """Callback for queue processing - calls our process_document_content method"""
        try:
            # Extract user_id from the file_path where we encoded it
            user_id = "placeholder_user_id"
            if document.file_path and document.file_path.startswith("user_id:"):
                user_id = document.file_path.split("user_id:")[1]
            
            # Add timeout to prevent hanging
            await asyncio.wait_for(
                self.process_document_content(document.id, user_id),
                timeout=1800  # 30 minutes timeout
            )
        except Exception as e:
            logger.error(f"Queue processing callback failed for document {document.id}: {e}")
            
            # Update document status to failed instead of crashing
            try:
                await supabase_service.update_document(
                    document.id, 
                    user_id, 
                    {
                        'status': ProcessingStatus.FAILED.value,
                        'error_message': f"Processing failed: {str(e)[:500]}"  # Limit error message length
                    }
                )
            except Exception as update_error:
                logger.error(f"Failed to update document status after error: {update_error}")
            
            # Don't re-raise the exception to prevent server crash
            return
    
    async def process_document_content(self, document_id: str, user_id: str):
        """
        Background processing of document content
        This is called by the queue worker
        """
        try:
            # Get document from Supabase
            document = await supabase_service.get_document(document_id, user_id)
            if not document:
                raise ValueError(f"Document {document_id} not found")
            
            # Update status to processing
            await supabase_service.update_document(
                document_id, 
                user_id, 
                {
                    'status': ProcessingStatus.PROCESSING.value,
                    'processing_started_at': datetime.now().isoformat()
                }
            )
            
            # Download file from Supabase storage to temporary location
            temp_file_path = await self._download_for_processing(document)
            
            try:
                # Extract text from the file
                extracted_text = await self._extract_text(temp_file_path, document['file_type'])
                
                if not extracted_text:
                    raise ValueError("No text could be extracted from the document")
                
                # Create text chunks
                chunks = self.text_chunker.chunk_text(extracted_text)
                
                if not chunks:
                    raise ValueError("No chunks created from extracted text")
                
                # Generate embeddings for chunks
                chunk_data = await self._process_chunks(chunks, document_id, user_id)
                
                # Store chunks with embeddings in Supabase
                await supabase_service.store_document_chunks(chunk_data)
                
                # Update document status to completed
                await supabase_service.update_document(
                    document_id, 
                    user_id, 
                    {
                        'status': ProcessingStatus.COMPLETED.value,
                        'processed_at': datetime.now().isoformat(),
                        'chunk_count': len(chunks)
                    }
                )
                
                logger.info(f"Document {document_id} processing completed successfully")
                
            finally:
                # Clean up temporary file
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
            
        except Exception as e:
            logger.error(f"Document processing failed for {document_id}: {e}")
            
            # Update document status to failed
            await supabase_service.update_document(
                document_id, 
                user_id, 
                {
                    'status': ProcessingStatus.FAILED.value,
                    'error_message': str(e)
                }
            )
            raise
    
    async def _download_for_processing(self, document: Dict[str, Any]) -> str:
        """Download file from Supabase storage for processing"""
        try:
            # Create temporary file
            temp_dir = tempfile.gettempdir()
            temp_filename = f"processing_{document['id']}_{document['filename']}"
            temp_file_path = os.path.join(temp_dir, temp_filename)
            
            # Download from appropriate storage based on how it was uploaded
            storage_path = document.get('storage_path', document['filename'])
            
            # Check if this is a chunked file (manifest path)
            if storage_path and storage_path.endswith('.manifest.txt'):
                logger.info(f"Detected chunked file with manifest: {storage_path}")
                
                # Reconstruct the original file from chunks
                success = await tus_upload_service.reconstruct_chunked_file(
                    manifest_path=storage_path,
                    output_path=temp_file_path
                )
                
                if success:
                    logger.info(f"Successfully reconstructed chunked file: {temp_file_path}")
                    return temp_file_path
                else:
                    raise ValueError(f"Failed to reconstruct chunked file from manifest: {storage_path}")
            
            # Check if file is stored locally or in Supabase (regular files)
            elif os.path.exists(storage_path):
                # File is stored locally, copy to temp location
                logger.info(f"File found locally: {storage_path}")
                import shutil
                shutil.copy2(storage_path, temp_file_path)
                logger.info(f"Copied local file to temp location: {temp_file_path}")
                return temp_file_path
            else:
                # Try downloading from Supabase storage (regular files)
                try:
                    logger.info(f"Downloading from Supabase storage: {storage_path}")
                    file_bytes = await supabase_service.download_file(storage_path)
                    
                    # Write to temporary file
                    with open(temp_file_path, 'wb') as f:
                        f.write(file_bytes)
                    
                    logger.info(f"Downloaded document {document['id']} from Supabase to {temp_file_path}")
                    return temp_file_path
                    
                except Exception as supabase_error:
                    logger.error(f"Failed to download from Supabase storage: {supabase_error}")
                    raise ValueError(f"Cannot process document: file not accessible in Supabase storage or locally")
            
        except Exception as e:
            logger.error(f"Failed to download document {document['id']}: {e}")
            raise
    
    async def _extract_text(self, file_path: str, file_type: str) -> str:
        """Extract text from file using existing text extraction service"""
        try:
            if file_type == 'pdf':
                return await text_extraction_service.extract_text_from_pdf(file_path)
            elif file_type == 'video':
                return await text_extraction_service.extract_text_from_video(file_path)
            elif file_type == 'audio':
                return await text_extraction_service.extract_text_from_audio(file_path)
            else:
                raise ValueError(f"Unsupported file type for text extraction: {file_type}")
                
        except Exception as e:
            logger.error(f"Text extraction failed for {file_path}: {e}")
            raise
    
    async def _process_chunks(self, chunks: List[str], document_id: str, user_id: str) -> List[Dict[str, Any]]:
        """Process text chunks and generate embeddings"""
        try:
            chunk_data = []
            
            for i, chunk_text in enumerate(chunks):
                # Generate embedding for this chunk
                embedding = await embedding_service.generate_embedding(chunk_text)
                
                chunk_record = {
                    'id': str(uuid.uuid4()),
                    'document_id': document_id,
                    'user_id': user_id,
                    'chunk_text': chunk_text,
                    'chunk_index': i,
                    'chunk_metadata': {
                        'word_count': len(chunk_text.split()),
                        'char_count': len(chunk_text)
                    },
                    'embedding': embedding,
                    'created_at': datetime.now().isoformat()
                }
                
                chunk_data.append(chunk_record)
            
            logger.info(f"Generated {len(chunk_data)} embeddings for document {document_id}")
            return chunk_data
            
        except Exception as e:
            logger.error(f"Chunk processing failed for document {document_id}: {e}")
            raise
    
    # Document management methods
    
    async def get_document_status(self, document_id: str, user_id: str) -> Dict[str, Any]:
        """Get document status"""
        return await supabase_service.get_document(document_id, user_id)
    
    async def get_user_documents(self, user_id: str) -> Dict[str, Dict[str, Any]]:
        """Get all documents for a user"""
        documents = await supabase_service.get_user_documents(user_id)
        
        # Convert to dictionary format expected by existing code
        result = {}
        for doc in documents:
            result[doc['id']] = doc
        
        return result
    
    async def delete_document(self, document_id: str, user_id: str):
        """Delete document and associated data"""
        try:
            # Get document info
            document = await supabase_service.get_document(document_id, user_id)
            if not document:
                raise ValueError(f"Document {document_id} not found")
            
            # Delete file from storage
            if document.get('storage_path'):
                await supabase_service.delete_file(document['storage_path'])
            
            # Delete from database (this will cascade to chunks)
            await supabase_service.delete_document(document_id, user_id)
            
            logger.info(f"Document {document_id} deleted successfully")
            
        except Exception as e:
            logger.error(f"Failed to delete document {document_id}: {e}")
            raise
    
    # Queue management methods (keep existing ones)
    
    def get_queue_status(self) -> Dict[str, int]:
        """Get current queue status"""
        try:
            return queue_service.get_queue_status()
        except Exception as e:
            logger.error(f"Failed to get queue status: {e}")
            return {
                "total_queue_size": 0,
                "pdf_queue_size": 0,
                "media_queue_size": 0,
                "active_workers": 0
            }
    
    def get_estimated_wait_time(self, filename: str = None) -> Optional[int]:
        """Get estimated wait time for processing"""
        try:
            # Determine file type from filename
            file_type = 'pdf'  # default
            if filename:
                extension = filename.lower().split('.')[-1] if '.' in filename else 'pdf'
                if extension in ['mp4', 'avi', 'mov', 'mkv', 'webm']:
                    file_type = 'video'
                elif extension in ['mp3', 'wav', 'm4a', 'aac', 'flac']:
                    file_type = 'audio'
            
            return queue_service.get_estimated_wait_time(file_type)
        except Exception as e:
            logger.error(f"Failed to get estimated wait time: {e}")
            return None


# Create global Supabase document processor instance
supabase_document_processor = SupabaseDocumentProcessor()