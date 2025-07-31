from typing import List, Dict, Any, Optional, Tuple
import uuid
import asyncio
from datetime import datetime

import chromadb
from chromadb.config import Settings as ChromaSettings
from loguru import logger

from app.core.config import settings
from app.services.embedding_service import embedding_service
from app.utils.text_chunker import text_chunker

class VectorStoreService:
    def __init__(self):
        self.client = None
        self.collection = None
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize ChromaDB client and collection"""
        try:
            # Create persistent client
            self.client = chromadb.PersistentClient(
                path=settings.vector_store_path,
                settings=ChromaSettings(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )
            
            # Get or create collection
            self.collection = self.client.get_or_create_collection(
                name=settings.collection_name,
                metadata={"description": "Document chunks for RAG"}
            )
            
            logger.info("ChromaDB initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB: {e}")
            raise
    
    async def add_document(self, document_id: str, text: str, metadata: Dict[str, Any]) -> int:
        """
        Add a document to the vector store by chunking and embedding it
        Returns the number of chunks created
        """
        try:
            # Validate that text is not an error message
            error_indicators = [
                "failed to extract",
                "processing encountered an issue",
                "text extraction failed",
                "transcription failed",
                "error extracting"
            ]
            
            if any(indicator in text.lower() for indicator in error_indicators):
                raise ValueError("Cannot store error message as document content")
            
            # Check minimum length based on content type
            min_length = 20 if metadata.get('file_type') == 'video' else 50
            if len(text.strip()) < min_length:
                # For video content, be more flexible with short transcriptions
                if metadata.get('file_type') == 'video' and len(text.strip()) >= 10:
                    logger.warning(f"Short video transcription ({len(text.strip())} chars): {text[:100]}...")
                    # Pad with context for better searchability
                    text = f"Video transcription (short): {text.strip()}"
                else:
                    raise ValueError("Document content too short or empty")
            # Determine content type for optimal chunking
            content_type = metadata.get('file_type', 'general')
            
            # Chunk the text with content-specific optimization
            chunks = text_chunker.chunk_text(text, content_type=content_type)
            if not chunks:
                raise ValueError("No chunks created from text")
            
            logger.info(f"Created {len(chunks)} chunks for document {document_id}")
            
            # Create embeddings for all chunks
            embeddings = await embedding_service.create_embeddings(chunks)
            
            # Prepare data for ChromaDB
            chunk_ids = []
            chunk_metadata = []
            
            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                chunk_id = f"{document_id}_chunk_{i}"
                chunk_ids.append(chunk_id)
                
                chunk_meta = {
                    **metadata,
                    "document_id": document_id,
                    "chunk_index": i,
                    "chunk_count": len(chunks),
                    "created_at": datetime.now().isoformat()
                }
                chunk_metadata.append(chunk_meta)
            
            # Add to ChromaDB
            await asyncio.get_event_loop().run_in_executor(
                None,
                self._add_to_collection,
                chunk_ids,
                embeddings,
                chunks,
                chunk_metadata
            )
            
            logger.info(f"Added {len(chunks)} chunks to vector store for document {document_id}")
            return len(chunks)
            
        except Exception as e:
            logger.error(f"Error adding document to vector store: {e}")
            raise ValueError(f"Failed to add document to vector store: {str(e)}")
    
    def _add_to_collection(self, ids: List[str], embeddings: List[List[float]], 
                          documents: List[str], metadatas: List[Dict[str, Any]]):
        """Add data to ChromaDB collection (synchronous)"""
        self.collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas
        )
    
    async def search_similar(self, query: str, document_id: str = None, 
                           n_results: int = 5) -> List[Dict[str, Any]]:
        """
        Search for similar chunks based on query
        """
        try:
            # Create embedding for query
            query_embedding = await embedding_service.create_single_embedding(query)
            
            # Prepare where clause for filtering by document_id if provided
            where_clause = {"document_id": document_id} if document_id else None
            
            # Search in ChromaDB
            results = await asyncio.get_event_loop().run_in_executor(
                None,
                self._search_collection,
                query_embedding,
                n_results,
                where_clause
            )
            
            # Format results
            formatted_results = []
            if results and results['documents']:
                for i in range(len(results['documents'][0])):
                    formatted_results.append({
                        'id': results['ids'][0][i],
                        'document': results['documents'][0][i],
                        'metadata': results['metadatas'][0][i],
                        'distance': results['distances'][0][i] if 'distances' in results else None
                    })
            
            logger.info(f"Found {len(formatted_results)} similar chunks")
            return formatted_results
            
        except Exception as e:
            logger.error(f"Error searching vector store: {e}")
            raise ValueError(f"Failed to search vector store: {str(e)}")
    
    def _search_collection(self, query_embedding: List[float], n_results: int, 
                          where_clause: Optional[Dict[str, Any]] = None):
        """Search ChromaDB collection (synchronous)"""
        return self.collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where_clause,
            include=['documents', 'metadatas', 'distances']
        )
    
    async def get_document_chunks(self, document_id: str) -> List[Dict[str, Any]]:
        """Get all chunks for a specific document"""
        try:
            results = await asyncio.get_event_loop().run_in_executor(
                None,
                self._get_document_chunks_sync,
                document_id
            )
            
            # Format results
            formatted_results = []
            if results and 'documents' in results and results['documents']:
                documents = results['documents']
                ids = results.get('ids', [])
                metadatas = results.get('metadatas', [])
                
                # Handle nested list structure from ChromaDB
                if isinstance(documents[0], list):
                    # ChromaDB returns nested lists
                    for i in range(len(documents[0])):
                        formatted_results.append({
                            'id': ids[0][i] if ids and len(ids[0]) > i else f"chunk_{i}",
                            'document': documents[0][i],
                            'metadata': metadatas[0][i] if metadatas and len(metadatas[0]) > i else {}
                        })
                else:
                    # Flat list structure
                    for i in range(len(documents)):
                        formatted_results.append({
                            'id': ids[i] if ids and len(ids) > i else f"chunk_{i}",
                            'document': documents[i],
                            'metadata': metadatas[i] if metadatas and len(metadatas) > i else {}
                        })
            
            # Sort by chunk_index
            formatted_results.sort(key=lambda x: x['metadata'].get('chunk_index', 0))
            
            logger.info(f"Formatted {len(formatted_results)} chunks for document {document_id}")
            return formatted_results
            
        except Exception as e:
            logger.error(f"Error getting document chunks: {e}")
            return []  # Return empty list instead of raising error
    
    def _get_document_chunks_sync(self, document_id: str):
        """Get document chunks synchronously"""
        try:
            result = self.collection.get(
                where={"document_id": document_id},
                include=['documents', 'metadatas']
            )
            logger.info(f"Retrieved {len(result.get('documents', []))} chunks for document {document_id}")
            return result
        except Exception as e:
            logger.error(f"Error in _get_document_chunks_sync: {e}")
            raise
    
    async def delete_document(self, document_id: str) -> int:
        """Delete all chunks for a document"""
        try:
            # Get chunks to count them
            chunks = await self.get_document_chunks(document_id)
            chunk_count = len(chunks)
            
            if chunk_count > 0:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._delete_document_sync,
                    document_id
                )
                logger.info(f"Deleted {chunk_count} chunks for document {document_id}")
            
            return chunk_count
            
        except Exception as e:
            logger.error(f"Error deleting document: {e}")
            raise ValueError(f"Failed to delete document: {str(e)}")
    
    def _delete_document_sync(self, document_id: str):
        """Delete document chunks synchronously"""
        self.collection.delete(where={"document_id": document_id})
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get vector store statistics"""
        try:
            count = await asyncio.get_event_loop().run_in_executor(
                None,
                self._get_collection_count
            )
            
            return {
                "total_chunks": count,
                "collection_name": settings.collection_name
            }
            
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {"error": str(e)}
    
    def _get_collection_count(self) -> int:
        """Get collection count synchronously"""
        return self.collection.count()

vector_store_service = VectorStoreService()