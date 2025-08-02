import os
import uuid
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any
from pathlib import Path

from loguru import logger

from app.models.document import Document, DocumentType, ProcessingStatus
from app.services.text_extraction import text_extraction_service
from app.services.vector_store import vector_store_service
from app.services.queue_service import queue_service
from app.core.config import settings

class DocumentProcessor:
    def __init__(self):
        self.processing_documents = {}
        self._queue_initialized = False
    
    async def _ensure_queue_started(self):
        """Ensure queue workers are started"""
        if not self._queue_initialized:
            await queue_service.start_workers()
            self._queue_initialized = True
    
    async def process_document(self, file_path: str, filename: str) -> Document:
        """Queue uploaded document for processing"""
        document_id = str(uuid.uuid4())
        
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
        
        # Create document record
        document = Document(
            id=document_id,
            filename=filename,
            file_type=file_type,
            file_path=file_path,
            status=ProcessingStatus.PENDING,
            created_at=datetime.now()
        )
        
        # Store in processing cache
        self.processing_documents[document_id] = document
        
        # Ensure queue is started
        await self._ensure_queue_started()
        
        # Add to queue instead of processing immediately
        await queue_service.add_document_to_queue(document, self._process_document_async)
        
        logger.info(f"Document {document_id} ({filename}) queued for processing")
        
        return document
    
    async def _process_document_async(self, document: Document):
        """Process document asynchronously"""
        try:
            # Update status to processing
            document.status = ProcessingStatus.PROCESSING
            document.processing_started_at = datetime.now()
            self.processing_documents[document.id] = document
            
            logger.info(f"Starting processing for document {document.id}")
            
            # Create progress callback for video processing
            def update_progress(progress_data):
                document.total_duration = progress_data.get('total_duration')
                document.processed_duration = progress_data.get('processed_duration')
                document.total_segments = progress_data.get('total_segments')
                document.processed_segments = progress_data.get('processed_segments')
                document.current_segment = progress_data.get('current_segment')
                
                # Calculate estimated completion time
                if (document.processed_segments and document.total_segments and 
                    document.processed_segments > 0 and document.processing_started_at):
                    
                    elapsed_time = (datetime.now() - document.processing_started_at).total_seconds()
                    avg_time_per_segment = elapsed_time / document.processed_segments
                    remaining_segments = document.total_segments - document.processed_segments
                    estimated_remaining_seconds = remaining_segments * avg_time_per_segment
                    document.estimated_completion = datetime.now() + timedelta(seconds=estimated_remaining_seconds)
                
                # Update cache
                self.processing_documents[document.id] = document
                logger.info(f"Progress update for {document.id}: {document.processed_segments}/{document.total_segments} segments")
            
            # Extract text based on file type
            if document.file_type == DocumentType.PDF:
                text_content = await text_extraction_service.extract_text_from_pdf(document.file_path)
            elif document.file_type == DocumentType.VIDEO:
                text_content = await text_extraction_service.extract_text_from_video(
                    document.file_path, 
                    progress_callback=update_progress
                )
            elif document.file_type == DocumentType.AUDIO:
                text_content = await text_extraction_service.extract_text_from_audio(
                    document.file_path, 
                    progress_callback=update_progress
                )
            else:
                raise ValueError(f"Unsupported file type: {document.file_type}")
            
            # Store text content
            document.text_content = text_content
            
            # Create metadata for vector store
            metadata = {
                "filename": document.filename,
                "file_type": document.file_type.value,
                "created_at": document.created_at.isoformat()
            }
            
            # Add to vector store
            chunk_count = await vector_store_service.add_document(
                document_id=document.id,
                text=text_content,
                metadata=metadata
            )
            
            # Update document
            document.chunk_count = chunk_count
            document.status = ProcessingStatus.COMPLETED
            document.processed_at = datetime.now()
            document.estimated_completion = None  # Clear estimate when completed
            
            logger.info(f"Successfully processed document {document.id} with {chunk_count} chunks")
            
        except Exception as e:
            logger.error(f"Error processing document {document.id}: {e}")
            document.status = ProcessingStatus.FAILED
            document.error_message = str(e)
            document.estimated_completion = None  # Clear estimate on failure
        
        finally:
            # Update cache
            self.processing_documents[document.id] = document
    
    def get_document_status(self, document_id: str) -> Document:
        """Get processing status of a document"""
        if document_id not in self.processing_documents:
            raise ValueError(f"Document {document_id} not found")
        
        return self.processing_documents[document_id]
    
    def get_all_documents(self) -> Dict[str, Document]:
        """Get all processed documents"""
        return self.processing_documents.copy()
    
    def get_queue_status(self):
        """Get current queue status"""
        return queue_service.get_queue_status()
    
    def get_estimated_wait_time(self, filename: str = ''):
        """Get estimated wait time for new uploads based on file type"""
        # Extract file extension to determine file type
        file_extension = filename.lower().split('.')[-1] if '.' in filename else 'pdf'
        return queue_service.get_estimated_wait_time(file_extension)
    
    async def delete_document(self, document_id: str):
        """Delete document and its data"""
        try:
            if document_id not in self.processing_documents:
                raise ValueError(f"Document {document_id} not found")
            
            document = self.processing_documents[document_id]
            
            # Delete from vector store
            await vector_store_service.delete_document(document_id)
            
            # Delete file if it exists
            if os.path.exists(document.file_path):
                os.remove(document.file_path)
            
            # Remove from cache
            del self.processing_documents[document_id]
            
            logger.info(f"Deleted document {document_id}")
            
        except Exception as e:
            logger.error(f"Error deleting document {document_id}: {e}")
            raise ValueError(f"Failed to delete document: {str(e)}")

document_processor = DocumentProcessor()