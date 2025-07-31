import os
import uuid
import asyncio
from datetime import datetime
from typing import Dict, Any
from pathlib import Path

from loguru import logger

from app.models.document import Document, DocumentType, ProcessingStatus
from app.services.text_extraction import text_extraction_service
from app.services.vector_store import vector_store_service
from app.core.config import settings

class DocumentProcessor:
    def __init__(self):
        self.processing_documents = {}
    
    async def process_document(self, file_path: str, filename: str) -> Document:
        """Process uploaded document and store in vector database"""
        document_id = str(uuid.uuid4())
        
        # Determine file type
        file_extension = Path(filename).suffix.lower()
        if file_extension == '.pdf':
            file_type = DocumentType.PDF
        elif file_extension in ['.mp4', '.avi', '.mov', '.mkv', '.webm']:
            file_type = DocumentType.VIDEO
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
        
        # Start processing asynchronously
        asyncio.create_task(self._process_document_async(document))
        
        return document
    
    async def _process_document_async(self, document: Document):
        """Process document asynchronously"""
        try:
            # Update status to processing
            document.status = ProcessingStatus.PROCESSING
            self.processing_documents[document.id] = document
            
            logger.info(f"Starting processing for document {document.id}")
            
            # Extract text based on file type
            if document.file_type == DocumentType.PDF:
                text_content = await text_extraction_service.extract_text_from_pdf(document.file_path)
            elif document.file_type == DocumentType.VIDEO:
                text_content = await text_extraction_service.extract_text_from_video(document.file_path)
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
            
            logger.info(f"Successfully processed document {document.id} with {chunk_count} chunks")
            
        except Exception as e:
            logger.error(f"Error processing document {document.id}: {e}")
            document.status = ProcessingStatus.FAILED
            document.error_message = str(e)
        
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